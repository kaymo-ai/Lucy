#!/usr/bin/env python3
"""
The 93 gendered profile summaries — rewrite `person_profile.summary` rows
that say he/she into they/them form, and enforce the result in code.

`person_profile` predates the pronoun rule the personality portraits were
written under, so 93 of 118 summaries use gendered pronouns for people who
never stated theirs — rendered in the "WHAT THEY DO" block directly beneath
a they/them portrait on the same card. This is the deferred-twice fix
(docs/learnings-lucy-2026-08-09.md §7, -2026-08-10.md §12).

The portraits taught the mechanism: the prompt asked for they/them and was
ignored about five percent of the time, and the fix was code, not a better
sentence. Same here. Selection and verification use the SAME regex, so this
pass cannot claim success it did not achieve — a rewrite that still matches
is retried with the failure named, and one that will not comply has its
summary dropped to empty rather than shipped. Nothing said about someone is
a smaller wrong than something wrong about them.

The rewrite must change pronouns and nothing else. The summary's facts are
already evidenced, and the evidence rows are not touched — so a rewrite that
drifts would detach the text from its citations. Two cheap guards reject
drift: length within 40% of the original, and no capitalised word (a name, a
place, a group) may disappear.

The portraits' one exception carries over: someone whose own messages state
their pronouns keeps them. Exempt rows are reported and left alone, so they
are expected — not pending work — on every future run.

Usage:
    python enrich_pronouns.py ./output/ps_knowledge.db             # dry run
    python enrich_pronouns.py ./output/ps_knowledge.db --yes
    python enrich_pronouns.py ./output/ps_knowledge.db --limit 5 --yes
"""
import argparse
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from enrich_personality import self_stated_pronouns
from enrich_schema import verify_evidence_complete
from vertex_client import run_all

# Selection AND verification. One pattern, used twice, is the whole design:
# a rewrite only counts as done if the check that found the problem can no
# longer find it. Word boundaries matter — "her" sits inside "there" and
# "his" inside "this" and "history", and none of those may match. The
# reflexives are listed because \b keeps "him" from reaching into "himself".
GENDERED = re.compile(
    r"\b(he|she|him|her|his|hers|himself|herself)\b", re.IGNORECASE)

# A faithful pronoun swap barely moves the character count. A rewrite outside
# this band changed more than pronouns, whatever it claims.
LENGTH_TOLERANCE = 0.4

# Ask, ask again with the failure named, ask once more. After that the
# summary is dropped rather than shipped gendered.
MAX_ATTEMPTS = 3

SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
}

# Describes the transformation and never demonstrates it — a worked example
# sentence in a prompt has been emitted verbatim as output here before
# (CLAUDE.md). Naming individual pronoun words is fine; a sentence is not.
SYSTEM_TEXT = """\
You are given one short profile summary of a camp member. It currently \
refers to that person with gendered pronouns. Rewrite it so that every \
pronoun referring to them uses the they/them form instead — subject, \
object, possessive, and reflexive — with verb agreement adjusted wherever \
the swap demands it.

Change nothing else. Every name, every fact, and every phrase that is not \
a pronoun (or a verb the pronoun swap forces to agree) stays exactly as \
written. Do not shorten, expand, reorder, or improve the text: the rewrite \
should differ from the original only where a gendered pronoun used to be.

Return the rewritten text as the "summary" field.
"""

# Appended on retries, mirroring enrich_personality's correction: name the
# failure, do not soften the rule.
CORRECTION = """

## Correction

A previous attempt at this rewrite failed its checks: either a gendered \
pronoun survived, or the text drifted — a name went missing, or the length \
changed by more than a pronoun swap can explain. Rewrite again: they/them \
throughout, including possessives, and everything that is not a pronoun \
kept exactly as the original has it."""


# --------------------------------------------------------------------------
# Selection — and the exemption the portraits established
# --------------------------------------------------------------------------

def select_gendered(conn, limit: int | None = None) -> list[tuple[int, str]]:
    """person_profile rows whose summary the regex catches, in id order for a
    deterministic --limit slice. Rows already rewritten (or dropped to empty)
    no longer match, which is what makes a second run find nothing."""
    rows = conn.execute("""
        SELECT person_id, summary FROM person_profile
        WHERE summary IS NOT NULL AND TRIM(summary) != ''
        ORDER BY person_id
    """).fetchall()
    hits = [(pid, s) for pid, s in rows if GENDERED.search(s)]
    return hits[:limit] if limit is not None else hits


def partition_exempt(conn, rows: list[tuple[int, str]]
                     ) -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """Split (to_rewrite, exempt). Exempt means the person's own messages
    state their pronouns — the same deliberately narrow test the portraits
    use (another member calling them "she" proves nothing)."""
    todo, exempt = [], []
    for pid, summary in rows:
        (exempt if self_stated_pronouns(conn, pid) else todo).append((pid, summary))
    return todo, exempt


# --------------------------------------------------------------------------
# The checks a rewrite must pass — the regex, then two drift guards
# --------------------------------------------------------------------------

_CAP_WORD = re.compile(r"\b[A-Z][\w'’-]*")

# Excluded from the capitalised-word comparison on both sides: a compliant
# rewrite legitimately loses a sentence-initial "He" and gains a "They", and
# neither is a name.
_PRONOUN_FORMS = {
    "he", "she", "him", "her", "his", "hers", "himself", "herself",
    "they", "them", "their", "theirs", "themself", "themselves",
}


def cap_words(text: str) -> set[str]:
    return {w for w in _CAP_WORD.findall(text or "")
            if w.casefold() not in _PRONOUN_FORMS}


def rejection(original: str, rewrite: str) -> str | None:
    """Why this rewrite cannot be accepted, or None if it can.

    Ordered by severity: a surviving pronoun is the failure this pass exists
    to prevent; the other two catch a model that fixed the pronouns by
    rewriting the summary into something its evidence no longer supports."""
    if not rewrite or not rewrite.strip():
        return "empty rewrite"
    if GENDERED.search(rewrite):
        return "gendered pronoun survived"
    if abs(len(rewrite) - len(original)) > LENGTH_TOLERANCE * len(original):
        return (f"length drifted ({len(original)} -> {len(rewrite)} chars, "
                f"beyond {LENGTH_TOLERANCE:.0%})")
    lost = cap_words(original) - cap_words(rewrite)
    if lost:
        return "capitalised words lost: " + ", ".join(sorted(lost))
    return None


# --------------------------------------------------------------------------
# The rewrite loop — retry with the failure named, then drop
# --------------------------------------------------------------------------

def rewrite_summaries(todo: list[tuple[int, str]]
                      ) -> tuple[dict[int, str], list[int], list[int]]:
    """Returns (accepted {person_id: new_summary}, dropped, unreached).

    Every candidate — first attempt or retry — faces the same rejection()
    gate, so nothing reaches `accepted` that the selection regex would still
    catch. What survives MAX_ATTEMPTS without passing is dropped, never
    stored as-is.

    Dropping is reserved for a model that ANSWERED and would not comply. A
    row whose every attempt came back empty never met the model at all —
    the 2026-08-11 run hit expired credentials and every one of 93 rewrites
    was "empty", which under drop-on-any-failure emptied 93 good summaries
    to fix zero pronouns. Those rows are returned as `unreached` and left
    exactly as they were: an outage is not a verdict."""
    remaining = dict(todo)
    accepted: dict[int, str] = {}
    answered: set[int] = set()
    for attempt in range(MAX_ATTEMPTS):
        if not remaining:
            break
        system = SYSTEM_TEXT if attempt == 0 else SYSTEM_TEXT + CORRECTION
        jobs = [(str(pid), original) for pid, original in remaining.items()]
        results = run_all(jobs, system, SCHEMA)
        still: dict[int, str] = {}
        for pid, original in remaining.items():
            parsed = results.get(str(pid))
            rewrite = ((parsed or {}).get("summary") or "").strip()
            if rewrite:
                answered.add(pid)
            reason = rejection(original, rewrite)
            if reason is None:
                accepted[pid] = rewrite
            else:
                print(f"  person {pid} attempt {attempt + 1}: {reason}")
                still[pid] = original
        print(f"  attempt {attempt + 1}: {len(remaining) - len(still)} of "
              f"{len(remaining)} came back clean")
        remaining = still
    dropped = sorted(pid for pid in remaining if pid in answered)
    unreached = sorted(pid for pid in remaining if pid not in answered)
    return accepted, dropped, unreached


def apply_changes(conn, accepted: dict[int, str], dropped: list[int]) -> None:
    """Accepted rewrites replace the summary; dropped ones become empty —
    the row (years_active, chapter, known_for) and its evidence stay, since
    neither ever contained the violation. An empty summary no longer matches
    the selection regex, so a dropped row is settled, not retried forever."""
    for pid, summary in accepted.items():
        conn.execute("UPDATE person_profile SET summary = ? WHERE person_id = ?",
                     (summary, pid))
    for pid in dropped:
        conn.execute("UPDATE person_profile SET summary = '' WHERE person_id = ?",
                     (pid,))


def run_pass(conn, limit: int | None = None) -> dict:
    """Select, exempt, rewrite, enforce, apply. Caller commits."""
    selected = select_gendered(conn, limit)
    todo, exempt = partition_exempt(conn, selected)
    accepted: dict[int, str] = {}
    dropped: list[int] = []
    unreached: list[int] = []
    if todo:
        accepted, dropped, unreached = rewrite_summaries(todo)
        apply_changes(conn, accepted, dropped)
    return {
        "selected": len(selected),
        "exempt": [pid for pid, _ in exempt],
        "rewritten": len(accepted),
        "dropped": dropped,
        "unreached": unreached,
    }


# --------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("db")
    ap.add_argument("--yes", action="store_true", help="skip the cost prompt")
    ap.add_argument("--limit", type=int,
                    help="only the first N matching rows (cheap trial run)")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        raise SystemExit(f"no database at {db}")
    conn = sqlite3.connect(db)

    def name_of(pid: int) -> str:
        row = conn.execute("SELECT name FROM person WHERE id = ?", (pid,)).fetchone()
        return row[0] if row else str(pid)

    selected = select_gendered(conn, args.limit)
    todo, exempt = partition_exempt(conn, selected)
    print(f"{len(selected)} summaries use gendered pronouns; "
          f"{len(exempt)} exempt (stated their own pronouns), "
          f"{len(todo)} to rewrite")
    for pid, _ in exempt:
        print(f"    exempt: {name_of(pid)}")
    if not todo:
        print("nothing to do")
        return
    if not args.yes:
        print("\ndry run — pass --yes to spend money on this")
        return

    stats = run_pass(conn, args.limit)
    verify_evidence_complete(conn)
    conn.commit()

    print(f"\nrewrote {stats['rewritten']} summaries")
    if stats["dropped"]:
        # The honest failure mode: named, not hidden in a count.
        print(f"dropped {len(stats['dropped'])} summaries that kept failing: "
              + ", ".join(name_of(pid) for pid in stats["dropped"]))
    if stats["unreached"]:
        # Not a verdict on the summaries — the model never answered for
        # these. Check credentials and quota, then run again; nothing about
        # these rows was changed.
        print(f"{len(stats['unreached'])} summaries unreached — every "
              "attempt returned nothing (auth or quota?). Left untouched.")
    left = select_gendered(conn)
    still_matching = [pid for pid, _ in left
                      if pid not in set(stats["exempt"])]
    if still_matching:
        # Only reachable with --limit; without it the loop guarantees zero.
        print(f"{len(still_matching)} gendered summaries remain (run again)")


if __name__ == "__main__":
    main()
