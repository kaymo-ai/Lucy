#!/usr/bin/env python3
"""
A short portrait of each camp member, from how they write in the group chat.

`person_profile` already says what someone does — "Piotr manages much of our
administration and logistics". This is the other half: how they come across.
Voice, what they keep coming back to, what they are like to camp alongside.

The prompt refuses to do psychology, and that is a correctness position as
much as a kindness one. A chat log is good evidence that someone writes in
short bursts at 2am about the generator; it is not evidence of their character,
and a model asked for a personality assessment will happily supply one anyway.
So the schema has no trait scores and the prompt has an explicit list of things
it must not say.

Evidence works as everywhere else, with the same defence the entity-records
pass needed: the model cites a message id and a quote, the id must be one this
job actually put in front of it, and the quote must be findable in that row's
real text. The stored quote is the database's text, never the model's.

Usage:
    python enrich_personality.py ./output/ps_knowledge.db            # dry run
    python enrich_personality.py ./output/ps_knowledge.db --yes
    python enrich_personality.py ./output/ps_knowledge.db --limit 5 --yes
    python enrich_personality.py ./output/ps_knowledge.db --person Oz --yes
"""
import argparse
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from enrich_schema import create_enrichment_tables, verify_evidence_complete
from vertex_client import run_all

# Below this there is not enough writing to say anything true about how someone
# writes. The camp has 1,338 names in the corpus and most are people who said
# three things in 2019.
MIN_MESSAGES = 30

# A cost and context bound, not a quality judgment. Most recent first, so a
# portrait reflects who someone is now rather than who they were in 2016.
MAX_MESSAGES = 300

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "voice": {"type": "string"},
        "cares_about": {"type": "string"},
        "shows_up_as": {"type": "string"},
        "signature_quote": {"type": "string"},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "message_id": {"type": "integer"},
                    "quote": {"type": "string"},
                },
                "required": ["message_id", "quote"],
            },
        },
    },
    "required": ["summary", "evidence"],
}


def norm_space(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().casefold()


def locate_quote(quote: str, content: str) -> str | None:
    """Return the source's own text for `quote`, or None if it isn't there.

    Matched whitespace- and case-insensitively, because a model will
    reasonably tidy both, but the *stored* quote is always sliced out of the
    database row. A quote nobody can reproduce from the source is not a quote.
    """
    if not quote or not content:
        return None
    hay, needle = norm_space(content), norm_space(quote)
    if not needle or needle not in hay:
        return None
    # Walk the original string, tracking its normalised offset, so the slice
    # can be taken from the real text rather than the normalised copy.
    start = hay.index(needle)
    end = start + len(needle)
    out_start = out_end = None
    n = 0
    prev_space = True
    for i, ch in enumerate(content):
        is_space = ch.isspace()
        if is_space and prev_space:
            continue
        if n == start and out_start is None:
            out_start = i
        if n == end:
            out_end = i
            break
        n += 1
        prev_space = is_space
    if out_start is None:
        return None
    return content[out_start:out_end].strip()


def candidates(conn, only: str | None, limit: int | None) -> list[tuple]:
    sql = """
        SELECT p.id, p.name, p.message_count
        FROM person p
        WHERE (SELECT COUNT(*) FROM person_content c
               WHERE c.person_id = p.id AND c.content IS NOT NULL
                 AND LENGTH(TRIM(c.content)) > 0) >= ?
        ORDER BY p.message_count DESC
    """
    rows = conn.execute(sql, (MIN_MESSAGES,)).fetchall()
    if only:
        rows = [r for r in rows if norm_space(r[1]) == norm_space(only)]
    return rows[:limit] if limit else rows


def messages_for(conn, person_id: int) -> list[tuple[int, str]]:
    rows = conn.execute("""
        SELECT id, content FROM person_content
        WHERE person_id = ? AND content IS NOT NULL
          AND LENGTH(TRIM(content)) > 0
        ORDER BY COALESCE(timestamp, '') DESC
        LIMIT ?
    """, (person_id, MAX_MESSAGES)).fetchall()
    return [(r[0], r[1]) for r in rows]


def build_prompt(name: str, msgs: list[tuple[int, str]]) -> str:
    lines = [f"[{mid}] {re.sub(r'\\s+', ' ', text).strip()}" for mid, text in msgs]
    return (f"These are messages written by {name} in the camp group chat, "
            f"most recent first. Each line begins with its message id in "
            f"square brackets — cite those ids in your evidence.\n\n"
            + "\n".join(lines))


# he/him/his, she/her/hers as whole words. Reflexives are listed explicitly
# because \b keeps "him" from reaching into "himself" — without them a
# portrait saying "built it himself" sailed past this check while the
# pronoun pass (enrich_pronouns.GENDERED, kept aligned with this) caught it.
_GENDERED = re.compile(
    r"\b(he|she|him|her|his|hers|himself|herself)\b", re.IGNORECASE)
_TEXT_FIELDS = ("summary", "voice", "cares_about", "shows_up_as")

# Whether someone said something about their own pronouns. Deliberately narrow:
# another member calling them "she" is not the person telling us anything.
_SELF_STATED = re.compile(
    r"\b(my pronouns|i'?m a (?:woman|man|guy|girl|dude)|as a (?:woman|man))\b",
    re.IGNORECASE)


def gendered_fields(parsed: dict) -> bool:
    return bool(_GENDERED.search(
        " ".join(str(parsed.get(f) or "") for f in _TEXT_FIELDS)))


def self_stated_pronouns(conn, person_id: int) -> bool:
    for (content,) in conn.execute(
            "SELECT content FROM person_content WHERE person_id = ?", (person_id,)):
        if content and _SELF_STATED.search(content):
            return True
    return False


def retry_gendered(conn, results: dict, jobs: dict, system_text: str,
                   names: dict[str, str] | None = None) -> int:
    """Re-run anyone the model gendered without warrant.

    The prompt asks for they/them and is ignored about five percent of the
    time — it inferred gender from names for five of the first hundred and one
    people, none of whom had ever said anything about their own pronouns.
    These portraits are read by the whole camp including their subject, so a
    rule that holds 95% of the time is not a rule. Asking again, with the
    failure named, is cheap; leaving it is not.
    """
    offenders = [pid for pid, parsed in results.items()
                 if parsed and gendered_fields(parsed)
                 and not self_stated_pronouns(conn, int(pid))]
    names = names or {}
    if not offenders:
        return 0
    print(f"\n{len(offenders)} portraits used he/she for someone who never "
          f"stated their pronouns — asking again")
    stern = system_text + (
        "\n\n## Correction\n\nA previous attempt at this portrait used 'he' "
        "or 'she' for someone who has never stated their pronouns anywhere "
        "in their messages. Do not do that. Use they/them throughout, "
        "including possessives ('their', not 'his' or 'her'). Rewrite any "
        "sentence that needs restructuring to avoid a gendered pronoun.")
    remaining = list(offenders)
    for attempt in range(2):
        retried = run_all([(pid, jobs[pid]) for pid in remaining], stern, SCHEMA)
        still = []
        for pid in remaining:
            parsed = retried.get(pid)
            if parsed and parsed.get("summary") and not gendered_fields(parsed):
                results[pid] = parsed
            else:
                still.append(pid)
        print(f"  attempt {attempt + 1}: {len(remaining) - len(still)} of "
              f"{len(remaining)} came back clean")
        remaining = still
        if not remaining:
            break

    # A portrait that will not stop guessing someone's gender does not ship.
    # Nothing written about them is a smaller wrong than something wrong.
    for pid in remaining:
        results.pop(pid, None)
    if remaining:
        print(f"  dropped {len(remaining)} portrait(s) that kept guessing: "
              f"{', '.join(names.get(pid, pid) for pid in remaining)}")
    return len(offenders) - len(remaining)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("db")
    ap.add_argument("--yes", action="store_true", help="skip the cost prompt")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--person", help="one person, by exact name")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        raise SystemExit(f"no database at {db}")
    conn = sqlite3.connect(db)
    create_enrichment_tables(conn)

    people = candidates(conn, args.person, args.limit)
    if not people:
        raise SystemExit("nobody has enough messages to write about")

    jobs, seen_ids, names = [], {}, {}
    for pid, name, _count in people:
        msgs = messages_for(conn, pid)
        if len(msgs) < MIN_MESSAGES:
            continue
        jobs.append((str(pid), build_prompt(name, msgs)))
        # The ids this job actually showed the model. A citation outside this
        # set is not evidence — the row existing proves nothing about whether
        # the model saw it.
        seen_ids[str(pid)] = {mid for mid, _ in msgs}
        names[str(pid)] = name

    print(f"{len(jobs)} people with >= {MIN_MESSAGES} messages")
    for pid, _ in jobs[:8]:
        print(f"    {names[pid]} ({len(seen_ids[pid])} messages)")
    if len(jobs) > 8:
        print(f"    ... and {len(jobs) - 8} more")
    if not args.yes:
        print("\ndry run — pass --yes to spend money on this")
        return

    system_text = (HERE / "prompts" / "personality.md").read_text()
    results = run_all(jobs, system_text, SCHEMA)
    retry_gendered(conn, results, dict(jobs), system_text, names)

    written = dropped_id = dropped_quote = 0
    for pid, parsed in results.items():
        if not parsed or not parsed.get("summary"):
            continue
        person_id = int(pid)
        cites = []
        for ev in parsed.get("evidence", []):
            mid = ev.get("message_id")
            if mid not in seen_ids[pid]:
                dropped_id += 1
                continue
            row = conn.execute(
                "SELECT content FROM person_content WHERE id = ?", (mid,)
            ).fetchone()
            located = locate_quote(ev.get("quote", ""), row[0] if row else "")
            if not located:
                dropped_quote += 1
                continue
            cites.append((mid, located))
        if not cites:
            # No evidence, no claim. The whole point of the spine.
            continue
        conn.execute("DELETE FROM evidence WHERE claim_table = 'personality' "
                     "AND claim_id = ?", (person_id,))
        conn.execute("DELETE FROM personality WHERE person_id = ?", (person_id,))
        conn.execute("""
            INSERT INTO personality (person_id, summary, voice, cares_about,
                                     shows_up_as, signature_quote)
            VALUES (?,?,?,?,?,?)""",
            (person_id, parsed["summary"].strip(),
             (parsed.get("voice") or "").strip(),
             (parsed.get("cares_about") or "").strip(),
             (parsed.get("shows_up_as") or "").strip(),
             (parsed.get("signature_quote") or "").strip()))
        for mid, quote in cites:
            conn.execute(
                "INSERT INTO evidence (claim_table, claim_id, source_table, "
                "source_id, quote) VALUES ('personality', ?, 'person_content', "
                "?, ?)", (person_id, mid, quote))
        written += 1

    conn.commit()
    verify_evidence_complete(conn)
    print(f"\nwrote {written} portraits")
    print(f"  dropped for citing a message not shown to the model: {dropped_id}")
    print(f"  dropped for a quote not found in the cited message:  {dropped_quote}")


if __name__ == "__main__":
    main()
