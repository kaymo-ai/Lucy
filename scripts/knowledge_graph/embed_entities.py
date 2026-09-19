#!/usr/bin/env python3
"""
Pass 5: Entity Embedding Generation

Generates embeddings for entities to enable semantic search.
Uses the same model as the existing pipeline (all-MiniLM-L6-v2).

For each entity, creates an embedding from:
- Person: canonical_name + summary (if available) + expertise
- Concept: canonical_name + definition (if available)

Usage:
    python -m knowledge_graph.embed_entities
"""

import json
import argparse

from sentence_transformers import SentenceTransformer

from .config import DB_PATH, EMBEDDING_MODEL, EMBEDDING_DIM
from .db import get_connection, transaction, get_profile, get_concept_definition


BATCH_SIZE = 50


def get_entity_text(conn, entity: dict) -> str:
    """Get text representation of entity for embedding."""
    entity_id = entity['id']
    entity_type = entity['type']
    name = entity['canonical_name']

    if entity_type == 'person':
        profile = get_profile(conn, entity_id)
        if profile:
            parts = [name]
            if profile.summary:
                parts.append(profile.summary)
            if profile.expertise:
                parts.append("Expertise: " + ", ".join(profile.expertise))
            if profile.key_contributions:
                parts.append(profile.key_contributions)
            return " ".join(parts)
        return name

    else:  # concept, event, equipment, role
        defn = get_concept_definition(conn, entity_id)
        if defn:
            parts = [name]
            if defn.definition:
                parts.append(defn.definition)
            if defn.category:
                parts.append(f"Category: {defn.category}")
            return " ".join(parts)
        return name


def main():
    parser = argparse.ArgumentParser(description='Generate entity embeddings')
    parser.add_argument('--force', action='store_true', help='Regenerate all embeddings')
    args = parser.parse_args()

    print("=" * 60)
    print("Pass 5: Entity Embedding Generation")
    print("=" * 60)
    print(f"\n   Database: {DB_PATH}")
    print(f"   Model: {EMBEDDING_MODEL}")

    # Load model
    print("\n1. Loading embedding model...")
    model = SentenceTransformer(EMBEDDING_MODEL)
    print(f"   Loaded ({model.get_sentence_embedding_dimension()} dimensions)")

    conn = get_connection(DB_PATH)
    cursor = conn.cursor()

    # Clear existing embeddings if forced
    if args.force:
        print("\n2. Clearing existing entity embeddings...")
        cursor.execute('DELETE FROM entity_embedding')
        conn.commit()

    # Get entities needing embeddings
    print("\n3. Finding entities without embeddings...")
    cursor.execute('''
        SELECT e.id, e.type, e.canonical_name
        FROM entity e
        LEFT JOIN entity_embedding ee ON e.id = ee.entity_id
        WHERE ee.id IS NULL
    ''')
    entities = [dict(row) for row in cursor.fetchall()]
    print(f"   Found {len(entities)} entities to embed")

    if not entities:
        print("   All entities already have embeddings")
        conn.close()
        return

    # Generate embeddings in batches
    print("\n4. Generating embeddings...")
    total_embedded = 0

    for i in range(0, len(entities), BATCH_SIZE):
        batch = entities[i:i+BATCH_SIZE]

        # Get text for each entity
        texts = [get_entity_text(conn, e) for e in batch]

        # Generate embeddings
        embeddings = model.encode(texts, show_progress_bar=False)

        # Store in database
        with transaction(conn):
            for entity, embedding in zip(batch, embeddings):
                emb_json = json.dumps(embedding.tolist())
                cursor.execute('''
                    INSERT OR REPLACE INTO entity_embedding
                    (entity_id, embedding, model_name)
                    VALUES (?, ?, ?)
                ''', (entity['id'], emb_json, EMBEDDING_MODEL))

        total_embedded += len(batch)
        progress = (i + len(batch)) / len(entities) * 100
        print(f"   {progress:.1f}% ({total_embedded}/{len(entities)})")

    # Verify
    cursor.execute('SELECT COUNT(*) FROM entity_embedding')
    count = cursor.fetchone()[0]

    print("\n" + "=" * 60)
    print("Entity Embedding Summary:")
    print(f"   Total entity embeddings: {count}")
    print("=" * 60)

    # Demo search
    print("\n   Testing entity search:")
    test_queries = ["power generator", "kitchen cooking", "DJ music"]

    for query in test_queries:
        query_emb = model.encode([query])[0]

        cursor.execute('''
            SELECT e.canonical_name, e.type, ee.embedding
            FROM entity_embedding ee
            JOIN entity e ON ee.entity_id = e.id
        ''')

        import numpy as np

        results = []
        for row in cursor.fetchall():
            emb = np.array(json.loads(row['embedding']))
            score = float(np.dot(query_emb, emb) / (np.linalg.norm(query_emb) * np.linalg.norm(emb)))
            results.append((row['canonical_name'], row['type'], score))

        results.sort(key=lambda x: x[2], reverse=True)

        print(f"\n   Query: '{query}'")
        for name, etype, score in results[:3]:
            print(f"      {name} ({etype}): {score:.3f}")

    conn.close()
    print("\n   Entity embeddings ready!")


if __name__ == '__main__':
    main()
