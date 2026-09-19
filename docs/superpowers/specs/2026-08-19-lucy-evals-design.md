# Lucy evals: a regression gate for common questions

2026-08-19. Approved approach: Python runner with conformance goldens
(approach A of three considered; the alternatives were a runner with no
goldens, and building the platform replay tests in the same change).

## Why

Three changes routinely alter what Lucy says: prompt edits, retrieval edits,
and enrichment pipeline rebuilds. None of them has a check today beyond asking
her things by hand and reading the journal. The 08-10 learnings also leave
"whether the full model earns its 3.11 GB" as the highest-value open question
in the project, blocked on exactly this machinery: the same questions through
both models, side by side.

This gate is deliberately deterministic and offline. Vertex groundedness and
pairwise judging (the backend spec's evaluation idea) remain a later layer on
top; they are not this.

## The drift problem is the design problem

A Python simulation of Swift retrieval already burned this project once: its
stop-word list omitted one word the app's list contains, it reported a
regression that did not exist, and a code change was made and then reverted.
"If a harness duplicates logic, something must keep the copies honest."

With Android underway there will be three copies of retrieval — Swift, Kotlin,
Python — and the Android design itself names the failure: the same question
answered from different rows on different phones. So the eval kit is built as
a **contract**, not just a script:

- **Goldens.** Every run records, per question: the extracted terms, the rows
  each layer chose (by id), and sha256 hashes of the composed fact sheet and
  of the rendered prompt for each turn style (hashes, not text — see "What
  runs against what"). Kotlin `core/` JUnit tests (when Android retrieval
  lands) and a later Swift test target replay the same questions against the
  same database and must match byte-for-byte. Drift becomes a failing test on
  the platform that drifted.
- **Source-sync tests, immediately.** The platform replays are follow-up
  work, so the Python test suite parses the Swift sources directly and asserts
  the copies are identical today: the stop-word list from `Retrieval.swift`,
  both turn-marker pairs from `LlamaHandle.swift`, and the prompt text from
  `LucyBrain.swift`. The one-stop-word class of bug becomes a red test the
  moment either side edits without the other.
- **Data pinned.** Each golden records the sha256 of the database it ran
  against. The corpus syncs between machines through the GCS backup, so a
  golden mismatch must be attributable: logic drift and data drift are
  different bugs.

## What runs against what

The runner evals `scripts/output/enriched_preview.db` — the artifact that
ships, the same file `check_shipped_db.py` gates — so the eval sees exactly
what a phone will see, redaction included.

The database is deliberately not in git, and the preview carries camp
operational secrets on purpose, so the split for what gets checked in is:
**eval cases are fine in git** — questions and their expectation strings are
hand-written examples, not db dumps — while **goldens must not carry the db's
text**. Goldens store row ids and extracted terms in the clear and the fact
sheet and rendered prompts as sha256 hashes: the byte-for-byte contract
survives, nothing readable lands in git history, and the full-text run
artifacts for debugging a mismatch stay in `scripts/output/` (gitignored),
regenerable on any machine that has the database.

## Layout

    evals/questions.yaml            the case file, checked in
    evals/golden/<id>.json          one golden per case, checked in
    scripts/eval_lucy.py            the runner
    scripts/tests/test_eval_lucy.py unit + source-sync tests

Reports and transcripts go to `scripts/output/` (gitignored, like the
database). Goldens are regenerated only by an explicit `--update-golden`, and
the diff is reviewed in git like any other change.

## Case schema

~30 hand-written cases across the categories the app serves: logistics,
gear/entities, people and expertise, documents (the med kit, the manual's
QUICK START), lore, follow-ups, refusals, contradictions. Seeded with every
failure case the learnings docs name.

```yaml
- id: water-how
  q: how do we get water
  category: logistics
  previous: null            # set to exercise follow-up term blending
  retrieval:
    must_hit: [voucher]     # substrings the composed fact sheet must contain
    must_not_hit: []        # e.g. the Noah's Ark party in a water answer
  answer:                   # scored only when a model layer runs
    must_mention: [voucher] # case-insensitive whole-word
    must_not_mention: []    # leak markers, invented names
    may_refuse: false       # "I don't know" fails unless true
  xfail: E7-vocab           # known gap: reported every run, does not gate
```

`xfail` is for the retrieval gaps that are real and known — medkit cannot
reach "first aid", Empire cannot reach "Emigrant", water cannot reach
"service vouchers" — so the gate is green today while the gaps stay printed
on every report. An xfail that starts passing is flagged for promotion.

Refusal cases pin rule 5 of the prompt: the mockery decline must not repeat
the mockery or the name inside it, and a question the corpus cannot answer
must produce a plain refusal rather than plausible camp life.

Contact details that camp documents carry — vendor phone numbers, published
emails, anything the redaction pass deliberately ships — are legitimate
answer content (Marcus, 2026-08-19). `must_not_mention` polices invention
markers and prompt-echo only; it never polices contact-detail shapes, and no
case may fail Lucy for quoting a number her own fact sheet handed her.

## The runner

**Layer 1 — retrieval and composition. Always runs, no model, ~instant.**
Mirrors `Retrieval.terms/search/answer`, the four `EntityStore` search
queries, and `LucyVoice.factSheet`. Scores `must_hit`/`must_not_hit` against
the composed fact sheet. Emits goldens.

**Layer 2 — the model. Optional: `--model light|full|both`.**
Runs the same GGUF files via `llama-cpp-python` as **raw completion with the
exact `chatWrap` markers for that model** — never the library's chat
template, which is where the turn-marker bug would sneak back in. Greedy
sampling for reproducibility (a deliberate, documented divergence from the
app's sampler), 320-token cap as in the app. Model paths come from a config
argument; if a GGUF is absent the layer skips with a notice rather than
failing, so the retrieval gate runs anywhere the repo does.

**`--compare`.** With `--model both`, writes a side-by-side markdown table —
question, facts given, 1B answer, E2B answer, per-case verdicts — which is
the artifact the "does the full model earn its 3.11 GB" decision has been
waiting for.

**Exit.** Any non-xfail failure exits non-zero. The report prints per-case,
per-layer results, xfails with their reasons, and xpasses.

## Testing the harness itself

TDD throughout. The mirrored retrieval gets unit tests against the existing
tiny fixture database (`build_fixture.py`), including the cases the Swift
comments call out: whole-word containment ("water" must not match
"floodwaters"), the distinguishing-terms rule, overlap² scoring, follow-up
blending only when the question is thin. The source-sync tests run in the
same suite. None of the tests require a model or the full corpus.

## Out of scope

- Vertex groundedness / LLM-judge scoring — later layer, per the backend spec.
- The Kotlin and Swift golden-replay tests — follow-up work per platform;
  this change defines the contract they consume.
- On-device eval runs. The Simulator does not prove the phone and neither
  does a Mac; this gate catches logic and data regressions, not Metal or
  memory-pressure ones. The journal remains the tool for the device.
- Mining chat logs for real unanswered questions — the honest source of new
  cases, but the phone does not upload yet.
