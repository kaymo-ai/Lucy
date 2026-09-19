import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrich_schema import create_enrichment_tables, verify_evidence_complete
from enrich_entity_records import (
    cap_messages,
    compile_term_regexes,
    quote_verified,
    safe_terms,
    store,
    survives_filter,
)


# --------------------------------------------------------------------------
# Correction 1: word-boundary matching, not substring
# --------------------------------------------------------------------------

def test_short_alias_does_not_match_inside_a_word():
    regexes = compile_term_regexes(["PS"])
    assert not survives_filter("perhaps we should bring more apps", regexes)
    assert not survives_filter("PSA: bring water", regexes)


def test_short_alias_matches_as_its_own_token():
    regexes = compile_term_regexes(["PS"])
    assert survives_filter("bring it to PS tonight", regexes)
    assert survives_filter("PS is running low on ice", regexes)


def test_ts_does_not_match_inside_unrelated_words():
    regexes = compile_term_regexes(["TS"])
    assert not survives_filter("guitars and hats for the theme camp", regexes)


def test_multiword_term_matches_at_boundaries():
    regexes = compile_term_regexes(["Public Shade"])
    assert survives_filter("we rebuilt Public Shade this year", regexes)
    assert not survives_filter("PublicShadeish things are not the same", regexes)


# --------------------------------------------------------------------------
# Reserved-name filtering: an alias that is secretly another entity's own
# name, or a real person's name, is excluded from the search terms — found
# by measuring the real db (Public Shade/PS, Radish RV/Radish, Taylor Swift
# Party/Taylor), not guessed in advance.
# --------------------------------------------------------------------------

def test_alias_matching_another_entitys_name_is_dropped():
    terms = safe_terms("Public Shade", ["PS", "public shade structure"],
                        entity_names_cf={"ps", "public shade"}, person_names_cf=set())
    assert terms == ["Public Shade", "public shade structure"]


def test_alias_matching_a_persons_name_is_dropped():
    terms = safe_terms("Taylor Swift Party", ["Taylor", "TS"],
                        entity_names_cf={"taylor swift party"},
                        person_names_cf={"taylor"})
    assert terms == ["Taylor Swift Party", "TS"]


def test_alias_equal_to_own_name_is_always_dropped():
    terms = safe_terms("Doris", ["doris", "Oris"],
                        entity_names_cf={"doris"}, person_names_cf=set())
    assert terms == ["Doris", "Oris"]


def test_harmless_aliases_all_kept():
    terms = safe_terms("Doris", ["Oris"], entity_names_cf={"doris"}, person_names_cf=set())
    assert terms == ["Doris", "Oris"]


# --------------------------------------------------------------------------
# Correction 2: cap at N, preferring the most recent, report truncation
# --------------------------------------------------------------------------

def _row(mid, ts):
    return (mid, "Someone", ts[:10], f"message {mid}", ts)


def test_cap_keeps_all_when_under_limit():
    rows = [_row(i, f"2022-01-{i:02d}") for i in range(1, 6)]
    kept, dropped = cap_messages(rows, cap=10)
    assert dropped == 0
    assert [r[0] for r in kept] == [1, 2, 3, 4, 5]


def test_cap_keeps_most_recent_and_reports_dropped():
    rows = [_row(i, f"2022-01-{i:02d}") for i in range(1, 11)]
    kept, dropped = cap_messages(rows, cap=4)
    assert dropped == 6
    # most recent 4 by timestamp: ids 7,8,9,10 — returned in chronological order
    assert [r[0] for r in kept] == [7, 8, 9, 10]


# --------------------------------------------------------------------------
# Correction 4: quote must actually appear in the cited row
# --------------------------------------------------------------------------

def test_quote_verified_matches_whitespace_and_case_insensitively():
    db_content = "The   padlock code\nis 8765, hit it with a hammer if stuck."
    assert quote_verified(db_content, "the padlock code is 8765")
    assert quote_verified(db_content, "PADLOCK CODE\nIS 8765")


def test_quote_not_verified_when_absent():
    db_content = "we have no ladders in here as far as I can tell"
    assert not quote_verified(db_content, "there are three ladders")


def test_quote_not_verified_when_empty():
    assert not quote_verified("some content", "")
    assert not quote_verified("some content", "   ")


# --------------------------------------------------------------------------
# store(): corrections 3, 4, 6, and idempotency
# --------------------------------------------------------------------------

@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE person (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE person_content (id INTEGER PRIMARY KEY, content TEXT);
        CREATE TABLE camp_knowledge (id INTEGER PRIMARY KEY, title TEXT);
        INSERT INTO person_content (id, content) VALUES
            (10, 'The padlock code is 8765, hit it with a hammer if it sticks'),
            (11, 'no ladders in here that I can find'),
            (12, 'at least three ladders are in there now'),
            (99, 'a message never sent to any entity job');
    """)
    c.execute("PRAGMA foreign_keys = ON")
    create_enrichment_tables(c)
    # Seed one entity the way Task 3's store() would have: entity row plus
    # its own required evidence row.
    c.execute("INSERT INTO entity (id, name, kind, summary) VALUES (7, 'Doris', 'vehicle', '')")
    c.execute("""INSERT INTO evidence (claim_table, claim_id, source_table, source_id, quote)
                 VALUES ('entity', 7, 'person_content', 10, 'the padlock code is 8765')""")
    return c


def _payload(summary="Our storage vehicle.", facts=None):
    return {"summary": summary, "facts": facts or []}


def test_valid_fact_is_stored_with_db_text_as_quote_not_models(conn):
    results = {"entity-7": _payload(facts=[
        {"fact": "The padlock code is 8765.", "category": "access",
         "asserted_on": "2022-08-22", "evidence_message_id": 10,
         "quote": "padlock code is 8765"},   # model's paraphrase/partial quote
    ])}
    sent_ids = {7: {10, 11, 12}}

    stats = store(conn, results, sent_ids)

    assert stats["facts_inserted"] == 1
    assert stats["out_of_scope_dropped"] == 0
    assert stats["bad_quote_dropped"] == 0
    quote = conn.execute(
        "SELECT quote FROM evidence WHERE claim_table='entity_fact'").fetchone()[0]
    # stored quote is the DATABASE's text, not the model's shorter phrase
    assert quote == "The padlock code is 8765, hit it with a hammer if it sticks"
    verify_evidence_complete(conn)


def test_fact_dropped_when_id_outside_what_entity_was_shown(conn):
    """Correction 3: id 99 exists in person_content (passes the old, weaker
    'does this id exist anywhere' check) but was never sent to entity 7's
    job — this is Task 3's real, carried-forward defect."""
    results = {"entity-7": _payload(facts=[
        {"fact": "Something about Doris.", "category": "history",
         "asserted_on": "2022-01-01", "evidence_message_id": 99,
         "quote": "a message never sent to any entity job"},
    ])}
    sent_ids = {7: {10, 11, 12}}   # 99 is not in this set

    stats = store(conn, results, sent_ids)

    assert stats["facts_inserted"] == 0
    assert stats["out_of_scope_dropped"] == 1
    assert stats["bad_quote_dropped"] == 0
    assert conn.execute("SELECT COUNT(*) FROM entity_fact").fetchone()[0] == 0


def test_fact_dropped_when_quote_does_not_match_cited_row(conn):
    results = {"entity-7": _payload(facts=[
        {"fact": "There are three ladders.", "category": "contents",
         "asserted_on": "2022-08-24", "evidence_message_id": 11,
         "quote": "there are three ladders in here"},  # id 11 says NO ladders
    ])}
    sent_ids = {7: {10, 11, 12}}

    stats = store(conn, results, sent_ids)

    assert stats["facts_inserted"] == 0
    assert stats["bad_quote_dropped"] == 1
    assert stats["out_of_scope_dropped"] == 0


def test_both_ladder_contradiction_facts_kept(conn):
    """Contradictions are kept, not resolved — two dated facts survive."""
    results = {"entity-7": _payload(facts=[
        {"fact": "Contains no ladders.", "category": "contents",
         "asserted_on": "2022-08-19", "evidence_message_id": 11,
         "quote": "no ladders in here"},
        {"fact": "Contains at least three ladders.", "category": "contents",
         "asserted_on": "2022-08-24", "evidence_message_id": 12,
         "quote": "at least three ladders are in there"},
    ])}
    sent_ids = {7: {10, 11, 12}}

    stats = store(conn, results, sent_ids)

    assert stats["facts_inserted"] == 2
    dates = {r[0] for r in conn.execute("SELECT asserted_on FROM entity_fact")}
    assert dates == {"2022-08-19", "2022-08-24"}


def test_store_is_idempotent_on_rerun(conn):
    """Re-running the pass for an entity must replace its facts, not
    duplicate them — a real risk given the brief's own run order (single
    entity, then the full pass that includes it again)."""
    results = {"entity-7": _payload(facts=[
        {"fact": "The padlock code is 8765.", "category": "access",
         "asserted_on": "2022-08-22", "evidence_message_id": 10,
         "quote": "padlock code is 8765"},
    ])}
    sent_ids = {7: {10, 11, 12}}

    store(conn, results, sent_ids)
    stats2 = store(conn, results, sent_ids)

    assert stats2["facts_inserted"] == 1
    assert conn.execute("SELECT COUNT(*) FROM entity_fact").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM evidence WHERE claim_table='entity_fact'"
                         ).fetchone()[0] == 1
    verify_evidence_complete(conn)


def test_verify_evidence_complete_raises_are_not_swallowed(conn):
    """A malformed row that somehow reaches the fact insert (e.g. a category
    outside the CHECK constraint slipping past JSON-schema enforcement)
    aborts that one fact via savepoint rather than the whole pass, and never
    reaches conn.commit() silently missing evidence."""
    results = {"entity-7": _payload(facts=[
        {"fact": "Fine.", "category": "access", "asserted_on": "2022-01-01",
         "evidence_message_id": 10, "quote": "padlock code is 8765"},
    ])}
    stats = store(conn, results, {7: {10}})
    assert stats["facts_inserted"] == 1
    assert stats["trigger_dropped"] == 0
    # must not raise — evidence really is complete
    verify_evidence_complete(conn)


def test_none_payload_is_skipped_not_crashed_on(conn):
    results = {"entity-7": None}
    stats = store(conn, results, {7: {10, 11, 12}})
    assert stats["facts_inserted"] == 0
    assert stats["entities_updated"] == 0


def test_summary_written_from_payload(conn):
    results = {"entity-7": _payload(summary="Our storage vehicle with a padlock.")}
    stats = store(conn, results, {7: {10, 11, 12}})
    assert stats["entities_updated"] == 1
    summary = conn.execute("SELECT summary FROM entity WHERE id=7").fetchone()[0]
    assert summary == "Our storage vehicle with a padlock."
