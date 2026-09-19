#!/usr/bin/env python3
"""
Build a small, hand-written enrichment fixture from the real corpus.

Why this exists
---------------
The iOS app needs to render enriched entity records — "what is Doris" answered
as a synthesized record, not as three chat messages that happen to embed near
the word. Those records come from the Gemini enrichment pipeline, which is not
built yet. Rather than have the UI designed against imagined data (which is how
the first attempt went wrong), this writes a handful of records by hand, in the
real schema, sourced from real corpus rows.

It has a second job. Every record here was written by a human reading the same
messages the model will read, so it is also the acceptance target for the
enrichment pass: when Gemini produces entity records for real, the question is
whether they are as good as these.

What is deliberately NOT here
-----------------------------
Two corpus rows are questions, and both are excluded on purpose:

  person_content 42   Cece Garland, 2022-08-19
                      "Is this because we store ladders on Boris?"
  person_content 8167 Amy Lamboley, 2021-06-07
                      "we wouldn't be able to use Lucy's roof deck while driving?"

Someone asking whether the ladders are on Boris is not evidence that they are.
Recording either as a fact would be the exact failure this design exists to
prevent, so any pipeline output containing a "Boris stores the ladders" or
"the roof deck cannot be used while driving" fact has failed, however fluent
it reads.

The Doris ladder contradiction IS here, both halves, with their dates. Cece
checked the inventory on 19 Aug 2022 and found no ladders; Marcus saw at least
three on 24 Aug. Nobody ever reconciled it. The app must show both.

Voice: first person plural throughout. Lucy is the camp's own app and speaks as
part of it — "our bike fleet", not "the camp's bike fleet". Quoted evidence is
the exception: it is reproduced verbatim and never rephrased.

Scope: this fixture carries only the corpus rows its evidence cites — enough to
render records and satisfy the evidence triggers, not enough to test retrieval.
For retrieval work, use the full corpus at scripts/output/ps_knowledge.db.

Usage:
    python build_fixture.py [--out output/fixture_enriched.db]
"""
import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from enrich_schema import create_enrichment_tables, verify_evidence_complete

CORPUS = Path(__file__).resolve().parent / "output" / "ps_knowledge.db"

# --------------------------------------------------------------------------
# Entities. Each fact is (category, fact, asserted_on, [source_content_ids]).
# Every fact cites at least one real person_content row; the quote is copied
# verbatim from the corpus at build time rather than retyped here, so a fact
# can never drift away from the words that justify it.
# --------------------------------------------------------------------------

ENTITIES = [
    {
        "name": "Doris",
        "kind": "vehicle",
        "summary": (
            "One of our two storage vehicles. Doris carries build "
            "materials, tools, kitchen equipment and the med kit to playa, and "
            "doubles as a cool space during the event. Locked with a padlock; "
            "Boris is its counterpart."
        ),
        "first_seen": "2021-04-21",
        "last_seen": "2024-04-19",
        "aliases": [],
        "facts": [
            ("access", "The padlock code is 8765. If the padlock sticks, hit it hard with a hammer.",
             "2022-08-22", [76]),
            ("access", "The hammer needed to free the stuck padlock is itself stored inside Doris.",
             "2024-04-19", [15973]),
            ("contents", "Contains no ladders.", "2022-08-19", [41]),
            ("contents", "Contains at least three ladders.", "2022-08-24", [201]),
            ("contents", "Holds the lag bolts used to secure structures in high wind.",
             "2022-08-24", [190]),
            ("contents", "Holds spare sockets and drivers the build depends on.",
             "2022-08-24", [211]),
            ("contents", "Carries our med kit.", "2022-08-22", [11738]),
            ("contents", "Has a fridge.", "2022-09-05", [12154]),
            ("contents", "Kitchen pans are packed into Doris during strike.",
             "2022-09-05", [12117]),
            ("contents", "Holds one of our water drums.", "2022-08-25", [456]),
            ("contents", "The pink lost-and-found bucket sits about halfway in, reachable "
                         "from the side door.", "2022-09-07", [12242]),
            ("handling", "A hired driver delivers Doris to site; placement gives the driver "
                         "the intersection and we meet them on arrival.",
             "2022-07-24", [10674]),
        ],
    },
    {
        "name": "Boris",
        "kind": "vehicle",
        "summary": (
            "Our second storage vehicle. Boris carries our bike fleet and "
            "has been used as sleeping quarters. Unlike Doris it has no padlock."
        ),
        "first_seen": "2021-03-01",
        "last_seen": "2023-09-09",
        "aliases": ["home for wayward girls"],
        "facts": [
            ("contents", "Carries our bike fleet — 73 bikes in 2022.",
             "2022-07-21", [10561]),
            ("access", "Has no padlock.", "2022-08-22", [76]),
            ("handling", "Can be slept in, but the door cannot be fully closed: there is no "
                         "inner door handle and no ventilation, so it must be left ajar.",
             "2022-07-27", [10729]),
            ("contents", "Holds no ladders, as far as anyone knows.", "2022-08-19", [43]),
            ("history", "Used as sleeping quarters and remembered fondly as low budget luxury.",
             "2022-07-27", [10730]),
        ],
    },
    {
        "name": "Lucy",
        "kind": "vehicle",
        "summary": (
            "Our snail-shaped art car, and this app's namesake. Lucy carries "
            "a sound system, runs sunrise sets on playa, and is registered and "
            "insured as a road vehicle."
        ),
        "first_seen": "2020-09-10",
        "last_seen": "2024-04-19",
        "aliases": ["Lucy S. Cargo"],
        "facts": [
            ("handling", "Drivers must provide a driver's licence for insurance and must have "
                         "no recent violations or accidents.", "2021-07-29", [8356]),
            ("handling", "Requires insurance and registration to operate.", "2021-06-16", [8238]),
            ("history", "A fire put a hole through the top of Lucy in 2019.",
             "2021-08-28", [8584]),
            ("history", "The build crew spent two days repairing Lucy before playa in 2021.",
             "2021-08-29", [8610]),
        ],
    },
]

# alias -> the corpus row that shows the name being used
ALIAS_EVIDENCE = {
    "home for wayward girls": 10730,
    "Lucy S. Cargo": 8534,
}

# person_id -> (summary, years_active, chapter, known_for, [source_ids])
PROFILES = {
    17: ("Runs build logistics — vehicle placement, tools, and making sure the "
         "things build depends on actually arrive.", "2022", None,
         "build logistics", [10674, 211]),
    16: ("Keeps our inventories. Checked the Doris inventory in 2022 and "
         "chased down what was and wasn't in it.", "2021-2022", None,
         "inventory", [41]),
    3: ("Keeper of our camp manual — the person who knows where the written "
        "answer is, including lock codes.", "2022", None, "camp manual", [76]),
}

# (person_id, topic, strength, [source_ids])
EXPERTISE = [
    (17, "build logistics", "strong", [10674]),
    (17, "tools", "moderate", [211]),
    (16, "inventory", "strong", [41]),
    (3, "camp manual", "strong", [76]),
    (55, "bikes", "strong", [10561]),
    (133, "med kit", "strong", [11738]),
]

# (person_a, person_b, kind, strength, [source_ids])
RELATIONSHIPS = [
    (20, 133, "builds_with", 1, [11738]),
    (1, 20, "builds_with", 1, [15973]),
]

# (title, story, year, people, [source_ids])
LORE = [
    ("The hammer is inside the locked box",
     "Doris's padlock sticks, and our remedy is to hit it hard with a hammer. "
     "In April 2024 one of us padlocked Doris — and the hammer was inside. The tool "
     "needed to open the box was in the box.",
     2024, "Piotr, Ana B", [15966, 15973]),
    ("Home for wayward girls",
     "Before it was simply the bike truck, Boris was sleeping quarters. Its door "
     "cannot shut — no inner handle, no ventilation — so it stayed ajar all week. "
     "Remembered by its residents as low budget luxury, and worth doing again.",
     2022, "Opal Jain, Madeline Gale", [10730, 10729]),
]


def copy_source_rows(corpus: sqlite3.Connection, fx: sqlite3.Connection,
                     content_ids: set[int]) -> None:
    """Copy the cited person_content rows, plus every person they involve."""
    fx.executescript("""
        CREATE TABLE person (
            id INTEGER PRIMARY KEY, name TEXT, email TEXT, phone TEXT,
            message_count INTEGER, first_seen TEXT, last_seen TEXT);
        CREATE TABLE person_content (
            id INTEGER PRIMARY KEY, person_id INTEGER, content TEXT, source TEXT,
            content_type TEXT, timestamp TEXT, media_ref TEXT);
        CREATE TABLE camp_knowledge (
            id INTEGER PRIMARY KEY, title TEXT, content TEXT, source_file TEXT,
            category TEXT, year INTEGER);
    """)

    placeholders = ",".join("?" * len(content_ids))
    rows = corpus.execute(
        f"SELECT id, person_id, content, source, content_type, timestamp, media_ref "
        f"FROM person_content WHERE id IN ({placeholders})",
        tuple(content_ids)).fetchall()
    if len(rows) != len(content_ids):
        missing = content_ids - {r[0] for r in rows}
        raise SystemExit(f"corpus is missing cited rows: {sorted(missing)}")
    fx.executemany("INSERT INTO person_content VALUES (?,?,?,?,?,?,?)", rows)

    # Everyone who authored a cited row, plus everyone named in a claim.
    need = {r[1] for r in rows if r[1] is not None}
    need |= set(PROFILES)
    need |= {p for p, _, _, _ in EXPERTISE}
    need |= {a for a, _, _, _, _ in RELATIONSHIPS}
    need |= {b for _, b, _, _, _ in RELATIONSHIPS}
    ph = ",".join("?" * len(need))
    people = corpus.execute(
        f"SELECT id, name, email, phone, message_count, first_seen, last_seen "
        f"FROM person WHERE id IN ({ph})", tuple(need)).fetchall()
    fx.executemany("INSERT INTO person VALUES (?,?,?,?,?,?,?)", people)
    return {r[0]: r[2] for r in rows}


def quote_for(quotes: dict[int, str], cid: int) -> str:
    """The exact corpus text, whitespace-normalised for display."""
    return " ".join(quotes[cid].split())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="output/fixture_enriched.db")
    args = ap.parse_args()

    out = Path(args.out)
    if not out.is_absolute():
        out = Path(__file__).resolve().parent / out
    if out.exists():
        out.unlink()
    out.parent.mkdir(parents=True, exist_ok=True)

    if not CORPUS.exists():
        raise SystemExit(f"corpus not found at {CORPUS}")

    cited: set[int] = set()
    for e in ENTITIES:
        for _, _, _, ids in e["facts"]:
            cited |= set(ids)
        for a in e["aliases"]:
            cited.add(ALIAS_EVIDENCE[a])
    for _, _, _, _, ids in PROFILES.values():
        cited |= set(ids)
    for _, _, _, ids in EXPERTISE:
        cited |= set(ids)
    for _, _, _, _, ids in RELATIONSHIPS:
        cited |= set(ids)
    for _, _, _, _, ids in LORE:
        cited |= set(ids)

    corpus = sqlite3.connect(f"file:{CORPUS}?mode=ro", uri=True)
    fx = sqlite3.connect(str(out))
    quotes = copy_source_rows(corpus, fx, cited)
    create_enrichment_tables(fx)

    def cite(claim_table: str, claim_id: int, ids: list[int]) -> None:
        for cid in ids:
            fx.execute(
                "INSERT INTO evidence (claim_table, claim_id, source_table, "
                "source_id, quote) VALUES (?,?,'person_content',?,?)",
                (claim_table, claim_id, cid, quote_for(quotes, cid)))

    for e in ENTITIES:
        cur = fx.execute(
            "INSERT INTO entity (name, kind, summary, first_seen, last_seen, "
            "mention_count) VALUES (?,?,?,?,?,?)",
            (e["name"], e["kind"], e["summary"], e["first_seen"], e["last_seen"],
             len(e["facts"])))
        eid = cur.lastrowid
        # An entity's own evidence is the union of what its facts rest on.
        cite("entity", eid, sorted({i for _, _, _, ids in e["facts"] for i in ids}))
        for alias in e["aliases"]:
            fx.execute("INSERT INTO entity_alias (entity_id, alias) VALUES (?,?)",
                       (eid, alias))
        for category, fact, asserted_on, ids in e["facts"]:
            fid = fx.execute(
                "INSERT INTO entity_fact (entity_id, fact, category, asserted_on) "
                "VALUES (?,?,?,?)", (eid, fact, category, asserted_on)).lastrowid
            cite("entity_fact", fid, ids)

    for pid, (summary, years, chapter, known_for, ids) in PROFILES.items():
        fx.execute("INSERT INTO person_profile (person_id, summary, years_active, "
                   "chapter, known_for) VALUES (?,?,?,?,?)",
                   (pid, summary, years, chapter, known_for))
        cite("person_profile", pid, ids)

    for pid, topic, strength, ids in EXPERTISE:
        xid = fx.execute("INSERT INTO expertise (person_id, topic, strength) "
                         "VALUES (?,?,?)", (pid, topic, strength)).lastrowid
        cite("expertise", xid, ids)

    for a, b, kind, strength, ids in RELATIONSHIPS:
        rid = fx.execute("INSERT INTO relationship (person_a, person_b, kind, "
                         "strength) VALUES (?,?,?,?)", (a, b, kind, strength)).lastrowid
        cite("relationship", rid, ids)

    for title, story, year, people, ids in LORE:
        lid = fx.execute("INSERT INTO lore (title, story, year, people) "
                         "VALUES (?,?,?,?)", (title, story, year, people)).lastrowid
        cite("lore", lid, ids)

    verify_evidence_complete(fx)
    fx.commit()

    counts = {t: fx.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("entity", "entity_alias", "entity_fact", "person_profile",
                        "expertise", "relationship", "lore", "evidence")}
    print(f"wrote {out}")
    for t, n in counts.items():
        print(f"  {t:<15} {n}")
    print(f"  {'source rows':<15} {len(cited)}")
    print("evidence completeness: OK")


if __name__ == "__main__":
    main()
