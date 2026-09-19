#!/usr/bin/env python3
"""
Pass 1 — find the named things the camp never defines.

Messages are chunked so each request sees a contiguous run of conversation:
entity names are established by how people use them in context, so shuffling
the corpus would destroy the signal this pass depends on.

Usage:
    python enrich_entities.py ./output/ps_knowledge.db --yes
    python enrich_entities.py ./output/ps_knowledge.db --limit 3 --yes   # cheap trial
"""
import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

from vertex_client import run_all
from enrich_schema import create_enrichment_tables, verify_evidence_complete

PROMPT = (Path(__file__).parent / "prompts" / "entity_discovery.md").read_text()
CHUNK = 300          # messages per request — a contiguous run of conversation

SCHEMA = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "kind": {"type": "string",
                             "enum": ["vehicle", "structure", "tool",
                                      "place", "tradition", "asset"]},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "evidence_message_id": {"type": "integer"},
                },
                "required": ["name", "kind", "aliases", "evidence_message_id"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["entities"],
    "additionalProperties": False,
}


def build_jobs(conn) -> list[tuple[str, str]]:
    """Contiguous runs of conversation, ordered by group then time.

    Entity names are established by how people use them in context, so
    shuffling the corpus would destroy the signal this pass depends on.
    """
    rows = conn.execute("""
        SELECT pc.id, COALESCE(p.name,'?'), substr(pc.timestamp,1,10), pc.content
        FROM person_content pc LEFT JOIN person p ON p.id = pc.person_id
        WHERE length(pc.content) > 15
        ORDER BY pc.source, pc.timestamp
    """).fetchall()

    jobs = []
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i + CHUNK]
        body = "\n".join(f"[{mid}] {date} {who}: {text}"
                         for mid, who, date, text in chunk)
        jobs.append((f"discover-{i // CHUNK}", f"Messages:\n\n{body}"))
    return jobs


def store(conn, results) -> int:
    """Merge chunk results. The same entity surfaces in many chunks; each
    sighting adds aliases and increments the mention count.

    Every entity is a claim and every claim needs an evidence row (global
    constraint — see enrich_schema.py). The model supplies a message id as
    evidence, not a quote: the quote is read back from person_content so it
    cannot drift from what was actually said. If the id doesn't exist in the
    corpus, the entity has no evidence and is dropped entirely rather than
    stored without one — a bad id must not kill the whole run (the
    evidence_source_exists trigger would raise sqlite3.IntegrityError on
    insert, after we've already paid for every request in the pass).

    Returns the number of entities dropped for a bad evidence id.
    """
    missing = [k for k, v in results.items() if v is None]
    if missing:
        print(f"WARNING: {len(missing)} chunks returned nothing: "
              f"{missing[:5]}", file=sys.stderr)

    dropped = 0       # bad/missing evidence_message_id — the count called out in the brief
    malformed = 0     # missing name/kind despite the schema requiring them; defense in depth
    for payload in filter(None, results.values()):
        for e in (payload or {}).get("entities") or []:
            name = e.get("name")
            kind = e.get("kind")
            if not name or not kind:
                malformed += 1
                continue

            mid = e.get("evidence_message_id")
            row = conn.execute(
                "SELECT content FROM person_content WHERE id = ?", (mid,)
            ).fetchone() if mid is not None else None
            if row is None:
                dropped += 1
                continue
            quote = row[0]

            # ON CONFLICT increments once per CHUNK that reported this entity,
            # not once per literal mention in the text — a chunk-frequency
            # signal, not a true mention count. Fine for ranking; do not read
            # it as an exact count.
            conn.execute("""
                INSERT INTO entity (name, kind, summary, mention_count)
                VALUES (?, ?, '', 1)
                ON CONFLICT(name) DO UPDATE SET mention_count = mention_count + 1
            """, (name, kind))
            eid = conn.execute("SELECT id FROM entity WHERE name = ?",
                               (name,)).fetchone()[0]

            for alias in e.get("aliases") or []:
                if alias.strip().lower() == name.strip().lower():
                    continue  # the prompt forbids this, but don't trust it blindly
                conn.execute(
                    "INSERT OR IGNORE INTO entity_alias (entity_id, alias) VALUES (?,?)",
                    (eid, alias))

            conn.execute("""
                INSERT INTO evidence (claim_table, claim_id, source_table, source_id, quote)
                VALUES ('entity', ?, 'person_content', ?, ?)
            """, (eid, mid, quote))

    verify_evidence_complete(conn)  # raise before committing anything
    conn.commit()
    if malformed:
        print(f"{malformed} entities dropped for missing name/kind (unexpected)",
              file=sys.stderr)
    return dropped


def _load_entities(conn) -> list[dict]:
    return [
        {"id": i, "name": n, "kind": k, "mention_count": m}
        for i, n, k, m in conn.execute(
            "SELECT id, name, kind, mention_count FROM entity")
    ]


def _load_alias_sets(conn) -> dict[int, set[str]]:
    sets: dict[int, set[str]] = {}
    for eid, alias in conn.execute("SELECT entity_id, alias FROM entity_alias"):
        sets.setdefault(eid, set()).add(alias.strip().casefold())
    return sets


def _names_match(a: dict, b: dict, alias_sets: dict[int, set[str]]) -> bool:
    """Same thing under a different spelling: equal names (case-only
    difference), or one's name sitting in the other's alias set."""
    na, nb = a["name"].strip().casefold(), b["name"].strip().casefold()
    if na == nb:
        return True
    if na in alias_sets.get(b["id"], set()):
        return True
    if nb in alias_sets.get(a["id"], set()):
        return True
    return False


def _pick_survivor(a: dict, b: dict) -> tuple[dict, dict]:
    """Highest mention_count wins; ties break alphabetically (casefolded)
    so the outcome is deterministic regardless of dict/query ordering."""
    def key(e):
        return (-e["mention_count"], e["name"].casefold(), e["name"])
    survivor, loser = sorted([a, b], key=key)
    return survivor, loser


def _merge_pair(conn, survivor: dict, loser: dict) -> None:
    sid, lid = survivor["id"], loser["id"]

    # Evidence follows its claim — repoint before the loser row disappears.
    conn.execute(
        "UPDATE evidence SET claim_id = ? WHERE claim_table = 'entity' AND claim_id = ?",
        (sid, lid))

    # entity_fact is empty in this pass (Task 4 populates it), but its FK is
    # ON DELETE CASCADE — repoint now so a future merge after Task 4 can't
    # silently drop or orphan a loser's facts.
    conn.execute("UPDATE entity_fact SET entity_id = ? WHERE entity_id = ?",
                 (sid, lid))

    # Fold the loser's aliases into the survivor's, skipping ones the
    # survivor already has (entity_alias has a UNIQUE(entity_id, alias)).
    for (alias,) in conn.execute(
            "SELECT alias FROM entity_alias WHERE entity_id = ?", (lid,)).fetchall():
        conn.execute(
            "INSERT OR IGNORE INTO entity_alias (entity_id, alias) VALUES (?,?)",
            (sid, alias))
    conn.execute("DELETE FROM entity_alias WHERE entity_id = ?", (lid,))

    # The loser's own name becomes an alias of the survivor, unless it's
    # just a case variant of the survivor's own name.
    if loser["name"].strip().casefold() != survivor["name"].strip().casefold():
        conn.execute(
            "INSERT OR IGNORE INTO entity_alias (entity_id, alias) VALUES (?,?)",
            (sid, loser["name"]))

    # mention_count is a chunk-frequency signal (see store()); summing it
    # across a merge keeps that meaning intact.
    conn.execute("UPDATE entity SET mention_count = mention_count + ? WHERE id = ?",
                 (loser["mention_count"], sid))
    conn.execute("DELETE FROM entity WHERE id = ?", (lid,))

    # Now that aliases from both sides are pooled on the survivor, drop any
    # that equal the survivor's own name (case-insensitively).
    survivor_name_cf = survivor["name"].strip().casefold()
    for alias_id, alias in conn.execute(
            "SELECT id, alias FROM entity_alias WHERE entity_id = ?", (sid,)).fetchall():
        if alias.strip().casefold() == survivor_name_cf:
            conn.execute("DELETE FROM entity_alias WHERE id = ?", (alias_id,))


def merge_duplicate_entities(conn) -> list[tuple[str, str, str, str]]:
    """Collapse entities that are the same thing under different spellings.

    Pass 1 chunks are read independently — a chunk that calls something
    "Playella" has no way to know another chunk spelled it "playaella" — so
    the same real-world thing surfaces as several entity rows and the
    `ON CONFLICT(name)` merge in store() only catches an exact string match.
    Left alone, Task 4 would enrich each spelling as its own entity: extra
    cost, and a rank split across thin duplicate records instead of one good
    one.

    Two entities are merged when, after casefold+strip, their names are
    equal, or one's name appears in the other's alias set. This is applied
    repeatedly (recomputing the candidate set after every merge) because
    matches chain — "Playaella" -> alias "playella" on a *different* entity
    named "Playella" -> alias "playaella" collapses to one row only if all
    three pairwise merges happen.

    Entities of different `kind` are never merged even if their names match
    — that means the discovery prompt classified the same name two ways,
    which is a signal for a human to look at, not something to silently
    paper over. Returns the list of such (name_a, kind_a, name_b, kind_b)
    collisions found.

    No model call: deterministic and idempotent, safe to re-run for free.
    """
    collisions: list[tuple[str, str, str, str]] = []
    reported: set[tuple[int, int]] = set()

    while True:
        entities = _load_entities(conn)
        alias_sets = _load_alias_sets(conn)
        merged_a_pair = False

        for i in range(len(entities)):
            for j in range(i + 1, len(entities)):
                a, b = entities[i], entities[j]
                if not _names_match(a, b, alias_sets):
                    continue
                if a["kind"] != b["kind"]:
                    key = tuple(sorted((a["id"], b["id"])))
                    if key not in reported:
                        reported.add(key)
                        collisions.append((a["name"], a["kind"], b["name"], b["kind"]))
                    continue
                survivor, loser = _pick_survivor(a, b)
                _merge_pair(conn, survivor, loser)
                merged_a_pair = True
                break
            if merged_a_pair:
                break

        if not merged_a_pair:
            break

    verify_evidence_complete(conn)  # merging repoints evidence; confirm nothing dangles
    conn.commit()
    return collisions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db_path", type=Path)
    parser.add_argument("--yes", action="store_true",
                         help="skip the confirmation prompt and proceed")
    parser.add_argument("--limit", type=int, default=None,
                         help="only send the first N chunks (cheap trial run)")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)

    # Create the schema before spending any money on requests — if this
    # fails, it fails before a single job has been sent.
    create_enrichment_tables(conn)

    jobs = build_jobs(conn)
    if args.limit is not None:
        jobs = jobs[:args.limit]

    print(f"{len(jobs)} chunks to send")
    if not args.yes:
        sys.exit("pass --yes to proceed")

    results = run_all(jobs, PROMPT, SCHEMA)

    # Cache raw results before touching the (shared, concurrently-written) db —
    # a "database is locked" at commit time must not lose 68 paid requests.
    cache_path = Path(__file__).parent / "output" / f"entity_discovery_raw_{int(time.time())}.json"
    cache_path.write_text(json.dumps(results, indent=2))
    print(f"raw results cached to {cache_path}")

    dropped = store(conn, results)

    before = conn.execute("SELECT COUNT(*) FROM entity").fetchone()[0]
    collisions = merge_duplicate_entities(conn)
    after = conn.execute("SELECT COUNT(*) FROM entity").fetchone()[0]

    print(f"\n{before} distinct entities before merge, {after} after "
          f"({before - after} duplicate spellings collapsed)")
    print(f"{dropped} entities dropped for a bad evidence_message_id")
    if collisions:
        print(f"{len(collisions)} same-name/alias collision(s) across different "
              f"kinds — NOT merged, needs a human look:")
        for na, ka, nb, kb in collisions:
            print(f"  {na!r} ({ka})  vs  {nb!r} ({kb})")
    for name, kind, seen in conn.execute(
            "SELECT name, kind, mention_count FROM entity "
            "ORDER BY mention_count DESC LIMIT 25"):
        print(f"  {seen:>4}x  {kind:<10} {name}")


if __name__ == "__main__":
    main()
