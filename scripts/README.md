# The corpus pipeline

Everything in this directory runs on a Mac, before the app is built. It turns
WhatsApp exports and camp documents into `output/enriched_preview.db`, the
SQLite file the app bundles and answers from. Nothing here runs on the phone.

`reingest.sh` runs the whole thing in the one order that works, and the
comment above each stage says why it sits where it does. Use it rather than
running the passes by hand: two of the orderings are load-bearing, and one
flag (`build_people.py --install`) was missing from every build for months
because its absence only printed a line and carried on.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest          # the pipeline's own tests, no network needed
./reingest.sh                       # the twelve stages; enrichment costs money
```

Tests that need `llama_cpp` or Google's SDK are skipped when those are not
installed. Everything else runs against in-memory SQLite.

## Stage by stage

**Parsing** — raw exports into `output/ps_knowledge.db`.

| Script | |
|---|---|
| `ingest_all.py` | Recreates the database from scratch and owns the order of the parsers below. Always first. |
| `parse_chats.py` | WhatsApp `.txt` exports into messages. Handles the bidi marks, system notices and attachment lines the format hides. |
| `parse_docs.py` | Markdown and CSV documents into `camp_knowledge`. `should_ingest()` decides by the shape of a file, never its folder — see `CLAUDE.md` for the three rules that each deleted something load-bearing. |
| `parse_shift_grid.py` | The shift sign-up sheet read as the 2-D grid it actually is. |
| `docx_to_markdown.py` | Converts a `.docx` build guide, figures and all, into the Markdown the Info tab renders. |
| `playa_data.py` | Black Rock City's public event, camp and art listings into the JSON the Info tab reads. |
| `bm_basics.py` | General Burning Man knowledge, written down rather than recalled, so the model has a small table of things about the desert that no camp row will ever say. |
| `ingest_manual_facts.py` | Loads hand-written facts (`manual_facts.json`, not committed) into `camp_fact`, for what nobody ever typed into a chat. Runs before embedding so they get vectors like every other claim. |
| `dedupe_people.py` | Deletes non-people and what they wrote. **Must run before enrichment**: evidence cites messages by id. |
| `build_people.py --install` | The roster, from the sign-up sheets. Without `--install` it writes a file nothing reads. |

**Enrichment** — conversation into dated, evidenced claims, with Gemini on
Vertex AI. Every pass funnels through `vertex_client.run_all`, so they are
incremental together.

| Script | |
|---|---|
| `vertex_client.py` | The model, the endpoint, retries. `gemini-3.1-pro-preview` on the global endpoint, both measured; the file records why. |
| `enrich_cache.py` | Content-keyed cache over every request, so a rebuild pays only for what changed. Keyed by sha256 of model, prompt and schema — never by row id, because `ingest_all` reassigns those. Failures are never cached. |
| `enrich_schema.py` | The enrichment tables, the evidence trigger, `verify_evidence_complete`. |
| `enrich_entities.py` | Pass 1: which phrases are things the camp has a name for. |
| `enrich_entity_records.py` | Pass 2: a record per entity, with dated facts and evidence. |
| `enrich_documents.py` | Pass 3: dated facts out of the documents. |
| `enrich_people.py` | Profiles for the people the roster and the chat agree on. |
| `enrich_personality.py`, `enrich_pronouns.py` | Extend those profiles. Both after `enrich_people`. |
| `enrich_lore.py` | The stories: things that happened, as the camp tells them. |
| `enrich_ask_vocab.py` | Last: the words people would *ask* with, generated for every fact and alias so retrieval can meet the question halfway. |
| `prompts/` | The prompt for each pass, as Markdown. |
| `validate_extraction.py` | Acceptance gate for enrichment, run against a case whose answer a human wrote from the same messages (`build_fixture.py`). |

**Embedding** — vectors over the claim layer.

| Script | |
|---|---|
| `embed_claims.py` | After everything that writes a claim. `check_shipped_db.py` refuses partial coverage. |
| `embed_corpus.py` | The corpus itself. |
| `embed_knowledge.py`, `generate_embeddings.py` | Earlier designs, superseded; kept for reference. |

**Shipping** — what reaches the phone.

| Script | |
|---|---|
| `build_preview_db.py` | Slims the database to what evidence cites and **redacts**: home addresses, card digits, order and bank numbers. Never deletes a document. The camp's own addresses ship; a shipping label does not. |
| `check_shipped_db.py` | Gates the artifact. Refuses partial vector coverage, refuses any table that shrank more than 25% against `build_baseline.json`, and scans for what redaction must have removed. `--accept` moves the baseline, and the baseline is committed so acceptance is a diff. |
| `package_db.py` | Stamps a build manifest into the database. |

**Measuring** — a prompt against real questions, instead of three answers on
a phone.

| Script | |
|---|---|
| `eval_lucy.py` | The regression gate over `../evals/questions.yaml`, through `lucy_mirror.py`, a Python mirror of the Swift answer path. Source-sync tests keep the mirror honest. |
| `score_answers.py`, `replay_prompts.py` | Score and replay against the journal's real questions. |
| `tune_server.py` | A local page with both models side by side. |
| `journal_html.py` | Renders the device journal — the only honest record of what the app did. |
| `test_swift_integration.py` | Runs the queries Swift runs, against the built database. |

**Voice** — a fine-tune that was designed and never run.

| Script | |
|---|---|
| `build_voice_dataset.py`, `tuned_model_to_gguf.sh` | Dataset builder and conversion. See `ARCHITECTURE.md`, current state. |

**Operations**

| Script | |
|---|---|
| `reingest.sh` | The rebuild. |
| `backup_to_gcs.sh` | The irreplaceable inputs to a private bucket. |

`knowledge_graph/` is the January design of the pipeline, before enrichment.
Nothing current imports it.
