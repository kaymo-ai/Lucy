import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrich_schema import create_enrichment_tables, verify_evidence_complete
from enrich_documents import (
    build_jobs,
    chunk_document,
    locate_quote,
    store,
)


# --------------------------------------------------------------------------
# Chunking: paragraph boundaries, target size, exact-substring reconstruction
# --------------------------------------------------------------------------

def test_short_document_is_a_single_chunk():
    content = "Para one.\n\nPara two.\n\nPara three."
    chunks = chunk_document(content, target=6000)
    assert chunks == [content]


def test_long_document_splits_on_paragraph_boundaries():
    paras = [f"Paragraph {i}. " + ("x" * 500) for i in range(20)]
    content = "\n\n".join(paras)
    chunks = chunk_document(content, target=2000)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c) <= 2000 or "\n\n" not in c  # oversized single paragraph is the only exception


def test_chunks_are_verbatim_slices_of_the_original_content():
    """Rejoining chunks (with the paragraph separator) must reproduce the
    original text exactly — this is what lets evidence extraction locate a
    quote's real position in the source document."""
    paras = [f"Paragraph {i} says something specific about topic {i}." for i in range(10)]
    content = "\n\n".join(paras)
    chunks = chunk_document(content, target=100)
    assert "\n\n".join(chunks) == content


def test_oversized_single_paragraph_is_hard_split():
    content = "x" * 15000  # no blank lines at all — PDF extraction artifact
    chunks = chunk_document(content, target=6000)
    assert len(chunks) == 3
    assert "".join(chunks) == content


def test_empty_document_returns_itself():
    assert chunk_document("", target=6000) == [""]


# --------------------------------------------------------------------------
# locate_quote: whitespace/case tolerant search, exact original substring
# --------------------------------------------------------------------------

def test_locate_quote_finds_exact_match():
    text = "The gray water tank is 4x4x4 and lives behind Doris."
    assert locate_quote(text, "the gray water tank is 4x4x4") == \
        "The gray water tank is 4x4x4"


def test_locate_quote_tolerant_of_whitespace_and_case():
    text = "Gray   water   goes   in   the tank.\nWe empty it weekly."
    located = locate_quote(text, "GRAY WATER GOES IN THE TANK")
    assert located == "Gray   water   goes   in   the tank"


def test_locate_quote_handles_multi_space_run_before_the_quote():
    text = "Intro line.\n\n\nGray water goes in the tank. End of section."
    located = locate_quote(text, "gray water goes in the tank")
    assert located == "Gray water goes in the tank"


def test_locate_quote_returns_none_when_absent():
    text = "We have no ladders in here as far as I can tell."
    assert locate_quote(text, "there are three ladders") is None


def test_locate_quote_returns_none_for_empty_quote():
    assert locate_quote("some content", "") is None
    assert locate_quote("some content", "   ") is None


def test_locate_quote_preserves_original_punctuation_inside_span():
    text = "United Site Services removes it at end of week, per contract."
    located = locate_quote(text, "united site services removes it at end of week")
    assert located == "United Site Services removes it at end of week"


# --------------------------------------------------------------------------
# build_jobs: chunking + prompt shape
# --------------------------------------------------------------------------

def test_build_jobs_one_job_per_chunk():
    documents = [
        (1, "Short Doc", "Just one short paragraph.", 2023),
        (2, "Long Doc", "\n\n".join([f"Paragraph {i} " + "x" * 500 for i in range(20)]), 2019),
    ]
    jobs, chunk_texts, doc_years = build_jobs(documents)
    doc1_jobs = [j for j in jobs if j[0].startswith("doc-1-")]
    doc2_jobs = [j for j in jobs if j[0].startswith("doc-2-")]
    assert len(doc1_jobs) == 1
    assert len(doc2_jobs) >= 1
    assert doc_years == {1: 2023, 2: 2019}
    assert "Short Doc" in doc1_jobs[0][1]
    assert all(jid in chunk_texts for jid, _ in jobs)


def test_build_jobs_includes_year_when_present_and_omits_when_absent():
    documents = [
        (1, "Doc With Year", "Some content here.", 2022),
        (2, "Doc Without Year", "Other content here.", None),
    ]
    jobs, _, _ = build_jobs(documents)
    prompt_by_doc = {jid: prompt for jid, prompt in jobs}
    assert "Year: 2022" in prompt_by_doc["doc-1-chunk-0"]
    assert "Year:" not in prompt_by_doc["doc-2-chunk-0"]


# --------------------------------------------------------------------------
# store(): quote verification, per-document wipe/idempotency, evidence
# --------------------------------------------------------------------------

@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE person (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE person_content (id INTEGER PRIMARY KEY, content TEXT);
        CREATE TABLE camp_knowledge (
            id INTEGER PRIMARY KEY, title TEXT, content TEXT,
            source_file TEXT, category TEXT, year INTEGER
        );
        INSERT INTO camp_knowledge (id, title, content, year) VALUES
            (1, 'PS Manual', 'Gray water goes in the 4x4x4 tank. We empty it weekly.', 2023),
            (2, 'PS Manual Old', 'No ladders are stored in Doris this year.', 2019);
    """)
    c.execute("PRAGMA foreign_keys = ON")
    create_enrichment_tables(c)
    return c


def _payload(facts=None):
    return {"facts": facts or []}


def test_valid_fact_is_stored_with_located_text_not_models(conn):
    chunk_texts = {"doc-1-chunk-0": "Gray water goes in the 4x4x4 tank. We empty it weekly."}
    doc_years = {1: 2023}
    results = {"doc-1-chunk-0": _payload(facts=[
        {"topic": "water", "fact": "Gray water goes in the 4x4x4 tank.",
         "category": "where", "quote": "gray water goes in the 4x4x4 tank"},
    ])}

    stats = store(conn, results, chunk_texts, doc_years)

    assert stats["facts_inserted"] == 1
    assert stats["bad_quote_dropped"] == 0
    fact = conn.execute("SELECT topic, category, year FROM camp_fact").fetchone()
    assert fact == ("water", "where", 2023)
    quote = conn.execute(
        "SELECT quote FROM evidence WHERE claim_table='camp_fact'").fetchone()[0]
    assert quote == "Gray water goes in the 4x4x4 tank"  # original text, original case
    verify_evidence_complete(conn)


def test_fact_dropped_when_quote_not_in_chunk(conn):
    chunk_texts = {"doc-1-chunk-0": "Gray water goes in the 4x4x4 tank. We empty it weekly."}
    doc_years = {1: 2023}
    results = {"doc-1-chunk-0": _payload(facts=[
        {"topic": "water", "fact": "We empty it monthly.",
         "category": "when", "quote": "we empty it monthly"},  # chunk says weekly
    ])}

    stats = store(conn, results, chunk_texts, doc_years)

    assert stats["facts_inserted"] == 0
    assert stats["bad_quote_dropped"] == 1
    assert conn.execute("SELECT COUNT(*) FROM camp_fact").fetchone()[0] == 0


def test_store_is_idempotent_per_document_on_rerun(conn):
    chunk_texts = {"doc-1-chunk-0": "Gray water goes in the 4x4x4 tank. We empty it weekly."}
    doc_years = {1: 2023}
    results = {"doc-1-chunk-0": _payload(facts=[
        {"topic": "water", "fact": "Gray water goes in the 4x4x4 tank.",
         "category": "where", "quote": "gray water goes in the 4x4x4 tank"},
    ])}

    store(conn, results, chunk_texts, doc_years)
    stats2 = store(conn, results, chunk_texts, doc_years)

    assert stats2["facts_inserted"] == 1
    assert conn.execute("SELECT COUNT(*) FROM camp_fact").fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM evidence WHERE claim_table='camp_fact'").fetchone()[0] == 1
    verify_evidence_complete(conn)


def test_multi_chunk_document_does_not_wipe_earlier_chunks_facts(conn):
    """A document split into two chunks: chunk 1's facts must survive after
    chunk 0's facts have already been stored — this is the wipe-per-document,
    not per-chunk, correction."""
    chunk_texts = {
        "doc-1-chunk-0": "Gray water goes in the 4x4x4 tank.",
        "doc-1-chunk-1": "We empty it weekly.",
    }
    doc_years = {1: 2023}
    results = {
        "doc-1-chunk-0": _payload(facts=[
            {"topic": "water", "fact": "Gray water goes in the 4x4x4 tank.",
             "category": "where", "quote": "gray water goes in the 4x4x4 tank"},
        ]),
        "doc-1-chunk-1": _payload(facts=[
            {"topic": "water", "fact": "We empty the tank weekly.",
             "category": "when", "quote": "we empty it weekly"},
        ]),
    }

    stats = store(conn, results, chunk_texts, doc_years)

    assert stats["facts_inserted"] == 2
    assert conn.execute("SELECT COUNT(*) FROM camp_fact").fetchone()[0] == 2


def test_rerun_of_one_document_does_not_touch_another_documents_facts(conn):
    chunk_texts_both = {
        "doc-1-chunk-0": "Gray water goes in the 4x4x4 tank.",
        "doc-2-chunk-0": "No ladders are stored in Doris this year.",
    }
    doc_years_both = {1: 2023, 2: 2019}
    results_both = {
        "doc-1-chunk-0": _payload(facts=[
            {"topic": "water", "fact": "Gray water goes in the 4x4x4 tank.",
             "category": "where", "quote": "gray water goes in the 4x4x4 tank"},
        ]),
        "doc-2-chunk-0": _payload(facts=[
            {"topic": "structures", "fact": "No ladders are stored in Doris.",
             "category": "where", "quote": "no ladders are stored in doris"},
        ]),
    }
    store(conn, results_both, chunk_texts_both, doc_years_both)
    assert conn.execute("SELECT COUNT(*) FROM camp_fact").fetchone()[0] == 2

    # Re-run doc 1 only — doc 2's fact must remain untouched.
    chunk_texts_1 = {"doc-1-chunk-0": "Gray water goes in the 4x4x4 tank."}
    doc_years_1 = {1: 2023}
    results_1 = {
        "doc-1-chunk-0": _payload(facts=[
            {"topic": "water", "fact": "Gray water goes in the 4x4x4 tank, updated.",
             "category": "where", "quote": "gray water goes in the 4x4x4 tank"},
        ]),
    }
    store(conn, results_1, chunk_texts_1, doc_years_1)

    facts = conn.execute("SELECT topic, fact FROM camp_fact ORDER BY id").fetchall()
    assert len(facts) == 2
    topics = {t for t, _ in facts}
    assert topics == {"water", "structures"}
    verify_evidence_complete(conn)


def test_rerun_takes_a_deleted_facts_ask_words_with_it(conn):
    """The wipe deletes camp_fact rows; their ask_word rows must go too.
    They used to survive as orphans — pointing at fact ids that no longer
    exist — and nothing could ever clean them up: the vocab stage only wipes
    words for facts it re-sends, a deleted fact is never sent, and ask_word
    is deliberately not a claim table so no verifier sees it."""
    chunk_texts = {
        "doc-1-chunk-0": "Gray water goes in the 4x4x4 tank.",
        "doc-2-chunk-0": "No ladders are stored in Doris this year.",
    }
    doc_years = {1: 2023, 2: 2019}
    results = {
        "doc-1-chunk-0": _payload(facts=[
            {"topic": "water", "fact": "Gray water goes in the 4x4x4 tank.",
             "category": "where", "quote": "gray water goes in the 4x4x4 tank"},
        ]),
        "doc-2-chunk-0": _payload(facts=[
            {"topic": "structures", "fact": "No ladders are stored in Doris.",
             "category": "where", "quote": "no ladders are stored in doris"},
        ]),
    }
    store(conn, results, chunk_texts, doc_years)

    # The vocab stage runs after and hangs words on both facts.
    ids = {topic: fid for fid, topic in
           conn.execute("SELECT id, topic FROM camp_fact")}
    conn.executemany(
        "INSERT INTO ask_word (claim_table, claim_id, word) "
        "VALUES ('camp_fact', ?, ?)",
        [(ids["water"], "greywater"), (ids["structures"], "stepladder")])

    # Re-run doc 1 only: its fact is replaced under a new id.
    store(conn,
          {"doc-1-chunk-0": results["doc-1-chunk-0"]},
          {"doc-1-chunk-0": chunk_texts["doc-1-chunk-0"]},
          {1: 2023})

    # No ask_word row may point at a fact that no longer exists…
    orphans = conn.execute(
        "SELECT COUNT(*) FROM ask_word WHERE claim_table = 'camp_fact' "
        "AND claim_id NOT IN (SELECT id FROM camp_fact)").fetchone()[0]
    assert orphans == 0
    # …and the other document's words are not collateral damage.
    assert conn.execute(
        "SELECT word FROM ask_word WHERE claim_id = ?",
        (ids["structures"],)).fetchall() == [("stepladder",)]


def test_none_payload_is_skipped_not_crashed_on(conn):
    results = {"doc-1-chunk-0": None}
    stats = store(conn, results, {"doc-1-chunk-0": "some text"}, {1: 2023})
    assert stats["facts_inserted"] == 0


def test_missing_topic_or_fact_is_skipped(conn):
    chunk_texts = {"doc-1-chunk-0": "Gray water goes in the 4x4x4 tank."}
    doc_years = {1: 2023}
    results = {"doc-1-chunk-0": _payload(facts=[
        {"topic": "", "fact": "Gray water goes in the 4x4x4 tank.",
         "category": "where", "quote": "gray water goes in the 4x4x4 tank"},
        {"topic": "water", "fact": "",
         "category": "where", "quote": "gray water goes in the 4x4x4 tank"},
    ])}
    stats = store(conn, results, chunk_texts, doc_years)
    assert stats["facts_inserted"] == 0
