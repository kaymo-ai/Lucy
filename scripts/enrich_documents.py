#!/usr/bin/env python3
"""
Pass 3 — read every camp_knowledge document (chunked where long) and write
dated-by-year, evidence-linked facts to camp_fact.

Why this exists: the 616 camp_knowledge documents were never enriched. The
app keyword-searches raw text and quotes fixed-width windows cut out of
documents as long as 63,000 characters — a window can open mid-shipping-
address. This pass follows the shape of enrich_entity_records.py (chunking,
evidence-scoped storage, quote verification against the real text) but
differs in one important way: entity_records stores the *entire* source
row as evidence.quote, which is fine when the source is a short chat
message but would silently reproduce this project's actual bug if applied
to a 63,000-character document. Instead this pass locates the model's quote
inside the exact chunk it was shown and stores the located ORIGINAL
substring — never the whole chunk, never the model's paraphrase.

Usage:
    python enrich_documents.py ./output/ps_knowledge.db --yes
    python enrich_documents.py ./output/ps_knowledge.db --limit 5 --yes
    python enrich_documents.py ./output/ps_knowledge.db --limit 150 --offset 150 --yes
    python enrich_documents.py ./output/ps_knowledge.db          # dry run: counts only, no spend
"""
import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

from vertex_client import run_all
from enrich_schema import create_enrichment_tables, verify_evidence_complete

PROMPT = (Path(__file__).parent / "prompts" / "document_facts.md").read_text()
CHUNK_TARGET = 6000   # ~6000 chars/chunk on paragraph boundaries; 83/616 docs need it

TOPICS = ["water", "power", "kitchen", "shifts", "bikes", "shade", "waste",
          "arrival", "safety", "food", "structures", "roster"]
CATEGORIES = ["how", "where", "who", "when", "rule"]

SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "enum": TOPICS},
                    "fact": {"type": "string"},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "quote": {"type": "string"},
                },
                "required": ["topic", "fact", "category", "quote"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["facts"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------
# Chunking: paragraph boundaries where possible, hard-split only for a
# paragraph that alone exceeds the target (rare — PDF extraction sometimes
# yields one giant blob with no blank lines at all).
# --------------------------------------------------------------------------

def chunk_document(content: str, target: int = CHUNK_TARGET) -> list[str]:
    """Split content into ~target-char chunks, packing whole paragraphs.
    Rejoining a chunk's paragraphs with '\\n\\n' reproduces the exact
    original substring — chunks are verbatim slices of `content`, which is
    what lets evidence extraction locate quotes against real document text."""
    paragraphs = content.split("\n\n")
    chunks: list[str] = []
    current: list[str] = []

    def flush():
        if current:
            chunks.append("\n\n".join(current))
            current.clear()

    for para in paragraphs:
        if len(para) > target:
            flush()
            for i in range(0, len(para), target):
                chunks.append(para[i:i + target])
            continue
        candidate_len = sum(len(p) for p in current) + 2 * len(current) + len(para)
        if current and candidate_len > target:
            flush()
        current.append(para)
    flush()
    return chunks or [content]


# --------------------------------------------------------------------------
# Quote location: find the model's (whitespace/case tolerant) quote inside
# the chunk it was shown, and return the exact original substring of the
# chunk — never the model's text, never the whole chunk.
# --------------------------------------------------------------------------

def _ws_normalize(s: str) -> str:
    return " ".join((s or "").split())


def locate_quote(text: str, model_quote: str) -> str | None:
    """Return the exact substring of `text` corresponding to `model_quote`
    (matched whitespace-collapsed and casefolded), or None if not found.

    The normalized string and its index-back-to-`text` mapping are built
    together, character by character, so they always stay the same length —
    casefolding a whole string first and mapping positions in afterward can
    desync when casefold expands a character (e.g. 'ß' -> 'ss')."""
    quote_norm = _ws_normalize(model_quote).casefold()
    if not quote_norm:
        return None

    norm_chars: list[str] = []
    mapping: list[int] = []
    in_ws = True
    for i, ch in enumerate(text):
        if ch.isspace():
            if not in_ws:
                norm_chars.append(" ")
                mapping.append(i)
                in_ws = True
        else:
            folded = ch.casefold()
            norm_chars.append(folded)
            mapping.extend([i] * len(folded))
            in_ws = False
    norm_text = "".join(norm_chars)
    if norm_text.endswith(" "):
        norm_text = norm_text[:-1]
        mapping = mapping[:-1]

    idx = norm_text.find(quote_norm)
    if idx == -1:
        return None
    end = idx + len(quote_norm) - 1
    start_orig = mapping[idx]
    end_orig = mapping[end] + 1
    return text[start_orig:end_orig]


# --------------------------------------------------------------------------
# Job construction
# --------------------------------------------------------------------------

def _job_id(doc_id: int, chunk_index: int) -> str:
    return f"doc-{doc_id}-chunk-{chunk_index}"


def _doc_id_from_job(job_id: str) -> int:
    return int(job_id.split("-")[1])


def build_jobs(documents: list[tuple]) -> tuple[list[tuple[str, str]], dict[str, str], dict[int, int | None]]:
    """documents: [(id, title, content, year), ...].
    Returns (jobs, chunk_texts, doc_years) where chunk_texts[job_id] is the
    exact chunk sent for that job (the scope quote verification checks
    against) and doc_years[doc_id] is that document's own year."""
    jobs: list[tuple[str, str]] = []
    chunk_texts: dict[str, str] = {}
    doc_years: dict[int, int | None] = {}

    for doc_id, title, content, year in documents:
        doc_years[doc_id] = year
        chunks = chunk_document(content)
        for i, chunk in enumerate(chunks):
            jid = _job_id(doc_id, i)
            year_line = f"Year: {year}\n" if year else ""
            prompt = f"Document: {title}\n{year_line}\nChunk text:\n\n{chunk}"
            jobs.append((jid, prompt))
            chunk_texts[jid] = chunk

    return jobs, chunk_texts, doc_years


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

def _wipe_existing_facts_for_doc(conn, doc_id: int) -> None:
    """Re-running this pass for a document must replace its facts, not
    duplicate them. camp_fact has no document reference of its own — the
    only link back to camp_knowledge is through evidence — so the fact ids
    must be captured BEFORE evidence is deleted, and evidence deleted
    before the facts, or evidence would be silently orphaned (which
    find_unevidenced_claims cannot catch: it only finds claims missing
    evidence, never the reverse).

    ask_word rows go with their facts for the same reason evidence does:
    they point at fact ids, and a deleted fact's words would otherwise sit
    orphaned, routing a camper's question at an id that belongs to nothing.
    The vocab stage cannot clean them up either — its wipe is scoped to the
    fact ids it re-sends, and a fact that no longer exists is never sent —
    and no verifier sees them (ask_word is deliberately not a claim table),
    so this wipe is the only place the orphans can be stopped."""
    fact_ids = [r[0] for r in conn.execute(
        "SELECT claim_id FROM evidence WHERE claim_table = 'camp_fact' "
        "AND source_table = 'camp_knowledge' AND source_id = ?", (doc_id,))]
    if not fact_ids:
        return
    placeholders = ",".join("?" * len(fact_ids))
    conn.execute(
        f"DELETE FROM evidence WHERE claim_table = 'camp_fact' "
        f"AND claim_id IN ({placeholders})", fact_ids)
    conn.execute(
        f"DELETE FROM ask_word WHERE claim_table = 'camp_fact' "
        f"AND claim_id IN ({placeholders})", fact_ids)
    conn.execute(f"DELETE FROM camp_fact WHERE id IN ({placeholders})", fact_ids)


def store(conn, results: dict, chunk_texts: dict[str, str],
          doc_years: dict[int, int | None]) -> dict:
    """Write camp_fact/evidence rows. Every fact's evidence insert is in the
    same savepoint as the fact insert: a fact whose quote can't be located
    in the real document text must not survive on its own."""
    missing = [k for k, v in results.items() if v is None]
    if missing:
        print(f"WARNING: {len(missing)} jobs returned nothing: {missing[:10]}",
              file=sys.stderr)

    # Wipe once per document (not per chunk-job) — a multi-chunk document's
    # later chunks must not wipe out facts just inserted for its earlier ones.
    doc_ids = sorted({_doc_id_from_job(jid) for jid in results})
    for doc_id in doc_ids:
        _wipe_existing_facts_for_doc(conn, doc_id)

    facts_inserted = 0
    bad_quote_dropped = 0     # model's quote not found in the chunk it was shown
    trigger_dropped = 0       # defense in depth; should stay 0

    for job_id, payload in results.items():
        if not payload:
            continue
        doc_id = _doc_id_from_job(job_id)
        chunk_text = chunk_texts.get(job_id, "")
        year = doc_years.get(doc_id)

        for f in payload.get("facts") or []:
            fact_text = f.get("fact")
            topic = f.get("topic")
            category = f.get("category")
            model_quote = f.get("quote")

            if not fact_text or not topic:
                continue  # malformed despite schema requiring it; defense in depth

            located_quote = locate_quote(chunk_text, model_quote)
            if located_quote is None:
                bad_quote_dropped += 1
                continue

            conn.execute("SAVEPOINT fact")
            try:
                fid = conn.execute(
                    "INSERT INTO camp_fact (topic, fact, category, year) "
                    "VALUES (?,?,?,?)", (topic, fact_text, category, year)).lastrowid
                conn.execute(
                    "INSERT INTO evidence (claim_table, claim_id, source_table, "
                    "source_id, quote) VALUES ('camp_fact', ?, 'camp_knowledge', ?, ?)",
                    (fid, doc_id, located_quote))
            except sqlite3.IntegrityError:
                conn.execute("ROLLBACK TO SAVEPOINT fact")
                conn.execute("RELEASE SAVEPOINT fact")
                trigger_dropped += 1
            else:
                conn.execute("RELEASE SAVEPOINT fact")
                facts_inserted += 1

    verify_evidence_complete(conn)   # raise before committing anything
    conn.commit()

    return {
        "facts_inserted": facts_inserted,
        "bad_quote_dropped": bad_quote_dropped,
        "trigger_dropped": trigger_dropped,
    }


# --------------------------------------------------------------------------

def _select_documents(conn, limit: int | None, offset: int) -> list[tuple]:
    rows = conn.execute(
        "SELECT id, title, content, year FROM camp_knowledge ORDER BY id").fetchall()
    if offset:
        rows = rows[offset:]
    if limit is not None:
        rows = rows[:limit]
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db_path", type=Path)
    parser.add_argument("--yes", action="store_true",
                         help="skip the confirmation prompt and proceed")
    parser.add_argument("--limit", type=int, default=None,
                         help="only process N documents (cheap trial run, or one batch)")
    parser.add_argument("--offset", type=int, default=0,
                         help="skip the first N documents — for batching the full run")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)

    # Create the schema before spending any money — if this fails, it fails
    # before a single job has been sent (no-op if already present).
    create_enrichment_tables(conn)

    documents = _select_documents(conn, args.limit, args.offset)
    print(f"{len(documents)} document(s) selected (offset={args.offset})")

    jobs, chunk_texts, doc_years = build_jobs(documents)
    chunked_docs = sum(1 for d in documents if len(chunk_document(d[2])) > 1)
    print(f"{len(jobs)} chunk job(s) to send "
          f"({chunked_docs} document(s) needed more than one chunk)")

    if not args.yes:
        sys.exit("pass --yes to proceed")

    results = run_all(jobs, PROMPT, SCHEMA)

    # Cache raw results before touching the (shared, concurrently-written) db —
    # a "database is locked" at commit time must not lose paid requests.
    cache_path = Path(__file__).parent / "output" / f"document_facts_raw_{int(time.time())}.json"
    cache_path.write_text(json.dumps(results, indent=2))
    print(f"raw results cached to {cache_path}")

    stats = store(conn, results, chunk_texts, doc_years)

    print(f"\n{stats['facts_inserted']} facts inserted")
    print(f"{stats['bad_quote_dropped']} facts dropped — "
          f"quote did not appear in the chunk the model was shown")
    if stats["trigger_dropped"]:
        print(f"{stats['trigger_dropped']} facts dropped by the evidence trigger "
              f"(unexpected — investigate)")


if __name__ == "__main__":
    main()
