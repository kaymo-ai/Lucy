# Next-level enrichment, and a livelier, more helpful Lucy

2026-08-11. Two asks: take build-time enrichment up a level, and make Lucy's
answers more entertaining and more helpful. Both are bounded by the invariant:
**retrieval supplies the facts; the model only phrases them.** Everything
"entertaining" below is a new kind of row, never a new kind of licence.

Also bounded by the standing prompt rules: no quotable example sentences in
`LucyBrain.prompt`, and any prompt-adjacent change is judged from
`Documents/lucy-journal.txt` on the phone, not from the Simulator.

## What was found before designing

- `lore` — title, story, year, people — exists in the schema, is copied by
  `build_preview_db.py`, has fixture support, and holds **zero rows**. No
  enrichment stage writes it and nothing in the app reads it.
- `relationship` holds 1,358 rows (builds_with, co_shift, chapter, mentors)
  and nothing in the app reads them. "Do Marcus and Sammy like each other" is
  a real journal question that found nothing while the answer's raw material
  sat in the artifact.
- `personality` (101 portraits), `person_profile.summary`, and
  `signature_quote` reach the People tab but never the model. "Who is Piotr"
  through ASK gets expertise topics at best.
- `general_knowledge` is **absent from the current `enriched_preview.db`**.
  `bm_basics.py` is a manual post-step nothing invokes, so every preview
  rebuild wipes the background layer and `searchGeneral` quietly returns
  nothing. The app code for the layer is live; the data is not there.
- The three open items from `docs/learnings-lucy-2026-08-10.md` §12 all sit in
  this work's path: the E7 vocabulary gap, expertise ranking weighting
  confidence over relevance, and 93 of 118 `person_profile` rows using he/she
  under they/them portraits.

## Design

Seven pieces. 1–4 are enrichment (build time, Python); 5–7 are the phone
(Swift). Each is independently shippable.

### 1. Lore: fill the empty table

New stage `scripts/enrich_lore.py`. Walks the chat corpus in date windows
(month-sized, merged when sparse), asks the frontier model for **stories the
camp still tells** — a fire through Lucy's roof, the year a structure failed,
a running joke with staying power — and writes `lore` rows. Each story cites
the messages it came from through the existing `evidence` table, same as every
other claim. A consolidation pass dedupes stories that span windows.

Quality bar: a story needs at least two supporting messages, keeps the year it
happened, and names only people the corpus names. Stories are retellings in
plain prose, not quotes; the quotes stay in `evidence`.

Redaction: lore stories pass through the same `redact()` scope as documents on
their way into the preview, and `check_shipped_db.py` extends its scan to the
`lore` table.

### 2. Ask-vocabulary: the words a camper would use

The E7 gap — "medkit" cannot reach "first aid", "water delivered" cannot reach
"service vouchers" — is a vocabulary problem, and the full fix (on-device
embeddings) means shipping and running an embedding model on the phone. Not
now. The build-time version gets the four known cases and most of the class:

New stage `scripts/enrich_ask_vocab.py`:
- For each `camp_fact` row (1,745, batched ~25 per call), generate the words
  and short phrases someone would *ask* with that do not already appear in the
  fact or its topic. Written to a new table
  `ask_word (claim_table, claim_id, word)`, whole words, lowercased.
- For each `entity`, generate missing aliases the same way ("Empire storage" →
  Emigrant Storage) into the existing `entity_alias` table, marked with a
  distinct source so hand-made aliases are distinguishable.

Swift: `searchCampFacts` scores an ask-word match below a fact-text match
(fact text 3, ask word 2, topic 8 unchanged); entity alias search needs no
change because the words land in the table it already reads. Generated
vocabulary can only *add* candidates; ranking still prefers direct matches, so
a bad generated word costs a candidate row, never a wrong top answer.

`check_shipped_db.py` scans `ask_word` too — generated text is still text.

### 3. The 93 gendered profile summaries

The deferred-twice rewrite. Extend the people enrichment with a targeted pass:
select `person_profile` rows whose summary matches `\b(he|she|him|her|his|
hers)\b` (case-insensitive), rewrite under the same they/them rule the
portraits used, and **enforce in code**: re-run a failing rewrite, drop the
summary after repeated failure rather than ship a violation. The check is the
same regex the selection used, so the pass cannot claim success it did not
achieve. Runs against `ps_knowledge.db`; the preview rebuild carries it over.

### 4. General knowledge survives a rebuild

`build_preview_db.py` gains a final step that invokes `bm_basics.py`'s writer
on the preview it just built, so the background layer cannot be dropped by a
rebuild again. `check_shipped_db.py` gains a structural gate: `general_
knowledge` present and non-empty, `lore` present, `ask_word` present. The
gate runs on the artifact, which is the only thing that matters.

### 5. Expertise ranking: relevance over confidence

`searchPeople` currently scores `matched × strength`, so a "moderate" tag
that brushes the question beats a "mentioned" tag that is exactly the
question. Reorder: match count dominates (primary sort key), enrichment
strength breaks ties only. The learnings doc's own case — Walter Lindell's
"bike repair" losing to "Bike Inventory Management" — becomes the acceptance
check.

### 6. People in the fact sheet

When a question names a person (their name or nickname matches a term), the
fact sheet gains an `ABOUT <NAME>` section drawn from rows the phone already
ships: `person_profile.summary` (what they do), `personality.summary` and
`cares_about` (how they come across — labelled as drawn from our chat),
`signature_quote`, and their top `relationship` rows rendered as plain lines
(kind and other name; wording like "builds with", "shares shifts with").
When two people are named in one question, the relationship rows linking them
lead, which is what "do X and Y get on" is actually asking.

This is the "entertaining" half made honest: her colour comes from the rows,
and the signature quote is quoting, not inventing. The prompt does not change.

### 7. The fact-sheet budget, spent deliberately

`factSheet` currently truncates the whole sheet at 4,000 characters, which
chops whatever section lands last — today that is PEOPLE, after this work it
would be lore. Change: assemble sections in authority order (camp records,
docs, entities, about-person, people-who-know, lore, ambiguity note,
background) and stop adding *whole sections* when the budget is reached,
rather than cutting mid-line. Lore and about-person sections are capped (one
story, one portrait) so they cannot crowd out operational answers.

Composed fallback (`LucyVoice.reply`) learns the same two sections in plain
form, so a phone without a model gets the same substance.

## What is deliberately not in this design

- **On-device embedding search.** The right E7 endgame, and a model download,
  a vector index and a latency budget away. Ask-vocab is the build-time step
  that needs no new runtime.
- **Prompt changes.** The five rules were tuned against journals this week.
  New material enters through the fact sheet, labelled, and the journal on
  the phone says whether the phrasing follows.
- **Blank-answer rework.** Touched by the gap-naming work two commits ago;
  not stacked on.

## Verification

- Every Python stage: pytest against fixtures, Vertex mocked, in
  `scripts/tests/` alongside the existing per-stage tests.
- Real enrichment runs happen against `ps_knowledge.db` **after** a snapshot
  copy (the existing snapshot pattern), then `build_preview_db.py`,
  then `check_shipped_db.py`, then the corpus-inspector pass.
- Swift: `xcodegen generate` + `xcodebuild build` (no signing) proves it
  compiles; behaviour is judged on the phone from the journal, by Marcus —
  reported honestly as unverified until then.
