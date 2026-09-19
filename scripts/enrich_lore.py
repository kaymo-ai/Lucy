#!/usr/bin/env python3
"""
Fill the empty `lore` table — the stories the camp still tells.

The table has existed since the schema did, is copied by build_preview_db.py,
has fixture support, and held zero rows: no pass ever wrote it. This one does.
It walks the sent-message corpus in date windows (month-sized, sparse months
merged so every window has enough conversation to hold a story), asks the
frontier model for memorable incidents — a fire through Lucy's roof, the year
a structure failed, a running joke with staying power — and writes `lore`
rows. Logistics and facts are deliberately out of scope; camp_fact owns those.

The invariant holds here the same way it does everywhere else: every story is
a claim, and every claim cites the person_content rows it came from through
`evidence`. The model supplies message ids, not quotes — the quote is read
back from the database so it cannot drift from what was said (the same
defence enrich_entities.py uses, and safe here for the same reason: chat
messages are short, so storing the whole row as the quote reproduces nothing
a fixed-width window would have mangled). A story citing fewer than two
messages that were actually shown to the model is refused: one message is a
remark, not a story the camp tells.

Usage:
    python enrich_lore.py ./output/ps_knowledge.db              # dry run: windows + prompt preview, no spend
    python enrich_lore.py ./output/ps_knowledge.db --yes
    python enrich_lore.py ./output/ps_knowledge.db --limit 3 --yes   # cheap trial: first 3 windows only
"""
import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from vertex_client import run_all
from enrich_schema import create_enrichment_tables, verify_evidence_complete

PROMPT = (HERE / "prompts" / "lore_stories.md").read_text()

# A story needs corroboration. One message is a remark; two or more messages —
# the incident and the reaction, or the joke and its callback — is the minimum
# shape of something the camp actually tells.
MIN_SUPPORT = 2

# Below this a month is conversation too thin to hold a story arc, so sparse
# months are folded together until a window clears the bar. The corpus runs
# about 330 sent messages a month on average but is heavily bursty — August
# spikes, winters go near-silent — so without merging, half the windows would
# be off-season trickle asked to produce stories they cannot contain.
MIN_WINDOW_MESSAGES = 150

# Years outside the camp's plausible existence are model arithmetic gone
# wrong, not history. Better a story with no year than a wrong one.
YEAR_MIN, YEAR_MAX = 2000, 2026

SCHEMA = {
    "type": "object",
    "properties": {
        "stories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "story": {"type": "string"},
                    "year": {"type": "integer"},
                    "people": {"type": "array", "items": {"type": "string"}},
                    "supporting_message_ids": {
                        "type": "array", "items": {"type": "integer"}},
                },
                "required": ["title", "story", "people",
                             "supporting_message_ids"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["stories"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------
# Windows: calendar months, merged forward while sparse
# --------------------------------------------------------------------------

def build_windows(conn, min_messages: int = MIN_WINDOW_MESSAGES) -> list[tuple[str, list[tuple]]]:
    """[(window_label, [(id, date, source, who, text), ...]), ...] in time order.

    Windows are date-bounded, not conversation-bounded, because a story is an
    event in time: the incident and the camp's reaction to it cluster around a
    date, across whichever chats were active. Months are the unit; consecutive
    sparse months merge forward until a window holds enough conversation, and
    a thin tail folds into the window before it rather than going out alone.
    """
    rows = conn.execute("""
        SELECT pc.id, substr(pc.timestamp, 1, 10), pc.source,
               COALESCE(p.name, '?'), pc.content
        FROM person_content pc LEFT JOIN person p ON p.id = pc.person_id
        WHERE pc.content_type = 'sent'
          AND pc.content IS NOT NULL AND LENGTH(TRIM(pc.content)) > 0
          AND pc.timestamp IS NOT NULL
        ORDER BY pc.timestamp, pc.id
    """).fetchall()

    # Group into calendar months first; merging decisions come after, so the
    # month boundaries themselves never depend on the threshold.
    months: list[tuple[str, list[tuple]]] = []
    for row in rows:
        month = row[1][:7]
        if not months or months[-1][0] != month:
            months.append((month, []))
        months[-1][1].append(row)

    windows: list[tuple[str, str, list[tuple]]] = []  # (first_month, last_month, rows)
    pending: list[tuple] = []
    pending_start = None
    for month, mrows in months:
        if pending_start is None:
            pending_start = month
        pending.extend(mrows)
        if len(pending) >= min_messages:
            windows.append((pending_start, month, pending))
            pending, pending_start = [], None
    if pending:
        if windows:
            first, _, wrows = windows[-1]
            windows[-1] = (first, months[-1][0], wrows + pending)
        else:
            windows.append((pending_start, months[-1][0], pending))

    return [(f"{first}..{last}" if first != last else first, wrows)
            for first, last, wrows in windows]


def build_jobs(windows: list[tuple[str, list[tuple]]]) -> tuple[list[tuple[str, str]], dict[str, set[int]]]:
    """One job per window. seen_ids[job_id] is the set of message ids that job
    actually put in front of the model — a citation outside that set is not
    evidence, because the row existing proves nothing about whether the model
    saw it (same rule as enrich_personality.py)."""
    jobs: list[tuple[str, str]] = []
    seen_ids: dict[str, set[int]] = {}

    for i, (label, wrows) in enumerate(windows):
        # Within a window, group by chat then time so each conversation reads
        # contiguously — the window's date bounds do the story-clustering; the
        # ordering keeps each thread followable.
        ordered = sorted(wrows, key=lambda r: (r[2] or "", r[1], r[0]))
        body = "\n".join(
            f"[{mid}] {date} {who}: " + " ".join(text.split())
            for mid, date, _source, who, text in ordered)
        jid = f"lore-{i}"
        jobs.append((jid, f"Messages from {label}:\n\n{body}"))
        seen_ids[jid] = {r[0] for r in wrows}

    return jobs, seen_ids


# --------------------------------------------------------------------------
# Validation: only stories the corpus supports, naming only people it names
# --------------------------------------------------------------------------

def known_names(conn) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT name FROM person WHERE name IS NOT NULL")]


def _name_in_corpus(name: str, corpus_names: list[str]) -> bool:
    """A model-supplied name counts when it is a corpus name or a contiguous
    whole-word run of one — "Piotr" matches "Piotr Moravec", "Ana B" matches
    "Ana B Torres". Substring alone is not enough: "Ana" must not match
    "Diana", hence word boundaries rather than `in`."""
    want = tuple(name.strip().casefold().split())
    if not want:
        return False
    for cname in corpus_names:
        words = cname.strip().casefold().split()
        for i in range(len(words) - len(want) + 1):
            if tuple(words[i:i + len(want)]) == want:
                return True
    return False


def collect_stories(results: dict, seen_ids: dict[str, set[int]],
                    corpus_names: list[str]) -> tuple[list[dict], dict]:
    """Turn raw window results into validated story dicts.

    Everything the model asserts is checked against what it was shown: cited
    ids outside the job's own window are discarded, and a story left with
    fewer than MIN_SUPPORT real citations is refused entirely — under-cited
    lore is exactly the "claim no row supports" the invariant exists to stop.
    People are filtered to names the corpus names; a stranger's name in the
    people column would be an invention wearing a byline.
    """
    missing = [k for k, v in results.items() if v is None]
    if missing:
        print(f"WARNING: {len(missing)} windows returned nothing: {missing[:5]}",
              file=sys.stderr)

    stories: list[dict] = []
    stats = {"refused_support": 0, "dropped_handle": 0, "dropped_malformed": 0,
             "names_filtered": 0}

    for job_id, payload in results.items():
        if not payload:
            continue
        shown = seen_ids.get(job_id, set())
        for s in payload.get("stories") or []:
            title = (s.get("title") or "").strip()
            text = (s.get("story") or "").strip()
            if not title or not text:
                stats["dropped_malformed"] += 1
                continue
            # The prompt forbids @handles in the retelling; don't trust it
            # blindly. A handle is a transcript leaking into prose, and the
            # safe move is to refuse the story, not to edit its words.
            if "@" in text or "@" in title:
                stats["dropped_handle"] += 1
                continue

            ids = sorted({mid for mid in (s.get("supporting_message_ids") or [])
                          if isinstance(mid, int) and mid in shown})
            if len(ids) < MIN_SUPPORT:
                stats["refused_support"] += 1
                continue

            people = []
            for name in s.get("people") or []:
                if _name_in_corpus(name, corpus_names):
                    people.append(name.strip())
                else:
                    stats["names_filtered"] += 1

            year = s.get("year")
            if not isinstance(year, int) or not (YEAR_MIN <= year <= YEAR_MAX):
                year = None

            stories.append({"title": title, "story": text, "year": year,
                            "people": people, "ids": set(ids)})

    return stories, stats


# --------------------------------------------------------------------------
# Consolidation: the same incident found in two windows becomes one row
# --------------------------------------------------------------------------
#
# Deterministic heuristic, not a second model call — the same choice
# merge_duplicate_entities made and for the same reasons: free, idempotent,
# and its failure mode is inspectable. A model asked "are these the same
# story" gives a different answer on a different day; a token-overlap
# threshold gives the same one every run. Evidence overlap cannot be the
# signal here (windows are disjoint date ranges, so a retelling in a later
# window cites different messages than the original), which leaves the text.
#
# The threshold errs toward NOT merging: a surviving duplicate is redundancy
# the reader shrugs at, but a wrong merge silently deletes a real story.

_STOPWORDS = {
    "that", "this", "with", "from", "they", "them", "their", "there",
    "were", "have", "been", "when", "what", "then", "than", "into",
    "about", "after", "before", "over", "under", "very", "much",
    "camp", "year", "would", "could", "still", "which", "because",
}


def _signature(story: dict) -> set[str]:
    words = re.findall(r"[a-z0-9']+",
                       (story["title"] + " " + story["story"]).casefold())
    return {w for w in words if len(w) >= 4 and w not in _STOPWORDS}


def _same_incident(a: dict, b: dict) -> bool:
    # Different stated years are different incidents, full stop — the camp
    # can absolutely have a structure fail twice.
    if a["year"] is not None and b["year"] is not None and a["year"] != b["year"]:
        return False
    sa, sb = _signature(a), _signature(b)
    if not sa or not sb:
        return False
    overlap = len(sa & sb) / len(sa | sb)
    return overlap >= 0.5


def _merge_stories(a: dict, b: dict) -> dict:
    """The retelling with more supporting messages survives — more citations
    means the model had more to work from. Ties break by longer story, then
    by title, so the outcome never depends on input order."""
    def key(s):
        return (-len(s["ids"]), -len(s["story"]), s["title"].casefold())
    survivor, loser = sorted([a, b], key=key)
    merged_people = list(survivor["people"])
    for name in loser["people"]:
        if name.casefold() not in {p.casefold() for p in merged_people}:
            merged_people.append(name)
    return {
        "title": survivor["title"],
        "story": survivor["story"],
        "year": survivor["year"] if survivor["year"] is not None else loser["year"],
        "people": merged_people,
        # Evidence pools: the merged row rests on everything both sightings
        # rested on, which is what makes the merge honest rather than lossy.
        "ids": survivor["ids"] | loser["ids"],
    }


def consolidate(stories: list[dict]) -> tuple[list[dict], int]:
    """Repeated pairwise merging until stable, because matches chain: window
    A's telling and window C's retelling may each clear the threshold only
    against window B's."""
    merged_count = 0
    stories = list(stories)
    while True:
        found = False
        for i in range(len(stories)):
            for j in range(i + 1, len(stories)):
                if _same_incident(stories[i], stories[j]):
                    merged = _merge_stories(stories[i], stories[j])
                    stories = [s for k, s in enumerate(stories)
                               if k not in (i, j)] + [merged]
                    merged_count += 1
                    found = True
                    break
            if found:
                break
        if not found:
            return stories, merged_count


# --------------------------------------------------------------------------
# Storage: wipe-and-rewrite the whole table
# --------------------------------------------------------------------------

def store(conn, stories: list[dict]) -> dict:
    """Replace the lore table's contents with this run's stories.

    The wipe is whole-table, not per-window like enrich_documents' per-
    document wipe, and deliberately so: consolidation is global — a merged
    story cites messages from several windows — so no per-window slice of the
    table is independently replaceable. This stage is the lore table's only
    writer in the real database (the fixture writes its own separate file),
    which is what makes owning the whole table safe. Evidence goes first,
    scoped by claim_table, so nothing is ever orphaned; a --limit trial run
    therefore leaves a partial table, which is what a trial is.
    """
    conn.execute("DELETE FROM evidence WHERE claim_table = 'lore'")
    conn.execute("DELETE FROM lore")

    inserted = 0
    # Sorted insert so identical inputs produce an identical table, ids and all.
    for s in sorted(stories, key=lambda s: (s["year"] or 0, s["title"].casefold())):
        lid = conn.execute(
            "INSERT INTO lore (title, story, year, people) VALUES (?,?,?,?)",
            (s["title"], s["story"], s["year"], ", ".join(s["people"]))).lastrowid
        for mid in sorted(s["ids"]):
            # The quote is the database's own text, never the model's. A chat
            # message is short enough that the whole row is the right quote —
            # the fixed-window mangling that forced enrich_documents to locate
            # substrings cannot happen here.
            row = conn.execute(
                "SELECT content FROM person_content WHERE id = ?", (mid,)).fetchone()
            conn.execute(
                "INSERT INTO evidence (claim_table, claim_id, source_table, "
                "source_id, quote) VALUES ('lore', ?, 'person_content', ?, ?)",
                (lid, mid, row[0]))
        inserted += 1

    verify_evidence_complete(conn)   # raise before committing anything
    conn.commit()
    return {"stories_inserted": inserted}


# --------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db_path", type=Path)
    parser.add_argument("--yes", action="store_true",
                        help="skip the confirmation prompt and proceed")
    parser.add_argument("--limit", type=int, default=None,
                        help="only send the first N windows (cheap trial run)")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)

    # Create the schema before spending any money — if this fails, it fails
    # before a single job has been sent (no-op if already present).
    create_enrichment_tables(conn)

    windows = build_windows(conn)
    if args.limit is not None:
        windows = windows[:args.limit]

    print(f"{len(windows)} window(s) to send")
    for label, wrows in windows:
        print(f"  {label:<18} {len(wrows)} messages")

    jobs, seen_ids = build_jobs(windows)

    if not args.yes:
        # Dry run: show what would be asked, spend nothing.
        if jobs:
            print("\nsystem prompt: prompts/lore_stories.md")
            print(f"first window prompt ({jobs[0][0]}), first 600 chars:\n")
            print(jobs[0][1][:600])
        print("\ndry run — pass --yes to spend money on this")
        return

    results = run_all(jobs, PROMPT, SCHEMA)

    # Cache raw results before touching the (shared, concurrently-written) db —
    # a "database is locked" at commit time must not lose paid requests.
    cache_path = HERE / "output" / f"lore_stories_raw_{int(time.time())}.json"
    cache_path.write_text(json.dumps(results, indent=2))
    print(f"raw results cached to {cache_path}")

    stories, stats = collect_stories(results, seen_ids, known_names(conn))
    stories, merged = consolidate(stories)
    stored = store(conn, stories)

    print(f"\n{stored['stories_inserted']} stories written "
          f"({merged} cross-window duplicate(s) merged)")
    print(f"{stats['refused_support']} refused — fewer than {MIN_SUPPORT} "
          f"supporting messages the model was actually shown")
    if stats["dropped_handle"]:
        print(f"{stats['dropped_handle']} dropped for @handles in the retelling")
    if stats["names_filtered"]:
        print(f"{stats['names_filtered']} name(s) removed — not names the corpus uses")
    if stats["dropped_malformed"]:
        print(f"{stats['dropped_malformed']} dropped for missing title/story (unexpected)")

    for title, year, people in conn.execute(
            "SELECT title, year, people FROM lore ORDER BY year, title"):
        who = f" — {people}" if people else ""
        print(f"  {year or '????'}  {title}{who}")


if __name__ == "__main__":
    main()
