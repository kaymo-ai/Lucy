#!/usr/bin/env python3
"""
Parse WhatsApp chat exports and extract PersonContent for the PS knowledge base.

Usage:
    python parse_chats.py <input_dir> <output_dir>
    
Example:
    python parse_chats.py "../PS Processed" "./output"
"""

import re
import json
import sqlite3
import os
import sys
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from typing import List, Dict, Tuple, Optional

# WhatsApp message pattern. WhatsApp prefixes media/attachment lines with
# U+200E (LEFT-TO-RIGHT MARK) *before* the opening bracket, so the optional
# ‎ prefix is required — without it those lines are swallowed as
# continuations of the previous message. That bug dropped 2,867 lines.
WHATSAPP_PATTERN = re.compile(
    r'^[‎‏]*\[(\d{1,2}/\d{1,2}/\d{2,4}),\s+'
    r'(\d{1,2}:\d{2}:\d{2}\s*[AP]M)\]\s+([^:]+):\s*(.*)$'
)

# Attachment marker inside a message body: ‎<attached: FILENAME>
ATTACHMENT_PATTERN = re.compile(r'<attached:\s*([^>]+)>')

# Media-placeholder token WhatsApp appends anywhere in a message body when
# the export was taken without media, e.g. "Almost there ‎image omitted".
# The caption preceding it is real user content and must survive; only the
# token itself is export noise. Media types below were derived from the
# actual corpus (see scripts/tests/test_parse_chats.py and the task notes):
# image, video, GIF, and document are the only types that occur.
MEDIA_OMITTED_PATTERN = re.compile(
    r'[‎‏]?\b(?:image|video|gif|document)\s+omitted\b',
    re.IGNORECASE
)

# Invisible bidirectional marks WhatsApp uses to tag machine-generated content.
BIDI_MARKS = '‎‏'

class ChatParser:
    def __init__(self):
        self.messages: List[Dict] = []
        self.people: Dict[str, Dict] = {}  # name -> {message_count, first_seen, last_seen}
        
    def parse_file(self, filepath: Path, source_name: str) -> int:
        """Parse a WhatsApp export file. Returns number of messages parsed."""
        count = 0
        current_message = None
        
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.rstrip('\r\n')
                
                # Try to match a new message
                match = WHATSAPP_PATTERN.match(line)
                
                if match:
                    # Save previous message if exists
                    if current_message:
                        self._save_message(current_message, source_name)
                        count += 1
                    
                    date_str, time_str, sender, text = match.groups()

                    # Parse timestamp: try 2-digit year first, then 4-digit
                    timestamp = None
                    try:
                        timestamp = datetime.strptime(
                            f"{date_str} {time_str}",
                            "%m/%d/%y %I:%M:%S %p"
                        )
                    except ValueError:
                        try:
                            timestamp = datetime.strptime(
                                f"{date_str} {time_str}",
                                "%m/%d/%Y %I:%M:%S %p"
                            )
                        except ValueError:
                            pass
                    
                    current_message = {
                        'timestamp': timestamp,
                        'sender': sender.strip(),
                        'text': text.strip(),
                    }
                elif current_message:
                    # Continuation of previous message
                    current_message['text'] += '\n' + line
            
            # Save last message
            if current_message:
                self._save_message(current_message, source_name)
                count += 1
        
        return count
    
    def _save_message(self, msg: Dict, source: str):
        """Save a parsed message, dropping only machine-generated notices.

        Classification is structural, not lexical. WhatsApp prefixes the body
        of every system notice with U+200E; user prose never carries it. A
        message with an attachment is always kept, because the mark there
        belongs to the <attached:> token rather than to the message.
        """
        raw = msg['text']
        sender = msg['sender']

        # Extract the attachment reference before anything else, so media
        # messages survive as first-class rows pointing at their file.
        media_ref = None
        attachment = ATTACHMENT_PATTERN.search(raw)
        if attachment:
            media_ref = attachment.group(1).strip()
            raw = ATTACHMENT_PATTERN.sub('', raw)

        # Strip a media-placeholder token wherever it falls in the body
        # (typically trailing a real caption). This must run before the
        # is_system_notice check below: for a bare placeholder body this
        # strips it down to nothing, so the message still gets dropped --
        # just via the "genuinely empty" branch rather than the notice
        # branch. For a captioned message, only the token is removed.
        raw = MEDIA_OMITTED_PATTERN.sub('', raw)

        # Did the ORIGINAL body open with a bidi mark? Check before stripping.
        # Leading whitespace can precede it, so lstrip whitespace only.
        is_system_notice = raw.lstrip().startswith(tuple(BIDI_MARKS))

        clean = raw.strip(BIDI_MARKS + ' \t\n').strip()

        if media_ref is None:
            if is_system_notice:
                return          # "X added Y", "joined using a group link",
                # "image omitted", "video omitted", …
            elif not clean:
                return          # genuinely empty
        # Short messages are kept deliberately: "ok", "ya", "👍" carry real
        # social signal for the enrichment pass, and the old len(text) < 3
        # floor was skewing per-person message counts.

        msg['text'] = clean
        msg['media_ref'] = media_ref
        msg['source'] = source
        self.messages.append(msg)

        # Track person stats
        if sender not in self.people:
            self.people[sender] = {
                'message_count': 0,
                'first_seen': msg['timestamp'],
                'last_seen': msg['timestamp'],
            }
        
        self.people[sender]['message_count'] += 1
        if msg['timestamp']:
            if self.people[sender]['last_seen'] is None or msg['timestamp'] > self.people[sender]['last_seen']:
                self.people[sender]['last_seen'] = msg['timestamp']
    
    def get_person_content(self, person_name: str) -> List[Dict]:
        """Get all messages sent by a person."""
        return [m for m in self.messages if m['sender'] == person_name]
    
    def get_mentions(self, person_name: str) -> List[Dict]:
        """Get all messages mentioning a person."""
        # WhatsApp mentions look like @⁨Name⁩
        patterns = [
            f'@⁨{person_name}⁩',
            f'@{person_name}',
            person_name,  # Direct name mentions
        ]
        
        results = []
        for msg in self.messages:
            if msg['sender'] == person_name:
                continue  # Skip self-mentions
            
            for pattern in patterns:
                if pattern.lower() in msg['text'].lower():
                    results.append(msg)
                    break
        
        return results


def create_database(output_dir: Path):
    """Create SQLite database with schema."""
    db_path = output_dir / 'ps_knowledge.db'
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Person table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS person (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            email TEXT,
            phone TEXT,
            message_count INTEGER DEFAULT 0,
            first_seen TEXT,
            last_seen TEXT
        )
    ''')
    
    # PersonContent table (unstructured, for RAG)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS person_content (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id INTEGER,
            content TEXT NOT NULL,
            source TEXT,
            content_type TEXT,  -- 'sent', 'mention', 'note'
            timestamp TEXT,
            media_ref TEXT,
            FOREIGN KEY (person_id) REFERENCES person(id)
        )
    ''')
    
    # Create indexes
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_person_name ON person(name)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_content_person ON person_content(person_id)')
    
    conn.commit()
    return conn


def write_database(parser: 'ChatParser', output_dir: Path) -> Path:
    """Create the database, insert people + content, and write the summary JSON.

    Shared by parse_chats.main() and ingest_all.py so the insert path is
    defined in exactly one place.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    print("\n💾 Creating SQLite database...")
    conn = create_database(output_dir)
    cursor = conn.cursor()

    # Insert people
    for name, stats in parser.people.items():
        cursor.execute('''
            INSERT OR REPLACE INTO person (name, message_count, first_seen, last_seen)
            VALUES (?, ?, ?, ?)
        ''', (
            name,
            stats['message_count'],
            stats['first_seen'].isoformat() if stats['first_seen'] else None,
            stats['last_seen'].isoformat() if stats['last_seen'] else None,
        ))

    # Insert person content
    for msg in parser.messages:
        # Get person ID
        cursor.execute('SELECT id FROM person WHERE name = ?', (msg['sender'],))
        row = cursor.fetchone()
        if row:
            person_id = row[0]
            cursor.execute('''
                INSERT INTO person_content
                    (person_id, content, source, content_type, timestamp, media_ref)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                person_id,
                msg['text'],
                msg['source'],
                'sent',
                msg['timestamp'].isoformat() if msg['timestamp'] else None,
                msg.get('media_ref'),
            ))

    conn.commit()

    # Export summary JSON
    summary = {
        'total_messages': len(parser.messages),
        'total_people': len(parser.people),
        'sources': list(set(m['source'] for m in parser.messages)),
        'people': {
            name: {
                'message_count': stats['message_count'],
                'first_seen': stats['first_seen'].isoformat() if stats['first_seen'] else None,
                'last_seen': stats['last_seen'].isoformat() if stats['last_seen'] else None,
            }
            for name, stats in parser.people.items()
        }
    }

    summary_path = output_dir / 'extraction_summary.json'
    with open(summary_path, 'w') as f:
        json.dump(summary, f, indent=2)

    print(f"\n✅ Done!")
    print(f"   Database: {output_dir / 'ps_knowledge.db'}")
    print(f"   Summary: {summary_path}")

    conn.close()
    return output_dir / 'ps_knowledge.db'


def main():
    if len(sys.argv) < 2:
        input_dir = Path('~/Snails/PS Processed')
        output_dir = Path('~/Snails/scripts/output')
    else:
        input_dir = Path(sys.argv[1])
        output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path('./output')
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"📂 Input directory: {input_dir}")
    print(f"📁 Output directory: {output_dir}")
    
    # Find all chat files
    chat_files = list(input_dir.glob('*.txt'))
    print(f"\n📄 Found {len(chat_files)} text files")
    
    parser = ChatParser()
    
    # Parse each file
    for chat_file in chat_files:
        source_name = chat_file.stem
        count = parser.parse_file(chat_file, source_name)
        print(f"   ✓ {chat_file.name}: {count} messages")
    
    print(f"\n📊 Total messages: {len(parser.messages)}")
    print(f"👥 Unique people: {len(parser.people)}")
    
    # Show top contributors
    print("\n🏆 Top 10 contributors:")
    sorted_people = sorted(
        parser.people.items(), 
        key=lambda x: x[1]['message_count'], 
        reverse=True
    )[:10]
    for name, stats in sorted_people:
        print(f"   {name}: {stats['message_count']} messages")

    write_database(parser, output_dir)


if __name__ == '__main__':
    main()
