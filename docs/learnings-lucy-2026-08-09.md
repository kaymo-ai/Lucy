# What this project taught us on 2026-08-09

104 commits in one day, across four pieces of work: redacting personal data on
the way to the device, building a camp roster out of seven signup sheets,
writing a portrait of each member from the group chat, and putting both behind
a People tab.

Every number below was checked against the repo or the databases at
`95164d0`, not recalled. Several of them moved half a dozen times during the
day; only the current value is given.

---

## 1. Identity matching is the hard part, and every fix broke something

The camp exists in two name-spaces that do not agree. `camp_member` comes from
signup sheets and carries full names. `person` comes from a WhatsApp export
and carries whatever each member had saved for the others: "Oz", "Piotr",
"~ Laszlo", "Marc MC". There are 1,338 chat rows against 291 roster rows and
neither is a subset of the other.

Roughly eight distinct bugs surfaced here in one day, and **each was
introduced by the rule that fixed the previous one**. That is the single most
important thing to know before touching `link_to_chat` or `merge_variants` in
`scripts/build_people.py`. What follows is not a list of features; it is a
list of load-bearing decisions, each with the failure it prevents.

### The linking rules, in the order they must run

The order is the substance. Reordering these reintroduces named bugs.

1. **Drop rows already taken by another member** — but do not stop there. A
   chat identity belongs to one human, so a linked row is out of the running;
   abandoning the search when the best candidate is taken left Marc Mercer
   Cabrera unlinked, because a loose short-form rule had pulled "Marcus" into his
   candidates and Marcus Foster claimed it first.

2. **Discard contentless stubs if anything better exists.** The corpus is full
   of names someone was addressed by once with no messages behind them. They
   are never the right target and they *poison the rules below*: an empty
   "Jessie" row sat in Jess Sheldon's candidate set, disagreed with "Jessica",
   broke the same-person collapse in step 3, and handed her link to an empty
   row instead of the one holding 212 messages and her portrait. Stubs are
   still allowed to win when nothing else is available, which is how members
   with no chat presence stay linked at all.

3. **Collapse candidates that are the same human, taking the row with the
   writing.** WhatsApp re-exports someone whenever their saved name changes.

4. **Only then, let an exact full-name match settle a genuine disagreement.**

Step 3 must precede step 4. With 4 first, an exactly-correct name attached to
an empty row beat the row holding 1,136 messages — which is how the project's
most prolific chatter ended up with no portrait on his own card.

### Supporting rules

- **Surname initials.** "MC" is what "Mercer Cabrera" shortens to in a contacts
  list. Requiring exact surname equality kept the row with his messages and
  portrait out of the candidate set entirely. Initials only match against a
  surname that genuinely has several words, or every two-letter surname
  matches anything starting with it.

- **Punctuation is not a name.** WhatsApp decorates guessed push-names with a
  leading tilde. `split_name` was reading the tilde as the given name, so the
  row holding Laszlo Sandor's 135 messages never matched him. Tokens with no
  letters or digits are dropped. The tilde is also stripped for display in
  `PeopleStore.swift` — it is an export artefact, not part of anyone's name.

- **Merge by clique, not by pair.** `merge_variants` originally required
  exactly one partner, so a cluster could never merge: three rows reading
  "Simo", "Simo" and "Simone" each had two partners and none qualified.
  Components now merge when every member agrees with every other. That still
  keeps Anna M, Anna Morrow and Anna Moss apart, because Morrow and Moss
  disagree and the component is incomplete.

- **A missing surname agrees with anything.** The sheets say "Giordano" one
  year and "Gio Salvi" the next. Demanding two surnames match meant a
  first-name-only row could never join its person. The clique check is what
  makes this safe.

- **Home city breaks ties the name cannot.** Two rows reading just "Ana", both
  from SF, sat beside three different Anas living in San Francisco,
  Zihuatanejo and Barcelona. A name that fits three people and a city that
  fits one is not ambiguous. Six merges were resolved this way. Cities are
  canonicalised for the handful of abbreviations this camp actually writes and
  split on "/", because people answer with two homes.

- **`PS People/same-person.txt` for what is not derivable.** Anna M, Anna
  Morrow and Anna Moss are all called Anna, all in SF, with no shared email or
  year. No rule reaches the right answer, and a rule invented to force it
  would fuse two real people somewhere else. Human knowledge goes in a file
  (gitignored with the rest of the roster data), one group per line, read at
  build time. **Reach for this before inventing a ninth heuristic.**

### Where it stands

291 roster people, 244 linked to a chat identity, 142 of those to a row that
actually has messages. 86 of the 101 portraits reach a roster row. 28 merges
recorded, of which 6 were resolved by city and 1 by hand; 47 records are left
deliberately separate as ambiguous.

`person_match`, `person_ids` and `match_notes` record how every decision was
made. That auditability is the only reason these bugs were findable — each one
was reported by a human reading the People list and naming a pair. Expect
more, and prefer adding a line to `same-person.txt` over another rule.

---

## 2. The evidence spine, and where it nearly broke

Every derived claim cites a source row and a verbatim quote. The roster
respects this: 528 roster facts, 1,011 evidence rows citing `camp_member`,
and zero quotes that cannot be found in the row they cite.

The near-miss is worth internalising. `searchCampFacts` in `EntityStore.swift`
joined `camp_knowledge` on `e.source_id` alone, with no `source_table`
predicate. That was latent for as long as every `camp_fact` cited the same
table. The moment `camp_member` became a source table, roster ids (1–291)
collided with document ids (1–616) and every roster fact would have been
attributed to whichever unrelated document happened to share its number —
Lucy citing the generator manual for who camped in 2019.

A citation that names the wrong source is worse than no citation, because the
whole point of showing receipts is that they can be checked. When adding a
source table, audit every join that reads `evidence`.

Related: `CREATE TABLE IF NOT EXISTS` is a no-op against a database that
already has the table, so new columns are silently absent until an explicit
`ALTER TABLE`. The same trap applies to `CHECK` constraints, which are fixed
at creation. `enrich_schema.py` now carries both migrations. The failure mode
is an unhelpful "no such column" at the far end of a long pipeline.

---

## 3. Redaction: two wrong fixes before the right one

The device database shipped 616 raw documents, and retrieval falls back to
searching them whenever `camp_fact` returns nothing. Asking Lucy for the
camp's shipping address rendered a member's home address and the last four of
their card.

**First wrong fix: delete the receipts.** The reasoning was that
`EntityStore.authority()` already scores them −10 so nothing would be lost.
The evidence against it was in the same output that proposed it: a receipt
scored 33.2 *with* the penalty applied, because the frequency term swamps it.
Worse, receipts are the camp's only inventory record — "tourniquet" appears in
exactly one document in the entire corpus. Deleting them removes the answer to
a medical question.

**Second wrong fix: match addresses anywhere.** This removed the personal data
and also ate the camp's own storage-unit and supplier addresses, thirteen
redactions inside the manual alone, and four recipes — because "Place" is a
street suffix and "Heat a skillet... Place the bread down" matches an address
pattern. That is precisely the inventory loss the first section argued
against, reintroduced by its own remedy. It was caught only by listing the 98
touched documents and reading the ones that were not receipts.

**What worked: scope by label.** What separates a personal address from the
camp's is not the address, it is where it sits. Personal addresses appear
under a label on a receipt; the camp's appear in prose. Addresses are removed
only inside a window opened by an explicit label or closed by a trailing one
(the PDF extraction drops the label at page breaks but never the line after
the block). Card digits, order numbers and bank details are removed
everywhere, because none of them answers anything.

**The distinction that settled the whole question:** data given *to* the camp
versus data that arrived stuck to a saved PDF. A signup-sheet email was
volunteered for camp use and belongs on the device; a shipping address on an
Amazon receipt was never given to anyone and does not. This is also why gate
codes and padlock combinations are kept deliberately — they are the point of
the app.

---

## 4. Prompts state intentions; only code enforces them

The personality pass is the cleanest experiment on this we have, because it
made two demands of the same model in the same prompt.

**The prohibition on psychology held.** The prompt refuses to produce
personality assessments, with an explicit banned list and worked examples on
both sides. Zero of 101 portraits contain clinical language. Instructions were
enough.

**The pronoun rule did not.** The same prompt mandates they/them unless the
person states their own pronouns. Five of the first 101 ignored it and
inferred gender from names — for people who had never said anything about
their pronouns in hundreds of messages. Instructions were not enough, and in
an app the whole camp reads, a rule that holds 95% of the time is not a rule.

The fix was code, not a better sentence: detect the violation, re-run with the
failure named, and **drop the portrait entirely if it still will not comply**.
Nothing written about someone is a smaller wrong than something wrong about
them. Current state: 0 of 101.

The general lesson is that a prompt constraint needs a matching check whenever
the cost of violation is asymmetric. Prohibitions the model can satisfy by
omission tend to hold; requirements it must actively maintain across
paragraphs tend not to.

---

## 5. Verification

**Rendering found bugs that reading code did not.** Two examples. A duplicate
"Alex Tam" card — one from the roster, one from the chat — was invisible in
the source and obvious in a screenshot. So was `person_profile`'s gendered
prose sitting directly beneath a they/them portrait on the same card; each
table was internally consistent and the combination was not.

**A drifting test harness is worse than none.** A Python simulation of the
Swift retrieval scoring was used all day to check answers without rebuilding
the app. Its copy of the stop-word list omitted a word the app's list
contains, which produced a confident report of a regression that did not
exist, and a code change made to fix it that had to be reverted. If a harness
duplicates logic, something must keep the copies honest.

**Do not reproduce the data you are arguing should not travel.** The findings
document quoted a real home address and card last-four verbatim as the worked
example of what was leaking, and was committed that way; fixed in `95164d0`.
This is a category, not an incident: writeups, test fixtures, commit messages
and bug reports are all places where sensitive values get copied out of the
system that was carefully protecting them. Describe the shape.

**State what has not been verified.** Building and installing an app is not
the same as seeing it work, and a database is a file rather than a result.
Several claims during the day had to be walked back because a build succeeded
and nothing else had been checked.

---

## 6. The evening: a console session, and what it found

The chat was returning what Marcus called complete nonsense. Six commits
later the cause turned out to be four separate things, only one of which was
the chat.

### The model was not there, and nothing said so

The app had been deleted earlier in the day to clear a wedged developer disk
image. Deleting an app takes its Documents directory, and that is where the
2.89 GB `gemma-4-E2B-it-Q4_K_M.gguf` lives — it is too large to bundle, so it
is pushed once and stays. `LucyBrain.answer` returns false when the model is
absent and the app falls back to the composed string templates that predate
the LLM entirely. Those templates concatenate retrieved facts mechanically:
genuinely bad prose, produced silently, looking exactly like a model that got
worse.

**The tell is the journal.** `Journal.write` runs inside `answer`, after the
guard. No `Documents/lucy-journal.txt` means the model never ran, and one
`devicectl device info files` settles it in seconds. Check that before
reading a single answer, because every prompt change made while the model is
missing is untested by definition — three of them were.

### One layer silently suppressed another

`Retrieval.answer` searched documents only when nothing else matched:

```swift
docs: camp.isEmpty ? store.searchDocs(terms: t) : []
```

The reasoning was that an extracted fact beats the passage it came from,
which holds fact-for-fact and fails in aggregate. "How do I get into Empire
storage" pulled twelve camp facts about packing Doris during strike — enough
to count as "something matched" — so the manual was never opened, even though
its QUICK START section, the first thing in the document, gives the lot number
and says the key lives in the Empire General Store. Scored properly the manual
gets 49.4 against the best camp fact's 9.6.

A rule where one layer can suppress another entirely is too blunt. Offer both
and let ranking decide.

### Whole tables were invisible to retrieval

"Who knows how to fix a bike" could not be answered at all. Not badly — at
all. Retrieval searched entities, camp facts, documents and general knowledge,
and never `expertise` (300 rows) or `person_profile` (118), the two tables
that record what people know. Walter Lindell has "bike repair" against his name
and no question could reach it.

**Ask what retrieval queries, not what the database contains.** The data being
present says nothing about it being reachable, and a question that returns
nothing looks identical whether the answer is missing or merely unindexed.

### A capped list is all priority order

`SFSpeechRecognitionRequest.contextualStrings` biases dictation toward known
vocabulary, which is the fix for a recogniser that has never heard "Ozgur" or
"Bartkowski". Apple recommends around a hundred phrases.

The first version added things and then people. The entity list is
alphabetical and full of lore — "36hr Acid Hookah Sunset Cruise", "Book of
Faces", "@psatbm" — so it consumed the entire budget and **not one person's
name got in**, which was the exact complaint. Now 75 people (nickname, full
name, given name) and 25 things, with things filtered to kinds that name an
object and anything containing punctuation or digits dropped.

When a list is capped, its order is the whole design. Verify what actually
lands in it rather than trusting the intent.

### The console triple

Attach with `devicectl device process launch --console` and every question
prints:

```
QUESTION:     what was asked
FACTS GIVEN:  what retrieval handed her
ANSWERED:     what she said
```

This separates the two failure modes that are indistinguishable on screen.
Wrong facts mean retrieval is at fault and the model is faithfully repeating
rubbish. Right facts and a wrong answer mean the prompt is. They need opposite
fixes.

Two practical notes: relaunching the app from the home screen detaches the
console, which is why one session went silent after the launch lines; and the
Metal kernel compilation on first load buries the diagnostic lines, so filter
with `grep -v ggml_metal`.

### Prompt instructions that backfired

Both were written here and both were wrong in the same direction — too
prescriptive about form.

- *"Two or three sentences unless the question genuinely needs more"* produced
  answers that omitted the detail the person needed. Replaced with: short
  sentences, but say the whole thing.
- *"Stay in first person plural: we, our"* made her borrow the camp's voice,
  because the facts are written that way ("we store the barrels in Doris").
  She now speaks as herself and translates: "the barrels live in Doris".

### Separating ask from noting

Capture used to live in the chat: a shutter button in the composer, a
"remember" keyword, and every past photo and voice note replayed above the
greeting. Three problems in one. A question you want answered now and a thing
you want handed to the camp later are different acts; the keyword could not
tell "remember when we lost the generator" from a request to open a camera;
and the thread opened with a week of things you already knew, which you
scrolled past to reach the part where you could ask something.

NOTE is its own tab now, over the same capture machinery. The dead code went
with it rather than being left inert — a `capture` field on a turn, the photo
thumbnail, the sheet and its state, and `collect()` — because leaving it would
tell the next reader that the chat still does capture.

## 6a. The vocabulary gap, now confirmed four times

Every unanswerable question this session had the same shape: the asker's words
and the corpus's words do not overlap, and whole-word matching cannot cross
that.

| asked | corpus says | shared words |
|---|---|---|
| water **delivered** | service **vouchers**, pumping | none |
| **medkit** | **first aid** | none |
| **Empire** storage | **Emigrant** Storage | none |
| **fix** a bike | bike **repair** | none |

"medkit" appears in zero of the 616 documents. The camp has written "first
aid" since 2018. No ranking change reaches this — the right facts are never
candidates. This is the argument for E7 below, and it is now supported by four
independent cases rather than one.

## 6b. Where it stands tonight

Six commits after the documentation was written: `b87fb1c` through `98f2056`.
Working tree clean, 181 tests passing, everything installed on the phone.

Verified working from the console: the model loads and answers, the Note tab
files a photo, a voice memo and an on-device transcript, and Empire storage
now reaches the manual.

Not verified: the playful voice, first-person speech, longer answers, the
people search and the speech vocabulary were all shipped after the last
console read. They compile and the data behind them was checked by query, but
nobody has watched Lucy use them.

## 7. Open

- **`person_profile` pronouns.** 93 of 118 rows use he/she, from an earlier
  enrichment pass that predates the pronoun rule. They render in the "WHAT
  THEY DO" block directly under the they/them portraits, so the inconsistency
  is visible on a single card. A targeted rewrite of those 93 summaries under
  the same rule is the obvious next job.

- **Embeddings for `camp_fact` (E7).** Retrieval is whole-word keyword
  matching, so "how do we get water delivered" cannot reach "pick up service
  vouchers at the USS Camp" — the two share no words. Possessives miss for the
  same reason. Five scoring changes were made in one day, each fixing one case
  and exposing another; that pattern is the argument for embeddings rather
  than a sixth. The corpus already has EmbeddingGemma vectors for messages and
  documents.

- **Ten phone numbers extracted into `camp_fact` from documents.** 94
  `camp_fact` rows contain a phone number; 84 are the contact facts generated
  deliberately from the roster, and 10 were pulled out of camp documents by
  the enrichment pass. They were left alone as camp operational contact
  information, but the decision was never explicitly made.

- **Git.** `master` is 112 commits ahead of `origin/master` and unpushed. The
  pack is 203.93 MiB, over GitHub's limits, because of large blobs in
  January's history. This needs a history rewrite before anything can be
  pushed.

- **Test suite.** 181 passing. Seven errors in `test_swift_integration.py` are
  pre-existing and unrelated: the file expects a `conn` fixture that does not
  exist.

- **Expertise ranking ignores relevance.** `searchPeople` weights by the
  strength the enrichment recorded — strong 3, moderate 2, mentioned 1 — so
  for "who knows how to fix a bike" Walter Lindell's "bike repair" (mentioned)
  ranks below "Bike Inventory Management" (moderate). The most relevant person
  loses to a confidence score. Relevance to the question should beat
  confidence in the tag.

- **Lucy is handed more context than before.** Documents are always searched
  and people are now included, so most questions produce camp facts, document
  passages and names together. That is the fix for two failures today and the
  obvious cause of rambling tomorrow. If answers wander, rank across the
  layers rather than reverting either change.

- **The model is not in the bundle and does not survive a delete.** Anyone
  reinstalling the app must push `gemma-4-E2B-it-Q4_K_M.gguf` back to
  `Documents/` or the app silently answers from string templates. It lives at
  `SnailsNative/spike/models/`.
