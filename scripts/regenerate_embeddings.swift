#!/usr/bin/env swift

import Foundation
import NaturalLanguage
import SQLite3

// MARK: - Database Helper

class Database {
    private var db: OpaquePointer?

    init(path: String) throws {
        if sqlite3_open(path, &db) != SQLITE_OK {
            throw NSError(domain: "DB", code: 1, userInfo: [NSLocalizedDescriptionKey: "Cannot open database"])
        }
    }

    deinit {
        sqlite3_close(db)
    }

    func execute(_ sql: String) throws {
        var errMsg: UnsafeMutablePointer<CChar>?
        if sqlite3_exec(db, sql, nil, nil, &errMsg) != SQLITE_OK {
            let error = errMsg != nil ? String(cString: errMsg!) : "Unknown error"
            sqlite3_free(errMsg)
            throw NSError(domain: "DB", code: 2, userInfo: [NSLocalizedDescriptionKey: error])
        }
    }

    func query(_ sql: String) throws -> [[String: Any]] {
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else {
            throw NSError(domain: "DB", code: 3, userInfo: [NSLocalizedDescriptionKey: "Failed to prepare statement"])
        }
        defer { sqlite3_finalize(stmt) }

        var results: [[String: Any]] = []
        while sqlite3_step(stmt) == SQLITE_ROW {
            var row: [String: Any] = [:]
            let columnCount = sqlite3_column_count(stmt)
            for i in 0..<columnCount {
                let name = String(cString: sqlite3_column_name(stmt, i))
                let type = sqlite3_column_type(stmt, i)
                switch type {
                case SQLITE_INTEGER:
                    row[name] = sqlite3_column_int64(stmt, i)
                case SQLITE_TEXT:
                    row[name] = String(cString: sqlite3_column_text(stmt, i))
                case SQLITE_FLOAT:
                    row[name] = sqlite3_column_double(stmt, i)
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

    func prepareStatement(_ sql: String) throws -> OpaquePointer {
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else {
            throw NSError(domain: "DB", code: 3, userInfo: [NSLocalizedDescriptionKey: "Failed to prepare statement"])
        }
        return stmt!
    }
}

// MARK: - Embedding Generator

func generateEmbeddings(dbPath: String) throws {
    print("🚀 Regenerating entity embeddings with Apple NLEmbedding (512-dim)")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    // Load embedding model
    guard let embedding = NLEmbedding.sentenceEmbedding(for: .english) else {
        throw NSError(domain: "NL", code: 1, userInfo: [NSLocalizedDescriptionKey: "Could not load sentence embedding"])
    }
    print("✅ Loaded Apple NLEmbedding for English")

    // Open database
    let db = try Database(path: dbPath)
    print("✅ Opened database: \(dbPath)")

    // Get all person entities
    let entities = try db.query("""
        SELECT e.id, e.canonical_name, ep.summary
        FROM entity e
        LEFT JOIN entity_profile ep ON e.id = ep.entity_id
        WHERE e.type = 'person'
        ORDER BY e.id
    """)
    print("📊 Found \(entities.count) person entities")

    // Clear existing embeddings for persons
    try db.execute("""
        DELETE FROM entity_embedding
        WHERE entity_id IN (SELECT id FROM entity WHERE type = 'person')
    """)
    print("🗑️  Cleared existing person embeddings")

    // Prepare insert statement
    let insertSQL = """
        INSERT OR REPLACE INTO entity_embedding (entity_id, embedding, model_name)
        VALUES (?, ?, 'apple-nlembedding-512')
    """
    let stmt = try db.prepareStatement(insertSQL)
    defer { sqlite3_finalize(stmt) }

    var successCount = 0
    var failCount = 0

    for (index, entity) in entities.enumerated() {
        guard let entityId = entity["id"] as? Int64,
              let name = entity["canonical_name"] as? String else {
            continue
        }

        // Create rich text for embedding: name + summary if available
        var textToEmbed = name
        if let summary = entity["summary"] as? String, !summary.isEmpty {
            textToEmbed = "\(name): \(summary)"
        }

        // Generate embedding
        guard let vector = embedding.vector(for: textToEmbed) else {
            print("⚠️  Failed to embed: \(name)")
            failCount += 1
            continue
        }

        // Convert to JSON array
        let floatVector = vector.map { Float($0) }
        let jsonData = try JSONSerialization.data(withJSONObject: floatVector)
        let jsonString = String(data: jsonData, encoding: .utf8)!

        // Insert into database
        sqlite3_reset(stmt)
        sqlite3_bind_int64(stmt, 1, entityId)
        sqlite3_bind_text(stmt, 2, jsonString, -1, unsafeBitCast(-1, to: sqlite3_destructor_type.self))

        if sqlite3_step(stmt) == SQLITE_DONE {
            successCount += 1
        } else {
            print("⚠️  Failed to insert embedding for: \(name)")
            failCount += 1
        }

        // Progress update every 50 entities
        if (index + 1) % 50 == 0 {
            print("   Progress: \(index + 1)/\(entities.count)")
        }
    }

    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("✅ Complete! Embedded \(successCount) entities (\(failCount) failed)")

    // Verify
    let verification = try db.query("""
        SELECT COUNT(*) as count, json_array_length(embedding) as dim
        FROM entity_embedding
        WHERE model_name = 'apple-nlembedding-512'
        LIMIT 1
    """)
    if let first = verification.first,
       let count = first["count"] as? Int64,
       let dim = first["dim"] as? Int64 {
        print("📊 Verification: \(count) embeddings with \(dim) dimensions")
    }
}

// MARK: - Main

let args = CommandLine.arguments
let dbPath: String

if args.count > 1 {
    dbPath = args[1]
} else {
    // Default path
    dbPath = FileManager.default.currentDirectoryPath + "/output/ps_knowledge.db"
}

do {
    try generateEmbeddings(dbPath: dbPath)
} catch {
    print("❌ Error: \(error.localizedDescription)")
    exit(1)
}
