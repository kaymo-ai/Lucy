import Foundation
import SQLite3

// Read-only reader for the enrichment schema (scripts/enrich_schema.py).
//
// The spine of the design: the database answers, the LLM only phrases it.
// Every row here traces back to `evidence`, and every evidence row names the
// exact corpus row and quote it came from. Nothing in this file invents,
// merges, or resolves anything -- including contradictions.

private let SQLITE_TRANSIENT = unsafeBitCast(-1, to: sqlite3_destructor_type.self)

struct Entity: Identifiable, Hashable {
    let id: Int64
    let name: String
    let kind: String
    let summary: String
    let firstSeen: String?
    let lastSeen: String?
    let mentionCount: Int
    var aliases: [String] = []
}

struct EntityFact: Identifiable, Hashable {
    let id: Int64
    let fact: String
    /// access | contents | location | handling | history — nil is allowed.
    let category: String?
    let assertedOn: String?
    var evidence: [Evidence] = []
}

struct Evidence: Identifiable, Hashable {
    let id: Int64
    let quote: String
    /// "Cece Garland · PS BUILD 22 · 19 Aug 2022" — resolved from the source row.
    let attribution: String
}

/// Fact categories in the order they matter to someone standing in front of
/// the thing: how do I get in, what's inside, where is it, how do I treat it,
/// what happened to it.
enum FactCategory: String, CaseIterable {
    case access, contents, location, handling, history

    var label: String {
        switch self {
        case .access:   return "GETTING IN"
        case .contents: return "WHAT'S INSIDE"
        case .location: return "WHERE IT IS"
        case .handling: return "HANDLING"
        case .history:  return "HISTORY"
        }
    }
}

final class EntityStore {
    // Not fileprivate: PeopleStore.swift extends this type from another file.
    var db: OpaquePointer?

    init?(path: String) {
        // Read-only, and never mutate a knowledge database in place.
        guard sqlite3_open_v2(path, &db, SQLITE_OPEN_READONLY, nil) == SQLITE_OK else {
            return nil
        }
    }

    deinit { sqlite3_close(db) }

    /// How many rows a table holds — the menu's database fingerprint. A
    /// table this database predates counts as zero, same degradation rule
    /// as every search over an optional table. Names come from code, never
    /// from input, which is why interpolation is acceptable here.
    func rowCount(_ table: String) -> Int {
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        guard sqlite3_prepare_v2(db, "SELECT COUNT(*) FROM \(table);",
                                 -1, &stmt, nil) == SQLITE_OK,
              sqlite3_step(stmt) == SQLITE_ROW else { return 0 }
        return Int(sqlite3_column_int64(stmt, 0))
    }

    // MARK: - Lookup

    /// Resolves a name through `entity_alias` as well as `entity.name` --
    /// the camp calls the same thing several names, which is why that table
    /// exists at all.
    func findEntity(named query: String) -> Entity? {
        let sql = """
        SELECT e.id, e.name, e.kind, e.summary, e.first_seen, e.last_seen, e.mention_count
        FROM entity e
        LEFT JOIN entity_alias a ON a.entity_id = e.id
        WHERE LOWER(e.name) = LOWER(?1) OR LOWER(a.alias) = LOWER(?1)
        LIMIT 1;
        """
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return nil }
        sqlite3_bind_text(stmt, 1, query, -1, SQLITE_TRANSIENT)
        guard sqlite3_step(stmt) == SQLITE_ROW else { return nil }

        var entity = Entity(
            id: sqlite3_column_int64(stmt, 0),
            name: text(stmt, 1) ?? "",
            kind: text(stmt, 2) ?? "",
            summary: text(stmt, 3) ?? "",
            firstSeen: text(stmt, 4),
            lastSeen: text(stmt, 5),
            mentionCount: Int(sqlite3_column_int(stmt, 6))
        )
        entity.aliases = aliases(entityID: entity.id)
        return entity
    }

    func allEntities() -> [Entity] {
        let sql = """
        SELECT id, name, kind, summary, first_seen, last_seen, mention_count
        FROM entity ORDER BY mention_count DESC;
        """
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }

        var out: [Entity] = []
        while sqlite3_step(stmt) == SQLITE_ROW {
            var e = Entity(
                id: sqlite3_column_int64(stmt, 0),
                name: text(stmt, 1) ?? "",
                kind: text(stmt, 2) ?? "",
                summary: text(stmt, 3) ?? "",
                firstSeen: text(stmt, 4),
                lastSeen: text(stmt, 5),
                mentionCount: Int(sqlite3_column_int(stmt, 6))
            )
            e.aliases = aliases(entityID: e.id)
            out.append(e)
        }
        return out
    }

    private func aliases(entityID: Int64) -> [String] {
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        guard sqlite3_prepare_v2(db, "SELECT alias FROM entity_alias WHERE entity_id = ?;",
                                 -1, &stmt, nil) == SQLITE_OK else { return [] }
        sqlite3_bind_int64(stmt, 1, entityID)
        var out: [String] = []
        while sqlite3_step(stmt) == SQLITE_ROW {
            if let a = text(stmt, 0) { out.append(a) }
        }
        return out
    }

    // MARK: - Facts

    /// Facts for an entity, oldest first within each category.
    ///
    /// Ordering by date is the whole point. The camp said "no ladders" in
    /// Doris on 2022-08-19 and "at least three ladders" on 2022-08-24. Both
    /// rows are kept and both are shown, in the order they were said. Picking
    /// the newer one and calling it the answer would be inventing a conclusion
    /// the camp never reached.
    func facts(entityID: Int64) -> [FactCategory: [EntityFact]] {
        let sql = """
        SELECT id, fact, category, asserted_on
        FROM entity_fact WHERE entity_id = ?
        ORDER BY asserted_on ASC, id ASC;
        """
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [:] }
        sqlite3_bind_int64(stmt, 1, entityID)

        var grouped: [FactCategory: [EntityFact]] = [:]
        while sqlite3_step(stmt) == SQLITE_ROW {
            let id = sqlite3_column_int64(stmt, 0)
            var fact = EntityFact(
                id: id,
                fact: text(stmt, 1) ?? "",
                category: text(stmt, 2),
                assertedOn: text(stmt, 3)
            )
            fact.evidence = evidence(claimTable: "entity_fact", claimID: id)
            let cat = FactCategory(rawValue: fact.category ?? "") ?? .history
            grouped[cat, default: []].append(fact)
        }
        return grouped
    }

    // MARK: - Evidence

    /// Every claim can show its receipt. Resolves the source row into a human
    /// attribution so a fact can be traced without leaving the screen.
    func evidence(claimTable: String, claimID: Int64) -> [Evidence] {
        let sql = """
        SELECT ev.id, ev.quote, ev.source_table, ev.source_id
        FROM evidence ev
        WHERE ev.claim_table = ? AND ev.claim_id = ?;
        """
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        sqlite3_bind_text(stmt, 1, claimTable, -1, SQLITE_TRANSIENT)
        sqlite3_bind_int64(stmt, 2, claimID)

        var out: [Evidence] = []
        while sqlite3_step(stmt) == SQLITE_ROW {
            let id = sqlite3_column_int64(stmt, 0)
            let quote = text(stmt, 1) ?? ""
            let table = text(stmt, 2) ?? ""
            let sourceID = sqlite3_column_int64(stmt, 3)
            out.append(Evidence(id: id, quote: quote,
                                attribution: attribution(table: table, id: sourceID)))
        }
        return out
    }

    private func attribution(table: String, id: Int64) -> String {
        switch table {
        case "person_content":
            let sql = """
            SELECT p.name, pc.source, pc.timestamp
            FROM person_content pc LEFT JOIN person p ON p.id = pc.person_id
            WHERE pc.id = ?;
            """
            var stmt: OpaquePointer?
            defer { sqlite3_finalize(stmt) }
            guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return "" }
            sqlite3_bind_int64(stmt, 1, id)
            guard sqlite3_step(stmt) == SQLITE_ROW else { return "" }
            return [text(stmt, 0), text(stmt, 1), Self.humanDate(text(stmt, 2))]
                .compactMap { $0 }.filter { !$0.isEmpty }
                .joined(separator: " · ")

        case "camp_knowledge":
            let sql = "SELECT title, source_file, year FROM camp_knowledge WHERE id = ?;"
            var stmt: OpaquePointer?
            defer { sqlite3_finalize(stmt) }
            guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return "" }
            sqlite3_bind_int64(stmt, 1, id)
            guard sqlite3_step(stmt) == SQLITE_ROW else { return "" }
            let year = sqlite3_column_int(stmt, 2)
            return [text(stmt, 0), text(stmt, 1), year > 0 ? String(year) : nil]
                .compactMap { $0 }.filter { !$0.isEmpty }
                .joined(separator: " · ")

        default:
            return ""
        }
    }

    // MARK: - Helpers

    private func text(_ stmt: OpaquePointer?, _ col: Int32) -> String? {
        guard let c = sqlite3_column_text(stmt, col) else { return nil }
        return String(cString: c)
    }

    /// "2022-08-19T09:47:44" -> "19 Aug 2022". Dates are load-bearing here, so
    /// they are always rendered, never dropped for being noisy.
    static func humanDate(_ raw: String?) -> String? {
        guard let raw, !raw.isEmpty else { return nil }
        let datePart = String(raw.prefix(10))
        let inFmt = DateFormatter()
        inFmt.dateFormat = "yyyy-MM-dd"
        inFmt.locale = Locale(identifier: "en_US_POSIX")
        guard let date = inFmt.date(from: datePart) else { return datePart }
        let outFmt = DateFormatter()
        outFmt.dateFormat = "d MMM yyyy"
        outFmt.locale = Locale(identifier: "en_US_POSIX")
        return outFmt.string(from: date)
    }
}

// MARK: - Documents and general knowledge
//
// Entities answer "what is Doris". They do not answer "how do we get water",
// because that lives in the camp manual, and neither answers "how much water
// does a person need", which nobody in the chat ever had to explain.

struct DocHit: Identifiable, Hashable {
    let id: Int64
    let title: String
    let category: String?
    let year: Int?
    /// The passage around the match, not the whole document — some of these
    /// run to thousands of words.
    let passage: String
}

struct GeneralFact: Identifiable, Hashable {
    let id: Int64
    let topic: String
    let fact: String
    let source: String
}

/// One person's name against one shift on one day.
struct ShiftAssignment: Identifiable, Hashable {
    let id: Int64
    let day: String
    let timeSlot: String
    let shift: String
    let category: String
    let person: String
    /// What the shift actually involves, in the sheet's own words. Every row
    /// has one, and "Burn Barrel" at 9am does not tell you what to do.
    let detail: String
    /// Which burn this sheet is for. Carried so a screen can say so, and so a
    /// second year cannot arrive unlabelled.
    let year: Int?
}

extension EntityStore {
    /// The newest year's rota, as rows.
    ///
    /// Read from `shift_grid`, which parse_shift_grid.py writes -- NOT from
    /// the legacy `shifts` table, which never held a real shift: 2,140 rows in
    /// which person_name and role were the same spreadsheet cell. Empty when
    /// the database predates that parser, which callers must handle, because a
    /// database without a rota is a valid database.
    ///
    /// ONE YEAR, not all of them. The parser deletes per source file, so two
    /// sheets coexist by design, and there are 2024, 2023, 2019, 2018 and 2016
    /// shift sheets in the corpus waiting to become parseable. Returning them
    /// all put a 2024 Sunday under the same "Sunday" header as a 2026 Sunday
    /// with nothing on screen between them -- the exact lie the schedule row
    /// next door goes out of its way to avoid telling.
    func rota() -> [ShiftAssignment] {
        guard hasTable("shift_grid") else { return [] }
        var out: [ShiftAssignment] = []
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        // Ordered by the week rather than alphabetically, because a rota is
        // read as a week -- then by category, so the bus crew sit together.
        // Deliberately NOT by time: the sheet writes times as free text
        // ("~5-9p", "Any time (3 hrs)", "10a-12p & 8-9p") and sorting those
        // as strings puts one in the afternoon before nine in the morning.
        let sql = """
            SELECT id, day, COALESCE(time_slot,''), shift,
                   COALESCE(category,''), person, COALESCE(description,'')
                   , year
            FROM shift_grid
            WHERE COALESCE(year, 0) = (SELECT MAX(COALESCE(year, 0))
                                       FROM shift_grid)
            ORDER BY CASE lower(day)
                       WHEN 'sunday' THEN 0 WHEN 'monday' THEN 1
                       WHEN 'tuesday' THEN 2 WHEN 'wednesday' THEN 3
                       WHEN 'thursday' THEN 4 WHEN 'friday' THEN 5
                       ELSE 6 END,
                     COALESCE(category,''), shift, person;
            """
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        while sqlite3_step(stmt) == SQLITE_ROW {
            out.append(ShiftAssignment(
                id: sqlite3_column_int64(stmt, 0),
                day: String(cString: sqlite3_column_text(stmt, 1)),
                timeSlot: String(cString: sqlite3_column_text(stmt, 2)),
                shift: String(cString: sqlite3_column_text(stmt, 3)),
                category: String(cString: sqlite3_column_text(stmt, 4)),
                person: String(cString: sqlite3_column_text(stmt, 5)),
                detail: String(cString: sqlite3_column_text(stmt, 6)),
                year: sqlite3_column_type(stmt, 7) == SQLITE_NULL
                      ? nil : Int(sqlite3_column_int64(stmt, 7))))
        }
        return out
    }

    /// The id of a camp fact whose text matches a LIKE pattern.
    ///
    /// For tests, which must not pin themselves to an autoincrement: camp_fact
    /// is rebuilt from scratch on every ingest, so an id that meant one row
    /// yesterday means another today, and a test written against it fails on
    /// a rebuild that broke nothing.
    func campFactID(matching pattern: String) -> Int64? {
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        let sql = "SELECT id FROM camp_fact WHERE lower(fact) LIKE ? ORDER BY id LIMIT 1;"
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return nil }
        sqlite3_bind_text(stmt, 1, (pattern.lowercased() as NSString).utf8String, -1, nil)
        guard sqlite3_step(stmt) == SQLITE_ROW else { return nil }
        return sqlite3_column_int64(stmt, 0)
    }

    /// One camp document, whole, for reading rather than for answering from.
    ///
    /// `searchDocs` returns a passage window around matched terms, which is
    /// right for a fact sheet and useless for a person who wants to read the
    /// section on gray water. This returns the document.
    func document(id: Int64) -> (title: String, content: String, year: Int?)? {
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        let sql = "SELECT title, content, year FROM camp_knowledge WHERE id = ?;"
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return nil }
        sqlite3_bind_int64(stmt, 1, id)
        guard sqlite3_step(stmt) == SQLITE_ROW else { return nil }
        return (String(cString: sqlite3_column_text(stmt, 0)),
                String(cString: sqlite3_column_text(stmt, 1)),
                sqlite3_column_type(stmt, 2) == SQLITE_NULL
                    ? nil : Int(sqlite3_column_int(stmt, 2)))
    }

    /// The camp's own manual, newest edition.
    ///
    /// Recognised by the camp's own name for it -- "PS 2025-26 Manual
    /// Potentially Useful Info", "PS 2019 Potentially Useful Info" -- which is
    /// the same marker `authority()` privileges when ranking documents.
    /// Newest wins, because the camp keeps every edition and only one is
    /// current, and the retired ones say so in their titles.
    ///
    /// Matching the camp's phrase rather than the bare word "manual" is what
    /// keeps the 110,000-character generator manual out of this.
    func campManual() -> (id: Int64, title: String, year: Int?)? {
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        let sql = """
            SELECT id, title, year FROM camp_knowledge
            WHERE lower(title) LIKE '%potentially useful info%'
              AND lower(title) NOT LIKE '%no longer in use%'
            ORDER BY COALESCE(year, 0) DESC, id DESC LIMIT 1;
            """
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK,
              sqlite3_step(stmt) == SQLITE_ROW else { return nil }
        return (sqlite3_column_int64(stmt, 0),
                String(cString: sqlite3_column_text(stmt, 1)),
                sqlite3_column_type(stmt, 2) == SQLITE_NULL
                    ? nil : Int(sqlite3_column_int(stmt, 2)))
    }

    /// What the camp has written down about events, newest first.
    ///
    /// Returns what it finds and lets the caller decide what counts as
    /// current. A screen headed "The schedule" that quietly showed a 2019
    /// party plan as this year's would be worse than one admitting there is
    /// nothing current, so the year travels with the row.
    func eventDocuments(limit: Int = 12) -> [(id: Int64, title: String, year: Int?)] {
        var out: [(Int64, String, Int?)] = []
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        let sql = """
            SELECT id, title, year FROM camp_knowledge WHERE category = 'events'
            ORDER BY COALESCE(year, 0) DESC, id DESC LIMIT ?;
            """
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        sqlite3_bind_int(stmt, 1, Int32(limit))
        while sqlite3_step(stmt) == SQLITE_ROW {
            out.append((sqlite3_column_int64(stmt, 0),
                        String(cString: sqlite3_column_text(stmt, 1)),
                        sqlite3_column_type(stmt, 2) == SQLITE_NULL
                            ? nil : Int(sqlite3_column_int(stmt, 2))))
        }
        return out
    }

    func searchDocs(terms: [String], limit: Int = 2) -> [DocHit] {
        guard !terms.isEmpty else { return [] }
        var hits: [(DocHit, Double)] = []
        let sql = "SELECT id, title, content, category, year FROM camp_knowledge;"
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }

        while sqlite3_step(stmt) == SQLITE_ROW {
            let id = sqlite3_column_int64(stmt, 0)
            let title = String(cString: sqlite3_column_text(stmt, 1))
            let content = sqlite3_column_text(stmt, 2).map { String(cString: $0) } ?? ""
            let category = sqlite3_column_text(stmt, 3).map { String(cString: $0) }
            let year = sqlite3_column_type(stmt, 4) == SQLITE_NULL
                ? nil : Int(sqlite3_column_int(stmt, 4))

            let lower = content.lowercased()
            let titleLower = title.lowercased()
            let cat = (category ?? "").lowercased()

            // What the document is about matters far more than what it happens
            // to mention. The generator manual says to keep cooling holes clear
            // of "mud, water, etc." — one incidental word, and on body-text
            // scoring alone it outranked every document actually about water.
            var score = 0.0
            for term in terms {
                if containsWord(titleLower, term) { score += 6 }
                if cat == term { score += 5 }
            }

            // How often, not merely whether. Requiring two distinct terms
            // rejected every single-word question — "how do we get water"
            // reduces to one term after stop words, so the camp manual scored
            // zero despite saying "water" 83 times, and a purchase order won on
            // its category alone.
            //
            // Frequency relative to length is the honest signal: it separates a
            // document about water from one that mentions it in passing, which
            // is what the two-term rule was clumsily reaching for.
            var occurrences = 0
            for term in terms { occurrences += Self.count(term, in: lower) }
            if occurrences > 0 {
                // Absolute count leads. Density alone penalised exactly the
                // documents worth reading: the manual says "water" 83 times in
                // 63k characters and lost to a 1.4k note that said it 7 times.
                // A long reference is not less relevant for being thorough.
                let per1k = Double(occurrences) * 1000.0 / Double(max(content.count, 1))
                score += min(Double(occurrences), 40) * 0.6 + min(per1k, 3)
            }
            let distinct = terms.filter { containsWord(lower, $0) }.count
            if distinct >= 2 { score += Double(distinct) }

            score += Self.authority(title: titleLower, category: cat, year: year)

            // After the authority prior, before the threshold: a stale
            // edition of the manual has to clear the same bar as a current
            // one. Guarded positive for the reason authority() explains.
            if score > 0 { score *= Self.recency(year) }

            guard score >= 4, let passage = Self.passage(in: content, around: terms) else { continue }
            hits.append((DocHit(id: id, title: title, category: category,
                                year: year, passage: passage), score))
        }
        return hits.sorted { $0.1 > $1.1 }.prefix(limit).map(\.0)
    }

    /// Not all documents are equally worth quoting. The camp manual is the
    /// thing people are told to read; a Costco receipt that happens to mention
    /// water is not an answer to anything. This is a prior on the document,
    /// independent of the question.
    ///
    /// "Potentially Useful Info" is the camp's own name for the manual —
    /// PS 2025-26, PS 2019 — so it is recognised explicitly rather than by a
    /// generic word.
    private static func authority(title: String, category: String,
                                  year _: Int?) -> Double {
        var score = 0.0
        for marker in ["manual", "potentially useful info", "binder",
                       "handbook", "survival guide"] where title.contains(marker) {
            score += 14
            break
        }
        // Receipts, invoices and purchase orders mention things without ever
        // explaining them.
        for marker in ["receipt", "invoice", "expense", "budget", "order",
                       "costco", "walmart"] where title.contains(marker) {
            score -= 10
            break
        }
        if category == "operations" { score += 2 }
        // Recency is NOT applied here. This function returns a NEGATIVE
        // number for receipts and invoices, and scaling a penalty by a decay
        // shrinks it -- -10 * 0.35 is -3.5, so an old receipt would outrank a
        // new one. Exactly backwards, and it would have looked like better
        // ranking. The decay is applied by the caller, where the score is
        // known to be positive.
        return score
    }

    /// Whole-word occurrences, which is what "is this document about X" needs.
    private static func count(_ word: String, in text: String) -> Int {
        var n = 0
        var from = text.startIndex
        while let r = text.range(of: word, range: from..<text.endIndex) {
            let before = r.lowerBound == text.startIndex ? nil
                : text[text.index(before: r.lowerBound)]
            let after = r.upperBound == text.endIndex ? nil : text[r.upperBound]
            let edge: (Character?) -> Bool = { c in
                guard let c else { return true }
                return !c.isLetter && !c.isNumber
            }
            if edge(before) && edge(after) { n += 1 }
            from = r.upperBound
            if n > 500 { break }
        }
        return n
    }

    /// A readable window around the first matching term. Documents here include
    /// scanned PDFs and spreadsheet dumps, so the window is trimmed to sentence
    /// boundaries where it can find them.
    private static func passage(in content: String, around terms: [String]) -> String? {
        let lower = content.lowercased()

        // Every position the question's words appear, not just the first. A
        // purchase order opens with a shipping address, and anchoring on the
        // first "water" showed the reader Brooklyn instead of the water order.
        var positions: [String.Index] = []
        for term in terms where containsWord(lower, term) {
            var from = lower.startIndex
            while let r = lower.range(of: term, range: from..<lower.endIndex) {
                positions.append(r.lowerBound)
                from = r.upperBound
                if positions.count > 400 { break }
            }
        }
        guard !positions.isEmpty else { return nil }

        // The window with the most matches in it is the part of the document
        // actually about the question.
        let radius = 220
        let anchor = positions.max { a, b in
            density(lower, around: a, radius: radius, positions: positions)
                < density(lower, around: b, radius: radius, positions: positions)
        } ?? positions[0]

        let start = content.index(anchor, offsetBy: -radius,
                                  limitedBy: content.startIndex) ?? content.startIndex
        let end = content.index(anchor, offsetBy: radius,
                                limitedBy: content.endIndex) ?? content.endIndex
        var window = String(content[start..<end])
            .replacingOccurrences(of: "\n", with: " ")
        while window.contains("  ") {
            window = window.replacingOccurrences(of: "  ", with: " ")
        }
        window = window.trimmingCharacters(in: .whitespaces)
        // The window lands mid-word on both ends more often than not, and a
        // ragged edge gets recited into answers verbatim — "ctly, or email
        // Kristina" reached a phone screen that way. Prefer the sentence
        // boundary, fall back to a word boundary, and mark a cut interior
        // edge as the excerpt it is.
        var leadingClean = start == content.startIndex
        if let dot = window.firstIndex(of: "."), !leadingClean,
           window.distance(from: window.startIndex, to: dot) < 90 {
            window = String(window[window.index(after: dot)...])
                .trimmingCharacters(in: .whitespaces)
            leadingClean = true
        }
        if !leadingClean, let sp = window.firstIndex(of: " ") {
            window = "… " + String(window[window.index(after: sp)...])
        }
        if end != content.endIndex, let sp = window.lastIndex(of: " ") {
            window = String(window[..<sp]) + " …"
        }
        guard !window.isEmpty, isReadable(window) else { return nil }
        return window
    }

    /// How many matches fall inside the window centred on `index`.
    private static func density(_ text: String, around index: String.Index,
                                radius: Int, positions: [String.Index]) -> Int {
        let lo = text.index(index, offsetBy: -radius, limitedBy: text.startIndex)
            ?? text.startIndex
        let hi = text.index(index, offsetBy: radius, limitedBy: text.endIndex)
            ?? text.endIndex
        return positions.filter { $0 >= lo && $0 <= hi }.count
    }

    /// Some documents are PDF extractions that lost their spacing —
    /// "ssthan3feet(1meter)fromabuildingor". The text is in the database and
    /// matches searches fine, but putting it on screen is worse than saying
    /// nothing: it reads as the app being broken, and it cannot be acted on.
    private static func isReadable(_ text: String) -> Bool {
        let words = text.split(separator: " ")
        guard !words.isEmpty else { return false }
        // A run of 25+ characters with no space is not a word in any document
        // a human wrote.
        if words.contains(where: { $0.count > 25 }) { return false }

        // Many documents are spreadsheet exports stored as raw JSON —
        // [{ "Cost": "250.0", "Member": "Kat", ... }]. Searchable, and useless
        // to read: a budget row is not an answer to "how do we get water".
        let structural = text.filter { "{}[]\"".contains($0) }.count
        if Double(structural) / Double(text.count) > 0.02 { return false }
        let spaces = text.filter { $0 == " " }.count
        // English prose sits near one space per 6 characters. Below one in 12
        // means the spacing is gone.
        return Double(spaces) / Double(text.count) > 1.0 / 12.0
    }

    func searchGeneral(terms: [String], limit: Int = 2) -> [GeneralFact] {
        guard !terms.isEmpty else { return [] }
        var hits: [(GeneralFact, Int)] = []
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        let sql = "SELECT id, topic, fact, source FROM general_knowledge;"
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }

        while sqlite3_step(stmt) == SQLITE_ROW {
            let id = sqlite3_column_int64(stmt, 0)
            let topic = String(cString: sqlite3_column_text(stmt, 1))
            let fact = String(cString: sqlite3_column_text(stmt, 2))
            let source = String(cString: sqlite3_column_text(stmt, 3))
            let hay = (topic + " " + fact).lowercased()
            // Topic matches count double: asking about water should reach the
            // water facts before a fact that merely mentions water in passing.
            let score = terms.reduce(0) {
                $0 + (topic.lowercased() == $1 ? 2 : (containsWord(hay, $1) ? 1 : 0))
            }
            guard score > 0 else { continue }
            hits.append((GeneralFact(id: id, topic: topic, fact: fact, source: source), score))
        }
        return hits.sorted { $0.1 > $1.1 }.prefix(limit).map(\.0)
    }
}

// MARK: - Camp facts
//
// What the documents actually say, extracted once at build time instead of
// keyword-searched at runtime. This is the thing four rounds of passage scoring
// were reaching for: "We must pick up our service vouchers at the USS Camp" is
// an answer; a 440-character window that opens on a shipping address is not.

struct CampFact: Identifiable, Hashable {
    let id: Int64
    let topic: String
    let fact: String
    let category: String?
    let year: Int?
    var quote: String = ""
    var sourceTitle: String = ""
    /// Cosine against the question, when the encoder is on this phone and this
    /// row had a vector. 0 means "not measured", which is the same as it was
    /// before E7 and is why it defaults rather than being optional: every
    /// existing construction of a CampFact keeps compiling and keeps meaning
    /// what it meant.
    var similarity: Float = 0
}

extension EntityStore {
    /// How much an old claim still counts, as a MULTIPLIER on its score.
    ///
    /// It used to be a capped bonus: `score += min((year - 2016) * 0.4, 4)`.
    /// That makes recency a tiebreaker rather than a weight. An exact topic
    /// match is worth 8 and a whole-word hit is worth 3, so a well-matched
    /// 2018 row beat a decently-matched 2026 row every time -- and 45% of the
    /// camp's facts are 2019 or older. 2018 and 2019 alone are 606 rows, more
    /// than every year since 2022 combined. The camp buys different water,
    /// parks in a different place and runs different shifts now.
    ///
    /// Multiplying instead means an old fact has to be genuinely BETTER
    /// matched to win, rather than merely as good. Roughly a 12% penalty per
    /// year, floored so that an old row which is the only match still
    /// surfaces: being stale is a reason to rank lower, never a reason to
    /// vanish.
    ///
    ///     2026  1.00      2022  0.60      2018  0.36 (floored to 0.35)
    ///     2025  0.88      2021  0.53      2016  0.35
    ///
    /// An undated claim scores , NOT 1.0. Scoring it 1.0 was
    /// the first version and it was wrong: 160 camp facts carry no year, so
    /// it handed every one of them a 3x advantage over anything dated 2019,
    /// rewarding missing metadata. It was caught by a test -- "how do we get
    /// water delivered" started preferring an undated vouchers row over the
    /// 2019 row that is the only one in the table containing the word
    /// "deliver".
    ///
    /// The honest prior for an unknown year is a typical year, and this
    /// corpus is heavy in 2018-2021, so undated sits near that weight rather
    /// than at the top of the range.
    ///
    /// NOT applied to lore. A good story does not go stale, and only 4% of
    /// lore is 2019 or older anyway. This is for the operational layers,
    /// where last year's answer is actively wrong.
    static func recency(_ year: Int?, now: Int = currentYear) -> Double {
        guard let year, year > 0 else { return undatedWeight }
        let age = max(0, now - year)
        return max(0.35, pow(0.88, Double(age)))
    }

    /// What a claim with no year is worth: roughly a 2021 claim, which is
    /// near this corpus's median.
    static let undatedWeight = 0.6

    /// Read once. A retrieval that changed its ranking halfway through a
    /// session because midnight passed would be a strange thing to debug.
    static let currentYear: Int =
        Calendar.current.component(.year, from: Date())

    func searchCampFacts(terms: [String], queryVector: [Float]? = nil,
                         limit: Int = 5) -> [CampFact] {
        guard !terms.isEmpty else { return [] }
        var hits: [(CampFact, Double)] = []
        // The join to camp_knowledge must test source_table as well as the id.
        // Evidence can now cite a roster row, and camp_member ids run 1-313
        // against camp_knowledge's 1-616 — so joining on the id alone gave
        // every roster fact the title of whichever unrelated document happened
        // to share its number. A citation that names the wrong source is worse
        // than none: the whole point of showing receipts is that they can be
        // checked.
        let sql = """
        SELECT f.id, f.topic, f.fact, f.category, f.year,
               COALESCE(e.quote, ''),
               -- No leading "our": the title lands inside a "[from our ...]"
               -- template, and "from our our camp roster" reads badly.
               COALESCE(k.title,
                        CASE WHEN e.source_table = 'camp_member'
                             THEN 'camp roster' END,
                        '')
        FROM camp_fact f
        LEFT JOIN evidence e ON e.claim_table = 'camp_fact' AND e.claim_id = f.id
        LEFT JOIN camp_knowledge k ON k.id = e.source_id
                                  AND e.source_table = 'camp_knowledge'
        GROUP BY f.id;
        """
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }

        // The words a camper would ask with that the fact itself never says —
        // "medkit" for the first-aid fact, "delivered" for service vouchers.
        // Generated at build time into ask_word; a database from before that
        // stage has no table, and the failed prepare leaves scoring exactly
        // as it was.
        var askWords: [Int64: Set<String>] = [:]
        var aw: OpaquePointer?
        if sqlite3_prepare_v2(db, """
            SELECT claim_id, word FROM ask_word WHERE claim_table = 'camp_fact';
            """, -1, &aw, nil) == SQLITE_OK {
            while sqlite3_step(aw) == SQLITE_ROW {
                guard let w = sqlite3_column_text(aw, 1) else { continue }
                // The stage writes single words and short phrases, but the
                // match below is against single question terms, so a stored
                // phrase could never fire. Each of its words carries the
                // match instead — loose on purpose: vocabulary nominates
                // candidates at score 2, it never outranks a direct match.
                let id = sqlite3_column_int64(aw, 0)
                for part in String(cString: w)
                    .components(separatedBy: CharacterSet.alphanumerics.inverted)
                    where part.count > 2 {
                    askWords[id, default: []].insert(part)
                }
            }
        }
        sqlite3_finalize(aw)

        while sqlite3_step(stmt) == SQLITE_ROW {
            let id = sqlite3_column_int64(stmt, 0)
            let topic = String(cString: sqlite3_column_text(stmt, 1))
            let fact = String(cString: sqlite3_column_text(stmt, 2))
            let category = sqlite3_column_text(stmt, 3).map { String(cString: $0) }
            let year = sqlite3_column_type(stmt, 4) == SQLITE_NULL
                ? nil : Int(sqlite3_column_int(stmt, 4))
            let quote = String(cString: sqlite3_column_text(stmt, 5))
            let title = String(cString: sqlite3_column_text(stmt, 6))

            let lower = fact.lowercased()
            var score = 0.0
            for term in terms {
                // The topic is the strongest signal: it is what the fact is
                // about, as opposed to a word that happens to appear in it.
                // "We cook the pasta in boiling salted water" is filed under
                // kitchen, and should not answer a question about water.
                if topic.lowercased() == term { score += 8 }
                if containsWord(lower, term) { score += 3 }
                // A generated ask-word is a guess at how someone would phrase
                // it, not the fact saying it: below fact text, far below
                // topic, so vocabulary can add a candidate row but never
                // steal the top answer from a direct match.
                else if askWords[id]?.contains(term) == true { score += 2 }
            }
            guard score > 0 else { continue }
            // Newer guidance supersedes older; the camp keeps every edition.
            // Multiplied, not added -- see EntityStore.recency.
            score *= Self.recency(year)
            hits.append((CampFact(id: id, topic: topic, fact: fact,
                                  category: category, year: year,
                                  quote: quote, sourceTitle: title), score))
        }
        // E7 -- semantic recall, unioned in.
        //
        // Deliberately NOT summed into the score above. Learnings section 4
        // already ruled on this class of thing: a one-term camp match can
        // outscore a two-term entity match by 3-4x under these formulas, so
        // adding a [0,1] cosine to weights of 8/3/2 would be a fourth
        // incomparable scale pretending to be arithmetic. Semantic decides
        // which rows are ELIGIBLE; the lexical score still orders the ones
        // that matched words, and rows that matched no words at all follow
        // them, ordered by how close they actually were.
        //
        // This is what E7 was named for: "how do we get water delivered"
        // shares no word with a row about picking up service vouchers, so no
        // amount of tuning the weights above could ever have reached it.
        var lexical = hits.sorted { $0.1 > $1.1 }.prefix(limit).map(\.0)
        guard let query = queryVector else { return Array(lexical) }

        let already = Set(lexical.map(\.id))
        var near = similarClaims(table: "camp_fact", to: query,
                                 limit: limit * 4, floor: Self.similarityFloor)
        // A row can arrive both ways. Carry the similarity onto the lexical
        // copy rather than listing it twice -- the fact sheet ranks on it.
        for i in lexical.indices {
            if let s = near[lexical[i].id] { lexical[i].similarity = s }
        }
        near = near.filter { !already.contains($0.key) }
        guard !near.isEmpty else { return Array(lexical) }

        var extra: [CampFact] = []
        for (id, similarity) in near.sorted(by: { $0.value > $1.value }) {
            guard var row = campFact(id: id) else { continue }
            // The roster is a near-duplicate attractor and must not be
            // reachable this way.
            //
            // Hundreds of rows read "X signed up and said yes to camping with
            // us in YEAR", and eighty-odd read "Contact details for X". They
            // are formulaic, so they cluster tightly, and any question carrying
            // a name or a year lands in that cluster ahead of anything that
            // answers it. Measured: "what's the camp placed in 2024" scored
            // 0.546-0.569 on eight roster signups -- higher than the genuine
            // hits for "do we cook our food" -- and every one of the top eight
            // for "why do people make fun of Ed" was somebody's email address.
            //
            // No floor separates these, because they are not weak matches:
            // they are strong matches to the wrong sense of the question. The
            // lexical path still reaches them, and should, because a name is
            // an exact match and that is high precision. Similarity is not.
            // , not : category holds who/how/rule/where/when,
            // and the roster lives under topic. 528 of the 1,745 camp facts --
            // 30% of the table -- are these, which is why they dominate.
            guard row.topic != "roster" else { continue }
            row.similarity = similarity
            extra.append(row)
            if extra.count == Self.semanticLimit { break }
        }
        return Array(lexical) + extra
    }

    /// Cosine of every stored vector for one table against the question,
    /// keyed by claim id, keeping only what clears the floor.
    ///
    /// Brute force on purpose. 1,745 vectors of 768 floats is about 1.3M
    /// multiply-adds, which is nothing next to a single token of generation,
    /// and an index would be a second thing that can silently disagree with
    /// the data it indexes.
    func similarClaims(table: String, to query: [Float],
                       limit: Int, floor: Float) -> [Int64: Float] {
        var out: [Int64: Float] = [:]
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        let sql = """
            SELECT claim_id, embedding FROM claim_embedding
            WHERE claim_table = ? AND model_name = ?;
            """
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [:] }
        sqlite3_bind_text(stmt, 1, (table as NSString).utf8String, -1, nil)
        // Vectors from a different encoder are not detectably wrong -- cosine
        // between mismatched vectors is just a number. Filtering by the name
        // the pipeline wrote is what stops a half-migrated database returning
        // confident nonsense.
        sqlite3_bind_text(stmt, 2, (Embedder.modelName as NSString).utf8String, -1, nil)

        var scored: [(Int64, Float)] = []
        while sqlite3_step(stmt) == SQLITE_ROW {
            let id = sqlite3_column_int64(stmt, 0)
            guard let blob = sqlite3_column_blob(stmt, 1) else { continue }
            let bytes = Int(sqlite3_column_bytes(stmt, 1))
            guard bytes == Embedder.dimensions * MemoryLayout<Float>.size else { continue }
            var v = [Float](repeating: 0, count: Embedder.dimensions)
            _ = v.withUnsafeMutableBytes { memcpy($0.baseAddress!, blob, bytes) }
            let similarity = dot(query, normalise(v))
            if similarity >= floor { scored.append((id, similarity)) }
        }
        for (id, similarity) in scored.sorted(by: { $0.1 > $1.1 }).prefix(limit) {
            out[id] = similarity
        }
        return out
    }

    /// Recalibrated against the 19 questions actually asked on the owner's
    /// phone after the encoder went live, because the first floor was set from
    /// one good question and 0.35 turned out to be well inside the noise.
    ///
    /// What the real questions score:
    ///
    ///     how do we get water delivered   0.569 .. 0.456   all water logistics
    ///     do we cook our food             0.551 .. 0.487   all cooking
    ///     does Piotr like water           0.413 .. 0.351   his battery, his shifts
    ///     what are some camp stories      0.416 .. 0.403   generators, arrival times
    ///     the story about Holmar          0.315 .. 0.196   Doris, coolers
    ///     why do people make fun of Ed    0.293 .. 0.224   contact details
    ///     what's an Oz hole               0.244 .. 0.213   contact details
    ///
    /// The break is clean at 0.45: everything above it answers the question,
    /// everything below merely mentions something in it. At 0.35 "does Piotr
    /// like water" pulled in four rows about Piotr's generator and battery --
    /// which is exactly the bug `gatedPortrait` was written to fix, walking
    /// back in through a door semantics had just opened.
    static let similarityFloor: Float = 0.45

    /// At most this many rows may be added by similarity alone.
    ///
    /// The lexical limit is 5, and semantic recall was allowed the same, so a
    /// vague question could double the fact sheet. More rows is more length and
    /// more list -- the thing the owner has been complaining about since the
    /// first device build. Semantic recall exists to reach the row words cannot,
    /// which is one or two rows, not five.
    static let semanticLimit = 2

    /// One stored vector, exactly as the pipeline wrote it -- unnormalised.
    /// Exists for `SemanticRecallTests`, which uses a stored vector as its own
    /// query so the arithmetic can be checked without the 333 MB encoder.
    func rawClaimVector(table: String, id: Int64) -> [Float]? {
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        let sql = """
            SELECT embedding FROM claim_embedding
            WHERE claim_table = ? AND claim_id = ?;
            """
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return nil }
        sqlite3_bind_text(stmt, 1, (table as NSString).utf8String, -1, nil)
        sqlite3_bind_int64(stmt, 2, id)
        guard sqlite3_step(stmt) == SQLITE_ROW,
              let blob = sqlite3_column_blob(stmt, 0) else { return nil }
        let bytes = Int(sqlite3_column_bytes(stmt, 0))
        guard bytes == Embedder.dimensions * MemoryLayout<Float>.size else { return nil }
        var v = [Float](repeating: 0, count: Embedder.dimensions)
        _ = v.withUnsafeMutableBytes { memcpy($0.baseAddress!, blob, bytes) }
        return v
    }

    /// How many claim vectors there are, optionally only those of a given
    /// byte length. Two counts that must agree is how a wrong dimension is
    /// caught in a test rather than in an answer.
    func claimVectorCount(bytes: Int?) -> Int {
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        let sql = bytes == nil
            ? "SELECT count(*) FROM claim_embedding;"
            : "SELECT count(*) FROM claim_embedding WHERE length(embedding) = ?;"
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return -1 }
        if let bytes { sqlite3_bind_int(stmt, 1, Int32(bytes)) }
        guard sqlite3_step(stmt) == SQLITE_ROW else { return -1 }
        return Int(sqlite3_column_int(stmt, 0))
    }

    /// One camp fact by id, for rows semantic recall reached that the lexical
    /// query never selected.
    func campFact(id: Int64) -> CampFact? {
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        // The same joins the lexical query above uses, narrowed to one row.
        // camp_fact has no source_id of its own -- provenance hangs off
        // `evidence` -- and the first version of this reached for f.source_id,
        // which does not exist. sqlite3_prepare_v2 simply failed, this
        // returned nil, and every semantically-recalled row was dropped on the
        // floor with no error anywhere. The tests caught it; nothing else
        // would have.
        let sql = """
            SELECT f.id, f.topic, f.fact, f.category, f.year,
                   COALESCE(e.quote, ''),
                   COALESCE(k.title,
                            CASE WHEN e.source_table = 'camp_member'
                                 THEN 'camp roster' END,
                            '')
            FROM camp_fact f
            LEFT JOIN evidence e ON e.claim_table = 'camp_fact' AND e.claim_id = f.id
            LEFT JOIN camp_knowledge k ON k.id = e.source_id
                                      AND e.source_table = 'camp_knowledge'
            WHERE f.id = ?
            GROUP BY f.id;
            """
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return nil }
        sqlite3_bind_int64(stmt, 1, id)
        guard sqlite3_step(stmt) == SQLITE_ROW else { return nil }
        return CampFact(
            id: sqlite3_column_int64(stmt, 0),
            topic: String(cString: sqlite3_column_text(stmt, 1)),
            fact: String(cString: sqlite3_column_text(stmt, 2)),
            category: sqlite3_column_text(stmt, 3).map { String(cString: $0) },
            year: sqlite3_column_type(stmt, 4) == SQLITE_NULL
                ? nil : Int(sqlite3_column_int(stmt, 4)),
            quote: sqlite3_column_text(stmt, 5).map { String(cString: $0) } ?? "",
            sourceTitle: sqlite3_column_text(stmt, 6).map { String(cString: $0) } ?? "")
    }
}

// MARK: - Lore
//
// The stories the camp still tells — a fire through Lucy's roof, the year a
// structure failed. The table sat in the schema holding zero rows for months
// while "remember when" questions found nothing; enrichment writes it now,
// and this is the read side. Retellings only: the quotes stay in evidence.

struct LoreHit: Identifiable, Hashable {
    let id: Int64
    let title: String
    let story: String
    let year: Int?
    /// Comma-separated names, for display only.
    let people: String
}

extension EntityStore {
    /// Stories the question brushes against. Scored modestly on purpose:
    /// lore is colour, not operations, and the fact sheet already places it
    /// after everything operational — this just decides whether a story is
    /// about the question at all. A single incidental word in a paragraph of
    /// story is not, which is what the threshold is for.
    ///
    /// Older databases have no lore table; the failed prepare returns empty,
    /// the same way searchGeneral survives a database without
    /// general_knowledge.
    /// Colour to talk with when the question found nothing.
    ///
    /// Not a search — there is nothing to search *with*, which is the whole
    /// situation this exists for. It returns stories at random so that "I have
    /// nothing on that" can be followed by something the camp actually
    /// recorded, rather than by silence. Random rather than newest or best:
    /// the same three stories arriving every time she is stumped would be its
    /// own kind of dead end.
    ///
    /// Still rows. Everything she says off the back of this is quoting the
    /// camp's own record, so being funny here cannot become inventing here.
    func colour(limit: Int = 2) -> [LoreHit] {
        var out: [LoreHit] = []
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        let sql = """
            SELECT id, title, story, year, people FROM lore
            WHERE story IS NOT NULL AND length(story) > 80
            ORDER BY RANDOM() LIMIT ?;
            """
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        sqlite3_bind_int(stmt, 1, Int32(limit))
        while sqlite3_step(stmt) == SQLITE_ROW {
            out.append(LoreHit(
                id: sqlite3_column_int64(stmt, 0),
                title: String(cString: sqlite3_column_text(stmt, 1)),
                story: String(cString: sqlite3_column_text(stmt, 2)),
                year: sqlite3_column_type(stmt, 3) == SQLITE_NULL
                    ? nil : Int(sqlite3_column_int(stmt, 3)),
                people: sqlite3_column_text(stmt, 4).map { String(cString: $0) } ?? ""))
        }
        return out
    }

    func searchLore(terms: [String], queryVector: [Float]? = nil,
                    limit: Int = 2) -> [LoreHit] {
        // A question with no usable terms can still have a meaning, which is
        // the whole point of a vector. Only bail when there is neither.
        guard !terms.isEmpty || queryVector != nil else { return [] }
        var hits: [(LoreHit, Double)] = []
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        let sql = "SELECT id, title, story, year, people FROM lore;"
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }

        while sqlite3_step(stmt) == SQLITE_ROW {
            let id = sqlite3_column_int64(stmt, 0)
            let title = String(cString: sqlite3_column_text(stmt, 1))
            let story = String(cString: sqlite3_column_text(stmt, 2))
            let year = sqlite3_column_type(stmt, 3) == SQLITE_NULL
                ? nil : Int(sqlite3_column_int(stmt, 3))
            let people = sqlite3_column_text(stmt, 4).map { String(cString: $0) } ?? ""

            let titleLower = title.lowercased()
            let storyLower = story.lowercased()
            let peopleLower = people.lowercased()
            var score = 0.0
            for term in terms {
                // The title is what the story is about; the body merely
                // mentions things. Repeats in the body count a little — a
                // story actually about the fire says "fire" more than once —
                // but never enough to outrank a title.
                if containsWord(titleLower, term) { score += 3 }
                if containsWord(peopleLower, term) { score += 2 }
                score += Double(min(Self.count(term, in: storyLower), 3))
            }
            guard score >= 2 else { continue }
            hits.append((LoreHit(id: id, title: title, story: story,
                                 year: year, people: people), score))
        }
        let lexical = hits.sorted { $0.1 > $1.1 }.prefix(limit).map(\.0)
        guard let query = queryVector else { return Array(lexical) }

        // Same rule as camp facts: semantic decides eligibility, the lexical
        // score still orders what matched words. Lore benefits from it more
        // than anything else here -- a story is remembered by what happened in
        // it, and "the year we nearly lost the truck" shares no word with a
        // row titled after the thing that actually burned.
        let already = Set(lexical.map(\.id))
        let near = similarClaims(table: "lore", to: query,
                                 limit: Self.semanticLimit,
                                 floor: Self.similarityFloor)
            .filter { !already.contains($0.key) }
        guard !near.isEmpty else { return Array(lexical) }

        var extra: [LoreHit] = []
        for (id, _) in near.sorted(by: { $0.value > $1.value }) {
            if let row = loreHit(id: id) { extra.append(row) }
        }
        return Array(lexical) + Array(extra.prefix(Self.semanticLimit))
    }

    /// One story by id, for rows semantic recall reached.
    func loreHit(id: Int64) -> LoreHit? {
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        let sql = "SELECT id, title, story, year, people FROM lore WHERE id = ?;"
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return nil }
        sqlite3_bind_int64(stmt, 1, id)
        guard sqlite3_step(stmt) == SQLITE_ROW else { return nil }
        return LoreHit(
            id: sqlite3_column_int64(stmt, 0),
            title: String(cString: sqlite3_column_text(stmt, 1)),
            story: String(cString: sqlite3_column_text(stmt, 2)),
            year: sqlite3_column_type(stmt, 3) == SQLITE_NULL
                ? nil : Int(sqlite3_column_int(stmt, 3)),
            people: sqlite3_column_text(stmt, 4).map { String(cString: $0) } ?? "")
    }
}
