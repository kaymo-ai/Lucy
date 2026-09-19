#!/usr/bin/env python3
"""
Generate embeddings for all content in the knowledge database.
Uses sentence-transformers with all-MiniLM-L6-v2 (384 dimensions).
"""

import sqlite3
import struct
from pathlib import Path
from sentence_transformers import SentenceTransformer

# Paths
DB_PATH = Path("../output/ps_knowledge.db")
MODEL_NAME = "all-MiniLM-L6-v2"
BATCH_SIZE = 64


def floats_to_blob(floats: list[float]) -> bytes:
    """Convert list of floats to binary blob."""
    return struct.pack(f'{len(floats)}f', *floats)


def embed_table(
    conn: sqlite3.Connection,
    model: SentenceTransformer,
    table: str,
    content_col: str,
    id_col: str = "id"
):
    """Generate embeddings for all rows in a table."""
    cursor = conn.cursor()

    # Get all rows without embeddings
    cursor.execute(f"""
        SELECT {id_col}, {content_col}
        FROM {table}
        WHERE embedding IS NULL
    """)
    rows = cursor.fetchall()

    if not rows:
        print(f"  {table}: all rows already have embeddings")
        return

    print(f"  {table}: generating embeddings for {len(rows)} rows...")

    # Process in batches
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        ids = [row[0] for row in batch]
        texts = [row[1] for row in batch]

        # Generate embeddings
        embeddings = model.encode(texts, show_progress_bar=False)

        # Update database
        for row_id, emb in zip(ids, embeddings):
            blob = floats_to_blob(emb.tolist())
            cursor.execute(f"""
                UPDATE {table}
                SET embedding = ?
                WHERE {id_col} = ?
            """, (blob, row_id))

        conn.commit()
        print(f"    Processed {min(i + BATCH_SIZE, len(rows))}/{len(rows)}")


def embed_people(conn: sqlite3.Connection, model: SentenceTransformer):
    """Generate embeddings for people based on their profile text."""
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, name, aliases, roles, personality_profile
        FROM people
        WHERE embedding IS NULL
    """)
    rows = cursor.fetchall()

    if not rows:
        print("  people: all rows already have embeddings")
        return

    print(f"  people: generating embeddings for {len(rows)} rows...")

    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]

        texts = []
        ids = []
        for row in batch:
            row_id, name, aliases, roles, profile = row

            # Build a text representation of the person
            text_parts = [f"Name: {name}"]
            if aliases:
                text_parts.append(f"Also known as: {aliases}")
            if roles:
                text_parts.append(f"Roles: {roles}")
            if profile:
                text_parts.append(f"Profile: {profile}")

            texts.append(" ".join(text_parts))
            ids.append(row_id)

        embeddings = model.encode(texts, show_progress_bar=False)

        for row_id, emb in zip(ids, embeddings):
            blob = floats_to_blob(emb.tolist())
            cursor.execute("""
                UPDATE people SET embedding = ? WHERE id = ?
            """, (blob, row_id))

        conn.commit()
        print(f"    Processed {min(i + BATCH_SIZE, len(rows))}/{len(rows)}")


def main():
    print(f"Loading model: {MODEL_NAME}")
    model = SentenceTransformer(MODEL_NAME)

    print(f"Opening database: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)

    print("\nGenerating embeddings:")

    # Document chunks
    embed_table(conn, model, "document_chunks", "content")

    # People (custom text representation)
    embed_people(conn, model)

    conn.close()
    print("\nDone!")


if __name__ == "__main__":
    main()
