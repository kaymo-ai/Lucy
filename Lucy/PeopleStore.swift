import Foundation
import SQLite3

// The camp, as people rather than as facts.
//
// Three sources have to meet here and none of them agree on who anyone is.
// `camp_member` is the signup sheets: full names, years, contact details.
// `person` is the WhatsApp export, where people appear as whatever their
// campmates had saved — "Oz", "Piotr". `person_profile`, `expertise` and
// `personality` hang off the chat side.
//
// `camp_member.person_id` is the join, made at build time and left NULL
// wherever the name was ambiguous. So a card may have roster details and no
// chat, chat and no roster, or both — and the view has to be honest about
// which, because "we have nothing written down about them" is a real and
// common answer for a camp whose corpus is seven years of one group chat.

/// Someone worth asking, and why.
struct PersonSkill: Hashable {
    let name: String
    let topics: [String]
    let knownFor: String
}

/// Who someone is, for the fact sheet. The rows are the ones the People tab
/// already shows — profile, portrait, signature quote, ties — flattened into
/// lines the model can phrase. "Who is Piotr" used to get expertise topics at
/// best while all of this sat one tab away, unread by retrieval.
struct PersonPortrait: Hashable {
    let name: String
    let role: String            // person_profile.summary — what they do
    let persona: String         // personality.summary — how they come across
    /// personality.shows_up_as — the character, not the job. "The dependable
    /// organizer who cheerfully tackles the most tedious ticket
    /// distributions" rather than "manages administration and logistics".
    let showsUpAs: String
    /// personality.voice — HOW they write, which is the only place the camp's
    /// tone survives enrichment. camp_fact holds no emoji at all; this field
    /// holds "ends with a 😂 or ❤️", and that is the difference between
    /// knowing what somebody does and knowing what they are like.
    let voice: String
    let caresAbout: String
    /// person_profile.known_for — the one-line identifier. "Camp
    /// administration, WAP distribution, and build weekend coordination",
    /// where `role` is four sentences saying the same thing at length.
    let knownFor: String
    /// What they are the person to ask about, strongest first.
    let expertise: [String]
    /// The stories they are IN, from lore.people.
    ///
    /// All 163 lore rows carry a people list and none of it reached this card:
    /// lore was only ever found by term-matching the question, so "who is
    /// Piotr" got his summary and never "Piotr's Red Sweater". For a camp,
    /// the stories somebody appears in ARE their character -- more than any
    /// summary of what they administer.
    let stories: [String]
    let signatureQuote: String  // their own words, labelled as such
    /// "builds with Ana B" — relationship rows, strongest first.
    let relations: [String]
    /// When the question names two people, the rows linking them — which is
    /// what "do X and Y get on" is actually asking, so they lead.
    let pair: [String]
}

struct PersonCard: Identifiable, Hashable {
    /// Stable across both sources: roster id when there is one, else the
    /// chat id offset out of its range.
    let id: Int64
    let name: String
    let nickname: String
    let homeCity: String
    let email: String
    let phone: String
    /// "2019, 2022, 2023" — years they said yes on a signup sheet.
    let yearsAttended: String
    let yearsListed: String

    // From the chat side. Empty when unlinked or unenriched.
    let personID: Int64?
    let messageCount: Int
    let roleSummary: String       // person_profile.summary — what they do
    let knownFor: String
    let chapter: String
    let summary: String           // personality.summary — how they come across
    let voice: String
    let caresAbout: String
    let showsUpAs: String
    let signatureQuote: String
    let expertise: [String]

    /// The year lists are stored comma-joined for querying; they are read by
    /// people, so they get their spaces back on the way to the screen.
    static func spaced(_ years: String) -> String {
        years.split(separator: ",").joined(separator: ", ")
    }

    var hasChat: Bool { personID != nil && messageCount > 0 }
    var hasPortrait: Bool { !summary.isEmpty }
    var hasRoster: Bool { !yearsListed.isEmpty }

    /// What to show under the name in the list, in order of what a campmate
    /// would most want to know.
    var subtitle: String {
        if !knownFor.isEmpty { return knownFor }
        if !caresAbout.isEmpty { return caresAbout }
        if !yearsAttended.isEmpty { return "camped \(Self.spaced(yearsAttended))" }
        if messageCount > 0 { return "\(messageCount) messages" }
        return "on the roster"
    }
}

extension EntityStore {
    /// Whether a table exists, for queries that must survive an older
    /// database. A missing table does not make a query return less -- it
    /// makes prepare fail and the query return nothing at all.
    func hasTable(_ name: String) -> Bool {
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        let sql = "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?;"
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return false }
        sqlite3_bind_text(stmt, 1, (name as NSString).utf8String, -1, nil)
        return sqlite3_step(stmt) == SQLITE_ROW
    }

    /// Canonical names for any question term that is a recorded alias.
    ///
    /// The portrait matches aliases itself, but nothing else does -- so
    /// "why does Peet hate water" would find the man and none of his facts,
    /// because camp_fact says "Piotr" and the question does not. Rather than
    /// teach every search about aliases, the question is widened once: a term
    /// that is a known alias contributes the real name, and every layer
    /// downstream matches as it always has.
    ///
    /// Returns names, not tokens; the caller splits them, because it already
    /// owns the stoplist that decides which tokens are worth searching on.
    func canonicalNames(matching terms: [String]) -> [String] {
        guard !terms.isEmpty, hasTable("person_alias") else { return [] }
        let holes = Array(repeating: "?", count: terms.count).joined(separator: ",")
        let sql = """
        SELECT DISTINCT p.name FROM person_alias a
        JOIN person p ON p.id = a.person_id
        WHERE lower(a.alias) IN (\(holes));
        """
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        for (i, term) in terms.enumerated() {
            sqlite3_bind_text(stmt, Int32(i + 1),
                              (term.lowercased() as NSString).utf8String, -1, nil)
        }
        var out: [String] = []
        while sqlite3_step(stmt) == SQLITE_ROW {
            if let t = sqlite3_column_text(stmt, 0) { out.append(String(cString: t)) }
        }
        return out
    }

    /// Every recorded alias, most-talked-about people first.
    ///
    /// For CampVocabulary. Priming the recogniser with only the corpus
    /// spelling is what produced "arsehole" for "Oz hole" and "cc" for
    /// Cece: the name somebody says is often not the name the corpus wrote
    /// down. Ordered by message count so that, when the hundred-phrase budget
    /// runs out, it runs out on people nobody mentions.
    func personAliases(limit: Int = 60) -> [String] {
        var out: [String] = []
        let sql = """
        SELECT a.alias FROM person_alias a
        JOIN person p ON p.id = a.person_id
        ORDER BY COALESCE(p.message_count, 0) DESC, length(a.alias) DESC
        LIMIT ?;
        """
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        sqlite3_bind_int(stmt, 1, Int32(limit))
        while sqlite3_step(stmt) == SQLITE_ROW {
            if let t = sqlite3_column_text(stmt, 0) { out.append(String(cString: t)) }
        }
        return out
    }

    /// Everyone the camp has a record of, roster first, then chat-only people.
    ///
    /// Sorted by how much there is to show rather than alphabetically: a list
    /// of 313 names where the first forty are people who signed up once in
    /// 2016 and never spoke is a list nobody scrolls.
    func allPeople() -> [PersonCard] {
        var cards: [PersonCard] = []
        var claimed = Set<Int64>()

        let rosterSQL = """
        SELECT m.id, m.name, COALESCE(m.nickname,''), COALESCE(m.home_city,''),
               COALESCE(m.email,''), COALESCE(m.phone,''),
               COALESCE(m.years_attended,''), COALESCE(m.years_listed,''),
               m.person_id, COALESCE(p.message_count, 0),
               COALESCE(pp.summary,''), COALESCE(pp.known_for,''),
               COALESCE(pp.chapter,''),
               COALESCE(y.summary,''), COALESCE(y.voice,''),
               COALESCE(y.cares_about,''), COALESCE(y.shows_up_as,''),
               COALESCE(y.signature_quote,''), COALESCE(m.person_ids,'')
        FROM camp_member m
        LEFT JOIN person p           ON p.id  = m.person_id
        LEFT JOIN person_profile pp  ON pp.person_id = m.person_id
        LEFT JOIN personality y      ON y.person_id  = m.person_id
        ORDER BY m.name;
        """
        forEachRow(rosterSQL) { stmt in
            let personID = sqlite3_column_type(stmt, 8) == SQLITE_NULL
                ? nil : sqlite3_column_int64(stmt, 8)
            if let personID { claimed.insert(personID) }
            // WhatsApp exported some people more than once, each row holding
            // part of their writing — Katharine Hensley has 110 messages under
            // one and 106 under another. All of them belong to this card.
            for raw in text(stmt, 18).split(separator: ",") {
                if let id = Int64(raw) { claimed.insert(id) }
            }
            cards.append(PersonCard(
                id: sqlite3_column_int64(stmt, 0),
                name: cleanName(text(stmt, 1)), nickname: text(stmt, 2),
                homeCity: text(stmt, 3), email: text(stmt, 4),
                phone: text(stmt, 5), yearsAttended: text(stmt, 6),
                yearsListed: text(stmt, 7), personID: personID,
                messageCount: Int(sqlite3_column_int(stmt, 9)),
                roleSummary: text(stmt, 10), knownFor: text(stmt, 11),
                chapter: text(stmt, 12), summary: text(stmt, 13),
                voice: text(stmt, 14), caresAbout: text(stmt, 15),
                showsUpAs: text(stmt, 16), signatureQuote: text(stmt, 17),
                expertise: []))
        }

        // People who are in the chat but never matched a roster row — the
        // corpus has 1,035 distinct chat names against 313 on the sheets, so
        // dropping them would hide most of the camp's actual voices. Only
        // those with something written about them are worth a card.
        let chatSQL = """
        SELECT p.id, p.name, COALESCE(p.message_count,0),
               COALESCE(pp.summary,''), COALESCE(pp.known_for,''),
               COALESCE(pp.chapter,''),
               COALESCE(y.summary,''), COALESCE(y.voice,''),
               COALESCE(y.cares_about,''), COALESCE(y.shows_up_as,''),
               COALESCE(y.signature_quote,'')
        FROM person p
        LEFT JOIN person_profile pp ON pp.person_id = p.id
        LEFT JOIN personality y     ON y.person_id  = p.id
        WHERE pp.person_id IS NOT NULL OR y.person_id IS NOT NULL
        ORDER BY p.message_count DESC;
        """
        // A roster row and a chat row can carry the same name and still not be
        // linked — the build-time matcher refuses anything ambiguous, which is
        // right for the database and wrong for a list, where it shows the same
        // human twice. Name is a good enough key for display even where it was
        // not good enough to write down.
        let rosterNames = Set(cards.map { normalisedName($0.name) })

        forEachRow(chatSQL) { stmt in
            let personID = sqlite3_column_int64(stmt, 0)
            guard !claimed.contains(personID) else { return }
            guard !rosterNames.contains(normalisedName(cleanName(text(stmt, 1)))) else { return }
            cards.append(PersonCard(
                // Offset well past any roster id so the two id-spaces cannot
                // collide in a SwiftUI ForEach.
                id: personID + 1_000_000,
                name: cleanName(text(stmt, 1)), nickname: "", homeCity: "", email: "",
                phone: "", yearsAttended: "", yearsListed: "",
                personID: personID,
                messageCount: Int(sqlite3_column_int(stmt, 2)),
                roleSummary: text(stmt, 3), knownFor: text(stmt, 4),
                chapter: text(stmt, 5), summary: text(stmt, 6),
                voice: text(stmt, 7), caresAbout: text(stmt, 8),
                showsUpAs: text(stmt, 9), signatureQuote: text(stmt, 10),
                expertise: []))
        }

        let byPerson = expertiseByPerson()
        cards = cards.map { card in
            guard let pid = card.personID, let topics = byPerson[pid] else { return card }
            var c = card
            c = PersonCard(id: c.id, name: c.name, nickname: c.nickname,
                           homeCity: c.homeCity, email: c.email, phone: c.phone,
                           yearsAttended: c.yearsAttended, yearsListed: c.yearsListed,
                           personID: c.personID, messageCount: c.messageCount,
                           roleSummary: c.roleSummary, knownFor: c.knownFor,
                           chapter: c.chapter, summary: c.summary, voice: c.voice,
                           caresAbout: c.caresAbout, showsUpAs: c.showsUpAs,
                           signatureQuote: c.signatureQuote, expertise: topics)
            return c
        }

        // Most to least to show. Within a tier, alphabetical.
        return cards.sorted { a, b in
            let ra = rank(a), rb = rank(b)
            if ra != rb { return ra > rb }
            return a.name.localizedCaseInsensitiveCompare(b.name) == .orderedAscending
        }
    }

    private func rank(_ c: PersonCard) -> Int {
        (c.hasPortrait ? 4 : 0) + (!c.roleSummary.isEmpty ? 2 : 0)
            + (c.hasRoster ? 1 : 0)
    }

    /// Who to ask about something.
    ///
    /// "Who knows how to fix a bike" is one of the most useful questions this
    /// app can answer and for a long time it could not answer it at all:
    /// retrieval searched facts, documents and general knowledge, and never
    /// the two tables that record what people know. Walter Lindell has "bike
    /// repair" against his name and no question could reach it.
    ///
    /// Relevance first, the enrichment's confidence second. Scoring used to
    /// be matched × strength, which let a "moderate" tag that brushes the
    /// question beat a "mentioned" tag that IS the question — Walter's "bike
    /// repair" lost that same bike question to someone's "Bike Inventory
    /// Management". So: how many of the question's words a person answers
    /// dominates; among equals, how much of the matched tag the question
    /// covers ("bike repair" is half about bikes, "Bike Inventory
    /// Management" a third); and strength — the enrichment's confidence in
    /// its own extraction, not ours in the person — only settles what is
    /// left.
    func searchPeople(terms: [String], limit: Int = 4) -> [PersonSkill] {
        guard !terms.isEmpty else { return [] }
        struct Tally {
            var name: String
            var topics: [String] = []
            var known = ""
            var matched = 0        // question words this person answers
            var coverage = 0.0     // how much of the matched tags that is
            var strength = 0.0     // enrichment confidence, last tie-break
        }
        var scored: [Int64: Tally] = [:]

        // What someone is recorded as knowing, which is the direct answer.
        let sql = """
        SELECT p.id, p.name, e.topic, e.strength
        FROM expertise e JOIN person p ON p.id = e.person_id;
        """
        forEachRow(sql) { stmt in
            let id = sqlite3_column_int64(stmt, 0)
            let topic = text(stmt, 2)
            let lower = topic.lowercased()
            let matched = terms.filter { containsWord(lower, $0) }.count
            guard matched > 0 else { return }
            let weight: Double
            switch text(stmt, 3) {
            case "strong": weight = 3
            case "moderate": weight = 2
            default: weight = 1
            }
            var entry = scored[id] ?? Tally(name: text(stmt, 1))
            entry.topics.append(topic)
            entry.matched += matched
            let topicWords = max(lower.split(separator: " ").count, 1)
            entry.coverage += Double(matched) / Double(topicWords)
            entry.strength += Double(matched) * weight
            scored[id] = entry
        }

        // What they are known for, which often says it in different words.
        // A match here is real relevance and counts as one, but a sentence
        // is not a tag, so it adds nothing to coverage.
        forEachRow("""
        SELECT p.id, p.name, COALESCE(pp.known_for,'')
        FROM person_profile pp JOIN person p ON p.id = pp.person_id;
        """) { stmt in
            let id = sqlite3_column_int64(stmt, 0)
            let known = text(stmt, 2)
            let matched = terms.filter { containsWord(known.lowercased(), $0) }.count
            guard matched > 0 || scored[id] != nil else { return }
            var entry = scored[id] ?? Tally(name: text(stmt, 1))
            entry.known = known
            entry.matched += matched
            entry.strength += Double(matched) * 2
            scored[id] = entry
        }

        return scored.values
            .filter { $0.matched > 0 }
            .sorted {
                if $0.matched != $1.matched { return $0.matched > $1.matched }
                if $0.coverage != $1.coverage { return $0.coverage > $1.coverage }
                return $0.strength > $1.strength
            }
            .prefix(limit)
            .map { PersonSkill(name: $0.name, topics: $0.topics, knownFor: $0.known) }
    }

    /// Every name each enriched person is called, most talkative first.
    ///
    /// `allPeople()` cannot answer this. A camp_member row CLAIMS the chat
    /// person, so the card comes back as "Ozgur Sezer" — and the name the
    /// camp actually says, "Oz", 1,702 messages and a tradition named after
    /// him, is nowhere on it. Before the roster installed on 2026-08-21 the
    /// chat row was returned directly and "Oz" reached the recogniser; the
    /// roster's formal names then masked it, and the journal already records
    /// what that costs — "Oz hole" transcribed as "arsehole" six times.
    ///
    /// Ordered by how much someone talks, because the recogniser only fails
    /// on names it has never seen and the vocabulary budget is 100 terms. A
    /// roster member with no messages is nobody anyone dictates a question
    /// about.
    func speakableNames() -> [[String]] {
        // Same guard as everywhere else: naming person_alias unconditionally
        // makes sqlite3_prepare_v2 FAIL on a database that predates it, which
        // returns NOTHING rather than less.
        let aliasColumn = hasTable("person_alias")
            ? "COALESCE((SELECT group_concat(a.alias, '\n') "
              + "FROM person_alias a WHERE a.person_id = p.id), '')"
            : "''"
        let sql = """
        SELECT p.name, COALESCE(m.name,''), COALESCE(m.nickname,''),
               \(aliasColumn)
        FROM person p
        LEFT JOIN camp_member m     ON m.person_id  = p.id
        LEFT JOIN person_profile pp ON pp.person_id = p.id
        LEFT JOIN personality y     ON y.person_id  = p.id
        WHERE pp.person_id IS NOT NULL OR y.person_id IS NOT NULL
        ORDER BY COALESCE(p.message_count, 0) DESC;
        """
        var out: [[String]] = []
        forEachRow(sql) { stmt in
            var names = [text(stmt, 0), text(stmt, 1), text(stmt, 2)]
            names += text(stmt, 3).split(separator: "\n").map(String.init)
            let cleaned = names
                .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                .filter { !$0.isEmpty }
            if !cleaned.isEmpty { out.append(cleaned) }
        }
        return out
    }

    /// The person a question names, with the card rows behind them.
    ///
    /// Matching is by name — chat name, roster name, or nickname, whole word,
    /// same rule as everything else — and only people with something written
    /// about them qualify, because a matched name with empty rows behind it
    /// would put a blank section on the fact sheet. One portrait per
    /// question: the best-matched person, ties going to whoever wrote more,
    /// since the portrait was drawn from their writing.
    ///
    /// `@MainActor` because it reads `Experiments.shared` -- every real call
    /// site (`Retrieval.answer`) is already on the main actor.
    @MainActor
    func portrait(terms: [String]) -> PersonPortrait? {
        guard !terms.isEmpty else { return nil }
        struct Named { var name = ""; var matched = 0; var matchedTerms: Set<String> = []
                       var messages = 0 }
        var named: [Int64: Named] = [:]
        // The LEFT JOIN to camp_member can fan out when the roster matcher
        // linked more than one signup row; the dictionary folds those back
        // to one person, keeping the best match.
        // Every way the camp refers to this person, so a question can use the
        // name people say rather than the one the corpus wrote down: "Cece"
        // reaches Cece Garland, "Saami" reaches Saami Khoury.
        //
        // Conditional because a database built before person_alias existed is
        // still a valid database, and naming a missing table makes
        // sqlite3_prepare_v2 FAIL -- which here returns no portraits at all
        // rather than portraits without aliases. That is a regression, not a
        // degradation, and it is how searchLore and searchGeneral already
        // guard themselves. Four tests caught it.
        let aliasColumn = hasTable("person_alias")
            ? "COALESCE((SELECT group_concat(a.alias, ' ') "
              + "FROM person_alias a WHERE a.person_id = p.id), '')"
            : "''"
        let sql = """
        SELECT p.id, p.name, COALESCE(m.name,''), COALESCE(m.nickname,''),
               COALESCE(p.message_count,0),
               \(aliasColumn)
        FROM person p
        LEFT JOIN camp_member m     ON m.person_id  = p.id
        LEFT JOIN person_profile pp ON pp.person_id = p.id
        LEFT JOIN personality y     ON y.person_id  = p.id
        WHERE pp.person_id IS NOT NULL OR y.person_id IS NOT NULL;
        """
        forEachRow(sql) { stmt in
            let hay = [text(stmt, 1), text(stmt, 2), text(stmt, 3), text(stmt, 5)]
                .joined(separator: " ").lowercased()
            // Which terms named this person, not merely how many -- gating
            // below needs to know what is LEFT of the question once the name
            // is subtracted, and a bare count throws that away.
            let hits = terms.filter { containsWord(hay, $0) }
            guard !hits.isEmpty else { return }
            let id = sqlite3_column_int64(stmt, 0)
            var entry = named[id] ?? Named()
            entry.name = cleanName(text(stmt, 1))
            entry.matched = max(entry.matched, hits.count)
            entry.matchedTerms.formUnion(hits)
            entry.messages = Int(sqlite3_column_int(stmt, 4))
            named[id] = entry
        }
        let ranked = named.sorted {
            if $0.value.matched != $1.value.matched {
                return $0.value.matched > $1.value.matched
            }
            return $0.value.messages > $1.value.messages
        }
        guard let best = ranked.first else { return nil }
        // Two named people is a question about the pair, so the rows linking
        // them lead the portrait -- and that question stays full below
        // regardless of what else the question asked, because "do Marcus and
        // Sammy like each other" genuinely needs those rows.
        let second = ranked.dropFirst().first
        let pair = second.map { pairLines(best.key, $0.key) } ?? []

        var role = "", persona = "", cares = "", quote = ""

        var shows = "", voice = ""
        forEachRow("""
        SELECT COALESCE(pp.summary,''), COALESCE(y.summary,''),
               COALESCE(y.cares_about,''), COALESCE(y.signature_quote,''),
               COALESCE(y.shows_up_as,''), COALESCE(y.voice,'')
        FROM person p
        LEFT JOIN person_profile pp ON pp.person_id = p.id
        LEFT JOIN personality y     ON y.person_id  = p.id
        WHERE p.id = \(best.key);
        """) { stmt in
            role = text(stmt, 0); persona = text(stmt, 1)
            cares = text(stmt, 2); quote = text(stmt, 3)
            shows = text(stmt, 4); voice = text(stmt, 5)
        }
        let relations = relationLines(of: best.key)
        let known = knownFor(of: best.key)
        let skills = expertiseLines(of: best.key)
        let stories = storiesAbout(best.value.name)
        // The join guaranteed a profile or portrait row exists; it did not
        // guarantee any of them says anything.
        guard !role.isEmpty || !persona.isEmpty || !quote.isEmpty
                || !shows.isEmpty || !voice.isEmpty || !known.isEmpty
                || !skills.isEmpty || !stories.isEmpty
                || !relations.isEmpty || !pair.isEmpty else { return nil }

        // Subtract the terms that matched this person's name from the
        // question. Nothing left means the question WAS the name -- "who is
        // Piotr" -- and today's full card is the right answer. Something
        // left means the question is about that something -- "why does
        // Piotr not like water" -- and there is no safe middle ground to
        // hand over: `role` (person_profile.summary) IS the biography she
        // was reciting -- for Piotr it is the "manages much of our
        // administration and logistics..." paragraph verbatim, and her bad
        // answer on the phone was a near-verbatim paraphrase of exactly that
        // sentence. There is no shorter identifier here that isn't just the
        // first clause of the same bio, so a leftover question gets nothing
        // from this table at all -- the entity facts and lore carry the
        // actual answer, and the person's own name is already in the
        // question anyway.
        let isPair = second != nil
        let leftover = Set(terms).subtracting(best.value.matchedTerms)
        if Experiments.shared.gatedPortrait && !isPair && !leftover.isEmpty {
            return nil
        }
        return PersonPortrait(name: best.value.name, role: role,
                              persona: persona, showsUpAs: shows, voice: voice,
                              caresAbout: cares, knownFor: known,
                              expertise: skills, stories: stories,
                              signatureQuote: quote, relations: relations,
                              pair: pair)
    }

    /// The one-line identifier, where `role` is four sentences.
    func knownFor(of personID: Int64) -> String {
        var out = ""
        forEachRow("""
            SELECT COALESCE(known_for,'') FROM person_profile
            WHERE person_id = \(personID);
            """) { stmt in out = text(stmt, 0) }
        return out
    }

    /// What they are the person to ask about. Strong ties first, because a
    /// weak one is a topic they touched once.
    func expertiseLines(of personID: Int64, limit: Int = 3) -> [String] {
        var out: [String] = []
        forEachRow("""
            SELECT topic FROM expertise WHERE person_id = \(personID)
            ORDER BY CASE COALESCE(strength,'') WHEN 'strong' THEN 0
                     WHEN 'medium' THEN 1 ELSE 2 END
            LIMIT \(limit);
            """) { stmt in out.append(text(stmt, 0)) }
        return out
    }

    /// The stories this person appears in.
    ///
    /// `lore.people` is a comma-separated list of names, so matching has to
    /// respect the separators: "Ed" must not match "Eden" or "Edd", and a
    /// LIKE '%name%' would match both. Split and compare whole entries.
    ///
    /// Newest first and capped, because a well-known camper appears in a lot
    /// of them and this section is colour on a person's card, not the whole
    /// history of the camp.
    func storiesAbout(_ name: String, limit: Int = 3) -> [String] {
        guard !name.isEmpty else { return [] }
        let wanted = name.lowercased()
        var scored: [(String, Int)] = []
        forEachRow("""
            SELECT title, COALESCE(people,''), COALESCE(year,0) FROM lore
            WHERE lower(people) LIKE '%\(wanted)%';
            """) { stmt in
            let title = text(stmt, 0)
            let people = text(stmt, 1)
                .split(separator: ",")
                .map { $0.trimmingCharacters(in: .whitespaces).lowercased() }
            // Whole entry, or the entry's first word -- the lore lists full
            // names ("Piotr Bartkowski") where the person row may hold only
            // "Piotr", and the reverse happens too.
            let hit = people.contains { entry in
                entry == wanted
                    || entry.split(separator: " ").first.map(String.init) == wanted
                    || wanted.split(separator: " ").first.map(String.init) == entry
            }
            if hit { scored.append((title, Int(sqlite3_column_int(stmt, 2)))) }
        }
        return scored.sorted { $0.1 > $1.1 }.prefix(limit).map(\.0)
    }

    /// Their strongest ties, as plain lines. `relationship` held 1,358 rows
    /// nothing read while "do Marcus and Sammy like each other" found
    /// nothing — the raw material sat in the artifact the whole time.
    private func relationLines(of id: Int64, limit: Int = 3) -> [String] {
        var out: [String] = []
        let sql = """
        SELECT r.kind, r.person_a, pa.name, pb.name
        FROM relationship r
        JOIN person pa ON pa.id = r.person_a
        JOIN person pb ON pb.id = r.person_b
        WHERE r.person_a = \(id) OR r.person_b = \(id)
        ORDER BY r.strength DESC, r.id ASC
        LIMIT \(limit);
        """
        forEachRow(sql) { stmt in
            let isA = sqlite3_column_int64(stmt, 1) == id
            let other = cleanName(isA ? text(stmt, 3) : text(stmt, 2))
            out.append(Self.phrase(kind: text(stmt, 0), subjectIsA: isA,
                                   other: other))
        }
        return out
    }

    /// The rows linking two named people, as whole sentences — the direct
    /// answer to "do X and Y get on".
    private func pairLines(_ a: Int64, _ b: Int64, limit: Int = 3) -> [String] {
        var out: [String] = []
        let sql = """
        SELECT r.kind, pa.name, pb.name
        FROM relationship r
        JOIN person pa ON pa.id = r.person_a
        JOIN person pb ON pb.id = r.person_b
        WHERE (r.person_a = \(a) AND r.person_b = \(b))
           OR (r.person_a = \(b) AND r.person_b = \(a))
        ORDER BY r.strength DESC
        LIMIT \(limit);
        """
        forEachRow(sql) { stmt in
            let subject = cleanName(text(stmt, 1))
            let object = cleanName(text(stmt, 2))
            out.append("\(subject) \(Self.phrase(kind: text(stmt, 0), subjectIsA: true, other: object))")
        }
        return out
    }

    /// relationship.kind, said the way a campmate would say it. "mentors" is
    /// the only directional one; an unknown kind falls back to its own words
    /// rather than being dropped, because a new kind from a new enrichment
    /// run should degrade to awkward, not to silence.
    private static func phrase(kind: String, subjectIsA: Bool, other: String) -> String {
        switch kind {
        case "builds_with": return "builds with \(other)"
        case "co_shift":    return "shares shifts with \(other)"
        case "chapter":     return "same chapter as \(other)"
        case "mentors":     return subjectIsA ? "mentors \(other)"
                                              : "learned from \(other)"
        default: return "\(kind.replacingOccurrences(of: "_", with: " ")) \(other)"
        }
    }

    /// The messages a portrait was drawn from, so a claim about someone can be
    /// checked against their own words rather than taken on trust.
    func portraitEvidence(personID: Int64, limit: Int = 8) -> [String] {
        var out: [String] = []
        let sql = """
        SELECT quote FROM evidence
        WHERE claim_table = 'personality' AND claim_id = \(personID)
        LIMIT \(limit);
        """
        forEachRow(sql) { stmt in out.append(text(stmt, 0)) }
        return out
    }

    private func expertiseByPerson() -> [Int64: [String]] {
        var out: [Int64: [String]] = [:]
        forEachRow("SELECT person_id, topic FROM expertise ORDER BY person_id;") { stmt in
            out[sqlite3_column_int64(stmt, 0), default: []].append(text(stmt, 1))
        }
        return out
    }

    // MARK: - Small helpers

    /// WhatsApp marks a name it guessed — a push name rather than a saved
    /// contact — with a leading tilde. That is an export artefact, not part of
    /// what anyone is called, so it does not belong on screen.
    private func cleanName(_ s: String) -> String {
        let junk = CharacterSet(charactersIn: "~").union(.whitespaces)
        return s.trimmingCharacters(in: junk)
    }

    /// Lowercased, letters and spaces only — enough to spot that a roster row
    /// and a chat row are the same person for the purpose of not listing them
    /// twice.
    private func normalisedName(_ s: String) -> String {
        s.lowercased().unicodeScalars
            .filter { CharacterSet.letters.contains($0) || $0 == " " }
            .map(String.init).joined()
            .trimmingCharacters(in: .whitespaces)
    }

    private func text(_ stmt: OpaquePointer?, _ col: Int32) -> String {
        sqlite3_column_text(stmt, col).map { String(cString: $0) } ?? ""
    }

    private func forEachRow(_ sql: String, _ body: (OpaquePointer?) -> Void) {
        var stmt: OpaquePointer?
        defer { sqlite3_finalize(stmt) }
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return }
        while sqlite3_step(stmt) == SQLITE_ROW { body(stmt) }
    }
}
