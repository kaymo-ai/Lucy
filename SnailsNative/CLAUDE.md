# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Snails Native is an iOS app for offline RAG (Retrieval-Augmented Generation) that answers questions about a Burning Man theme camp called "Preservation Society" (PS). It uses on-device ML models via the RunAnywhere Swift SDK.

## Build Commands

### Data Pipeline (Python)
```bash
cd scripts
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Step 1: Ingest PS Processed files into SQLite
python3 ingest_knowledge.py

# Step 2: Generate embeddings (384-dim MiniLM-L6-v2)
python3 generate_embeddings.py
```

### iOS App (Xcode)
1. Open `SnailsApp/SnailsApp.xcodeproj`
2. Add `Snails/` as a local Swift package dependency
3. Copy `output/ps_knowledge.db` to app bundle
4. Build and run on device (simulator lacks Metal acceleration)

## Architecture

```
RAGService (orchestrator)
├── QueryRouter - classifies queries and routes to handlers
│   ├── LLM-based classification (JSON structured output)
│   └── In-memory vector search (documentEmbeddings, peopleEmbeddings)
├── EmbeddingService - MiniLM-L6-v2 embeddings (384 dim)
├── LLMService - SmolLM2-360M text generation
└── KnowledgeDatabase - SQLite (people, document_chunks, recipes, shift_assignments, chat_messages)
```

### Query Types
The QueryRouter classifies into: `personLookup`, `personInfo`, `socialQuery`, `recipeLookup`, `shiftQuery`, `procedureQuery`, `inventoryQuery`, `eventInfo`, `generalKnowledge`. Each type has a specialized handler that combines structured DB lookups with vector search.

### Key Patterns
- All services are Swift actors for thread safety
- Embeddings are loaded into memory at startup for sub-ms search
- Streaming responses via `AsyncThrowingStream`
- RAGService.Status enum tracks initialization state

## Data Flow
1. `ingest_knowledge.py` parses markdown/CSV/chat exports from `PS Processed/` folder into SQLite tables
2. `generate_embeddings.py` adds 384-dim embeddings to `document_chunks` and `people` tables
3. iOS app bundles `ps_knowledge.db` and loads embeddings into memory on launch
4. User queries are classified by LLM, routed to handlers, and answered with retrieved context

## Dependencies
- **RunAnywhere Swift SDK**: On-device LLM/embedding inference with Metal acceleration
- **SQLite.swift**: Database access
- **sentence-transformers** (Python): Embedding generation at build time
