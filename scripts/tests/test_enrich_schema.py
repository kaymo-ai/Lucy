import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enrich_schema import (
    create_enrichment_tables,
    find_unevidenced_claims,
    verify_evidence_complete,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE person (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE person_content (id INTEGER PRIMARY KEY, content TEXT);
        CREATE TABLE camp_knowledge (id INTEGER PRIMARY KEY, title TEXT);
        INSERT INTO person (id, name) VALUES (1, 'Nick Hadley');
        INSERT INTO person (id, name) VALUES (7, 'Doris Chen');
        INSERT INTO person_content (id, content) VALUES (1, 'You will need a 9/16 socket');
    """)
    c.execute("PRAGMA foreign_keys = ON")
    create_enrichment_tables(c)
    return c


def test_creates_every_enrichment_table(conn):
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("entity", "entity_alias", "entity_fact", "person_profile",
              "expertise", "relationship", "lore", "evidence"):
        assert t in names, f"missing table: {t}"


def test_evidence_requires_a_real_source_row(conn):
    """An evidence row must point at content that exists."""
    conn.execute("INSERT INTO entity (name, kind, summary) VALUES (?,?,?)",
                 ("Doris", "vehicle", "A storage vehicle."))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("""
            INSERT INTO evidence (claim_table, claim_id, source_table, source_id, quote)
            VALUES ('entity', 1, 'person_content', 99999, 'nonexistent')
        """)


def test_evidence_check_rejects_an_unknown_source_table(conn):
    """An unrecognized source_table is rejected by the CHECK constraint on
    evidence.source_table, before the evidence_source_exists trigger even
    runs (its WHERE clause only fires for 'person_content' or
    'camp_knowledge' and would silently pass anything else)."""
    conn.execute("INSERT INTO entity (name, kind, summary) VALUES (?,?,?)",
                 ("Doris", "vehicle", "A storage vehicle."))
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        conn.execute("""
            INSERT INTO evidence (claim_table, claim_id, source_table, source_id, quote)
            VALUES ('entity', 1, 'twitter', 1, 'nope')
        """)


def test_evidence_trigger_rejects_a_known_source_table_with_missing_row(conn):
    """A recognized source_table with a source_id that doesn't exist is
    rejected by the evidence_source_exists trigger, not the CHECK."""
    conn.execute("INSERT INTO entity (name, kind, summary) VALUES (?,?,?)",
                 ("Doris", "vehicle", "A storage vehicle."))
    with pytest.raises(sqlite3.IntegrityError, match="does not exist in the named source table"):
        conn.execute("""
            INSERT INTO evidence (claim_table, claim_id, source_table, source_id, quote)
            VALUES ('entity', 1, 'person_content', 99999, 'nope')
        """)


def test_entity_facts_can_disagree_with_each_other(conn):
    """Cece said no ladders; Marcus said three. Both are recorded."""
    conn.execute("INSERT INTO entity (name, kind, summary) VALUES (?,?,?)",
                 ("Doris", "vehicle", "A storage vehicle."))
    conn.execute("""INSERT INTO entity_fact (entity_id, fact, category, asserted_on)
                    VALUES (1, 'Contains no ladders', 'contents', '2022-08-19')""")
    conn.execute("""INSERT INTO entity_fact (entity_id, fact, category, asserted_on)
                    VALUES (1, 'Contains at least 3 ladders', 'contents', '2022-08-24')""")
    rows = conn.execute(
        "SELECT COUNT(*) FROM entity_fact WHERE category='contents'").fetchone()[0]
    assert rows == 2


def test_entity_kind_rejects_unknown_value(conn):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        conn.execute("INSERT INTO entity (name, kind, summary) VALUES (?,?,?)",
                     ("Doris", "banana", "A storage vehicle."))


def test_entity_fact_category_rejects_unknown_value(conn):
    conn.execute("INSERT INTO entity (name, kind, summary) VALUES (?,?,?)",
                 ("Doris", "vehicle", "A storage vehicle."))
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        conn.execute("""INSERT INTO entity_fact (entity_id, fact, category, asserted_on)
                        VALUES (1, 'Contains no ladders', 'nonsense', '2022-08-19')""")


def test_entity_fact_category_allows_null(conn):
    """category is nullable — the CHECK must not reject NULL."""
    conn.execute("INSERT INTO entity (name, kind, summary) VALUES (?,?,?)",
                 ("Doris", "vehicle", "A storage vehicle."))
    conn.execute("""INSERT INTO entity_fact (entity_id, fact, category, asserted_on)
                    VALUES (1, 'Contains no ladders', NULL, '2022-08-19')""")
    row = conn.execute("SELECT category FROM entity_fact WHERE id = 1").fetchone()
    assert row[0] is None


def test_expertise_strength_rejects_unknown_value(conn):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        conn.execute("INSERT INTO expertise (person_id, topic, strength) VALUES (1, 'welding', 'expert')")


def test_evidence_claim_table_rejects_unknown_value(conn):
    conn.execute("INSERT INTO entity (name, kind, summary) VALUES (?,?,?)",
                 ("Doris", "vehicle", "A storage vehicle."))
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        conn.execute("""
            INSERT INTO evidence (claim_table, claim_id, source_table, source_id, quote)
            VALUES ('not_a_real_table', 1, 'person_content', 1, 'nope')
        """)


def test_aliases_are_unique_per_entity(conn):
    conn.execute("INSERT INTO entity (name, kind, summary) VALUES (?,?,?)",
                 ("Doris", "vehicle", "A storage vehicle."))
    conn.execute("INSERT INTO entity_alias (entity_id, alias) VALUES (1, 'Boris/Doris')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO entity_alias (entity_id, alias) VALUES (1, 'Boris/Doris')")


# -- evidence completeness (Finding 1) ---------------------------------------
#
# A claim row must exist before an evidence row can reference its id, so
# evidence completeness cannot be a per-row trigger. verify_evidence_complete
# is meant to run once at the end of a pipeline pass, after every claim from
# that pass has had its chance to pick up evidence.


def test_claim_with_evidence_passes_verify(conn):
    conn.execute("INSERT INTO entity (name, kind, summary) VALUES (?,?,?)",
                 ("Doris", "vehicle", "A storage vehicle."))
    conn.execute("""
        INSERT INTO evidence (claim_table, claim_id, source_table, source_id, quote)
        VALUES ('entity', 1, 'person_content', 1, 'the socket truck')
    """)
    verify_evidence_complete(conn)  # must not raise


def test_claim_with_no_evidence_raises_and_names_the_table(conn):
    conn.execute("INSERT INTO entity (name, kind, summary) VALUES (?,?,?)",
                 ("Doris", "vehicle", "A storage vehicle."))
    with pytest.raises(ValueError, match="entity"):
        verify_evidence_complete(conn)


def test_person_profile_is_checked_by_person_id_not_id(conn):
    """A naive implementation selects `id` for every claim table; person_profile
    has no `id` column at all — its primary key is person_id. An impl that gets
    this wrong either blows up with 'no such column: id' or silently treats the
    table as always-empty/always-fine. Both are wrong: a person_profile with
    no evidence must be flagged, keyed by its actual person_id."""
    conn.execute("""INSERT INTO person_profile (person_id, summary)
                    VALUES (7, 'Doris has run the tool truck since 2019.')""")

    offenders = find_unevidenced_claims(conn)
    assert offenders.get("person_profile") == [7]

    # Evidence pointing at some *other* claim_id must not clear person 7's flag.
    conn.execute("""
        INSERT INTO evidence (claim_table, claim_id, source_table, source_id, quote)
        VALUES ('person_profile', 1, 'person_content', 1, 'wrong id, should not count')
    """)
    offenders = find_unevidenced_claims(conn)
    assert offenders.get("person_profile") == [7]

    with pytest.raises(ValueError, match="person_profile"):
        verify_evidence_complete(conn)

    # Evidence keyed correctly by person_id clears it.
    conn.execute("""
        INSERT INTO evidence (claim_table, claim_id, source_table, source_id, quote)
        VALUES ('person_profile', 7, 'person_content', 1, 'Doris runs the tool truck')
    """)
    assert "person_profile" not in find_unevidenced_claims(conn)
    verify_evidence_complete(conn)  # must not raise


def test_database_with_no_claims_passes_verify(conn):
    verify_evidence_complete(conn)  # must not raise
    assert find_unevidenced_claims(conn) == {}


# -- camp_fact (documents are not entities) ----------------------------------

def test_camp_fact_table_exists(conn):
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "camp_fact" in names


def test_camp_fact_requires_evidence(conn):
    conn.execute("INSERT INTO camp_knowledge (id, title) VALUES (1, 'PS Manual')")
    conn.execute("""INSERT INTO camp_fact (topic, fact, category, year)
                    VALUES ('water', 'We order water fills from the supplier.', 'how', 2023)""")
    with pytest.raises(ValueError, match="camp_fact"):
        verify_evidence_complete(conn)

    conn.execute("""
        INSERT INTO evidence (claim_table, claim_id, source_table, source_id, quote)
        VALUES ('camp_fact', 1, 'camp_knowledge', 1, 'we order water fills')
    """)
    verify_evidence_complete(conn)  # must not raise


def test_camp_fact_evidence_requires_a_real_camp_knowledge_row(conn):
    conn.execute("""INSERT INTO camp_fact (topic, fact, category, year)
                    VALUES ('water', 'We order water fills.', 'how', 2023)""")
    with pytest.raises(sqlite3.IntegrityError,
                        match="does not exist in the named source table"):
        conn.execute("""
            INSERT INTO evidence (claim_table, claim_id, source_table, source_id, quote)
            VALUES ('camp_fact', 1, 'camp_knowledge', 99999, 'nope')
        """)


def test_camp_fact_category_rejects_unknown_value(conn):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        conn.execute("""INSERT INTO camp_fact (topic, fact, category, year)
                        VALUES ('water', 'We order water fills.', 'nonsense', 2023)""")


def test_camp_fact_category_allows_null(conn):
    conn.execute("""INSERT INTO camp_fact (topic, fact, category, year)
                    VALUES ('water', 'We order water fills.', NULL, 2023)""")
    row = conn.execute("SELECT category FROM camp_fact WHERE id = 1").fetchone()
    assert row[0] is None


def test_camp_fact_year_can_differ_from_another_facts_year(conn):
    """A 2019 rule and a 2025 rule about the same topic must both be
    storable and distinguishable by year."""
    conn.execute("INSERT INTO camp_knowledge (id, title) VALUES (1, 'PS Manual 2019')")
    conn.execute("INSERT INTO camp_knowledge (id, title) VALUES (2, 'PS Manual 2025')")
    conn.execute("""INSERT INTO camp_fact (topic, fact, category, year)
                    VALUES ('safety', 'Fire extinguishers required at every structure.', 'rule', 2019)""")
    conn.execute("""INSERT INTO camp_fact (topic, fact, category, year)
                    VALUES ('safety', 'Fire extinguishers required within 10 feet of any flame effect.', 'rule', 2025)""")
    conn.executemany("""
        INSERT INTO evidence (claim_table, claim_id, source_table, source_id, quote)
        VALUES ('camp_fact', ?, 'camp_knowledge', ?, ?)
    """, [(1, 1, "Fire extinguishers required at every structure."),
          (2, 2, "Fire extinguishers required within 10 feet of any flame effect.")])
    verify_evidence_complete(conn)
    years = {r[0] for r in conn.execute("SELECT year FROM camp_fact")}
    assert years == {2019, 2025}


def test_stale_evidence_check_is_migrated_preserving_existing_rows():
    """A database from before camp_fact existed has an evidence table whose
    CHECK constraint doesn't mention it. create_enrichment_tables must widen
    that CHECK in place (CREATE TABLE IF NOT EXISTS alone is a no-op here)
    without losing rows already in evidence."""
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE person (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE person_content (id INTEGER PRIMARY KEY, content TEXT);
        CREATE TABLE camp_knowledge (id INTEGER PRIMARY KEY, title TEXT);
        INSERT INTO person_content (id, content) VALUES (1, 'You will need a 9/16 socket');

        CREATE TABLE entity (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
            kind TEXT, summary TEXT, first_seen TEXT, last_seen TEXT,
            mention_count INTEGER DEFAULT 0
        );
        INSERT INTO entity (id, name, kind, summary) VALUES (1, 'Doris', 'vehicle', 'A storage vehicle.');

        -- The old, pre-camp_fact evidence table shape.
        CREATE TABLE evidence (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_table  TEXT NOT NULL CHECK (claim_table IN
                ('entity', 'entity_fact', 'person_profile', 'expertise', 'relationship', 'lore')),
            claim_id     INTEGER NOT NULL,
            source_table TEXT NOT NULL CHECK (source_table IN ('person_content', 'camp_knowledge')),
            source_id    INTEGER NOT NULL,
            quote        TEXT NOT NULL
        );
        INSERT INTO evidence (claim_table, claim_id, source_table, source_id, quote)
            VALUES ('entity', 1, 'person_content', 1, 'a pre-existing evidence row');
    """)
    c.execute("PRAGMA foreign_keys = ON")

    create_enrichment_tables(c)  # must migrate, not silently leave the old CHECK

    preserved = c.execute(
        "SELECT claim_table, claim_id, quote FROM evidence").fetchall()
    assert preserved == [("entity", 1, "a pre-existing evidence row")]

    # camp_fact evidence must now be insertable — this is what was broken.
    c.execute("INSERT INTO camp_knowledge (id, title) VALUES (1, 'PS Manual')")
    c.execute("""INSERT INTO camp_fact (topic, fact, category, year)
                VALUES ('water', 'We order water fills.', 'how', 2023)""")
    c.execute("""
        INSERT INTO evidence (claim_table, claim_id, source_table, source_id, quote)
        VALUES ('camp_fact', 1, 'camp_knowledge', 1, 'we order water fills')
    """)
    verify_evidence_complete(c)


def test_drop_enrichment_tables_removes_camp_fact(conn):
    from enrich_schema import drop_enrichment_tables
    conn.execute("INSERT INTO camp_knowledge (id, title) VALUES (1, 'PS Manual')")
    conn.execute("""INSERT INTO camp_fact (topic, fact, category, year)
                    VALUES ('water', 'We order water fills.', 'how', 2023)""")
    drop_enrichment_tables(conn)
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "camp_fact" not in names
