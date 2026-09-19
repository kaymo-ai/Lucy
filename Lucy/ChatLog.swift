import Foundation
import SQLite3

// What you told her, kept.
//
// Every question and answer, for as long as the app is installed. Not a
// context window: a week of conversation does not fit in one, and never will
// at any model size. This is the same architecture the camp knowledge already
// uses -- store everything, retrieve what bears on the question, and let the
// model phrase what it is handed.
//
// That is what makes recall work at distance. "Where did I say I parked my
// bike" reaches a turn from Tuesday that is three hundred turns back; no
// context window reaches that, and a rolling summary would have thrown the
// detail away long before you asked.
//
// Deliberately a SEPARATE database from enriched_preview.db. That one ships
// inside the app, is read-only, and is replaced wholesale by an update. This
// one is yours, lives in Documents, and must survive every update -- so the two
// cannot share a file. It is also the split that matters conceptually: what the
// camp knows, against what you said.
//
// Called ChatLog and not Memory. "Memory" had come to mean four things here --
// this, the Memory tab (which is notes and captures), llama's KV cache, and the
// phone's RAM -- and two of them are one tap apart in the same screen. This is
// the name the backend already uses for it (/v1/chatlog) and the name the spec
// uses, so Help Lucy has one word for it end to end.

struct Remembered {
    let askedAt: Date
    let question: String
    let answer: String
    /// Whether retrieval found nothing for this turn -- recorded when the turn
    /// was answered, not inferred from the words afterwards. nil for turns
    /// written before the column existed, which is why it is an optional and
    /// not a Bool: "we did not record this" and "we recorded that she had
    /// material" are different states and only one of them is a gap.
    let unanswered: Bool?
}

/// The conversation, on disk.
@MainActor
final class ChatLog {
    static let shared = ChatLog()

    private var db: OpaquePointer?

    /// Documents, not Caches: iOS empties Caches under pressure, and a
    /// companion that forgets the week when the phone runs low on space is
    /// worse than one that never claimed to remember.
    private static var path: String {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("lucy-memory.db").path
    }

    private init() {
        guard sqlite3_open_v2(Self.path, &db,
                              SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE, nil) == SQLITE_OK
        else {
            print("LUCY: memory open failed at \(Self.path)")
            db = nil
            return
        }
        exec("""
            CREATE TABLE IF NOT EXISTS turn (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                asked_at  REAL NOT NULL,
                question  TEXT NOT NULL,
                answer    TEXT NOT NULL
            );
            """)
        // Recall searches both sides: what you asked and what she said back.
        // Half of what you tell her arrives inside a question.
        exec("CREATE INDEX IF NOT EXISTS turn_asked ON turn(asked_at);")

        // These two columns arrived after the table shipped. This database
        // survives app updates, so CREATE TABLE IF NOT EXISTS never revisits
        // it -- additions go through guarded ALTER TABLE, checked against
        // pragma table_info so a second launch does not re-add them.
        //
        // `uploaded` lives here, on the turn itself, and the server acking a
        // turn is the only thing that flips it: a sync that dies halfway
        // leaves the flag untouched and retries exactly the turns the camp
        // never got. A marker anywhere else could drift from the rows it
        // describes.
        let have = columns("turn")
        if !have.contains("uploaded") {
            exec("ALTER TABLE turn ADD COLUMN uploaded INTEGER NOT NULL DEFAULT 0;")
        }
        if !have.contains("model") {
            exec("ALTER TABLE turn ADD COLUMN model TEXT;")
        }
        // The structural replacement for reading her own words back.
        //
        // Whether a turn was a gap is known at the moment it is answered --
        // retrieval either found rows or it did not -- so it is recorded then.
        // It used to be recovered afterwards by matching the answer against a
        // list of refusal phrases, three of which the app had written itself.
        // Nullable on purpose: turns from before this shipped genuinely have
        // no answer to the question, and must not be silently counted as
        // having had material.
        if !have.contains("unanswered") {
            exec("ALTER TABLE turn ADD COLUMN unanswered INTEGER;")
        }
    }

    private func columns(_ table: String) -> Set<String> {
        guard let db else { return [] }
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, "PRAGMA table_info(\(table));", -1, &stmt, nil) == SQLITE_OK
        else { return [] }
        defer { sqlite3_finalize(stmt) }
        var out = Set<String>()
        while sqlite3_step(stmt) == SQLITE_ROW {
            if let name = sqlite3_column_text(stmt, 1) {
                out.insert(String(cString: name))
            }
        }
        return out
    }

    private func exec(_ sql: String) {
        guard let db else { return }
        var err: UnsafeMutablePointer<CChar>?
        if sqlite3_exec(db, sql, nil, nil, &err) != SQLITE_OK, let err {
            print("LUCY: memory exec failed — \(String(cString: err))")
            sqlite3_free(err)
        }
    }

    // MARK: - Writing

    /// `model` is the file that actually generated the answer, or nil when the
    /// templates did. Unattributed answers were unarguable once already; the
    /// journal learned this lesson first and the log gets the same treatment.
    func record(question: String, answer: String, model: String? = nil,
                unanswered: Bool? = nil) {
        guard let db, !question.isEmpty, !answer.isEmpty else { return }
        var stmt: OpaquePointer?
        let sql = """
            INSERT INTO turn (asked_at, question, answer, model, unanswered)
            VALUES (?, ?, ?, ?, ?);
            """
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return }
        defer { sqlite3_finalize(stmt) }
        sqlite3_bind_double(stmt, 1, Date().timeIntervalSince1970)
        sqlite3_bind_text(stmt, 2, (question as NSString).utf8String, -1, nil)
        sqlite3_bind_text(stmt, 3, (answer as NSString).utf8String, -1, nil)
        if let model {
            sqlite3_bind_text(stmt, 4, (model as NSString).utf8String, -1, nil)
        } else {
            sqlite3_bind_null(stmt, 4)
        }
        if let unanswered {
            sqlite3_bind_int(stmt, 5, unanswered ? 1 : 0)
        } else {
            sqlite3_bind_null(stmt, 5)
        }
        sqlite3_step(stmt)
    }

    // MARK: - Uploading

    /// Turns the camp has not acked yet, oldest first, capped at the server's
    /// batch limit. The rowid rides along because it is what the entry's
    /// stable upload id derives from, and what `markUploaded` needs back.
    func pendingUpload(limit: Int = 500)
        -> [(rowid: Int64, askedAt: Date, question: String, answer: String,
             model: String?, unanswered: Bool?)]
    {
        guard let db else { return [] }
        var stmt: OpaquePointer?
        let sql = """
            SELECT id, asked_at, question, answer, model, unanswered FROM turn
            WHERE uploaded = 0 ORDER BY id ASC LIMIT \(limit);
            """
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        defer { sqlite3_finalize(stmt) }
        var out: [(rowid: Int64, askedAt: Date, question: String, answer: String,
                   model: String?, unanswered: Bool?)] = []
        while sqlite3_step(stmt) == SQLITE_ROW {
            out.append((rowid: sqlite3_column_int64(stmt, 0),
                        askedAt: Date(timeIntervalSince1970: sqlite3_column_double(stmt, 1)),
                        question: sqlite3_column_text(stmt, 2).map { String(cString: $0) } ?? "",
                        answer: sqlite3_column_text(stmt, 3).map { String(cString: $0) } ?? "",
                        model: sqlite3_column_text(stmt, 4).map { String(cString: $0) },
                        unanswered: sqlite3_column_type(stmt, 5) == SQLITE_NULL
                            ? nil : sqlite3_column_int(stmt, 5) != 0))
        }
        return out
    }

    /// Called only after the server said it stored the batch. Nothing else
    /// flips the flag.
    func markUploaded(rowids: [Int64]) {
        guard !rowids.isEmpty else { return }
        let list = rowids.map(String.init).joined(separator: ",")
        exec("UPDATE turn SET uploaded = 1 WHERE id IN (\(list));")
    }

    // MARK: - Reading

    /// The last few turns, newest last. Conversational continuity: enough for
    /// "what about the other one" to resolve, and for her not to repeat the
    /// answer she just gave.
    func recent(_ limit: Int = 3) -> [Remembered] {
        rows("SELECT asked_at, question, answer, unanswered FROM turn ORDER BY id DESC LIMIT \(limit);")
            .reversed()
    }

    /// Turns that bear on this question, by the same whole-word matching the
    /// camp facts use.
    ///
    /// It inherits that approach's weakness: "where's my bike" will not reach
    /// "I left the cruiser by the trash fence", because the words do not
    /// overlap. Personal history is smaller and in your own words, so it misses
    /// less often than the camp corpus does -- but embeddings would fix both.
    ///
    /// Recent turns are excluded here because `recent()` already carries them,
    /// and the same turn appearing twice in one prompt reads as emphasis.
    func recall(matching query: String, limit: Int = 4, excludingLast skip: Int = 3) -> [Remembered] {
        let terms = Retrieval.terms(query)
        guard !terms.isEmpty else { return [] }

        let recentIDs = rows("SELECT asked_at, question, answer, unanswered FROM turn ORDER BY id DESC LIMIT \(skip);")
        let excluded = Set(recentIDs.map { $0.askedAt })

        var scored: [(Remembered, Int)] = []
        for row in rows("SELECT asked_at, question, answer, unanswered FROM turn ORDER BY id DESC LIMIT 400;") {
            guard !excluded.contains(row.askedAt) else { continue }
            let hay = (row.question + " " + row.answer).lowercased()
            let hits = terms.filter { containsWord(hay, $0) }.count
            if hits > 0 { scored.append((row, hits)) }
        }
        return scored.sorted { $0.1 > $1.1 }.prefix(limit).map(\.0)
    }

    /// One page of older turns, newest page first, each page in reading order.
    ///
    /// For the thread's "earlier" control. Paged rather than loaded whole: the
    /// reason captures stopped opening the thread was that a week of things
    /// you already knew stood between you and the place you type, and handing
    /// the view three hundred turns at once would put it back.
    ///
    /// `offset` counts turns already on screen, so the caller does not have to
    /// track ids -- the log is append-only from the view's side, and a turn
    /// added while you read cannot renumber the ones behind it.
    func page(skipping offset: Int, limit: Int = 5) -> [Remembered] {
        rows("SELECT asked_at, question, answer, unanswered FROM turn "
             + "ORDER BY id DESC LIMIT \(limit) OFFSET \(offset);")
            .reversed()
    }

    /// How much she is holding, for the menu.
    var count: Int {
        rows("SELECT asked_at, question, answer, unanswered FROM turn;").count
    }

    func forget() {
        exec("DELETE FROM turn;")
    }

    private func rows(_ sql: String) -> [Remembered] {
        guard let db else { return [] }
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        defer { sqlite3_finalize(stmt) }
        var out: [Remembered] = []
        while sqlite3_step(stmt) == SQLITE_ROW {
            let at = sqlite3_column_double(stmt, 0)
            let q = sqlite3_column_text(stmt, 1).map { String(cString: $0) } ?? ""
            let a = sqlite3_column_text(stmt, 2).map { String(cString: $0) } ?? ""
            // Every caller of `rows` selects unanswered as column 3. A row
            // from before the column existed reads NULL and stays nil.
            let u: Bool? = sqlite3_column_count(stmt) > 3
                && sqlite3_column_type(stmt, 3) != SQLITE_NULL
                ? sqlite3_column_int(stmt, 3) != 0 : nil
            out.append(Remembered(askedAt: Date(timeIntervalSince1970: at),
                                  question: q, answer: a, unanswered: u))
        }
        return out
    }
}
