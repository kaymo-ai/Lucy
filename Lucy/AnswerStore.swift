import Foundation
import SQLite3

// What the camp told her.
//
// Two kinds of row live here and the difference matters. `mine` is an answer
// typed on this phone -- it works offline, immediately, unreviewed, because it
// is yours and Lucy saying it back to you crosses no trust boundary. Everything
// else arrived approved from the camp server and is a fact for everyone.
//
// Deliberately a separate database from lucy-memory.db and from the shipped
// enriched_preview.db. The shipped one is read-only and replaced wholesale by
// an update; this one must survive updates, like ChatLog's. Keeping it apart
// from ChatLog is the conceptual split: what you ASKED against what the camp
// ANSWERED.
//
// A row here reaches the model only through Retrieval, as a CampFact, which is
// the invariant in CLAUDE.md: retrieval supplies the facts, the model phrases
// them. Nothing in this file is ever pasted into a prompt.

struct CampAnswer {
    let id: UUID
    let gapKey: String
    let question: String
    let body: String
}

/// Answers, on disk.
@MainActor
final class AnswerStore {
    static let shared = AnswerStore()

    private var db: OpaquePointer?

    private static var path: String {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("lucy-answers.db").path
    }

    private init() {
        guard sqlite3_open_v2(Self.path, &db,
                              SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE, nil) == SQLITE_OK
        else {
            print("LUCY: answers open failed at \(Self.path)")
            db = nil
            return
        }
        // `answer_id` is the server's id for a row that came down, and NULL
        // for one typed here that has not been uploaded yet. UNIQUE on it is
        // what makes two syncs of the same answer store one row -- SQLite
        // treats NULLs as distinct, so locally-typed rows are unaffected.
        exec("""
            CREATE TABLE IF NOT EXISTS answer (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                answer_id  TEXT UNIQUE,
                gap_key    TEXT,
                question   TEXT NOT NULL,
                body       TEXT NOT NULL,
                mine       INTEGER NOT NULL DEFAULT 0,
                uploaded   INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            );
            """)
        exec("CREATE INDEX IF NOT EXISTS answer_pending ON answer(uploaded) WHERE mine = 1;")
    }

    private func exec(_ sql: String) {
        guard let db else { return }
        var err: UnsafeMutablePointer<CChar>?
        if sqlite3_exec(db, sql, nil, nil, &err) != SQLITE_OK, let err {
            print("LUCY: answers exec failed — \(String(cString: err))")
            sqlite3_free(err)
        }
    }

    // MARK: - Writing

    /// An answer typed on this phone. Serves this phone at once; reaches the
    /// camp only after a Sync and a human approving it.
    func record(question: String, body: String) {
        guard let db, !question.isEmpty, !body.isEmpty else { return }
        var stmt: OpaquePointer?
        let sql = """
            INSERT INTO answer (question, body, mine, uploaded, created_at)
            VALUES (?, ?, 1, 0, ?);
            """
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return }
        defer { sqlite3_finalize(stmt) }
        sqlite3_bind_text(stmt, 1, (question as NSString).utf8String, -1, nil)
        sqlite3_bind_text(stmt, 2, (body as NSString).utf8String, -1, nil)
        sqlite3_bind_double(stmt, 3, Date().timeIntervalSince1970)
        sqlite3_step(stmt)
    }

    /// Approved answers that came down on Sync. `mine` stays 0, so they are
    /// never queued back up.
    func store(approved: [CampAnswer]) {
        guard let db, !approved.isEmpty else { return }
        for a in approved {
            var stmt: OpaquePointer?
            let sql = """
                INSERT OR IGNORE INTO answer
                    (answer_id, gap_key, question, body, mine, uploaded, created_at)
                VALUES (?, ?, ?, ?, 0, 1, ?);
                """
            guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { continue }
            sqlite3_bind_text(stmt, 1, (a.id.uuidString as NSString).utf8String, -1, nil)
            sqlite3_bind_text(stmt, 2, (a.gapKey as NSString).utf8String, -1, nil)
            sqlite3_bind_text(stmt, 3, (a.question as NSString).utf8String, -1, nil)
            sqlite3_bind_text(stmt, 4, (a.body as NSString).utf8String, -1, nil)
            sqlite3_bind_double(stmt, 5, Date().timeIntervalSince1970)
            sqlite3_step(stmt)
            sqlite3_finalize(stmt)
        }
    }

    // MARK: - Uploading

    /// Answers typed here that the camp has not acked. The rowid rides along
    /// because `markUploaded` needs it back, same idiom as ChatLog.
    /// `device` is injectable so this is testable off a phone: the default
    /// reaches Identity.shared, which reads the keychain, and a unit-test host
    /// has no keychain entitlement. Same shape as the injectable clock the
    /// android port gave ChatLog.
    func pendingUpload(limit: Int = 200, device: UUID? = nil)
        -> [(rowid: Int64, id: UUID, question: String, body: String)]
    {
        guard let db else { return [] }
        var stmt: OpaquePointer?
        let sql = """
            SELECT id, question, body FROM answer
            WHERE mine = 1 AND uploaded = 0 ORDER BY id ASC LIMIT \(limit);
            """
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        defer { sqlite3_finalize(stmt) }
        var out: [(rowid: Int64, id: UUID, question: String, body: String)] = []
        while sqlite3_step(stmt) == SQLITE_ROW {
            let rowid = sqlite3_column_int64(stmt, 0)
            out.append((
                rowid: rowid,
                // Derived, not random: a retry after a dead connection must
                // send the same id or the server stores the answer twice.
                // Same UUIDv5-by-hand idiom as Capture.derivedID.
                id: Self.uploadID(rowid: rowid, device: device),
                question: sqlite3_column_text(stmt, 1).map { String(cString: $0) } ?? "",
                body: sqlite3_column_text(stmt, 2).map { String(cString: $0) } ?? ""))
        }
        return out
    }

    /// Stable across retries and unique across phones.
    static func uploadID(rowid: Int64, device: UUID? = nil) -> UUID {
        let id = device ?? Identity.shared.deviceID
        return Capture.derivedID(device: id, folder: "answer-\(rowid)")
    }

    /// Called only after the server said it stored the batch.
    func markUploaded(rowids: [Int64]) {
        guard !rowids.isEmpty else { return }
        let list = rowids.map(String.init).joined(separator: ",")
        exec("UPDATE answer SET uploaded = 1 WHERE id IN (\(list));")
    }

    // MARK: - Reading

    /// Answers bearing on this question, as CampFacts.
    ///
    /// CampFact and not a new type on purpose: LucyVoice already composes camp
    /// facts, so an answer needs no new branch anywhere downstream, and cannot
    /// accidentally become a second prose path into the prompt.
    ///
    /// Both sides are matched, and only the body is ever returned as the fact.
    ///
    /// Matching the question is not optional: somebody asked "where is the
    /// medkit" and the answer is "In Doris, left side" -- which contains no
    /// word of the question. Search the body alone and the answer is
    /// unreachable by the only words anyone will ever look for it with, which
    /// defeats the entire feature.
    ///
    /// What keeps her from parroting the question back is the CampFact shape,
    /// not the search: the question becomes `topic` and only the body becomes
    /// `fact`. LucyVoice states facts.
    func search(terms: [String], limit: Int = 3) -> [CampFact] {
        guard db != nil, !terms.isEmpty else { return [] }
        var scored: [(CampFact, Int)] = []
        for row in rows("SELECT id, question, body FROM answer ORDER BY id DESC LIMIT 400;") {
            let hay = (row.question + " " + row.body).lowercased()
            let hits = terms.filter { containsWord(hay, $0) }.count
            guard hits > 0 else { continue }
            // Negative, so a camp answer can never collide with a
            // camp_knowledge id. CampFact is Identifiable and ChatView renders
            // ForEach(turn.camp); both id spaces are autoincrements starting at
            // 1, so the first answer would otherwise share an identity with
            // camp_knowledge row 1. EntityStore.swift:547 records what that
            // costs -- a citation naming the wrong source.
            scored.append((CampFact(id: -row.rowid,
                                    topic: row.question,
                                    fact: row.body,
                                    category: "camp answer",
                                    year: nil,
                                    quote: "",
                                    // What LucyVoice cites. A camp answer must
                                    // never read as if it shipped in the
                                    // corpus.
                                    sourceTitle: "answered by the camp"), hits))
        }
        return scored.sorted { $0.1 > $1.1 }.prefix(limit).map(\.0)
    }

    func forget() {
        exec("DELETE FROM answer;")
    }

    private func rows(_ sql: String) -> [(rowid: Int64, question: String, body: String)] {
        guard let db else { return [] }
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        defer { sqlite3_finalize(stmt) }
        var out: [(rowid: Int64, question: String, body: String)] = []
        while sqlite3_step(stmt) == SQLITE_ROW {
            out.append((rowid: sqlite3_column_int64(stmt, 0),
                        question: sqlite3_column_text(stmt, 1).map { String(cString: $0) } ?? "",
                        body: sqlite3_column_text(stmt, 2).map { String(cString: $0) } ?? ""))
        }
        return out
    }
}
