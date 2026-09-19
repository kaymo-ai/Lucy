#!/usr/bin/env swift

// Preprocess data and regenerate embeddings using Apple's NLEmbedding (512-dim)
// This script:
// 1. Cleans camp_knowledge content (removes page markers, filters garbage)
// 2. Filters out system messages from person_content
// 3. Regenerates embeddings with cleaned data
//
// Run with: swift preprocess_and_embed.swift [db_path]

import Foundation
import NaturalLanguage
import SQLite3

// MARK: - Configuration

/// Content categories to skip entirely (receipts, invoices, etc)
let skipCategories: Set<String> = []  // We'll filter by content patterns instead

/// Title patterns that indicate garbage content (receipts, orders, policies)
let garbageTitlePatterns: [String] = [
    "receipt",
    "order",
    "invoice",
    "policy.*encrypted",
    "costco.*receipt",
    "instacart.*order",
    "_uss_",  // United Site Services receipts
    "pendant.*necklaces",  // Random purchase
    "stickers",
    "banners",
    "dry ice",
]

/// Content patterns that indicate garbage (JSON, receipt data, etc)
let garbageContentPatterns: [String] = [
    "Customer ID:",
    "Order No:",
    "Credit Card Authorize",
    "XXXXXXXXXXXX",  // Masked card numbers
    "Terms: Due Upon Receipt",
    "Tran #:",
    "Cashier:",
    "Total:",
    "Customer Copy",
]

/// Chat system message patterns to filter
let systemMessagePatterns: [String] = [
    "joined using a group link",
    "added you",
    "left the group",
    "changed the group",
    "changed this group",
    "created group",
    "Messages and calls are end-to-end encrypted",
]

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

// MARK: - Content Cleaning

/// Check if title indicates garbage content
func isGarbageTitle(_ title: String) -> Bool {
    let lowercased = title.lowercased()
    for pattern in garbageTitlePatterns {
        if let regex = try? NSRegularExpression(pattern: pattern, options: .caseInsensitive) {
            let range = NSRange(lowercased.startIndex..., in: lowercased)
            if regex.firstMatch(in: lowercased, range: range) != nil {
                return true
            }
        }
    }
    return false
}

/// Check if content indicates garbage (receipts, orders, etc)
func isGarbageContent(_ content: String) -> Bool {
    for pattern in garbageContentPatterns {
        if content.contains(pattern) {
            return true
        }
    }

    // Check for high punctuation ratio (JSON-like content)
    let punctuation = content.filter { "{}[]\",:".contains($0) }
    if Double(punctuation.count) / Double(max(content.count, 1)) > 0.15 {
        return true
    }

    return false
}

/// Check if chat message is a system message
func isSystemMessage(_ content: String) -> Bool {
    let lowercased = content.lowercased()
    for pattern in systemMessagePatterns {
        if lowercased.contains(pattern.lowercased()) {
            return true
        }
    }

    // Check for join/leave patterns with special characters
    if content.hasPrefix("~") || content.hasPrefix("‎") {
        return true
    }

    return false
}

/// Clean document content (remove page markers, normalize whitespace)
func cleanDocumentContent(_ content: String) -> String {
    var result = content

    // Remove Unicode control characters
    result = result.replacingOccurrences(of: "‎", with: "")
    result = result.replacingOccurrences(of: "\u{200E}", with: "")  // Left-to-right mark
    result = result.replacingOccurrences(of: "\u{200F}", with: "")  // Right-to-left mark
    result = result.replacingOccurrences(of: "\u{FEFF}", with: "")  // Zero-width no-break space

    // Remove page/slide markers
    let pagePatterns = [
        "## Page \\d+",
        "## Slide \\d+",
        "\\*\\*Slide \\d+\\*\\*",
        "^---+$",
        "---\\s*##",
    ]

    for pattern in pagePatterns {
        if let regex = try? NSRegularExpression(pattern: pattern, options: [.anchorsMatchLines, .caseInsensitive]) {
            result = regex.stringByReplacingMatches(
                in: result,
                range: NSRange(result.startIndex..., in: result),
                withTemplate: ""
            )
        }
    }

    // Normalize whitespace
    while result.contains("  ") {
        result = result.replacingOccurrences(of: "  ", with: " ")
    }
    while result.contains("\n\n\n") {
        result = result.replacingOccurrences(of: "\n\n\n", with: "\n\n")
    }

    return result.trimmingCharacters(in: .whitespacesAndNewlines)
}

/// Clean chat content
func cleanChatContent(_ content: String) -> String {
    var result = content

    // Remove Unicode control characters
    result = result.replacingOccurrences(of: "‎", with: "")
    result = result.replacingOccurrences(of: "~", with: "")
    result = result.replacingOccurrences(of: "\u{200E}", with: "")
    result = result.replacingOccurrences(of: "\u{200F}", with: "")
    result = result.replacingOccurrences(of: "\u{FEFF}", with: "")

    return result.trimmingCharacters(in: .whitespacesAndNewlines)
}

// MARK: - Embedding Generator

func generateEmbedding(_ text: String, embedding: NLEmbedding) -> [Float]? {
    // Skip empty or very short text
    let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
    guard trimmed.count >= 3 else { return nil }

    guard let vector = embedding.vector(for: trimmed) else { return nil }
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

    print("🧹 Data Preprocessing & Embedding Script")
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

    // Process person_content (chat messages)
    print("\n💬 Processing person_content...")
    let contentRows = try db.query("SELECT id, content FROM person_content")
    var contentCount = 0
    var contentSkipped = 0

    for row in contentRows {
        guard let id = row["id"] as? Int64,
              let content = row["content"] as? String else { continue }

        // Skip system messages
        if isSystemMessage(content) {
            contentSkipped += 1
            continue
        }

        // Clean the content
        let cleaned = cleanChatContent(content)
        guard !cleaned.isEmpty else {
            contentSkipped += 1
            continue
        }

        if let vector = generateEmbedding(cleaned, embedding: sentenceEmbedding) {
            let json = vectorToJSON(vector)
            try db.insert(
                "INSERT INTO embeddings (content_id, embedding, model_name) VALUES (?, ?, ?)",
                bindings: [id, json, "apple-nlembedding-512"]
            )
            contentCount += 1

            if contentCount % 100 == 0 {
                print("   Processed \(contentCount)/\(contentRows.count - contentSkipped)")
            }
        }
    }
    print("✅ Embedded \(contentCount) chat messages (skipped \(contentSkipped) system messages)")

    // Process camp_knowledge
    print("\n📄 Processing camp_knowledge...")
    let knowledgeRows = try db.query("SELECT id, title, content, category FROM camp_knowledge")
    var knowledgeCount = 0
    var knowledgeSkipped = 0
    var skippedTitles: [String] = []

    for row in knowledgeRows {
        guard let id = row["id"] as? Int64,
              let title = row["title"] as? String,
              let content = row["content"] as? String else { continue }

        // Skip garbage content by title
        if isGarbageTitle(title) {
            knowledgeSkipped += 1
            skippedTitles.append(title)
            continue
        }

        // Skip garbage content by content patterns
        if isGarbageContent(content) {
            knowledgeSkipped += 1
            skippedTitles.append(title)
            continue
        }

        // Clean the content
        let cleanedContent = cleanDocumentContent(content)
        guard !cleanedContent.isEmpty else {
            knowledgeSkipped += 1
            continue
        }

        // Combine title and content for embedding
        let text = "\(title)\n\(cleanedContent)"

        if let vector = generateEmbedding(text, embedding: sentenceEmbedding) {
            let json = vectorToJSON(vector)
            try db.insert(
                "INSERT INTO knowledge_embeddings (knowledge_id, embedding, model_name) VALUES (?, ?, ?)",
                bindings: [id, json, "apple-nlembedding-512"]
            )
            knowledgeCount += 1

            if knowledgeCount % 50 == 0 {
                print("   Processed \(knowledgeCount)/\(knowledgeRows.count - knowledgeSkipped)")
            }
        }
    }
    print("✅ Embedded \(knowledgeCount) knowledge items (skipped \(knowledgeSkipped) garbage entries)")

    if !skippedTitles.isEmpty {
        print("\n📋 Skipped documents:")
        for title in skippedTitles.prefix(10) {
            print("   - \(title)")
        }
        if skippedTitles.count > 10 {
            print("   ... and \(skippedTitles.count - 10) more")
        }
    }

    // Verify
    let embCount = try db.query("SELECT COUNT(*) as cnt FROM embeddings")
    let kembCount = try db.query("SELECT COUNT(*) as cnt FROM knowledge_embeddings")

    print("\n✨ Done!")
    print("   Chat embeddings: \(embCount.first?["cnt"] ?? 0)")
    print("   Knowledge embeddings: \(kembCount.first?["cnt"] ?? 0)")
    print("   Dimension: 512 (Apple NLEmbedding)")
}

do {
    try main()
} catch {
    print("❌ Error: \(error.localizedDescription)")
    exit(1)
}
