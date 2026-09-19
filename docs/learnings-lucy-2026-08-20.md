# What this project taught us on 2026-08-20

Yesterday ended with the owner saying the answers were still bad. Today began
by building the thing that could say whether they were — and most of what
follows was found by that, or by a test, and almost none of it by reasoning
about the code.

Three things shipped broken today and each one looked finished. That is the
theme, and it is the same theme as the turn-marker bug: the failures that cost
the most here are silent, and the only defence is to run the thing and count.

---

## 1. E7 shipped, installed, and did nothing

Semantic retrieval was built, tested, gated, uploaded, downloaded onto the
phone and confirmed loaded. Every test passed. It contributed nothing to a
single answer.

With `n_gpu_layers = 999` the encoder returns a vector of the right length
full of `NaN`. `embed()` returns non-nil. Cosine against every stored vector
is NaN. NaN clears no floor, so semantic recall silently added zero rows. No
error, no log, no crash.

Everything around it was correct — the vectors, the packing, the arithmetic,
the union logic, the budget. The one thing nobody had run was the encoder
itself, because running it needs a 333 MB model that is not in the test
bundle. `EmbedderLiveTests` exists specifically because of that gap: it skips
when the model is absent, and the model was copied into one simulator's
Documents so it would not skip there.

It runs on the CPU now. That is not a placeholder. The failure is silent, the
GPU path is unverified on device, and the correct choice between a fast path
that might return nothing and a slower path that is known to work is not
close — especially for a 300M model encoding one short sentence per question,
which also leaves Metal entirely to the 3.1 GB chat model. `embed()` also
refuses a non-finite vector outright now, because a poisoned vector reports
downstream as "nothing was close", which is indistinguishable from a question
the corpus does not cover.

**A test that skips is not a test that passes.** The skip was correct and the
gap it left was real.

## 2. Then E7 worked, and made answers worse

Once it ran, the owner said he could not notice a difference. Measuring it
against the 19 questions actually asked on his phone after the encoder loaded:
it fired on 13 of 15, and added up to five rows a question, of which roughly
half were noise. "Does Piotr like water" gained his battery specifications and
his generator. "What are some camp stories" gained who signed up in 2017.

More material, more length, more list — which is the complaint that started
all of this. Two causes, both visible only once the cosines were printed:

**The floor was inside the noise.** It had been set from one good question.

    how do we get water delivered   0.569 .. 0.456   all water logistics
    do we cook our food             0.551 .. 0.487   all cooking
    does Piotr like water           0.413 .. 0.351   his battery, his shifts
    why do people make fun of Ed    0.293 .. 0.224   contact details

Everything above 0.45 answers the question; everything below merely mentions
something in it. At 0.35, "does Piotr like water" pulled in four rows of
Piotr admin — the exact bug `gatedPortrait` was written to fix, walking back
in through a door semantics had just opened.

**And the roster is a near-duplicate attractor.** 528 of 1,745 camp facts —
30% of the table — read "X signed up and said yes in YEAR" or "Contact details
for X". They are formulaic, so they cluster tightly, and any question carrying
a name or a year lands among them: "what's the camp placed in 2024" scored
0.546–0.569 on eight roster signups, *higher* than the genuine hits for "do we
cook our food". No floor separates those, because they are not weak matches —
they are strong matches to the wrong sense of the question. Lexical still
reaches the roster and should: a name is an exact match, which is high
precision. Similarity is not.

After: 6 of 15 questions get different material and every addition is
relevant. Capped at two rows, because reaching the row that words cannot reach
is one or two rows, not five.

**A threshold calibrated on one example is a guess wearing a number.**

## 3. The bench, and what it proved about rules

Every prompt change in this project had been made, built, installed and judged
by reading a handful of answers. `score_answers.py` scores a journal with no
labelling and no model: invented specifics (rule 1, checked), share lifted
verbatim, the "We ..." sentence rate, persona-paragraph overlap.
`replay_prompts.py` runs the camp's real questions and recorded fact sheets
through the real model under different prompts. `tune_server.py` is the same
with a person in the loop.

The owner's instinct was that fewer rules would be better. Measured, on 40
real questions:

                                       full    bare
    answers inventing a specific         2%     82%
    answers over half lifted verbatim   20%      2%
    median length                        15      56

With no rules she invents a camp specific in four answers out of five. But she
also nearly stops copying and writes three times longer. **Rule 1 is buying
truth and paying for it in voice**, and the listing everyone dislikes is the
price of the grounding rule. You cannot get the voice by deleting rules,
because deleting rules deletes the grounding — which is the argument for
putting voice in weights instead.

The same harness settled a question open since 2026-08-09. The owner
remembered things improving after the turn-marker fix and wondered whether he
had really just switched to the small model. He had, and the mechanism makes
it likely: before the fix the markers were hardcoded to *Gemma 3's*, so on the
small model they were RIGHT and the prompt well-formed, while on the full
model the whole prompt tokenised as ordinary characters. **The bug only ever
affected the full model.** And the small model does read freer — 51 words to
15 — while inventing a camp specific in more than one answer in four. Part of
what felt like improvement was the grounding letting go.

## 4. The same one-character bug, in a second file

`docs/learnings-lucy-2026-08-19.md` is partly about `Retrieval.terms`
filtering `$0.count > 2` and deleting "oz" out of every question that named
him. It was fixed there. `CampVocabulary.add` had the identical guard and was
missed, so Oz — who has a camp tradition named after him — was never handed to
the speech recogniser at all.

The journal shows what that cost: "Oz hole" transcribed as **"arsehole"** six
times, plus "Is in Oz hole", "No in Oz hole", "Oh in Oz hole". Sixteen of 105
distinct spoken questions arrived mistranscribed and the largest single
cluster was this word.

**When a rule is found to be wrong, grep for it.** A fix applied in one file
is not a fix.

## 5. Half the person was in the database and never selected

`personality` has held `voice` and `shows_up_as` since the first enrichment
run. The portrait query selected `pp.summary`, `y.summary`, `cares_about` and
`signature_quote`, and stopped. So the two fields that describe what somebody
is *like* — rather than what they administer — never reached the model, and
the section led with the administration paragraph the 2026-08-19 pass caught
her reciting back as an answer.

`lore.people` is populated on all 163 rows and names the cast of every story.
None of it was reachable from a person, because lore was only ever found by
matching words in the *question* — so "who is Piotr" got his summary and never
"Piotr's Red Sweater". For a camp, the stories somebody appears in *are* their
character.

`voice` earns its place twice over: `camp_fact` holds **zero emoji across
1,745 rows**, while a voice line reads "ends with a 😂 or ❤️". Enrichment
turns conversation into claims, and a claim has no tone — the camp's tone
survives only in `evidence.quote` (16% carry emoji) and in the personality
rows. That is worth knowing before deciding what a fine-tune should learn.

## 6. Names: the corpus and the camp disagree

"Cece → cc" and "Saami/Sammy don't match" turned out to be two different
failures, and only one was about hearing.

`entity_alias` has done mishearings and spellings for THINGS since
`enrich_ask_vocab` existed — 321 rows, which is why "Oz Hole" and "OzHole"
both resolve. **People had nothing**, and people are what questions are mostly
about. Worse, the variants already existed and were being deleted:
`dedupe_people` groups by normalised name, keeps the row with the most
messages and drops the rest, so "CeCe", "CeCe Garlan" and "Cece" all vanished
into "Cece Garland".

And "Sammy" is not a mishearing at all. The string appears **zero times** in
24,192 messages; the person is Saami Khoury. The camp says one name and the
corpus only ever wrote the other. No recogniser fix could bridge that.

`person_alias` now records what dedupe was throwing away, plus first and last
names split off full ones, plus a curated `manual_aliases.json` for the cases
the corpus cannot supply — Piotr is one word spelled correctly every time it
is written and mangled every time it is spoken.

The half that makes it work: the portrait matched aliases, and **nothing else
did**. Rather than teach every search what a name is, `Retrieval` widens the
question once — a term that is a known alias contributes the real name — and
every layer below matches as it always has. Added, never substituted: an alias
table is a guess about people, not a correction of them.

## 7. A heuristic invented 753 people

`process_shift_csv` fell back to "the first non-empty cell over two characters
is probably a name". That filled `person` with quoted Venmo transaction ids
(the quotes defeat `.isdigit()`), header cells like "Location", and dates like
"Tuesday, August 15 (6-10PM)". `is_shift_csv` decides from headers, and a
shopping list has a date column, so a 2018 alcohol order and a bank export
both classified as rotas.

The `shifts` table was 2,140 rows and had never held a real shift:
`person_name` and `role` held the *same cell*, `day` was mostly null, and 117
rows had a time slot. Its three biggest sources were shopping lists.

After: 181 rows, 154 people, **zero** that are not people. The floor check in
`ingest_all` failed at that point — it was calibrated at 2,000 against the
garbage — and was lowered to 10 with the reasoning written in.

The genuine sheets are 2-D grids: names spread across "Name 1/2/3" or "Role
1/2/3", and the 2018 sheet has a sentence where its header row should be. A
row-oriented parser cannot read them and now yields little rather than
fiction. Real shift data needs a grid parser, and the owner's call is that it
feeds the Info tab for display rather than retrieval — a rota is a table, and
the failure mode of paraphrasing a table is somebody missing their shift.

## 8. Recency was a tiebreaker pretending to be a weight

45% of the camp's facts are 2019 or older; 2018 and 2019 alone are 606 rows,
more than every year since 2022 combined. Recency was a capped bonus,
`min((year - 2016) * 0.4, 4)`, against an exact topic match worth 8. A 2018
topic match scored 8.8 and a 2026 two-word match scored 10 — one more matched
word flipped it.

It multiplies now, 0.88 per year floored at 0.35. Two things that went wrong
on the way, both caught rather than reasoned about:

- Applying the decay inside `authority()` was **backwards**. That function
  returns a negative number for receipts and invoices, and scaling a penalty
  by a decay shrinks it: an old receipt would have outranked a new one.
- Undated claims scored 1.0, which **rewarded missing metadata** — 160 camp
  facts carry no year, so it handed all of them a 3x advantage over anything
  dated 2019. It surfaced as a test failure: "how do we get water delivered"
  began preferring an undated vouchers row over the 2019 row that is the only
  row in the table containing the word "deliver". Undated is 0.6 now, about a
  2021 claim, because the honest prior for an unknown year is a typical year.

## 9. Ordering is not a preference

`dedupe_people.py` must run BEFORE enrichment. Run after, it deletes
non-people and the `person_content` they wrote, leaving 247 evidence rows
citing deleted messages; dropping those leaves 49 claims with no evidence at
all, and `verify_evidence_complete` refuses the artifact — correctly, because
an unevidenced claim is exactly what this project does not ship.

That was measured by doing it, in that order, and restoring from backup. The
whole sequence now lives in `scripts/reingest.sh` with each constraint written
beside the step it governs, because prose in a README already failed once here
and shipped a database missing 601 knowledge rows.

Related, and the same shape three times over: **a missing table makes
`sqlite3_prepare_v2` FAIL**, so the query returns nothing rather than less.
Naming `person_alias` unconditionally in the portrait query killed person
cards entirely on any database without it. `hasTable` guards it now, the way
`searchLore` and `searchGeneral` already guarded themselves.

## 10. What the method keeps proving

Everything above was found by running something and counting. Nothing was
found by reading code and thinking hard about it — several confident readings,
including of my own changes, were wrong until a measurement said so.

- E7's NaN: a test that had to be written because `embed()` had never executed.
- E7's noise: 19 real questions, replayed and diffed.
- The rules question: 40 real questions through the real model.
- The recency defects: two tests failing on cases nobody predicted.
- Oz: counted in the journal, not deduced.
- The person card: a query read line by line against the table it selects from.

And a smaller one worth keeping. A monitor watching that pipeline greped for
`^=== ` and stayed silent for an hour while everything worked, because the
banner prints ANSI colour codes first. **Silence is not success.** Check that
your check can fire.
