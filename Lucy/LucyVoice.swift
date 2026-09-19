import Foundation

// Who Lucy is, and the one rule that makes her safe to talk to.
//
// Lucy is the camp's snail art car. She has been to the burn since 2018, took a
// hole through her roof from a fire in 2019, is registered with the DMV as
// "Lucy S CarGo", and carries the sound system. She is the camp's memory, and
// speaks as herself about the camp -- "the barrels live in Doris", not "we
// store the barrels in Doris". The facts are written in the camp's "we" because
// that is how the camp wrote them down; borrowing it makes her claim to have
// been there. See rule 8 in LucyBrain.prompt.
//
// Her manner: dry, unhurried, practical. Fond of the camp without being
// sentimental about it. Short sentences, because whoever is reading is standing
// in dust with one hand full.
//
// THE RULE
//
// Personality is how she says things. It is never what she claims. Every
// sentence Lucy utters that asserts a fact must come from a row in the
// database, and the receipt stays attached. When she does not know, she says
// so plainly — which is in character, since a snail who has seen ten burns has
// also missed most of what happened at them.
//
// This matters more here than in most apps. On playa nobody can check her.
// A confident wrong answer sends someone across camp for a hammer that is not
// there, and the person who invented it will not be around to correct it.

enum LucyVoice {

    /// She opens by saying what she is and what she is not. Setting the bound up
    /// front is the honest move: people trust a thing that tells them its edges.
    /// Short on purpose. The old greeting explained what she was for across
    /// four lines, which is a thing you read once and scroll past forever
    /// after — and it is read standing in dust with one hand full. She says
    /// hello and gets out of the way; what she knows is discoverable by
    /// asking, which is the whole point of her.
    static let greeting = "Hello lovely. How can I help?"

    /// Her line for something she has been asked to keep. Written as her doing
    /// the remembering, because that is the whole conceit — you hand her a
    /// thing, she holds onto it.
    static func remembered(_ capture: Capture) -> String {
        if let said = capture.text, !said.isEmpty {
            return "Got it. You said: \u{201C}\(said)\u{201D}"
        }
        return "Got it. Photographed, no voice note."
    }

    /// Does this read as an instruction to remember rather than a question?
    /// Deliberately narrow — a false positive opens the camera on someone who
    /// asked a question, which is far more annoying than missing the shortcut.
    static func asksToRemember(_ text: String) -> Bool {
        let t = text.lowercased().trimmingCharacters(in: .whitespacesAndNewlines)
        return t == "remember" || t == "remember this"
            || t.hasPrefix("remember this") || t.hasPrefix("remember that")
            || t.hasPrefix("lucy remember")
    }

    /// What she says when a question finds nothing. Deliberately plain — an
    /// apology or a hedge invites the reader to assume she half-knows.
    static func blank(_ question: String) -> String {
        [
            "I don't know that one.",
            "Nothing in what I've got covers that.",
            "That's not something I've been told.",
        ].randomElement()!
    }

    /// The distinct things the retrieved facts are about. "How do we get water"
    /// reaches drinking water, the grey water tank and refilling jugs — three
    /// different questions wearing one sentence. Naming them lets her ask which
    /// one is meant instead of picking for you.
    static func topics(_ answer: Answer) -> [String] {
        var found: [String] = []
        func add(_ t: String) { if !found.contains(t) { found.append(t) } }

        for hit in answer.hits {
            for fact in hit.facts {
                switch fact.category {
                case "access":   add("getting into \(hit.entity.name)")
                case "contents": add("what is in \(hit.entity.name)")
                case "location": add("where \(hit.entity.name) is")
                case "handling": add("how we handle \(hit.entity.name)")
                case "history":  add("\(hit.entity.name)'s history")
                default: break
                }
            }
        }
        for doc in answer.docs {
            let p = doc.passage.lowercased()
            if p.contains("gray water") || p.contains("grey water") {
                add("the grey water tank")
            }
            if p.contains("permit") { add("permits") }
            if let cat = doc.category, !cat.isEmpty, cat != "general" { add(cat) }
        }
        if !answer.general.isEmpty {
            for g in answer.general where !found.contains(g.topic) { add(g.topic) }
        }
        return found
    }

    /// Document passages are cut from the middle of a file, so they often open
    /// on the tail of an unrelated sentence — "Please don't forget to get a
    /// permit. ## GRAY WATER ...". Dropping the fragment before the first
    /// heading or sentence break stops that leading into her answer.
    private static func clean(_ passage: String) -> String {
        var text = passage
        if let hash = text.range(of: "##") {
            let after = String(text[hash.upperBound...])
            if after.count > 80 { text = after.trimmingCharacters(in: .whitespaces) }
        } else if let stop = text.firstIndex(of: "."),
                  text.distance(from: text.startIndex, to: stop) < 60 {
            text = String(text[text.index(after: stop)...])
                .trimmingCharacters(in: .whitespaces)
        }
        return text
    }

    /// Exactly what the model is allowed to say, as plain lines. Dates are
    /// included because she is expected to quote them, and the source of each
    /// general fact is included so she can tell camp practice from event rules.
    /// What you told her, rendered as its own section.
    ///
    /// Kept strictly apart from the facts and labelled as conversation, because
    /// the invariant is that facts come from the database and nothing else. A
    /// remembered turn is evidence of what was SAID, not of what is TRUE -- she
    /// can be told something wrong, and repeating it back as a camp fact would
    /// be the worst failure this app has.
    static func chatLogSection(recent: [Remembered], recalled: [Remembered]) -> [String] {
        guard !recent.isEmpty || !recalled.isEmpty else { return [] }
        var lines: [String] = [""]
        let when = DateFormatter()
        when.dateFormat = "EEE HH:mm"

        // Long answers are clipped for the prompt, at a word boundary: a
        // mid-word cut ("Black Rock S") reads as damage, and the model
        // reproduces damage faithfully.
        func clip(_ s: String) -> String {
            guard s.count > 180 else { return s }
            let cut = String(s.prefix(180))
            guard let sp = cut.lastIndex(of: " ") else { return cut + " …" }
            return String(cut[..<sp]) + " …"
        }

        if !recalled.isEmpty {
            lines.append("=== WHAT THEY TOLD YOU BEFORE — this is conversation, "
                         + "not a camp record. Use it to understand the question, "
                         + "and say who said it if you repeat it ===")
            for r in recalled {
                lines.append("- [\(when.string(from: r.askedAt))] they asked: \(r.question)")
                lines.append("  you answered: \(clip(r.answer))")
            }
        }
        if !recent.isEmpty {
            lines.append("=== JUST NOW — the last few exchanges, so a follow-up "
                         + "makes sense. Do not repeat an answer you already gave ===")
            for r in recent {
                lines.append("- they asked: \(r.question)")
                lines.append("  you answered: \(clip(r.answer))")
            }
        }
        return lines
    }

    /// `@MainActor` because it reads `Experiments.shared.register` (and now
    /// `.rankByRelevance`) -- every real call site (`ChatView.ask`) is
    /// already on the main actor, the same reasoning as `PeopleStore.portrait`.
    ///
    /// Dispatches on the lab flag rather than branching inside one function:
    /// `legacyFactSheet` is kept as the untouched original assembly so
    /// turning the experiment off is provably the old behaviour, not a
    /// reimplementation of it that happens to agree today.
    @MainActor
    static func factSheet(_ answer: Answer) -> String {
        Experiments.shared.rankByRelevance ? rankedFactSheet(answer) : legacyFactSheet(answer)
    }

    // MARK: - Section content
    //
    // What each source contributes, with no header baked in -- the header
    // depends on whether this section ends up leading the sheet, which only
    // the assembly functions below know. Shared by both `legacyFactSheet` and
    // `rankedFactSheet` so the facts themselves can never drift between the
    // two paths; only their order and framing can.

    private static func campLines(_ answer: Answer) -> [String] {
        var lines: [String] = []
        for fact in answer.camp {
            let where_ = fact.sourceTitle.isEmpty ? "" : " [from our \(fact.sourceTitle)]"
            let when = fact.year.map { " (\($0))" } ?? ""
            lines.append("- \(fact.fact)\(when)\(where_)")
        }
        if answer.hits.isEmpty && answer.docs.isEmpty && answer.camp.isEmpty {
            lines.append("(nothing — we have not written this down)")
        }
        return lines
    }

    private static func docsLines(_ answer: Answer) -> [String] {
        var lines: [String] = []
        for doc in answer.docs {
            lines.append("Our \u{201C}\(doc.title)\u{201D} says:")
            lines.append("- \(clean(doc.passage))")
        }
        return lines
    }

    private static func entitiesLines(_ answer: Answer) -> [String] {
        var lines: [String] = []
        for hit in answer.hits {
            lines.append("About \(hit.entity.name), our \(hit.entity.kind):")
            for fact in hit.facts {
                let when = fact.assertedOn.map { " [said \($0)]" } ?? ""
                lines.append("- \(fact.fact)\(when)")
            }
        }
        return lines
    }

    // Who the question named. Her colour comes from these rows — the
    // portrait, the quote, the ties — never from invention: the quote is
    // quoting, and every line is a row the People tab already shows.
    // `answer.about` is nil, not a stripped-down card, whenever the
    // question was about something other than who this person is --
    // PeopleStore.portrait(terms:) returns nothing in that case rather
    // than a shorter version of the same biography she was reciting.
    // So this header only ever fires for the case it was written for.
    /// The person, in the order that makes her sound like she knows them.
    ///
    /// Character leads and the job trails. `role` is person_profile.summary --
    /// "manages much of our administration and logistics" -- and it used to be
    /// the first line here, which is why an answer about a camper read like a
    /// LinkedIn page. The 2026-08-19 pass documented her reciting exactly that
    /// paragraph back as an answer.
    ///
    /// `voice` and `showsUpAs` were in the database from the first enrichment
    /// run and never reached this function at all: the portrait query did not
    /// select them. They are the two fields that say what somebody is LIKE
    /// rather than what they administer, and `voice` is the only place the
    /// camp's tone survives enrichment -- camp_fact carries no emoji at all,
    /// while a voice line carries "ends with a 😂 or ❤️".
    private static func aboutLines(_ who: PersonPortrait) -> [String] {
        var about = ["", "=== ABOUT \(who.name.uppercased()) — what they are "
                         + "like, from our own chat ==="]
        about += who.pair.map { "- \($0)" }
        if !who.knownFor.isEmpty { about.append("- Known for: \(who.knownFor)") }
        if !who.persona.isEmpty { about.append("- \(who.persona)") }
        if !who.showsUpAs.isEmpty { about.append("- \(who.showsUpAs)") }
        if !who.voice.isEmpty { about.append("- How they write: \(who.voice)") }
        if !who.signatureQuote.isEmpty {
            about.append("- In their own words: \u{201C}\(who.signatureQuote)\u{201D}")
        }
        if !who.caresAbout.isEmpty { about.append("- Cares about: \(who.caresAbout)") }
        if !who.expertise.isEmpty {
            about.append("- Ask them about: " + who.expertise.joined(separator: "; "))
        }
        // The stories they are in. For a camp these ARE the person: nobody
        // remembers who ran the WAP spreadsheet, everybody remembers the red
        // sweater. All 163 lore rows name their cast and none of it reached
        // this card until now, because lore was only ever found by matching
        // words in the question.
        if !who.stories.isEmpty {
            about.append("- Stories we tell about them: "
                         + who.stories.joined(separator: "; "))
        }
        if !who.relations.isEmpty {
            about.append("- " + who.relations.joined(separator: "; "))
        }
        // Last, and only when nothing else described them. What they do is a
        // fact about the camp's org chart; every line above is about the
        // person, and those are what a question about a person wants.
        if !who.role.isEmpty && about.count <= 2 {
            about.append("- \(who.role)")
        }
        return about
    }

    // Who to ask, which is often the real answer. "Who knows how to fix a
    // bike" wants a name, and no amount of facts about bikes is a
    // substitute for one.
    private static func peopleLines(_ answer: Answer) -> [String] {
        guard !answer.people.isEmpty else { return [] }
        var lines: [String] = []
        for person in answer.people {
            var why = person.topics.prefix(3).joined(separator: ", ")
            if why.isEmpty { why = person.knownFor }
            lines.append("- \(person.name): \(why)")
        }
        return lines
    }

    // How many stories, no more — colour must not crowd out operations. The
    // count is the active register's, dry meaning none at all. Each story
    // stays its own mini-section, exactly as before, so the budget below can
    // drop one story without dropping every story.
    private static func loreSections(_ answer: Answer, limit: Int) -> [[String]] {
        answer.lore.prefix(limit).map { story in
            let when = story.year.map { " (\($0))" } ?? ""
            return ["", "A story we tell — \(story.title)\(when): \(story.story)"]
        }
    }

    // MARK: - Assembly: today's fixed order

    /// The original assembly, byte-for-byte: camp always leads under "answer
    /// from these first", then docs, then entities, then about/people/lore/
    /// note/background. This is what `rankByRelevance == false` returns, so
    /// the flag is provably an A/B and not a rewrite that happens to agree.
    @MainActor
    private static func legacyFactSheet(_ answer: Answer) -> String {
        var sections: [[String]] = []

        var camp = ["=== OUR CAMP'S OWN RECORDS — answer from these first ==="]
        camp += campLines(answer)
        sections.append(camp)

        sections.append(docsLines(answer))
        sections.append(entitiesLines(answer))

        if let who = answer.about {
            sections.append(aboutLines(who))
        }

        if !answer.people.isEmpty {
            var people = ["", "=== PEOPLE WHO KNOW ABOUT THIS — name them ==="]
            people += peopleLines(answer)
            sections.append(people)
        }

        sections += loreSections(answer, limit: Experiments.shared.register.loreCount)

        let subjects = topics(answer)
        if subjects.count >= 3 {
            sections.append(["", "=== NOTE: these facts cover several different things — "
                                 + subjects.prefix(4).joined(separator: "; ")
                                 + " — so the question may be asking about any of them ==="])
        }

        if !answer.general.isEmpty {
            var general = ["", "=== BACKGROUND: how Burning Man works generally. "
                               + "Use only to fill a gap our own records leave ==="]
            for g in answer.general {
                general.append("- \(g.fact) [\(g.source)]")
            }
            sections.append(general)
        }

        return spend(sections)
    }

    // MARK: - Assembly: ranked by relevance

    /// How many of the question's distinct terms appear, whole-word, in
    /// `text`. Not each source's own internal score -- camp_fact's
    /// topic-match bonus, the document authority prior, and an entity's
    /// squared fact-overlap are three different formulas on three different
    /// scales, so comparing them directly would just trade one arbitrary
    /// order for another. Term coverage is the one number that means the
    /// same thing everywhere: "matched Piotr and the topic" outscores
    /// "matched water" because 2 > 1, not because of how either number was
    /// built.
    private static func coverage(_ text: String, terms: Set<String>) -> Int {
        guard !terms.isEmpty else { return 0 }
        let lower = text.lowercased()
        var n = 0
        for term in terms where containsWord(lower, term) { n += 1 }
        return n
    }

    /// One of the five camp's-own-record sources, ranked as a unit. `about`
    /// is not ranked separately — it rides with `entities`, exactly where it
    /// has always sat, because it is a person's card, not a sixth source.
    private struct FactSheetGroup {
        let sections: [[String]]
        /// Camp shows a header even with nothing under it, same as today;
        /// every other section only appears when it has something to say.
        let forceVisible: Bool
        let coverage: Int
        /// Today's fixed order, and the tie-break: two sources matching the
        /// question equally well keep the order they've always had, so nothing
        /// reshuffles between turns on a coin flip.
        let index: Int
        let leadHeader: String
        /// nil when this source has never had a section header of its own
        /// (docs, entities, lore) -- it stays headerless unless it leads.
        let plainHeader: String?
    }

    @MainActor
    private static func rankedFactSheet(_ answer: Answer) -> String {
        let terms = Set(answer.terms)
        func coverageOf(_ text: String) -> Int { coverage(text, terms: terms) }

        let camp = campLines(answer)
        let docs = docsLines(answer)
        let entities = entitiesLines(answer)
        let about = answer.about.map(aboutLines) ?? []
        let people = peopleLines(answer)
        let lore = loreSections(answer, limit: Experiments.shared.register.loreCount)

        // E7 changes what "how well did this group match" can mean.
        //
        // A row reached by semantic recall matches few or none of the
        // question's words -- that is the entire point of it. Scored on term
        // coverage alone it lands at 0 or 1, loses the lead header to a group
        // with two lexical matches of junk, and can be evicted whole by
        // `spend()`. Semantic retrieval would work and the phone would behave
        // identically, which is the worst possible outcome to debug.
        //
        // So a close row earns standing in the units this ranker already
        // speaks. The thresholds are the same measurement the floor came from:
        // across 400 real rows the median cosine was 0.12, while genuinely
        // relevant rows sat between 0.37 and 0.54. Two bands, deliberately
        // coarse -- this decides section order, not which row leads.
        func semanticCoverage(_ similarity: Float) -> Int {
            if similarity >= 0.52 { return 2 }
            if similarity >= Float(EntityStore.similarityFloor) { return 1 }
            return 0
        }
        let campCoverage = answer.camp.map {
            max(coverageOf($0.topic + " " + $0.fact), semanticCoverage($0.similarity))
        }.max() ?? 0
        let entityCoverage = answer.hits.map { hit -> Int in
            let perFact = hit.facts.map { coverageOf(hit.entity.name + " " + $0.fact) }
            return perFact.max() ?? coverageOf(hit.entity.name)
        }.max() ?? 0
        let docsCoverage = answer.docs
            .map { coverageOf($0.title + " " + clean($0.passage)) }.max() ?? 0
        let peopleCoverage = answer.people.map {
            coverageOf(([$0.name] + $0.topics + [$0.knownFor]).joined(separator: " "))
        }.max() ?? 0
        let loreCoverage = answer.lore
            .map { coverageOf($0.title + " " + $0.story + " " + $0.people) }.max() ?? 0

        let groups: [FactSheetGroup] = [
            FactSheetGroup(sections: [camp], forceVisible: true, coverage: campCoverage, index: 0,
                           leadHeader: "=== OUR CAMP'S OWN RECORDS — answer from these first ===",
                           plainHeader: "=== OUR CAMP'S OWN RECORDS ==="),
            FactSheetGroup(sections: [docs], forceVisible: false, coverage: docsCoverage, index: 1,
                           leadHeader: "=== OUR OWN DOCUMENTS — answer from these first ===",
                           plainHeader: nil),
            FactSheetGroup(sections: (entities.isEmpty ? [] : [entities])
                                    + (about.isEmpty ? [] : [about]),
                           forceVisible: false, coverage: entityCoverage, index: 2,
                           leadHeader: "=== WHAT WE'VE WRITTEN ABOUT THIS — answer from these first ===",
                           plainHeader: nil),
            FactSheetGroup(sections: [people], forceVisible: false, coverage: peopleCoverage, index: 3,
                           leadHeader: "=== PEOPLE WHO KNOW ABOUT THIS — name them, "
                                       + "answer from these first ===",
                           plainHeader: "=== PEOPLE WHO KNOW ABOUT THIS — name them ==="),
            FactSheetGroup(sections: lore, forceVisible: false, coverage: loreCoverage, index: 4,
                           leadHeader: "=== A STORY WE TELL — answer from these first ===",
                           plainHeader: nil),
        ]

        // Highest coverage first. Ties compare `index`, not insertion order --
        // Array's sort is not documented as stable, and a fact sheet that
        // reshuffles equally-good sections between identical turns would look
        // like a bug even though nothing about the facts changed.
        let ordered = groups.sorted { a, b in
            a.coverage != b.coverage ? a.coverage > b.coverage : a.index < b.index
        }

        var sections: [[String]] = []
        var leadAssigned = false
        for group in ordered {
            let hasContent = group.sections.contains { !$0.isEmpty }
            guard group.forceVisible || hasContent else { continue }
            var pieces = group.sections
            let isLeading = !leadAssigned
            leadAssigned = true
            if let header = isLeading ? group.leadHeader : group.plainHeader {
                pieces[0] = [header] + pieces[0]
            }
            sections += pieces
        }

        // Not ranked -- a passing remark on how many different things the
        // facts touch, and BACKGROUND, which rule 4 keeps last no matter what
        // matched best inside our own records.
        let subjects = topics(answer)
        if subjects.count >= 3 {
            sections.append(["", "=== NOTE: these facts cover several different things — "
                                 + subjects.prefix(4).joined(separator: "; ")
                                 + " — so the question may be asking about any of them ==="])
        }

        if !answer.general.isEmpty {
            var general = ["", "=== BACKGROUND: how Burning Man works generally. "
                               + "Use only to fill a gap our own records leave ==="]
            for g in answer.general {
                general.append("- \(g.fact) [\(g.source)]")
            }
            sections.append(general)
        }

        return spend(sections)
    }

    // MARK: - Budget

    /// Bounded on purpose. Even with a batch big enough to accept it, a fact
    /// sheet that fills the window leaves no room for her answer, and a model
    /// given forty facts writes about all of them. Roughly 4 chars per token,
    /// so 4000 chars is about 1000 tokens of the 4096 window.
    ///
    /// Spent in section order: the old whole-sheet truncate chopped whatever
    /// section happened to land last, mid-line. When a section does not fit,
    /// it is dropped whole and so is everything below it -- a section must
    /// not sneak in on the budget one above it freed by being too big. The
    /// first section is always admitted regardless of its own size (`total >
    /// 0` only trips after something has already been spent), which is what
    /// keeps a lower-relevance section, even an oversized one, from pushing
    /// the highest-relevance section off the sheet: whichever section leads —
    /// by relevance when the flag is on, camp always when it is off — gets in
    /// before the budget can say no.
    private static func spend(_ sections: [[String]]) -> String {
        var lines: [String] = []
        var total = 0
        for section in sections where !section.isEmpty {
            let cost = section.reduce(0) { $0 + $1.count + 1 }
            if total > 0 && total + cost > 4000 { break }
            lines += section
            total += cost
        }
        return lines.joined(separator: "\n")
    }

    /// Answers drawing on all three sources, kept in order of authority: what
    /// we said to each other, then what we wrote down, then how the event
    /// works. She names which is which, because they are different kinds of
    /// claim — the first two the camp can change, the last it cannot.
    static func reply(to question: String, answer: Answer) -> Reply {
        guard !answer.isEmpty else {
            return Reply(text: blank(question), facts: [], entity: nil)
        }

        var parts: [String] = []
        var facts: [EntityFact] = []
        var entity: Entity?

        // Extracted document facts lead. They are the most direct answer we
        // have — "pick up service vouchers at the USS Camp, 8am to 6pm" — and
        // they were invisible here while the model saw them, because only the
        // fact sheet carried them and this path composes its own text.
        for fact in answer.camp.prefix(3) {
            parts.append(fact.fact)
        }

        if !answer.hits.isEmpty {
            let fromCamp = reply(to: question, hits: answer.hits)
            parts.append(fromCamp.text)
            facts = fromCamp.facts
            entity = fromCamp.entity
        }

        if let doc = answer.docs.first {
            let lead = parts.isEmpty ? "Our" : "Our"
            parts.append("\(lead) \(doc.title.lowercased()) says:\n\(doc.passage)")
        }

        // The same substance the fact sheet carries, in plain form — a phone
        // without a model still gets who someone is and the story. Every
        // line is a row; the quote is labelled as theirs because repeating
        // someone's words without saying so would be Lucy claiming them.
        if let who = answer.about {
            var lines: [String] = []
            lines += who.pair
            if !who.role.isEmpty { lines.append(who.role) }
            if !who.persona.isEmpty { lines.append(who.persona) }
            if !who.caresAbout.isEmpty { lines.append("Cares about: \(who.caresAbout)") }
            if !who.signatureQuote.isEmpty {
                lines.append("In their own words: \u{201C}\(who.signatureQuote)\u{201D}")
            }
            if !who.relations.isEmpty {
                lines.append("\(who.name) " + who.relations.joined(separator: "; ") + ".")
            }
            if !lines.isEmpty { parts.append(lines.joined(separator: "\n")) }
        }

        if let story = answer.lore.first {
            let when = story.year.map { " (\($0))" } ?? ""
            parts.append("A story we tell — \(story.title)\(when): \(story.story)")
        }

        if !answer.general.isEmpty {
            // Marked out as general practice rather than camp policy. Someone
            // acting on "we don't pour greywater out" should know whether that
            // is our rule or the event's.
            let general = answer.general.map(\.fact).joined(separator: "\n\n")
            parts.append("On playa generally:\n\(general)")
        }

        return Reply(text: parts.joined(separator: "\n\n"),
                     facts: facts, entity: entity,
                     docs: Array(answer.docs.prefix(1)),
                     general: answer.general)
    }

    /// A reply built from retrieved facts. The prose is Lucy's; the claims are
    /// the database's, and each one keeps its fact so the UI can show evidence.
    static func reply(to question: String, hits: [Hit]) -> Reply {
        guard let best = hits.first else {
            return Reply(text: blank(question), facts: [], entity: nil)
        }

        var lines: [String] = []
        let facts = best.facts

        // A contradiction is the most important thing she can say, so it leads.
        if let clash = contradiction(in: facts) {
            lines.append("Two answers on that, and we never settled it.")
            lines.append(dated(clash.0))
            lines.append("Then, five days later: \(clash.1.fact)"
                         .replacingOccurrences(of: "five days later",
                                               with: gap(clash.0, clash.1)))
            // Nothing else. A padlock code is not an answer to a question
            // about ladders, and padding the reply buries the thing she was
            // asked.
            return Reply(text: lines.joined(separator: "\n\n"),
                         facts: [clash.0, clash.1],
                         entity: best.entity)
        }

        lines.append(facts.first?.fact ?? best.entity.summary)
        for fact in facts.dropFirst().prefix(2) { lines.append(fact.fact) }

        return Reply(text: lines.joined(separator: "\n\n"),
                     facts: Array(facts.prefix(3)),
                     entity: best.entity)
    }

    /// Two facts in the same category whose dates differ — the shape of a
    /// disagreement the camp left open, like the Doris ladders.
    private static func contradiction(in facts: [EntityFact]) -> (EntityFact, EntityFact)? {
        for a in facts {
            for b in facts where a.id < b.id {
                guard a.category != nil, a.category == b.category,
                      let da = a.assertedOn, let db = b.assertedOn, da != db else { continue }
                if disagree(a.fact, b.fact) {
                    return da < db ? (a, b) : (b, a)
                }
            }
        }
        return nil
    }

    /// A cheap negation check. Deliberately conservative: showing two facts that
    /// merely differ is harmless, and missing a real contradiction is not.
    private static func disagree(_ a: String, _ b: String) -> Bool {
        let negatives = ["no ", "not ", "never ", "zero ", "none"]
        let aNeg = negatives.contains { a.lowercased().contains($0) }
        let bNeg = negatives.contains { b.lowercased().contains($0) }
        return aNeg != bNeg
    }

    private static func dated(_ fact: EntityFact) -> String {
        guard let on = fact.assertedOn, let pretty = day(on) else { return fact.fact }
        return "\(pretty): \(fact.fact)"
    }

    /// Day precision, not month. The Doris ladders were counted five days
    /// apart — "Aug 2022, then Aug 2022" tells the reader nothing.
    private static func day(_ iso: String) -> String? {
        let parser = DateFormatter()
        parser.dateFormat = "yyyy-MM-dd"
        guard let date = parser.date(from: iso) else { return nil }
        let out = DateFormatter()
        out.dateFormat = "d MMM yyyy"
        return out.string(from: date)
    }

    /// How much later the second claim was made, in words. Five days apart is a
    /// different kind of disagreement from two years apart, and the reader
    /// should be able to weigh that without doing date arithmetic.
    private static func gap(_ a: EntityFact, _ b: EntityFact) -> String {
        let parser = DateFormatter(); parser.dateFormat = "yyyy-MM-dd"
        guard let da = a.assertedOn.flatMap(parser.date(from:)),
              let db = b.assertedOn.flatMap(parser.date(from:)) else { return "later" }
        let days = Calendar.current.dateComponents([.day], from: da, to: db).day ?? 0
        switch days {
        case ..<1:   return "the same day"
        case 1:      return "the next day"
        case 2...13: return "\(days) days later"
        case 14...60: return "a few weeks later"
        case 61...400: return "months later"
        default:     return "years later"
        }
    }
}

struct Reply {
    let text: String
    let facts: [EntityFact]
    let entity: Entity?
    var docs: [DocHit] = []
    var general: [GeneralFact] = []
}

private extension String {
    var lowercasedFirst: String {
        guard let f = first else { return self }
        return f.lowercased() + dropFirst()
    }
}
