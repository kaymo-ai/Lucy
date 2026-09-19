import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrich_schema import create_enrichment_tables, verify_evidence_complete
from enrich_lore import (
    build_jobs,
    build_windows,
    collect_stories,
    consolidate,
    known_names,
    store,
)


# --------------------------------------------------------------------------
# Fixture corpus: a handful of sent messages across several months, thin
# winter months included so window merging has something to merge.
# --------------------------------------------------------------------------

@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE person (
            id INTEGER PRIMARY KEY, name TEXT, email TEXT, phone TEXT,
            message_count INTEGER, first_seen TEXT, last_seen TEXT);
        CREATE TABLE person_content (
            id INTEGER PRIMARY KEY, person_id INTEGER, content TEXT, source TEXT,
            content_type TEXT, timestamp TEXT, media_ref TEXT);
        CREATE TABLE camp_knowledge (
            id INTEGER PRIMARY KEY, title TEXT, content TEXT,
            source_file TEXT, category TEXT, year INTEGER);
        INSERT INTO person (id, name) VALUES
            (1, 'Piotr Moravec'), (2, 'Cece Garland'), (3, 'Marcus Foster');
        INSERT INTO person_content (id, person_id, content, source, content_type, timestamp) VALUES
            (10, 1, 'the padlock on Doris is stuck again',        'build chat', 'sent', '2022-07-03 10:00:00'),
            (11, 2, 'hit it hard with the hammer, works every time', 'build chat', 'sent', '2022-07-03 10:05:00'),
            (12, 3, 'update: the hammer is INSIDE Doris',          'build chat', 'sent', '2022-08-14 09:00:00'),
            (13, 1, 'we locked the tool we need inside the box we need it for', 'build chat', 'sent', '2022-08-14 09:10:00'),
            (14, 2, 'quiet month check-in',                        'main chat',  'sent', '2022-11-20 12:00:00'),
            (15, 3, 'happy new year snails',                       'main chat',  'sent', '2023-01-01 00:01:00'),
            (16, 1, 'remember when the hammer got locked inside Doris last summer', 'main chat', 'sent', '2023-02-10 15:00:00'),
            (17, 2, 'the Doris hammer saga, never forget',         'main chat',  'sent', '2023-02-10 15:04:00'),
            (18, 3, 'received message that should be excluded',    'main chat',  'received', '2022-07-03 11:00:00');
    """)
    c.execute("PRAGMA foreign_keys = ON")
    create_enrichment_tables(c)
    return c


NAMES = ["Piotr Moravec", "Cece Garland", "Marcus Foster"]


def _story(title="The hammer inside Doris",
           story="Doris's padlock stuck, and the fix was a hammer. "
                 "Then someone locked the hammer inside Doris itself.",
           year=2022, people=None, ids=None):
    return {"title": title, "story": story, "year": year,
            "people": people if people is not None else ["Piotr"],
            "supporting_message_ids": ids if ids is not None else [10, 11]}


# --------------------------------------------------------------------------
# Windows: month grouping, sparse-month merging, sent-only
# --------------------------------------------------------------------------

def test_windows_merge_sparse_months_and_fold_the_tail(conn):
    # Threshold 3: July (2 msgs) merges into August (2 msgs) to clear it;
    # the remaining Nov/Jan/Feb trickle (4 msgs) forms the second window.
    windows = build_windows(conn, min_messages=3)
    assert [label for label, _ in windows] == ["2022-07..2022-08", "2022-11..2023-02"]
    assert [len(rows) for _, rows in windows] == [4, 4]


def test_windows_exclude_non_sent_content(conn):
    windows = build_windows(conn, min_messages=1)
    all_ids = {mid for _, rows in windows for mid, *_ in rows}
    assert 18 not in all_ids       # content_type 'received'
    assert all_ids == {10, 11, 12, 13, 14, 15, 16, 17}


def test_thin_tail_folds_backward_not_into_its_own_window(conn):
    # Threshold 5 leaves a 4-message tail after the first window closes; it
    # must fold into that window rather than ship as an undersized one.
    windows = build_windows(conn, min_messages=5)
    assert len(windows) == 1
    assert len(windows[0][1]) == 8


def test_build_jobs_tracks_shown_ids_per_window(conn):
    windows = build_windows(conn, min_messages=3)
    jobs, seen_ids = build_jobs(windows)
    assert [jid for jid, _ in jobs] == ["lore-0", "lore-1"]
    assert seen_ids["lore-0"] == {10, 11, 12, 13}
    assert seen_ids["lore-1"] == {14, 15, 16, 17}
    assert "[10]" in jobs[0][1] and "Piotr Moravec" in jobs[0][1]


# --------------------------------------------------------------------------
# collect_stories: support threshold, shown-id check, people filtering
# --------------------------------------------------------------------------

def test_story_with_enough_support_is_collected():
    results = {"lore-0": {"stories": [_story(ids=[10, 11])]}}
    stories, stats = collect_stories(results, {"lore-0": {10, 11, 12, 13}}, NAMES)
    assert len(stories) == 1
    assert stories[0]["ids"] == {10, 11}
    assert stats["refused_support"] == 0


def test_story_with_one_supporting_message_is_refused():
    results = {"lore-0": {"stories": [_story(ids=[10])]}}
    stories, stats = collect_stories(results, {"lore-0": {10, 11}}, NAMES)
    assert stories == []
    assert stats["refused_support"] == 1


def test_citation_outside_the_shown_window_does_not_count():
    # Two ids cited, but only one was actually shown to this job's model
    # call — the other cannot be evidence, so the story falls below the bar.
    results = {"lore-0": {"stories": [_story(ids=[10, 16])]}}
    stories, stats = collect_stories(results, {"lore-0": {10, 11}}, NAMES)
    assert stories == []
    assert stats["refused_support"] == 1


def test_people_not_named_by_the_corpus_are_filtered():
    results = {"lore-0": {"stories": [
        _story(people=["Piotr", "Cece Garland", "Lord Byron"])]}}
    stories, stats = collect_stories(results, {"lore-0": {10, 11}}, NAMES)
    assert stories[0]["people"] == ["Piotr", "Cece Garland"]
    assert stats["names_filtered"] == 1


def test_partial_word_name_match_does_not_count():
    # "Ana" must not ride in on "Diana" — whole words only.
    results = {"lore-0": {"stories": [_story(people=["Ana"])]}}
    stories, _ = collect_stories(results, {"lore-0": {10, 11}}, ["Diana Prince"])
    assert stories[0]["people"] == []


def test_story_containing_a_handle_is_dropped():
    results = {"lore-0": {"stories": [
        _story(story="Then @piotr locked the hammer inside.")]}}
    stories, stats = collect_stories(results, {"lore-0": {10, 11}}, NAMES)
    assert stories == []
    assert stats["dropped_handle"] == 1


def test_implausible_year_becomes_null_not_wrong():
    results = {"lore-0": {"stories": [_story(year=1972)]}}
    stories, _ = collect_stories(results, {"lore-0": {10, 11}}, NAMES)
    assert stories[0]["year"] is None


def test_none_payload_is_skipped_not_crashed_on():
    stories, _ = collect_stories({"lore-0": None}, {"lore-0": {10, 11}}, NAMES)
    assert stories == []


# --------------------------------------------------------------------------
# Consolidation: the same incident found in two windows becomes one story
# --------------------------------------------------------------------------

def test_same_incident_across_windows_is_merged_with_pooled_evidence():
    first = {"title": "The hammer inside Doris",
             "story": "Doris's stuck padlock needed the hammer, and someone "
                      "locked the hammer inside Doris itself.",
             "year": 2022, "people": ["Piotr"], "ids": {10, 11, 12}}
    retold = {"title": "The Doris hammer saga",
              "story": "The padlock on Doris stuck and the hammer that opens "
                       "it was locked inside Doris.",
              "year": 2022, "people": ["Cece Garland"], "ids": {16, 17}}
    merged, count = consolidate([first, retold])
    assert count == 1
    assert len(merged) == 1
    # More supporting messages wins the retelling; evidence and people pool.
    assert merged[0]["title"] == "The hammer inside Doris"
    assert merged[0]["ids"] == {10, 11, 12, 16, 17}
    assert set(merged[0]["people"]) == {"Piotr", "Cece Garland"}


def test_different_years_are_never_merged_even_when_similar():
    a = {"title": "Shade structure collapse",
         "story": "The shade structure collapsed in the windstorm overnight.",
         "year": 2019, "people": [], "ids": {10, 11}}
    b = {"title": "Shade structure collapse",
         "story": "The shade structure collapsed in the windstorm overnight.",
         "year": 2023, "people": [], "ids": {16, 17}}
    merged, count = consolidate([a, b])
    assert count == 0
    assert len(merged) == 2


def test_unrelated_stories_are_left_alone():
    a = {"title": "The hammer inside Doris",
         "story": "The hammer needed for the padlock was locked inside Doris.",
         "year": 2022, "people": [], "ids": {10, 11}}
    b = {"title": "Sunrise set on Lucy",
         "story": "Lucy rolled out before dawn and played until the sun came up.",
         "year": 2022, "people": [], "ids": {16, 17}}
    merged, count = consolidate([a, b])
    assert count == 0
    assert len(merged) == 2


# --------------------------------------------------------------------------
# store: rows, evidence, idempotent re-run
# --------------------------------------------------------------------------

def _collected(conn):
    results = {"lore-0": {"stories": [_story(ids=[10, 11])]}}
    stories, _ = collect_stories(results, {"lore-0": {10, 11}}, known_names(conn))
    return stories


def test_story_is_stored_with_evidence_citing_the_corpus(conn):
    stats = store(conn, _collected(conn))
    assert stats["stories_inserted"] == 1

    title, story, year, people = conn.execute(
        "SELECT title, story, year, people FROM lore").fetchone()
    assert title == "The hammer inside Doris"
    assert year == 2022
    assert people == "Piotr"

    evidence = conn.execute(
        "SELECT source_table, source_id, quote FROM evidence "
        "WHERE claim_table = 'lore' ORDER BY source_id").fetchall()
    assert [(t, i) for t, i, _ in evidence] == \
        [("person_content", 10), ("person_content", 11)]
    # The quote is the database's own text, never the model's paraphrase.
    assert evidence[0][2] == "the padlock on Doris is stuck again"
    verify_evidence_complete(conn)


def test_rerun_is_idempotent_wipe_and_rewrite(conn):
    store(conn, _collected(conn))
    stats2 = store(conn, _collected(conn))

    assert stats2["stories_inserted"] == 1
    assert conn.execute("SELECT COUNT(*) FROM lore").fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM evidence WHERE claim_table='lore'"
    ).fetchone()[0] == 2
    verify_evidence_complete(conn)


def test_rerun_does_not_touch_other_stages_evidence(conn):
    # A camp_fact claim from another stage must survive a lore wipe intact.
    conn.execute("INSERT INTO camp_fact (id, topic, fact) VALUES (1, 'water', 'x')")
    conn.execute(
        "INSERT INTO evidence (claim_table, claim_id, source_table, source_id, "
        "quote) VALUES ('camp_fact', 1, 'person_content', 10, 'q')")
    store(conn, _collected(conn))
    assert conn.execute(
        "SELECT COUNT(*) FROM evidence WHERE claim_table='camp_fact'"
    ).fetchone()[0] == 1
    verify_evidence_complete(conn)


def test_stored_order_is_deterministic(conn):
    stories = [
        {"title": "B story", "story": "s", "year": 2023, "people": [], "ids": {10, 11}},
        {"title": "A story", "story": "s", "year": 2022, "people": [], "ids": {12, 13}},
    ]
    store(conn, stories)
    titles = [r[0] for r in conn.execute("SELECT title FROM lore ORDER BY id")]
    assert titles == ["A story", "B story"]


# --------------------------------------------------------------------------
# End to end against fabricated model output (the model is never called)
# --------------------------------------------------------------------------

def test_windows_to_table_round_trip_with_cross_window_dedupe(conn):
    windows = build_windows(conn, min_messages=3)
    jobs, seen_ids = build_jobs(windows)
    assert len(jobs) == 2

    results = {
        "lore-0": {"stories": [
            {"title": "The hammer inside Doris",
             "story": "Doris's stuck padlock needed the hammer, and someone "
                      "locked the hammer inside Doris itself.",
             "year": 2022, "people": ["Piotr"],
             "supporting_message_ids": [10, 11, 12, 13]}]},
        "lore-1": {"stories": [
            {"title": "The Doris hammer saga",
             "story": "The padlock on Doris stuck and the hammer that opens "
                      "it was locked inside Doris.",
             "year": 2022, "people": ["Cece Garland"],
             "supporting_message_ids": [16, 17]}]},
    }

    stories, _ = collect_stories(results, seen_ids, known_names(conn))
    stories, merged = consolidate(stories)
    stats = store(conn, stories)

    assert merged == 1
    assert stats["stories_inserted"] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM evidence WHERE claim_table='lore'"
    ).fetchone()[0] == 6
    verify_evidence_complete(conn)
