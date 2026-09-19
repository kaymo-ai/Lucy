#!/usr/bin/env python3
"""
Test the entity queries work correctly by simulating what Swift will do.
This verifies the database schema and data are correct for the Swift code.
"""

import sqlite3
import json
from pathlib import Path

DB_PATH = Path(__file__).parent / "output" / "ps_knowledge.db"


def test_has_entity_tables(conn):
    """Test: hasEntityTables()"""
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='entity'")
    result = cursor.fetchone()
    assert result is not None, "Entity table should exist"
    print("   hasEntityTables: PASS")


def test_find_person_entity(conn):
    """Test: findPersonEntity(_ name: String)"""
    cursor = conn.cursor()

    # Exact match
    cursor.execute("""
        SELECT e.id, e.type, e.canonical_name, e.slug
        FROM entity e
        JOIN entity_alias ea ON e.id = ea.entity_id
        WHERE e.type = 'person' AND LOWER(ea.alias) = LOWER(?)
        LIMIT 1
    """, ("Piotr",))
    result = cursor.fetchone()
    assert result is not None, "Should find Piotr by exact match"
    print(f"   findPersonEntity('Piotr'): {result[2]} (id={result[0]})")

    # Fuzzy match
    cursor.execute("""
        SELECT e.id, e.type, e.canonical_name, e.slug
        FROM entity e
        JOIN entity_alias ea ON e.id = ea.entity_id
        WHERE e.type = 'person' AND LOWER(ea.alias) LIKE LOWER(?)
        LIMIT 1
    """, ("%Zach C%",))
    result = cursor.fetchone()
    assert result is not None, "Should find Zach by fuzzy match"
    print(f"   findPersonEntity('Zach C'): {result[2]}")
    print("   findPersonEntity: PASS")


def test_get_entity_profile(conn):
    """Test: getEntityProfile(_ entityId: Int64)"""
    cursor = conn.cursor()

    # Find a person with a profile
    cursor.execute("""
        SELECT ep.entity_id, ep.summary, ep.expertise, ep.years_active, ep.key_contributions
        FROM entity_profile ep
        JOIN entity e ON ep.entity_id = e.id
        WHERE ep.summary IS NOT NULL AND ep.summary != ''
        LIMIT 1
    """)
    result = cursor.fetchone()
    assert result is not None, "Should find a profile"

    entity_id, summary, expertise_json, years_json, contributions = result
    expertise = json.loads(expertise_json) if expertise_json else []
    years = json.loads(years_json) if years_json else []

    print(f"   Profile for entity {entity_id}:")
    print(f"      Summary: {summary[:80]}...")
    print(f"      Expertise: {expertise}")
    print(f"      Years: {years}")
    print("   getEntityProfile: PASS")


def test_get_entity_aliases(conn):
    """Test: getEntityAliases(_ entityId: Int64)"""
    cursor = conn.cursor()

    # Find an entity with multiple aliases
    cursor.execute("""
        SELECT e.id, e.canonical_name, GROUP_CONCAT(ea.alias, '|') as aliases
        FROM entity e
        JOIN entity_alias ea ON e.id = ea.entity_id
        WHERE e.type = 'person'
        GROUP BY e.id
        HAVING COUNT(ea.alias) >= 2
        LIMIT 1
    """)
    result = cursor.fetchone()
    assert result is not None, "Should find entity with aliases"

    entity_id, name, aliases = result
    alias_list = aliases.split('|')

    print(f"   Aliases for '{name}': {alias_list}")
    print("   getEntityAliases: PASS")


def test_get_entity_relationships(conn):
    """Test: getEntityRelationships(_ entityId: Int64)"""
    cursor = conn.cursor()

    # Find an entity with relationships
    cursor.execute("""
        SELECT DISTINCT r.source_entity_id
        FROM relationship r
        LIMIT 1
    """)
    result = cursor.fetchone()
    assert result is not None, "Should find relationships"

    entity_id = result[0]

    cursor.execute("""
        SELECT source_entity_id, target_entity_id, type, weight, year
        FROM relationship
        WHERE source_entity_id = ? OR target_entity_id = ?
        LIMIT 5
    """, (entity_id, entity_id))

    relationships = cursor.fetchall()
    print(f"   Relationships for entity {entity_id}: {len(relationships)} found")
    for rel in relationships[:3]:
        print(f"      {rel[2]}: {rel[0]} -> {rel[1]} (year={rel[4]})")
    print("   getEntityRelationships: PASS")


def test_get_entity_with_profile(conn):
    """Test: getEntityWithProfile(_ entityId: Int64)"""
    cursor = conn.cursor()

    # Find an entity with a profile
    cursor.execute("""
        SELECT e.id, e.canonical_name, ep.summary
        FROM entity e
        JOIN entity_profile ep ON e.id = ep.entity_id
        WHERE ep.summary IS NOT NULL
        LIMIT 1
    """)
    result = cursor.fetchone()
    assert result is not None, "Should find entity with profile"

    entity_id, name, summary = result

    # Get aliases
    cursor.execute("SELECT alias FROM entity_alias WHERE entity_id = ?", (entity_id,))
    aliases = [r[0] for r in cursor.fetchall()]

    # Get relationships
    cursor.execute("""
        SELECT COUNT(*) FROM relationship
        WHERE source_entity_id = ? OR target_entity_id = ?
    """, (entity_id, entity_id))
    rel_count = cursor.fetchone()[0]

    print(f"   Full entity '{name}':")
    print(f"      Aliases: {aliases}")
    print(f"      Has profile: True")
    print(f"      Relationships: {rel_count}")
    print("   getEntityWithProfile: PASS")


def test_get_all_entity_embeddings(conn):
    """Test: getAllEntityEmbeddings()"""
    cursor = conn.cursor()

    cursor.execute("""
        SELECT COUNT(*) FROM entity_embedding
    """)
    total = cursor.fetchone()[0]
    assert total > 0, "Should have entity embeddings"

    cursor.execute("""
        SELECT ee.entity_id, e.canonical_name, e.type, LENGTH(ee.embedding)
        FROM entity_embedding ee
        JOIN entity e ON ee.entity_id = e.id
        LIMIT 3
    """)

    print(f"   Total entity embeddings: {total}")
    for row in cursor.fetchall():
        print(f"      {row[1]} ({row[2]}): {row[3]} bytes")
    print("   getAllEntityEmbeddings: PASS")


def main():
    print("=" * 60)
    print("Swift Integration Test")
    print("=" * 60)
    print(f"\nDatabase: {DB_PATH}\n")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    tests = [
        ("hasEntityTables", test_has_entity_tables),
        ("findPersonEntity", test_find_person_entity),
        ("getEntityProfile", test_get_entity_profile),
        ("getEntityAliases", test_get_entity_aliases),
        ("getEntityRelationships", test_get_entity_relationships),
        ("getEntityWithProfile", test_get_entity_with_profile),
        ("getAllEntityEmbeddings", test_get_all_entity_embeddings),
    ]

    passed = 0
    failed = 0

    for name, test_func in tests:
        try:
            print(f"\nTest: {name}")
            test_func(conn)
            passed += 1
        except AssertionError as e:
            print(f"   FAILED: {e}")
            failed += 1
        except Exception as e:
            print(f"   ERROR: {e}")
            failed += 1

    conn.close()

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    exit(main())
