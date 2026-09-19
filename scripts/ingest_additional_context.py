#!/usr/bin/env python3
"""
Ingest additional Burning Man context into the knowledge database.
Parses markdown files and adds them as:
1. Concept definitions (from glossary)
2. Knowledge documents (from other content)
"""

import sqlite3
import json
import re
from pathlib import Path

DB_PATH = Path(__file__).parent / "output" / "ps_knowledge.db"
CONTEXT_PATH = Path(__file__).parent.parent / "Additional-context"


def slugify(text: str) -> str:
    """Create a URL-safe slug from text."""
    slug = text.lower()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    return slug


def parse_glossary_json(content: str) -> list[dict]:
    """Extract JSON glossary entries from markdown."""
    # Find JSON array in the content
    match = re.search(r'\[\s*\{.*?\}\s*\]', content, re.DOTALL)
    if not match:
        return []

    json_text = match.group()
    # Clean up escaped underscores
    json_text = json_text.replace('\\_', '_')

    try:
        return json.loads(json_text)
    except json.JSONDecodeError as e:
        print(f"⚠️ JSON parse error: {e}")
        # Try to fix common issues
        json_text = re.sub(r',\s*\]', ']', json_text)  # Remove trailing commas
        try:
            return json.loads(json_text)
        except:
            return []


def ingest_glossary_terms(conn: sqlite3.Connection, terms: list[dict]):
    """Add glossary terms as concept entities."""
    added = 0
    skipped = 0

    for term_data in terms:
        term = term_data.get('term', '')
        if not term:
            continue

        # Check if entity already exists
        cursor = conn.execute(
            "SELECT id FROM entity WHERE LOWER(canonical_name) = LOWER(?)",
            (term,)
        )
        if cursor.fetchone():
            skipped += 1
            continue

        # Map category to entity type
        category = term_data.get('category', 'Concept').lower()
        type_map = {
            'location': 'location',
            'organization': 'concept',
            'agency': 'concept',
            'logistics': 'concept',
            'transportation': 'concept',
            'slang': 'slang',
            'infrastructure': 'object',
            'technology': 'object',
            'principle': 'concept',
        }
        entity_type = type_map.get(category, 'concept')

        slug = slugify(term)

        # Create entity
        cursor = conn.execute(
            "INSERT INTO entity (type, canonical_name, slug) VALUES (?, ?, ?)",
            (entity_type, term, slug)
        )
        entity_id = cursor.lastrowid

        # Create alias
        conn.execute(
            "INSERT INTO entity_alias (entity_id, alias, source) VALUES (?, ?, ?)",
            (entity_id, term, 'glossary')
        )

        # Add full form as alias if present
        full_form = term_data.get('full_form')
        if full_form:
            conn.execute(
                "INSERT INTO entity_alias (entity_id, alias, source) VALUES (?, ?, ?)",
                (entity_id, full_form, 'glossary')
            )

        # Create concept definition
        definition = term_data.get('definition', '')
        if full_form:
            definition = f"{full_form}. {definition}"

        conn.execute(
            "INSERT INTO concept_definition (entity_id, definition, category) VALUES (?, ?, ?)",
            (entity_id, definition, category.title())
        )

        added += 1

    conn.commit()
    return added, skipped


def extract_sections(content: str) -> list[dict]:
    """Extract sections from markdown content."""
    sections = []

    # Split by ## headers
    parts = re.split(r'\n##\s+', content)

    for part in parts[1:]:  # Skip content before first header
        lines = part.strip().split('\n')
        if not lines:
            continue

        title = lines[0].strip('*# ')
        body = '\n'.join(lines[1:]).strip()

        if body and len(body) > 50:
            sections.append({
                'title': title,
                'content': body
            })

    return sections


def ingest_knowledge_docs(conn: sqlite3.Connection, sections: list[dict], source_file: str):
    """Add knowledge sections as camp_knowledge entries."""
    added = 0

    for section in sections:
        title = section['title']
        content = section['content']

        # Clean up markdown formatting
        content = re.sub(r'\*\*([^*]+)\*\*', r'\1', content)  # Remove bold
        content = re.sub(r'\*([^*]+)\*', r'\1', content)  # Remove italic
        content = re.sub(r'\\\_', '_', content)  # Fix escaped underscores

        # Check if similar content exists
        cursor = conn.execute(
            "SELECT id FROM camp_knowledge WHERE title = ? AND source_file = ?",
            (title, source_file)
        )
        if cursor.fetchone():
            continue

        conn.execute(
            """INSERT INTO camp_knowledge (title, content, source_file, category)
               VALUES (?, ?, ?, ?)""",
            (title, content, source_file, 'burning_man_culture')
        )
        added += 1

    conn.commit()
    return added


def run(db_path: Path = DB_PATH, context_path: Path = CONTEXT_PATH) -> None:
    """Ingest Additional-context markdown into camp_knowledge (+ glossary entities).

    Callable entry point so ingest_all.py can invoke this stage directly
    (import + call, not subprocess) and get a raised exception on failure
    instead of an ignored exit code. main() below is a thin CLI wrapper
    around this function.
    """
    print("📚 Ingesting Additional Context")
    print("=" * 60)

    if not context_path.exists():
        print(f"❌ Context folder not found: {context_path}")
        return

    conn = sqlite3.connect(db_path)

    # Process each markdown file
    md_files = list(context_path.glob("*.md"))
    print(f"Found {len(md_files)} markdown files\n")

    total_terms = 0
    total_docs = 0

    for md_file in md_files:
        print(f"📄 Processing: {md_file.name}")
        content = md_file.read_text()

        # Check if this is the glossary JSON file
        if 'Glossary JSON' in md_file.name:
            terms = parse_glossary_json(content)
            if terms:
                added, skipped = ingest_glossary_terms(conn, terms)
                print(f"   ✅ Added {added} glossary terms ({skipped} already existed)")
                total_terms += added

        # Extract and add knowledge sections
        sections = extract_sections(content)
        if sections:
            added = ingest_knowledge_docs(conn, sections, md_file.name)
            print(f"   ✅ Added {added} knowledge sections")
            total_docs += added

    conn.close()

    print("\n" + "=" * 60)
    print(f"✅ Ingestion complete!")
    print(f"   📖 {total_terms} new glossary terms")
    print(f"   📄 {total_docs} new knowledge sections")
    print(f"\n💡 Copy database to app and rebuild to use new content")


def main():
    run()


if __name__ == "__main__":
    main()
