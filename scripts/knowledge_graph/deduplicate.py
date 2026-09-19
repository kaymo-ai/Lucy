#!/usr/bin/env python3
"""
Pass 2: Entity Deduplication

Merges duplicate person entities using:
1. Rule-based pre-filtering (normalize names, match emails)
2. LLM verification for ambiguous pairs

Expected outcome: ~1,142 people -> ~400-500 canonical people

Usage:
    python -m knowledge_graph.deduplicate [--skip-llm] [--dry-run]
"""

import re
import sys
import argparse
from collections import defaultdict
from difflib import SequenceMatcher

try:
    import anthropic
except ImportError:
    anthropic = None

from .config import (
    DB_PATH, ANTHROPIC_API_KEY, EXTRACTION_MODEL,
    DEDUP_VERIFICATION_PROMPT, RATE_LIMIT_DELAY
)
from .db import (
    get_connection, transaction, get_aliases_for_entity,
    get_person_messages
)


def similarity_ratio(a: str, b: str) -> float:
    """Calculate string similarity ratio."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def normalize_for_comparison(name: str) -> str:
    """Aggressively normalize name for comparison."""
    name = name.lower()

    # Remove common prefixes/suffixes
    name = re.sub(r'^~\s*', '', name)
    name = re.sub(r'^dr\.?\s+', '', name)
    name = re.sub(r'^mr\.?\s+', '', name)
    name = re.sub(r'^ms\.?\s+', '', name)

    # Remove emojis
    name = re.sub(r'[\U0001F300-\U0001F9FF\U0001FA00-\U0001FAFF]', '', name)

    # Remove parenthetical content (nicknames)
    name = re.sub(r'\s*[\(\[].*?[\)\]]', '', name)

    # Remove quotes and nicknames
    name = re.sub(r"['\"].*?['\"]", '', name)

    # Remove numbers
    name = re.sub(r'\d+', '', name)

    # Clean whitespace
    name = ' '.join(name.split())

    return name.strip()


def extract_first_name(name: str) -> str:
    """Extract first name from full name."""
    normalized = normalize_for_comparison(name)
    parts = normalized.split()
    return parts[0] if parts else ""


def has_last_name(name: str) -> bool:
    """Check if name has more than one part (has last name)."""
    normalized = normalize_for_comparison(name)
    parts = normalized.split()
    return len(parts) >= 2


def get_last_name(name: str) -> str:
    """Extract last name from full name."""
    normalized = normalize_for_comparison(name)
    parts = normalized.split()
    return parts[-1] if len(parts) >= 2 else ""


def are_likely_same_person(name1: str, name2: str) -> tuple[bool, str]:
    """
    Determine if two names likely refer to the same person.
    Returns (is_same, reason).

    CONSERVATIVE approach - only merge when confident:
    1. Exact match after normalization
    2. Very high similarity (>95%, likely typos)
    3. Same full name with different formatting
    4. First + Last initial match (e.g., "John S" = "John Smith")

    DO NOT merge based on first name alone - too many false positives.
    """
    norm1 = normalize_for_comparison(name1)
    norm2 = normalize_for_comparison(name2)

    # Skip very short names (likely garbage)
    if len(norm1) < 2 or len(norm2) < 2:
        return False, "too_short"

    # Exact match after normalization
    if norm1 == norm2:
        return True, "exact_normalized"

    # Very high similarity (>95%, likely typos only)
    sim = similarity_ratio(norm1, norm2)
    if sim > 0.95:
        return True, f"high_similarity_{sim:.2f}"

    # Check for first + last initial match (e.g., "John S" = "John Smith")
    first1 = extract_first_name(name1)
    first2 = extract_first_name(name2)
    last1 = get_last_name(name1)
    last2 = get_last_name(name2)

    if first1 and first2 and first1 == first2:
        # Both have last names - they must match
        if last1 and last2:
            if last1 == last2:
                return True, "full_name_match"
            # Last initial match (e.g., "Smith" starts with "S")
            if len(last1) == 1 and last2.startswith(last1):
                return True, "first_last_initial_match"
            if len(last2) == 1 and last1.startswith(last2):
                return True, "first_last_initial_match"
            # Different last names - definitely different people
            return False, "different_last_names"

        # One has last name, one doesn't
        # Only merge if the one without is a strict prefix
        if has_last_name(name1) and not has_last_name(name2):
            # name2 is just first name - needs verification
            return None, "needs_verification"
        if has_last_name(name2) and not has_last_name(name1):
            return None, "needs_verification"

        # Neither has last name, both just first names
        # Same single first name is NOT enough - could be different people
        return None, "needs_verification"

    # First names don't match - check if one contains the other
    # e.g., "~ Viviane" vs "Viviane Sloane"
    if norm1 in norm2 or norm2 in norm1:
        longer = norm1 if len(norm1) > len(norm2) else norm2
        # Only if the longer one looks like it has the same person
        if sim > 0.7:
            return None, "needs_verification"

    return False, "no_match"


def find_duplicate_candidates(conn) -> list[tuple[dict, dict]]:
    """Find pairs of entities that might be duplicates."""
    cursor = conn.cursor()

    # Get all person entities with their aliases
    cursor.execute('''
        SELECT e.id, e.canonical_name, e.slug
        FROM entity e
        WHERE e.type = 'person'
    ''')
    entities = [dict(row) for row in cursor.fetchall()]

    # Add aliases to each entity
    for entity in entities:
        entity['aliases'] = get_aliases_for_entity(conn, entity['id'])

    # Find potential duplicates
    candidates = []
    seen_pairs = set()

    for i, e1 in enumerate(entities):
        for e2 in entities[i+1:]:
            # Skip if already processed
            pair_key = tuple(sorted([e1['id'], e2['id']]))
            if pair_key in seen_pairs:
                continue

            # Check all alias combinations
            for alias1 in e1['aliases']:
                for alias2 in e2['aliases']:
                    is_same, reason = are_likely_same_person(alias1, alias2)

                    if is_same is True:
                        candidates.append((e1, e2, reason, "auto"))
                        seen_pairs.add(pair_key)
                        break
                    elif is_same is None:  # Needs verification
                        candidates.append((e1, e2, reason, "verify"))
                        seen_pairs.add(pair_key)
                        break
                else:
                    continue
                break

    return candidates


def verify_with_llm(client, conn, e1: dict, e2: dict) -> bool:
    """Use LLM to verify if two entities are the same person."""
    if not client:
        return False

    # Get context for each entity
    # Find the person_id for each entity by matching names
    cursor = conn.cursor()

    def get_context(entity):
        # Try to find person by name
        cursor.execute('''
            SELECT id FROM person WHERE name = ?
        ''', (entity['canonical_name'],))
        row = cursor.fetchone()

        if row:
            messages = get_person_messages(conn, row['id'], limit=5)
            return " | ".join(messages[:3]) if messages else "No messages found"
        return "No context available"

    context1 = get_context(e1)
    context2 = get_context(e2)

    prompt = DEDUP_VERIFICATION_PROMPT.format(
        name1=e1['canonical_name'],
        context1=context1[:500],
        name2=e2['canonical_name'],
        context2=context2[:500]
    )

    try:
        response = client.messages.create(
            model=EXTRACTION_MODEL,
            max_tokens=10,
            messages=[{"role": "user", "content": prompt}]
        )

        answer = response.content[0].text.strip().lower()
        return answer.startswith('yes')

    except Exception as e:
        print(f"   LLM error: {e}")
        return False


def merge_entities(conn, primary_id: int, secondary_id: int, dry_run: bool = False) -> None:
    """Merge secondary entity into primary entity."""
    cursor = conn.cursor()

    if dry_run:
        print(f"   Would merge entity {secondary_id} into {primary_id}")
        return

    # Move aliases from secondary to primary
    cursor.execute('''
        UPDATE entity_alias
        SET entity_id = ?
        WHERE entity_id = ?
    ''', (primary_id, secondary_id))

    # Update relationships
    cursor.execute('''
        UPDATE relationship
        SET source_entity_id = ?
        WHERE source_entity_id = ?
    ''', (primary_id, secondary_id))

    cursor.execute('''
        UPDATE relationship
        SET target_entity_id = ?
        WHERE target_entity_id = ?
    ''', (primary_id, secondary_id))

    # Delete secondary entity
    cursor.execute('DELETE FROM entity WHERE id = ?', (secondary_id,))


def main():
    parser = argparse.ArgumentParser(description='Deduplicate person entities')
    parser.add_argument('--skip-llm', action='store_true', help='Skip LLM verification')
    parser.add_argument('--dry-run', action='store_true', help='Show what would be merged')
    parser.add_argument('--limit', type=int, default=0, help='Limit LLM verifications')
    args = parser.parse_args()

    print("=" * 60)
    print("Pass 2: Entity Deduplication")
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

    # Get initial count
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM entity WHERE type = 'person'")
    initial_count = cursor.fetchone()[0]
    print(f"\n   Initial person entities: {initial_count}")

    # Find duplicate candidates
    print("\n1. Finding duplicate candidates...")
    candidates = find_duplicate_candidates(conn)

    auto_merge = [(e1, e2) for e1, e2, reason, method in candidates if method == "auto"]
    needs_verify = [(e1, e2) for e1, e2, reason, method in candidates if method == "verify"]

    print(f"   Auto-merge candidates: {len(auto_merge)}")
    print(f"   Needs verification: {len(needs_verify)}")

    # Perform auto-merges
    print("\n2. Performing automatic merges...")
    merged_count = 0

    with transaction(conn):
        for e1, e2 in auto_merge:
            # Keep entity with more aliases or longer name
            aliases1 = len(e1.get('aliases', []))
            aliases2 = len(e2.get('aliases', []))

            if aliases1 >= aliases2:
                primary, secondary = e1, e2
            else:
                primary, secondary = e2, e1

            if not args.dry_run:
                merge_entities(conn, primary['id'], secondary['id'])
            else:
                print(f"   Would merge '{secondary['canonical_name']}' -> '{primary['canonical_name']}'")

            merged_count += 1

    print(f"   Merged {merged_count} entities")

    # LLM verification for ambiguous pairs
    if client and needs_verify:
        print(f"\n3. LLM verification for {len(needs_verify)} pairs...")

        verify_limit = args.limit if args.limit > 0 else len(needs_verify)
        verified_merges = 0

        with transaction(conn):
            for i, (e1, e2) in enumerate(needs_verify[:verify_limit]):
                is_same = verify_with_llm(client, conn, e1, e2)

                if is_same:
                    if not args.dry_run:
                        merge_entities(conn, e1['id'], e2['id'])
                    else:
                        print(f"   Would merge '{e2['canonical_name']}' -> '{e1['canonical_name']}'")
                    verified_merges += 1

                if (i + 1) % 10 == 0:
                    print(f"   Verified {i + 1}/{verify_limit}...")

                import time
                time.sleep(RATE_LIMIT_DELAY)

        print(f"   Merged {verified_merges} verified entities")
        merged_count += verified_merges

    # Final count
    cursor.execute("SELECT COUNT(*) FROM entity WHERE type = 'person'")
    final_count = cursor.fetchone()[0]

    print("\n" + "=" * 60)
    print("Deduplication Summary:")
    print(f"   Initial entities: {initial_count}")
    print(f"   Merged: {merged_count}")
    print(f"   Final entities: {final_count}")
    print(f"   Reduction: {((initial_count - final_count) / initial_count * 100):.1f}%")
    print("=" * 60)

    conn.close()


if __name__ == '__main__':
    main()
