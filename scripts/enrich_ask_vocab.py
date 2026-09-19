#!/usr/bin/env python3
"""
Pass 5 — close the ask-vocabulary gap: the words a camper says are not the
words the corpus wrote down. "Medkit" cannot reach a fact that only says
"first aid"; "water delivered" cannot reach "service vouchers"; "Empire
storage" cannot reach the entity named "Emigrant Storage". The full fix is
embedding search on the phone; this is the build-time version that needs no
new runtime.

Two stages, each wiping and rewriting only its own rows:

  A. For each camp_fact (batched ~25 per model call), generate the words and
     short phrases someone would *ask* with that do not already appear in the
     fact or its topic, into ask_word with claim_table='camp_fact'.
  B. For each entity, generate missing aliases — mishearings, spelling
     variants, colloquial names — into entity_alias with source='ask_vocab',
     so aliases actually observed in the corpus are never touched.

No evidence rows here, on purpose: an ask word asserts nothing about the
world. It can only route a question to a claim whose own evidence stands,
and on-device ranking scores it below a direct text match, so a bad
generated word costs a candidate row, never a wrong top answer. That is what
licenses generation without a supporting quote (see the ask_word comment in
enrich_schema.py).

Usage:
    python enrich_ask_vocab.py ./output/ps_knowledge.db --yes
    python enrich_ask_vocab.py ./output/ps_knowledge.db --limit 25 --yes  # cheap trial
    python enrich_ask_vocab.py ./output/ps_knowledge.db        # dry run: counts only, no spend
"""
import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

from vertex_client import run_all
from enrich_schema import create_enrichment_tables, verify_evidence_complete

BATCH = 25                 # facts or entities per model call
CAP_PER_CLAIM = 6          # generated words kept per fact / aliases per entity
ALIAS_SOURCE = "ask_vocab" # entity_alias.source value marking generated rows

# Mirror of `Retrieval.stop` in Lucy/Retrieval.swift, plus its length rule
# (`$0.count > 2`). The device strips these from every query before searching,
# so a stored word that is only stop words or short tokens can never match
# anything — it is dead weight in the shipped artifact. Keep in sync by hand;
# a divergence only ever costs storage, never a wrong answer.
STOP = frozenset([
    "what", "whats", "who", "whos", "where", "wheres", "when", "whens",
    "why", "how", "is", "are", "was", "were", "the", "a", "an", "of", "in",
    "on", "at", "to", "for", "do", "does", "did", "i", "we", "you", "my",
    "our", "it", "its", "and", "or", "with", "about", "tell", "me", "get",
    "got", "need", "any", "some", "there", "have", "has", "can", "should",
    "know", "lucy",
])
MIN_TERM_LEN = 3

# Redaction posture: a run of four or more digits is the shape of card
# fragments, order numbers, and account numbers — none of which anyone asks
# with, and none of which belong in a table that ships to forty phones.
# Shorter runs stay: "4x4x4", "747", "6am" are real camp vocabulary.
_DIGIT_RUN = re.compile(r"\d{4,}")

# House prompts live in scripts/prompts/*.md; these are inline because this
# wave adds no files there. Short enough that the stage stays self-contained.
PROMPT_FACTS = """\
You are reading numbered facts from a Burning Man camp's knowledge base. The
camp's phone assistant finds facts by keyword match only, so a fact phrased
one way cannot be found by a camper who asks with different words.

For each fact, list the words and short phrases a camper standing in the
desert would actually SAY when asking for this fact — vocabulary that is
missing from the fact's own wording. Think of casual synonyms, generic
category names, common abbreviations, and the everyday name for a thing the
fact calls by a formal or brand name.

Rules:
- lowercase; single words or two-to-three-word phrases
- at most six per fact; fewer is better than padding
- never repeat a word that already appears in the fact or its topic
- no prices, no order/card/account numbers, no personal contact details
- when the fact's own wording already covers how people would ask, return an
  empty list for it — that is the expected common case
"""

PROMPT_ALIASES = """\
You are reading the named things a Burning Man camp talks about — vehicles,
structures, places, tools, traditions. Each entry gives the entity's name,
kind, a short summary, and the aliases already known.

For each entity, list MISSING aliases: common mishearings, misspellings and
misrememberings of the name, and the colloquial or generic names campers
would actually use in speech for this thing.

Rules:
- at most six per entity; fewer is better than padding
- never repeat the entity's name or an alias already listed for it
- no invented backstory — an alias is another handle for the same thing,
  nothing more
- when nothing plausible is missing, return an empty list for that entity —
  that is the expected common case
"""

SCHEMA_FACTS = {
    "type": "object",
    "properties": {
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fact_id": {"type": "integer"},
                    "words": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["fact_id", "words"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["entries"],
    "additionalProperties": False,
}

SCHEMA_ALIASES = {
    "type": "object",
    "properties": {
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "entity_id": {"type": "integer"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["entity_id", "aliases"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["entries"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------
# Word hygiene: everything stored must be a word the device can actually
# search with, and must add something the claim's own text does not have.
# --------------------------------------------------------------------------

def normalize(word: str) -> str:
    """Lowercase, collapse internal whitespace, strip. The stored form."""
    return " ".join((word or "").lower().split())


def contains_whole_word(haystack: str, needle: str) -> bool:
    """Whole-word presence, matching the boundary rule of containsWord in
    Lucy/Retrieval.swift: a boundary is any non-alphanumeric character or the
    string edge. 'med kit' contains 'kit' but not 'medkit', which is exactly
    why the check runs before insert — a generated word already reachable
    through the fact's own text is noise, not vocabulary."""
    hay = normalize(haystack)
    ndl = normalize(needle)
    if not ndl:
        return True  # nothing to add; treat as already present so it is skipped
    return re.search(
        rf"(?<![a-z0-9]){re.escape(ndl)}(?![a-z0-9])", hay) is not None


def is_searchable(word: str) -> bool:
    """True when the device's term extraction would keep at least one token.
    Retrieval.terms() drops stop words and tokens shorter than three
    characters; a candidate made only of those can never be matched, so
    storing it is dead weight."""
    tokens = re.findall(r"[a-z0-9]+", word)
    return any(len(t) >= MIN_TERM_LEN and t not in STOP for t in tokens)


def looks_like_account_number(word: str) -> bool:
    return bool(_DIGIT_RUN.search(word))


def filter_words(candidates: list[str], already_present_in: list[str],
                 cap: int = CAP_PER_CLAIM) -> list[str]:
    """Apply every guardrail in order, keep at most `cap`, preserve the
    model's ordering (it tends to put the best word first)."""
    kept: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        word = normalize(raw)
        if not word or word in seen:
            continue
        if not is_searchable(word):
            continue
        if looks_like_account_number(word):
            continue
        if any(contains_whole_word(text, word) for text in already_present_in):
            continue
        seen.add(word)
        kept.append(word)
        if len(kept) >= cap:
            break
    return kept


# --------------------------------------------------------------------------
# Curated vocabulary — asks the journal actually recorded
# --------------------------------------------------------------------------
# The generator guesses how a camper might phrase a question; the journal on
# the phone records how they did. The first real run of this stage covered
# the class handsomely — 6,191 words — and still missed all four of the
# observed cases that motivated it (docs/learnings-lucy-2026-08-10.md §12,
# E7): nobody guesses "medkit" when "medical" and "bandages" fill the cap,
# and no generator invents the specific mishearing a speech recogniser
# produced. Observed asks are data, so they live here in code, are applied
# through the same filters on every run, and can never be lost to a rewrite.

# Whole-word phrase the fact must contain -> the observed asks to add.
CURATED_ASK_WORDS = {
    "first aid": ["medkit", "med kit"],
    "pumping service": ["delivered", "delivery"],
    # Both numbers, because the whole-word rule means "voucher" cannot see
    # "vouchers" — and the camp's own facts use both.
    "voucher": ["delivered", "delivery"],
    "vouchers": ["delivered", "delivery"],
}

# Entity name (matched case-insensitively) -> observed mishearings.
# "Empire storage" is how the recogniser heard the Emigrant/PS storage unit
# in a real journal question that then retrieved nothing.
# Keyed by both names the unit goes by — the corpus entity is "PS storage",
# the company is Emigrant Storage — so the mishearing lands whichever name
# an enrichment run settles on. The filters drop whatever a name already
# covers.
CURATED_ALIASES = {
    "ps storage": ["empire storage", "emigrant storage", "empire", "emigrant"],
    "emigrant storage": ["empire storage", "emigrant storage", "empire", "emigrant"],
}


# --------------------------------------------------------------------------
# Stage A — ask words for camp facts
# --------------------------------------------------------------------------

def build_fact_jobs(facts: list[tuple]) -> tuple[list[tuple[str, str]], dict]:
    """facts: [(id, topic, fact), ...]. Returns (jobs, batches) where
    batches[job_id] maps fact_id -> (topic, fact) for exactly the facts that
    job was shown — the store step trusts no id the model was not given."""
    jobs: list[tuple[str, str]] = []
    batches: dict[str, dict[int, tuple[str, str]]] = {}
    for i in range(0, len(facts), BATCH):
        batch = facts[i:i + BATCH]
        jid = f"vocab-facts-{i // BATCH}"
        body = "\n".join(f"[{fid}] ({topic}) {fact}"
                         for fid, topic, fact in batch)
        jobs.append((jid, f"Facts:\n\n{body}"))
        batches[jid] = {fid: (topic or "", fact or "")
                        for fid, topic, fact in batch}
    return jobs, batches


def store_fact_words(conn, results: dict, batches: dict) -> dict:
    """Write ask_word rows for camp facts. Wipe-and-rewrite per fact, and
    only for facts whose job actually came back: a failed job keeps its
    facts' previous words rather than deleting paid work and writing
    nothing in its place."""
    missing = [k for k, v in results.items() if v is None]
    if missing:
        print(f"WARNING: {len(missing)} fact jobs returned nothing: "
              f"{missing[:10]}", file=sys.stderr)

    inserted = 0
    unknown_ids = 0   # model returned a fact_id its batch never contained

    for job_id, payload in results.items():
        if not payload:
            continue
        batch = batches.get(job_id, {})

        # Every fact the model saw loses its old generated words — including
        # facts the model returned nothing for, whose stale words would
        # otherwise outlive the fact text that has since changed under them.
        for fid in batch:
            conn.execute(
                "DELETE FROM ask_word WHERE claim_table = 'camp_fact' "
                "AND claim_id = ?", (fid,))

        for entry in payload.get("entries") or []:
            fid = entry.get("fact_id")
            if fid not in batch:
                unknown_ids += 1
                continue
            topic, fact = batch[fid]
            for word in filter_words(entry.get("words") or [], [fact, topic]):
                conn.execute(
                    "INSERT OR IGNORE INTO ask_word (claim_table, claim_id, word) "
                    "VALUES ('camp_fact', ?, ?)", (fid, word))
                inserted += 1

        # Curated words ride on top of the model's, outside the cap: an
        # observed ask is not competing with guesses for the six slots.
        # Applied per wiped batch so a failed job's facts keep their old
        # rows, curated included, exactly like the model words above.
        for fid, (topic, fact) in batch.items():
            for needle, words in CURATED_ASK_WORDS.items():
                if not contains_whole_word(fact, needle):
                    continue
                for word in filter_words(list(words), [fact, topic]):
                    conn.execute(
                        "INSERT OR IGNORE INTO ask_word (claim_table, claim_id, word) "
                        "VALUES ('camp_fact', ?, ?)", (fid, word))
                    inserted += 1

    verify_evidence_complete(conn)   # this pass adds no claims; prove it
    conn.commit()
    return {"words_inserted": inserted, "unknown_fact_ids": unknown_ids}


# --------------------------------------------------------------------------
# Stage B — missing aliases for entities
# --------------------------------------------------------------------------

def select_entities(conn, limit: int | None = None) -> list[tuple]:
    """[(id, name, kind, summary, [hand-made aliases]), ...]. Generated
    aliases are deliberately left out of the alias list: they are wiped
    before rewriting, so counting them as "already present" would make a
    re-generated alias skip itself and vanish on every second run."""
    rows = conn.execute(
        "SELECT id, name, kind, summary FROM entity ORDER BY id").fetchall()
    if limit is not None:
        rows = rows[:limit]
    out = []
    for eid, name, kind, summary in rows:
        aliases = [a for (a,) in conn.execute(
            "SELECT alias FROM entity_alias WHERE entity_id = ? "
            "AND (source IS NULL OR source != ?)", (eid, ALIAS_SOURCE))]
        out.append((eid, name, kind, summary, aliases))
    return out


def build_entity_jobs(entities: list[tuple]) -> tuple[list[tuple[str, str]], dict]:
    """entities: [(id, name, kind, summary, aliases), ...]. Returns (jobs,
    batches) where batches[job_id] maps entity_id -> (name, aliases)."""
    jobs: list[tuple[str, str]] = []
    batches: dict[str, dict[int, tuple[str, list[str]]]] = {}
    for i in range(0, len(entities), BATCH):
        batch = entities[i:i + BATCH]
        jid = f"vocab-entities-{i // BATCH}"
        lines = []
        for eid, name, kind, summary, aliases in batch:
            line = f"[{eid}] {name} ({kind}) — {summary}"
            if aliases:
                line += f" | known as: {', '.join(aliases)}"
            lines.append(line)
        jobs.append((jid, "Entities:\n\n" + "\n".join(lines)))
        batches[jid] = {eid: (name, aliases)
                        for eid, name, kind, summary, aliases in batch}
    return jobs, batches


def store_entity_aliases(conn, results: dict, batches: dict) -> dict:
    """Write generated aliases with source='ask_vocab'. Same wipe-and-rewrite
    shape as the fact stage, scoped to this stage's own rows: an alias with
    any other source was observed in the corpus or made by hand and is never
    deleted here. Generated aliases are stored lowercased, like ask words —
    on-device matching lowercases both sides, and the lowercase form marks
    them visually as vocabulary rather than a name someone wrote."""
    missing = [k for k, v in results.items() if v is None]
    if missing:
        print(f"WARNING: {len(missing)} entity jobs returned nothing: "
              f"{missing[:10]}", file=sys.stderr)

    inserted = 0
    unknown_ids = 0

    for job_id, payload in results.items():
        if not payload:
            continue
        batch = batches.get(job_id, {})

        for eid in batch:
            conn.execute(
                "DELETE FROM entity_alias WHERE entity_id = ? AND source = ?",
                (eid, ALIAS_SOURCE))

        for entry in payload.get("entries") or []:
            eid = entry.get("entity_id")
            if eid not in batch:
                unknown_ids += 1
                continue
            name, aliases = batch[eid]
            # The name and every hand-made alias count as "already present":
            # "emigrant" adds nothing to "Emigrant Storage", but "empire
            # storage" — the mishearing — is exactly what belongs here.
            present = [name] + aliases
            for alias in filter_words(entry.get("aliases") or [], present):
                conn.execute(
                    "INSERT OR IGNORE INTO entity_alias (entity_id, alias, source) "
                    "VALUES (?, ?, ?)", (eid, alias, ALIAS_SOURCE))
                inserted += 1

        # Observed mishearings, same terms as the curated ask words above.
        for eid, (name, aliases) in batch.items():
            curated = CURATED_ALIASES.get(normalize(name))
            if not curated:
                continue
            for alias in filter_words(list(curated), [name] + aliases):
                conn.execute(
                    "INSERT OR IGNORE INTO entity_alias (entity_id, alias, source) "
                    "VALUES (?, ?, ?)", (eid, alias, ALIAS_SOURCE))
                inserted += 1

    verify_evidence_complete(conn)   # this pass adds no claims; prove it
    conn.commit()
    return {"aliases_inserted": inserted, "unknown_entity_ids": unknown_ids}


# --------------------------------------------------------------------------

def _select_facts(conn, limit: int | None) -> list[tuple]:
    rows = conn.execute(
        "SELECT id, topic, fact FROM camp_fact ORDER BY id").fetchall()
    if limit is not None:
        rows = rows[:limit]
    return rows


def _cache(results: dict, name: str) -> None:
    """Cache raw results before touching the (shared, concurrently-written)
    db — a "database is locked" at commit time must not lose paid requests."""
    out_dir = Path(__file__).parent / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}_raw_{int(time.time())}.json"
    path.write_text(json.dumps(results, indent=2))
    print(f"raw results cached to {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db_path", type=Path)
    parser.add_argument("--yes", action="store_true",
                        help="skip the confirmation prompt and proceed")
    parser.add_argument("--limit", type=int, default=None,
                        help="only process N facts and N entities (cheap trial run)")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)

    # Create/migrate the schema before spending any money — ask_word and the
    # entity_alias.source column must exist before a single job is sent.
    create_enrichment_tables(conn)

    facts = _select_facts(conn, args.limit)
    entities = select_entities(conn, args.limit)
    fact_jobs, fact_batches = build_fact_jobs(facts)
    entity_jobs, entity_batches = build_entity_jobs(entities)

    print(f"{len(facts)} fact(s) in {len(fact_jobs)} job(s); "
          f"{len(entities)} entit(ies) in {len(entity_jobs)} job(s)")

    if not args.yes:
        sys.exit("pass --yes to proceed (dry run: nothing sent, nothing written)")

    # Stage A first, cached and stored before stage B spends anything, so a
    # failure between them leaves a complete, committed half rather than two
    # broken ones.
    fact_results = run_all(fact_jobs, PROMPT_FACTS, SCHEMA_FACTS)
    _cache(fact_results, "ask_vocab_facts")
    fact_stats = store_fact_words(conn, fact_results, fact_batches)

    entity_results = run_all(entity_jobs, PROMPT_ALIASES, SCHEMA_ALIASES)
    _cache(entity_results, "ask_vocab_aliases")
    entity_stats = store_entity_aliases(conn, entity_results, entity_batches)

    print(f"\n{fact_stats['words_inserted']} ask word(s) inserted")
    print(f"{entity_stats['aliases_inserted']} generated alias(es) inserted")
    for label, stats, key in (("fact", fact_stats, "unknown_fact_ids"),
                              ("entity", entity_stats, "unknown_entity_ids")):
        if stats[key]:
            print(f"{stats[key]} {label} id(s) returned that were never sent "
                  f"(dropped — investigate if large)")


if __name__ == "__main__":
    main()
