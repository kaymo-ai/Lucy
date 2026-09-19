#!/usr/bin/env python3
"""
Pass 3: Relationship Extraction

Builds relationship graph between entities from:
1. Structured data (shifts, roster) - no LLM needed
2. Co-mentions in messages - no LLM needed
3. Complex relationships from message analysis - LLM

Relationship types:
- works_on: person -> role (from shifts)
- attended: person -> year (from roster)
- knows_about: person -> topic (from messages)
- leads: person -> role/area (from LLM analysis)
- mentioned_with: person -> person (co-occurrence)

Usage:
    python -m knowledge_graph.extract_relations [--skip-llm]
"""

import re
import json
import argparse
from collections import defaultdict

try:
    import anthropic
except ImportError:
    anthropic = None

from .config import (
    DB_PATH, ANTHROPIC_API_KEY, EXTRACTION_MODEL,
    RATE_LIMIT_DELAY
)
from .db import (
    get_connection, transaction, normalize_slug,
    insert_relationship, get_entity_by_slug, get_entity_by_alias
)
from .models import Relationship


LEADERSHIP_EXTRACTION_PROMPT = """Analyze these messages and identify leadership or coordination roles.

Messages:
{messages}

Return JSON array of relationships found:
[
  {{"person": "Name", "role": "what they lead/coordinate", "evidence": "quote from message"}}
]

Only include clear leadership/coordination mentions. Return empty array [] if none found.
Return ONLY valid JSON:"""


def extract_shifts_relationships(conn) -> list[Relationship]:
    """Extract person -> role relationships from shifts table."""
    cursor = conn.cursor()
    cursor.execute('''
        SELECT DISTINCT s.person_name, s.role, s.year
        FROM shifts s
        WHERE s.person_name IS NOT NULL AND s.role IS NOT NULL
    ''')

    relationships = []
    for row in cursor.fetchall():
        person_name = row['person_name']
        role = row['role']
        year = row['year']

        # Find or create entities
        person_entity = get_entity_by_alias(conn, person_name)
        if not person_entity:
            continue

        # Create role entity slug
        role_slug = normalize_slug(role)
        role_entity = get_entity_by_slug(conn, role_slug)

        if role_entity:
            rel = Relationship(
                source_entity_id=person_entity.id,
                target_entity_id=role_entity.id,
                type='works_on',
                year=year,
                evidence=[f"Shift assignment: {role}"]
            )
            relationships.append(rel)

    return relationships


def extract_roster_relationships(conn) -> list[Relationship]:
    """Extract person -> year attendance relationships from roster."""
    cursor = conn.cursor()
    cursor.execute('''
        SELECT DISTINCT r.name, r.year, r.home_city
        FROM roster r
        WHERE r.name IS NOT NULL AND r.year IS NOT NULL
    ''')

    relationships = []
    for row in cursor.fetchall():
        person_name = row['name']
        year = row['year']
        home_city = row['home_city']

        person_entity = get_entity_by_alias(conn, person_name)
        if not person_entity:
            continue

        # Create "attended_YEAR" relationship
        # Since we don't have year entities, we store year in the relationship
        # This creates a self-relationship with year metadata
        rel = Relationship(
            source_entity_id=person_entity.id,
            target_entity_id=person_entity.id,  # Self-reference for attendance
            type='attended',
            year=year,
            evidence=[f"Roster entry, home city: {home_city or 'unknown'}"]
        )
        relationships.append(rel)

    return relationships


def extract_comentions(conn) -> list[Relationship]:
    """Extract co-mention relationships from messages."""
    cursor = conn.cursor()

    # Get all person entities and their aliases
    cursor.execute('''
        SELECT e.id, e.canonical_name, GROUP_CONCAT(ea.alias, '|') as aliases
        FROM entity e
        JOIN entity_alias ea ON e.id = ea.entity_id
        WHERE e.type = 'person'
        GROUP BY e.id
    ''')
    entities = [dict(row) for row in cursor.fetchall()]

    # Build alias -> entity_id lookup
    alias_to_entity = {}
    for entity in entities:
        for alias in entity['aliases'].split('|'):
            alias_lower = alias.lower()
            if len(alias_lower) >= 3:  # Skip very short names
                alias_to_entity[alias_lower] = entity['id']

    # Find co-mentions in messages
    cursor.execute('SELECT content FROM person_content WHERE content IS NOT NULL')

    co_mentions = defaultdict(int)

    for row in cursor.fetchall():
        content = row['content'].lower()

        # Find all mentioned entities in this message
        mentioned = set()
        for alias, entity_id in alias_to_entity.items():
            # Use word boundary matching for longer names
            if len(alias) >= 4:
                if re.search(r'\b' + re.escape(alias) + r'\b', content):
                    mentioned.add(entity_id)
            elif alias in content:
                mentioned.add(entity_id)

        # Create pairs from co-mentions
        mentioned_list = list(mentioned)
        for i, e1 in enumerate(mentioned_list):
            for e2 in mentioned_list[i+1:]:
                pair = tuple(sorted([e1, e2]))
                co_mentions[pair] += 1

    # Create relationships for significant co-mentions
    relationships = []
    for (e1_id, e2_id), count in co_mentions.items():
        if count >= 3:  # At least 3 co-mentions
            rel = Relationship(
                source_entity_id=e1_id,
                target_entity_id=e2_id,
                type='mentioned_with',
                weight=min(count / 10.0, 1.0),  # Normalize weight
                evidence=[f"Co-mentioned {count} times"]
            )
            relationships.append(rel)

    return relationships


def extract_leadership_with_llm(client, conn) -> list[Relationship]:
    """Use LLM to extract leadership relationships from messages."""
    if not client:
        return []

    cursor = conn.cursor()

    # Find messages that mention leadership keywords
    cursor.execute('''
        SELECT pc.content, p.name
        FROM person_content pc
        JOIN person p ON pc.person_id = p.id
        WHERE pc.content LIKE '%lead%'
           OR pc.content LIKE '%coordinator%'
           OR pc.content LIKE '%in charge%'
           OR pc.content LIKE '%responsible for%'
           OR pc.content LIKE '%head of%'
        LIMIT 200
    ''')

    messages = [f"{row['name']}: {row['content']}" for row in cursor.fetchall()]

    if not messages:
        return []

    # Process in batches
    relationships = []
    batch_size = 20

    for i in range(0, len(messages), batch_size):
        batch = messages[i:i+batch_size]
        batch_text = "\n".join(batch)

        prompt = LEADERSHIP_EXTRACTION_PROMPT.format(messages=batch_text[:3000])

        try:
            response = client.messages.create(
                model=EXTRACTION_MODEL,
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}]
            )

            text = response.content[0].text.strip()

            # Extract JSON array
            json_match = re.search(r'\[.*\]', text, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())

                for item in data:
                    person_name = item.get('person', '')
                    role = item.get('role', '')
                    evidence = item.get('evidence', '')

                    person_entity = get_entity_by_alias(conn, person_name)
                    if not person_entity:
                        continue

                    role_slug = normalize_slug(role)
                    role_entity = get_entity_by_slug(conn, role_slug)

                    if role_entity:
                        rel = Relationship(
                            source_entity_id=person_entity.id,
                            target_entity_id=role_entity.id,
                            type='leads',
                            evidence=[evidence] if evidence else []
                        )
                        relationships.append(rel)

        except json.JSONDecodeError:
            pass
        except Exception as e:
            print(f"   LLM error: {e}")

        import time
        time.sleep(RATE_LIMIT_DELAY)

    return relationships


def main():
    parser = argparse.ArgumentParser(description='Extract relationships between entities')
    parser.add_argument('--skip-llm', action='store_true', help='Skip LLM-based extraction')
    args = parser.parse_args()

    print("=" * 60)
    print("Pass 3: Relationship Extraction")
    print("=" * 60)
    print(f"\n   Database: {DB_PATH}")

    # Initialize Claude client
    client = None
    if not args.skip_llm:
        if anthropic and ANTHROPIC_API_KEY:
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            print(f"   LLM: {EXTRACTION_MODEL}")
        else:
            print("   LLM: Skipped (no API key)")

    conn = get_connection(DB_PATH)
    cursor = conn.cursor()

    # Clear existing relationships
    print("\n1. Clearing existing relationships...")
    cursor.execute('DELETE FROM relationship')
    conn.commit()

    all_relationships = []

    # Extract from shifts
    print("\n2. Extracting shift relationships...")
    shift_rels = extract_shifts_relationships(conn)
    print(f"   Found {len(shift_rels)} shift relationships")
    all_relationships.extend(shift_rels)

    # Extract from roster
    print("\n3. Extracting roster relationships...")
    roster_rels = extract_roster_relationships(conn)
    print(f"   Found {len(roster_rels)} roster relationships")
    all_relationships.extend(roster_rels)

    # Extract co-mentions
    print("\n4. Extracting co-mention relationships...")
    comention_rels = extract_comentions(conn)
    print(f"   Found {len(comention_rels)} co-mention relationships")
    all_relationships.extend(comention_rels)

    # Extract leadership with LLM
    if client:
        print("\n5. Extracting leadership relationships (LLM)...")
        leadership_rels = extract_leadership_with_llm(client, conn)
        print(f"   Found {len(leadership_rels)} leadership relationships")
        all_relationships.extend(leadership_rels)

    # Insert all relationships
    print("\n6. Inserting relationships...")
    with transaction(conn):
        for rel in all_relationships:
            insert_relationship(conn, rel)

    # Summary
    cursor.execute('''
        SELECT type, COUNT(*) as count
        FROM relationship
        GROUP BY type
    ''')

    print("\n" + "=" * 60)
    print("Relationship Summary:")
    for row in cursor.fetchall():
        print(f"   {row['type']}: {row['count']}")

    cursor.execute('SELECT COUNT(*) FROM relationship')
    total = cursor.fetchone()[0]
    print(f"\n   Total relationships: {total}")
    print("=" * 60)

    conn.close()


if __name__ == '__main__':
    main()
