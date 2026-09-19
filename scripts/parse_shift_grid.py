#!/usr/bin/env python3
"""Read a shift sign-up GRID, which a row-oriented parser cannot.

The camp signs up for shifts on a spreadsheet shaped like a wall planner:
shift types down the left, days across the top, names in the cells. Every
row-oriented CSV parser in this repo reads that as gibberish -- parse_docs
produced 2,140 `shifts` rows in which person_name and role held the SAME
cell, `day` was mostly null, and the three biggest sources were shopping
lists. That table never held a real shift.

The shape, from "PS 2026 Shifts.xlsx - Sign Up 2026.csv":

    row 0                     title
    row 5   cols 5,9,13...    Sunday, Monday, Tuesday ... one day per 4 columns
    row 6   col 2, col 3      "Descriptions", "Time"
    row 7   col 1             "Maintenance"      <- a category, no time
    row 8   col 1,2,3         shift, description, time
            cols 9,10,11      Dayo, Winter, Christina   <- Monday
            cols 13,14,15     Ozge, Jon, Kat            <- Tuesday

So a day is a COLUMN RANGE, running from its header to the next one, and a
person's shift is the intersection of a row and a range. Nothing about that
survives being read a row at a time.

WHAT THIS IS FOR. Not retrieval. A rota is a table, and the failure mode of
paraphrasing a table is somebody missing their shift, so these rows are for
the Info tab to DISPLAY. They are deliberately not written to camp_fact and
carry no evidence rows, because they are not claims about the world -- they
are a copy of a sheet, and the sheet is the authority.

Usage:
    python parse_shift_grid.py ./output/ps_knowledge.db
    python parse_shift_grid.py ./output/ps_knowledge.db --dry-run
"""
import argparse
import csv
import re
import sqlite3
import sys
from pathlib import Path

DEFAULT_INPUT = Path("~/Snails/PS Processed")

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday",
        "saturday", "sunday"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS shift_grid (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    year        INTEGER,
    category    TEXT,          -- "Maintenance", "Cooking", ...
    shift       TEXT NOT NULL, -- "Burn Barrel"
    description TEXT,
    time_slot   TEXT,          -- "9-12p", "Any time (3 hrs)"
    day         TEXT NOT NULL, -- "Monday"
    person      TEXT NOT NULL,
    source_file TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS shift_grid_person ON shift_grid(person);
CREATE INDEX IF NOT EXISTS shift_grid_day ON shift_grid(day);
"""

# A cell that is a note to the reader rather than somebody's name.
NOT_A_NAME = re.compile(
    r"^\s*$|^\d+$|^(x|n/a|tbd|tbc|none|open|free|any|all)$"
    r"|please|sign ?up|^see |^note", re.I)


def find_day_header(rows: list[list[str]]) -> tuple[int, dict[int, str]] | None:
    """The row that names the days, and where each day starts.

    Found by content rather than by position: a sheet gains a row above the
    header every time somebody adds an instruction at the top, and hardcoding
    "row 5" would break on the next edit.
    """
    for i, row in enumerate(rows[:25]):
        hits = {}
        for col, cell in enumerate(row):
            name = cell.strip().lower()
            if name in DAYS:
                hits[col] = cell.strip()
        if len(hits) >= 3:
            return i, hits
    return None


def day_ranges(header: dict[int, str], width: int) -> list[tuple[int, int, str]]:
    """Each day as [start, end) columns, running to the next day's header."""
    cols = sorted(header)
    out = []
    for n, col in enumerate(cols):
        end = cols[n + 1] if n + 1 < len(cols) else width
        out.append((col, end, header[col]))
    return out


def year_from(path: Path) -> int | None:
    m = re.search(r"(20\d\d)", path.name)
    return int(m.group(1)) if m else None


def parse(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        rows = [list(r) for r in csv.reader(fh)]
    found = find_day_header(rows)
    if not found:
        return []
    header_row, header = found
    width = max(len(r) for r in rows)
    ranges = day_ranges(header, width)
    year = year_from(path)

    out: list[dict] = []
    category = None
    for row in rows[header_row + 1:]:
        cell = lambda i: row[i].strip() if i < len(row) else ""
        shift, description, time_slot = cell(1), cell(2), cell(3)
        if not shift:
            continue
        # A label with nothing else on the line is a section heading, and it
        # names the shifts under it until the next one.
        if not time_slot and not description:
            category = shift
            continue
        for start, end, day in ranges:
            for col in range(start, end):
                person = cell(col)
                if not person or NOT_A_NAME.match(person):
                    continue
                out.append({
                    "year": year, "category": category, "shift": shift,
                    "description": description or None,
                    "time_slot": time_slot or None, "day": day,
                    "person": person, "source_file": path.name,
                })
    return out


def run(db_path: str, input_dir: Path, dry: bool) -> int:
    grids = [p for p in sorted(input_dir.rglob("*.csv"))
             if re.search(r"shift", p.name, re.I)]
    total = 0
    parsed: list[dict] = []
    for p in grids:
        rows = parse(p)
        if rows:
            print(f"  {len(rows):>4} assignments  {p.name}")
            parsed += rows
        total += len(rows)

    if not parsed:
        print("no shift grids found")
        return 0

    if dry:
        print(f"\n(dry run) {total} assignments across {len(grids)} sheets")
        for r in parsed[:8]:
            print(f"    {r['day']:<10} {r['time_slot'] or '—':<16} "
                  f"{r['shift'][:26]:<28} {r['person']}")
        return total

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        # Rebuilt wholesale per source file: a sheet is edited constantly and
        # a half-updated rota is worse than a stale one.
        for p in grids:
            conn.execute("DELETE FROM shift_grid WHERE source_file = ?", (p.name,))
        conn.executemany(
            "INSERT INTO shift_grid (year, category, shift, description,"
            " time_slot, day, person, source_file)"
            " VALUES (:year, :category, :shift, :description, :time_slot,"
            " :day, :person, :source_file)", parsed)
        conn.commit()
        print(f"\n{total} shift assignments written")
    finally:
        conn.close()
    return total


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("db")
    ap.add_argument("--input", default=str(DEFAULT_INPUT))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.dry_run and not Path(args.db).exists():
        raise SystemExit(f"no database at {args.db}")
    run(args.db, Path(args.input), args.dry_run)


if __name__ == "__main__":
    main()
