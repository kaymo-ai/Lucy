#!/usr/bin/env python3
"""
Pass 1: Entity Extraction

Extracts entities from all data sources:
- People from the person table (normalize names)
- Concepts, events, equipment, roles from camp_knowledge using LLM

Usage:
    python -m knowledge_graph.extract_entities [--skip-llm]
"""

import re
import sys
import json
import time
import argparse
from collections import defaultdict

try:
    import anthropic
except ImportError:
    anthropic = None

from .config import (
    DB_PATH, ANTHROPIC_API_KEY, EXTRACTION_MODEL,
    ENTITY_EXTRACTION_PROMPT, BATCH_SIZE, RATE_LIMIT_DELAY
)
from .db import (
    get_connection, migrate_schema, transaction,
    normalize_slug, insert_entity, insert_alias, get_entity_by_slug
)
from .models import Entity, EntityAlias, ExtractedEntities


def is_garbage_entry(name: str) -> bool:
    """Check if a name is garbage data (not a person)."""
    name_lower = name.lower().strip()

    # Skip empty or very short
    if len(name_lower) < 2:
        return True

    # Skip timestamps and dates
    if re.match(r'^\d{4}-\d{2}-\d{2}', name):  # YYYY-MM-DD
        return True
    if re.match(r'^\d{1,2}:\d{2}', name):  # HH:MM times
        return True
    if re.match(r'^\d+\.\d+$', name):  # Numbers like "115.75"
        return True
    if re.match(r'^\d+$', name):  # Pure numbers
        return True

    # Skip phone numbers (including Unicode variants)
    # Remove common Unicode phone number characters first
    clean_for_phone = re.sub(r'[\u202A\u202C\u200E\u200F‪‬\s\-\(\)]', '', name)
    if re.match(r'^[\+\d]+$', clean_for_phone) and len(re.findall(r'\d', clean_for_phone)) >= 7:
        return True

    # Skip account statements and business documents
    if 'account statement' in name_lower or '@preservationsociety' in name_lower:
        return True

    # Skip email addresses standing alone
    if re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', name.strip()):
        return True

    # Skip food/ingredient items
    food_keywords = [
        'chips', 'carrots', 'pickles', 'jerky', 'meat', 'cheese', 'sliced',
        'onion', 'tomato', 'vodka', 'gin', 'whiskey', 'tequila', 'rum',
        'beer', 'wine', 'juice', 'water', 'soda', 'coffee', 'tea',
        'bread', 'butter', 'eggs', 'milk', 'cream', 'salt', 'pepper',
        'lettuce', 'cucumber', 'celery', 'gazpacho', 'soup', 'oregano',
        'bbq', 'sauce', 'salsa', 'dip', 'nuts', 'crackers', 'fruit',
        'vegetable', 'hummus', 'guac', 'avocado', 'lime', 'lemon',
        'olive', 'mayo', 'mustard', 'ketchup', 'relish', 'ranch'
    ]
    if any(kw in name_lower for kw in food_keywords):
        return True

    # Skip entries with common food/instruction patterns
    if re.search(r'\((?:fresh|oz|similar|these|optional|pitted|dried|canned|frozen)\)', name_lower):
        return True

    # Skip instruction/action text
    junk_patterns = [
        'venmo', 'paypal', 'transfer funds', 'booking', 'payment',
        'happy birthday', 'congrat', 'thank you', 'welcome',
        'dates', 'dried', 'additional', 'please', 'reminder'
    ]
    if any(p in name_lower for p in junk_patterns):
        return True

    # Skip shift/role descriptions
    role_keywords = [
        'shift', 'roast', 'cooking', 'serving', 'pulling', 'parking',
        'clean up', 'set up', 'breakdown', 'monitor'
    ]
    if any(kw in name_lower for kw in role_keywords):
        return True

    # Skip day/time descriptions
    time_keywords = [
        'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday',
        'mon ', 'tue ', 'wed ', 'thu ', 'fri ', 'sat ', 'sun ',
        'morning', 'afternoon', 'evening', 'night', 'am', 'pm',
        'build weekend', 'welcome dinner'
    ]
    if any(kw in name_lower for kw in time_keywords):
        return True

    # Skip entries that look like numeric IDs
    if re.match(r'^"\d+', name):
        return True

    # Skip entries with oz (ounces) pattern
    if re.search(r'\(oz\)', name_lower) or name_lower.endswith(' oz'):
        return True

    return False


def normalize_person_name(name: str) -> str:
    """Normalize a person name for deduplication."""
    # Remove WhatsApp prefix
    name = re.sub(r'^~\s*', '', name)

    # Remove emojis
    name = re.sub(r'[\U0001F300-\U0001F9FF\U0001FA00-\U0001FAFF\U00002700-\U000027BF]', '', name)

    # Remove trailing numbers (e.g., "John 2")
    name = re.sub(r'\s+\d+$', '', name)

    # Clean up whitespace
    name = ' '.join(name.split())

    return name.strip()


def extract_nickname(name: str) -> tuple[str, str | None]:
    """Extract canonical name and nickname from formats like 'Piotr "Sunrise"'."""
    # Match patterns like: Name 'Nickname', Name "Nickname", Name (Nickname)
    match = re.match(r"^([^'\"(]+)['\"]([^'\"]+)['\"]$", name.strip())
    if match:
        return match.group(1).strip(), match.group(2).strip()

    match = re.match(r"^([^(]+)\(([^)]+)\)$", name.strip())
    if match:
        return match.group(1).strip(), match.group(2).strip()

    return name.strip(), None


def extract_people_from_db(conn) -> list[dict]:
    """Extract all people from the person table with context."""
    cursor = conn.cursor()
    cursor.execute('''
        SELECT
            p.id,
            p.name,
            p.email,
            p.phone,
            p.message_count,
            p.first_seen,
            p.last_seen
        FROM person p
        ORDER BY p.message_count DESC
    ''')

    people = []
    for row in cursor.fetchall():
        raw_name = row['name']
        normalized = normalize_person_name(raw_name)
        canonical, nickname = extract_nickname(normalized)

        people.append({
            'id': row['id'],
            'raw_name': raw_name,
            'canonical_name': canonical,
            'nickname': nickname,
            'email': row['email'],
            'phone': row['phone'],
            'message_count': row['message_count'] or 0,
            'first_seen': row['first_seen'],
            'last_seen': row['last_seen']
        })

    return people


def extract_entities_with_llm(client, content: str) -> ExtractedEntities:
    """Use Claude to extract entities from document content."""
    if not client:
        return ExtractedEntities()

    prompt = ENTITY_EXTRACTION_PROMPT.format(content=content[:4000])

    try:
        response = client.messages.create(
            model=EXTRACTION_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )

        text = response.content[0].text.strip()

        # Try to extract JSON from response
        # Handle cases where model adds explanation
        json_match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
        if json_match:
            text = json_match.group()

        data = json.loads(text)
        return ExtractedEntities.from_json(data)

    except json.JSONDecodeError as e:
        print(f"   JSON parse error: {e}")
        return ExtractedEntities()
    except Exception as e:
        print(f"   LLM error: {e}")
        return ExtractedEntities()


def create_person_entities(conn, people: list[dict]) -> int:
    """Create entity records for all people."""
    created = 0
    skipped_garbage = 0

    for person in people:
        canonical = person['canonical_name']
        if not canonical or len(canonical) < 2:
            continue

        # Skip garbage entries
        if is_garbage_entry(canonical) or is_garbage_entry(person['raw_name']):
            skipped_garbage += 1
            continue

        slug = normalize_slug(canonical)

        # Skip if entity already exists
        if get_entity_by_slug(conn, slug):
            continue

        # Create entity
        entity = Entity(
            type='person',
            canonical_name=canonical,
            slug=slug
        )
        entity_id = insert_entity(conn, entity)

        # Add canonical name as alias
        insert_alias(conn, EntityAlias(
            entity_id=entity_id,
            alias=canonical,
            source='person'
        ))

        # Add raw name if different
        if person['raw_name'] != canonical:
            insert_alias(conn, EntityAlias(
                entity_id=entity_id,
                alias=person['raw_name'],
                source='person'
            ))

        # Add nickname if present
        if person['nickname']:
            insert_alias(conn, EntityAlias(
                entity_id=entity_id,
                alias=person['nickname'],
                source='person'
            ))

        created += 1

    if skipped_garbage > 0:
        print(f"   Skipped {skipped_garbage} garbage entries (food, times, shifts)")
    return created


def create_concept_entities(conn, entity_type: str, names: list[str], source: str) -> int:
    """Create entity records for concepts/events/equipment/roles."""
    created = 0

    for name in names:
        name = name.strip()
        if not name or len(name) < 2:
            continue

        slug = normalize_slug(name)

        # Skip if entity already exists
        if get_entity_by_slug(conn, slug):
            continue

        # Create entity
        entity = Entity(
            type=entity_type,
            canonical_name=name,
            slug=slug
        )
        entity_id = insert_entity(conn, entity)

        # Add name as alias
        insert_alias(conn, EntityAlias(
            entity_id=entity_id,
            alias=name,
            source=source
        ))

        created += 1

    return created


def main():
    parser = argparse.ArgumentParser(description='Extract entities from knowledge base')
    parser.add_argument('--skip-llm', action='store_true', help='Skip LLM extraction (people only)')
    parser.add_argument('--limit', type=int, default=0, help='Limit documents for LLM extraction')
    args = parser.parse_args()

    print("=" * 60)
    print("Pass 1: Entity Extraction")
    print("=" * 60)
    print(f"\n   Database: {DB_PATH}")

    # Initialize Claude client
    client = None
    if not args.skip_llm:
        if anthropic and ANTHROPIC_API_KEY:
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            print(f"   LLM: {EXTRACTION_MODEL}")
        else:
            print("   LLM: Skipped (no API key or anthropic not installed)")
            print("   Install with: pip install anthropic")
            print("   Set ANTHROPIC_API_KEY environment variable")

    conn = get_connection(DB_PATH)

    # Run schema migration
    print("\n1. Migrating database schema...")
    migrate_schema(conn)

    # Extract people
    print("\n2. Extracting people from person table...")
    people = extract_people_from_db(conn)
    print(f"   Found {len(people)} people")

    with transaction(conn):
        created = create_person_entities(conn, people)
        print(f"   Created {created} person entities")

    # Extract concepts from camp_knowledge using LLM
    if client:
        print("\n3. Extracting concepts from camp_knowledge...")
        cursor = conn.cursor()

        query = 'SELECT id, title, content, category FROM camp_knowledge'
        if args.limit > 0:
            query += f' LIMIT {args.limit}'
        cursor.execute(query)

        docs = cursor.fetchall()
        print(f"   Processing {len(docs)} documents...")

        all_concepts = defaultdict(set)
        processed = 0

        for i, doc in enumerate(docs):
            # Combine title and content for extraction
            text = f"Title: {doc['title']}\n\n{doc['content']}"

            extracted = extract_entities_with_llm(client, text)

            for concept in extracted.concepts:
                all_concepts['concept'].add(concept)
            for event in extracted.events:
                all_concepts['event'].add(event)
            for equip in extracted.equipment:
                all_concepts['equipment'].add(equip)
            for role in extracted.roles:
                all_concepts['role'].add(role)

            processed += 1
            if processed % 50 == 0:
                print(f"   Processed {processed}/{len(docs)} documents...")

            # Rate limiting
            time.sleep(RATE_LIMIT_DELAY)

        # Create entities for extracted concepts
        print("\n4. Creating concept entities...")
        with transaction(conn):
            for entity_type, names in all_concepts.items():
                created = create_concept_entities(conn, entity_type, list(names), 'camp_knowledge')
                print(f"   Created {created} {entity_type} entities")

    # Summary
    cursor = conn.cursor()
    cursor.execute('SELECT type, COUNT(*) as count FROM entity GROUP BY type')
    print("\n" + "=" * 60)
    print("Entity Summary:")
    for row in cursor.fetchall():
        print(f"   {row['type']}: {row['count']}")

    cursor.execute('SELECT COUNT(*) FROM entity')
    total = cursor.fetchone()[0]
    print(f"\n   Total entities: {total}")
    print("=" * 60)

    conn.close()


if __name__ == '__main__':
    main()
