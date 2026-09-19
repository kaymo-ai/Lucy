#!/usr/bin/env python3
"""
Detect ambiguous terms and camp-specific jargon that Lucy doesn't understand.
Uses heuristics and optionally Claude to identify terms that need explanation.
"""

import sqlite3
import json
import os
import re
from pathlib import Path
from collections import Counter

# Try to import Anthropic, but make it optional
try:
    from anthropic import Anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

DB_PATH = Path(__file__).parent.parent / "output" / "ps_knowledge.db"

# Common words to exclude from detection
COMMON_WORDS = {
    'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'must', 'shall', 'can', 'need', 'dare',
    'ought', 'used', 'to', 'of', 'in', 'for', 'on', 'with', 'at', 'by',
    'from', 'up', 'about', 'into', 'over', 'after', 'beneath', 'under',
    'above', 'and', 'but', 'or', 'nor', 'so', 'yet', 'both', 'either',
    'neither', 'not', 'only', 'own', 'same', 'than', 'too', 'very',
    'just', 'also', 'now', 'here', 'there', 'when', 'where', 'why', 'how',
    'all', 'each', 'every', 'both', 'few', 'more', 'most', 'other', 'some',
    'such', 'no', 'any', 'this', 'that', 'these', 'those', 'what', 'which',
    'who', 'whom', 'whose', 'i', 'you', 'he', 'she', 'it', 'we', 'they',
    'me', 'him', 'her', 'us', 'them', 'my', 'your', 'his', 'its', 'our',
    'their', 'mine', 'yours', 'hers', 'ours', 'theirs', 'if', 'then',
    'else', 'as', 'because', 'while', 'although', 'though', 'unless',
    'until', 'before', 'after', 'since', 'during', 'going', 'get', 'got',
    'make', 'made', 'take', 'took', 'come', 'came', 'go', 'went', 'see',
    'saw', 'know', 'knew', 'think', 'thought', 'want', 'wanted', 'look',
    'looked', 'use', 'find', 'found', 'give', 'gave', 'tell', 'told',
    'work', 'worked', 'call', 'called', 'try', 'tried', 'ask', 'asked',
    'put', 'keep', 'kept', 'let', 'begin', 'began', 'seem', 'seemed',
    'help', 'helped', 'show', 'showed', 'hear', 'heard', 'play', 'played',
    'run', 'ran', 'move', 'moved', 'live', 'lived', 'believe', 'believed',
    'bring', 'brought', 'happen', 'happened', 'write', 'wrote', 'provide',
    'sit', 'sat', 'stand', 'stood', 'lose', 'lost', 'pay', 'paid', 'meet',
    'met', 'include', 'included', 'continue', 'continued', 'set', 'learn',
    'learned', 'change', 'changed', 'lead', 'led', 'understand', 'understood',
    'watch', 'watched', 'follow', 'followed', 'stop', 'stopped', 'create',
    'speak', 'spoke', 'read', 'allow', 'allowed', 'add', 'added', 'spend',
    'spent', 'grow', 'grew', 'open', 'opened', 'walk', 'walked', 'win', 'won',
    'offer', 'offered', 'remember', 'remembered', 'love', 'loved', 'consider',
    'appear', 'appeared', 'buy', 'bought', 'wait', 'waited', 'serve', 'served',
    'die', 'died', 'send', 'sent', 'expect', 'expected', 'build', 'built',
    'stay', 'stayed', 'fall', 'fell', 'cut', 'reach', 'reached', 'kill',
    'killed', 'remain', 'remained', 'suggest', 'suggested', 'raise', 'raised',
    'pass', 'passed', 'sell', 'sold', 'require', 'required', 'report',
    'decide', 'decided', 'pull', 'pulled', 'yeah', 'yes', 'no', 'ok', 'okay',
    'thanks', 'thank', 'please', 'sorry', 'hi', 'hello', 'hey', 'bye',
    'am', 'pm', 'like', 'really', 'well', 'right', 'back', 'still', 'even',
    'way', 'much', 'thing', 'things', 'people', 'time', 'day', 'days',
    'year', 'years', 'week', 'weeks', 'today', 'tomorrow', 'yesterday',
    'morning', 'afternoon', 'evening', 'night', 'monday', 'tuesday',
    'wednesday', 'thursday', 'friday', 'saturday', 'sunday', 'camp',
    'burning', 'man', 'playa', 'burn', 'burner', 'shift', 'kitchen',
    'bar', 'food', 'water', 'drink', 'drinks', 'stuff', 'awesome', 'great',
    'good', 'bad', 'nice', 'cool', 'fun', 'sure', 'maybe', 'probably',
    'definitely', 'actually', 'basically', 'literally', 'totally', 'done',
    'need', 'needs', 'needed', 'new', 'first', 'last', 'next', 'long',
    'little', 'big', 'small', 'large', 'high', 'low', 'old', 'young',
    'early', 'late', 'hard', 'easy', 'best', 'worst', 'better', 'worse'
}

def create_questions_table(conn: sqlite3.Connection):
    """Create the concept_question table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS concept_question (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            term TEXT NOT NULL,
            term_type TEXT,              -- 'object', 'acronym', 'slang', 'event', 'location', 'role'
            example_context TEXT,
            source TEXT,
            status TEXT DEFAULT 'pending',  -- pending, answered, dismissed
            user_explanation TEXT,
            entity_id INTEGER,           -- links to entity table once answered
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(term)
        )
    """)
    conn.commit()
    print("✅ Created concept_question table")


def get_existing_concepts(conn: sqlite3.Connection) -> set:
    """Get terms we already have definitions for."""
    cursor = conn.execute("""
        SELECT LOWER(canonical_name) FROM entity WHERE type = 'concept'
        UNION
        SELECT LOWER(alias) FROM entity_alias ea
        JOIN entity e ON ea.entity_id = e.id
        WHERE e.type = 'concept'
    """)
    return {row[0] for row in cursor.fetchall()}


def get_known_people(conn: sqlite3.Connection) -> set:
    """Get names of known people to exclude."""
    cursor = conn.execute("""
        SELECT LOWER(canonical_name) FROM entity WHERE type = 'person'
        UNION
        SELECT LOWER(alias) FROM entity_alias ea
        JOIN entity e ON ea.entity_id = e.id
        WHERE e.type = 'person'
    """)
    return {row[0] for row in cursor.fetchall()}


def get_pending_questions(conn: sqlite3.Connection) -> set:
    """Get terms we've already queued for questions."""
    cursor = conn.execute("SELECT LOWER(term) FROM concept_question")
    return {row[0] for row in cursor.fetchall()}


def extract_sample_content(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    """Get sample content from various sources for analysis."""
    samples = []

    # Get chat messages from person_content
    cursor = conn.execute("""
        SELECT content, 'chat' as source
        FROM person_content
        WHERE content IS NOT NULL AND LENGTH(content) > 50
        ORDER BY RANDOM()
        LIMIT ?
    """, (limit // 2,))
    samples.extend([{"content": row[0], "source": row[1]} for row in cursor.fetchall()])

    # Get knowledge docs
    cursor = conn.execute("""
        SELECT content, source_file as source
        FROM camp_knowledge
        WHERE content IS NOT NULL AND LENGTH(content) > 50
        ORDER BY RANDOM()
        LIMIT ?
    """, (limit // 2,))
    samples.extend([{"content": row[0], "source": row[1] or "docs"} for row in cursor.fetchall()])

    return samples


def detect_unknowns_heuristic(samples: list[dict], known_concepts: set, known_people: set) -> list[dict]:
    """Use heuristics to detect potential camp-specific terms."""

    # Find capitalized words/phrases that appear multiple times
    term_counter = Counter()
    term_contexts = {}

    for sample in samples:
        content = sample['content']

        # Find capitalized words that aren't at sentence start
        # Pattern: space + Capitalized word (not common)
        words = re.findall(r'(?:^|\s)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)', content)
        for word in words:
            word_clean = word.strip()
            word_lower = word_clean.lower()

            # Skip if common word, known person, or known concept
            if word_lower in COMMON_WORDS:
                continue
            if word_lower in known_people:
                continue
            if word_lower in known_concepts:
                continue
            if len(word_clean) < 3:
                continue

            term_counter[word_clean] += 1
            if word_clean not in term_contexts:
                # Extract context around the term
                idx = content.find(word_clean)
                if idx >= 0:
                    start = max(0, idx - 30)
                    end = min(len(content), idx + len(word_clean) + 50)
                    term_contexts[word_clean] = content[start:end].strip()

        # Find potential acronyms (2-5 uppercase letters)
        acronyms = re.findall(r'\b([A-Z]{2,5})\b', content)
        for acr in acronyms:
            if acr.lower() in COMMON_WORDS:
                continue
            if acr.lower() in known_concepts:
                continue
            # Skip common acronyms
            if acr in {'PS', 'DJ', 'BBQ', 'RV', 'AC', 'DC', 'AM', 'PM', 'USA', 'UK', 'SF', 'LA', 'NY', 'BM'}:
                continue
            term_counter[acr] += 1
            if acr not in term_contexts:
                idx = content.find(acr)
                if idx >= 0:
                    start = max(0, idx - 30)
                    end = min(len(content), idx + len(acr) + 50)
                    term_contexts[acr] = content[start:end].strip()

    # Filter to terms that appear multiple times (likely camp jargon)
    results = []
    for term, count in term_counter.most_common(50):
        if count >= 2:  # Appears at least twice
            # Determine type based on patterns
            term_type = "concept"
            if term.isupper():
                term_type = "acronym"
            elif re.match(r'^[A-Z][a-z]+$', term):
                # Single capitalized word - could be object name
                term_type = "object"

            results.append({
                "term": term,
                "type": term_type,
                "example": term_contexts.get(term, ""),
                "reason": f"Appears {count} times in camp communications"
            })

    return results


def detect_unknowns_llm(samples: list[dict], known_concepts: set, known_people: set) -> list[dict]:
    """Use Claude to detect ambiguous terms (requires API key)."""

    if not HAS_ANTHROPIC:
        print("⚠️ Anthropic SDK not installed, skipping LLM detection")
        return []

    try:
        client = Anthropic()
    except Exception as e:
        print(f"⚠️ Could not initialize Anthropic client: {e}")
        return []

    # Combine samples into a single text block
    content_block = "\n\n---\n\n".join([
        f"[Source: {s['source']}]\n{s['content'][:500]}"
        for s in samples[:20]
    ])

    prompt = f"""Analyze this content from a Burning Man theme camp called "Preservation Society" (PS).

Identify terms, names, or references that would be confusing to an outsider - things that seem like insider knowledge or camp-specific jargon.

Look for:
1. OBJECTS/EQUIPMENT: Names that refer to camp infrastructure (vehicles, structures, equipment) - e.g., if "Boris" is mentioned like an object not a person
2. ACRONYMS: Unexplained abbreviations (not common ones like "DJ" or "BBQ")
3. CAMP SLANG: Unusual terms or phrases with special meaning
4. EVENTS: Named events or traditions that aren't self-explanatory
5. LOCATIONS: Camp-specific places or zones
6. ROLES: Camp job titles or positions that need explanation

EXCLUDE:
- Common English words
- Obvious person names used as people
- Standard Burning Man terms (MOOP, playa, etc. - we know those)
- Generic terms like "camp", "shift", "kitchen"

Content to analyze:
{content_block}

Return a JSON array of objects:
[{{"term": "Boris", "type": "object", "example": "We need to load Boris", "reason": "Named object/vehicle"}}]

Only include terms you're confident are camp-specific. Return [] if none found.
Return ONLY the JSON array."""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )

        text = response.content[0].text.strip()
        if text.startswith("```"):
            text = re.sub(r"```json?\n?", "", text)
            text = re.sub(r"\n?```$", "", text)

        results = json.loads(text)

        # Filter out known terms
        filtered = []
        for item in results:
            term_lower = item["term"].lower()
            if term_lower not in known_concepts and term_lower not in known_people:
                filtered.append(item)

        return filtered
    except Exception as e:
        print(f"⚠️ LLM detection error: {e}")
        return []


def save_questions(conn: sqlite3.Connection, questions: list[dict], source: str):
    """Save detected questions to database."""
    for q in questions:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO concept_question (term, term_type, example_context, source)
                VALUES (?, ?, ?, ?)
            """, (q["term"], q.get("type"), q.get("example"), source))
        except sqlite3.Error as e:
            print(f"⚠️ Error saving {q['term']}: {e}")
    conn.commit()


def run_detection(use_llm: bool = False, num_samples: int = 200):
    """Run the full detection pipeline."""
    print("🔍 Detecting unknown terms for 'Help Lucy Understand'")
    print("=" * 60)

    conn = sqlite3.connect(DB_PATH)

    # Setup
    create_questions_table(conn)
    known_concepts = get_existing_concepts(conn)
    known_people = get_known_people(conn)
    existing_questions = get_pending_questions(conn)

    print(f"📚 Known concepts: {len(known_concepts)}")
    print(f"👤 Known people: {len(known_people)}")
    print(f"❓ Existing questions: {len(existing_questions)}")

    # Combine all known terms
    all_known = known_concepts | known_people | existing_questions

    print(f"\n📦 Loading {num_samples} samples...")
    samples = extract_sample_content(conn, num_samples)
    print(f"   Loaded {len(samples)} samples")

    # Detect unknowns using heuristics
    print("\n🔎 Detecting terms using heuristics...")
    questions = detect_unknowns_heuristic(samples, all_known, known_people)

    # Optionally use LLM for additional detection
    if use_llm and HAS_ANTHROPIC:
        print("\n🤖 Running LLM-based detection...")
        llm_questions = detect_unknowns_llm(samples[:50], all_known, known_people)
        # Merge, avoiding duplicates
        seen_terms = {q["term"].lower() for q in questions}
        for q in llm_questions:
            if q["term"].lower() not in seen_terms:
                questions.append(q)
                seen_terms.add(q["term"].lower())

    if questions:
        print(f"\n📋 Found {len(questions)} potential terms:")
        for q in questions[:20]:  # Show first 20
            print(f"   • {q['term']} ({q.get('type', '?')})")

        if len(questions) > 20:
            print(f"   ... and {len(questions) - 20} more")

        # Save to database
        save_questions(conn, questions, "heuristic")
        print(f"\n💾 Saved {len(questions)} questions to database")
    else:
        print("\n   No new terms found")

    conn.close()

    print("\n" + "=" * 60)
    print(f"✅ Detection complete! Found {len(questions)} terms for Lucy to learn")
    print(f"📱 Run the iOS app and go to 'Help Lucy' tab to teach her!")


def show_pending_questions():
    """Display all pending questions."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.execute("""
        SELECT term, term_type, example_context, source
        FROM concept_question
        WHERE status = 'pending'
        ORDER BY term
    """)

    questions = cursor.fetchall()
    conn.close()

    if not questions:
        print("No pending questions!")
        return

    print(f"\n📋 {len(questions)} terms Lucy needs help understanding:\n")
    for term, term_type, example, source in questions:
        print(f"• {term} ({term_type or 'unknown'})")
        if example:
            print(f"  Example: \"{example[:60]}...\"")
        print()


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "show":
        show_pending_questions()
    elif len(sys.argv) > 1 and sys.argv[1] == "--llm":
        run_detection(use_llm=True, num_samples=200)
    else:
        run_detection(use_llm=False, num_samples=300)
