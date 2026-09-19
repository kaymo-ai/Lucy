#!/usr/bin/env python3
"""
Pass 4: Profile Generation

Generates LLM-powered profiles for:
- People: summary, expertise, years_active, key_contributions
- Concepts: definition, category, related_terms

Uses Sonnet for higher quality profiles.

Usage:
    python -m knowledge_graph.generate_profiles [--people-only] [--concepts-only] [--limit N]
"""

import re
import json
import argparse
import time

try:
    import anthropic
except ImportError:
    anthropic = None

from .config import (
    DB_PATH, ANTHROPIC_API_KEY, PROFILE_MODEL,
    PERSON_PROFILE_PROMPT, CONCEPT_DEFINITION_PROMPT,
    RATE_LIMIT_DELAY
)
from .db import (
    get_connection, transaction,
    get_person_messages, get_person_shifts, get_person_roster,
    insert_profile, insert_concept_definition
)
from .models import EntityProfile, ConceptDefinition


def generate_person_profile(client, conn, entity_id: int, canonical_name: str) -> EntityProfile | None:
    """Generate a profile for a person entity."""
    if not client:
        return None

    # Find matching person in person table
    cursor = conn.cursor()
    cursor.execute('SELECT id, email, phone FROM person WHERE name = ?', (canonical_name,))
    person_row = cursor.fetchone()

    if not person_row:
        # Try partial match
        cursor.execute('SELECT id, email, phone FROM person WHERE name LIKE ?', (f'%{canonical_name}%',))
        person_row = cursor.fetchone()

    if not person_row:
        return None

    person_id = person_row['id']
    email = person_row['email']
    phone = person_row['phone']

    # Gather context
    messages = get_person_messages(conn, person_id, limit=20)
    shifts = get_person_shifts(conn, person_id)
    roster = get_person_roster(conn, person_id)

    if not messages and not shifts and not roster:
        return None

    # Format context
    messages_text = "\n".join(f"- {m[:200]}" for m in messages[:15]) if messages else "No messages"

    shifts_text = "\n".join(
        f"- {s['year']}: {s['role']} ({s['day']} {s['time_slot']})"
        for s in shifts[:10]
    ) if shifts else "No shift records"

    roster_text = "\n".join(
        f"- {r['year']}: {r['status'] or 'member'}, {r['home_city'] or 'unknown city'}"
        for r in roster
    ) if roster else "No roster records"

    prompt = PERSON_PROFILE_PROMPT.format(
        name=canonical_name,
        messages=messages_text,
        shifts=shifts_text,
        roster=roster_text
    )

    try:
        response = client.messages.create(
            model=PROFILE_MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}]
        )

        text = response.content[0].text.strip()

        # Extract JSON
        json_match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())

            return EntityProfile(
                entity_id=entity_id,
                summary=data.get('summary', ''),
                expertise=data.get('expertise', []),
                years_active=data.get('years_active', []),
                key_contributions=data.get('key_contributions', ''),
                contact_info={'email': email, 'phone': phone} if email or phone else {}
            )

    except json.JSONDecodeError as e:
        print(f"   JSON error for {canonical_name}: {e}")
    except Exception as e:
        print(f"   Error for {canonical_name}: {e}")

    return None


def generate_concept_definition(client, conn, entity_id: int, concept_name: str) -> ConceptDefinition | None:
    """Generate a definition for a concept entity."""
    if not client:
        return None

    cursor = conn.cursor()

    # Find example usages in camp_knowledge and messages
    examples = []

    # Search in camp_knowledge
    cursor.execute('''
        SELECT content FROM camp_knowledge
        WHERE content LIKE ?
        LIMIT 5
    ''', (f'%{concept_name}%',))

    for row in cursor.fetchall():
        content = row['content']
        # Extract sentence containing the term
        sentences = re.split(r'[.!?]', content)
        for sent in sentences:
            if concept_name.lower() in sent.lower():
                sent = sent.strip()
                if 20 < len(sent) < 200:
                    examples.append(sent)
                    break

    # Search in messages
    cursor.execute('''
        SELECT content FROM person_content
        WHERE content LIKE ?
        LIMIT 10
    ''', (f'%{concept_name}%',))

    for row in cursor.fetchall():
        content = row['content']
        if 20 < len(content) < 200:
            examples.append(content)

    if not examples:
        return None

    examples_text = "\n".join(f"- {ex[:150]}" for ex in examples[:5])

    prompt = CONCEPT_DEFINITION_PROMPT.format(
        term=concept_name,
        examples=examples_text
    )

    try:
        response = client.messages.create(
            model=PROFILE_MODEL,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}]
        )

        text = response.content[0].text.strip()

        json_match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())

            return ConceptDefinition(
                entity_id=entity_id,
                definition=data.get('definition', ''),
                examples=examples[:3],
                related_concepts=data.get('related_terms', []),
                category=data.get('category', '')
            )

    except json.JSONDecodeError as e:
        print(f"   JSON error for {concept_name}: {e}")
    except Exception as e:
        print(f"   Error for {concept_name}: {e}")

    return None


def main():
    parser = argparse.ArgumentParser(description='Generate profiles and definitions')
    parser.add_argument('--people-only', action='store_true', help='Only generate person profiles')
    parser.add_argument('--concepts-only', action='store_true', help='Only generate concept definitions')
    parser.add_argument('--limit', type=int, default=0, help='Limit number of profiles to generate')
    parser.add_argument('--min-messages', type=int, default=5, help='Minimum messages for person profile')
    args = parser.parse_args()

    print("=" * 60)
    print("Pass 4: Profile Generation")
    print("=" * 60)
    print(f"\n   Database: {DB_PATH}")

    # Initialize Claude client
    if not anthropic or not ANTHROPIC_API_KEY:
        print("   Error: anthropic library or API key not available")
        print("   Install with: pip install anthropic")
        print("   Set ANTHROPIC_API_KEY environment variable")
        return

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    print(f"   LLM: {PROFILE_MODEL}")

    conn = get_connection(DB_PATH)
    cursor = conn.cursor()

    # Generate person profiles
    if not args.concepts_only:
        print("\n1. Generating person profiles...")

        # Get people entities that need profiles
        cursor.execute('''
            SELECT e.id, e.canonical_name
            FROM entity e
            LEFT JOIN entity_profile ep ON e.id = ep.entity_id
            WHERE e.type = 'person' AND ep.id IS NULL
            ORDER BY e.canonical_name
        ''')
        people = [dict(row) for row in cursor.fetchall()]

        # Filter by message count
        filtered_people = []
        for person in people:
            cursor.execute('''
                SELECT message_count FROM person WHERE name = ?
            ''', (person['canonical_name'],))
            row = cursor.fetchone()
            if row and (row['message_count'] or 0) >= args.min_messages:
                filtered_people.append(person)

        if args.limit > 0:
            filtered_people = filtered_people[:args.limit]

        print(f"   Processing {len(filtered_people)} people (min {args.min_messages} messages)...")

        profiles_created = 0
        for i, person in enumerate(filtered_people):
            profile = generate_person_profile(
                client, conn,
                person['id'],
                person['canonical_name']
            )

            if profile:
                with transaction(conn):
                    insert_profile(conn, profile)
                profiles_created += 1

            if (i + 1) % 10 == 0:
                print(f"   Processed {i + 1}/{len(filtered_people)}... ({profiles_created} profiles created)")

            time.sleep(RATE_LIMIT_DELAY)

        print(f"   Created {profiles_created} person profiles")

    # Generate concept definitions
    if not args.people_only:
        print("\n2. Generating concept definitions...")

        cursor.execute('''
            SELECT e.id, e.canonical_name, e.type
            FROM entity e
            LEFT JOIN concept_definition cd ON e.id = cd.entity_id
            WHERE e.type IN ('concept', 'event', 'equipment', 'role')
            AND cd.id IS NULL
            ORDER BY e.type, e.canonical_name
        ''')
        concepts = [dict(row) for row in cursor.fetchall()]

        if args.limit > 0:
            concepts = concepts[:args.limit]

        print(f"   Processing {len(concepts)} concepts...")

        definitions_created = 0
        for i, concept in enumerate(concepts):
            defn = generate_concept_definition(
                client, conn,
                concept['id'],
                concept['canonical_name']
            )

            if defn:
                with transaction(conn):
                    insert_concept_definition(conn, defn)
                definitions_created += 1

            if (i + 1) % 10 == 0:
                print(f"   Processed {i + 1}/{len(concepts)}... ({definitions_created} definitions created)")

            time.sleep(RATE_LIMIT_DELAY)

        print(f"   Created {definitions_created} concept definitions")

    # Summary
    cursor.execute('SELECT COUNT(*) FROM entity_profile')
    profile_count = cursor.fetchone()[0]

    cursor.execute('SELECT COUNT(*) FROM concept_definition')
    definition_count = cursor.fetchone()[0]

    print("\n" + "=" * 60)
    print("Profile Generation Summary:")
    print(f"   Person profiles: {profile_count}")
    print(f"   Concept definitions: {definition_count}")
    print("=" * 60)

    conn.close()


if __name__ == '__main__':
    main()
