#!/usr/bin/env python3
"""
Pass 2 — read every message about each entity discovered in Task 3, and write
a summary plus dated, evidence-linked facts.

Corrections applied to the original brief (see .superpowers/sdd/e4-brief.md):

1. Name matching is word-boundary, not substring. The brief's
   `LIKE '%' || ? || '%'` alone is badly wrong for short names/aliases: alias
   "PS" substring-matches "perhaps"; "TS" matches inside dozens of unrelated
   words. LIKE is used only to pull a candidate superset from sqlite (which
   can't do regex); a Python regex with `(?<!\\w)...(?!\\w)` on each candidate
   is the actual filter. Every candidate that fails the regex is not sent to
   the model at all — it never gets a chance to be misread as evidence.
2. Capped at 400 messages per entity, preferring the most recent, with a
   printed truncation notice — a cost/context bound, not a quality judgment.
3. Evidence ids are scoped to what THIS entity's job actually saw: the id set
   sent in that job's prompt, not "exists anywhere in person_content" (that
   was Task 3's defect — the existence trigger proves the row exists, not
   that the model saw it). A cited id outside that set is dropped and counted
   separately from a bad quote.
4. The model's quote is checked against the real person_content.content for
   the id it cited (whitespace-normalised, casefolded substring check). If it
   doesn't hold up, the fact is dropped and counted separately from #3. The
   stored evidence.quote is always the database's text, never the model's —
   a quote must be reproducible from the source row.

Usage:
    python enrich_entity_records.py ./output/ps_knowledge.db --yes
    python enrich_entity_records.py ./output/ps_knowledge.db --entity Doris --yes
    python enrich_entity_records.py ./output/ps_knowledge.db --limit 5 --yes
    python enrich_entity_records.py ./output/ps_knowledge.db          # dry run: counts only, no spend
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

PROMPT = (Path(__file__).parent / "prompts" / "entity_record.md").read_text()
MAX_MESSAGES_PER_ENTITY = 400   # cost/context bound (correction 2) — Lucy has ~375 mentions

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fact": {"type": "string"},
                    "category": {"type": "string",
                                 "enum": ["access", "contents", "location",
                                          "handling", "history"]},
                    "asserted_on": {"type": "string"},
                    "evidence_message_id": {"type": "integer"},
                    "quote": {"type": "string"},
                },
                "required": ["fact", "category", "asserted_on",
                             "evidence_message_id", "quote"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "facts"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------
# Text normalisation. Two flavours: whitespace-only (what we render into the
# prompt and store as evidence.quote — matches build_fixture.quote_for, the
# hand-written fixture this pass is scored against) and whitespace+casefold
# (what we use to decide whether the model's quote actually appears in the
# source row).
# --------------------------------------------------------------------------

# WhatsApp wraps an @-mention in invisible bidi isolates:
# "@\u2068Madeline Gale\u2069 has been building Doris's med kit".
# 1,631 of 27,163 messages carry them -- 6%, and disproportionately the ones
# naming campmates, because that is what a mention IS.
#
# The model quotes the readable text and drops the markup, which is the right
# thing to do and used to cost the fact: quote_verified() did a raw substring
# match, found "Madeline Gale has been..." absent from
# "@\u2068Madeline Gale\u2069 has been...", and dropped the claim as
# unverifiable. Silently, counted as a correction.
#
# Only invisible formatting is stripped, plus the @ that introduces a mention.
# Nothing that a reader would see is touched, so a paraphrase still fails --
# which is what this check is actually for.
_INVISIBLE = dict.fromkeys(
    [0x2066, 0x2067, 0x2068, 0x2069,      # directional isolates
     0x200E, 0x200F,                       # LRM / RLM
     0x202A, 0x202B, 0x202C, 0x202D, 0x202E],  # embeddings and overrides
    None)


def _strip_mention_markup(s: str) -> str:
    return (s or "").replace("@\u2068", "").translate(_INVISIBLE)


def _ws_normalize(s: str) -> str:
    return " ".join(_strip_mention_markup(s).split())


def _cf_normalize(s: str) -> str:
    return _ws_normalize(s).casefold()


def quote_verified(db_content: str, model_quote: str) -> bool:
    """Correction 4: does the model's quote actually appear in the row it cited?"""
    if not model_quote or not model_quote.strip():
        return False
    return _cf_normalize(model_quote) in _cf_normalize(db_content)


# --------------------------------------------------------------------------
# Candidate gathering: LIKE for a cheap superset, word-boundary regex to
# actually decide (correction 1).
# --------------------------------------------------------------------------

def load_aliases(conn, entity_id: int) -> list[str]:
    return [a for (a,) in conn.execute(
        "SELECT alias FROM entity_alias WHERE entity_id = ?", (entity_id,))]


def load_reserved_names(conn) -> tuple[set[str], set[str]]:
    """Names that mean something ELSE: every other entity's own name, and
    every real person's name. Measured on the full 129-entity set before the
    full pass (not guessed in advance): a handful of Task-3 aliases are
    exact-string collisions with a different real thing —
    'Public Shade' has alias 'PS', which is also entity 'PS' (a different,
    already-flagged-as-ambiguous entity); 'Radish RV' has alias 'Radish',
    which is also entity 'Radish'; 'Taylor Swift Party' and 'T Swift' both
    have alias 'Taylor', which is also a real camp member's name (818
    messages of NYT/celebrity-news links about the actual Taylor Swift
    turned up under that alias, not the camp party). Using such an alias as
    a search term would feed one entity's job with another named thing's
    messages. Returns (entity_names_cf, person_names_cf), both casefolded."""
    entity_names_cf = {n.strip().casefold() for (n,) in conn.execute(
        "SELECT name FROM entity")}
    person_names_cf = {n.strip().casefold() for (n,) in conn.execute(
        "SELECT name FROM person WHERE name IS NOT NULL")}
    return entity_names_cf, person_names_cf


def safe_terms(name: str, aliases: list[str], entity_names_cf: set[str],
               person_names_cf: set[str]) -> list[str]:
    """The entity's own name, plus any alias that isn't secretly the name of
    a different entity or a real person."""
    own_cf = name.strip().casefold()
    kept = [name]
    for alias in aliases:
        alias_cf = alias.strip().casefold()
        if alias_cf == own_cf:
            continue  # the discovery prompt forbids this; don't trust it blindly
        if alias_cf in entity_names_cf:
            print(f"  {name}: dropping alias {alias!r} — it is another "
                  f"entity's own name", file=sys.stderr)
            continue
        if alias_cf in person_names_cf:
            print(f"  {name}: dropping alias {alias!r} — it is a real "
                  f"person's name", file=sys.stderr)
            continue
        kept.append(alias)
    return kept


def compile_term_regexes(terms: list[str]) -> list[re.Pattern]:
    return [re.compile(r"(?<!\w)" + re.escape(t.strip()) + r"(?!\w)", re.I)
            for t in terms if t and t.strip()]


def fetch_candidates(conn, terms: list[str]) -> list[tuple]:
    """Rows whose content contains ANY term as a plain substring (cheap
    superset via LIKE — sqlite has no regex). Returns
    (id, who, date, content, timestamp)."""
    terms = [t for t in terms if t and t.strip()]
    if not terms:
        return []
    conds = " OR ".join(["lower(pc.content) LIKE '%' || lower(?) || '%'"] * len(terms))
    sql = f"""
        SELECT pc.id, COALESCE(p.name,'?'),
               COALESCE(substr(pc.timestamp,1,10), 'unknown'),
               pc.content, pc.timestamp
        FROM person_content pc LEFT JOIN person p ON p.id = pc.person_id
        WHERE ({conds})
    """
    return conn.execute(sql, terms).fetchall()


def survives_filter(content: str, regexes: list[re.Pattern]) -> bool:
    return any(rx.search(content) for rx in regexes)


def cap_messages(rows: list[tuple], cap: int = MAX_MESSAGES_PER_ENTITY) -> tuple[list[tuple], int]:
    """Correction 2: keep at most `cap`, preferring the most recent.
    Returns (kept rows in chronological order, number dropped)."""
    if len(rows) <= cap:
        return sorted(rows, key=lambda r: r[4] or ""), 0
    most_recent_first = sorted(rows, key=lambda r: r[4] or "", reverse=True)
    kept = most_recent_first[:cap]
    return sorted(kept, key=lambda r: r[4] or ""), len(rows) - cap


def build_jobs(conn, entities: list[tuple]) -> tuple[list[tuple[str, str]], dict[int, set[int]]]:
    """entities: [(id, name, kind), ...]. Returns (jobs, sent_ids) where
    sent_ids[entity_id] is the exact set of person_content ids that entity's
    prompt contained — the scope correction 3 checks facts against."""
    jobs: list[tuple[str, str]] = []
    sent_ids: dict[int, set[int]] = {}
    entity_names_cf, person_names_cf = load_reserved_names(conn)

    for eid, name, kind in entities:
        aliases = load_aliases(conn, eid)
        terms = safe_terms(name, aliases, entity_names_cf, person_names_cf)
        candidates = fetch_candidates(conn, terms)
        regexes = compile_term_regexes(terms)
        survived = [r for r in candidates if survives_filter(r[3], regexes)]
        print(f"  {name}: {len(candidates)} LIKE candidate(s), "
              f"{len(survived)} survived word-boundary filter", file=sys.stderr)

        if not survived:
            print(f"  SKIPPED {name}: 0 messages survived the filter", file=sys.stderr)
            continue

        capped, dropped = cap_messages(survived)
        if dropped:
            print(f"  TRUNCATED {name}: kept the most recent "
                  f"{MAX_MESSAGES_PER_ENTITY} of {len(survived)} messages, "
                  f"dropped {dropped}", file=sys.stderr)

        body = "\n".join(
            f"[{mid}] {date} {who}: {_ws_normalize(text)}"
            for mid, who, date, text, _ts in capped)
        used_aliases = [t for t in terms if t != name]
        alias_line = f"Known aliases: {', '.join(used_aliases)}\n" if used_aliases else ""
        prompt = f"Entity: {name}\n{alias_line}\nMessages:\n\n{body}"

        jobs.append((f"entity-{eid}", prompt))
        sent_ids[eid] = {r[0] for r in capped}

    return jobs, sent_ids


# --------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------

def _wipe_existing_facts(conn, entity_id: int) -> None:
    """Re-running this pass for an entity must replace its facts, not
    duplicate them — entity_fact has no unique constraint of its own.
    Evidence has no FK to entity_fact, so facts must be deleted AFTER their
    evidence rows or the evidence would be silently orphaned (and
    find_unevidenced_claims would not catch it — it only looks for claims
    missing evidence, never evidence missing a claim)."""
    conn.execute("""
        DELETE FROM evidence WHERE claim_table = 'entity_fact'
          AND claim_id IN (SELECT id FROM entity_fact WHERE entity_id = ?)
    """, (entity_id,))
    conn.execute("DELETE FROM entity_fact WHERE entity_id = ?", (entity_id,))


def store(conn, results: dict, sent_ids: dict[int, set[int]]) -> dict:
    """Write entity.summary and entity_fact/evidence rows.

    Every fact's evidence insert is in the same savepoint as the fact insert
    (brief requirement): a fact whose evidence fails to validate must not
    survive on its own. Two independent validations happen before either
    insert is attempted (corrections 3 and 4), so the schema trigger firing
    at all would mean a bug upstream, not fabricated model output — it's
    handled anyway, as defense in depth.
    """
    missing = [k for k, v in results.items() if v is None]
    if missing:
        print(f"WARNING: {len(missing)} jobs returned nothing: {missing[:10]}",
              file=sys.stderr)

    entities_updated = 0
    facts_inserted = 0
    out_of_scope_dropped = 0   # correction 3: id not in what this entity's job saw
    bad_quote_dropped = 0      # correction 4: quote doesn't appear in the cited row
    trigger_dropped = 0        # defense in depth; should stay 0

    for job_id, payload in results.items():
        if not payload:
            continue
        eid = int(job_id.split("-", 1)[1])
        allowed = sent_ids.get(eid, set())

        _wipe_existing_facts(conn, eid)   # idempotent re-run, not additive

        summary = (payload.get("summary") or "").strip()
        if summary:
            conn.execute("UPDATE entity SET summary = ? WHERE id = ?", (summary, eid))
            entities_updated += 1

        for f in payload.get("facts") or []:
            fact_text = f.get("fact")
            category = f.get("category")
            asserted_on = f.get("asserted_on")
            mid = f.get("evidence_message_id")
            model_quote = f.get("quote")

            if not fact_text or not category:
                continue  # malformed despite schema requiring it; defense in depth

            if mid not in allowed:
                out_of_scope_dropped += 1
                continue

            row = conn.execute(
                "SELECT content FROM person_content WHERE id = ?", (mid,)).fetchone()
            if row is None:
                # allowed came from real rows fetched moments ago, so this
                # shouldn't happen — same bucket as out-of-scope if it does.
                out_of_scope_dropped += 1
                continue
            db_quote = _ws_normalize(row[0])

            if not quote_verified(row[0], model_quote):
                bad_quote_dropped += 1
                continue

            conn.execute("SAVEPOINT fact")
            try:
                fid = conn.execute(
                    "INSERT INTO entity_fact (entity_id, fact, category, asserted_on) "
                    "VALUES (?,?,?,?)", (eid, fact_text, category, asserted_on)).lastrowid
                conn.execute(
                    "INSERT INTO evidence (claim_table, claim_id, source_table, "
                    "source_id, quote) VALUES ('entity_fact', ?, 'person_content', ?, ?)",
                    (fid, mid, db_quote))
            except sqlite3.IntegrityError:
                conn.execute("ROLLBACK TO SAVEPOINT fact")
                conn.execute("RELEASE SAVEPOINT fact")
                trigger_dropped += 1
            else:
                conn.execute("RELEASE SAVEPOINT fact")
                facts_inserted += 1

    verify_evidence_complete(conn)   # correction 6: raise before committing anything
    conn.commit()

    return {
        "entities_updated": entities_updated,
        "facts_inserted": facts_inserted,
        "out_of_scope_dropped": out_of_scope_dropped,
        "bad_quote_dropped": bad_quote_dropped,
        "trigger_dropped": trigger_dropped,
    }


# --------------------------------------------------------------------------

def _select_entities(conn, only_name: str | None, limit: int | None) -> list[tuple]:
    entities = conn.execute("SELECT id, name, kind FROM entity ORDER BY id").fetchall()
    if only_name is not None:
        target = only_name.strip().casefold()
        entities = [e for e in entities if e[1].strip().casefold() == target]
        if not entities:
            sys.exit(f"no entity named {only_name!r}")
    if limit is not None:
        entities = entities[:limit]
    return entities


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db_path", type=Path)
    parser.add_argument("--yes", action="store_true",
                         help="skip the confirmation prompt and proceed")
    parser.add_argument("--limit", type=int, default=None,
                         help="only send the first N entities (cheap trial run)")
    parser.add_argument("--entity", type=str, default=None,
                         help="only run this one entity, by name")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)

    # Create the schema before spending any money — if this fails, it fails
    # before a single job has been sent (no-op if already present).
    create_enrichment_tables(conn)

    entities = _select_entities(conn, args.entity, args.limit)
    print(f"{len(entities)} entit{'y' if len(entities) == 1 else 'ies'} selected")

    jobs, sent_ids = build_jobs(conn, entities)
    print(f"\n{len(jobs)} jobs to send "
          f"({len(entities) - len(jobs)} skipped: 0 messages survived the filter)")
    if not args.yes:
        sys.exit("pass --yes to proceed")

    results = run_all(jobs, PROMPT, SCHEMA)

    # Cache raw results before touching the (shared, concurrently-written) db —
    # a "database is locked" at commit time must not lose paid requests.
    cache_path = Path(__file__).parent / "output" / f"entity_records_raw_{int(time.time())}.json"
    cache_path.write_text(json.dumps(results, indent=2))
    print(f"raw results cached to {cache_path}")

    stats = store(conn, results, sent_ids)

    print(f"\n{stats['entities_updated']} entity summaries written")
    print(f"{stats['facts_inserted']} facts inserted")
    print(f"{stats['out_of_scope_dropped']} facts dropped — "
          f"evidence_message_id outside what the model was shown for that entity")
    print(f"{stats['bad_quote_dropped']} facts dropped — "
          f"quote did not appear in the cited message")
    if stats["trigger_dropped"]:
        print(f"{stats['trigger_dropped']} facts dropped by the evidence trigger "
              f"(unexpected — investigate)")


if __name__ == "__main__":
    main()
