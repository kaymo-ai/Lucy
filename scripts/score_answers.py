#!/usr/bin/env python3
"""Score Lucy's answers against the facts she was actually given.

Every prompt change in this project has been judged by reading a few answers
on a phone, and two learnings documents record what that costs: a list of
confident changes that did something else. The 2026-08-19 pass could not even
settle whether she got better or worse after the turn markers were fixed,
because 81 of 99 turns came from the one day the prompt changed eight times.

This needs no labelled data and no model. Documents/lucy-journal.txt already
records QUESTION, FACTS GIVEN and ANSWERED for every turn, which is enough to
measure the four things that actually go wrong:

  invented   a name, number or year in the answer that appears in neither the
             facts nor the question. This is the invariant, stated exactly as
             LucyBrain's rule 1 states it, and it is the only metric here that
             is a bug rather than a preference.

  copied     how much of the answer is lifted verbatim from a row. The corpus
             rows are already finished prose in the camp's voice, so copying
             is the path of least resistance -- high numbers here are the
             "lists facts instead of writing" complaint, quantified.

  we_rate    sentences beginning "We". The listing signature: seven rows each
             starting "We" produce seven sentences each starting "We".

  leak       overlap with the persona paragraph. She has answered questions by
             reciting her own prompt (learnings 2026-08-19 section 8); this
             counts it instead of waiting to notice it.

Usage:
    python score_answers.py <journal.txt> [--since MARKER] [--verbose]

Compare two builds by scoring two journals, or one journal either side of a
marker line the app writes on launch.
"""
import argparse
import re
import statistics
import sys
from pathlib import Path

# The opening of LucyBrain.prompt. Kept here rather than imported because this
# script reads journals from builds whose prompt may differ from the tree's.
PERSONA = ("you are lucy the preservation society's snail art car you have "
           "been to burning man since 2018 you carry the camp's sound system "
           "and you are the camp's memory you belong to the camp and speak "
           "about it the build the bike fleet who meets the driver")

# A capitalised word mid-sentence, a bare number, or a year. Deliberately
# crude: the point is to catch a specific she never saw, and a false positive
# is cheap to eyeball while a false negative is the failure this exists for.
SPECIFIC = re.compile(r"\b(?:[A-Z][a-z]{2,}|\d{1,4}(?:[:.]\d{2})?)\b")

# Words that are capitalised in ordinary English and assert nothing about the
# camp. Without these every sentence start is a finding.
COMMON = {
    "The", "This", "That", "These", "Those", "There", "Their", "They", "Them",
    "We", "Our", "Ours", "You", "Your", "It", "Its", "He", "She", "His", "Her",
    "I", "If", "In", "On", "At", "To", "For", "And", "But", "Or", "So", "As",
    "When", "Where", "What", "Why", "How", "Who", "Which", "While", "With",
    "Without", "Because", "Before", "After", "During", "Since", "Until",
    "Lucy", "Yes", "No", "Not", "Nothing", "Nobody", "Everyone", "Someone",
    "Camp", "Burning", "Man", "One", "Two", "Three", "Four", "Five", "Six",
    "Seven", "Eight", "Nine", "Ten", "First", "Last", "Next", "Also", "Only",
    "Just", "Still", "Then", "Than", "Here", "Once", "Every", "Each", "Some",
    "Any", "All", "Both", "Most", "More", "Less", "Much", "Many", "Few",
    "Preservation", "Society", "Playa", "A", "An", "Of", "From", "By", "Do",
    "Does", "Did", "Is", "Are", "Was", "Were", "Be", "Been", "Being", "Have",
    "Has", "Had", "Can", "Could", "Should", "Would", "Will", "May", "Might",
}


def normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower())


def words(text: str) -> list[str]:
    return [w for w in normalise(text).split() if w]


def turns(journal: str) -> list[dict]:
    """Every turn the journal recorded with all three parts present."""
    out = []
    for block in journal.split("\n----\n"):
        q = re.search(r"QUESTION: (.+)", block)
        a = re.search(r"ANSWERED:\n(.*)", block, re.S)
        f = re.search(r"FACTS GIVEN:\n(.*?)(?=\n\[|\nANSWERED:)", block, re.S)
        if not f:
            # The empty-handed path journals colour instead of facts. Those
            # turns are scored too: she has material, just not an answer.
            f = re.search(r"EMPTY-HANDED, COLOUR GIVEN:\n(.*?)(?=\n\[|\nANSWERED:)",
                          block, re.S)
        if not (q and a and f):
            continue
        answer = a.group(1).strip()
        if not answer:
            continue
        out.append({"question": q.group(1).strip(),
                    "facts": f.group(1).strip(),
                    "answer": answer})
    return out


def invented(turn: dict) -> list[str]:
    """Specifics in the answer that were in neither the facts nor the question.

    This is rule 1, checked. A name, date, number or place not in the facts is
    one she does not know, so one that appears anyway came from the model.
    """
    haystack = normalise(turn["facts"] + " " + turn["question"])
    found = []
    for token in SPECIFIC.findall(turn["answer"]):
        if token in COMMON:
            continue
        if normalise(token).strip() in haystack.split():
            continue
        found.append(token)
    return sorted(set(found))


def copied(turn: dict, n: int = 8) -> float:
    """Share of the answer covered by n-grams lifted straight from the facts."""
    a, f = words(turn["answer"]), words(turn["facts"])
    if len(a) < n:
        return 0.0
    grams = {" ".join(f[i:i + n]) for i in range(len(f) - n + 1)}
    hits = sum(1 for i in range(len(a) - n + 1)
               if " ".join(a[i:i + n]) in grams)
    return hits / max(len(a) - n + 1, 1)


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def we_rate(turn: dict) -> float:
    s = sentences(turn["answer"])
    if not s:
        return 0.0
    return sum(1 for x in s if x.lower().startswith("we ")) / len(s)


def leak(turn: dict, n: int = 6) -> float:
    """Overlap with the persona paragraph, as a share of the answer."""
    a = words(turn["answer"])
    p = PERSONA.split()
    if len(a) < n:
        return 0.0
    grams = {" ".join(p[i:i + n]) for i in range(len(p) - n + 1)}
    hits = sum(1 for i in range(len(a) - n + 1)
               if " ".join(a[i:i + n]) in grams)
    return hits / max(len(a) - n + 1, 1)


def score(journal_text: str, verbose: bool = False) -> dict:
    rows = turns(journal_text)
    if not rows:
        raise SystemExit("no scorable turns found in that journal")

    inv, cop, wes, lek, lens = [], [], [], [], []
    worst = []
    for t in rows:
        i = invented(t)
        inv.append(len(i))
        cop.append(copied(t))
        wes.append(we_rate(t))
        lek.append(leak(t))
        lens.append(len(words(t["answer"])))
        if i or copied(t) > 0.5 or leak(t) > 0:
            worst.append((t, i))

    def med(xs):
        return statistics.median(xs) if xs else 0

    result = {
        "turns": len(rows),
        "invented_any": sum(1 for x in inv if x) / len(rows),
        "invented_median": med(inv),
        "copied_median": med(cop),
        "copied_over_half": sum(1 for x in cop if x > 0.5) / len(rows),
        "we_rate_median": med(wes),
        "leak_any": sum(1 for x in lek if x) / len(rows),
        "length_median": med(lens),
    }

    if verbose:
        print("\n--- turns worth looking at ---")
        for t, i in worst[:15]:
            print(f"\nQ: {t['question']}")
            print(f"A: {' '.join(t['answer'].split())[:200]}")
            if i:
                print(f"   INVENTED: {i}")
            if copied(t) > 0.5:
                print(f"   COPIED: {copied(t):.0%} of the answer is lifted verbatim")
            if leak(t) > 0:
                print(f"   PERSONA LEAK: {leak(t):.0%}")
    return result


def report(name: str, s: dict) -> None:
    print(f"\n===== {name} =====")
    print(f"turns scored                    {s['turns']}")
    print(f"answers with an invented specific  {s['invented_any']:.0%}")
    print(f"  median invented per answer       {s['invented_median']:.0f}")
    print(f"median share lifted verbatim       {s['copied_median']:.0%}")
    print(f"answers over half lifted           {s['copied_over_half']:.0%}")
    print(f'median share of sentences "We ..." {s["we_rate_median"]:.0%}')
    print(f"answers reciting the persona       {s['leak_any']:.0%}")
    print(f"median answer length (words)       {s['length_median']:.0f}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("journal")
    ap.add_argument("--since", help="only score turns after this marker appears")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    text = Path(args.journal).read_text(encoding="utf-8", errors="replace")
    if args.since:
        i = text.find(args.since)
        if i < 0:
            raise SystemExit(f"marker not found: {args.since}")
        text = text[i:]
    report(Path(args.journal).name, score(text, args.verbose))
    return 0


if __name__ == "__main__":
    sys.exit(main())
