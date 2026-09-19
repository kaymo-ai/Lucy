#!/usr/bin/env python3
"""
Ingests PS Processed files into a SQLite knowledge database.
Parses markdown, CSV files, and chat exports into structured data.
"""

import os
import re
import json
import sqlite3
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Optional
import csv

# Paths
PROCESSED_DIR = Path("../../PS Processed")
OUTPUT_DB = Path("../output/ps_knowledge.db")


def create_tables(conn: sqlite3.Connection):
    """Create all database tables."""
    cursor = conn.cursor()

    # People table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS people (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            aliases TEXT DEFAULT '[]',
            email TEXT,
            phone TEXT,
            city TEXT,
            years_attended TEXT DEFAULT '[]',
            roles TEXT DEFAULT '[]',
            personality_profile TEXT,
            embedding BLOB
        )
    """)

    # Document chunks table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS document_chunks (
            id TEXT PRIMARY KEY,
            source_file TEXT NOT NULL,
            source_type TEXT NOT NULL,
            title TEXT,
            content TEXT NOT NULL,
            metadata TEXT DEFAULT '{}',
            embedding BLOB
        )
    """)

    # Recipes table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recipes (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            servings INTEGER,
            prep_time_minutes INTEGER,
            cook_time_minutes INTEGER,
            ingredients TEXT DEFAULT '[]',
            steps TEXT DEFAULT '[]',
            notes TEXT,
            source_file TEXT
        )
    """)

    # Shift assignments table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS shift_assignments (
            id TEXT PRIMARY KEY,
            person_id TEXT,
            person_name TEXT NOT NULL,
            role TEXT NOT NULL,
            date TEXT,
            time_slot TEXT,
            year INTEGER NOT NULL,
            notes TEXT
        )
    """)

    # Chat messages table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            sender_name TEXT NOT NULL,
            sender_id TEXT,
            content TEXT NOT NULL,
            group_name TEXT NOT NULL,
            reply_to_id TEXT
        )
    """)

    # Create indexes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_people_name ON people(name)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_chunks_type ON document_chunks(source_type)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_recipes_name ON recipes(name)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_shifts_year_role ON shift_assignments(year, role)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_sender ON chat_messages(sender_name)")

    conn.commit()


def generate_id(content: str) -> str:
    """Generate a deterministic ID from content."""
    return hashlib.md5(content.encode()).hexdigest()[:16]


def detect_document_type(filepath: Path) -> str:
    """Detect document type from filepath."""
    path_lower = str(filepath).lower()

    if "recipe" in path_lower or "cookbook" in path_lower or "food" in path_lower:
        return "recipe"
    if "roster" in path_lower or "member" in path_lower:
        return "roster"
    if "shift" in path_lower or "role" in path_lower or "sign" in path_lower:
        return "shiftSchedule"
    if "budget" in path_lower or "dues" in path_lower or "finance" in path_lower:
        return "budget"
    if "inventory" in path_lower or "doris" in path_lower:
        return "inventory"
    if "debrief" in path_lower or "learning" in path_lower:
        return "debrief"
    if "manual" in path_lower or "guide" in path_lower or "ops" in path_lower:
        return "manual"
    if "event" in path_lower or "party" in path_lower:
        return "event"

    return "other"


def extract_year(filepath: Path) -> Optional[int]:
    """Extract year from filepath if present."""
    match = re.search(r"20[12]\d", str(filepath))
    return int(match.group()) if match else None


def chunk_markdown(content: str, filepath: Path, max_tokens: int = 500) -> list[dict]:
    """
    Chunk markdown content by headers or paragraphs.
    Returns list of chunk dicts.
    """
    chunks = []
    doc_type = detect_document_type(filepath)
    year = extract_year(filepath)

    # Split by H2 headers first
    sections = re.split(r'\n##\s+', content)

    for i, section in enumerate(sections):
        if not section.strip():
            continue

        # Get section title (first line if it was split from a header)
        lines = section.strip().split('\n')
        title = lines[0].strip('#').strip() if i > 0 else None
        text = '\n'.join(lines[1:]).strip() if i > 0 else section.strip()

        # Skip very short sections
        if len(text) < 50:
            continue

        # Further chunk if too long
        if len(text.split()) > max_tokens:
            # Split by paragraphs
            paragraphs = text.split('\n\n')
            current_chunk = []
            current_len = 0

            for para in paragraphs:
                para_len = len(para.split())
                if current_len + para_len > max_tokens and current_chunk:
                    chunk_text = '\n\n'.join(current_chunk)
                    chunks.append({
                        'id': generate_id(chunk_text),
                        'source_file': str(filepath),
                        'source_type': doc_type,
                        'title': title,
                        'content': chunk_text,
                        'metadata': json.dumps({'year': year, 'section': title})
                    })
                    current_chunk = [para]
                    current_len = para_len
                else:
                    current_chunk.append(para)
                    current_len += para_len

            if current_chunk:
                chunk_text = '\n\n'.join(current_chunk)
                chunks.append({
                    'id': generate_id(chunk_text),
                    'source_file': str(filepath),
                    'source_type': doc_type,
                    'title': title,
                    'content': chunk_text,
                    'metadata': json.dumps({'year': year, 'section': title})
                })
        else:
            chunks.append({
                'id': generate_id(text),
                'source_file': str(filepath),
                'source_type': doc_type,
                'title': title,
                'content': text,
                'metadata': json.dumps({'year': year, 'section': title})
            })

    return chunks


def parse_roster_csv(filepath: Path) -> list[dict]:
    """Parse roster/member CSV into people records."""
    people = []

    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Try to find name column
            name = None
            for col in ['Name', 'name', 'Full Name', 'Member', 'Person']:
                if col in row and row[col]:
                    name = row[col].strip()
                    break

            if not name or name.lower() in ['name', '', 'total']:
                continue

            # Extract other fields
            email = row.get('Email', row.get('email', ''))
            phone = row.get('Phone', row.get('phone', ''))
            city = row.get('City', row.get('city', row.get('Location', '')))

            year = extract_year(filepath)

            people.append({
                'id': generate_id(name.lower()),
                'name': name,
                'email': email.strip() if email else None,
                'phone': phone.strip() if phone else None,
                'city': city.strip() if city else None,
                'years_attended': json.dumps([year] if year else [])
            })

    return people


def parse_shift_csv(filepath: Path) -> list[dict]:
    """Parse shift/role CSV into shift assignments."""
    shifts = []
    year = extract_year(filepath) or datetime.now().year

    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []

        # Detect role column
        role_col = None
        for col in ['Role', 'role', 'Shift', 'shift', 'Position', 'Job']:
            if col in headers:
                role_col = col
                break

        # Detect name column
        name_col = None
        for col in ['Name', 'name', 'Person', 'Member', 'Assigned']:
            if col in headers:
                name_col = col
                break

        if not role_col and not name_col:
            # Try to infer from structure (name rows, role columns)
            return shifts

        for row in reader:
            role = row.get(role_col, '') if role_col else ''
            name = row.get(name_col, '') if name_col else ''

            if not name or not role:
                continue

            shifts.append({
                'id': generate_id(f"{name}-{role}-{year}"),
                'person_name': name.strip(),
                'role': role.strip(),
                'year': year
            })

    return shifts


def parse_chat_txt(filepath: Path) -> list[dict]:
    """Parse WhatsApp chat export into messages."""
    messages = []
    group_name = filepath.stem.replace('main-', '').replace('-CHAT', '').replace('PS-', 'PS ')

    # WhatsApp format: [date, time] sender: message
    # or: date, time - sender: message
    pattern = r'[\[\(]?(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}),?\s*(\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?)\]?\s*[-–]?\s*([^:]+):\s*(.+)'

    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        current_msg = None

        for line in f:
            line = line.strip()
            if not line:
                continue

            match = re.match(pattern, line, re.IGNORECASE)
            if match:
                # Save previous message
                if current_msg:
                    messages.append(current_msg)

                date_str, time_str, sender, content = match.groups()

                # Parse timestamp
                try:
                    timestamp = datetime.strptime(f"{date_str} {time_str}", "%m/%d/%y %I:%M %p")
                except:
                    try:
                        timestamp = datetime.strptime(f"{date_str} {time_str}", "%d/%m/%Y %H:%M")
                    except:
                        timestamp = datetime.now()

                current_msg = {
                    'id': generate_id(f"{timestamp}-{sender}-{content[:50]}"),
                    'timestamp': timestamp.isoformat(),
                    'sender_name': sender.strip(),
                    'content': content.strip(),
                    'group_name': group_name
                }
            elif current_msg:
                # Continuation of previous message
                current_msg['content'] += '\n' + line

        if current_msg:
            messages.append(current_msg)

    return messages


def ingest_all(processed_dir: Path, db_path: Path):
    """Ingest all processed files into the database."""
    OUTPUT_DB.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    create_tables(conn)
    cursor = conn.cursor()

    stats = {
        'chunks': 0,
        'people': 0,
        'shifts': 0,
        'messages': 0
    }

    # Process all files
    for filepath in processed_dir.rglob('*'):
        if filepath.is_dir():
            continue

        rel_path = filepath.relative_to(processed_dir)
        print(f"Processing: {rel_path}")

        try:
            if filepath.suffix == '.md':
                # Markdown document -> chunks
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()

                chunks = chunk_markdown(content, rel_path)
                for chunk in chunks:
                    cursor.execute("""
                        INSERT OR REPLACE INTO document_chunks
                        (id, source_file, source_type, title, content, metadata)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        chunk['id'],
                        chunk['source_file'],
                        chunk['source_type'],
                        chunk['title'],
                        chunk['content'],
                        chunk['metadata']
                    ))
                    stats['chunks'] += 1

            elif filepath.suffix == '.csv':
                path_lower = str(filepath).lower()

                if 'roster' in path_lower or 'member' in path_lower or 'camp' in path_lower:
                    # Roster -> people
                    people = parse_roster_csv(filepath)
                    for person in people:
                        cursor.execute("""
                            INSERT OR IGNORE INTO people
                            (id, name, email, phone, city, years_attended)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (
                            person['id'],
                            person['name'],
                            person['email'],
                            person['phone'],
                            person['city'],
                            person['years_attended']
                        ))
                        stats['people'] += 1

                elif 'shift' in path_lower or 'role' in path_lower or 'sign' in path_lower:
                    # Shift schedule -> shifts
                    shifts = parse_shift_csv(filepath)
                    for shift in shifts:
                        cursor.execute("""
                            INSERT OR REPLACE INTO shift_assignments
                            (id, person_name, role, year)
                            VALUES (?, ?, ?, ?)
                        """, (
                            shift['id'],
                            shift['person_name'],
                            shift['role'],
                            shift['year']
                        ))
                        stats['shifts'] += 1

                else:
                    # Generic CSV -> chunk as document
                    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()

                    doc_type = detect_document_type(rel_path)
                    year = extract_year(rel_path)

                    cursor.execute("""
                        INSERT OR REPLACE INTO document_chunks
                        (id, source_file, source_type, title, content, metadata)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        generate_id(content),
                        str(rel_path),
                        doc_type,
                        filepath.stem,
                        content[:10000],  # Truncate very long CSVs
                        json.dumps({'year': year})
                    ))
                    stats['chunks'] += 1

            elif filepath.suffix == '.txt':
                # Chat export
                messages = parse_chat_txt(filepath)
                for msg in messages:
                    cursor.execute("""
                        INSERT OR REPLACE INTO chat_messages
                        (id, timestamp, sender_name, content, group_name)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        msg['id'],
                        msg['timestamp'],
                        msg['sender_name'],
                        msg['content'],
                        msg['group_name']
                    ))
                    stats['messages'] += 1

        except Exception as e:
            print(f"  Error: {e}")

    conn.commit()
    conn.close()

    print("\n=== Ingestion Complete ===")
    print(f"Document chunks: {stats['chunks']}")
    print(f"People: {stats['people']}")
    print(f"Shifts: {stats['shifts']}")
    print(f"Messages: {stats['messages']}")
    print(f"Database: {db_path}")


if __name__ == "__main__":
    os.chdir(Path(__file__).parent)
    ingest_all(PROCESSED_DIR, OUTPUT_DB)
