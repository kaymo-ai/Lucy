import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import enrich_pronouns
from enrich_pronouns import (
    GENDERED,
    MAX_ATTEMPTS,
    cap_words,
    partition_exempt,
    rejection,
    run_pass,
    select_gendered,
)
from enrich_schema import create_enrichment_tables


# --------------------------------------------------------------------------
# The regex — selection and verification are the same pattern, so its edge
# cases are the pass's edge cases
# --------------------------------------------------------------------------

def test_whole_word_pronouns_match_case_insensitively():
    for text in ("He runs the kitchen", "the truck is hers",
                 "built it HIMSELF", "we asked her twice",
                 "his spreadsheet", "She keeps the lists", "give him a shift",
                 "did it herself"):
        assert GENDERED.search(text), text


def test_her_inside_there_does_not_match():
    assert not GENDERED.search("there is more shade there than anywhere")


def test_his_inside_this_and_history_does_not_match():
    assert not GENDERED.search("this camp values its history")


def test_she_inside_shed_and_he_inside_the_do_not_match():
    assert not GENDERED.search("the shed by the kitchen")


def test_contraction_still_matches():
    # \b sits between the pronoun and the apostrophe, so "He's" is caught.
    assert GENDERED.search("He's been on placement crew since 2019")


# --------------------------------------------------------------------------
# Fixture — person, person_content (for the self-stated exemption), and
# person_profile rows in the shapes the pass must tell apart
# --------------------------------------------------------------------------

SUMMARIES = {
    1: "Piotr manages our logistics. He keeps the Doris paperwork moving.",
    2: "Runs the kitchen and the water schedule with a steady hand.",
    3: "Everyone agrees there is more history there than the wiki records.",
    4: "She organises the art car build every year.",
}


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE person (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE person_content (
            id INTEGER PRIMARY KEY, person_id INTEGER, content TEXT);
        INSERT INTO person (id, name) VALUES
            (1, 'Piotr'), (2, 'Oz'), (3, 'Nick Hadley'), (4, 'Kat Hensley');
        -- Person 4 states their own pronouns; person 1 never does. Another
        -- member talking ABOUT someone must not count as self-stating.
        INSERT INTO person_content (id, person_id, content) VALUES
            (10, 1, 'padlock code is on the wiki'),
            (11, 4, 'for the record my pronouns are she/her'),
            (12, 1, 'she said the truck is ready');
    """)
    create_enrichment_tables(c)
    for pid, summary in SUMMARIES.items():
        c.execute("INSERT INTO person_profile (person_id, summary) VALUES (?,?)",
                  (pid, summary))
    return c


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------

def test_select_finds_only_gendered_rows(conn):
    assert [pid for pid, _ in select_gendered(conn)] == [1, 4]


def test_select_respects_limit(conn):
    assert [pid for pid, _ in select_gendered(conn, limit=1)] == [1]


def test_self_stated_person_is_exempt_not_work(conn):
    todo, exempt = partition_exempt(conn, select_gendered(conn))
    assert [pid for pid, _ in todo] == [1]
    assert [pid for pid, _ in exempt] == [4]


def test_someone_elses_she_does_not_exempt(conn):
    # Row 12 says "she" about person 1 — person 1 stated nothing themselves.
    todo, _ = partition_exempt(conn, [(1, SUMMARIES[1])])
    assert [pid for pid, _ in todo] == [1]


# --------------------------------------------------------------------------
# rejection() — the regex again, plus the two drift guards
# --------------------------------------------------------------------------

GOOD_REWRITE = ("Piotr manages our logistics. "
                "They keep the Doris paperwork moving.")


def test_clean_rewrite_accepted():
    assert rejection(SUMMARIES[1], GOOD_REWRITE) is None


def test_rewrite_with_surviving_pronoun_rejected():
    assert rejection(SUMMARIES[1], SUMMARIES[1]) == "gendered pronoun survived"


def test_empty_rewrite_rejected():
    assert rejection(SUMMARIES[1], "  ") == "empty rewrite"


def test_rewrite_that_balloons_rejected():
    padded = GOOD_REWRITE + " They also once repainted the container twice."
    assert "length drifted" in rejection(SUMMARIES[1], padded)


def test_rewrite_that_loses_a_name_rejected():
    # "Doris" gone: pronouns fixed, content quietly changed. Not acceptable —
    # the evidence rows still cite what the original summary said.
    dropped_name = "Piotr manages our logistics. They keep the paperwork moving along."
    assert "Doris" in rejection(SUMMARIES[1], dropped_name)


def test_pronoun_capitals_do_not_count_as_names():
    # Sentence-initial "She" vanishing and "They" appearing is the entire
    # point of the rewrite, so neither may trip the capitalised-word guard.
    assert cap_words("She organises the art car build.") == set()
    assert rejection(SUMMARIES[4],
                     "They organise the art car build every year.") is None


# --------------------------------------------------------------------------
# The full pass, model mocked — compliance is enforced, not assumed
# --------------------------------------------------------------------------

def fake_run_all(responses_by_attempt):
    """A run_all whose answers vary by attempt: responses_by_attempt[n] maps
    job_id -> summary (or None for a failed job) on the n-th call."""
    calls = []

    def fake(jobs, system_text, schema):
        attempt = len(calls)
        calls.append([jid for jid, _ in jobs])
        script = responses_by_attempt[min(attempt, len(responses_by_attempt) - 1)]
        return {jid: ({"summary": script[jid]} if script.get(jid) else None)
                for jid, _ in jobs}

    fake.calls = calls
    return fake


def test_compliant_rewrite_is_written(conn, monkeypatch):
    monkeypatch.setattr(enrich_pronouns, "run_all",
                        fake_run_all([{"1": GOOD_REWRITE}]))
    stats = run_pass(conn)
    assert stats["rewritten"] == 1
    assert stats["dropped"] == []
    assert stats["exempt"] == [4]
    stored = conn.execute(
        "SELECT summary FROM person_profile WHERE person_id = 1").fetchone()[0]
    assert stored == GOOD_REWRITE
    # The exempt and clean rows were not touched.
    for pid in (2, 3, 4):
        assert conn.execute("SELECT summary FROM person_profile WHERE person_id = ?",
                            (pid,)).fetchone()[0] == SUMMARIES[pid]


def test_noncompliant_rewrite_is_retried_then_accepted(conn, monkeypatch):
    fake = fake_run_all([{"1": SUMMARIES[1]},      # attempt 1: still gendered
                         {"1": GOOD_REWRITE}])     # attempt 2: clean
    monkeypatch.setattr(enrich_pronouns, "run_all", fake)
    stats = run_pass(conn)
    assert len(fake.calls) == 2
    assert stats["rewritten"] == 1
    assert stats["dropped"] == []


def test_never_compliant_summary_is_dropped_to_empty(conn, monkeypatch):
    fake = fake_run_all([{"1": SUMMARIES[1]}])     # gendered on every attempt
    monkeypatch.setattr(enrich_pronouns, "run_all", fake)
    stats = run_pass(conn)
    assert len(fake.calls) == MAX_ATTEMPTS
    assert stats["rewritten"] == 0
    assert stats["dropped"] == [1]
    stored = conn.execute(
        "SELECT summary FROM person_profile WHERE person_id = 1").fetchone()[0]
    assert stored == ""


def test_failed_job_is_retried_like_a_violation(conn, monkeypatch):
    fake = fake_run_all([{"1": None},              # attempt 1: job failed
                         {"1": GOOD_REWRITE}])
    monkeypatch.setattr(enrich_pronouns, "run_all", fake)
    stats = run_pass(conn)
    assert stats["rewritten"] == 1
    assert stats["dropped"] == []


def test_second_run_finds_nothing_and_spends_nothing(conn, monkeypatch):
    monkeypatch.setattr(enrich_pronouns, "run_all",
                        fake_run_all([{"1": GOOD_REWRITE}]))
    run_pass(conn)

    def explode(*_args, **_kwargs):
        raise AssertionError("second run must not call the model")

    monkeypatch.setattr(enrich_pronouns, "run_all", explode)
    stats = run_pass(conn)
    # Person 4 still matches the regex but is exempt, so the second run has
    # no work — exempt rows are expected, not pending.
    assert stats["rewritten"] == 0
    assert stats["dropped"] == []
    assert stats["exempt"] == [4]


def test_dropped_summary_is_settled_on_the_next_run(conn, monkeypatch):
    monkeypatch.setattr(enrich_pronouns, "run_all",
                        fake_run_all([{"1": SUMMARIES[1]}]))
    run_pass(conn)   # drops person 1 to empty

    monkeypatch.setattr(enrich_pronouns, "run_all",
                        lambda *a, **k: pytest.fail("must not be called"))
    stats = run_pass(conn)
    assert stats["rewritten"] == 0
    assert stats["dropped"] == []


def test_model_never_answering_leaves_summaries_untouched(conn, monkeypatch):
    # The 2026-08-11 outage: expired credentials made every attempt come
    # back empty. That must read as "unreached", never as "dropped" — an
    # outage emptied 93 good summaries once, and once is the limit.
    fake = fake_run_all([{"1": None}])             # nothing, every attempt
    monkeypatch.setattr(enrich_pronouns, "run_all", fake)
    stats = run_pass(conn)
    assert len(fake.calls) == MAX_ATTEMPTS
    assert stats["rewritten"] == 0
    assert stats["dropped"] == []
    assert stats["unreached"] == [1]
    stored = conn.execute(
        "SELECT summary FROM person_profile WHERE person_id = 1").fetchone()[0]
    assert stored == SUMMARIES[1]                  # exactly as it was


def test_one_real_refusal_still_counts_as_answered(conn, monkeypatch):
    # Unreached is strictly "the model never answered at all". A model that
    # produced one non-compliant rewrite met the row and would not fix it;
    # going silent on the retries does not upgrade that to an outage.
    fake = fake_run_all([{"1": SUMMARIES[1]},      # gendered answer
                         {"1": None}])             # then silence
    monkeypatch.setattr(enrich_pronouns, "run_all", fake)
    stats = run_pass(conn)
    assert stats["dropped"] == [1]                 # it DID answer and refuse
    assert stats["unreached"] == []
