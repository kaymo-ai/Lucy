#!/usr/bin/env python3
"""E7 — embed the claim layer, the tables the phone actually retrieves from.

The corpus already had EmbeddingGemma vectors, and they were useless to the
app: `embeddings` covers person_content (raw messages) and
`knowledge_embeddings` covers camp_knowledge (documents), and both predate the
enrichment passes. What retrieval on the phone leans on is camp_fact,
entity_fact and lore -- the *output* of enrichment -- and none of those had a
vector at all. Shipping what existed would have given semantic search over the
raw chat log and keyword search over everything that answers questions.

Why this exists at all, from docs/learnings-lucy-2026-08-09.md:

    "how do we get water delivered" cannot reach "pick up service vouchers at
    the USS Camp" -- the two share no words. Five scoring changes were made in
    one day, each fixing one case and exposing another; that pattern is the
    argument for embeddings rather than a sixth.

TASK PREFIXES ARE NOT OPTIONAL, AND GETTING THEM WRONG IS SILENT.

EmbeddingGemma is trained asymmetrically: a document and a query are encoded
with different instruction prefixes. Encode both bare and cosine still returns
numbers, ranking still happens, nothing errors -- it is simply worse, with no
signal saying so. That is the same shape as the turn-marker bug, where every
prompt reached the model wrapped in text that tokenised as ordinary characters
and nothing looked broken for months.

Measured on 400 real camp_fact rows against the question above, before any of
this was built:

                    target rank    median cosine    worst cosine
    bare              9 of 400        0.2362          0.1028
    prefixed          6 of 400        0.1206         -0.0284

Prefixed ranks the target better and, more importantly, has a far wider
spread: bare encoding gives everything a high floor, so the similarity carries
less information. The phone MUST encode questions with QUERY_PREFIX below or
the two sides disagree and retrieval quietly degrades.

Deliberately not embedded here: camp_knowledge. Documents already retrieve
through their own path with an authority prior, and a document wants passage
level vectors rather than one vector for a whole manual -- a different job.

Usage:
    python embed_claims.py ./output/ps_knowledge.db ./models/embeddinggemma-300M-Q8_0.gguf
"""
import sqlite3
import struct
import sys
from pathlib import Path

import llama_cpp
from llama_cpp import Llama
from tqdm import tqdm

EMBED_DIM = 768
MODEL_NAME = 'embeddinggemma-300M-Q8_0'

# EmbeddingGemma's documented forms. The phone mirrors QUERY_PREFIX exactly;
# see Embedder.swift, which cites this constant by name.
QUERY_PREFIX = 'task: search result | query: '


def document_text(title: str, body: str) -> str:
    """The document side of the asymmetry. `title` may be empty; the model was
    trained with the literal word "none" in that slot rather than a blank."""
    return f"title: {title or 'none'} | text: {body}"


# Each source names the columns that make up its title and its body. Kept as
# data rather than three near-identical loops so that adding a table is one
# line and cannot drift in how it builds text.
SOURCES = (
    ('camp_fact', 'SELECT id, topic, fact FROM camp_fact'),
    ('entity_fact', 'SELECT f.id, e.name, f.fact FROM entity_fact f'
                    ' JOIN entity e ON e.id = f.entity_id'),
    ('lore', 'SELECT id, title, story FROM lore'),
)


def pack_vector(values) -> bytes:
    """Little-endian Float32, which is what Swift reads back directly."""
    return struct.pack(f'<{len(values)}f', *values)


def load_model(model_path: str) -> Llama:
    """Same construction as embed_corpus.py, deliberately.

    pooling_type is named rather than left to GGUF metadata: llama-cpp-python
    raises "pooling type is not set" for models that ship without it, and a
    magic integer here would be unreadable and easy to get wrong.
    """
    return Llama(model_path=model_path, embedding=True,
                 pooling_type=llama_cpp.LLAMA_POOLING_TYPE_MEAN,
                 n_ctx=2048, verbose=False)


def ensure_schema(conn):
    # Dropped and rebuilt wholesale. A partially-rewritten vector table is the
    # worst of both worlds: it reports coverage and returns confident nonsense
    # for whichever rows kept a vector from an older convention.
    conn.executescript('''
        DROP TABLE IF EXISTS claim_embedding;

        CREATE TABLE claim_embedding (
            claim_table TEXT    NOT NULL,
            claim_id    INTEGER NOT NULL,
            embedding   BLOB    NOT NULL,
            model_name  TEXT    NOT NULL,
            PRIMARY KEY (claim_table, claim_id)
        );
    ''')
    conn.commit()


def run(db_path: str, model_path: str) -> int:
    conn = sqlite3.connect(db_path)
    ensure_schema(conn)
    llm = load_model(model_path)

    total = 0
    for table, sql in SOURCES:
        rows = conn.execute(sql).fetchall()
        for claim_id, title, body in tqdm(rows, desc=table, unit='row'):
            if not body:
                continue
            vector = llm.embed(document_text(title or '', body))
            if len(vector) != EMBED_DIM:
                raise SystemExit(
                    f'refusing to write: {table}/{claim_id} embedded to '
                    f'{len(vector)} dimensions, expected {EMBED_DIM}')
            conn.execute(
                'INSERT OR REPLACE INTO claim_embedding'
                ' (claim_table, claim_id, embedding, model_name)'
                ' VALUES (?, ?, ?, ?)',
                (table, claim_id, pack_vector(vector), MODEL_NAME))
            total += 1
        conn.commit()

    # Coverage is checked here rather than left to the shipping gate, because
    # a row that silently has no vector is invisible to semantic retrieval and
    # looks exactly like a row that is merely a poor match.
    for table, _ in SOURCES:
        want = conn.execute(
            f'SELECT count(*) FROM {table} WHERE '
            + ('story' if table == 'lore' else 'fact') + " IS NOT NULL AND "
            + ('story' if table == 'lore' else 'fact') + " != ''").fetchone()[0]
        got = conn.execute('SELECT count(*) FROM claim_embedding'
                           ' WHERE claim_table = ?', (table,)).fetchone()[0]
        if got != want:
            raise SystemExit(
                f'refusing to finish: {table} has {want} rows worth embedding '
                f'but {got} vectors')
        print(f'{table:14} {got} vectors')

    conn.close()
    return total


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit(__doc__.strip().splitlines()[-1])
    db, model = sys.argv[1], sys.argv[2]
    for path in (db, model):
        if not Path(path).exists():
            raise SystemExit(f'no such file: {path}')
    n = run(db, model)
    print(f'\n{n} claim vectors written with {MODEL_NAME}')
