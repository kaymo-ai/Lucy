#!/usr/bin/env python3
"""
Pass 3 — write a short reference profile, demonstrated-expertise list, and
co-membership relationships for every camp member with enough of a message
history to say something true about.

This pass writes notes about real, named people who will read them. The
governing rule, from .superpowers/sdd/e5-brief.md: only what the messages
support, expertise means demonstrated knowledge rather than enthusiasm, every
claim cites a message, and nothing about health, relationships, money, or
conflict.

Five corrections applied to the brief (see e5-brief.md and the task that
carried them forward):

1. The relationship SQL in the brief self-joins person_content, so COUNT(*)
   counts message pairs, not shared groups — two people with 500 and 300
   messages in one group would score 150,000, and the brief's
   HAVING COUNT(*) >= 50 passed almost everyone. Relationships are derived
   here from DISTINCT (person_id, source) membership instead: strength is
   the number of shared groups, which is what the column is supposed to mean.
2. The catch-all 'PS' group is excluded from relationship derivation — it is
   most of the camp, so co-membership there is a membership list, not a
   relationship. Only the six named regional/build groups carry signal:
   PS London Meetups, PS NYC, PS🖤Berlin, PS🔥SF, PS Build 1, PS BUILD 22.
   Measured before deciding: even restricted to those six, single-group
   co-membership alone produces 6,466 pairs (PS NYC has 81 distinct people —
   3,240 pairs from that group by itself; PS🔥SF has 74 — 2,701 more). That
   is a membership list, not a relationship, so the bar is raised to
   requiring >= 2 shared groups, which brings it to 1,358 pairs — under the
   ~3,000 guideline and a real signal (two people who show up together in
   two distinct small groups, not one big one).
3. relationship is a claim table, so each row needs evidence. Each gets two
   rows: the earliest message person_a wrote in their shared group, and the
   earliest person_b wrote there — honest evidence for "these two are both
   in this group," not a fabricated quote about their relationship. Chosen
   deterministically when a pair shares more than one group: the group where
   the later of the two people's first messages is earliest, i.e. the group
   where the overlap between them started soonest.
4. Same guards as Task 4: an evidence_message_id must be in the exact set of
   message ids this person's job was shown (not "exists anywhere in
   person_content" — that only proves the row exists, not that the model saw
   it), and the model's quote must actually appear in the row it cites
   (whitespace/casefold check). A citation failing either check is dropped
   and counted in one of two buckets. If a profile's summary_evidence has no
   surviving citation at all, the whole profile (and that person's
   expertise) is dropped rather than stored uncited — same principle Task 3
   used for entities with a bad discovery citation.
5. No interactive prompts — argparse with --yes, --limit, --only, and (for
   batching a long full pass) --offset and --skip-relationships.

Two things fixed after the Nick Hadley trial round-tripped through review:
voice ("the camp's X" leaked into 63/118 summaries — the prompt now bans the
phrase outright), and the expertise strength scale (the model was not
calibrating strong/moderate/mentioned on its own judgment — one restaurant
recommendation came back "strong"). Strength is now counted, not judged: the
model lists every message it can find that demonstrates a topic, and this
pass derives strength from how many distinct, verified messages survive —
1 -> mentioned, 2 -> moderate, 3+ -> strong. known_for is also now a required
field with an honest fallback ("no single specialty stands out") instead of
being left blank, which had been happening for 69/118 people.

One addition beyond the five corrections: years_active and chapter are
computed here from SQL over the person's full message history, not asked of
the model. That is arithmetic, not judgment (the brief's own reason for
keeping relationships out of the model), and it avoids a real accuracy bug —
the per-job message cap below prefers a person's most RECENT messages (a
cost/context bound, same as Task 4), so a model asked for years_active from
that capped window would understate tenure for anyone with more than the cap
(the most active person here has 1,513 messages against a 400-message cap).
'Chapter' is defined the same way the relationship pass defines a chapter —
a person's most-active non-PS source — so the two parts of this pass agree
with each other about what a chapter is.

Usage:
    python enrich_people.py ./output/ps_knowledge.db --only "Nick Hadley"
    python enrich_people.py ./output/ps_knowledge.db --limit 30 --yes
    python enrich_people.py ./output/ps_knowledge.db --yes
    python enrich_people.py ./output/ps_knowledge.db --relationships-only --yes
    python enrich_people.py ./output/ps_knowledge.db   # dry run: counts only, no spend
"""
import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

from vertex_client import run_all
from enrich_schema import create_enrichment_tables, verify_evidence_complete

PROMPT = (Path(__file__).parent / "prompts" / "person_profile.md").read_text()
MAX_MESSAGES_PER_PERSON = 400   # cost/context bound (same rationale as Task 4)
MIN_MESSAGES_FOR_PROFILE = 20   # Step 1: below this there is nothing to write

# Correction 2: only these six carry co-membership signal. 'PS' is the
# catch-all (excluded); 'PS Music' and 'PS+ Book Club' are topic channels, not
# chapters, and are not named in the correction — excluded for the same
# reason 'PS' is: they are not what "chapter" means elsewhere in this pass.
REGIONAL_SOURCES = (
    "PS London Meetups", "PS NYC", "PS🖤Berlin",
    "PS🔥SF", "PS Build 1", "PS BUILD 22",
)
MIN_SHARED_GROUPS = 2   # correction 2: raised from "any" — see module docstring

# Coordinator correction: the model does not self-calibrate strong/moderate/
# mentioned reliably (a single restaurant recommendation came back "strong").
# Strength is now counted from the number of distinct, verified messages an
# expertise claim survives with — a counting rule, not a judgment call.
# Keyed by min(verified_count, 3): 1 -> mentioned, 2 -> moderate, 3+ -> strong.
STRENGTH_BY_EVIDENCE_COUNT = {1: "mentioned", 2: "moderate", 3: "strong"}

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "known_for": {"type": "string"},
        "summary_evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "evidence_message_id": {"type": "integer"},
                    "quote": {"type": "string"},
                },
                "required": ["evidence_message_id", "quote"],
                "additionalProperties": False,
            },
        },
        "expertise": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "evidence": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "evidence_message_id": {"type": "integer"},
                                "quote": {"type": "string"},
                            },
                            "required": ["evidence_message_id", "quote"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["topic", "evidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "known_for", "summary_evidence", "expertise"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------
# Text normalisation — identical convention to enrich_entity_records.py.
# --------------------------------------------------------------------------

def _ws_normalize(s: str) -> str:
    return " ".join((s or "").split())


def _cf_normalize(s: str) -> str:
    return _ws_normalize(s).casefold()


def quote_verified(db_content: str, model_quote: str) -> bool:
    """Correction 4: does the model's quote actually appear in the row it cited?"""
    if not model_quote or not model_quote.strip():
        return False
    return _cf_normalize(model_quote) in _cf_normalize(db_content)


# --------------------------------------------------------------------------
# Step 1: who gets a profile
# --------------------------------------------------------------------------

def _eligible_people(conn) -> list[tuple[int, str]]:
    """Every person at or above the message floor, ordered by id for a
    deterministic --limit/--offset slice."""
    return conn.execute(f"""
        SELECT p.id, p.name
        FROM person p
        JOIN (SELECT person_id, COUNT(*) AS n FROM person_content
              WHERE person_id IS NOT NULL GROUP BY person_id) c
          ON c.person_id = p.id
        WHERE c.n >= {MIN_MESSAGES_FOR_PROFILE}
        ORDER BY p.id
    """).fetchall()


def _select_people(conn, only_name: str | None, limit: int | None,
                    offset: int) -> list[tuple[int, str]]:
    people = _eligible_people(conn)
    if only_name is not None:
        target = only_name.strip().casefold()
        people = [p for p in people if p[1].strip().casefold() == target]
        if not people:
            sys.exit(f"no person named {only_name!r} at or above the "
                      f"{MIN_MESSAGES_FOR_PROFILE}-message floor")
        return people
    if offset:
        people = people[offset:]
    if limit is not None:
        people = people[:limit]
    return people


# --------------------------------------------------------------------------
# years_active / chapter — computed, not asked of the model (see module
# docstring: arithmetic over the full history, not the capped prompt window).
# --------------------------------------------------------------------------

def compute_profile_meta(conn, person_id: int) -> dict:
    years = conn.execute("""
        SELECT MIN(substr(timestamp,1,4)), MAX(substr(timestamp,1,4))
        FROM person_content WHERE person_id = ? AND timestamp IS NOT NULL
    """, (person_id,)).fetchone()
    lo, hi = years
    if lo and hi:
        years_active = lo if lo == hi else f"{lo}–{hi}"
    else:
        years_active = None

    chapter_row = conn.execute("""
        SELECT source, COUNT(*) AS n
        FROM person_content
        WHERE person_id = ? AND source IS NOT NULL AND source != 'PS'
        GROUP BY source ORDER BY n DESC LIMIT 1
    """, (person_id,)).fetchone()
    chapter = chapter_row[0] if chapter_row else None

    return {"years_active": years_active, "chapter": chapter}


# --------------------------------------------------------------------------
# Job construction
# --------------------------------------------------------------------------

def cap_messages(rows: list[tuple], cap: int = MAX_MESSAGES_PER_PERSON) -> tuple[list[tuple], int]:
    """Keep at most `cap`, preferring the most recent (same convention as
    Task 4's cap_messages). Returns (kept rows in chronological order, dropped)."""
    if len(rows) <= cap:
        return sorted(rows, key=lambda r: r[2] or ""), 0
    most_recent_first = sorted(rows, key=lambda r: r[2] or "", reverse=True)
    kept = most_recent_first[:cap]
    return sorted(kept, key=lambda r: r[2] or ""), len(rows) - cap


def build_jobs(conn, people: list[tuple[int, str]]
               ) -> tuple[list[tuple[str, str]], dict[int, set[int]], dict[int, dict]]:
    """Returns (jobs, sent_ids, meta). sent_ids[person_id] is the exact set of
    person_content ids that person's prompt contained (correction 4's scope).
    meta[person_id] carries the computed years_active/chapter."""
    jobs: list[tuple[str, str]] = []
    sent_ids: dict[int, set[int]] = {}
    meta: dict[int, dict] = {}

    for pid, name in people:
        rows = conn.execute("""
            SELECT id, content, timestamp, source FROM person_content
            WHERE person_id = ?
        """, (pid,)).fetchall()

        capped, dropped = cap_messages(rows)
        if dropped:
            print(f"  TRUNCATED {name}: kept the most recent "
                  f"{MAX_MESSAGES_PER_PERSON} of {len(rows)} messages, "
                  f"dropped {dropped}", file=sys.stderr)

        body = "\n".join(
            f"[{mid}] {(ts or 'unknown')[:10]} ({src or 'unknown'}): {_ws_normalize(text)}"
            for mid, text, ts, src in capped)
        prompt = f"Person: {name}\n\nMessages:\n\n{body}"

        jobs.append((f"person-{pid}", prompt))
        sent_ids[pid] = {r[0] for r in capped}
        meta[pid] = compute_profile_meta(conn, pid)

    return jobs, sent_ids, meta


# --------------------------------------------------------------------------
# Storage — profiles and expertise
# --------------------------------------------------------------------------

def _wipe_existing_profile(conn, person_id: int) -> None:
    """Re-running this pass for a person must replace their profile and
    expertise, not duplicate/orphan them. Evidence has no FK to either table,
    so it must be deleted before its claim row or it would be silently
    orphaned (find_unevidenced_claims would not catch it — it only looks for
    claims missing evidence, never evidence missing a claim)."""
    conn.execute("""
        DELETE FROM evidence WHERE claim_table = 'expertise'
          AND claim_id IN (SELECT id FROM expertise WHERE person_id = ?)
    """, (person_id,))
    conn.execute("DELETE FROM expertise WHERE person_id = ?", (person_id,))
    conn.execute("""
        DELETE FROM evidence WHERE claim_table = 'person_profile'
          AND claim_id = ?
    """, (person_id,))
    conn.execute("DELETE FROM person_profile WHERE person_id = ?", (person_id,))


def store_profiles(conn, results: dict, sent_ids: dict[int, set[int]],
                    meta: dict[int, dict]) -> dict:
    """Write person_profile and expertise rows, each backed by verified
    evidence. Correction 4's two guards apply to every citation, whether it
    backs the profile itself (summary_evidence) or one expertise entry."""
    missing = [k for k, v in results.items() if v is None]
    if missing:
        print(f"WARNING: {len(missing)} jobs returned nothing: {missing[:10]}",
              file=sys.stderr)

    profiles_written = 0
    expertise_inserted = 0
    people_dropped_no_evidence = 0   # correction 4: zero surviving profile citations
    out_of_scope_dropped = 0         # correction 4: id not in what this person's job saw
    bad_quote_dropped = 0            # correction 4: quote doesn't appear in the cited row
    trigger_dropped = 0              # defense in depth; should stay 0

    for job_id, payload in results.items():
        if not payload:
            continue
        pid = int(job_id.split("-", 1)[1])
        allowed = sent_ids.get(pid, set())

        def verify(mid, model_quote):
            """Shared guard for both summary_evidence and expertise citations.
            Returns the database's own text on success, None on failure,
            updating the shared out_of_scope/bad_quote counters as it goes."""
            nonlocal out_of_scope_dropped, bad_quote_dropped
            if mid not in allowed:
                out_of_scope_dropped += 1
                return None
            row = conn.execute(
                "SELECT content FROM person_content WHERE id = ?", (mid,)).fetchone()
            if row is None:
                out_of_scope_dropped += 1
                return None
            if not quote_verified(row[0], model_quote):
                bad_quote_dropped += 1
                return None
            return _ws_normalize(row[0])

        verified_summary_evidence = []
        for ev in payload.get("summary_evidence") or []:
            db_quote = verify(ev.get("evidence_message_id"), ev.get("quote"))
            if db_quote is not None:
                verified_summary_evidence.append(
                    (ev.get("evidence_message_id"), db_quote))

        _wipe_existing_profile(conn, pid)   # idempotent re-run, not additive

        if not verified_summary_evidence:
            # Correction 4 / Task 3's principle: a profile that cannot cite
            # itself does not get stored, uncited, alongside real ones.
            people_dropped_no_evidence += 1
            continue

        summary = (payload.get("summary") or "").strip()
        # known_for is required (not left as NULL/empty): the prompt asks the
        # model to say plainly when nothing stands out rather than return
        # nothing at all. This fallback only fires if the model ignores that
        # instruction — defense in depth, not the primary mechanism.
        known_for = (payload.get("known_for") or "").strip() or \
            "no single specialty stands out from these messages"
        m = meta.get(pid, {})

        conn.execute("SAVEPOINT profile")
        try:
            conn.execute(
                "INSERT INTO person_profile (person_id, summary, years_active, "
                "chapter, known_for) VALUES (?,?,?,?,?)",
                (pid, summary, m.get("years_active"), m.get("chapter"), known_for))
            for mid, db_quote in verified_summary_evidence:
                conn.execute(
                    "INSERT INTO evidence (claim_table, claim_id, source_table, "
                    "source_id, quote) VALUES ('person_profile', ?, 'person_content', ?, ?)",
                    (pid, mid, db_quote))
        except sqlite3.IntegrityError:
            conn.execute("ROLLBACK TO SAVEPOINT profile")
            conn.execute("RELEASE SAVEPOINT profile")
            trigger_dropped += 1
            continue
        else:
            conn.execute("RELEASE SAVEPOINT profile")
            profiles_written += 1

        for exp in payload.get("expertise") or []:
            topic = exp.get("topic")
            if not topic:
                continue  # malformed despite schema requiring it; defense in depth

            # Strength is counted, not asked of the model (coordinator
            # correction): the model was not calibrating strong/moderate/
            # mentioned consistently on its own say-so, so it now supplies
            # per-message evidence and this pass counts how many distinct,
            # verified messages survive. Same two guards as summary_evidence
            # apply to every citation before it counts.
            seen_mids = set()
            verified = []
            for ev in exp.get("evidence") or []:
                mid = ev.get("evidence_message_id")
                db_quote = verify(mid, ev.get("quote"))
                if db_quote is None or mid in seen_mids:
                    continue
                seen_mids.add(mid)
                verified.append((mid, db_quote))

            if not verified:
                continue

            strength = STRENGTH_BY_EVIDENCE_COUNT[min(len(verified), 3)]

            conn.execute("SAVEPOINT expertise")
            try:
                eid = conn.execute(
                    "INSERT INTO expertise (person_id, topic, strength) "
                    "VALUES (?,?,?)", (pid, topic, strength)).lastrowid
                for mid, db_quote in verified:
                    conn.execute(
                        "INSERT INTO evidence (claim_table, claim_id, source_table, "
                        "source_id, quote) VALUES ('expertise', ?, 'person_content', ?, ?)",
                        (eid, mid, db_quote))
            except sqlite3.IntegrityError:
                # e.g. duplicate topic for this person — UNIQUE(person_id, topic)
                conn.execute("ROLLBACK TO SAVEPOINT expertise")
                conn.execute("RELEASE SAVEPOINT expertise")
                trigger_dropped += 1
            else:
                conn.execute("RELEASE SAVEPOINT expertise")
                expertise_inserted += 1

    return {
        "profiles_written": profiles_written,
        "expertise_inserted": expertise_inserted,
        "people_dropped_no_evidence": people_dropped_no_evidence,
        "out_of_scope_dropped": out_of_scope_dropped,
        "bad_quote_dropped": bad_quote_dropped,
        "trigger_dropped": trigger_dropped,
    }


# --------------------------------------------------------------------------
# Relationships — corrections 1-3. Plain SQL/Python, no model calls: this is
# counting distinct shared-group membership, not a judgment about closeness.
# --------------------------------------------------------------------------

def _earliest_by_person_source(conn) -> dict[tuple[int, str], tuple[int, str, str]]:
    """(person_id, source) -> (message id, timestamp, content) of that
    person's earliest message in that source, restricted to the six regional
    sources (correction 2). Ties broken by id for determinism."""
    placeholders = ",".join("?" * len(REGIONAL_SOURCES))
    rows = conn.execute(f"""
        WITH ranked AS (
            SELECT id, person_id, source, timestamp, content,
                   ROW_NUMBER() OVER (
                       PARTITION BY person_id, source
                       ORDER BY timestamp ASC, id ASC
                   ) AS rn
            FROM person_content
            WHERE person_id IS NOT NULL AND source IN ({placeholders})
        )
        SELECT person_id, source, id, timestamp, content FROM ranked WHERE rn = 1
    """, REGIONAL_SOURCES).fetchall()
    return {(pid, src): (mid, ts, content) for pid, src, mid, ts, content in rows}


def compute_relationships(conn) -> list[dict]:
    """Correction 1: distinct-membership co-occurrence, not a message-pair
    self-join. Correction 2: six regional sources only, requiring >= 2 shared
    groups (see module docstring for the measured pair counts that justified
    this). Correction 3: evidence is each person's own earliest message in
    the shared source where their overlap started soonest — never a
    fabricated or borrowed quote."""
    earliest = _earliest_by_person_source(conn)
    sources_by_person: dict[int, set[str]] = {}
    for (pid, src) in earliest:
        sources_by_person.setdefault(pid, set()).add(src)

    people = sorted(sources_by_person)
    out = []
    for i, a in enumerate(people):
        for b in people[i + 1:]:
            shared = sources_by_person[a] & sources_by_person[b]
            if len(shared) < MIN_SHARED_GROUPS:
                continue
            # The group where both people's overlap started soonest: the
            # later of the pair's two first-appearances in that group,
            # minimised over the shared groups.
            chosen = min(shared, key=lambda s: max(earliest[(a, s)][1],
                                                     earliest[(b, s)][1]))
            a_mid, _, a_content = earliest[(a, chosen)]
            b_mid, _, b_content = earliest[(b, chosen)]
            out.append({
                "person_a": a, "person_b": b, "strength": len(shared),
                "source": chosen,
                "a_evidence": (a_mid, _ws_normalize(a_content)),
                "b_evidence": (b_mid, _ws_normalize(b_content)),
            })
    return out


def _wipe_existing_relationships(conn) -> None:
    """Correction: INSERT OR IGNORE with UNIQUE(person_a,person_b,kind) means
    a re-run would never update `strength` if the bar changes. Wipe evidence
    before claims, then claims, so a re-run is a clean recompute rather than
    a silent accumulation of stale rows."""
    conn.execute("""
        DELETE FROM evidence WHERE claim_table = 'relationship'
          AND claim_id IN (SELECT id FROM relationship WHERE kind = 'chapter')
    """)
    conn.execute("DELETE FROM relationship WHERE kind = 'chapter'")


def store_relationships(conn) -> dict:
    pairs = compute_relationships(conn)
    _wipe_existing_relationships(conn)

    inserted = 0
    for rel in pairs:
        rid = conn.execute(
            "INSERT OR IGNORE INTO relationship (person_a, person_b, kind, strength) "
            "VALUES (?,?,'chapter',?)",
            (rel["person_a"], rel["person_b"], rel["strength"])).lastrowid
        if rid is None:
            continue
        a_mid, a_quote = rel["a_evidence"]
        b_mid, b_quote = rel["b_evidence"]
        conn.execute(
            "INSERT INTO evidence (claim_table, claim_id, source_table, source_id, quote) "
            "VALUES ('relationship', ?, 'person_content', ?, ?)", (rid, a_mid, a_quote))
        conn.execute(
            "INSERT INTO evidence (claim_table, claim_id, source_table, source_id, quote) "
            "VALUES ('relationship', ?, 'person_content', ?, ?)", (rid, b_mid, b_quote))
        inserted += 1

    return {"pairs_created": inserted, "pairs_considered": len(pairs)}


# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("db_path", type=Path)
    parser.add_argument("--yes", action="store_true",
                         help="skip the confirmation prompt and proceed")
    parser.add_argument("--limit", type=int, default=None,
                         help="only send the first N eligible people (cheap trial run, "
                              "or one batch of a chunked full pass with --offset)")
    parser.add_argument("--offset", type=int, default=0,
                         help="skip the first N eligible people (for batching the "
                              "full pass across multiple invocations)")
    parser.add_argument("--only", type=str, default=None,
                         help="only run this one person, by name")
    parser.add_argument("--skip-relationships", action="store_true",
                         help="skip SQL relationship derivation this run (use for "
                              "every batch but the last, when chunking with --limit)")
    parser.add_argument("--relationships-only", action="store_true",
                         help="make no model calls; only (re)derive relationships")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)
    conn.execute("PRAGMA foreign_keys = ON")

    # Create the schema before spending any money — if this fails, it fails
    # before a single job has been sent (no-op if already present).
    create_enrichment_tables(conn)

    if args.relationships_only:
        rel_stats = store_relationships(conn)
        verify_evidence_complete(conn)
        conn.commit()
        print(f"{rel_stats['pairs_created']} relationship pairs created "
              f"(considered {rel_stats['pairs_considered']})")
        return

    people = _select_people(conn, args.only, args.limit, args.offset)
    print(f"{len(people)} person/people selected "
          f"(floor: >= {MIN_MESSAGES_FOR_PROFILE} messages)")

    if not people:
        sys.exit("nothing to do")

    jobs, sent_ids, meta = build_jobs(conn, people)
    print(f"\n{len(jobs)} jobs to send")
    if not args.yes:
        sys.exit("pass --yes to proceed")

    results = run_all(jobs, PROMPT, SCHEMA)

    # Cache raw results before touching the (shared, concurrently-written) db —
    # a "database is locked" at commit time must not lose paid requests.
    cache_path = Path(__file__).parent / "output" / f"person_profiles_raw_{int(time.time())}.json"
    cache_path.write_text(json.dumps(results, indent=2))
    print(f"raw results cached to {cache_path}")

    stats = store_profiles(conn, results, sent_ids, meta)

    rel_stats = None
    if args.only:
        print("\n--only in use: relationships left untouched for this run")
    elif args.skip_relationships:
        print("\n--skip-relationships: relationships left untouched for this run")
    else:
        rel_stats = store_relationships(conn)

    verify_evidence_complete(conn)   # raise before committing anything
    conn.commit()

    print(f"\n{stats['profiles_written']} person profiles written")
    print(f"{stats['expertise_inserted']} expertise rows inserted")
    print(f"{stats['people_dropped_no_evidence']} people dropped — "
          f"no summary citation survived verification")
    print(f"{stats['out_of_scope_dropped']} citations dropped — "
          f"evidence_message_id outside what the model was shown for that person")
    print(f"{stats['bad_quote_dropped']} citations dropped — "
          f"quote did not appear in the cited message")
    if stats["trigger_dropped"]:
        print(f"{stats['trigger_dropped']} rows dropped by a constraint "
              f"(unexpected — investigate)")
    if rel_stats is not None:
        print(f"{rel_stats['pairs_created']} relationship pairs created "
              f"(considered {rel_stats['pairs_considered']})")


if __name__ == "__main__":
    main()
