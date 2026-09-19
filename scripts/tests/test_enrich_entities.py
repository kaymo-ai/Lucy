import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrich_schema import create_enrichment_tables, verify_evidence_complete
from enrich_entities import merge_duplicate_entities


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE person (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE person_content (id INTEGER PRIMARY KEY, content TEXT);
        CREATE TABLE camp_knowledge (id INTEGER PRIMARY KEY, title TEXT);
        INSERT INTO person_content (id, content) VALUES
            (1, 'Playaella is happening again this year'),
            (2, 'playella was so much fun'),
            (3, 'anyone remember Playella 2019'),
            (4, 'Doris needs a new padlock'),
            (5, 'PS is our camp name'),
            (6, 'Public Shade needs new poles');
    """)
    c.execute("PRAGMA foreign_keys = ON")
    create_enrichment_tables(c)
    return c


def _add_entity(conn, name, kind, mention_count=1, aliases=(), evidence_id=1,
                 quote="evidence quote"):
    conn.execute(
        "INSERT INTO entity (name, kind, summary, mention_count) VALUES (?,?,'',?)",
        (name, kind, mention_count))
    eid = conn.execute("SELECT id FROM entity WHERE name = ?", (name,)).fetchone()[0]
    for alias in aliases:
        conn.execute("INSERT INTO entity_alias (entity_id, alias) VALUES (?,?)",
                     (eid, alias))
    conn.execute("""INSERT INTO evidence (claim_table, claim_id, source_table,
                    source_id, quote) VALUES ('entity', ?, 'person_content', ?, ?)""",
                 (eid, evidence_id, quote))
    return eid


def test_case_only_names_merge(conn):
    _add_entity(conn, "Playella", "tradition", mention_count=2, evidence_id=1)
    _add_entity(conn, "playella", "tradition", mention_count=5, evidence_id=2)

    collisions = merge_duplicate_entities(conn)

    assert collisions == []
    rows = conn.execute("SELECT name, mention_count FROM entity").fetchall()
    assert rows == [("playella", 7)]  # higher mention_count survives


def test_alias_collision_merges(conn):
    """A's own name sitting in B's alias set means they're the same thing."""
    _add_entity(conn, "Doris", "vehicle", mention_count=3, aliases=["Oris"],
                evidence_id=4)
    _add_entity(conn, "Oris", "vehicle", mention_count=1, evidence_id=4)

    collisions = merge_duplicate_entities(conn)

    assert collisions == []
    rows = conn.execute("SELECT name, mention_count FROM entity").fetchall()
    assert rows == [("Doris", 4)]
    aliases = {a for (a,) in conn.execute("SELECT alias FROM entity_alias")}
    assert aliases == {"Oris"}  # a genuinely different spelling — kept


def test_three_way_chain_collapses_to_one(conn):
    """Playaella -> alias 'playella' on a different row named 'Playella' ->
    alias 'playaella' on a third row named 'playella' — none pairwise-equal
    to all others, but they chain into a single entity."""
    _add_entity(conn, "Playaella", "tradition", mention_count=5,
                aliases=["playella"], evidence_id=1)
    _add_entity(conn, "Playella", "tradition", mention_count=2,
                aliases=["playaella"], evidence_id=2)
    _add_entity(conn, "playella", "tradition", mention_count=2, evidence_id=3)

    collisions = merge_duplicate_entities(conn)

    assert collisions == []
    rows = conn.execute("SELECT name, mention_count FROM entity").fetchall()
    assert len(rows) == 1
    assert rows[0] == ("Playaella", 9)


def test_evidence_survives_merge_and_points_at_a_real_entity(conn):
    a = _add_entity(conn, "Playella", "tradition", mention_count=1, evidence_id=1)
    b = _add_entity(conn, "playella", "tradition", mention_count=1, evidence_id=2)

    merge_duplicate_entities(conn)

    survivor_id = conn.execute("SELECT id FROM entity").fetchone()[0]
    claim_ids = {r[0] for r in conn.execute(
        "SELECT claim_id FROM evidence WHERE claim_table='entity'")}
    assert claim_ids == {survivor_id}
    assert a not in claim_ids or b not in claim_ids  # loser id is gone
    verify_evidence_complete(conn)  # must not raise


def test_different_kind_pairs_are_not_merged_but_reported(conn):
    _add_entity(conn, "PS", "place", mention_count=2, evidence_id=5)
    _add_entity(conn, "Public Shade", "structure", mention_count=8,
                aliases=["PS"], evidence_id=6)

    collisions = merge_duplicate_entities(conn)

    names = {r[0] for r in conn.execute("SELECT name FROM entity")}
    assert names == {"PS", "Public Shade"}  # both still present, not merged
    assert len(collisions) == 1
    na, ka, nb, kb = collisions[0]
    assert {ka, kb} == {"place", "structure"}
    assert {na, nb} == {"PS", "Public Shade"}


def test_verify_evidence_complete_passes_after_merge(conn):
    _add_entity(conn, "Doris", "vehicle", mention_count=1, evidence_id=4)
    _add_entity(conn, "doris", "vehicle", mention_count=1, evidence_id=4)
    _add_entity(conn, "Public Shade", "structure", mention_count=1, evidence_id=6)

    merge_duplicate_entities(conn)

    verify_evidence_complete(conn)  # must not raise
    n = conn.execute("SELECT COUNT(*) FROM entity").fetchone()[0]
    assert n == 2


def test_entity_fact_is_repointed_not_orphaned_or_cascaded(conn):
    """entity_fact.entity_id has ON DELETE CASCADE — a merge that deletes the
    loser row without repointing entity_fact first would silently drop its
    facts. Empty in this pass, but Task 4 populates it, so this must hold now."""
    conn.execute("PRAGMA foreign_keys = ON")
    a = _add_entity(conn, "Playella", "tradition", mention_count=1, evidence_id=1)
    b = _add_entity(conn, "playella", "tradition", mention_count=5, evidence_id=2)
    conn.execute("INSERT INTO entity_fact (id, entity_id, fact) VALUES (1, ?, 'runs every year')",
                 (a,))
    conn.execute("""INSERT INTO evidence (claim_table, claim_id, source_table,
                    source_id, quote) VALUES ('entity_fact', 1, 'person_content', 1, 'q')""")

    merge_duplicate_entities(conn)

    survivor_id = conn.execute("SELECT id FROM entity").fetchone()[0]
    facts = conn.execute("SELECT entity_id, fact FROM entity_fact").fetchall()
    assert facts == [(survivor_id, "runs every year")]


def test_merge_is_idempotent(conn):
    _add_entity(conn, "Playella", "tradition", mention_count=1, evidence_id=1)
    _add_entity(conn, "playella", "tradition", mention_count=1, evidence_id=2)

    merge_duplicate_entities(conn)
    n_after_first = conn.execute("SELECT COUNT(*) FROM entity").fetchone()[0]
    collisions = merge_duplicate_entities(conn)
    n_after_second = conn.execute("SELECT COUNT(*) FROM entity").fetchone()[0]

    assert n_after_first == n_after_second == 1
    assert collisions == []
