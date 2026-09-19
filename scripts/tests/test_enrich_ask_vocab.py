import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import enrich_ask_vocab
from enrich_schema import create_enrichment_tables, verify_evidence_complete
from enrich_ask_vocab import (
    ALIAS_SOURCE,
    CAP_PER_CLAIM,
    build_entity_jobs,
    build_fact_jobs,
    contains_whole_word,
    filter_words,
    is_searchable,
    select_entities,
    store_entity_aliases,
    store_fact_words,
)


# --------------------------------------------------------------------------
# The four known gap cases drive the fixtures: "medkit" -> first aid,
# "water delivered" -> service vouchers, "Empire storage" -> Emigrant
# Storage. The facts and entity below are written so the corpus-side word
# is present and the camper-side word is not.
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_curated(monkeypatch, request):
    """The fixtures below deliberately embed the observed gap cases, so the
    curated lists would fire in nearly every test and every exact count
    would measure two mechanisms at once. Curation has its own tests —
    named "curated", which this fixture leaves alone; everywhere else it is
    silenced so the hygiene tests measure only the model path."""
    if "curated" in request.node.name:
        return
    monkeypatch.setattr(enrich_ask_vocab, "CURATED_ASK_WORDS", {})
    monkeypatch.setattr(enrich_ask_vocab, "CURATED_ALIASES", {})


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
            (1, 'Med kit supplies', 'First aid supplies live in the med kit.', 2023);
        INSERT INTO person_content (id, content) VALUES
            (1, 'Stuff is at Emigrant Storage in Fernley');
    """)
    c.execute("PRAGMA foreign_keys = ON")
    create_enrichment_tables(c)

    # Two camp facts, each evidence-backed as the invariant requires — this
    # stage adds no claims, but it runs verify_evidence_complete and must do
    # so against a database that already satisfies it.
    c.executescript("""
        INSERT INTO camp_fact (id, topic, fact, category, year) VALUES
            (1, 'safety', 'First aid supplies live in the med kit in Doris.', 'where', 2023),
            (2, 'water',  'We buy service vouchers for potable water fills.', 'how', 2023);
        INSERT INTO evidence (claim_table, claim_id, source_table, source_id, quote) VALUES
            ('camp_fact', 1, 'camp_knowledge', 1, 'First aid supplies live in the med kit.'),
            ('camp_fact', 2, 'camp_knowledge', 1, 'First aid supplies live in the med kit.');

        INSERT INTO entity (id, name, kind, summary) VALUES
            (1, 'Emigrant Storage', 'place', 'The storage units in Fernley.');
        INSERT INTO evidence (claim_table, claim_id, source_table, source_id, quote) VALUES
            ('entity', 1, 'person_content', 1, 'Stuff is at Emigrant Storage in Fernley');
        -- A hand-made alias: source NULL, per the schema convention.
        INSERT INTO entity_alias (entity_id, alias) VALUES (1, 'the storage unit');
    """)
    return c


def _fact_batches(conn):
    facts = conn.execute(
        "SELECT id, topic, fact FROM camp_fact ORDER BY id").fetchall()
    _, batches = build_fact_jobs(facts)
    return batches


def _entity_batches(conn):
    _, batches = build_entity_jobs(select_entities(conn))
    return batches


def _ask_words(conn, fact_id):
    return {w for (w,) in conn.execute(
        "SELECT word FROM ask_word WHERE claim_table='camp_fact' AND claim_id=?",
        (fact_id,))}


# --------------------------------------------------------------------------
# Word hygiene helpers
# --------------------------------------------------------------------------

def test_contains_whole_word_respects_boundaries():
    fact = "First aid supplies live in the med kit in Doris."
    assert contains_whole_word(fact, "first aid")     # the phrase is there
    assert contains_whole_word(fact, "kit")           # whole word, present
    assert not contains_whole_word(fact, "medkit")    # 'med kit' != 'medkit'
    assert not contains_whole_word(fact, "aids")      # 'aid' is not 'aids'


def test_is_searchable_mirrors_device_term_extraction():
    assert not is_searchable("the")        # stop word
    assert not is_searchable("rv")         # too short for Retrieval.terms
    assert not is_searchable("in the")     # nothing survives extraction
    assert is_searchable("tank")
    assert is_searchable("4x4x4 tank")     # one searchable token is enough


def test_filter_words_caps_and_dedupes():
    candidates = [f"word{i}" for i in range(10)] + ["word0"]
    kept = filter_words(candidates, ["some unrelated fact text"])
    assert len(kept) == CAP_PER_CLAIM
    assert kept == [f"word{i}" for i in range(CAP_PER_CLAIM)]


# --------------------------------------------------------------------------
# Stage A: ask words for camp facts
# --------------------------------------------------------------------------

def test_words_written_and_lowercased(conn):
    results = {"vocab-facts-0": {"entries": [
        {"fact_id": 1, "words": ["MedKit", "Band-Aids"]},
        {"fact_id": 2, "words": ["Water Delivered", "water truck"]},
    ]}}
    stats = store_fact_words(conn, results, _fact_batches(conn))

    assert stats["words_inserted"] == 4
    assert _ask_words(conn, 1) == {"medkit", "band-aids"}
    assert _ask_words(conn, 2) == {"water delivered", "water truck"}
    verify_evidence_complete(conn)   # nothing here created an unbacked claim


def test_word_already_in_fact_or_topic_is_skipped(conn):
    # "first aid" is in the fact, "safety" is the topic, "kit" is a whole
    # word of the fact; only "medkit" is genuinely missing vocabulary.
    results = {"vocab-facts-0": {"entries": [
        {"fact_id": 1, "words": ["first aid", "safety", "kit", "medkit"]},
    ]}}
    stats = store_fact_words(conn, results, _fact_batches(conn))
    assert stats["words_inserted"] == 1
    assert _ask_words(conn, 1) == {"medkit"}


def test_stop_words_and_short_words_are_refused(conn):
    results = {"vocab-facts-0": {"entries": [
        {"fact_id": 1, "words": ["how", "the", "rv", "in the", "medkit"]},
    ]}}
    store_fact_words(conn, results, _fact_batches(conn))
    assert _ask_words(conn, 1) == {"medkit"}


def test_digit_runs_are_refused(conn):
    results = {"vocab-facts-0": {"entries": [
        {"fact_id": 2, "words": ["1117249140", "visa 4111111111111111",
                                 "4x4x4 tank"]},
    ]}}
    store_fact_words(conn, results, _fact_batches(conn))
    assert _ask_words(conn, 2) == {"4x4x4 tank"}


def test_per_fact_cap_is_enforced(conn):
    results = {"vocab-facts-0": {"entries": [
        {"fact_id": 1, "words": [f"word{i}" for i in range(10)]},
    ]}}
    stats = store_fact_words(conn, results, _fact_batches(conn))
    assert stats["words_inserted"] == CAP_PER_CLAIM
    assert len(_ask_words(conn, 1)) == CAP_PER_CLAIM


def test_unknown_fact_id_is_dropped_not_stored(conn):
    results = {"vocab-facts-0": {"entries": [
        {"fact_id": 999, "words": ["phantom"]},
    ]}}
    stats = store_fact_words(conn, results, _fact_batches(conn))
    assert stats["words_inserted"] == 0
    assert stats["unknown_fact_ids"] == 1
    assert conn.execute("SELECT COUNT(*) FROM ask_word").fetchone()[0] == 0


def test_rerun_is_idempotent_and_replaces_stale_words(conn):
    batches = _fact_batches(conn)
    first = {"vocab-facts-0": {"entries": [
        {"fact_id": 1, "words": ["medkit", "boo boo kit"]},
    ]}}
    store_fact_words(conn, first, batches)
    store_fact_words(conn, first, batches)   # same input twice: no dupes
    assert _ask_words(conn, 1) == {"medkit", "boo boo kit"}

    # A new run's output replaces the old rows entirely, including for a
    # fact the model now returns nothing for.
    second = {"vocab-facts-0": {"entries": [
        {"fact_id": 1, "words": ["trauma kit"]},
    ]}}
    store_fact_words(conn, second, batches)
    assert _ask_words(conn, 1) == {"trauma kit"}


def test_failed_job_keeps_previous_words(conn):
    batches = _fact_batches(conn)
    store_fact_words(conn, {"vocab-facts-0": {"entries": [
        {"fact_id": 1, "words": ["medkit"]},
    ]}}, batches)
    # The whole job came back None (quota, safety block): paid work from the
    # previous run must not be wiped in exchange for nothing.
    store_fact_words(conn, {"vocab-facts-0": None}, batches)
    assert _ask_words(conn, 1) == {"medkit"}


def test_fact_jobs_batch_at_twenty_five():
    facts = [(i, "water", f"fact number {i}") for i in range(1, 31)]
    jobs, batches = build_fact_jobs(facts)
    assert len(jobs) == 2
    assert set(batches["vocab-facts-0"]) == set(range(1, 26))
    assert set(batches["vocab-facts-1"]) == set(range(26, 31))


# --------------------------------------------------------------------------
# Stage B: generated entity aliases
# --------------------------------------------------------------------------

def test_alias_written_with_source_marker(conn):
    results = {"vocab-entities-0": {"entries": [
        {"entity_id": 1, "aliases": ["Empire Storage"]},
    ]}}
    stats = store_entity_aliases(conn, results, _entity_batches(conn))

    assert stats["aliases_inserted"] == 1
    rows = conn.execute(
        "SELECT alias, source FROM entity_alias WHERE entity_id=1 ORDER BY id"
    ).fetchall()
    # The hand-made alias keeps its NULL source; the generated one is marked
    # and stored lowercased.
    assert rows == [("the storage unit", None), ("empire storage", ALIAS_SOURCE)]


def test_alias_matching_name_or_existing_alias_is_skipped(conn):
    results = {"vocab-entities-0": {"entries": [
        {"entity_id": 1, "aliases": [
            "Emigrant Storage",     # the name itself
            "emigrant",             # whole word of the name — adds nothing
            "the storage unit",     # already a hand-made alias
            "empire storage",       # the actual mishearing: keep
        ]},
    ]}}
    stats = store_entity_aliases(conn, results, _entity_batches(conn))
    assert stats["aliases_inserted"] == 1
    generated = [a for (a,) in conn.execute(
        "SELECT alias FROM entity_alias WHERE entity_id=1 AND source=?",
        (ALIAS_SOURCE,))]
    assert generated == ["empire storage"]


def test_alias_rerun_wipes_only_generated_rows(conn):
    batches = _entity_batches(conn)
    store_entity_aliases(conn, {"vocab-entities-0": {"entries": [
        {"entity_id": 1, "aliases": ["empire storage", "immigrant storage"]},
    ]}}, batches)
    store_entity_aliases(conn, {"vocab-entities-0": {"entries": [
        {"entity_id": 1, "aliases": ["empire storage"]},
    ]}}, batches)

    rows = conn.execute(
        "SELECT alias, source FROM entity_alias WHERE entity_id=1"
    ).fetchall()
    # Hand-made survives both runs; the second run's rows fully replace the
    # first's — 'immigrant storage' is gone, nothing is duplicated.
    assert ("the storage unit", None) in rows
    assert ("empire storage", ALIAS_SOURCE) in rows
    assert len(rows) == 2


def test_select_entities_ignores_generated_aliases_for_presence(conn):
    """A generated alias must not count as 'already present' on the next
    run: the store wipes generated rows first, so treating them as present
    would make every regenerated alias skip itself and vanish."""
    batches = _entity_batches(conn)
    results = {"vocab-entities-0": {"entries": [
        {"entity_id": 1, "aliases": ["empire storage"]},
    ]}}
    store_entity_aliases(conn, results, batches)

    # Rebuild the batches from the database, as a fresh run would.
    rebuilt = _entity_batches(conn)
    _, aliases = rebuilt["vocab-entities-0"][1]
    assert aliases == ["the storage unit"]   # hand-made only

    store_entity_aliases(conn, results, rebuilt)
    generated = [a for (a,) in conn.execute(
        "SELECT alias FROM entity_alias WHERE entity_id=1 AND source=?",
        (ALIAS_SOURCE,))]
    assert generated == ["empire storage"]


def test_unknown_entity_id_is_dropped(conn):
    results = {"vocab-entities-0": {"entries": [
        {"entity_id": 42, "aliases": ["phantom place"]},
    ]}}
    stats = store_entity_aliases(conn, results, _entity_batches(conn))
    assert stats["aliases_inserted"] == 0
    assert stats["unknown_entity_ids"] == 1


# --------------------------------------------------------------------------
# Curated vocabulary — the observed asks the first real run proved a
# generator does not guess
# --------------------------------------------------------------------------

def test_curated_words_land_even_when_the_model_offers_none(conn):
    # The model filled its cap with plausible words and never said "medkit".
    # Curated words ride on top of whatever the model returns — including
    # nothing at all.
    batches = _fact_batches(conn)
    results = {jid: {"entries": []} for jid in batches}
    store_fact_words(conn, results, batches)
    assert "medkit" in _ask_words(conn, 1)          # "first aid" fact
    assert "delivered" in _ask_words(conn, 2)       # "vouchers" fact
    assert "delivery" in _ask_words(conn, 2)


def test_curated_words_respect_the_already_present_filter(conn):
    # Fact 1 says "med kit" in so many words; storing it again as vocabulary
    # would be noise. The curated list goes through the same filters.
    batches = _fact_batches(conn)
    store_fact_words(conn, {jid: {"entries": []} for jid in batches}, batches)
    assert "med kit" not in _ask_words(conn, 1)


def test_curated_words_survive_a_rerun_outside_the_cap(conn):
    # Six model words fill the cap; the observed ask still lands, and a
    # second run reproduces the same rows rather than losing them.
    batches = _fact_batches(conn)
    jid = next(iter(batches))
    six = ["bandage", "ouch", "hurt", "injury", "sling", "plaster"]
    results = {j: {"entries": [{"fact_id": 1, "words": six}]}
               if j == jid else {"entries": []} for j in batches}
    store_fact_words(conn, results, batches)
    first = _ask_words(conn, 1)
    assert "medkit" in first and len([w for w in six if w in first]) == 6
    store_fact_words(conn, results, batches)
    assert _ask_words(conn, 1) == first


def test_curated_mishearing_becomes_an_alias(conn):
    # "Empire storage" is how the recogniser heard it in a real journal
    # question that retrieved nothing. The entity's own names are filtered
    # out; only the mishearing is new information.
    batches = _entity_batches(conn)
    store_entity_aliases(conn, {jid: {"entries": []} for jid in batches}, batches)
    stored = {a for (a,) in conn.execute(
        "SELECT alias FROM entity_alias WHERE entity_id=1 AND source=?",
        (ALIAS_SOURCE,))}
    assert "empire storage" in stored and "empire" in stored
    assert "emigrant storage" not in stored          # it IS the name
    assert "emigrant" not in stored
