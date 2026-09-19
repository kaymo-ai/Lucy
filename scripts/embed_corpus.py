#!/usr/bin/env python3
"""
Generate EmbeddingGemma vectors for person_content and camp_knowledge.

Vectors are produced by llama-cpp-python against the SAME GGUF the iOS app
loads, so corpus vectors and query vectors come from identical code. Using a
different implementation on either side (e.g. sentence-transformers here,
llama.cpp there) risks subtly different pooling or normalisation, which shows
up as quietly degraded retrieval rather than an error.

Usage:
    python embed_corpus.py ./output/ps_knowledge.db ./models/embeddinggemma-300M-Q8_0.gguf
"""
import sys
import struct
import sqlite3
from pathlib import Path

import llama_cpp
from llama_cpp import Llama
from tqdm import tqdm

EMBED_DIM = 768
MODEL_NAME = 'embeddinggemma-300M-Q8_0'


def pack_vector(values) -> bytes:
    """Pack floats as little-endian Float32. iOS reads these directly."""
    return struct.pack(f'<{len(values)}f', *values)


def unpack_vector(blob: bytes):
    return list(struct.unpack(f'<{len(blob) // 4}f', blob))


def load_model(model_path: str) -> Llama:
    """Load EmbeddingGemma for sequence-level embeddings.

    pooling_type is set explicitly with the named constant rather than left to
    the GGUF metadata: llama-cpp-python raises "Failed to get embeddings from
    sequence, pooling type is not set" when a model ships without it, and a
    magic integer here would be unreadable and easy to get wrong.
    """
    return Llama(
        model_path=model_path,
        embedding=True,
        pooling_type=llama_cpp.LLAMA_POOLING_TYPE_MEAN,
        n_ctx=2048,
        verbose=False,
    )


def ensure_schema(conn):
    conn.executescript('''
        DROP TABLE IF EXISTS embeddings;
        DROP TABLE IF EXISTS knowledge_embeddings;

        CREATE TABLE embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_id INTEGER UNIQUE NOT NULL,
            embedding BLOB NOT NULL,
            model_name TEXT NOT NULL,
            FOREIGN KEY (content_id) REFERENCES person_content(id)
        );
        CREATE INDEX idx_emb_content ON embeddings(content_id);

        CREATE TABLE knowledge_embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            knowledge_id INTEGER UNIQUE NOT NULL,
            embedding BLOB NOT NULL,
            model_name TEXT NOT NULL,
            FOREIGN KEY (knowledge_id) REFERENCES camp_knowledge(id)
        );
        CREATE INDEX idx_kemb_knowledge ON knowledge_embeddings(knowledge_id);
    ''')
    conn.commit()


def embed_table(conn, llm, select_sql, insert_sql, label):
    rows = conn.execute(select_sql).fetchall()
    inserted = 0
    for row_id, text in tqdm(rows, desc=label):
        if not text or not text.strip():
            continue
        vector = llm.embed(text)
        if len(vector) != EMBED_DIM:
            raise SystemExit(
                f"{label}: expected {EMBED_DIM} dims, model returned {len(vector)}"
            )
        conn.execute(insert_sql, (row_id, pack_vector(vector), MODEL_NAME))
        inserted += 1
    conn.commit()
    return len(rows), inserted


def main():
    db_path = Path(sys.argv[1])
    model_path = Path(sys.argv[2])

    llm = load_model(str(model_path))

    conn = sqlite3.connect(db_path)
    ensure_schema(conn)

    total_c, done_c = embed_table(
        conn, llm,
        "SELECT id, content FROM person_content",
        "INSERT INTO embeddings (content_id, embedding, model_name) VALUES (?, ?, ?)",
        "person_content",
    )
    total_k, done_k = embed_table(
        conn, llm,
        "SELECT id, title || '\n\n' || content FROM camp_knowledge",
        "INSERT INTO knowledge_embeddings (knowledge_id, embedding, model_name) VALUES (?, ?, ?)",
        "camp_knowledge",
    )

    print(f"\nperson_content:  {done_c}/{total_c} embedded")
    print(f"camp_knowledge:  {done_k}/{total_k} embedded")

    if done_k < total_k:
        raise SystemExit(
            f"ERROR: {total_k - done_k} camp_knowledge rows have no embedding. "
            "This is the defect that left 72% of camp docs unsearchable. "
            "Every row must be embedded."
        )

    conn.close()


if __name__ == '__main__':
    main()
