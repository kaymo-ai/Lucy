#!/usr/bin/env python3
"""Load scripts/manual_facts.json into camp_fact.

Enrichment can only extract what somebody typed. Plenty of what a camp knows
about itself was never typed at all -- who is married to whom, whose kid is
which age, who does not eat meat, which two people should not share a shift.
This is where that goes, and it becomes an ordinary camp_fact row so it is
retrieved, ranked by recency, embedded by E7 and cited exactly like every
other fact. The invariant is untouched: retrieval still supplies it and the
model still only phrases it. What changed is where the row came from.

EVERY CLAIM MUST CITE A SOURCE, and evidence.source_table is CHECK-constrained
to person_content, camp_knowledge and camp_member. So this creates ONE
camp_knowledge document standing for the file and points every fact at it.
That is honest rather than a workaround -- the source really is a document the
camp maintains, and it shows up in a citation as "[from our Camp facts added
by hand]".

Re-runnable. It deletes only the rows it previously wrote, found by their
evidence pointing at that document, so editing the file and running again
replaces the set rather than doubling it.

RUN IT AFTER ENRICHMENT, before embed_claims.py. camp_fact must exist, and the
vectors have to cover these rows like any other -- check_shipped_db.py refuses
a database whose claim_embedding coverage is partial.

Usage:
    python ingest_manual_facts.py ./output/ps_knowledge.db
"""
import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FACTS_FILE = HERE / "manual_facts.json"


def load_file() -> dict:
    if not FACTS_FILE.exists():
        raise SystemExit(f"no such file: {FACTS_FILE}")
    return json.loads(FACTS_FILE.read_text(encoding="utf-8"))


def source_document(conn, title: str) -> int:
    """The camp_knowledge row that every manual fact cites.

    Its content is the file's own readme, so somebody reading the corpus finds
    an explanation rather than an orphan document with a suggestive title.
    """
    row = conn.execute(
        "SELECT id FROM camp_knowledge WHERE title = ?", (title,)).fetchone()
    if row:
        return row[0]
    body = ("Facts the camp added by hand, because the chat never wrote them "
            "down. Maintained in scripts/manual_facts.json.")
    cur = conn.execute(
        "INSERT INTO camp_knowledge (title, content, source_file, category, year)"
        " VALUES (?, ?, ?, ?, ?)",
        (title, body, "manual_facts.json", "general", None))
    return cur.lastrowid


def clear_previous(conn, doc_id: int) -> int:
    """Remove the facts written by an earlier run, and only those.

    Identified by their evidence citing the standing document, which is why
    the evidence is written for every fact rather than only where it is
    interesting: it is also how this run finds its own footprint.
    """
    ids = [r[0] for r in conn.execute(
        "SELECT claim_id FROM evidence"
        " WHERE claim_table = 'camp_fact' AND source_table = 'camp_knowledge'"
        "   AND source_id = ?", (doc_id,))]
    if not ids:
        return 0
    marks = ",".join("?" * len(ids))
    conn.execute(f"DELETE FROM evidence WHERE claim_table = 'camp_fact'"
                 f" AND claim_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM camp_fact WHERE id IN ({marks})", ids)
    return len(ids)


def run(db_path: str) -> int:
    data = load_file()
    facts = data.get("facts", [])
    title = data.get("document_title", "Camp facts added by hand")
    if not facts:
        print("manual_facts.json holds no facts; nothing to do")
        return 0

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        doc_id = source_document(conn, title)
        removed = clear_previous(conn, doc_id)
        if removed:
            print(f"replaced {removed} fact(s) from a previous run")

        written = 0
        for entry in facts:
            text = (entry.get("fact") or "").strip()
            topic = (entry.get("topic") or "general").strip()
            if not text:
                continue
            cur = conn.execute(
                "INSERT INTO camp_fact (topic, fact, category, year)"
                " VALUES (?, ?, ?, ?)",
                (topic, text, entry.get("category"), entry.get("year")))
            claim_id = cur.lastrowid
            # The quote is the fact itself. There is no earlier wording to
            # point at -- a human wrote this sentence, and pretending it was
            # extracted from something would be the one dishonest thing this
            # file could do.
            conn.execute(
                "INSERT INTO evidence (claim_table, claim_id, source_table,"
                " source_id, quote) VALUES ('camp_fact', ?, 'camp_knowledge',"
                " ?, ?)", (claim_id, doc_id, text))
            written += 1
            print(f"  + [{topic}] {text[:70]}")

        conn.commit()
        print(f"\n{written} manual fact(s) written, citing "
              f"camp_knowledge {doc_id} ({title!r})")
        return written
    finally:
        conn.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__.strip().splitlines()[-1])
    if not Path(sys.argv[1]).exists():
        raise SystemExit(f"no database at {sys.argv[1]}")
    run(sys.argv[1])
