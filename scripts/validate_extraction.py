#!/usr/bin/env python3
"""
Acceptance gate for the enrichment pass, run against a case whose answer we know.

The Doris record in build_fixture.py was written by hand from the same messages
the model reads. This runs the extraction for real and scores the result against
that record, checking the things that are easy to get fluently wrong: keeping a
contradiction instead of resolving it, refusing to turn a question into a fact,
and quoting evidence verbatim rather than paraphrasing it.

Run this before trusting a prompt OR MODEL change at scale. Costs one request.

    python validate_extraction.py

First run, 2026-08-09, gemini-3.1-pro-preview: 7/7, 12 facts, all dates correct,
and it found two facts the hand-written record had missed.

WHY IT WAS USELESS ON 2026-08-20. The enrichment model was swapped to
gemini-3.6-flash and entity discovery fell from 149 to 64 -- the camp's
traditions from 70 to 24 -- with every gate green. This file is the one thing
that could have caught it, and it could not, for three independent reasons:

  1. MODEL was hardcoded here, so it tested the OLD model no matter what the
     pipeline was actually running.
  2. SYSTEM was an inline copy of the prompt, already diverged from
     prompts/entity_record.md, which is what the pipeline really sends. It
     scored a prompt nobody uses.
  3. Nothing invoked it. A repo-wide grep found no reference outside this
     docstring -- not in reingest.sh, not anywhere.

All three are fixed below. It now imports the live MODEL and the live prompt,
so it cannot drift from the pipeline again by construction.
"""
import json
import sqlite3
import sys
import time
from pathlib import Path

import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# Imported, never copied. A gate that carries its own copy of the thing it is
# gating tests a configuration nobody runs -- which is exactly what happened.
from vertex_client import MODEL, client

CORPUS = str(HERE / "output" / "ps_knowledge.db")

# The prompt the PIPELINE sends, read from the same file
# enrich_entity_records.py reads. This used to be an inline copy that had
# already diverged -- it carried neither the Cece/Marcus ladder example nor
# the alias rule -- so the gate scored a prompt that nothing in production
# used, and passed.
SYSTEM = (HERE / "prompts" / "entity_record.md").read_text()

SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "kind": {"type": "string",
                 "enum": ["vehicle", "structure", "tool", "place", "tradition", "asset"]},
        "summary": {"type": "string"},
        "aliases": {"type": "array", "items": {"type": "string"}},
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fact": {"type": "string"},
                    "category": {"type": "string",
                                 "enum": ["access", "contents", "location",
                                          "handling", "history"]},
                    "asserted_on": {"type": "string"},
                    "source_id": {"type": "integer"},
                    "quote": {"type": "string"},
                },
                "required": ["fact", "category", "asserted_on", "source_id", "quote"],
            },
        },
    },
    "required": ["name", "kind", "summary", "aliases", "facts"],
}


def main() -> None:
    c = sqlite3.connect(f"file:{CORPUS}?mode=ro", uri=True)
    rows = [
        (cid, str(ts)[:10], name or "unknown", " ".join(txt.split()))
        for cid, name, txt, ts in c.execute("""
            SELECT pc.id, p.name, pc.content, pc.timestamp
            FROM person_content pc LEFT JOIN person p ON p.id = pc.person_id
            WHERE pc.content LIKE '%oris%' AND pc.timestamp IS NOT NULL
            ORDER BY pc.timestamp""")
        # BOTH Doris and Boris. The SQL above says '%oris%' on purpose -- the
        # whole point of the Boris rows is that they are the tempting wrong
        # answer, and the model has to be GIVEN them and decline to cite them.
        # A `if "doris" in txt` filter used to sit here and removed every
        # Boris-only row, so Cece's "is this because we store ladders on
        # Boris?" never reached the model and the check that exists to catch
        # a question-turned-into-a-fact never ran. It scored 7/7 by not
        # asking.
        if "doris" in txt.lower() or "boris" in txt.lower()
    ]
    print(f"feeding {len(rows)} messages mentioning Doris\n")

    corpus_text = "\n".join(f"[{cid}] {ts} {name}: {txt}" for cid, ts, name, txt in rows)
    prompt = (f"Build the entity record for Doris from these messages.\n\n{corpus_text}")

    # vertex_client.client(), so project and location cannot drift from the
    # pipeline either -- the us-central1 404 on 2026-08-20 was exactly that.
    c_ai = client()
    t = time.time()
    r = c_ai.models.generate_content(
        model=MODEL, contents=prompt,
        config={"response_mime_type": "application/json",
                "response_schema": SCHEMA, "system_instruction": SYSTEM})
    elapsed = time.time() - t
    out = json.loads(r.text)

    print(f"=== {MODEL} — {elapsed:.1f}s — {len(out['facts'])} facts ===")
    print(f"{out['name']} ({out['kind']})  aliases={out['aliases']}")
    print(f"{out['summary']}\n")
    for f in sorted(out["facts"], key=lambda x: (x["category"], x["asserted_on"])):
        print(f"  [{f['category']:<9}] {f['asserted_on']}  {f['fact']}")
        print(f"              src {f['source_id']}: \"{f['quote'][:80]}\"")

    # ---- score against the hand-written record -------------------------------
    print("\n" + "=" * 74)
    print("SCORED AGAINST THE FIXTURE")
    print("=" * 74)
    by_src = {f["source_id"] for f in out["facts"]}

    # Cece's question about Boris, found BY WHAT IT SAYS.
    #
    # This was `42 not in by_src`. person_content.id is an autoincrement and
    # ingest_all recreates the database from scratch, so after any rebuild row
    # 42 is some other message -- and the check passed by verifying that a row
    # which no longer exists was not cited. Vacuously green, which is worse
    # than red. The Swift tests learned this same lesson in 304c170.
    question_row = next(
        (cid for cid, _, _, txt in rows
         if "is this because" in txt.lower() and "boris" in txt.lower()), None)
    text = json.dumps(out).lower()
    quotes_ok, quote_fails = 0, []
    corpus_by_id = {cid: txt for cid, _, _, txt in rows}
    for f in out["facts"]:
        src = corpus_by_id.get(f["source_id"], "")
        if f["quote"].strip().lower()[:60] in src.lower():
            quotes_ok += 1
        else:
            quote_fails.append((f["source_id"], f["quote"][:60]))

    ladder_facts = [f for f in out["facts"] if "ladder" in f["fact"].lower()]
    checks = [
        ("keeps BOTH ladder claims (the contradiction)", len(ladder_facts) >= 2),
        # Two different failures, kept apart: the model cited a question, or
        # the corpus no longer holds the message this check is built on. The
        # second is not a model result and must not read like one.
        (("excludes Cece's question about Boris (a question is not a fact)"
          if question_row is not None else
          "CANNOT CHECK: Cece's Boris question is not in this corpus"),
         question_row is not None and question_row not in by_src),
        ("has the padlock code 8765", "8765" in text),
        ("has the hammer-locked-inside catch-22",
         any("hammer" in f["fact"].lower() for f in out["facts"])),
        ("uses first person plural", " our " in text or "our " in text),
        ("avoids third-person 'the camp's'", "the camp's" not in text),
        (f"every quote verbatim in its cited row ({quotes_ok}/{len(out['facts'])})",
         not quote_fails),
    ]
    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if ladder_facts:
        print("\n  ladder facts returned:")
        for f in ladder_facts:
            print(f"    {f['asserted_on']}  {f['fact']}")
    if quote_fails:
        print("\n  quotes not found in cited row:")
        for sid, q in quote_fails:
            print(f"    src {sid}: {q}")

    print(f"\n  fixture had 12 facts; model returned {len(out['facts'])}")
    print(f"  VERDICT: {sum(ok for _, ok in checks)}/{len(checks)} checks passed")


if __name__ == "__main__":
    main()
