#!/usr/bin/env python3
"""
Unzip every WhatsApp export, ingest all of them, and reconcile the row count
against the raw line count so silent parser losses can never go unnoticed again.

Usage:
    python ingest_all.py "../Whatsapp PS Exports" ./output
"""
import re
import sys
import shutil
import sqlite3
import zipfile
import tempfile
from pathlib import Path

from parse_chats import ChatParser, write_database
from parse_docs import run as parse_docs_run

MESSAGE_LINE = re.compile(r'^[‎‏]*\[\d{1,2}/\d{1,2}/\d{2,4},')

# Floors deliberately sit below the known-good reference counts (601 / 790 /
# 2087, from LucyPT/LucyPT/ps_knowledge.db) so ordinary source-data drift
# doesn't trip the guard, while a table vanishing entirely (the Task 3.5
# defect this function exists to catch) always does.
EXPECTED_TABLES = {
    'person': 1,
    'person_content': 20000,
    # 120, not 300, not the original 500.
    #
    # The 500 floor was calibrated against an untrimmed 627-document corpus of
    # which 172 were finance and 157 were food, against 3 for water and 2 for
    # power. parse_docs.should_ingest() now drops 276 of those -- ledgers,
    # Venmo exports, grocery lists, sign-up grids -- and the honest count is
    # 351. The floor tripped on the very first run after the trim, correctly:
    # it could not tell a deliberate cut from a broken ingest.
    #
    # This number has now moved twice in one day, both times because the RULE
    # changed and not because the guard was inconvenient: 627 documents became
    # 351, then 144, as should_ingest() tightened. Expected is 144, so 120
    # leaves ~17% of headroom for source drift.
    #
    # A broken ingest does not return 120 documents, it returns nearly none,
    # so the failure this exists to catch still trips it. Move this when the
    # rule moves. Never to make a red line go green.
    'camp_knowledge': 120,
    'roster': 700,
    # 26, not 2,000.
    #
    # The old floor was calibrated against a shifts table that was 60%
    # shopping lists and bank transactions -- 2,140 rows in which person_name
    # and role held the same spreadsheet cell, day was mostly null, and only
    # 117 had a time slot. parse_docs stopped inventing those on 2026-08-20
    # and the honest count collapsed.
    #
    # Deliberately not raised back. The genuine sign-up sheets are 2-D GRIDS --
    # names spread across "Name 1/2/3" or "Role 1/2/3" columns, and the 2018
    # sheet has a sentence where its header row should be -- so a row-oriented
    # parser cannot read them, and yields almost nothing rather than fiction.
    # Real shift data needs a grid parser feeding the Info tab, unwritten.
    #
    # This floor still does its job: catching a table that vanished entirely.
    'shifts': 10,
}


def check_expected_tables(db_path: Path) -> None:
    """Fail loudly if any expected table is missing or below its floor."""
    conn = sqlite3.connect(db_path)
    try:
        failures = []
        print(f"\n{'TABLE':<20}{'COUNT':>10}{'FLOOR':>10}  STATUS")
        print('-' * 52)
        for table, floor in EXPECTED_TABLES.items():
            try:
                count = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
            except sqlite3.OperationalError:
                count = None
            ok = count is not None and count >= floor
            shown = 'MISSING' if count is None else count
            print(f"{table:<20}{shown:>10}{floor:>10}  {'OK' if ok else 'FAIL'}")
            if not ok:
                failures.append((table, count, floor))
    finally:
        conn.close()

    if failures:
        print("\nERROR: the following tables are missing or below their floor:",
              file=sys.stderr)
        for table, count, floor in failures:
            got = 'missing entirely' if count is None else f"got {count}"
            print(f"  {table}: {got}, need >= {floor}", file=sys.stderr)
        sys.exit(1)


def raw_line_count(path: Path) -> int:
    """Count every line that starts a message, LTR-prefixed or not."""
    with open(path, encoding='utf-8') as f:
        return sum(1 for line in f if MESSAGE_LINE.match(line))


def main():
    export_dir = Path(sys.argv[1])
    output_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path('./output')
    output_dir.mkdir(parents=True, exist_ok=True)

    parser = ChatParser()
    report = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for zpath in sorted(export_dir.glob('*.zip')):
            dest = tmp / zpath.stem
            dest.mkdir()
            with zipfile.ZipFile(zpath) as z:
                z.extractall(dest)

            txts = list(dest.glob('*.txt'))
            if not txts:
                print(f"WARNING: no .txt inside {zpath.name}", file=sys.stderr)
                continue

            source = zpath.stem.replace('WhatsApp Chat - ', '')
            before = len(parser.messages)
            parser.parse_file(txts[0], source)
            parsed = len(parser.messages) - before
            raw = raw_line_count(txts[0])
            report.append((source, raw, parsed))

    print(f"\n{'GROUP':<34}{'RAW':>8}{'KEPT':>8}{'DROPPED':>9}{'%':>7}")
    print('-' * 66)
    tr = tk = 0
    for source, raw, parsed in report:
        drop = raw - parsed
        pct = (drop / raw * 100) if raw else 0
        print(f"{source:<34}{raw:>8}{parsed:>8}{drop:>9}{pct:>6.1f}%")
        tr += raw
        tk += parsed
    print('-' * 66)
    print(f"{'TOTAL':<34}{tr:>8}{tk:>8}{tr - tk:>9}{(tr - tk) / tr * 100:>6.1f}%")

    # Legitimate drop is two things: administrative system notices (~3.6%:
    # "X added Y", "joined using a group link", "This message was deleted",
    # "Waiting for this message...") plus media placeholders (~4.4%: "image
    # omitted", "video omitted", etc. -- dropped per the owner's decision not
    # to pollute the semantic index with ~1,162 near-identical placeholder
    # strings). Together that's ~8.1% of raw lines, so the guard sits at 9%:
    # about 1pp of headroom, not a lot, so a small composition shift can trip
    # it. Individual groups can and do run above 9% on their own (PS 9.5%,
    # PS Berlin 21.8% on a tiny base) -- it's the TOTAL that's guarded. If
    # the drop rate creeps past this, check whether the admin/placeholder
    # split above still explains it before touching the threshold again.
    if (tr - tk) / tr > 0.09:
        print("\nERROR: more than 9% of message lines were dropped.", file=sys.stderr)
        print("Legitimate drop is administrative notices (~3.6%) plus media "
              "placeholders (~4.4%, e.g. 'image omitted'), ~8.1% total. "
              "Investigate before shipping -- and before raising this "
              "threshold again.",
              file=sys.stderr)
        sys.exit(1)

    db_path = output_dir / 'ps_knowledge.db'
    if db_path.exists():
        shutil.move(str(db_path), str(db_path) + '.bak')
    write_database(parser, output_dir)
    print(f"\nWrote {db_path}")

    # Stage 2: camp documentation → camp_knowledge, roster, shifts
    #
    # This MUST run after the chat ingest above, because write_database()
    # (via create_database()) recreates the file from scratch and would
    # otherwise destroy its tables. Encoding the order here rather than in
    # the README is deliberate: an earlier version of this pipeline
    # documented the order in prose and silently shipped a database missing
    # 601 knowledge rows, 790 roster rows and 2,087 shift rows. It is
    # imported and called directly (not subprocess) so a failure raises
    # instead of being swallowed by an ignored exit code.
    #
    # A stage 3 used to follow: six Markdown files of pasted LLM output about
    # Burning Man in general, from the January design. Removed 2026-09-19;
    # general knowledge is the model's own now (see the invariant in CLAUDE.md)
    # and hand-written camp facts go in manual_facts.json.
    print("\nStage 2/2: parsing camp documents into camp_knowledge/roster/shifts...")
    parse_docs_run(db_path=db_path)

    check_expected_tables(db_path)
    print(f"\nCorpus build complete: {db_path}")


if __name__ == '__main__':
    main()
