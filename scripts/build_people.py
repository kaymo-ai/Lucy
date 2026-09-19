#!/usr/bin/env python3
"""
One table of camp members out of seven differently-shaped signup sheets.

`PS People/` holds a signup sheet per year, and no two of them agree on
anything: 2016 and 2017 have a single `Name` column and no email at all, 2016
carries a second roster of prospects side by side in columns 17-28, 2019 splits
the RSVP across two columns, and 2024 and 2026 push the header down to row 3
under a title. What they have in common is a person, a year, and an answer
about whether they were coming.

Identity is the whole problem. Email is the only reliable key and it only
appears from 2019 on, so the earlier years have to be matched by name — and
2017 abbreviates them ("David D.", "Michael F", "P 'Sunrise' B"). Every merge
records how it was made, so a wrong one can be found and undone rather than
silently becoming fact. Where a name is ambiguous the rows are left separate:
two records for one person is a visible problem, one record merging two people
is not.

"Attended" is not knowable from a signup sheet. What is knowable is what the
person said, so `status` keeps their answer and `years_attended` counts only
the years they said yes.

Read `years_attended` across years with care. The 2022 and 2023 files are
titled "Confirmed Campers" and carry no RSVP column, so being on them is the
answer and they come out at 100% by construction — 80 of 80, 54 of 54. Every
other year has people on the sheet who said no or hedged. 2022's 80 and 2024's
46 are not the same measurement, and the gap is the sheet's design rather than
the camp's.

`--install` writes the roster into the knowledge database as `camp_member`,
and turns it into `camp_fact` rows Lucy can actually retrieve — a table nothing
queries answers nothing. Each generated fact cites the roster row it came from
with a quote lifted verbatim from that row's `record`, the same spine every
other claim in the database sits on. Email and phone live in the table because
matching people across seven years depends on them, and they ship to the
device: a signup-sheet address was given to the camp for camp use, which is a
different thing from a home address that arrived stuck to a receipt.

Usage:
    python build_people.py [--src "../PS People"] [--out output/people.db]
                           [--csv output/camp_members.csv]
                           [--install [output/ps_knowledge.db]]
"""
import argparse
import csv
import re
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from enrich_schema import create_enrichment_tables, verify_evidence_complete

HERE = Path(__file__).resolve().parent

# Where the signup sheets live, which is not always beside this file.
#
# The default was "../PS People", relative, and in a git WORKTREE that
# resolves to <worktree>/PS People -- a directory that does not exist,
# because the corpus is gitignored and lives only in the main checkout. The
# script then printed "no signup sheets at ..." and reingest.sh, which guards
# this stage with `|| echo (build_people skipped)`, carried on.
#
# So every rebuild run from a worktree produced camp_member with ZERO rows,
# and nothing said so: the roster simply was not there. Evidence is allowed
# to cite camp_member, and build_preview_db ships it, so the phone got a
# camp with no members and degraded quietly, exactly the way this project's
# other silent failures do.
CORPUS_DIRNAME = "PS People"


def default_src() -> Path:
    """The sheets, looked for beside the repo and then in the main checkout.

    A linked worktree's .git is a FILE reading "gitdir: <main>/.git/worktrees/
    <name>", which is how the main checkout is found without shelling out.
    """
    here = HERE.parent / CORPUS_DIRNAME
    if here.is_dir():
        return here

    dotgit = HERE.parent / ".git"
    if dotgit.is_file():
        text = dotgit.read_text().strip()
        if text.startswith("gitdir:"):
            gitdir = Path(text.split(":", 1)[1].strip())
            # .../<main>/.git/worktrees/<name> -> <main>
            for parent in gitdir.parents:
                if parent.name == ".git":
                    candidate = parent.parent / CORPUS_DIRNAME
                    if candidate.is_dir():
                        return candidate
                    break
    return here

# status values, in the order they win when a person answers twice in a year
ATTENDING, MAYBE, NOT_ATTENDING, LISTED = "attending", "maybe", "not_attending", "listed"
_RANK = {ATTENDING: 3, MAYBE: 2, LISTED: 1, NOT_ATTENDING: 0}


@dataclass
class Sheet:
    """Where the columns are in one year's sheet. Indices, not guesses."""
    path: str
    year: int
    header_row: int
    first_data_row: int
    name: int | None = None          # single "Name" column (2016, 2017, 2019)
    first: int | None = None         # split name columns (2022 onward)
    last: int | None = None
    email: int | None = None
    phone: int | None = None
    city: int | None = None
    status_col: int | None = None
    default_status: str = LISTED
    # Ceiling on how committed a row in this block can be read as. The 2016
    # prospects list uses the same words as the roster beside it — "Confident"
    # — but it is a list of people who had not committed, so a yes there is
    # not the same claim as a yes on the roster.
    max_status: str = ATTENDING
    # Columns where a filled-in value is a commitment rather than an opinion.
    # 2019 has no plain yes/no column, but it records who paid $420 of dues,
    # who was marked "Coming?", and who was given a strike shift — none of
    # which happens for someone who stayed home. These override the RSVP,
    # which was collected in Q1 and is the earliest, softest thing on the
    # sheet.
    confirm_cols: tuple[int, ...] = ()
    # A second roster sitting alongside the first, as in 2016.
    extra: "Sheet | None" = None


SHEETS = [
    Sheet("PS 2016 - Signup.csv", 2016, header_row=1, first_data_row=2,
          name=1, city=9, status_col=2, default_status=LISTED,
          # Columns 17-28 are a second list of people who had not committed.
          extra=Sheet("", 2016, header_row=1, first_data_row=2,
                      name=17, city=25, status_col=18, default_status=LISTED,
                      max_status=MAYBE)),
    Sheet("PS 2017 - Sign up.csv", 2017, header_row=0, first_data_row=1,
          name=1, city=9, status_col=3),
    Sheet("PS 2019 - Sign Up.csv", 2019, header_row=0, first_data_row=1,
          name=1, email=13, city=7, status_col=3,
          # 11 = dues paid (an amount), 25 = "Coming? (Dues paid?)",
          # 28 = strike shift assigned.
          confirm_cols=(11, 25, 28)),
    Sheet("PS BM 2022 - Confirmed Campers 22.csv", 2022, header_row=0,
          first_data_row=1, first=1, last=2, email=3, city=4,
          # The file is the confirmed roster; being on it is the answer.
          default_status=ATTENDING),
    Sheet("PS BM 2023 - Confirmed Campers.csv", 2023, header_row=0,
          first_data_row=1, first=1, last=2, email=5, phone=6, city=8,
          status_col=4, default_status=ATTENDING),
    Sheet("PS BM 2024 (Cleaned).xlsx - Camp Roster.csv", 2024, header_row=2,
          first_data_row=3, first=1, last=2, email=9, phone=10, city=11,
          status_col=3),
    Sheet("PS BM 2026.xlsx - 1. Camp Roster.csv", 2026, header_row=2,
          first_data_row=3, first=1, last=2, email=4, status_col=3),
]

_NOT = re.compile(r"\bnot\s+attending\b|^no\b|^n$|^0$", re.IGNORECASE)
_MAYBE = re.compile(r"50/50|maybe|hopeful|probably|unlikely|tbd|\?", re.IGNORECASE)
# "Yesssss!" and "yes plz" are both a yes, so the s is allowed to run on and
# the word boundary has to go — \b after "yes" fails on the fourth s.
_YES = re.compile(r"100%|^y(?:e+s+|ep|up)|^y$|confident|^iwbly$|lolyes|obvi",
                  re.IGNORECASE)


def read_status(raw: str, default: str) -> str:
    """Turn a free-text answer into one of four states.

    The sheets are answered by hand and it shows: "Yesssss!", "LOLYES",
    "Yes. Obvi", "Unlikely because I am at the top of a tall mountain in
    Tibet". Order matters — "not attending" contains no "yes", but
    "yes/hopefully" contains both a yes and a maybe, and the hedge is the
    honest reading.
    """
    s = (raw or "").strip()
    if not s:
        return default
    if _NOT.search(s):
        return NOT_ATTENDING
    if _MAYBE.search(s):
        return MAYBE
    if _YES.search(s):
        return ATTENDING
    return default


_AFFIRMATIVE = re.compile(r"^(y|yes|paid|venmo|cash|done|✓|✔)\b", re.IGNORECASE)


def confirms(raw: str) -> bool:
    """Whether a commitment column counts as somebody having actually done it.

    Filled-in is not enough. The 2019 dues column holds amounts — 420, 940 —
    but also "NO DUES" and "Refunded", and treating any non-empty value as a
    yes flipped four people who had written "No. :(" in the RSVP box into
    attendees. A commitment needs a positive value: a number above zero, or a
    word that means paid.
    """
    s = (raw or "").strip()
    if not s:
        return False
    number = re.sub(r"[^0-9.\-]", "", s)
    if number and re.fullmatch(r"-?\d+(\.\d+)?", number):
        return float(number) > 0
    return bool(_AFFIRMATIVE.match(s))


_NICK = re.compile(r"['\"“”‘’]([^'\"“”‘’]+)['\"“”‘’]")


def split_name(full: str) -> tuple[str, str, str]:
    """(first, last, nickname) from a display name.

    Nicknames are written inline and in several quote styles — Piotr
    'Sunrise' Bartkowski, Jen 'Cheshire' Clement, Joseph "ZenMaster" Carlton
    — and they are how half the camp refers to each other, so they are kept
    rather than discarded.
    """
    full = (full or "").strip()
    nick = ""
    m = _NICK.search(full)
    if m:
        nick = m.group(1).strip()
        full = (full[:m.start()] + " " + full[m.end():]).strip()
    # WhatsApp names arrive decorated — "~ Laszlo", "~~G~", "Chloe 🎬🦷".
    # A token with no letters or digits in it is punctuation, not a name, and
    # treating "~" as the given name is why the row holding Laszlo Sandor's 135
    # messages never matched him.
    parts = [p for p in re.split(r"\s+", full) if p and norm(p)]
    if not parts:
        return "", "", nick
    if len(parts) == 1:
        return parts[0], "", nick
    return parts[0], " ".join(parts[1:]), nick


def norm(s: str) -> str:
    """Lowercase, unpunctuated, single-spaced — for comparing names."""
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def norm_email(s: str) -> str:
    s = (s or "").strip().lower()
    return s if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", s) else ""


@dataclass
class Appearance:
    """One person on one year's sheet."""
    year: int
    display: str
    first: str
    last: str
    nickname: str
    email: str
    phone: str
    city: str
    status: str
    source: str
    row: int


def read_sheet(sheet: Sheet, rows: list[list[str]], source: str) -> list[Appearance]:
    out = []

    def cell(r: list[str], i: int | None) -> str:
        return r[i].strip() if i is not None and i < len(r) else ""

    header_name = cell(rows[sheet.header_row], sheet.name if sheet.name is not None
                       else sheet.first)
    for n, r in enumerate(rows[sheet.first_data_row:], start=sheet.first_data_row):
        if sheet.name is not None:
            display = cell(r, sheet.name)
            first, last, nick = split_name(display)
        else:
            first_raw, last = cell(r, sheet.first), cell(r, sheet.last)
            f, _, nick = split_name(first_raw)
            first = f
            display = " ".join(x for x in (first_raw, last) if x)
        if not first:
            continue
        # A repeated header, or a total line that slipped into the data range.
        if norm(display) == norm(header_name) or norm(first) in {"total", "name"}:
            continue
        status = min(read_status(cell(r, sheet.status_col),
                                 sheet.default_status),
                     sheet.max_status, key=lambda s: _RANK[s])
        # Doing beats saying. Someone who paid the dues was going, whatever
        # they put in the RSVP box in March.
        if status != ATTENDING and any(confirms(cell(r, c))
                                       for c in sheet.confirm_cols):
            status = min(ATTENDING, sheet.max_status, key=lambda s: _RANK[s])
        out.append(Appearance(
            year=sheet.year, display=display, first=first, last=last,
            nickname=nick, email=norm_email(cell(r, sheet.email)),
            phone=cell(r, sheet.phone), city=cell(r, sheet.city),
            status=status, source=source, row=n + 1))
    return out


@dataclass
class Person:
    emails: set[str] = field(default_factory=set)
    names: set[str] = field(default_factory=set)
    appearances: list[Appearance] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, a: Appearance, how: str) -> None:
        self.appearances.append(a)
        if a.email:
            self.emails.add(a.email)
        if a.last:
            self.names.add(norm(f"{a.first} {a.last}"))
        if how:
            self.notes.append(how)


def resolve(appearances: list[Appearance]) -> list[Person]:
    """Group appearances into people.

    Two passes on purpose. Everything with an email is clustered first, so
    that the name-only rows from 2016 and 2017 have a complete set of known
    people to match against — matching them in file order would mean an early
    row never sees the email that would have identified it.
    """
    people: list[Person] = []
    by_email: dict[str, Person] = {}
    by_name: dict[str, Person] = {}

    withm = [a for a in appearances if a.email]
    without = [a for a in appearances if not a.email]

    for a in withm:
        p = by_email.get(a.email)
        if p is None:
            # Same full name, no email yet on that record — adopt it.
            p = by_name.get(norm(f"{a.first} {a.last}")) if a.last else None
            if p is None:
                p = Person()
                people.append(p)
            by_email[a.email] = p
        p.add(a, "")
        for n in p.names:
            by_name.setdefault(n, p)

    # An index for matching abbreviated names: "David D." -> "david dimarco".
    def initial_key(first: str, last: str) -> str:
        return f"{norm(first)} {norm(last)[:1]}" if last else ""

    by_initial: dict[str, list[Person]] = defaultdict(list)
    by_first: dict[str, list[Person]] = defaultdict(list)
    for p in people:
        for n in p.names:
            f, _, l = n.partition(" ")
            if l:
                by_initial[f"{f} {l[:1]}"].append(p)
            by_first[f].append(p)

    for a in without:
        full = norm(f"{a.first} {a.last}")
        how = ""
        p = by_name.get(full) if a.last else None
        if p is None and a.last:
            # "David D." — one candidate only, or leave it separate.
            cands = {id(x): x for x in by_initial.get(initial_key(a.first, a.last), [])}
            if len(cands) == 1:
                p = next(iter(cands.values()))
                how = f"{a.year}: matched '{a.display}' on first name + last initial"
        if p is None and not a.last:
            # A bare first name. Only safe when exactly one person owns it.
            cands = {id(x): x for x in by_first.get(norm(a.first), [])}
            if len(cands) == 1:
                p = next(iter(cands.values()))
                how = f"{a.year}: matched '{a.display}' on a unique first name"
        if p is None:
            p = by_name.get(full) or Person()
            if p not in people:
                people.append(p)
            how = how or (f"{a.year}: no email and no match — kept separate"
                          if not a.email else "")
        p.add(a, how)
        if a.last:
            by_name.setdefault(full, p)
    return people


# Diminutives that share too few letters for the prefix rule to see them.
# Deliberately short: every entry here is a decision to treat two spellings as
# one person, and a wrong one silently fuses two campers.
_SAME = [
    {"pip", "philippa"}, {"edd", "ed", "edward", "ted"}, {"jim", "jimmy", "james"},
    {"bob", "rob", "robert"}, {"bill", "will", "william"}, {"dick", "richard"},
    {"jack", "john", "jonathan"}, {"harry", "henry"}, {"betty", "liz", "beth",
    "elizabeth"}, {"kate", "katie", "katharine", "katherine", "kathryn"},
    {"meg", "megsy", "megan"}, {"tony", "anthony"}, {"mike", "michael"},
    {"chris", "christopher"}, {"dave", "david"}, {"steve", "stephen", "steven"},
    {"matt", "matthew"}, {"nick", "nic", "nicolas", "nicholas"},
    {"sam", "samuel"}, {"alex", "alexander"}, {"ben", "benjamin"},
    {"dan", "danny", "daniel"}, {"joe", "joseph"}, {"tom", "thomas"},
    {"andy", "andrew"}, {"pete", "peter"}, {"greg", "gregory"},
    {"jess", "jessica"}, {"cece", "cecilia"}, {"lav", "lavanya"},
    {"don", "donald"}, {"jo", "joanna", "joanne"},
]


def first_names_agree(a: str, b: str) -> bool:
    """Whether two given names plausibly belong to one person.

    A shared first letter is not enough — Aylon and Amir Stennett are two
    people, as are John and James E. Requiring either a real prefix or an
    explicit diminutive keeps those apart while joining Don to Donald.
    """
    # Split before normalising: norm() strips the slash, so "James/Jimmy"
    # would become the single word "jamesjimmy" and match nothing.
    alts_a = {norm(x) for x in re.split(r"[/&]", a or "") if norm(x)}
    alts_b = {norm(x) for x in re.split(r"[/&]", b or "") if norm(x)}
    if not alts_a or not alts_b:
        return False
    if norm(a) == norm(b):
        return True
    if alts_a & alts_b:
        return True
    for x in alts_a:
        for y in alts_b:
            if len(x) >= 2 and len(y) >= 2 and (x.startswith(y) or y.startswith(x)):
                return True
            if any({x, y} <= group for group in _SAME):
                return True
    return False


def _initials(s: str) -> str:
    return "".join(w[0] for w in norm(s).split() if w)


def surnames_agree(a: str, b: str) -> bool:
    """Exact, an initial standing in for the full name, or initials for both.

    "Nic Z" / "Nicolas Z" is the single-initial case. "Marc MC" / "Marc Mercer
    Cabrera" is the other one: MC is what Mercer Cabrera shortens to in a contacts
    list, and requiring exact equality left the row holding his 53 messages
    and his portrait out of the candidate set entirely.
    """
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if len(na) == 1 or len(nb) == 1:
        return na[0] == nb[0]
    # Initials, but only against a surname that actually has several words —
    # otherwise every two-letter surname matches every name starting with it.
    for short, long in ((na, b), (nb, a)):
        if len(short) <= 3 and len(norm(long).split()) > 1 and \
                short == _initials(long):
            return True
    return False


# Written a dozen ways across seven sheets. Kept short and obvious: a wrong
# equivalence here merges two people, so only the abbreviations this camp
# actually uses are listed.
_CITY_ALIASES = [
    {"sf", "san francisco", "san fran", "sanfrancisco"},
    {"nyc", "ny", "new york", "new york city", "brooklyn"},
    {"la", "los angeles"},
    {"sea", "seattle"},
    {"chi", "chicago"},
]


def _city_key(city: str) -> str:
    c = norm(city)
    for group in _CITY_ALIASES:
        if c in group:
            return sorted(group)[0]
    return c


def person_cities(p: "Person") -> set[str]:
    """Every place a person gave, canonicalised.

    Split on separators because people answer "LA / SF" and "NYC / London" —
    someone with two homes should match on either.
    """
    out: set[str] = set()
    for a in p.appearances:
        for part in re.split(r"[/,&]|\bor\b", a.city or ""):
            key = _city_key(part)
            if key:
                out.add(key)
    return out


def cities_agree(a: set[str], b: set[str]) -> bool:
    return bool(a & b)


def read_same_person(src: Path) -> list[set[str]]:
    """Identities the sheets cannot prove but the camp knows.

    Some pairs are simply not derivable. Anna M, Anna Morrow and Anna Moss are
    all called Anna, all live in SF, and have no shared email or year — no
    rule reaches the right answer, and a rule invented to force it would merge
    two real people somewhere else. So this is written down instead.

    `PS People/same-person.txt`, one group per line, names separated by " = ":

        Anna M = Anna Morrow
        Kat = Katharine Hensley

    Lines beginning # are comments. Names are matched case- and
    punctuation-insensitively against the roster.
    """
    path = src / "same-person.txt"
    if not path.exists():
        return []
    groups = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        names = {norm(n) for n in line.split("=") if norm(n)}
        if len(names) > 1:
            groups.append(names)
    return groups


def apply_same_person(people: list["Person"], groups: list[set[str]]
                      ) -> list["Person"]:
    """Fold together the records named in each hand-written group."""
    if not groups:
        return people
    absorbed: set[int] = set()
    for group in groups:
        matched = [i for i, p in enumerate(people)
                   if i not in absorbed
                   and norm(" ".join(person_name(p)).strip()) in group]
        if len(matched) < 2:
            continue
        keep_i = max(matched,
                     key=lambda i: len("".join(person_name(people[i]))))
        keep = people[keep_i]
        for i in matched:
            if i == keep_i:
                continue
            other = people[i]
            keep.notes.append(
                f"merged '{' '.join(person_name(other)).strip()}' with "
                f"'{' '.join(person_name(keep)).strip()}' — recorded by hand "
                f"in same-person.txt")
            for a in other.appearances:
                keep.add(a, "")
            keep.notes.extend(n for n in other.notes if n)
            absorbed.add(i)
    return [p for i, p in enumerate(people) if i not in absorbed]


def person_name(p: "Person") -> tuple[str, str]:
    """The most complete spelling this person appears under.

    Not the most recent: 2017 abbreviates, so "David D." would beat "David
    DiMarco" on recency and become the canonical name. Longest wins instead,
    which is the same rule a human would apply reading the sheets side by side.
    """
    first = max((a.first for a in p.appearances), key=len, default="")
    last = max((a.last for a in p.appearances), key=len, default="")
    return first, last


def merge_variants(people: list["Person"]) -> list["Person"]:
    """Second pass: join records that name the same person differently.

    The first pass keys on email, so one person with two addresses becomes two
    records (Jess Lang had both jessh@ and jess.h@), and the
    2017 sheet's abbreviations produce more.

    Agreement is computed across every pair before anything is merged. Doing
    it incrementally made the result depend on file order: "Joe H" in 2017
    agrees with both Jo Hanley and Joe Horvath, and whichever had been
    processed first would silently absorb it. A name that fits two people
    identifies neither, so both sides are left alone and the ambiguity is
    recorded instead.
    """
    named = [(p, *person_name(p)) for p in people]
    partners: dict[int, set[int]] = {i: set() for i in range(len(named))}
    for i, (_, fi, li) in enumerate(named):
        for j in range(i + 1, len(named)):
            _, fj, lj = named[j]
            # A missing surname agrees with anything — the sheets record
            # "Giordano" one year and "Gio Salvi" another, and demanding two
            # surnames match meant a first-name-only row could never join the
            # person it belongs to. The ambiguity guard below is what keeps
            # that safe: a bare given name that fits two people fits neither.
            surnames_ok = (not norm(li) or not norm(lj)
                           or surnames_agree(li, lj))
            if surnames_ok and first_names_agree(fi, fj):
                partners[i].add(j)
                partners[j].add(i)

    # Merge by connected component, not pair by pair.
    #
    # Requiring exactly one partner meant a cluster could never merge: the
    # sheets carry "Simo", "Simo" and "Simone" for one person, each agreeing
    # with the other two, so every row had two partners and none of them
    # merged. A component only merges when it is a clique — every member
    # agreeing with every other. That is what still keeps Anna M, Anna Morrow
    # and Anna Moss apart, since Morrow and Moss disagree with each other and
    # the component is not complete.
    seen: set[int] = set()
    absorbed: set[int] = set()
    for start in range(len(named)):
        if start in seen:
            continue
        component, stack = set(), [start]
        while stack:
            k = stack.pop()
            if k in component:
                continue
            component.add(k)
            stack.extend(partners[k] - component)
        seen |= component
        if len(component) < 2:
            continue

        members = sorted(component)
        is_clique = all(b in partners[a] for a in members for b in members if a != b)
        if not is_clique:
            # A first-name-only row in a crowd: the sheets also record where
            # people live, and that often settles it. Two rows reading just
            # "Ana", both from SF, sat beside Ana Bartkowska of San Francisco,
            # Ana Luna of Zihuatanejo and Ana Stancuic of Barcelona — a name
            # that fits three people, and a city that fits one.
            for i in list(members):
                p, fi, li = named[i]
                if i in absorbed or norm(li):
                    continue
                mine = person_cities(p)
                if not mine:
                    continue
                fits = [j for j in partners[i]
                        if j not in absorbed and norm(named[j][2])
                        and cities_agree(mine, person_cities(named[j][0]))]
                if len(fits) != 1:
                    continue
                j = fits[0]
                keep, kf, kl = named[j]
                shared = sorted(mine & person_cities(keep))
                keep.notes.append(
                    f"merged '{fi}' with '{f'{kf} {kl}'.strip()}' — a first "
                    f"name that fits several people, and only one of them "
                    f"lives in {shared[0]}")
                for a in p.appearances:
                    keep.add(a, "")
                keep.notes.extend(n for n in p.notes if n)
                absorbed.add(i)

            others = {i: ", ".join(f"{named[j][1]} {named[j][2]}".strip()
                                   for j in sorted(partners[i]))
                      for i in members}
            for i in members:
                if i in absorbed:
                    continue
                p, fi, li = named[i]
                p.notes.append(f"'{f'{fi} {li}'.strip()}' is ambiguous — also "
                               f"matches {others[i]}; left as separate records")
            continue

        # The fullest spelling keeps the record; the rest fold into it.
        keep_i = max(members, key=lambda i: len(f"{named[i][1]}{named[i][2]}"))
        keep = named[keep_i][0]
        kf, kl = named[keep_i][1], named[keep_i][2]
        for i in members:
            if i == keep_i:
                continue
            p, fi, li = named[i]
            if norm(fi) != norm(kf) or norm(li) != norm(kl):
                keep.notes.append(f"merged '{f'{fi} {li}'.strip()}' with "
                                  f"'{f'{kf} {kl}'.strip()}' — names agree")
            elif p.emails and keep.emails and not (p.emails & keep.emails):
                keep.notes.append(
                    f"merged two records for '{kf} {kl}' with different "
                    f"addresses: {', '.join(sorted(p.emails | keep.emails))}")
            for a in p.appearances:
                keep.add(a, "")
            keep.notes.extend(n for n in p.notes if n)
            absorbed.add(i)

    return [p for i, (p, _, _) in enumerate(named) if i not in absorbed]


def best(values: list[str]) -> str:
    """Most recent non-empty value wins; the caller sorts by year."""
    for v in reversed(values):
        if v:
            return v
    return ""


def build(src: Path) -> list[dict]:
    appearances: list[Appearance] = []
    for sheet in SHEETS:
        path = src / sheet.path
        if not path.exists():
            print(f"  missing: {path}", file=sys.stderr)
            continue
        rows = list(csv.reader(open(path, newline="", encoding="utf-8-sig")))
        appearances += read_sheet(sheet, rows, sheet.path)
        if sheet.extra:
            extra = sheet.extra
            extra.path = sheet.path
            appearances += read_sheet(extra, rows, sheet.path)
        got = sum(1 for a in appearances if a.source == sheet.path)
        print(f"  {sheet.year}  {got:>3} rows  {sheet.path}")

    people = apply_same_person(merge_variants(resolve(appearances)),
                               read_same_person(src))

    out = []
    for p in people:
        apps = sorted(p.appearances, key=lambda a: a.year)
        # One answer per year: the most committal one they gave.
        per_year: dict[int, str] = {}
        for a in apps:
            if a.year not in per_year or _RANK[a.status] > _RANK[per_year[a.year]]:
                per_year[a.year] = a.status
        attended = sorted(y for y, s in per_year.items() if s == ATTENDING)
        listed = sorted(per_year)
        first, last = person_name(p)
        out.append({
            "name": " ".join(x for x in (first, last) if x),
            "first_name": first,
            "last_name": last,
            "nickname": best([a.nickname for a in apps]),
            "email": best([a.email for a in apps]),
            "phone": best([a.phone for a in apps]),
            "home_city": best([a.city for a in apps]),
            "years_attended": ",".join(str(y) for y in attended),
            "years_listed": ",".join(str(y) for y in listed),
            "year_count": len(attended),
            "first_year": listed[0] if listed else None,
            "last_year": listed[-1] if listed else None,
            "statuses": ";".join(f"{y}={per_year[y]}" for y in listed),
            "match_notes": "; ".join(n for n in p.notes if n),
        })
    out.sort(key=lambda r: (-r["year_count"], r["name"].lower()))
    return out


COLUMNS = ["name", "first_name", "last_name", "nickname", "email", "phone",
           "home_city", "years_attended", "years_listed", "year_count",
           "first_year", "last_year", "statuses", "match_notes"]


def years_phrase(years: list[int]) -> str:
    """'2019, 2022 and 2023' — how a person would say it."""
    ys = [str(y) for y in years]
    if len(ys) == 1:
        return ys[0]
    return ", ".join(ys[:-1]) + " and " + ys[-1]


def render_record(r: dict) -> str:
    """The roster row as text.

    This is what `evidence.quote` cites, so every generated fact must be
    supportable by a verbatim substring of it — including the contact details,
    which is why they appear here rather than only in their own columns.
    """
    parts = [f"{r['name']}."]
    if r["nickname"]:
        parts.append(f"Known as {r['nickname']}.")
    if r["home_city"]:
        parts.append(f"Base {r['home_city']}.")
    if r["email"]:
        parts.append(f"Email: {r['email']}.")
    if r["phone"]:
        parts.append(f"Phone: {r['phone']}.")
    if r["years_listed"]:
        parts.append("Signed up in "
                     f"{', '.join(r['years_listed'].split(','))}.")
    if r["years_attended"]:
        parts.append("Said yes in "
                     f"{', '.join(r['years_attended'].split(','))}.")
    return " ".join(parts)


def roster_facts(members: list[tuple[int, dict, str]]) -> list[dict]:
    """Turn roster rows into facts Lucy can retrieve, each with its citation.

    Retrieval only searches `camp_fact`, entities and documents — a table
    nothing queries answers nothing. So the roster becomes sentences, and
    every sentence carries the row it came from and a quote lifted verbatim
    from that row's `record`.
    """
    facts: list[dict] = []
    by_year: dict[int, list[tuple[int, str]]] = defaultdict(list)

    for member_id, r, record in members:
        attended = [int(y) for y in r["years_attended"].split(",") if y]
        if attended:
            quote = f"Said yes in {', '.join(str(y) for y in attended)}."
            assert quote in record, quote
            # "has camped with us" is not what the evidence says. The quote
            # backing it reads "Said yes in 2016, 2017, 2019" — a signup sheet
            # records an answer, not an arrival, and for 2022 and 2023 it does
            # not even record that: being on a sheet titled "Confirmed
            # Campers" is the yes. A claim its own receipt does not support is
            # the exact failure the evidence spine exists to catch, so the
            # fact is worded to the thing that was actually written down.
            facts.append({
                "topic": "roster", "category": "who", "year": attended[-1],
                "fact": (f"{r['name']} signed up and said yes to camping with "
                         f"us in {years_phrase(attended)}."),
                "cites": [(member_id, quote)],
            })
            for y in attended:
                by_year[y].append((member_id, quote))
        if r["nickname"]:
            quote = f"Known as {r['nickname']}."
            assert quote in record, quote
            facts.append({
                "topic": "roster", "category": "who",
                "year": attended[-1] if attended else None,
                "fact": f"{r['name']} is known as {r['nickname']}.",
                "cites": [(member_id, quote)],
            })

        # How to reach someone is the roster's most ordinary use, and a column
        # nothing queries is not an answer. Worded with "reach", "email" and
        # "phone" because those are the words the question arrives in.
        if r["email"] or r["phone"]:
            cites, how = [], []
            if r["email"]:
                q = f"Email: {r['email']}."
                assert q in record, q
                cites.append((member_id, q))
                how.append(f"by email at {r['email']}")
            if r["phone"]:
                q = f"Phone: {r['phone']}."
                assert q in record, q
                cites.append((member_id, q))
                how.append(f"by phone on {r['phone']}")
            facts.append({
                "topic": "roster", "category": "who",
                "year": attended[-1] if attended else None,
                "fact": (f"Contact details for {r['name']}: we can reach "
                         f"them {' or '.join(how)}."),
                "cites": cites,
            })

    # "How many of us are coming" is a real question, and the answer is a
    # count over the whole roster rather than anything one row says — so it
    # cites every row it counted, not a convenient one.
    #
    # The wording is load bearing. "48 of us said yes for 2026" scores exactly
    # the same as any one person's attendance fact — both match "2026" and
    # carry the same year — so the count lost a tie to an arbitrary camper.
    # Saying "people" and "coming", the words the question uses, is what puts
    # it ahead. The caveat is not padding either: a signup sheet is a count of
    # answers, not of people who turned up.
    for year, cites in sorted(by_year.items()):
        facts.append({
            "topic": "roster", "category": "who", "year": year,
            "fact": (f"{len(cites)} people said yes to coming in {year}. "
                     f"That is how many signed up, not a headcount on playa."),
            "cites": cites,
        })
    return facts


def all_same_person(candidate_ids, people: list[tuple[int, str]]) -> bool:
    """Whether every candidate chat row plausibly names the same human.

    True for "Piotr" / "Piotr 'Sunrise'" — one given name, no conflicting
    surname. False the moment two candidates carry surnames that disagree,
    which is the case this must not collapse.
    """
    names = {pid: name for pid, name in people}
    parsed = [split_name(names[pid]) for pid in candidate_ids if pid in names]
    if len(parsed) < 2:
        return True
    for i, (fi, li, _) in enumerate(parsed):
        for fj, lj, _ in parsed[i + 1:]:
            if not first_names_agree(fi, fj):
                return False
            # An absent surname agrees with anything; two present ones must match.
            if norm(li) and norm(lj) and not surnames_agree(li, lj):
                return False
    return True


def link_to_chat(conn) -> tuple[int, dict[str, int]]:
    """Point each roster row at the same human in the chat corpus.

    The two name-spaces do not agree. WhatsApp exported whatever people had
    saved for each other — "Oz", "Piotr", "Nick Darnell" — while the signup
    sheets carry full names. There are no emails on the chat side to key on,
    so this is name matching, with the roster's own rule: link only where
    exactly one candidate fits, and leave it NULL otherwise. A wrong link
    attributes one person's words to another, which is worse than no link.
    """
    members = [(r[0], r[1], r[2], r[3]) for r in conn.execute(
        "SELECT id, first_name, last_name, nickname FROM camp_member")]
    linked, how_counts = 0, defaultdict(int)
    taken: set[int] = set()

    rows = conn.execute("SELECT id, name, COALESCE(message_count, 0) FROM person "
                        "WHERE name IS NOT NULL AND name != ''").fetchall()
    people = [(r[0], r[1]) for r in rows]
    message_counts = {r[0]: r[2] for r in rows}

    for member_id, first, last, nick in members:
        full = norm(f"{first} {last}")
        hits: list[tuple[int, str]] = []
        for person_id, pname in people:
            pn = norm(pname)
            if not pn:
                continue
            pf, pl, _ = split_name(pname)
            if pn == full:
                hits.append((person_id, "exact full name"))
            elif nick and pn == norm(nick):
                hits.append((person_id, f"chat name is their nickname, {nick}"))
            elif not norm(pl):
                # A bare chat handle: "Piotr", "Oz". Only safe when one member
                # owns that given name.
                if first_names_agree(pf, first):
                    hits.append((person_id, "chat name is a bare given name"))
            elif surnames_agree(pl, last) and first_names_agree(pf, first):
                hits.append((person_id, "given name and surname agree"))

        # A chat identity belongs to one human, so a row already linked is out
        # of the running — but dropping it must not end the search. Collapsing
        # onto an already-taken row and giving up left Marc Mercer Cabrera
        # unlinked because the loose short-form rule had pulled "Marcus" into
        # his candidate set and Marcus Foster got there first.
        unique = {pid: how for pid, how in hits if pid not in taken}

        # The corpus is full of contentless stubs — a name someone was
        # addressed by once, with no messages behind it. They are never the
        # right link target, and worse, they poison the decisions below: the
        # empty row "Jessie" sat in Jess Sheldon's candidates, disagreed with
        # "Jessica", broke the same-person collapse, and handed the link to an
        # empty "Jess Sheldon" instead of the "Jessica Sheldon" row holding
        # 212 messages and her portrait.
        #
        # So: if any candidate has writing behind it, only those compete.
        # Stubs are still allowed to win when nothing else is available, which
        # is how people with no chat presence stay linked at all.
        with_writing = {pid: how for pid, how in unique.items()
                        if message_counts.get(pid, 0) > 0}
        if with_writing:
            unique = with_writing
        # Kept before the narrowing below, which reduces `unique` to the
        # winner — the runners-up are exactly what person_ids needs.
        candidates = dict(unique)

        # Order matters here, and getting it wrong is how Marcus Foster ended
        # up linked to an empty chat row.
        #
        # First: several candidates are not necessarily several people.
        # WhatsApp exports the same human again whenever their saved name
        # changes, so "Piotr" and "Piotr 'Sunrise'", or "Marcus" and "Marcus
        # Foster", are one person spread across rows — only one of which has
        # the messages. Collapse those to the row that actually carries the
        # writing.
        if len(unique) > 1 and all_same_person(unique, people):
            best_id = max(unique, key=lambda pid: message_counts.get(pid, 0))
            unique = {best_id: f"{unique[best_id]} (chat has "
                               f"{len(hits)} rows for them)"}
        # Only then: when the candidates genuinely disagree, an exact
        # full-name match settles it. "Alex Tam" matched the chat row "Alex
        # Tam" and also, through the short-form rule, "Alexis" and
        # "Alexandria" — different people, whose disagreement with each other
        # would otherwise veto the one match that was certain.
        #
        # This must not run first. Doing so preferred the exact name over the
        # messages, and "Marcus Foster" (0 messages) beat "Marcus" (1,136 and
        # a portrait) — an exactly-right name attached to an empty person.
        elif len(unique) > 1:
            exact = {pid: how for pid, how in unique.items()
                     if how == "exact full name"}
            if len(exact) == 1:
                unique = exact
        if len(unique) == 1:
            person_id, how = next(iter(unique.items()))
            # Every candidate that agrees with the winner is the same human
            # exported more than once. Recording them all is what stops the
            # runners-up appearing in the People list as separate people —
            # Katharine Hensley was in the chat twice, with 110 messages under
            # one name and 106 under the other.
            # Only rows with writing are absorbed. A stub never renders a card,
            # so claiming it buys nothing — and claiming it wrongly is real
            # harm: "Ana Luna" swallowed the empty "Anastasia 'Biscuit'" row
            # and with it Anastasia's own chance of ever linking.
            same = sorted({person_id} | {
                pid for pid in candidates
                if pid != person_id and message_counts.get(pid, 0) > 0
                and all_same_person({person_id, pid}, people)})
            conn.execute(
                "UPDATE camp_member SET person_id = ?, person_match = ?, "
                "person_ids = ? WHERE id = ?",
                (person_id, how, ",".join(str(p) for p in same), member_id))
            taken.update(same)
            linked += 1
            how_counts[how.split(",")[0]] += 1
    return linked, dict(how_counts)


def install(db_path: Path, people: list[dict]) -> tuple[int, int]:
    """Write the roster and its facts into the knowledge database.

    Replaces any previous roster wholesale: the sheets are the truth and this
    is fully re-derivable from them. Evidence for the old roster goes first,
    because the trigger that guards `evidence.source_id` would otherwise be
    pointing at rows that no longer exist.
    """
    conn = sqlite3.connect(db_path)
    create_enrichment_tables(conn)

    conn.execute("DELETE FROM evidence WHERE source_table = 'camp_member'")
    conn.execute("""DELETE FROM camp_fact WHERE id IN (
                        SELECT claim_id FROM evidence
                        WHERE claim_table='camp_fact'
                          AND source_table='camp_member')""")
    # Any roster fact whose evidence was already removed above.
    conn.execute("""DELETE FROM camp_fact
                    WHERE topic='roster' AND id NOT IN (
                        SELECT claim_id FROM evidence WHERE claim_table='camp_fact')
                      AND EXISTS (SELECT 1 FROM camp_member)""")
    conn.execute("DELETE FROM camp_member")

    members = []
    for r in people:
        record = render_record(r)
        cur = conn.execute(
            f"INSERT INTO camp_member ({','.join(COLUMNS)}, record) "
            f"VALUES ({','.join('?' * (len(COLUMNS) + 1))})",
            tuple(r[c] for c in COLUMNS) + (record,))
        members.append((cur.lastrowid, r, record))

    facts = roster_facts(members)
    n_evidence = 0
    for f in facts:
        cur = conn.execute(
            "INSERT INTO camp_fact (topic, fact, category, year) VALUES (?,?,?,?)",
            (f["topic"], f["fact"], f["category"], f["year"]))
        fact_id = cur.lastrowid
        for member_id, quote in f["cites"]:
            conn.execute(
                "INSERT INTO evidence (claim_table, claim_id, source_table, "
                "source_id, quote) VALUES ('camp_fact', ?, 'camp_member', ?, ?)",
                (fact_id, member_id, quote))
            n_evidence += 1
    linked, how_counts = link_to_chat(conn)
    conn.commit()
    verify_evidence_complete(conn)
    print(f"  linked to chat:   {linked} of {len(people)}")
    for how, n in sorted(how_counts.items(), key=lambda kv: -kv[1]):
        print(f"      {n:>3}  {how}")
    return len(facts), n_evidence


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", default=None,
                    help="signup sheets; defaults to the first of "
                         "<repo>/PS People or the main checkout's copy")
    ap.add_argument("--out", default="output/people.db")
    ap.add_argument("--csv", default="output/camp_members.csv")
    ap.add_argument("--install", nargs="?", const="output/ps_knowledge.db",
                    default=None,
                    help="also write camp_member and its facts into the "
                         "knowledge database (default output/ps_knowledge.db)")
    args = ap.parse_args()
    if args.src is None:
        args.src = str(default_src())

    src = Path(args.src) if Path(args.src).is_absolute() else HERE / args.src
    if not src.exists():
        raise SystemExit(f"no signup sheets at {src}")

    print(f"reading {src}")
    people = build(src)

    out_path = HERE / args.out if not Path(args.out).is_absolute() else Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(out_path)
    conn.execute("DROP TABLE IF EXISTS camp_member")
    conn.execute("""
        CREATE TABLE camp_member (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            name           TEXT NOT NULL,
            first_name     TEXT,
            last_name      TEXT,
            nickname       TEXT,
            email          TEXT,
            phone          TEXT,
            home_city      TEXT,
            years_attended TEXT,
            years_listed   TEXT,
            year_count     INTEGER,
            first_year     INTEGER,
            last_year      INTEGER,
            statuses       TEXT,
            match_notes    TEXT
        )""")
    conn.executemany(
        f"INSERT INTO camp_member ({','.join(COLUMNS)}) "
        f"VALUES ({','.join('?' * len(COLUMNS))})",
        [tuple(r[c] for c in COLUMNS) for r in people])
    conn.commit()

    csv_path = HERE / args.csv if not Path(args.csv).is_absolute() else Path(args.csv)
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(people)

    with_email = sum(1 for r in people if r["email"])
    returning = sum(1 for r in people if r["year_count"] > 1)
    print(f"\n{len(people)} people -> {out_path}")
    print(f"            -> {csv_path}")
    print(f"  with an email address: {with_email}")
    print(f"  attended more than one year: {returning}")

    if args.install:
        db = (Path(args.install) if Path(args.install).is_absolute()
              else HERE / args.install)
        if not db.exists():
            raise SystemExit(f"no knowledge database at {db}")
        n_facts, n_evidence = install(db, people)
        print(f"\ninstalled into {db}")
        print(f"  camp_member rows: {len(people)}")
        print(f"  roster facts:     {n_facts}  ({n_evidence} evidence rows)")


if __name__ == "__main__":
    main()
