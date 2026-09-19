#!/usr/bin/env python3
"""Refuse to ship a database that still contains personal data.

`redact()` in build_preview_db.py is what removes home addresses and card
digits from the camp's order confirmations. This checks the *artifact* rather
than the script, because the artifact is what goes into the IPA and out to
other people's phones -- a passing test on the redaction function proves
nothing about a database file built before that function existed.

Roughly a third of `camp_knowledge` is receipts, and they stay: "Med kit
supplies" is the only document in the corpus containing the word "tourniquet",
so deleting them costs the camp its only inventory record. Redaction, never
deletion -- see docs/no-fact-documents-findings.md.

What deliberately stays: gate codes and padlock combinations. The alpha goes to
camp members and that information is the point of the app.

What must never ship: an individual's home address, card digits, bank or order
numbers, email addresses or phone numbers. Contact details used to be on the
"stays" list, on the reasoning that a camp roster is what the app is for. They
came off it on 2026-08-10: a phone number is no use in a place with no signal,
and it is somebody's whether or not it is useful.

Since 2026-08-11 this gate is also structural: an artifact missing the
general_knowledge, lore or ask_word layers is refused before any text is
scanned, because the Swift consumers degrade silently when a table is absent
and a missing layer already shipped once without anything looking broken.

    python3 scripts/check_shipped_db.py scripts/output/enriched_preview.db

Exits non-zero, loudly, with the rows it objects to.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

# Each is a thing that must not survive redaction. The names are what gets
# printed, so they are phrased as the objection.
# Never legitimate, anywhere in the database.
ALWAYS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("a card number", re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{1,4}\b")),
    ("card last-four", re.compile(r"\b(?:ending\s+in|last\s*4|xxxx[ -]?)\s*\d{4}\b",
                                  re.IGNORECASE)),
    ("an order number", re.compile(r"\b\d{3}-\d{7}-\d{7}\b")),
    # A consumer email provider is an individual on its own evidence. Business
    # domains are not checked here: the camp's suppliers are supposed to ship,
    # and a gate that refuses them would be trained around within a week.
    ("a personal email address", re.compile(
        r"\b[A-Za-z0-9._%+\-]+@(?:gmail|hotmail|yahoo|icloud|me|mac|outlook"
        r"|live|aol|protonmail|gmx|msn|ymail)\.[A-Za-z]{2,}\b", re.IGNORECASE)),
    # A number under a label that marks it as a person's. "Telephone:" and
    # "Toll Free:" are how a business prints its own and are left alone.
    #
    # Bare "Phone:" is NOT in this list any more; it is checked separately,
    # because it writes both a campmate and a supplier. The sign-up roster
    # prints a naked `Phone: +15555550100.` with the name in another column,
    # and the manual prints the same label inside the Black Rock Rentals block
    # with the landlord's number in it. See `_BARE_PHONE` below.
    ("a personal phone number", re.compile(
        r"(?:(?:primary|home|cell|mobile|personal|emergency)\s*(?:phone|number)"
        r"|contact\s+(?:details?|numbers?)\s+for"
        r"|reach\s+(?:them|him|her)\b)"
        r"[^\n]{0,40}?"
        # Any 9-to-15-digit run with sparse separators, not just US 3-3-4
        # grouping: the sign-up roster is full of UK, Dutch and Mexican
        # numbers, and a gate that only recognizes San Francisco called them
        # all clean. Kept identical to _PHONE in build_preview_db.py.
        r"(?<![\w.\-/=%#&?])\+?\(?\d(?:[ .\-')(]{0,2}\d){8,14}",
        re.IGNORECASE)),
    # "Melissa - 415-555-0137": a person tied to their number by nothing but a
    # dash, the form a driver sheet takes for someone outside the roster.
    # Case-sensitive on purpose -- under IGNORECASE "[A-Z][a-z]+" is every
    # word there is, and the gate would object to the whole database.
    ("a name-dash phone number", re.compile(
        r"\b[A-Z][a-z]+\s*[-–—]\s*"
        r"(?<![\w.\-/=%#&?])\+?\(?\d(?:[ .\-')(]{0,2}\d){8,14}")),
    ("bank details", re.compile(r"(?:Routing|Account)\s*(?:Number|No\.?|#)?\s*:?\s*\d{6,17}",
                                re.IGNORECASE)),
)

# Bare "Phone:" on its own, plus what tells the two cases apart.
#
# The roster writes `Phone: +15555550100.` and nothing else; ten of those are
# campmates' mobiles in evidence.quote and they must never ship. The manual
# writes the same label inside a block carrying a company name, a trade word
# or a state-and-ZIP -- Black Rock Rentals, HWY 447 MM70 Empire, NV 89405 --
# and that is the landlord of the lot the camp rents, which somebody standing
# at a locked gate needs.
#
# Kept deliberately narrow, and duplicated from build_preview_db rather than
# imported: this file gates the ARTIFACT, so it has to be able to disagree
# with the function that produced it. A false positive here only nags; a false
# negative ships a campmate's mobile to forty phones.
_BARE_PHONE = re.compile(
    r"\bphone\s*[:#][^\n]{0,40}?"
    r"(?<![\w.\-/=%#&?])\+?\(?\d(?:[ .\-')(]{0,2}\d){8,14}",
    re.IGNORECASE)

_BUSINESS_CONTEXT = re.compile(
    r"\b(?:inc|llc|l\.l\.c|ltd|corp|co|company|rentals?|storage|services?"
    r"|supply|supplies|solutions|equipment|hardware|dealer|motors|towing"
    r"|sanitation|portable|propane|lumber)\b\.?"
    r"|\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b",
    re.IGNORECASE)

# How far back to look for the business marker. Wider than the redactor's 90,
# because the company name sits at the top of a contact block and the label at
# the bottom of it.
_BUSINESS_WINDOW = 200


def bare_phone_finding(text: str) -> str | None:
    """The bare-label match, unless a business stands next to it."""
    m = _BARE_PHONE.search(text)
    if not m:
        return None
    lo = max(0, m.start() - _BUSINESS_WINDOW)
    if _BUSINESS_CONTEXT.search(text[lo:m.start()]):
        return None
    return m.group(0)[:60]

# Addresses are checked only where redact() actually removes them: inside the
# window a shipping/billing label opens, in a document. Run wider than that and
# the gate objects to the camp's own places -- the Monument warehouse at 140
# 9th St, the Fernley dump, the Tahoe decom house. Those are camp operational
# information, they are the answer to real questions, and they ship.
ADDRESSES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("a street address", re.compile(
        r"\b\d{1,6}\s+[A-Za-z0-9.'#\-]+(?:\s+[A-Za-z0-9.'#\-]+){0,4}?\s+"
        r"(?:ST|STREET|AVE|AVENUE|RD|ROAD|BLVD|DR|DRIVE|LN|LANE|WAY|"
        r"CT|COURT|PL|PLACE|TER|TERRACE|HWY|HIGHWAY|PKWY|CIR|CIRCLE)\b",
        re.IGNORECASE)),
    ("a city/state/ZIP line", re.compile(
        r"\b[A-Z][A-Za-z.\- ]{2,30},\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?\b")),
)

# Kept identical to build_preview_db.py. If redaction's scope changes, this
# must change with it or the gate stops testing what redaction promises.
_LABEL = re.compile(
    r"(?:shipping|billing|mailing|delivery)\s*(?:address|info(?:rmation)?)\b"
    r"|\b(?:ship|bill)\s*to\b", re.IGNORECASE)
_TRAILING = re.compile(
    r"shipping\s*speed\b|payment\s*(?:information|method)\b", re.IGNORECASE)
_WINDOW = 200

# Where a labelled address block can plausibly appear. Documents, because a
# third of them are receipts; lore, because its stories are model prose
# retold from chat and prose can reproduce a pasted label block wholesale.
# The scan stays inside label windows here as everywhere, so the camp's own
# addresses in a story's plain prose -- the Monument warehouse, the Fernley
# dump, the Tahoe decom house -- pass, exactly as they do in a document.
ADDRESS_TABLES = {"camp_knowledge", "lore"}


def label_windows(text: str) -> list[tuple[int, int]]:
    """The regions redact() rewrites -- where a personal address may hide."""
    spans = [(m.end(), min(len(text), m.end() + _WINDOW))
             for m in _LABEL.finditer(text)]
    spans += [(max(0, m.start() - _WINDOW), m.start())
              for m in _TRAILING.finditer(text)]
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]

def tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


# --------------------------------------------------------------------------
# Build over build
# --------------------------------------------------------------------------
#
# Every check above asks "is this artifact internally sound". None of them
# asks "is it as good as the last one", and on 2026-08-20 that cost a day:
# swapping the enrichment model dropped entity discovery from 149 to 64 --
# the camp's traditions from 70 to 24, taking Edd's law, decomrecom, Meatany
# and the burn book with them -- and every gate stayed green. It was caught
# because somebody happened to be watching a number scroll past.
#
# A fixed floor cannot do this job. ingest_all.py has floors and they work,
# but they were hand-set years ago against counts nobody re-derives, and a
# floor of 500 on a corpus that is deliberately 351 fails for the wrong
# reason. What is wanted is a comparison against the last build that a human
# accepted, so a large swing has to be acknowledged rather than noticed.
#
# The baseline is a committed file, so the acceptance is a diff in the commit
# rather than a state on somebody's laptop.

BASELINE = Path(__file__).resolve().parent / "build_baseline.json"

# 25%. Today's regression was 57% and the deliberate document trim was 44%,
# so this catches both -- and catching a deliberate change is correct: the
# point is that a big move gets acknowledged, not that it gets prevented.
SHRINK_LIMIT = 0.25


def measure(conn, present: set[str]) -> dict[str, int]:
    counts = {}
    for table in sorted(present):
        if table.startswith("sqlite_"):
            continue
        counts[table] = conn.execute(
            f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return counts


def compare_to_baseline(counts: dict[str, int]) -> list[str]:
    """Complaints about shrinkage since the last accepted build.

    Silent when no baseline exists: a first build has nothing to regress
    from, and refusing one would make the check impossible to adopt.
    """
    if not BASELINE.exists():
        print("no build_baseline.json yet -- run with --accept to record "
              "this build as the baseline")
        return []

    old = json.loads(BASELINE.read_text()).get("counts", {})
    complaints = []
    print("\ntable                       baseline      now    change")
    print("-" * 58)
    for table in sorted(set(old) | set(counts)):
        was, now = old.get(table), counts.get(table)
        if was is None:
            print(f"{table:<26} {'--':>9} {now:>8}    new")
            continue
        if now is None:
            complaints.append(f"  {table} has VANISHED (was {was} rows)")
            print(f"{table:<26} {was:>9} {'GONE':>8}    !!")
            continue
        delta = (now - was) / was if was else 0.0
        flag = ""
        if was and delta <= -SHRINK_LIMIT:
            flag = "  SHRANK"
            complaints.append(
                f"  {table}: {was} -> {now} rows "
                f"({delta*100:.0f}%), past the {SHRINK_LIMIT*100:.0f}% limit")
        print(f"{table:<26} {was:>9} {now:>8} {delta*100:>+7.0f}%{flag}")
    return complaints


def write_baseline(counts: dict[str, int], path: str) -> None:
    BASELINE.write_text(json.dumps(
        {"source": path, "counts": counts}, indent=2, sort_keys=True) + "\n")
    print(f"\nbaseline written to {BASELINE.name} "
          f"({len(counts)} tables) -- commit it")


def main(path: str, accept: bool = False) -> int:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    present = tables(conn)

    # Structure before content: the layers the app reads have to exist in the
    # artifact at all. general_knowledge shipped absent for weeks -- bm_basics
    # was a manual step nothing invoked, every rebuild dropped it, and
    # searchGeneral returned [] on device with nothing looking broken. The
    # Swift consumers degrade silently by design when a table is missing,
    # which is exactly why the gate cannot: silence on the phone is the
    # failure mode, so the artifact is where it has to be loud.
    #
    # general_knowledge must be present AND non-empty -- its rows are written
    # by the build itself, so empty means the build step was skipped. lore and
    # ask_word need only be present: their stages may legitimately not have
    # run yet, but an absent table means the schema never made it in and the
    # app will read nothing without saying so.
    structural: list[str] = []
    if "general_knowledge" not in present:
        structural.append("  general_knowledge table is missing -- the build "
                          "never wrote the background layer")
    elif conn.execute("SELECT COUNT(*) FROM general_knowledge").fetchone()[0] == 0:
        structural.append("  general_knowledge is empty -- the build wrote "
                          "the table and no rows")
    for required in ("lore", "ask_word"):
        if required not in present:
            structural.append(f"  {required} table is missing -- the schema "
                              f"never reached the artifact")

    # E7's vectors, checked the way package_db.py checks the corpus: measured
    # from the file, never asserted from a constant. A manifest that agrees
    # with itself catches nothing.
    #
    # Absent is allowed -- a database built before embed_claims.py existed is
    # a valid artifact and the phone falls back to lexical retrieval. Present
    # but wrong is not, and it is the dangerous case: cosine between vectors
    # of mismatched dimension or from a different encoder is just a number, so
    # nothing fails, the app simply answers from the wrong rows.
    if "claim_embedding" in present:
        EXPECT_MODEL = "embeddinggemma-300M-Q8_0"
        EXPECT_BYTES = 768 * 4          # 768 little-endian Float32
        models = [r[0] for r in conn.execute(
            "SELECT DISTINCT model_name FROM claim_embedding")]
        if len(models) > 1:
            structural.append(
                f"  claim_embedding mixes encoders {sorted(models)} -- vectors "
                f"from two models are not comparable and cosine will not say so")
        elif models and models[0] != EXPECT_MODEL:
            structural.append(
                f"  claim_embedding was built with {models[0]}, but the app "
                f"looks for {EXPECT_MODEL} and will find nothing")
        widths = [r[0] for r in conn.execute(
            "SELECT DISTINCT length(embedding) FROM claim_embedding")]
        if [w for w in widths if w != EXPECT_BYTES]:
            structural.append(
                f"  claim_embedding holds vectors of {sorted(widths)} bytes; "
                f"the app reads {EXPECT_BYTES} and skips anything else")
        # Coverage, per table. A row without a vector is invisible to semantic
        # recall and looks exactly like a row that merely matched poorly.
        for claim_table, column in (("camp_fact", "fact"),
                                    ("entity_fact", "fact"),
                                    ("lore", "story")):
            if claim_table not in present:
                continue
            want = conn.execute(
                f"SELECT COUNT(*) FROM {claim_table}"
                f" WHERE {column} IS NOT NULL AND {column} != ''").fetchone()[0]
            got = conn.execute(
                "SELECT COUNT(*) FROM claim_embedding WHERE claim_table = ?",
                (claim_table,)).fetchone()[0]
            if got != want:
                structural.append(
                    f"  claim_embedding covers {got} of {want} {claim_table} "
                    f"rows -- rerun scripts/embed_claims.py")
    if structural:
        conn.close()
        print(f"REFUSING TO SHIP {path}", file=sys.stderr)
        print("the artifact is structurally incomplete:\n", file=sys.stderr)
        for line in structural:
            print(line, file=sys.stderr)
        print("\nRebuild with scripts/build_preview_db.py -- a layer the app "
              "reads never made it in.", file=sys.stderr)
        return 1

    findings: list[str] = []
    scanned = 0

    # Every text column of every table, not a hand-listed few. The list was
    # how this gate called a database clean while camp_member sat there with
    # 291 email addresses in it: coverage by memory, in the same script whose
    # whole purpose is not to rely on memory.
    for table in sorted(present):
        if table.startswith("sqlite_"):
            continue
        info = list(conn.execute(f"PRAGMA table_info({table})"))
        usable = [r[1] for r in info
                  if (r[2] or "").upper() in ("TEXT", "") or "CHAR" in (r[2] or "").upper()]
        if not usable:
            continue
        query = f"SELECT rowid, {', '.join(usable)} FROM {table}"
        for row in conn.execute(query):
            rowid, values = row[0], row[1:]
            for column, value in zip(usable, values):
                if not value:
                    continue
                scanned += 1
                text = str(value)
                for label, pattern in ALWAYS:
                    m = pattern.search(text)
                    if m:
                        findings.append(
                            f"  {table}.{column} rowid={rowid} contains {label}: "
                            f"{m.group(0)[:60]!r}")
                bare = bare_phone_finding(text)
                if bare:
                    findings.append(
                        f"  {table}.{column} rowid={rowid} contains a personal "
                        f"phone number: {bare!r}")
                if table not in ADDRESS_TABLES:
                    continue
                for start, end in label_windows(text):
                    window = text[start:end]
                    for label, pattern in ADDRESSES:
                        m = pattern.search(window)
                        if m:
                            findings.append(
                                f"  {table}.{column} rowid={rowid} has {label} "
                                f"under a shipping label: {m.group(0)[:60]!r}")

    counts = measure(conn, present)
    conn.close()

    shrinkage = compare_to_baseline(counts)
    if shrinkage and not accept:
        print(f"\nREFUSING TO SHIP {path}", file=sys.stderr)
        print(f"{len(shrinkage)} table(s) shrank past "
              f"{SHRINK_LIMIT*100:.0f}% since the last accepted build:\n",
              file=sys.stderr)
        for line in shrinkage:
            print(line, file=sys.stderr)
        print("\nIf the change is intended, re-run with --accept to move the "
              "baseline and commit it.\nIf it is not, this is the regression "
              "the check exists for.", file=sys.stderr)
        return 1

    if findings:
        print(f"REFUSING TO SHIP {path}", file=sys.stderr)
        print(f"{len(findings)} row(s) still carry personal data:\n",
              file=sys.stderr)
        # Capped: a broken redaction pass produces hundreds and the first
        # dozen are enough to see which pattern stopped matching.
        for line in findings[:12]:
            print(line, file=sys.stderr)
        if len(findings) > 12:
            print(f"  ... and {len(findings) - 12} more", file=sys.stderr)
        print("\nFix redact() in scripts/build_preview_db.py and rebuild.",
              file=sys.stderr)
        return 1

    print(f"clean: {scanned} text fields checked, nothing personal found")
    if accept:
        write_baseline(counts, path)
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    accept = "--accept" in sys.argv[1:]
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(args[0], accept=accept))
