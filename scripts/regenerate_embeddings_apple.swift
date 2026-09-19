#!/usr/bin/env swift

// Regenerate embeddings using Apple's NLEmbedding (512-dim)
// Run with: swift regenerate_embeddings_apple.swift [db_path]

import Foundation
import NaturalLanguage
import SQLite3

// MARK: - Database Helper

class Database {
    private var db: OpaquePointer?

    init(path: String) throws {
        if sqlite3_open(path, &db) != SQLITE_OK {
            throw NSError(domain: "SQLite", code: 1, userInfo: [NSLocalizedDescriptionKey: "Could not open database"])
        }
    }

    deinit {
        sqlite3_close(db)
    }

    func execute(_ sql: String) throws {
        var errorMessage: UnsafeMutablePointer<CChar>?
        if sqlite3_exec(db, sql, nil, nil, &errorMessage) != SQLITE_OK {
            let error = errorMessage.map { String(cString: $0) } ?? "Unknown error"
            sqlite3_free(errorMessage)
            throw NSError(domain: "SQLite", code: 2, userInfo: [NSLocalizedDescriptionKey: error])
        }
    }

    func query(_ sql: String) throws -> [[String: Any]] {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &statement, nil) == SQLITE_OK else {
            throw NSError(domain: "SQLite", code: 3, userInfo: [NSLocalizedDescriptionKey: "Could not prepare statement"])
        }
        defer { sqlite3_finalize(statement) }

        var results: [[String: Any]] = []
        let columnCount = sqlite3_column_count(statement)

        while sqlite3_step(statement) == SQLITE_ROW {
            var row: [String: Any] = [:]
            for i in 0..<columnCount {
                let name = String(cString: sqlite3_column_name(statement, i))
                let type = sqlite3_column_type(statement, i)

                switch type {
                case SQLITE_INTEGER:
                    row[name] = sqlite3_column_int64(statement, i)
                case SQLITE_FLOAT:
                    row[name] = sqlite3_column_double(statement, i)
                case SQLITE_TEXT:
                    row[name] = String(cString: sqlite3_column_text(statement, i))
                case SQLITE_NULL:
                    row[name] = NSNull()
                default:
                    row[name] = NSNull()
                }
            }
            results.append(row)
        }
        return results
    }

    func insert(_ sql: String, bindings: [Any]) throws {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &statement, nil) == SQLITE_OK else {
            throw NSError(domain: "SQLite", code: 3, userInfo: [NSLocalizedDescriptionKey: "Could not prepare statement"])
        }
        defer { sqlite3_finalize(statement) }

        for (index, value) in bindings.enumerated() {
            let idx = Int32(index + 1)
            switch value {
            case let v as Int64:
                sqlite3_bind_int64(statement, idx, v)
            case let v as Int:
                sqlite3_bind_int64(statement, idx, Int64(v))
            case let v as String:
                sqlite3_bind_text(statement, idx, v, -1, unsafeBitCast(-1, to: sqlite3_destructor_type.self))
            default:
                sqlite3_bind_null(statement, idx)
            }
        }

        if sqlite3_step(statement) != SQLITE_DONE {
            throw NSError(domain: "SQLite", code: 4, userInfo: [NSLocalizedDescriptionKey: "Insert failed"])
        }
    }
}

// MARK: - Embedding Generator

func generateEmbedding(_ text: String, embedding: NLEmbedding) -> [Float]? {
    guard let vector = embedding.vector(for: text) else { return nil }
    return vector.map { Float($0) }
}

func vectorToJSON(_ vector: [Float]) -> String {
    let strings = vector.map { String(format: "%.6f", $0) }
    return "[\(strings.joined(separator: ","))]"
}

// MARK: - Main

func main() throws {
    let args = CommandLine.arguments
    let dbPath = args.count > 1 ? args[1] : "~/Snails/assets/ps_knowledge.db"

    print("🧠 Apple NLEmbedding Regeneration Script")
    print("📊 Database: \(dbPath)")

    // Load embedding model
    print("\n🔄 Loading Apple NLEmbedding...")
    guard let sentenceEmbedding = NLEmbedding.sentenceEmbedding(for: .english) else {
        print("❌ Could not load sentence embedding model")
        exit(1)
    }
    print("✅ Loaded (512 dimensions)")

    // Open database
    let db = try Database(path: dbPath)

    // Create new embedding tables with 512-dim
    print("\n📝 Creating embedding tables...")

    // Drop old embeddings and recreate
    try db.execute("DROP TABLE IF EXISTS embeddings")
    try db.execute("""
        CREATE TABLE embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_id INTEGER UNIQUE NOT NULL,
            embedding TEXT NOT NULL,
            model_name TEXT NOT NULL,
            FOREIGN KEY (content_id) REFERENCES person_content(id)
        )
    """)
    try db.execute("CREATE INDEX IF NOT EXISTS idx_emb_content ON embeddings(content_id)")

    try db.execute("DROP TABLE IF EXISTS knowledge_embeddings")
    try db.execute("""
        CREATE TABLE knowledge_embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            knowledge_id INTEGER UNIQUE NOT NULL,
            embedding TEXT NOT NULL,
            model_name TEXT NOT NULL,
            FOREIGN KEY (knowledge_id) REFERENCES camp_knowledge(id)
        )
    """)
    try db.execute("CREATE INDEX IF NOT EXISTS idx_kemb_knowledge ON knowledge_embeddings(knowledge_id)")

    // Embed person_content
    print("\n💬 Embedding person_content...")
    let contentRows = try db.query("SELECT id, content FROM person_content")
    var contentCount = 0

    for row in contentRows {
        guard let id = row["id"] as? Int64,
              let content = row["content"] as? String else { continue }

        if let vector = generateEmbedding(content, embedding: sentenceEmbedding) {
            let json = vectorToJSON(vector)
            try db.insert(
                "INSERT INTO embeddings (content_id, embedding, model_name) VALUES (?, ?, ?)",
                bindings: [id, json, "apple-nlembedding-512"]
            )
            contentCount += 1

            if contentCount % 100 == 0 {
                print("   Processed \(contentCount)/\(contentRows.count)")
            }
        }
    }
    print("✅ Embedded \(contentCount) content items")

    // Embed camp_knowledge
    print("\n📄 Embedding camp_knowledge...")
    let knowledgeRows = try db.query("SELECT id, title, content FROM camp_knowledge")
    var knowledgeCount = 0

    for row in knowledgeRows {
        guard let id = row["id"] as? Int64,
              let title = row["title"] as? String,
              let content = row["content"] as? String else { continue }

        // Combine title and content for embedding
        let text = "\(title)\n\(content)"

        if let vector = generateEmbedding(text, embedding: sentenceEmbedding) {
            let json = vectorToJSON(vector)
            try db.insert(
                "INSERT INTO knowledge_embeddings (knowledge_id, embedding, model_name) VALUES (?, ?, ?)",
                bindings: [id, json, "apple-nlembedding-512"]
            )
            knowledgeCount += 1

            if knowledgeCount % 50 == 0 {
                print("   Processed \(knowledgeCount)/\(knowledgeRows.count)")
            }
        }
    }
    print("✅ Embedded \(knowledgeCount) knowledge items")

    // Verify
    let embCount = try db.query("SELECT COUNT(*) as cnt FROM embeddings")
    let kembCount = try db.query("SELECT COUNT(*) as cnt FROM knowledge_embeddings")

    print("\n✨ Done!")
    print("   Content embeddings: \(embCount.first?["cnt"] ?? 0)")
    print("   Knowledge embeddings: \(kembCount.first?["cnt"] ?? 0)")
    print("   Dimension: 512 (Apple NLEmbedding)")
}

do {
    try main()
} catch {
    print("❌ Error: \(error.localizedDescription)")
    exit(1)
}
