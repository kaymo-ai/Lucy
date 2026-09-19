#!/usr/bin/env python3
"""
Generate embeddings for PersonContent using sentence-transformers.
Uses all-MiniLM-L6-v2 (22MB, 384 dimensions) - same model can run on-device.

Usage:
    python generate_embeddings.py [db_path]
"""

import sqlite3
import json
import sys
import numpy as np
from pathlib import Path
from sentence_transformers import SentenceTransformer

# Small, fast model that can also run on mobile
MODEL_NAME = 'all-MiniLM-L6-v2'
BATCH_SIZE = 100


def add_embedding_table(conn: sqlite3.Connection):
    """Add embeddings table to database."""
    cursor = conn.cursor()
    
    # Store embeddings as JSON array (simple, portable)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_id INTEGER UNIQUE NOT NULL,
            embedding TEXT NOT NULL,  -- JSON array of floats
            model_name TEXT NOT NULL,
            FOREIGN KEY (content_id) REFERENCES person_content(id)
        )
    ''')
    
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_emb_content ON embeddings(content_id)')
    conn.commit()


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two vectors."""
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def search_similar(conn: sqlite3.Connection, query_embedding: np.ndarray, top_k: int = 5):
    """Find most similar content to query embedding."""
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT e.content_id, pc.content, p.name, e.embedding
        FROM embeddings e
        JOIN person_content pc ON e.content_id = pc.id
        JOIN person p ON pc.person_id = p.id
    ''')
    
    results = []
    for content_id, content, name, emb_json in cursor.fetchall():
        emb = np.array(json.loads(emb_json))
        score = cosine_similarity(query_embedding, emb)
        results.append({
            'content_id': content_id,
            'content': content,
            'name': name,
            'score': score
        })
    
    results.sort(key=lambda x: x['score'], reverse=True)
    return results[:top_k]


def main():
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        Path('~/Snails/scripts/output/ps_knowledge.db')
    
    print(f"📊 Database: {db_path}")
    
    # Load model
    print(f"🧠 Loading model: {MODEL_NAME}")
    model = SentenceTransformer(MODEL_NAME)
    print(f"   ✓ Model loaded ({model.get_sentence_embedding_dimension()} dimensions)")
    
    # Connect to database
    conn = sqlite3.connect(db_path)
    add_embedding_table(conn)
    cursor = conn.cursor()
    
    # Get content that needs embeddings
    cursor.execute('''
        SELECT pc.id, pc.content 
        FROM person_content pc
        LEFT JOIN embeddings e ON pc.id = e.content_id
        WHERE e.id IS NULL
    ''')
    rows = cursor.fetchall()
    
    print(f"📝 Generating embeddings for {len(rows)} messages...")
    
    # Process in batches
    total_embedded = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i+BATCH_SIZE]
        content_ids = [r[0] for r in batch]
        texts = [r[1] for r in batch]
        
        # Generate embeddings
        embeddings = model.encode(texts, show_progress_bar=False)
        
        # Store in database
        for content_id, embedding in zip(content_ids, embeddings):
            emb_json = json.dumps(embedding.tolist())
            cursor.execute('''
                INSERT OR REPLACE INTO embeddings (content_id, embedding, model_name)
                VALUES (?, ?, ?)
            ''', (content_id, emb_json, MODEL_NAME))
        
        total_embedded += len(batch)
        progress = (i + len(batch)) / len(rows) * 100
        print(f"   {progress:.1f}% ({total_embedded}/{len(rows)})")
    
    conn.commit()
    
    # Verify
    cursor.execute('SELECT COUNT(*) FROM embeddings')
    count = cursor.fetchone()[0]
    print(f"\n✅ Done! {count} embeddings stored")
    
    # Demo search
    print("\n🔍 Testing semantic search:")
    test_queries = [
        "who knows about generators or power",
        "someone who can DJ",
        "cooking or food preparation",
    ]
    
    for query in test_queries:
        print(f"\n   Query: '{query}'")
        query_emb = model.encode([query])[0]
        results = search_similar(conn, query_emb, top_k=3)
        for r in results:
            preview = r['content'][:60].replace('\n', ' ')
            print(f"   → {r['name']}: \"{preview}...\" (score: {r['score']:.3f})")
    
    conn.close()
    print("\n✨ Vector database ready!")


if __name__ == '__main__':
    main()
