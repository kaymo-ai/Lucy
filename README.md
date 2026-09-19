# Lucy

Lucy is an offline assistant for a Burning Man camp. She answers questions
about how the camp runs — where the ladders are, how the generator starts,
who to ask about the water truck — from the camp's own WhatsApp history and
documents, on an iPhone, in a place with no signal. There is also a small
camp server: when someone presses Sync, notes taken on the playa go up and
everyone else's come down.

There is a write-up with screenshots of the app in use at
[lucy.marcusfoster.com/tech](https://lucy.marcusfoster.com/tech).

This repository is the code. **The camp's data is not here**, and neither
is anything derived from it. The app builds, but it knows nothing until you
run the pipeline over a corpus of your own. See
[What is not in this repository](#what-is-not-in-this-repository).

## The one rule

**Retrieval supplies the facts; the model only phrases them.**

- `Retrieval` picks rows out of a SQLite database that was built on a Mac,
  weeks before, from the chat history and documents.
- `LucyVoice` composes those rows into a fact sheet.
- A small Gemma model, running on the phone through llama.cpp, turns the
  fact sheet into a sentence.

Anything particular to the camp — a name, a date, a number, a place, who
did what, what the camp owns or decided — has to come from a row. A claim
the model invented is a bug, not a style problem, because somebody acts on
it at 2am in the dust. What the model knows about the desert and the world
in general is hers to use, kept plainly apart from the camp's record.

Measured on the camp's real questions: with no grounding rules the model
invents a camp particular in 82% of answers; with them, 2%. The flat,
list-like voice that produces is the price of that number.

## How the pieces fit

```
WhatsApp exports ─┐
                  ├─ scripts/  parse → SQLite → enrichment (Gemini on Vertex AI)
Documents ────────┘            → claims with evidence → embeddings
                               → build_preview_db.py (redact, slim)
                               → check_shipped_db.py (gate)
                               → enriched_preview.db, bundled into the app

Lucy/  (iOS)   Retrieval + LucyVoice + Gemma via llama.cpp, all on the phone
               Notes and captures → Sync → backend/

backend/       FastAPI + Postgres behind Caddy, on one VM
site/          The install page (behind a codeword) and a public write-up
evals/         The regression gate: thirty real questions, hashed goldens
```

Two models ship. The full one is Gemma 4 E2B (3.1 GB, Q4_K_M); the light
one is Gemma 3 1B (806 MB). Both are downloaded on first launch from a
release manifest, never bundled. They use **different chat turn markers**,
and the app decides which by tokenising a marker, never by filename — see
`CLAUDE.md` for the day that was learned.

## Repository layout

| Path | What it is |
|---|---|
| `Lucy/` | The SwiftUI app. The Xcode project is generated from `project.yml` with xcodegen. Unit tests in `Lucy/Tests`, UI tests in `Lucy/UITests`. |
| `scripts/` | The corpus pipeline: parsing, enrichment, embedding, redaction, the shipping gate, and the tools that measure a prompt against real questions. `reingest.sh` runs the whole thing in the one order that works. |
| `evals/` | The question set and goldens for the regression gate. `scripts/eval_lucy.py` runs it through a Python mirror of the Swift answer path. |
| `backend/` | The camp server. Its own README covers running it and the two rules that are easy to break. |
| `site/` | The install page, its gate, and `/tech`, a public description of the project with blurred screenshots. |
| `deploy/` | Creating the VM, pushing a release to it, and two helpers for App Store Connect. Every credential comes from the environment. |
| `SnailsNative/` | The pinned llama.cpp checkout and the script that builds `llama.xcframework` from it, plus the memory spike that decided the model size. |
| `docs/` | Design records, implementation plans, and dated learnings — what went wrong and why, at length. |
| `CLAUDE.md` | Working notes for anyone (or anything) changing the code. Everything in it has already gone wrong here at least once. |
| `ARCHITECTURE.md` | The data model, the guards, and the build pipeline in detail. |
| `style-guide.md` | How Lucy looks, moves and talks. |

A Kotlin port of the retrieval core exists on a separate branch of the
private repository and is not part of this release.

## Building the iOS app

You need Xcode 16 or later, [xcodegen](https://github.com/yonaskolb/XcodeGen)
(`brew install xcodegen`), and a physical iPhone. The Simulator has no
background `URLSession`, no camera, and the Mac's microphone; nothing that
matters can be judged there.

1. Build the llama.cpp framework once:

   ```bash
   SnailsNative/vendor/build-llama-xcframework.sh
   ```

2. Generate the project:

   ```bash
   cd Lucy && xcodegen generate
   ```

3. Supply the bundle resources. `project.yml` marks these `optional`, so the
   project generates without them, but the app is empty until they exist:

   | Resource | Comes from |
   |---|---|
   | `scripts/output/enriched_preview.db` | The pipeline, below. Without it there is nothing to retrieve. |
   | `Lucy/build-2026.md` + `Lucy/BuildFigures/` | A camp build guide as Markdown with figures, converted with `scripts/docx_to_markdown.py`. Shown on the Info tab. |
   | `Lucy/ps-2026-shifts.md` | The shift sign-up sheet as Markdown. Shown on the Info tab. |
   | `Lucy/playa-*.json` | Black Rock City's public event, camp and art listings, converted with `scripts/playa_data.py`. |

4. Set the camp secret. `Lucy/Sync.swift` carries a placeholder; the value
   the app ships with must match `LUCY_JOIN_CODE` on the server. Nobody
   types it — the app carrying it is the whole authentication, on the
   reasoning that TestFlight already decides who has the app.

5. Point model downloads at your own bucket. `Lucy/ModelDownload.swift`
   fetches a manifest with the shape of `deploy/manifest.json`; host the
   two GGUF files and the manifest somewhere the phone can reach over HTTPS.

6. `Lucy/device.sh` builds and installs to the connected phone.
   `Lucy/render.sh` produces the screenshots the site uses.

`Documents/lucy-journal.txt` on the device records every question, the
facts given, and the answer. Read it after any prompt change.

## Building a corpus of your own

The pipeline reads two directories at the repository root, both gitignored:

- `Whatsapp PS Exports/` — WhatsApp chat exports as `.txt`. Strip the media
  first; a "with media" export is two thousand photos around a single 1.8 MB
  text file, and only the text is read.
- `PS Processed/` — the camp's documents converted to Markdown and CSV.
  `scripts/parse_docs.py` decides what to ingest by the shape of each file,
  not its folder or year, for reasons recorded in `CLAUDE.md`.

Enrichment — turning conversation into dated, evidenced claims — runs on
Gemini through Vertex AI. You need a Google Cloud project with access to
`gemini-3.1-pro-preview` on the global endpoint; `scripts/vertex_client.py`
holds both settings and the measurements behind them. Every pass is
incremental and content-keyed, so a rebuild pays only for what changed.

```bash
cd scripts
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest                 # the pipeline's own tests
./reingest.sh                              # twelve stages, in order
.venv/bin/python check_shipped_db.py       # refuses what must not ship
```

`build_preview_db.py` strips home addresses, card digits, order and bank
numbers before anything reaches a phone. `check_shipped_db.py` gates the
artifact, and refuses any table that shrank more than 25% against the last
build a human accepted.

## Measuring instead of reading three answers on a phone

```bash
cd scripts
python3 eval_lucy.py                       # the gate: 32 questions, hashed goldens
python3 tune_server.py                     # both models side by side, local page
python3 score_answers.py                   # score a prompt against real questions
```

`evals/README.md` explains the goldens, which carry row ids and hashes and
never the database's text.

## The camp server

See `backend/README.md`. In short: one HTTP service, one Postgres, one
Caddy, meant to run equally on a cloud VM and on a box behind a playa router
with no internet.

## What is not in this repository

This is a public copy of a private working repository. It was made by
copying the code and leaving out the camp:

- **The corpus.** No chat exports, no documents, no roster, and no built
  database. The private repository's history once contained them; this
  repository's history never has.
- **The bundled camp documents.** The build guide with its figures, the
  shift rota, and the city listings are the camp's and were removed. The
  code that renders them stays.
- **Screenshots that showed people.** The site's `/tech` images are blurred
  and remain; the unblurred ones do not.
- **The camp secret.** `Sync.swift` carries a placeholder.
- **Hand-written camp facts.** `manual_facts.json` and `manual_aliases.json`
  ship with their documentation and no entries.
- **Real names.** The pipeline tests, fixtures and design notes use
  examples drawn from the camp's records. Every surname in them has been
  replaced with a fictional one that keeps the original's initial, because
  several tests match on initials. First names and playa names remain where
  the code depends on them. Any resemblance the fictional names bear to a
  real person is accidental.
- **Contact details.** Phone numbers, email addresses, bank identifiers and
  a home address used as redaction test inputs have been replaced with
  values of the same shape.

Infrastructure names — the Google Cloud project, the bucket that serves the
models, the server's hostname — are identifiers, not credentials, and were
left as they are.

## Where to read next

- `CLAUDE.md` — the short version of everything that went wrong.
- `ARCHITECTURE.md` — the long version of how it is built.
- `docs/learnings-lucy-2026-08-10.md` before changing prompts, models or the
  deploy path; `docs/learnings-lucy-2026-08-20.md` before touching retrieval
  or the enrichment order.
- `docs/superpowers/specs/` — the decision records, in date order.

## Licence

GNU Affero General Public License v3.0. See `LICENSE`. In short: use it,
change it, run it for your own camp; if you distribute it or run a modified
version as a service for others, publish your changes under the same terms.
