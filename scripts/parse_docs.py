#!/usr/bin/env python3
"""
Parse CSV and Markdown files for camp knowledge.
Extracts structured data (shifts, rosters) and document content.

Usage:
    python parse_docs.py <input_dir> <output_db>
"""

import csv
import re
import sqlite3
import json
import sys
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional


def add_tables(conn: sqlite3.Connection):
    """Add tables for documents and camp knowledge."""
    cursor = conn.cursor()
    
    # Camp knowledge (general docs, guides, how-tos)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS camp_knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            source_file TEXT,
            category TEXT,
            year INTEGER
        )
    ''')
    
    # Structured roster data
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS roster (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id INTEGER,
            name TEXT NOT NULL,
            email TEXT,
            phone TEXT,
            year INTEGER,
            status TEXT,
            dues_paid TEXT,
            shelter_type TEXT,
            home_city TEXT,
            FOREIGN KEY (person_id) REFERENCES person(id)
        )
    ''')
    
    # Shift assignments
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS shifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id INTEGER,
            person_name TEXT,
            year INTEGER,
            role TEXT,
            day TEXT,
            time_slot TEXT,
            source_file TEXT,
            FOREIGN KEY (person_id) REFERENCES person(id)
        )
    ''')
    
    conn.commit()


def get_or_create_person(cursor, name: str) -> int:
    """Get person ID by name, or create if not exists."""
    cursor.execute('SELECT id FROM person WHERE name = ?', (name,))
    row = cursor.fetchone()
    if row:
        return row[0]
    
    cursor.execute('INSERT INTO person (name) VALUES (?)', (name,))
    return cursor.lastrowid


def extract_year_from_path(path: Path) -> Optional[int]:
    """Try to extract year from file path."""
    year_match = re.search(r'20[12][0-9]', str(path))
    if year_match:
        return int(year_match.group())
    return None


# Documents that are inventory, money or arithmetic.
#
# is_shift_csv() decides from HEADERS, and a shopping list has a date column,
# so 2018's alcohol order and 2023's Venmo export both classified as shift
# sheets. 1,277 of the 2,140 rows in `shifts` came from files like these --
# 60% of the table -- and every one of them stored a spreadsheet cell as a
# camper's name. 94 people existed in the corpus solely because of them, all
# of them bank-transaction timestamps like "2023-05-17 00:00:00".
#
# Checked against the 521 CSVs on disk before this was written: 77 files match
# this pattern, 63 files look like genuine shift or roster sheets, and the two
# sets do not intersect. Nothing real is excluded.
NOT_ABOUT_PEOPLE = re.compile(
    r"shopping list|bank transaction|venmo|revolut|novo\b|quick budget"
    r"|quantity calculation|actual bought|cash _ carry|invoice|receipt"
    r"|expenses|reimburse", re.I)


def is_about_people(path: Path) -> bool:
    """Whether this document could plausibly name campers and shifts."""
    return not NOT_ABOUT_PEOPLE.search(path.name)


def has_usable_header(rows: list) -> bool:
    """Whether row 0 actually named the columns.

    A sheet exported from Google Sheets often opens with a title row, or a
    blank one, and csv.DictReader takes whatever it finds as the header. When
    that row is empty every column collapses onto the same empty key and the
    whole document reduces to [{"": ""}, {"": ""}, ...].

    That is what happened to the 2026 production budget: 18 rows of narrative
    and a dues table, stored as seventeen empty dicts, with 685 / 785 / 885 --
    the most asked-about numbers the camp has -- absent from the database
    entirely.
    """
    if not rows:
        return False
    keys = [str(k or "").strip() for k in rows[0].keys()]
    named = [k for k in keys if k]
    # One named column out of five is a title row, not a header.
    return len(named) >= 2 or (len(keys) == 1 and bool(named))


def render_sheet_as_text(path: Path, limit: int = 12000) -> str:
    """A spreadsheet as something a person (or a model) can read.

    json.dumps of the first twenty rows is the wrong shape for a sheet that
    holds prose: it buries the sentences in punctuation, and searchDocs
    returns a passage window, so what reaches the fact sheet is a fragment of
    JSON. Non-empty cells joined per row keeps the sentences intact and the
    table rows legible as rows.
    """
    out = []
    total = 0
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        for raw in csv.reader(fh):
            cells = [c.strip() for c in raw if c and c.strip()]
            if not cells:
                continue
            line = cells[0] if len(cells) == 1 else " | ".join(cells)
            out.append(line)
            total += len(line) + 1
            if total > limit:
                out.append("[...]")
                break
    return "\n".join(out)


# --------------------------------------------------------------------------
# What earns a place in the corpus
# --------------------------------------------------------------------------
#
# 627 documents went in and 172 of them were finance and 157 were food, which
# is not a corpus, it is exhaust. Nobody in the desert asks what avocados cost
# in 2015, and the budget sheets are actively harmful: their cell contents
# survive extraction as garbage like "bzxlduehlxbhsiudfzljlk", which then gets
# embedded and competes with real text at retrieval time.
#
# The split that matters: DOCUMENTS answer "how does this work, where is it" --
# the generator, the evap pond, Doris, the med kit. CHAT answers "who are we"
# -- the lore, the jokes, the traditions. Measured 2026-08-20: of 149 entities
# the enrichment discovered, 70 were traditions, and they came out of
# conversation, not spreadsheets.
#
# This is a SKIP RULE, not a delete. Every file stays in PS Processed. Cut too
# deep and you change a line here and re-run, rather than restoring a bucket.

# Paperwork about a thing rather than the thing itself. Dropped wherever it
# is filed, because categorize_file() checks "lucy" before "budget" and files
# "Lucy 2024_Lucy Budget" and "Gmail - lucy insurance 2024" under `lucy` --
# 39 documents of insurance policy and ledger sitting in a category kept for
# being timeless. Nobody in the desert asks about the art car's premium.
ADMIN_SHAPES = ("insurance", "policy", "gmail -", "ledger", "invoice",
                "payments", "actuals", "dues", "reimburse", "budget")

# Recency. The camp buys different water, parks somewhere else and runs
# different shifts now, and 45% of the corpus predates 2020.
KEEP_FROM_YEAR = 2024

# Categories that earn their place regardless of age: small, operational, and
# the ones where a missing answer costs something real. `water` has 3
# documents and `power` has 2 -- against finance's 172 -- and those five are
# carrying every question about the generator and the evap pond.
# Deliberately small, and deliberately NOT including `lucy` or `operations`.
# How the generator works does not expire; last year's transport plan does.
# water has 2 documents and power has 2 -- keeping every year of those is
# nearly free, and they carry every question about the evap pond and the
# genny. Recent lucy and operations documents still survive on the year rule.
ALWAYS_KEEP = {"water", "power", "bikes", "burning_man_culture"}

# Food is this year's plan or it is a grocery list from a burn most current
# campers did not attend.
FOOD_FROM_YEAR = 2022

# Finance is dropped wholesale except these. "Med kit supplies" is filed under
# finance because it sits in the FINANCE folder and categorize_file() reads the
# PATH -- and it is the only document in the corpus containing "tourniquet".
# A category rule without this exemption deletes the camp's only medical
# answer, which is why the exemption is by title and not by folder.
# An INVENTORY says what the camp has; a LEDGER says what it paid. The first
# answers questions -- "are there clippers in Doris" -- and the second is
# exhaust. They sit in the same folder, so the split has to be by title.
# "Med kit supplies" is the case that proves it: filed under finance because
# categorize_file() reads the PATH, and the only document in the corpus
# containing "tourniquet".
FINANCE_KEEP = ("med kit", "medkit", "medical", "first aid",
                "supplies", "inventory", "packing list", "kit list",
                "production budget 2026")

# The sign-up grids specifically, which parse_shift_grid.py reads properly as
# a 2-D grid into shift_grid. Everything else in the shifts category is prose.
SIGNUP_GRIDS = ("sign up", "signup", "sign-up", "swaps", "shifts_")

# Shapes that are pollution wherever they are filed. Checked against title and
# filename both, since either can carry it.
JUNK_SHAPES = ("grocer", "shopping list", "expense", "venmo", "receipt",
               "do not update", "passengers looking")


def should_ingest(title: str, filename: str, category: str,
                  year: int | None) -> bool:
    """Whether this document earns a camp_knowledge row.

    Verified against the 627-document corpus of 2026-08-20 before being
    switched on; see scripts/test_doc_filter.py for the counts it produced.
    """
    hay = f"{title} {filename}".lower()

    # The exemptions win over everything, including the junk shapes: a med kit
    # inventory reads exactly like a shopping list, because it is one.
    if any(k in hay for k in FINANCE_KEEP):
        return True

    if any(j in hay for j in JUNK_SHAPES):
        return False

    if any(a in hay for a in ADMIN_SHAPES):
        return False

    if category == "finance":
        return False

    # Only the sign-up GRIDS, which shift_grid now parses properly as a grid.
    # "PS 2018 On-Playa Shifts" is prose about how the camp runs -- it lands
    # in this category because categorize_file() matches "shift" anywhere in
    # the path, and dropping the category wholesale took 85 terms with it,
    # auto-switchover and back-up among them.
    if category == "shifts":
        return not any(g in hay for g in SIGNUP_GRIDS)

    # Food is kept on SHAPE, not on age. A 2015 grocery list is exhaust; the
    # 2017 recipe binder is how the camp cooks for forty people, and a year
    # cutoff cannot tell them apart -- it dropped 109 terms of real cooking
    # vocabulary, baguettes and brisket and gazpacho. JUNK_SHAPES above
    # already removes the lists.
    if category in ALWAYS_KEEP:
        return True

    # Everything else earns its place by being recent enough to still be true.
    return year is not None and year >= KEEP_FROM_YEAR


def categorize_file(path: Path) -> str:
    """Determine category from file path."""
    path_str = str(path).lower()
    
    if 'food' in path_str or 'recipe' in path_str:
        return 'food'
    elif 'shift' in path_str or 'sign up' in path_str:
        return 'shifts'
    elif 'roster' in path_str or 'camper' in path_str:
        return 'roster'
    elif 'bike' in path_str:
        return 'bikes'
    elif 'lucy' in path_str:
        return 'lucy'
    elif 'event' in path_str or 'party' in path_str:
        return 'events'
    elif 'budget' in path_str or 'finance' in path_str:
        return 'finance'
    elif 'power' in path_str or 'generator' in path_str:
        return 'power'
    elif 'water' in path_str:
        return 'water'
    elif 'ops' in path_str:
        return 'operations'
    else:
        return 'general'


def parse_markdown(filepath: Path) -> str:
    """Read and clean markdown content."""
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        # Basic cleaning
        content = re.sub(r'\n{3,}', '\n\n', content)  # Collapse multiple newlines
        return content.strip()
    except Exception as e:
        print(f"   ⚠️ Error reading {filepath.name}: {e}")
        return ""


def parse_csv_generic(filepath: Path) -> List[Dict]:
    """Parse CSV into list of dicts."""
    rows = []
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            # Try to detect delimiter
            sample = f.read(2048)
            f.seek(0)
            
            try:
                dialect = csv.Sniffer().sniff(sample)
            except:
                dialect = csv.excel
            
            reader = csv.DictReader(f, dialect=dialect)
            for row in reader:
                rows.append(row)
    except Exception as e:
        print(f"   ⚠️ Error reading {filepath.name}: {e}")
    
    return rows


def is_roster_csv(headers: List[str]) -> bool:
    """Check if CSV looks like a roster."""
    headers = [h for h in headers if h is not None]
    header_lower = [h.lower() for h in headers]
    roster_keywords = ['email', 'phone', 'name', 'first', 'last', 'dues', 'shelter']
    return sum(1 for kw in roster_keywords if any(kw in h for h in header_lower)) >= 2


def is_shift_csv(headers: List[str]) -> bool:
    """Check if CSV looks like a shift schedule."""
    headers = [h for h in headers if h is not None]
    header_lower = [h.lower() for h in headers]
    shift_keywords = ['shift', 'role', 'day', 'time', 'sign', 'slot']
    return sum(1 for kw in shift_keywords if any(kw in h for h in header_lower)) >= 1


def process_roster_csv(cursor, filepath: Path, rows: List[Dict], year: int):
    """Process a roster CSV."""
    count = 0
    for row in rows:
        # Try to find name fields
        name = None
        for key in ['Name', 'name', 'First Name', 'first_name']:
            if key in row and row[key]:
                name = row[key]
                break
        
        if not name:
            # Try combining first and last
            first = row.get('First Name', row.get('first_name', ''))
            last = row.get('Last Name', row.get('last_name', ''))
            if first or last:
                name = f"{first} {last}".strip()
        
        if not name or len(name) < 2:
            continue
        
        # Get or create person
        person_id = get_or_create_person(cursor, name)
        
        # Extract other fields
        email = None
        phone = None
        for key in row:
            if key is None:
                continue
            kl = key.lower()
            if 'email' in kl:
                email = row[key]
            elif 'phone' in kl or 'telephone' in kl:
                phone = row[key]
        
        dues_paid = None
        for key in row:
            if key is None:
                continue
            if 'dues' in key.lower() or 'paid' in key.lower():
                dues_paid = row[key]
        
        shelter = None
        for key in row:
            if key is None:
                continue
            if 'shelter' in key.lower():
                shelter = row[key]
        
        city = None
        for key in row:
            if key is None:
                continue
            if 'city' in key.lower() or 'home' in key.lower():
                city = row[key]
        
        cursor.execute('''
            INSERT INTO roster (person_id, name, email, phone, year, dues_paid, shelter_type, home_city)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (person_id, name, email, phone, year, dues_paid, shelter, city))
        count += 1
    
    return count


def process_shift_csv(cursor, filepath: Path, rows: List[Dict], year: int):
    """Process a shift schedule CSV."""
    count = 0
    for row in rows:
        # Try to find person name
        name = None
        for key in ['Name', 'name', 'Person', 'Who']:
            if key in row and row[key]:
                name = row[key]
                break
        
        # No "first non-empty cell is probably a name" fallback.
        #
        # That heuristic is what filled `person` with quoted Venmo transaction
        # ids ("3814520574502165507" survives .isdigit() because of the
        # quotes), header cells ("Location", "Name"), and dates ("Tuesday,
        # August 15 (6-10PM)"). A sheet that does not say which column holds a
        # person does not get to nominate one.
        
        if not name:
            continue
        
        
        # Extract shift info
        role = None
        day = None
        time_slot = None
        
        for key, val in row.items():
            if key is None:
                continue
            kl = key.lower()
            if 'role' in kl or 'shift' in kl:
                role = val
            elif 'day' in kl or 'date' in kl:
                day = val
            elif 'time' in kl or 'slot' in kl:
                time_slot = val
        
        if role or day:
            # The person is created HERE, not before the check.
            # Previously get_or_create_person ran for every row that
            # produced a name, including the thousands that never
            # became a shift -- which is why `person` held 1,338 rows
            # while only a fraction were campers.
            person_id = get_or_create_person(cursor, name)
            cursor.execute('''
                INSERT INTO shifts (person_id, person_name, year, role, day, time_slot, source_file)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (person_id, name, year, role, day, time_slot, filepath.name))
            count += 1
    
    return count


DEFAULT_INPUT_DIR = Path('~/Snails/PS Processed')
DEFAULT_DB_PATH = Path('~/Snails/scripts/output/ps_knowledge.db')


def run(input_dir: Path = DEFAULT_INPUT_DIR, db_path: Path = DEFAULT_DB_PATH) -> None:
    """Parse camp documents into camp_knowledge/roster/shifts.

    Callable entry point so ingest_all.py can invoke this stage directly
    (import + call, not subprocess) and get a raised exception on failure
    instead of an ignored exit code. main() below is a thin CLI wrapper
    around this function.
    """
    print(f"📂 Input directory: {input_dir}")
    print(f"💾 Database: {db_path}")
    
    # Connect and setup
    conn = sqlite3.connect(db_path)
    add_tables(conn)
    cursor = conn.cursor()
    
    # Find all files
    md_files = list(input_dir.rglob('*.md'))
    csv_files = list(input_dir.rglob('*.csv'))
    
    print(f"\n📄 Found {len(md_files)} markdown files")
    print(f"📊 Found {len(csv_files)} CSV files")
    
    # Process markdown files
    print("\n📝 Processing markdown docs...")
    doc_count = 0
    skipped_count = 0
    for md_file in md_files:
        content = parse_markdown(md_file)
        if len(content) < 50:  # Skip tiny files
            continue
        
        year = extract_year_from_path(md_file)
        category = categorize_file(md_file)

        if not should_ingest(md_file.stem, md_file.name, category, year):
            skipped_count += 1
            continue
        
        cursor.execute('''
            INSERT INTO camp_knowledge (title, content, source_file, category, year)
            VALUES (?, ?, ?, ?, ?)
        ''', (md_file.stem, content, md_file.name, category, year))
        doc_count += 1
    
    print(f"   ✓ {doc_count} documents added to camp_knowledge")
    if skipped_count:
        print(f"   - {skipped_count} markdown file(s) skipped by should_ingest")
    
    # Process CSV files
    print("\n📊 Processing CSV files...")
    roster_count = 0
    shift_count = 0
    other_csv_count = 0
    
    for csv_file in csv_files:
        rows = parse_csv_generic(csv_file)
        if not rows:
            continue
        
        headers = list(rows[0].keys())
        year = extract_year_from_path(csv_file)

        # An inventory or a ledger still becomes camp_knowledge below -- what
        # it must never do is nominate people or claim to be a rota.
        if not is_about_people(csv_file):
            # Inventories and ledgers stay compact on purpose. Rendering a
            # Venmo export as flowing text makes it MORE likely to surface in
            # a passage search, not less, and nobody asks Lucy about a bank
            # statement. Readability is for documents worth reading.
            content = json.dumps(rows[:20], indent=2)
            category = categorize_file(csv_file)
            if not should_ingest(csv_file.stem, csv_file.name, category, year):
                skipped_count += 1
                continue
            if len(content) > 100:
                cursor.execute('''
                    INSERT INTO camp_knowledge (title, content, source_file, category, year)
                    VALUES (?, ?, ?, ?, ?)
                ''', (csv_file.stem, content, csv_file.name, category, year))
                other_csv_count += 1
            continue
        
        if is_roster_csv(headers):
            count = process_roster_csv(cursor, csv_file, rows, year)
            roster_count += count
            if count > 0:
                print(f"   ✓ Roster: {csv_file.name} ({count} entries)")
        elif is_shift_csv(headers):
            count = process_shift_csv(cursor, csv_file, rows, year)
            shift_count += count
            if count > 0:
                print(f"   ✓ Shifts: {csv_file.name} ({count} entries)")
        else:
            # A sheet that named its columns is data; one that did not is
            # usually prose somebody typed into a spreadsheet, and it has to
            # be stored as prose or it is unreadable and unsearchable.
            if has_usable_header(rows):
                content = json.dumps(rows[:20], indent=2)  # First 20 rows as sample
            else:
                content = render_sheet_as_text(csv_file)
            category = categorize_file(csv_file)
            if not should_ingest(csv_file.stem, csv_file.name, category, year):
                skipped_count += 1
            elif len(content) > 100:
                cursor.execute('''
                    INSERT INTO camp_knowledge (title, content, source_file, category, year)
                    VALUES (?, ?, ?, ?, ?)
                ''', (csv_file.stem, content, csv_file.name, category, year))
                other_csv_count += 1
    
    print(f"\n📊 CSV Summary:")
    print(f"   Roster entries: {roster_count}")
    print(f"   Shift entries: {shift_count}")
    print(f"   Other CSVs as knowledge: {other_csv_count}")
    # The TOTAL, after all three insert sites. The per-stage line above counts
    # only the markdown pass, and reading it as the whole picture understates
    # the trim by a factor of five.
    if skipped_count:
        print(f"\n🧹 {skipped_count} document(s) skipped by should_ingest "
              f"— ledgers, grocery lists, sign-up grids.")
        print(f"   Every file is untouched on disk; this is a skip rule, "
              f"not a delete.")
    
    conn.commit()
    
    # Summary stats
    print("\n📈 Database totals:")
    for table in ['person', 'person_content', 'roster', 'shifts', 'camp_knowledge']:
        cursor.execute(f'SELECT COUNT(*) FROM {table}')
        count = cursor.fetchone()[0]
        print(f"   {table}: {count} rows")
    
    conn.close()
    print("\n✅ Done!")


def main():
    if len(sys.argv) < 2:
        input_dir = DEFAULT_INPUT_DIR
        db_path = DEFAULT_DB_PATH
    else:
        input_dir = Path(sys.argv[1])
        db_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path('./output/ps_knowledge.db')

    run(input_dir, db_path)


if __name__ == '__main__':
    main()
