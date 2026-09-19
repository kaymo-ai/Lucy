# Lucy evals

~30 common questions, run through a Python mirror of the app's answer path
against the shipped preview database. See
`docs/superpowers/specs/2026-08-19-lucy-evals-design.md`.

    cd scripts
    python3 eval_lucy.py                    # the gate: retrieval + composition
    python3 eval_lucy.py --update-golden    # after an intended behaviour change
    python3 eval_lucy.py --model both --compare --models-dir ~/lucy-models

Run the gate after touching: `Lucy/Retrieval.swift`, `Lucy/LucyVoice.swift`,
`Lucy/LucyBrain.swift`, `Lucy/EntityStore.swift`, `Lucy/PeopleStore.swift`
(then update the mirror first — the source-sync tests in
`scripts/tests/test_lucy_mirror.py` will insist), `scripts/build_preview_db.py`,
or the enrichment pipeline.

- `questions.yaml` — the cases. Expectations assert desired behaviour;
  `xfail:` names a known gap and keeps it on the report without gating.
- `golden/` — per-case row ids and sha256 hashes (never db text): the
  byte-for-byte contract Kotlin and Swift replay tests consume.
- Full-text run artifacts land in `scripts/output/eval/` (gitignored).

An XPASS means a known gap closed: remove its `xfail`, re-run
`--update-golden`, and commit both.
