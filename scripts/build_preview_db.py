#!/usr/bin/env python3
"""
Build a slim, device-sized database carrying the full enrichment layer.

The preview app bundles a knowledge database as a resource. The working corpus
is 105 MB, almost all of it embeddings and messages the app never reads — the
ASK screens need entities, facts, and the exact source rows the evidence cites.
This copies the enrichment tables whole and brings across only the corpus rows
that are actually referenced, which is a few thousand out of 24,192.

Same schema as the corpus and as the hand-written fixture, so an app built
against either reads this one with no code change.

Usage:
    python build_preview_db.py [--src output/ps_knowledge.db]
                               [--out output/enriched_preview.db]
"""
import argparse
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bm_basics import write_general_knowledge
from enrich_schema import create_enrichment_tables, verify_evidence_complete

HERE = Path(__file__).resolve().parent

# Order matters: `evidence` has a trigger checking that its source row exists,
# so camp_member has to be in place before the evidence citing it arrives.
# ask_word rides along with camp_fact: it is not a claim table and carries no
# evidence, but a fact whose ask words were left behind is unfindable by
# exactly the vocabulary the words were generated to catch.
ENRICHMENT_TABLES = ("entity", "entity_alias", "entity_fact", "person_profile",
                     "personality", "expertise", "relationship", "lore",
                     "camp_member", "camp_fact", "ask_word", "evidence",
                     # The names a camper is actually called. Useless without
                     # the person rows they point at, so they travel together.
                     "person_alias",
                     # E7. Vectors for camp_fact, entity_fact and lore, so the
                     # phone can reach a row that shares no words with the
                     # question. They are useless without the rows they point
                     # at, so they ship in the same list and by the same rule.
                     "claim_embedding",
                     # The rota, as a copy of the camp's sign-up sheet. Not a
                     # claim about the world and carrying no evidence rows, so
                     # it is exempt from the completeness gate -- the Info tab
                     # displays it, Lucy never answers out of it.
                     "shift_grid")

# The roster's contact columns do not go to the device.
#
# This reverses what used to be written here. The old reasoning was that a
# signup-sheet email was *given* to the camp for camp use — unlike a receipt's
# shipping address, which is in the corpus by accident — and that blanking the
# roster while ten phone numbers sat in camp documents was an inconsistency
# rather than a policy. That was true, and the resolution went the other way on
# 2026-08-10: the inconsistency is now fixed by removing both.
#
# What settled it is where the copies are. The corpus keeps everything and is
# backed up to a private bucket; the camp server holds the roster and hands it
# over on request. The device database is the copy that goes to forty phones,
# to Burning Man, in a pocket. It is the one copy where the answer to "who else
# has this" is "nobody knows", so it is the one that carries the least.
_DROP_ON_DEVICE: dict[str, tuple[str, ...]] = {
    "camp_member": ("email", "phone"),
}

# Roughly a third of the documents are order confirmations, and they arrive
# with the buyer's home address and the last four of their card attached.
#
# Deleting those documents is the obvious move and the wrong one: they are the
# only record the camp has of what it owns. "Med kit supplies" is the sole
# document in the corpus containing the word "tourniquet" — drop it and a
# medical question stops having an answer. So the item lists stay and the
# personal data goes.
#
# A blanket street-address pattern is the other obvious move and is also wrong.
# The first version of this matched addresses anywhere, and ate the camp's own:
# the storage unit at 1056 Greg St, the hardware store at 470 S Rock Blvd,
# Empire General at 8 Glendale Ave — the addresses someone driving to Gerlach
# actually needs. It also ate "Heat a skillet to medium-high heat. Place",
# because "Place" is a street suffix.
#
# What separates the two is not the address, it is where it sits. Personal
# addresses appear under a label on a receipt; the camp's appear in prose. So
# addresses are only removed inside a window following an explicit label, and
# the manual is left alone.
#
# This runs on the way to the device, not against the corpus, which is left
# intact for enrichment.

# Never useful to anyone standing in the desert, wherever they appear.
_ALWAYS = (
    # Visa | Last digits: 2775
    (re.compile(r"(Last\s+(?:4\s+)?digits\s*:?\s*)\d{3,4}", re.IGNORECASE),
     r"\1****"),
    # "Visa ending in 1014" -- the same four digits in the sentence form a
    # receipt actually uses. Only the labelled form was covered, and nothing
    # scanned camp_knowledge.content for the other one.
    (re.compile(r"((?:ending\s+in|last\s*4)\s*:?\s*)\d{4}\b", re.IGNORECASE),
     r"\1****"),
    # Amazon.com order number: 111-7249140-5066635
    (re.compile(r"\b\d{3}-\d{7}-\d{7}\b"), "[order number removed]"),
    # The camp's own bank details, pasted into the group chat once.
    (re.compile(r"((?:Routing|Account)\s*(?:Number|No\.?|#)?\s*:?\s*)\d{6,17}",
                re.IGNORECASE), r"\1[removed]"),
    (re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{1,4}\b"), "[card number removed]"),
)

# --- Contact details ------------------------------------------------------
#
# The same lesson as addresses, in a second domain. A phone number is not
# personal because of its shape; it is personal because of whose it is. The
# corpus has 673 of them and they split cleanly: 348 consumer-provider emails
# under "Contact details for <campmate>", against vendor numbers sitting in
# receipts and manuals -- the Honda dealer, 4imprint's toll-free line, the
# insurance broker. Redacting by shape would take the camp's own suppliers out
# with the roster, which is the mistake the address rule already made once.
#
# So: a contact goes only when something says it belongs to a person. A
# consumer email provider says so on its own. Otherwise a roster name or a
# personal label has to sit just before it.

# The second branch is for PDF extraction, which breaks a line inside the
# domain: "PARKER.CHENOWETH@GMAIL. COM" is one address and the plain pattern
# sees no TLD at all. The TLD list is closed on purpose -- allowing any word
# after ". " would eat the first word of the next sentence.
_EMAIL = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+"
    r"(?:\.[A-Za-z]{2,}\b|\.\s(?:com|org|net|edu|gov|io|co\.?\s?uk)\b)",
    re.IGNORECASE)
# Any run of 9 to 15 digits with at most two separator characters between
# them: +1 415 555 1234, (415) 555-1234, 1-443-555-0161, +52 5555 019876,
# 07400123456, 1.314.555.0174 and the bare digits half the roster wrote.
#
# This used to demand US 3-3-4 grouping with a [2-9] area code, which read
# well and silently excluded most of the world: every UK, Dutch, Mexican and
# bare 11-digit US number in the sign-up roster sailed through redaction
# because it was not shaped like San Francisco. Shape does not decide what is
# personal here -- _belongs_to_a_person does -- so the shape's only job is to
# see the number at all, and a loose shape costs nothing but a wider net for
# the context check. Epochs and part numbers still ship: they have nobody
# standing next to them.
#
# The lookbehind rejects a leading word character or URL punctuation, which
# keeps this out of eventbrite ticket ids and appstore /id1554374905 paths --
# digit runs that sit inside an identifier, where a "@Marcus" mention 90
# characters earlier would otherwise get the link eaten.
_PHONE = re.compile(
    r"(?<![\w.\-/=%#&?])\+?\(?\d(?:[ .\-')(]{0,2}\d){8,14}"
    # Not followed by more digits, and not by a separator that leads to more.
    # The old guard was (?![\d.\-]), which also rejected a number at the end of
    # a sentence -- so every "...by phone on +15555550100." was left untouched
    # and the tests never noticed, because none of them ended in a full stop.
    r"(?!\d)(?![.\-]\d)")

# Matched on the provider, not the whole domain: the roster has
# hotmail.co.uk, yahoo.gr and a typo'd gmail.con in it, and a frozenset of
# full domains missed all three.
_CONSUMER_PROVIDER = re.compile(
    r"@(?:gmail|hotmail|yahoo|icloud|me|mac|outlook|live|aol|protonmail|proton"
    r"|gmx|msn|ymail|comcast|sbcglobal|googlemail|rocketmail|mail|web"
    r"|zoho|fastmail|hushmail|inbox|mail.ru)\.", re.IGNORECASE)

# Labels that mark the number as an individual's, on a form or in a roster.
# "Telephone:", "Toll Free:" and "Fax:" are deliberately absent -- those are
# how a business prints its own.
#
# Bare "Phone:" is handled separately, below -- it means both things.
_PERSONAL_LABEL = re.compile(
    r"(?:primary|home|cell|mobile|personal|emergency)\s*(?:phone|number)"
    r"|contact\s+(?:details?|numbers?|info(?:rmation)?)\s+for"
    r"|reach\s+(?:them|him|her|me)\b"
    r"|(?:primary|alternate|personal)\s*email"
    r"|campmates?\b", re.IGNORECASE)

# "Melissa - 415-555-0137" on a driver sheet: a name the roster has never
# heard of, tied to its number by nothing but a dash. A capitalized word and a
# dash directly before the number is how a person is listed; a business prints
# a labelled line or its name on its own line, and neither has the dash.
# Matching a business this way only costs a supplier's number, which is the
# safe direction to be wrong in. Case matters here, so it runs on the raw
# text, not the lowercased window.
_NAME_DASH = re.compile(r"\b[A-Z][a-z]+\s*[-–—]\s*$")

# Filled from the source roster at build time. Empty by default so the tests
# can set exactly the names they mean.
_ROSTER: set[str] = set()

# Bare "Phone:" writes both a campmate and a supplier, so on its own it decides
# nothing. The sign-up roster renders as a naked `Phone: +15555550100.` with
# the name in another column -- ten of those are in `evidence.quote`, and they
# are campmates' mobiles. The manual writes the same label inside a block that
# is obviously a business:
#
#     Black Rock Rentals (fka Burn Pit Storage)
#     Contact details:
#     HWY 447 MM70 Empire, NV 89405
#     Phone: Kieth: +1-775-555-0142
#
# That is the landlord of the lot the camp rents, and somebody standing at a
# locked gate in Empire needs it. What separates them is not the label, it is
# what stands around it -- the same shape-versus-context lesson the address
# rule and the roster rule each learned once already.
#
# Removing the bare label outright was tried and was wrong: it exposed those
# ten roster numbers. `check_shipped_db.py` refused the build, which is what
# it is for -- the count that justified the removal had only been taken over
# `camp_knowledge`, and the roster numbers live in `evidence.quote`.
_BARE_PHONE_LABEL = re.compile(r"\bphone\s*[:#]", re.IGNORECASE)

# A company suffix, a trade word, or a US state-and-ZIP. Any of these standing
# next to a bare "Phone:" makes it a supplier's line rather than a campmate's.
# Deliberately narrow: a false positive here ships a person's number, so this
# only matches things a roster row never contains.
_BUSINESS_CONTEXT = re.compile(
    r"\b(?:inc|llc|l\.l\.c|ltd|corp|co|company|rentals?|storage|services?"
    r"|supply|supplies|solutions|equipment|hardware|dealer|motors|towing"
    r"|sanitation|portable|propane|lumber)\b\.?"
    r"|\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b",
    re.IGNORECASE)

_CONTACT_WINDOW = 90


def set_roster(names) -> None:
    """Every name token the camp roster knows, lowercased.

    A contact detail sitting just after one of these belongs to a person.
    Matching a vendor by accident only costs a supplier's number, which is the
    safe direction to be wrong in.

    **A single character is never a name here.** The roster has a member whose
    last_name is the letter "S", and two more at "T" and "C". Those went in
    whole, because only the word-parts loop below had a length guard -- and
    `_belongs_to_a_person` matches on `\\bs\\b`, which hits the apostrophe-s in
    "Keith's", "It's" and "Curtis'". So virtually every number in every
    document sat next to a "roster name" and was redacted as personal: Keith
    runs the storage lot the camp rents, and "Keith's number is [phone
    removed]" is what the manual shipped.

    The safe direction to be wrong in is redacting a vendor, and this rule
    took that so far it redacted almost all of them. Two characters still
    count -- Oz, BJ, Jo, MC, KC and Lu are real campmates and their names have
    to keep working.
    """
    _ROSTER.clear()
    for name in names:
        if not name:
            continue
        whole = name.strip().lower()
        if len(whole) > 1:
            _ROSTER.add(whole)
        for part in name.split():
            if len(part) > 2 and part[0].isupper():
                _ROSTER.add(part.lower())


def _belongs_to_a_person(text: str, start: int) -> bool:
    lo = max(0, start - _CONTACT_WINDOW)
    if _NAME_DASH.search(text, lo, start):
        return True
    raw = text[lo:start]
    window = raw.lower()
    if _PERSONAL_LABEL.search(window):
        return True
    # A bare "Phone:" decides only when nothing around it says "business".
    # Run on the raw text: the state-and-ZIP branch is case-sensitive.
    if _BARE_PHONE_LABEL.search(window) and not _BUSINESS_CONTEXT.search(raw):
        return True
    return any(re.search(rf"\b{re.escape(n)}\b", window) for n in _ROSTER)


def _local_part_is_a_campmate(address: str) -> bool:
    """chandra@example.org is a campmate at her own domain.

    A consumer provider gives an address away as personal; a custom one does
    not, and the roster name is the only thing left to go on. Four characters
    minimum so "amy" does not match half the alphabet.
    """
    local = address.split("@", 1)[0].lower()
    parts = {p for p in re.split(r"[._\-+0-9]+", local) if len(p) >= 4}
    return bool(parts & _ROSTER)


def _strip_contacts(text: str) -> str:
    out, cursor = [], 0
    marks = sorted(
        [(m.start(), m.end(), "email") for m in _EMAIL.finditer(text)]
        + [(m.start(), m.end(), "phone") for m in _PHONE.finditer(text)])
    for start, end, kind in marks:
        if start < cursor:
            continue
        hit = text[start:end]
        personal = (
            kind == "email"
            and (_CONSUMER_PROVIDER.search(hit) is not None
                 or _local_part_is_a_campmate(hit))
        ) or _belongs_to_a_person(text, start)
        out.append(text[cursor:start])
        out.append(f"[{kind} removed]" if personal else hit)
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


# Labels that introduce someone's personal address on a receipt or invoice.
_LABEL = re.compile(
    r"(?:shipping|billing|mailing|delivery)\s*(?:address|info(?:rmation)?)\b"
    r"|\b(?:ship|bill)\s*to\b", re.IGNORECASE)

# On a multi-page order the address repeats at each page break, and the PDF
# extraction drops the label when it does — but never the line that follows it.
# These close an address block rather than opening one, so their window runs
# backwards.
_TRAILING = re.compile(
    r"shipping\s*speed\b|payment\s*(?:information|method)\b", re.IGNORECASE)

# Applied only inside the window a label opens.
_ADDRESS = (
    # 1272 RHODE ISLAND ST UNIT 19
    re.compile(r"\b\d{1,6}\s+[A-Za-z0-9.'#\-]+(?:\s+[A-Za-z0-9.'#\-]+){0,4}?\s+"
               r"(?:ST|STREET|AVE|AVENUE|RD|ROAD|BLVD|DR|DRIVE|LN|LANE|WAY|"
               r"CT|COURT|PL|PLACE|TER|TERRACE|HWY|HIGHWAY|PKWY|CIR|CIRCLE)\b"
               r"(?:\s+(?:UNIT|APT|STE|SUITE|#)\s*[\w\-]+)?", re.IGNORECASE),
    # SAN FRANCISCO, CA 94107-4405
    re.compile(r"\b[A-Z][A-Za-z.\- ]{2,30},\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?\b"),
)

# Long enough to cover a name, street, city/state/ZIP and country; short enough
# not to run into the item list that follows.
#
# 200 at first, which was sized for an order confirmation. An insurance form
# puts two addresses under one "Mailing Address" heading and the second sat at
# ~260 -- redacted the producer's line and left the next one standing. The gate
# scans a wider window than this did, which is the only reason anyone noticed.
_WINDOW = 340


def redact(text: str | None) -> str | None:
    """Strip an individual's details from a document.

    Card, order and bank numbers go everywhere. Addresses and contact details
    go only where something marks them as a person's -- a receipt label, a
    roster name, a consumer email provider. The camp's own places and its
    suppliers' numbers stay, because those are what somebody in the desert
    actually needs.
    """
    if not text:
        return text

    for pattern, replacement in _ALWAYS:
        text = pattern.sub(replacement, text)

    text = _strip_contacts(text)

    # Collect the windows first; editing while scanning would shift positions.
    spans = [(m.end(), min(len(text), m.end() + _WINDOW))
             for m in _LABEL.finditer(text)]
    spans += [(max(0, m.start() - _WINDOW), m.start())
              for m in _TRAILING.finditer(text)]
    if not spans:
        return text

    # Merge overlapping windows so a run of labels is handled once.
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    out, cursor = [], 0
    for start, end in merged:
        out.append(text[cursor:start])
        chunk = text[start:end]
        for pattern in _ADDRESS:
            chunk = pattern.sub("[address removed]", chunk)
        out.append(chunk)
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", default="output/ps_knowledge.db")
    ap.add_argument("--out", default="output/enriched_preview.db")
    args = ap.parse_args(argv)

    src_path = HERE / args.src if not Path(args.src).is_absolute() else Path(args.src)
    out_path = HERE / args.out if not Path(args.out).is_absolute() else Path(args.out)
    if not src_path.exists():
        raise SystemExit(f"no database at {src_path}")
    if out_path.exists():
        out_path.unlink()

    # Migrate the source before copying a single row. The copy below is a
    # positional INSERT — SELECT * out of the source, ? placeholders into the
    # preview's freshly created schema — so the two sides must agree on column
    # count. A source built before entity_alias grew its `source` column is
    # one column short of the preview's schema and the copy dies with a
    # column-count error that names neither the table's age nor the migration
    # that fixes it. create_enrichment_tables carries the ALTER TABLE
    # migrations and is a no-op on a current source.
    mig = sqlite3.connect(str(src_path))
    create_enrichment_tables(mig)
    mig.close()

    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    out = sqlite3.connect(str(out_path))

    # Before anything is redacted: a contact detail is personal when it sits
    # beside one of these.
    # Both rosters. person and camp_member do not hold the same names -- the
    # first is who has spoken in the chats, the second is who signed up -- and
    # drawing only from person left the sign-up-only members' addresses on the
    # device at their own domains.
    def _names():
        for q in ("SELECT name FROM person WHERE name IS NOT NULL",
                  "SELECT name FROM camp_member WHERE name IS NOT NULL",
                  "SELECT first_name FROM camp_member WHERE first_name IS NOT NULL",
                  "SELECT last_name FROM camp_member WHERE last_name IS NOT NULL",
                  "SELECT nickname FROM camp_member WHERE nickname IS NOT NULL"):
            try:
                yield from (r[0] for r in src.execute(q))
            except sqlite3.OperationalError:
                continue

    set_roster(_names())

    # Corpus tables the app reads, created empty and filled selectively below.
    out.executescript("""
        CREATE TABLE person (
            id INTEGER PRIMARY KEY, name TEXT, email TEXT, phone TEXT,
            message_count INTEGER, first_seen TEXT, last_seen TEXT);
        CREATE TABLE person_content (
            id INTEGER PRIMARY KEY, person_id INTEGER, content TEXT, source TEXT,
            content_type TEXT, timestamp TEXT, media_ref TEXT);
        CREATE TABLE camp_knowledge (
            id INTEGER PRIMARY KEY, title TEXT, content TEXT, source_file TEXT,
            category TEXT, year INTEGER);
    """)

    # Which corpus rows does the evidence actually point at?
    cited_msgs = {r[0] for r in src.execute(
        "SELECT DISTINCT source_id FROM evidence WHERE source_table='person_content'")}
    cited_docs = {r[0] for r in src.execute(
        "SELECT DISTINCT source_id FROM evidence WHERE source_table='camp_knowledge'")}

    def copy_rows(table: str, ids: set[int], cols: int,
                  redact_cols: tuple[int, ...] = (),
                  blank_cols: tuple[int, ...] = (),
                  redact_text: bool = False) -> None:
        if not ids:
            return
        ph = ",".join("?" * len(ids))
        rows = src.execute(
            f"SELECT * FROM {table} WHERE id IN ({ph})", tuple(ids)).fetchall()
        if redact_cols or blank_cols or redact_text:
            rows = [tuple(None if i in blank_cols
                          else redact(v)
                          if (i in redact_cols
                              or (redact_text and isinstance(v, str)))
                          else v
                          for i, v in enumerate(row)) for row in rows]
        out.executemany(
            f"INSERT INTO {table} VALUES ({','.join('?' * cols)})", rows)

    # Chat messages get the same treatment: the camp's bank routing and account
    # numbers were pasted into the group chat once, and that is worse on a lost
    # phone than any home address.
    copy_rows("person_content", cited_msgs, 7, redact_cols=(2,))

    # Every document, not just the cited ones. The camp manual is the answer to
    # a whole class of question — "how do we get water" is in the manual, not in
    # anyone's chat message — and shipping only evidence-cited docs meant the
    # phone carried none of it at all. All 616 are 2.6 MB of text.
    # Title as well as content: a third of these documents are titled with the
    # order number they are about.
    all_docs = {r[0] for r in src.execute("SELECT id FROM camp_knowledge")}
    copy_rows("camp_knowledge", all_docs | cited_docs, 6, redact_text=True)

    # Everyone a profile, expertise row or relationship names, plus the authors
    # of every message we brought across.
    people = {r[0] for r in out.execute(
        "SELECT DISTINCT person_id FROM person_content WHERE person_id IS NOT NULL")}
    # personality included: a portrait whose person row was left behind shows
    # up in the People tab as a card with no name.
    for q in ("SELECT person_id FROM person_profile",
              "SELECT person_id FROM personality",
              "SELECT person_id FROM expertise",
              "SELECT person_a FROM relationship",
              "SELECT person_b FROM relationship"):
        people |= {r[0] for r in src.execute(q) if r[0] is not None}
    # Columns 2 and 3 are email and phone. Emptied rather than redacted:
    # nothing in the app reads them, and a contact detail is no use in a place
    # with no signal. The corpus keeps them; the device does not get them.
    copy_rows("person", people, 7, blank_cols=(2, 3))

    create_enrichment_tables(out)
    for table in ENRICHMENT_TABLES:
        cur = src.execute(f"SELECT * FROM {table}")
        rows = cur.fetchall()
        if not rows:
            continue

        # Carry the table's own CREATE across if the destination lacks it.
        #
        # The output schema is three hand-written CREATEs plus whatever
        # create_enrichment_tables() makes. shift_grid is written by
        # parse_shift_grid.py and is in neither, so adding it to
        # ENRICHMENT_TABLES read 85 rows out of the corpus and died on
        # "no such table: shift_grid" at the destination -- after every one
        # of the twelve enrichment stages had finished.
        #
        # Generalised rather than special-cased: any table added to that
        # tuple now brings its schema with it, so the next one does not
        # repeat this.
        exists = out.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,)).fetchone()
        if not exists:
            ddl = src.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table,)).fetchone()
            if ddl and ddl[0]:
                out.executescript(ddl[0])

        width = len(rows[0])
        drop = _DROP_ON_DEVICE.get(table)
        names = [d[0] for d in cur.description]
        blank = {names.index(c) for c in (drop or ()) if c in names}
        # Vectors are not prose and must not go through redact(). Every
        # text column of every enrichment table is scrubbed below, which is
        # right for facts and wrong for a Float32 BLOB: SQLite would hand it
        # over as bytes, a substitution would corrupt the packing, and the
        # damage would surface as quietly worse retrieval rather than as an
        # error. Nothing in here is prose -- the table is (table, id, blob,
        # model) -- so it is copied through untouched.
        if table == "claim_embedding":
            out.executemany(
                f"INSERT INTO claim_embedding VALUES ({','.join('?' * width)})",
                rows)
            continue
        # Every text column, not a hand-listed few. The enrichment tables were
        # copied straight through, which is how 299 phone numbers and emails
        # reached camp_fact.fact while camp_knowledge was carefully cleaned --
        # redaction applied to the tables someone remembered rather than to
        # everything made of prose. redact() is conservative by construction:
        # addresses only inside a labelled window, so running it everywhere
        # costs nothing and forgets nothing.
        #
        # That "everything" is load-bearing for the new tables too. lore.story
        # is model prose retold from chat -- exactly the kind of text that can
        # reproduce a pasted label block or a card number -- and it gets the
        # same scope documents get by falling through this loop, not by being
        # remembered. ask_word.word rides through as well, deliberately: the
        # generating stage already refuses any word with a four-digit run, so
        # numbers cannot arrive, and a short lowercase fragment can never
        # satisfy the labelled-window address patterns -- but exempting it
        # would recreate the hand-listed coverage this loop exists to end,
        # and redact() on a clean word is the identity.
        rows = [tuple(None if i in blank
                      else redact(v) if isinstance(v, str) else v
                      for i, v in enumerate(row)) for row in rows]
        out.executemany(
            f"INSERT INTO {table} VALUES ({','.join('?' * width)})", rows)

    verify_evidence_complete(out)

    # The background layer, written into the artifact by the build itself.
    # bm_basics.py used to be a manual post-step nothing invoked, so every
    # rebuild silently dropped general_knowledge and searchGeneral returned []
    # on device -- the app code for the layer was live, the data was not
    # there. A layer the build does not write is a layer the build deletes.
    write_general_knowledge(out)

    out.commit()
    out.execute("VACUUM")

    print(f"wrote {out_path}")
    for t in ("entity", "entity_fact", "evidence", "person_profile",
              "expertise", "relationship", "lore", "ask_word",
              "general_knowledge", "person", "person_content"):
        print(f"  {t:<16} {out.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}")
    print(f"  {'size':<16} {out_path.stat().st_size / 1_048_576:.1f} MB "
          f"(source {src_path.stat().st_size / 1_048_576:.0f} MB)")
    print("evidence completeness: OK")


if __name__ == "__main__":
    main()
