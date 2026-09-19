# What this project taught us on 2026-08-19

The day started as the answer half of Help Lucy — the server side that lets
campmates answer the questions Lucy could not. That shipped. But the moment a
build reached the phone, the interesting failures were somewhere else entirely,
and most of this document is about those.

Everything below was found by reading `Documents/lucy-journal.txt` off the
device — 99 real answered turns, then 107 speech presses — and measuring. Almost
nothing here was found by reasoning about the code, and several confident
theories (including three of mine, noted where they happened) were wrong.

---

## 1. The through-line: she does what the shape of the input suggests

Three separate bugs today turned out to be one behaviour wearing different
clothes. The model answers in the shape it is handed, and every attempt to fix
that with better instructions lost to the shape.

| She was given | She produced |
|---|---|
| a section headed "ABOUT PIOTR — how they come across" | a character sketch, whatever was asked |
| weak matches under "answer from these first" | an answer from the weak matches |
| seven finished sentences each starting "We" | seven finished sentences each starting "We" |
| a persona paragraph that reads as a quotable line | that line, verbatim, as an answer |

In every case the prompt already contained an instruction telling her to do
something better, and in every case the instruction lost. The prompt's own rule
ordering says "the earlier the rule, the more it matters", and the manner
paragraph — the part that asks for judgement, weaving and brevity — is last.

**The lesson is not "write better instructions".** It is that changing what she
is *given* works, and changing what she is *told* mostly does not. Every fix
that held today was a change to the material: remove the section she recites,
reorder what leads, delete nothing from the question before searching.

---

## 2. The person portrait answered questions it was never asked

`PeopleStore.portrait(terms:)` receives the question's terms with stopwords
already stripped, and returns a person's whole card if any term matches their
name. It never sees the question. So these are identical inputs to it:

    "who is Piotr"                  -> [piotr]
    "why does Piotr not like water" -> [piotr, not, like, water]

Both got the full card. The second then got answered with Piotr's
administration-and-logistics biography — while the fact sheet *already carried*
the Book of Lucy passage where the Lord commands Piotr to build an ark to save
his family from floodwater. The camp's own joke about Piotr and water, retrieved
correctly, sitting four sections below a header that said to answer from the
other ones first.

The fix is a gate: strip the matched name from the terms, and if anything is
left, the question is about that something. Validated against all 71 distinct
questions in the journal before writing code — 4 got the full card and all four
were genuine identity questions; 16 were reduced and every one was a case that
was broken.

Two things that only fell out of doing it against real data:

- **"Do Marcus and Sammy like each other"** names two people and needs the
  existing `pairLines` path. A naive gate demotes it and loses the very rows
  that answer it.
- **The first version of the fix did nothing.** It reduced the portrait to
  "role" — and `role` is `person_profile.summary`, which *is* the biography she
  was reciting. The implementer flagged the doubt instead of shipping it
  quietly; checking that flag is what found item 3. The reduced portrait now
  emits nothing at all, because there is no short identifier available that is
  not just the first sentence of the same bio.

---

## 3. A length filter deleted a person's name

`Retrieval.terms` filtered `$0.count > 2`. "oz" is two characters.

Oz has a camp tradition named after him, appears throughout the lore, and is one
of the most-referenced people in the corpus. He was invisible to retrieval.
Worse, "who" and "is" are stopwords, so:

    "Who is Oz"  -> zero search terms

Six of the 71 distinct questions in the journal produced **no search terms at
all**. The journal shows what that costs:

- "Who is Oz" was answered from facts beginning *"We are provided with dinner
  prepared by the camp, but breakfast is DIY."*
- "Is Oz crazy" was answered from *"Tools Drill for lag screws 1 ladder."*

Not an error, not a refusal. A confident answer assembled from noise.

The filter is now `> 1`, with the two-letter noise words (`so no up oh be he us
ok`) added to the stoplist where they belong. Across the real questions, `oz`
was 10 of the 22 two-letter occurrences; the rest were one or two each. A
stoplist is the right mechanism for words that carry nothing. A length rule is
not, because it cannot tell a filler word from a name.

---

## 4. The fact sheet ranked by source table, not by relevance

`LucyVoice.factSheet` ordered sections by which table a row came from, and led
with camp facts under a header instructing "answer from these first".

Asked "Does Piotr hate water", she received five camp facts that matched the
single common word "water" — shower limits, refill lead times, gray-water rules
— above an entity fact that matched *both* "piotr" and the topic. The prompt
told her to prefer the weakest material on the sheet, and she obeyed.

Sections are now ordered by how many distinct question terms their best row
matches. Background stays last unconditionally; the prompt's Rule 4 ("our own
records outrank the background section") is untouched — this reorders only
*within* the camp's own records.

Two details worth keeping:

- **Per-source scores could not be used.** Camp, document and entity scores are
  on incomparable scales; a one-term camp match can outscore a two-term entity
  match by 3-4x under the existing formulas. Using them would have looked
  principled and fixed nothing. Distinct-term coverage is the comparable signal.
- **The 4000-character budget is spent top-down, dropping whole sections.** So
  reordering changes what gets *evicted*, not only what leads. Before this, a
  large block of weak camp facts could push the ark passage off the sheet
  entirely. There is now a test with 60 one-term camp rows exceeding the budget,
  proving the small two-term entity fact survives.

---

## 5. The refusal detector missed how she actually refuses

Help Lucy only offers "Do you know?" when the answer is detected as a refusal.
On the phone, the offer never appeared. The UI was fine; the detector was wrong.

Measured against all 99 answered turns: 33 caught, 5 missed. Two defects:

- **`hasPrefix` should have been `contains`.** *"OK ho. I don't know what a K
  is."* was missed for two words of filler in front of a phrase the rule already
  knew.
- **Three shapes were absent**, including the one from the owner's own question:
  *"I can only say what the facts state. The facts provided do not contain
  information about why it is so hot at Burning Man."*

### The partial index that cached the old answer

The server mirrors this as `lucy_is_refusal()`. Changing it with
`CREATE OR REPLACE FUNCTION` is not enough, and this is the trap:

`chatlog_refusal_idx` is a **partial index** whose predicate is
`lucy_is_refusal(answer)`. Postgres evaluates that predicate at index time and
stores the result. Replace the function and every chat turn already on the
server stays invisible to the gap query — the function returns the right answer
and the index never asks it again. The `REINDEX` in `005_refusal_shapes.sql` is
load-bearing, and it was proven with an Index Scan before and after.

This is the second time in one day that "the function is correct" and "the
migration actually takes effect" turned out to be different questions. See
item 9.

---

## 6. A press too short to record poisoned the next one

107 speech presses produced 100 transcripts. The missing seven had a cause:

A press released before `SpeechListener.start()` finished opening the tap hits
the `guard status == .listening` early return in `finish()` and returns nothing
— but the still-suspended `start()` completes anyway, leaving a live microphone
and an active audio session that nothing tears down. That corrupts the *next*
press. A stray tap poisons the recording after it, which is exactly what
"the audio gets easily truncated" feels like from the outside.

Fixed with a generation counter, so `start()` aborts itself if the press has
already ended, plus a `.failed` status and a journal line so the next
investigation can count it.

### The microphone is deaf for a quarter of a second, every time

Measured across 107 presses: median **0.284s**, min 0.237s, max 0.449s between
the press and the tap opening, while the audio category is set, the session
activated and the tap installed.

The design covers this with a ~0.3s tone fired on touch-down — "whoever waits
for the beep is speaking into a live microphone". Two problems. The tone does
not cover the 0.449s worst case. And the `RecordingAura` was driven by
`isRecording`, which is pure touch state, so the app's loudest visual signal
said "recording" while the microphone was shut. The `listening…` *text* was
gated correctly, which is why a first inspection said the UI was fine — it is
worth checking every affordance, not the first one you find.

The aura now shows a quieter "ready" phase until `status == .listening`. It is
deliberately not delayed: a control that does nothing for a third of a second on
touch feels broken, which is worse than the bug.

`prewarm()` still deliberately does not set the audio category. A standing
recording category stops iOS honouring the Taptic Engine — that trade is
documented in `SpeechListener.swift` and was not reopened.

---

## 7. Four instructions to be long, and none to be brief

Asked why answers were long and list-shaped, the prompt answered plainly. It
pushes toward length in four places:

    "say the whole thing"
    "If the facts carry four details that bear on the question, give all four"
    "a clipped answer that leaves out the bit they needed is worse than a longer one"
    "take a few sentences and use them ... instead of trading them for brevity"

The last was added by `86c0d7f` ("feat(voice): fuller answers") on 2026-08-11,
fixing a real problem — answers clipped so short they omitted the needed detail.

The defect is not any one of those sentences. It is that **length is a
constant**. Every question gets maximum. "Where is the Reno storage unit" wants
one sentence with an address and a lock code; a question about the ark wants a
story. Length should follow the question and the material.

The listing has a second cause, which is item 1 again: the corpus rows are
already finished prose in the camp's voice. There is nothing left to compose,
and under Rule 1 ("say only what the facts below say") copying is both safest
and easiest.

---

## 8. The prompt's own examples came back

`3548bc8` on 2026-08-10 fixed "she was answering with the prompt's own examples",
and added a doc comment stating the rule: *describe the transformation; never
write a sentence she could paste.*

It stripped the examples out of the **rules**. It left one in the **persona
paragraph**. On 2026-08-19, asked for the best DJ in camp, she answered:

> "I only carry the camp's sound system and I speak about the build, the bike
> fleet, and who meets the driver."

Verbatim from the opening line of her own prompt, converted to first person.
Rare — 3 turns in 99 — but a documented failure recurring in the one place the
original fix did not look. Any new prompt text, anywhere in the file, is subject
to that rule; "it is not a rule, it is the persona" is not an exemption.

---

## 9. Never amend a migration that has already been applied

`backend/app/db.py` records applied migrations by **filename** and skips any it
has seen. The plan for the answer half told an implementer to append new DDL to
`003_help_lucy.sql`, and claimed the `IF NOT EXISTS` forms made that safe.

They do not, because the file never runs a second time at all. On the camp VM,
where `push.sh` runs `migrate()` on every restart, the appended `ALTER TABLE`
would never execute and `GET /v1/answers` would fail on a missing column.
Safe-to-re-run is irrelevant when it never re-runs.

The signal was there and got read past: the implementer had to manually
`DELETE FROM schema_migration` to make its own tests pass. **A workaround needed
to make a migration take effect locally is evidence about production, not a
local inconvenience.**

---

## 10. What was actually verified, and what was not

Verified by running it:

- Both suites green: 131 backend tests, 65 Swift tests.
- Migrations apply 001->005 from scratch on a virgin database, and a database
  holding 003+004 correctly gains 005.
- The refusal function matches all five real journal refusals and not a real
  answer.
- The portrait gate's classification, against all 71 real questions.
- The two-letter fix, against the real question set.

**Not verified, and not claimed:**

- How anything looks. Nobody — not the author of this document, not any agent
  involved — has seen the recording aura, the thinking dots, or the "Do you
  know?" offer rendered. Every visual change in this day's work is unjudged.
- Whether any of it made her *better*. Answer quality is the owner's call, made
  on the device, and the honest position is that prompt and retrieval work here
  is empirical: the 2026-08-10 learnings are a list of confident prompt changes
  that did something else.
- The sync half of Help Lucy end to end, which needs a deploy that has not
  happened.

## 11. The method that worked

Reading the journal and counting beat reading the code and reasoning, every
single time.

- "Better after the turn-marker fix, then worse" could **not** be settled from
  the data — 81 of 99 turns are from the one day the prompt changed eight times.
  Saying so was better than constructing a timeline the data did not support.
- The portrait gate was validated against 71 real questions before any code was
  written, which is what surfaced the two-name carve-out.
- Refusal shapes were measured (33 caught, 5 missed) rather than guessed at.
- The 0.284s deaf window is a median of 107 samples, not an estimate.

And the strongest verification available for a claim of the form "this test
proves the feature works" is to **delete the feature and watch the test fail.**
Thirteen tests written across this session would have passed with the feature
removed. Every one was caught that way, by agents told to neuter their own work
and report what broke.

The corollary, which cost real time before it was learned: an agent reporting
"done, tests pass" is reporting on its tests, not on the feature.
