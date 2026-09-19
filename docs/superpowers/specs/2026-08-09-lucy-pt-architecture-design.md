# Lucy PT — Architecture Design

**Date:** 2026-08-09
**Status:** Approved design, not yet implemented
**Supersedes:** the RunAnywhere / SmolLM2 architecture described in `SnailsNative/CLAUDE.md` and `README.md`

---

## 1. Why this document exists

Lucy PT is an offline assistant for the Preservation Society Burning Man camp. The project was reviewed on 2026-08-09 because on-device model capabilities had moved substantially since it was built.

The review found that the project had no product spec, and that the documents standing in for one described a system that does not exist:

| document claims | reality |
|---|---|
| `CLAUDE.md`: QueryRouter does "LLM-based classification (JSON structured output)" | `QueryRouter.swift` is regex plus a hand-maintained stopword list (`looksLikeName`, line 154). No LLM involved. |
| `CLAUDE.md` / `README.md`: 384-dim MiniLM embeddings | App runs Apple `NLEmbedding` at 512-dim. |
| Tables `people`, `document_chunks`, `recipes`, `shift_assignments`, `chat_messages` | Actual tables: `person`, `person_content`, `camp_knowledge`, `roster`, `shifts`. |

It also found two live defects, documented in §9.

This document is the spec that was missing.

## 2. Requirements

From the camp's needs, in priority order:

1. **Works completely offline in the desert.** Black Rock City has no connectivity. Non-negotiable.
2. **Quick retrieval of facts.** Shifts, quantities, procedures, contacts, locations.
3. **Documentation capture.** Take a photo, record a voice memo. Deep analysis may happen on return.
4. **Inferred insight into social dynamics** from WhatsApp history — stories, anecdotes, who knows what.
5. **General Burning Man knowledge.**

### Non-requirements

Explicitly out of scope, decided rather than deferred by omission:

- **Android.** iOS only at first. Revisit after the iOS app is real.
- **Universal device support.** Target iPhone 15 Pro and newer. Older devices are told the app will not run on them.
- **Fine-tuning, in any form.** See §4.
- **A sync server.** Captures export to the Mac by share sheet.
- **Streaming-only UX assumptions.** Streaming is available and will be used, but nothing depends on it.

## 3. Target device

**iPhone 15 Pro or newer (8 GB RAM), ~5 GB free storage** (4.43 GB of models plus the bundled knowledge base).

Checked at first launch. If unmet, the app explains why and stops. There is no fallback path, no capability detection beyond this one gate, and no degraded mode. This is the single largest simplification in the design.

## 4. The central decision: where the thinking happens

The question that prompted this review was whether Lucy should be built around a fine-tuned model. The answer is no, and the reasoning generalises into the architecture.

"Fine-tune" collapses three separable things:

**Knowing facts** — fine-tuning is actively wrong. The roster changes yearly and shifts change daily; facts in weights cannot be cited, corrected, or dated. Many fact queries are not language problems at all — "when is my shift" is a `SELECT`.

**Drawing social inferences from 23k chat messages** — feels like fine-tuning, is not. It is corpus analysis. 23k messages is ~3 MB of text: a handful of frontier-model passes on a Mac, not a training run. Cheap enough to re-run every year, which a fine-tune is not.

**Sounding like the camp** — the one thing fine-tuning is genuinely good at, and the only real candidate. Not worth a training pipeline and a bespoke model artifact on its own; achievable in the prompt.

The generalisation: **expensive inference happens at build time on a Mac with frontier models. The phone retrieves conclusions that were already drawn.** This is the spine of the architecture and the reason "analysis can happen on return from the desert" falls out naturally rather than being bolted on.

### Invariant

> Answers are assembled from retrieved database rows. The LLM phrases them and never supplies facts.

The model never answers a camp question from its own knowledge. If the retrieved set is empty, Lucy says she does not know. This is the hallucination guard, and it matters most for exactly the questions the camp will ask — quantities, procedures, locations, dates.

## 5. Model stack

Downloaded once at first launch, on wifi, at home. A pre-playa readiness check confirms all three are present before departure.

| model | role | size |
|---|---|---|
| **EmbeddingGemma 300M** (`embeddinggemma-300M-Q8_0.gguf`) | retrieval embeddings, 768-dim (Matryoshka-truncatable to 128) | 334 MB |
| **Gemma 4 E2B** (`gemma-4-E2B-it-Q4_K_M.gguf`) | generation + query understanding, 128K context | 3.11 GB |
| **mmproj-F16** (`mmproj-F16.gguf`) | multimodal projector, photo understanding | 986 MB |

Total ~4.43 GB. All three sizes verified against published GGUF file listings, not estimated.

All three run through **llama.cpp, integrated directly** at a current version.

### Why not RunAnywhere

The existing `runanywhere-sdks` dependency is removed:

- `LlamaCPP.capabilities = [.llm]` — text generation only. No embeddings.
- `ONNX.capabilities = [.stt, .tts, .vad]` — no backend implements `.embedding`, despite `ModelComponent.embedding` existing in the type system.
- `VLM` appears only in error enums. No multimodal path.
- Pins `llamaCppVersion = "b7199"`, which predates Gemma 4.
- Lives git-ignored at `SnailsNative/runanywhere-sdks/`, an untracked clone of a third-party repo. The build is not reproducible.

It cannot run the models this design requires, and it abstracts things that are already platform-native.

### Why not Apple Foundation Models

Considered and rejected. Its 4,096-token context is limiting for synthesis across a profile, a dozen messages and several lore entries; it does not support token streaming; and its eligible-device set overlaps the Gemma target closely enough that supporting both earns a second code path for nothing.

### Why EmbeddingGemma is the load-bearing choice

Retrieval quality, not generation quality, is the system's actual bottleneck. Apple `NLEmbedding` is a 2019-era approach. The blocker on replacing it was tokenization — a better embedding model means WordPiece, and hand-writing WordPiece in Swift is not a reasonable cost. **llama.cpp handles tokenization**, so committing to it dissolves the blocker. This is the strongest single argument for the whole stack.

## 6. Architecture

```
┌─ TIER 1 · BUILD-TIME  (Mac, online, frontier models) ─────────┐
│  parse → resolve → ENRICH → embed → package                   │
│  output: ps_knowledge_2026.db  (read-only, versioned)         │
└───────────────────────────┬───────────────────────────────────┘
                            │ ships in app bundle
┌─ TIER 2 · ON-DEVICE  (iOS, fully offline) ────────────────────┐
│  identity → retrieve → ground → phrase                        │
│  capture  → captures.db (read-write, Documents/)              │
└───────────────────────────┬───────────────────────────────────┘
                            │ export on return
┌─ TIER 3 · POST-BURN  (Mac) ───────────────────────────────────┐
│  transcribe audio · describe photos · merge → feeds Tier 1    │
└───────────────────────────────────────────────────────────────┘
```

Each burn makes the next year's Lucy smarter. That loop is the product.

### Tier 1 — build-time enrichment (Mac, Python)

Stages:

1. **Parse** — WhatsApp exports, markdown/CSV docs, roster, shifts, plus the previous year's captures.
2. **Resolve** — deduplicate people across chat handles, roster names and nicknames. Identity resolution moves here from the device.
3. **Enrich** — frontier-model passes producing derived knowledge as rows.
4. **Embed** — EmbeddingGemma vectors over every retrievable unit.
5. **Package** — a single versioned artifact with a manifest.

Enrichment output tables:

| table | contents |
|---|---|
| `person_profile` | who someone is, years attended, chapter, what they are known for |
| `expertise` | topic → person, e.g. hardware, storage and logistics, sound |
| `relationship` | who works with whom, edge kind and strength |
| `lore` | memorable incidents, running jokes, stories worth recalling |
| `entity` | named things every veteran knows and no document defines |

**Every enriched row carries an `evidence_ref`** pointing to the source message or document. Lucy cites where a claim came from, which makes her trustworthy and makes bad inferences findable and correctable.

The corpus supports this. A sample of 35 lines from one previously-uningested group chat yielded: camp placement history, a tool specification, a named shared asset, vendor knowledge, an expertise map across four people, and several instances of distinctive camp voice. Signal density is high; this is institutional knowledge that exists in no document.

### Tier 2 — on-device (iOS, Swift)

**Two databases, strictly separated:**

| database | mode | location | lifecycle |
|---|---|---|---|
| `ps_knowledge.db` | read-only | app bundle | replaced wholesale each year |
| `captures.db` | read-write | `Documents/` | never touched by an app update |

**Load guard.** `ps_knowledge.db` carries a `meta` table recording `embedding_model`, `embedding_dim`, `schema_version` and `built_at`. The app refuses to launch on a mismatch. See §9.

**Identity.** A one-time "which member are you" picker, persisted. Currently absent entirely; every personalised feature depends on it.

**Query flow:**

```
question (text or voice)
  → LLM structured extraction  → intent + entities + filters
  → retrieval:  structured (SQL) and/or semantic (EmbeddingGemma → cosine top-k)
  → result set WITH provenance
  → grounded prompt assembly   (128K context — no chunk rationing)
  → phrased answer + citations
```

The regex `QueryRouter` is replaced by LLM structured extraction. Guaranteed model presence is what makes this safe, and it is what `CLAUDE.md` always claimed was happening.

**Extraction is itself grounded.** Moving query understanding into the LLM means the model now decides *what to retrieve*, and the §4 invariant does not cover that — it governs phrasing only. A hallucinated entity name at extraction time would produce a wrong or empty result set that the invariant cannot catch, and unlike the regex router's failures, it would not be legible.

So: extracted entities are resolved against `person` and `entity` rows through `EntityResolver` **before** retrieval runs. An entity that does not resolve surfaces as "I don't know who or what that is" rather than being passed through to semantic search, where it would return plausible-looking noise. `EntityResolver.swift` already exists and is unchanged by this design.

**Capture.** Photo, voice memo, GPS coordinate and timestamp in one gesture, fully offline. On-device photo understanding via the projector, so captures are described and searchable *on playa* rather than only after return. Export by share sheet.

Crash safety: the media file is written and fsynced **before** the database row, with an orphan sweep on launch to reattach files that have no row. A week offline with no backup means this data is irreplaceable; this is the one place defensive work is warranted.

**Proactive notifications.** Local notifications derived from the 2,087 shift rows. Offline, cheap, and the difference between a reference tool and a companion.

### Tier 3 — post-burn (Mac)

Import captures, transcribe audio, describe photos with a frontier model, merge into the corpus. Feeds Tier 1 for the following year.

## 7. What stays from the current code

Removing RunAnywhere is a dependency change, not a rewrite. Of ~2,800 lines in `SnailsCore`, only `LLMService.swift` (285 lines) is coupled to it.

| component | disposition |
|---|---|
| `KnowledgeDatabase.swift` | keep; add `meta` guard, BLOB embeddings |
| `EntityResolver.swift` | keep |
| `ResponseFormatter.swift` | keep |
| `RAGService.swift` | keep; rewire retrieval and grounding |
| Models (`Person`, `Document`, `Query`) | keep, extend for enrichment tables |
| `QueryRouter.swift` | **replace** with LLM structured extraction |
| `EmbeddingService.swift` | **replace** — NLEmbedding → EmbeddingGemma via llama.cpp |
| `LLMService.swift` | **rewrite** against llama.cpp directly |

Dependencies: remove `runanywhere-sdks`, keep `SQLite.swift`, add a llama.cpp binding.

## 8. Risks

**Memory is the assumption everything rests on, and it must be validated first.** Gemma 4 E2B Q4_K_M (3.11 GB) plus the projector (986 MB) is ~4.1 GB resident on an 8 GB iPhone, under iOS's per-app cap. This needs the `com.apple.developer.kernel.increased-memory-limit` entitlement and is genuinely tight.

Mitigations, in order: drop to a Q3 quant (~2.4 GB); load the projector only during capture and unload after; fall back to deferred photo analysis on the Mac.

**No line of application code should be written before this is spiked.**

Secondary risks:

- **llama.cpp integration on iOS** — Metal, memory pressure, model lifecycle. The only genuinely new engineering surface.
- **Download UX** — ~4.4 GB must land before departure. Needs a real pre-playa readiness check, not a spinner that fails outside Gerlach.
- **Enrichment quality** — frontier-model inferences about real people will sometimes be wrong. `evidence_ref` makes them auditable; a review pass over generated `person_profile` and `lore` rows before shipping the DB is warranted.
- **Thermals** — sustained inference in desert heat. Retrieval-only answers are cheap; generation is not. Worth measuring.

## 9. Defects in the current build

Both found during this review. Both must be fixed by the Tier 1 rebuild.

**Retrieval runs at 28% coverage of the knowledge base.** `camp_knowledge` holds 601 rows; `knowledge_embeddings` holds 167. The missing 434 load with `embedding: nil` via the `LEFT OUTER JOIN` at `KnowledgeDatabase.swift:198` and silently never match. Worst affected: finance 163/176 missing, food 104/144, operations 79/138 — the cookbooks, ops manuals and budgets Lucy exists to answer from.

**The documented build step ships a silently broken database.** Two divergent pipelines exist:

| database | model | dim | knowledge coverage |
|---|---|---|---|
| `LucyPT/LucyPT/ps_knowledge.db` (shipped) | `apple-nlembedding-512` | 512 | 167/601 |
| `scripts/output/ps_knowledge.db` (pipeline) | `all-MiniLM-L6-v2` | 384 | 601/601 |

`README.md:74` instructs `cp output/ps_knowledge.db ../LucyPT/LucyPT/`. Doing so ships 384-dim vectors into a runtime generating 512-dim queries. `EmbeddingService.cosineSimilarity` guards on `a.count == b.count` and returns `0`, so every semantic search would return uniform zero similarity — no crash, no error, no log. It has not fired only because the shipped database happens to hold the Apple vectors.

The `meta` table and load guard in §6 exist to make this class of failure impossible.

**The chat parser drops ~10% of messages.** The PS export contains 11,576 timestamped lines, 262 of them system messages, so ~11,314 real messages; the database holds 10,123. The loss is likely multi-line messages. Across all groups the gap is ~2,500 messages. Additionally, the `PS BUILD 22` group (563 messages) was never ingested at all.

Note that the nine WhatsApp exports in `Whatsapp PS Exports/` are **not** new data — every group is already ingested and no message postdates what is in the database. Their value is recovering the parser's losses and the one missing group.

## 10. Open questions

- Which quantization survives the memory spike (§8). Determines whether on-device photo understanding is baseline or deferred.
- Whether `general` and glossary entries earn their retrieval slots, or whether Gemma 4 already knows general Burning Man culture well enough to make them dead weight competing against camp-specific facts. One test query settles it.
- Whether GPS coordinates should be decoded to Black Rock City clock addresses. Charming and offline-computable, but the Golden Spike position and street naming are recalibrated annually. Late, and nothing should depend on it.

## 11. Decomposition

Four subsystems with little overlap. Each warrants its own implementation plan.

1. **Memory spike** — validate §8 before anything else.
2. **Corpus and enrichment** (Mac/Python) — fix the parser, re-ingest, enrichment passes, EmbeddingGemma vectors, packaging with manifest. No iOS work. Highest value, fully decoupled.
3. **Retrieval and identity** (iOS) — llama.cpp integration, RunAnywhere removal, load guard, identity, grounded answering.
4. **Capture and the post-burn loop** (iOS + Mac) — offline capture, crash-safe writes, export, re-ingest.

5. **Photo corpus** (Mac, then iOS) — deferred, own plan. See below.

### Photo corpus (deferred)

The camp has thousands of photos in a Google Photos shared album, mostly unsorted. They matter because for physical and spatial questions — how the swamp cooler hooks up, where the grey water valve is — an image is a strictly better answer than prose, and Lucy currently has no way to give one.

Labeling is build-time work reusing the Tier 3 path: a frontier VLM describes each photo, the description is embedded into the same 768-dim space as everything else, and retrieval returns the image with its description and provenance. No vision model on device, no second index.

Three things make this tractable despite the photos being unsorted:

- **The chat corpus is a timestamp index for the photos.** 24,192 timestamped messages spanning 2020–2026 mean any photo's capture time can be matched against what the camp was saying within minutes of it. Given that context, a VLM writes "camp placement at 7:59 & C, looking toward the portos" rather than "people in a dusty field." This crosswalk is why unsorted is acceptable, and it depends on the rebuilt message table having correct timestamps.
- **Cluster by capture time before labeling.** A burst of photos from one afternoon is one event; labeling the cluster together yields coherent descriptions at a fraction of the per-photo cost.
- **Prime the VLM with camp vocabulary** — glossary, entity list, member names — so labels use the words campers search with. `PS Processed/02. DORIS` is evidence that vocabulary is real and specific.

Size is managed by splitting description from pixels: **describe and index every photo; ship images only for the operationally useful ones.** The VLM classifies informational versus social, so party photos stay findable by description while roughly a few hundred infrastructure photos ship at ~200 KB each.

Prerequisite that only the owner can do: run Google Takeout for the album and place the export locally. Takeout sidecars carry capture time and often GPS; where GPS is present, playa photos can be located against the Black Rock City grid.

**Order.** Subsystem 1 (the memory spike) comes first and blocks 3 and 4 — a plan built on a 4.1 GB resident assumption that turns out to be false is a plan that gets rewritten. Subsystem 2 runs in parallel with everything, starting immediately: it is pure Mac/Python, shares no code with the app, and is where most of the value is. Then 3, then 4.
