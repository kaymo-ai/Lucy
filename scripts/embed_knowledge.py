#!/usr/bin/env python3
"""
Generate embeddings for camp_knowledge content.
"""

import sqlite3
import json
import sys
import numpy as np
from pathlib import Path
from sentence_transformers import SentenceTransformer

MODEL_NAME = 'all-MiniLM-L6-v2'
BATCH_SIZE = 50
MAX_LENGTH = 2000  # Truncate long docs


def main():
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        Path('~/Snails/scripts/output/ps_knowledge.db')
    
    print(f"📊 Database: {db_path}")
    print(f"🧠 Loading model: {MODEL_NAME}")
    model = SentenceTransformer(MODEL_NAME)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Add embedding table for camp_knowledge
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS knowledge_embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            knowledge_id INTEGER UNIQUE NOT NULL,
            embedding TEXT NOT NULL,
            model_name TEXT NOT NULL,
            FOREIGN KEY (knowledge_id) REFERENCES camp_knowledge(id)
        )
    ''')
    
    # Get content that needs embeddings
    cursor.execute('''
        SELECT ck.id, ck.title, ck.content 
        FROM camp_knowledge ck
        LEFT JOIN knowledge_embeddings ke ON ck.id = ke.knowledge_id
        WHERE ke.id IS NULL
    ''')
    rows = cursor.fetchall()
    
    print(f"📝 Generating embeddings for {len(rows)} knowledge docs...")
    
    total = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i+BATCH_SIZE]
        
        # Combine title + content, truncate
        texts = []
        for _, title, content in batch:
            text = f"{title}\n\n{content[:MAX_LENGTH]}"
            texts.append(text)
        
        knowledge_ids = [r[0] for r in batch]
        embeddings = model.encode(texts, show_progress_bar=False)
        
        for kid, emb in zip(knowledge_ids, embeddings):
            cursor.execute('''
                INSERT OR REPLACE INTO knowledge_embeddings (knowledge_id, embedding, model_name)
                VALUES (?, ?, ?)
            ''', (kid, json.dumps(emb.tolist()), MODEL_NAME))
        
        total += len(batch)
        print(f"   {total}/{len(rows)}")
    
    conn.commit()
    
    cursor.execute('SELECT COUNT(*) FROM knowledge_embeddings')
    count = cursor.fetchone()[0]
    print(f"\n✅ Done! {count} knowledge embeddings stored")
    
    # Quick test
    print("\n🔍 Testing knowledge search:")
    query = "how to set up the generator"
    query_emb = model.encode([query])[0]
    
    cursor.execute('SELECT knowledge_id, embedding FROM knowledge_embeddings')
    results = []
    for kid, emb_json in cursor.fetchall():
        emb = np.array(json.loads(emb_json))
        score = float(np.dot(query_emb, emb) / (np.linalg.norm(query_emb) * np.linalg.norm(emb)))
        results.append((kid, score))
    
    results.sort(key=lambda x: x[1], reverse=True)
    print(f"   Query: '{query}'")
    for kid, score in results[:3]:
        cursor.execute('SELECT title, category FROM camp_knowledge WHERE id = ?', (kid,))
        title, cat = cursor.fetchone()
        print(f"   → [{cat}] {title} (score: {score:.3f})")
    
    conn.close()


if __name__ == '__main__':
    main()
