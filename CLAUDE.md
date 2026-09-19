# Snails — working notes

Lucy is an offline iOS assistant for a Burning Man camp. She answers from the
camp's own WhatsApp history and documents, on the phone, with no signal. There
is also a camp server, which the phone talks to only when someone presses Sync.

Everything below is something that has already gone wrong here at least once.

## The invariant

**Retrieval supplies the facts; the model only phrases them.** `Retrieval`
picks rows out of the database, `LucyVoice` composes them, and Gemma turns that
into a sentence. A claim that the model invented is a bug, not a style problem.
When changing anything in this path, the question is not "does it read better"
but "could this sentence contain something no row supports".

Scoped, since 2026-08-20, to **camp particulars**: names, dates, numbers,
places, who did what, what the camp owns or decided. Those need a row, because
a wrong one sends somebody to the wrong warehouse. What she knows about the
desert and the world is hers to use, kept plainly apart from the camp's own
record. The old blanket rule also forbade her from knowing the desert is hot,
with a 16-row table standing in for all general knowledge.

Measured before you loosen it further: with NO rules she invents a camp
specific in 82% of answers, against 2% with them. Rule 1 buys truth and pays
for it in voice, and the flat listing everyone dislikes is that price. Deleting
rules deletes the grounding.

## Repository

- **Never `git add -A`.** Build directories and a 106 MB corpus live here and
  made the repo unpushable once already. Stage named paths.
- `ps_knowledge.db`, `Whatsapp PS Exports/` and `PS Processed/` are not in git.
  They are backed up with `scripts/backup_to_gcs.sh` to a private bucket.
- `backend/.env` never enters git. It exists on the VM and nowhere else.

## The models

There are two, and **they use different chat turn markers**:

| | file | markers |
|---|---|---|
| Full | `gemma-4-E2B-it-Q4_K_M.gguf`, 3.11 GB | `<\|turn>role` / `<turn\|>` |
| Light | `gemma-3-1b-it-Q4_K_M.gguf`, 806 MB | `<start_of_turn>` / `<end_of_turn>` |

`LlamaHandle.turnStyle` decides by **tokenising `<start_of_turn>` and counting
tokens** — never by filename. The markers were hardcoded to Gemma 3's from the
day the LLM was integrated, so every prompt reached the shipping model wrapped
in text that tokenised as ordinary characters. Nothing looked broken. Any
prompt tuning done before 2026-08-10 was tuned against malformed input.

The bug only ever affected the FULL model: the hardcoded markers were Gemma
3's, so on the Light model they were right all along. Anyone who switched to
Light before the fix got a genuine improvement that had nothing to do with any
fix. Measured 2026-08-20 on 40 real questions, same prompt: Light reads far
freer — 51 words against 15, and much less lifted verbatim — and invents a
camp specific in 28% of answers against E2B's 2%. Judge a prompt on both;
`scripts/tune_server.py` loads both at once for that reason.

**Never put a quotable example sentence in `LucyBrain.prompt`.** Rules used to
illustrate themselves — "the barrels live in Doris" — and she emitted them
verbatim as answers. It got *worse* after the markers were fixed, because a
model that parses the prompt properly copies its examples faithfully. Describe
the transformation; never write a sentence she could paste.

## Verifying anything

- **The Simulator does not prove the phone.** Background `URLSession` does not
  exist there; the camera does not exist there; the microphone is the Mac's.
  Push to the device with `Lucy/device.sh` and judge it there.
- **`Documents/lucy-journal.txt`** records `QUESTION` / `FACTS GIVEN` /
  `ANSWERED` per turn and which model loaded. Read it after any prompt change.
- **Instrument before theorising.** Three separate, plausible explanations for
  a dead haptic were each wrong; the fix came from adding a second code path
  and watching. When two or three honest attempts fail to explain a component,
  test whether it is running at all before inventing a fourth theory.
- **No claim about how something looks without an image in the message.**

## Where the camp's tone survives

Enrichment turns conversation into claims, and a claim has no tone.
`camp_fact` holds **zero emoji across 1,745 rows**. 22% of corpus messages
carry them, and they survive in exactly two places: `evidence.quote` (16% of
8,020) and the `personality` rows — `voice` ("ends with a 😂 or ❤️") and
`signature_quote`. If something needs to know what the camp sounds like rather
than what it decided, those are the tables.

## One word, four meanings

"Memory" meant four things and two of them were a tap apart on the same
screen. The split now:

| | |
|---|---|
| `ChatLog` | what you told Lucy — the SQLite log in Documents, and what Help Lucy will upload. Named after the backend's `/v1/chatlog`. |
| **Memory** tab | notes and captures. `NoteView`, `Capture`, `CaptureStore`. |
| `clearContext()` | llama's KV cache. Was `clearMemory()`. |
| `physicalMemory` | the phone's RAM. Apple's name, left alone. |

## The model that builds the corpus

`scripts/vertex_client.py`. Two settings, both measured, both easy to get
wrong in a way that fails quietly.

**`gemini-3.1-pro-preview`, not Flash.** Measured 2026-08-20 on a
byte-identical corpus: Flash found 64 distinct entities against Pro's 149.
The headcount is not the point — Flash keeps what you can photograph and
loses what the camp *says*.

| kind | Pro | Flash |
|---|---|---|
| tradition | 70 | 24 |
| asset | 26 | 7 |
| vehicle | 12 | 11 |

The hundred names it dropped were Edd's law, Ketamine Kittens, decomrecom,
Meatany, the burn book. Enrichment reads rambling group chat and decides a
phrase is a thing the camp has a name for; that is judgement, and capability
beats throughput on this pass. The preview tag is not a choice — probed
2026-08-20, `gemini-3.1-pro`, `-3.5-pro`, `-3.6-pro` and `-3.7-pro` all 404,
and the only other Pro that serves is 2.5.

**`LOCATION = "global"`, and that is a property of the model.** Asking
us-central1 for 3.6 Flash returns 404 while the console model card lists it
as available — both true, because access and a regional endpoint are
different questions, and the 404 text conflates them. The global endpoint has
its own quota under the `..._global` metrics, so a quota reading taken
against a region says nothing about what this consumes.

**Availability findings expire in weeks.** Every dated probe result in this
repo is a fact about its date. One from eleven days earlier was cited as
current on 2026-08-20 and was wrong.

## Only pay for what changed

`scripts/enrich_cache.py`, wired into `vertex_client.run_all` — the one
chokepoint all eight enrichment passes funnel through, so they became
incremental together without any of them learning about it.

1,830 of 27,163 messages were newer than 23 January. Six per cent, and every
rebuild re-derived the other ninety-four. The cost was never the money: it
set the iteration loop at hours, so "is Flash good enough" and "did the trim
break anything" each took a whole run to answer, and three runs were
abandoned part-way.

**Keyed by content, never by row id.** `ingest_all` recreates the database
and reassigns every autoincrement, so a row-keyed cache would return the
answer belonging to whatever message inherited that id — and look healthy
doing it. The key is sha256 over model, system prompt, schema and prompt, so
a prompt edit correctly invalidates everything.

Failures are never cached: caching a `None` would make a transient 5xx
permanent. `LUCY_NO_CACHE=1` forces a cold run.

## Most of the documents were exhaust

627 documents became 144 on 2026-08-20, and entity discovery went 149 → 138.
Cutting 77% cost almost nothing, which is the finding: **the camp's identity
lives in the chat, not in the spreadsheets.**

`parse_docs.should_ingest()` decides. It splits on SHAPE, not on folder or
year, because three earlier rules each deleted something load-bearing:

- *drop `finance`* deleted "Med kit supplies", the only document containing
  "tourniquet" — it is filed under finance because `categorize_file()` reads
  the PATH and it sits in the FINANCE folder.
- *drop `food` before 2022* deleted a recipe binder, 109 terms of real
  cooking vocabulary. A grocery list and a recipe binder are both food.
- *drop `shifts`* deleted "PS 2018 On-Playa Shifts", prose about how the camp
  runs, filed there because "shift" appears in its path.

An inventory says what the camp has and answers questions; a ledger says what
it paid and does not. They live in the same folder.

Decided with an **orphan-term test**, not by taste: a document is safe to drop
only when every distinctive word in it appears somewhere in the kept set. That
test rejected all three rules above.

It is a SKIP RULE. Every file stays in `PS Processed/`, so cutting too deep
costs one line.

**Floors move when the rule moves, never to make a red line go green.**
`ingest_all.EXPECTED_TABLES['camp_knowledge']` went 500 → 300 → 120 in one
day as the rule tightened, and tripping at stage 1 each time was correct — a
floor cannot tell a deliberate cut from a broken ingest.

## Nothing compared a build to the last one

`check_shipped_db.py` now refuses a build where any table shrank more than
25% against `scripts/build_baseline.json`, the last build a human accepted.
`--accept` moves the baseline; it is committed, so acceptance is a diff
somebody can question rather than a state on a laptop.

This exists because the Flash regression above passed every gate. The only
reason it was caught is that somebody was watching a number scroll past.

## Rebuilding the corpus

`scripts/reingest.sh` runs the twelve stages in the one order that works, with
each constraint written beside the step it governs. Use it rather than running
the passes by hand.

Two orderings are load-bearing, and one flag is:

- **`dedupe_people.py` before enrichment.** It deletes non-people and the
  `person_content` they wrote, and `evidence` cites `person_content` by id.
  Run it after and 247 evidence rows point at deleted messages; drop those and
  49 claims are left unevidenced, at which point `verify_evidence_complete`
  refuses the artifact. Correctly.
- **`embed_claims.py` after everything that writes a claim**, and before
  `build_preview_db.py`. `check_shipped_db.py` now refuses a database whose
  vector coverage is partial, so a forgotten run fails loudly instead of
  shipping half-blind retrieval.
- **`build_people.py --install`.** Without the flag it writes
  `output/people.db` and a CSV that nothing downstream reads, and
  `camp_member` stays empty. That, plus a `--src` default that resolved
  inside a git worktree where the gitignored corpus does not live, is why the
  roster — 291 people across seven sheets — was absent from every build until
  2026-08-20, leaving `evidence` citing two of its three permitted source
  tables. Both failures printed a line and carried on.

A WhatsApp "with media" export is ~2,000 photos around a single 1.8 MB
`_chat.txt`. Strip the media before putting one in `Whatsapp PS Exports/` —
the same export went 1.26 GB to 654 KB with no message lost. Loose exports in
the repo root are gitignored now, by shape rather than by name.

## Two things that fail silently in SQLite here

- **A missing table makes `sqlite3_prepare_v2` FAIL**, so the query returns
  NOTHING rather than returning less. Any query naming a table that a older
  database might not have must be guarded by `hasTable` — `searchLore` and
  `searchGeneral` already do. Naming `person_alias` unconditionally killed
  every person card until four tests caught it.
- **A partial index stores the RESULT of its predicate.** Replacing the
  function it calls does not re-evaluate it (hence the `REINDEX` in 005), and
  a *changed* predicate cannot be reindexed into place at all — the index has
  to be dropped and rebuilt (hence 006).

## Shipping the database

`scripts/build_preview_db.py::redact()` strips home addresses, card digits,
order and bank numbers. It **never deletes documents** — "Med kit supplies" is
the only document containing "tourniquet", and dropping it takes a medical
answer with it. The camp's *own* addresses must ship: the Monument warehouse,
the Fernley dump, the Tahoe decom house. What separates them is not the
address, it is whether it sits inside a shipping-label block.

`scripts/check_shipped_db.py` gates the artifact, not the function, and runs
before every release. Camp operational secrets — gate codes, padlock combos —
deliberately stay.

## The camp server

`backend/`, deployed to a Compute Engine VM in the `lucy-snails` project,
serving `https://lucy.marcusfoster.com`. FastAPI, Postgres, Caddy for TLS.

```bash
cd backend
docker compose -f compose.test.yml up -d     # throwaway Postgres on :55432
.venv/bin/python -m pytest                   # a real database, not a fake
./deploy/push.sh                             # copy up, rebuild, reload Caddy
```

Four things that are easy to break:

- **The note's id comes from the phone.** That is the whole idempotency
  mechanism for a retry on a bad connection. Do not let the server assign it.
- **Blobs are never deleted.** Identical bytes from two members are one file;
  unlinking it when one note is deleted blinds the other.
- **`seq` is assigned under an advisory lock.** Without it a client polling
  `?since=N` can step over a note that committed late and never see it.
- **`POSTGRES_PASSWORD` only applies when Postgres initialises an empty data
  directory.** Regenerating it in `.env` breaks authentication and looks like a
  config error.

Deploying from macOS: `tar` writes `._*` AppleDouble sidecars that match a
`*.sql` glob and kill the container on a binary read; the Caddyfile is a bind
mount so `compose up -d` leaves the old config loaded. `push.sh` handles both —
don't hand-roll a deploy that doesn't.

## The camp site

`site/index.html`, one self-contained file, served by the API behind a codeword
(`backend/app/site.py`). Server-side on purpose: the page is never sent to a
browser that has not passed, so it is not one `curl` away. Not basic auth —
that means a browser dialog asking for a username the camp does not have.

Screenshots come from `Lucy/render.sh` and must be re-rendered when the UI
changes; `docs/design/renders/` is an older two-screen design and does not
match the shipping app. **Do not publish an individual campmate's profile
card** — the People screenshot is the collapsed list for that reason.

## The city's own listings

`Playa data/` holds three pages saved out of a browser plus two API fetches,
and what each preserves is different. The Better Playa Guide carries its
whole payload in a `window.__GUIDE__` assignment, so the events are complete.
The two playamap.org pages are saved DOM: 1,181 camp names with addresses,
and for the art only names and artists — the page bulk-fetches descriptions
AFTER it loads, so the save never had them. `api-art-list.json` and
`api-art-desc.json` are `curl https://playamap.org/api/art/` (and `?desc=1`)
from 2026-08-26, and put all 327 descriptions back. Locations stay absent:
the city does not release them until the Sunday, and the Art row says so on
its face.

`scripts/playa_data.py` converts all of it into `Lucy/playa-*.json`. The
folder is gitignored (a source whose product is committed); the saved pages
are not re-savable after the burn, so it belongs in the corpus backup.

- **The map pages are windows-1252.** Read as UTF-8 they go binary, and then
  `grep` silently matches nothing at all — not an error, no output. An hour
  went into "the file has no `<title>`" before that was the answer.
- **The guide repeats itself, in three different ways**, and each one reached
  the screen before it was caught. 3,382 of 4,224 descriptions are the same
  text twice over. 173 slots duplicate another slot on the same event. And a
  slot can carry a date with no clock time, which is genuinely all-day 48
  times and is the same listing written twice the other 15. Undeduped, one
  Sunday drew four identical "Dusi Bubbly Rosé" rows.
- **Only an EXACT doubling is cut.** A near-doubling keeps everything: a
  repeated sentence costs a reader a moment and a wrongly trimmed one loses
  what the camp said.
- **The guide's `sm` field is model-written and is dropped**, along with its
  ranking scaffolding. What ships is what Burning Man's own listing said.
- **Counts live in `playa-counts.json`, not in the files they count.** The
  Info tab is drawn on every launch and counting the events means decoding
  1.1 MB. Written by the same script from the same data, so it still cannot
  drift; `PlayaDataTests` holds the two together.
- **Malformed addresses ship verbatim** and the script prints how many. 30 do
  not match `7:45 & B` — some are real (Epicenter, `CC@ 4:00` is a Center
  Camp frontage) and some are damage (`10:10:00 & 00 B`). Guessing which is
  which would be inventing an address.

Not wired into retrieval. Lucy cannot answer "where is Camp Juicy" from
these; they are a listing you read, like the manual. Doing that is a separate
piece of work and a bigger one — 1,181 rows of somebody else's data in
`camp_fact` would swamp the camp's own.

## Where things are written down

- `docs/learnings-lucy-2026-08-09.md`, `-2026-08-10.md`, `-2026-08-19.md`,
  `-2026-08-20.md` — what went wrong and why, at length. Read 08-10 before
  changing prompts, models or the deploy path; read 08-20 before touching
  retrieval, the enrichment order, or anything whose failure would be silent.
- `scripts/reingest.sh` — the corpus rebuild, in the only order that works,
  annotated with the reason for each step's position.
- `scripts/score_answers.py`, `replay_prompts.py`, `tune_server.py` — measure
  a prompt against the camp's real questions instead of reading three answers
  on a phone. `tune_server.py` is a local page with both models side by side.
- `docs/superpowers/specs/2026-08-10-lucy-backend-design.md` — the sync design,
  including four open questions, two of which are the camp's to answer.
- `docs/superpowers/plans/2026-08-10-lucy-backend-sync-slice.md` — the plan.
  All eleven tasks are done. The round trip was verified on Marcus's phone on
  2026-08-10: a note with a photo and a voice memo reached the server and came
  back byte-identical with its EXIF intact. Two decisions in the plan were
  reversed afterwards by Marcus and the plan text does not reflect them —
  nobody types a join code (the app carries the secret), and notes are
  anonymous (no name is collected or shown).
- `ARCHITECTURE.md` and the root `README.md` are **stale** — the README still
  mentions RunAnywhere and SmolLM2, neither of which is used.
