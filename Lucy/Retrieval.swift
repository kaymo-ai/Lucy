import Foundation

// Finding the right facts for a question.
//
// The old resolver stripped question words and looked for an entity whose name
// matched what was left. That answers "what is Doris" and nothing else — ask
// "what socket do I need for the lag bolts" and it finds nothing, even though
// the answer is sitting in an entity_fact row.
//
// This searches the facts themselves as well as names and aliases, and returns
// the facts rather than an entity, because a question is usually about a thing
// that happened, not about a noun.

/// Everything the question reached, across all three kinds of source.
struct Answer {
    let hits: [Hit]
    let camp: [CampFact]
    let docs: [DocHit]
    let general: [GeneralFact]
    /// Who to ask. "Who knows how to fix a bike" is answered by people, not
    /// by facts, and nothing else in here can reach that.
    let people: [PersonSkill]
    /// Who the question named. "Who is Piotr" is answered by the card the
    /// People tab already shows — profile, portrait, ties — which retrieval
    /// could not reach until it looked.
    let about: PersonPortrait?
    /// A story the camp still tells, when the question brushes one.
    let lore: [LoreHit]
    /// The question's terms, after stopword-stripping and any previous-turn
    /// blend, as searched. Carried along so `LucyVoice.factSheet` can judge
    /// how much of the question each source's best row actually matched,
    /// instead of guessing from which table it came from. Defaults empty so
    /// a hand-built `Answer` in a test keeps compiling; an empty list scores
    /// every source 0, which ties and falls back to the fixed order -- never
    /// a crash, never an invented ranking.
    var terms: [String] = []
    var isEmpty: Bool {
        hits.isEmpty && camp.isEmpty && docs.isEmpty && general.isEmpty
            && people.isEmpty && about == nil && lore.isEmpty
    }
}

struct Hit {
    let entity: Entity
    let facts: [EntityFact]
    /// Higher is better. Only meaningful for ranking within one query.
    let score: Double
}

/// Whole-word containment. Substring matching made "water" match
/// "floodwaters", which pulled a Noah's Ark party into an answer about
/// drinking water — the same bug the Python pipeline had, where the alias "PS"
/// matched 2,953 messages through "perhaps" and "apps".
func containsWord(_ haystack: String, _ word: String) -> Bool {
    guard let r = haystack.range(of: word) else { return false }
    let before = r.lowerBound == haystack.startIndex ? nil
        : haystack[haystack.index(before: r.lowerBound)]
    let after = r.upperBound == haystack.endIndex ? nil : haystack[r.upperBound]
    let boundary: (Character?) -> Bool = { c in
        guard let c else { return true }
        return !c.isLetter && !c.isNumber
    }
    return boundary(before) && boundary(after)
}

enum Retrieval {
    /// Words that appear in nearly every question and so tell us nothing about
    /// which fact is wanted.
    ///
    /// "lucy" was here from the start, on the reasoning that people address
    /// her by name. But Lucy is also the camp's most-mentioned entity — the
    /// bus itself — and stopping her name made every question ABOUT her
    /// return nothing: "what is Lucy" reduced to no terms at all. The evals
    /// caught it (lucy-what, lucy-access, 2026-08-19). The vocative worry is
    /// covered the other way: a question like "lucy how do we get water"
    /// keeps its real subject because entity naming terms never pick facts —
    /// only the rest of the question does (see `distinguishing` below).
    ///
    /// A stoplist is the right tool for a word that carries no signal. Length
    /// is not: the filter here used to drop anything two letters or shorter,
    /// which reads as a harmless heuristic and is not one — "Oz" is two
    /// letters, and that rule deleted a camper's name out of every question
    /// that named him, silently, before this ever became a search term.
    /// Measured against the owner's real journal, six of 71 questions lost
    /// every term this way, "Who is Oz" among them. So/no/up/oh/be/he/us/ok
    /// really are noise and belong here explicitly; "oz" and "dj" do not
    /// ("the best DJ in camp" is a real question) and must not be added.
    private static let stop: Set<String> = [
        "what", "whats", "who", "whos", "where", "wheres", "when", "whens",
        "why", "how", "is", "are", "was", "were", "the", "a", "an", "of", "in",
        "on", "at", "to", "for", "do", "does", "did", "i", "we", "you", "my",
        "our", "it", "its", "and", "or", "with", "about", "tell", "me", "get",
        "got", "need", "any", "some", "there", "have", "has", "can", "should",
        "know",
        "so", "no", "up", "oh", "be", "he", "us", "ok",
    ]

    static func terms(_ query: String) -> [String] {
        query.lowercased()
            .components(separatedBy: CharacterSet.alphanumerics.inverted)
            .filter { $0.count > 1 && !stop.contains($0) }
    }

    /// Ranks entities by how well the question matches their name, aliases and
    /// facts, then returns only the facts that actually matched — asking about
    /// ladders should not return everything known about Doris.
    static func search(_ query: String, store: EntityStore, limit: Int = 3) -> [Hit] {
        let terms = terms(query)
        guard !terms.isEmpty else { return [] }

        var hits: [Hit] = []
        for entity in store.allEntities() {
            let name = entity.name.lowercased()
            let aliases = entity.aliases.map { $0.lowercased() }
            var score = 0.0

            // A question that names a thing is almost always about that thing.
            for term in terms {
                if name == term { score += 12 }
                else if name.contains(term) { score += 6 }
                if aliases.contains(where: { $0 == term }) { score += 8 }
            }

            // The entity's own name appears in most of its own facts, so it
            // ranks everything equally and the reply pads out with whatever
            // happened to be first. Only the rest of the question picks facts.
            let naming = Set([name] + aliases)
            let distinguishing = terms.filter { term in
                !naming.contains(where: { $0.contains(term) })
            }

            let facts = store.facts(entityID: entity.id).values.flatMap { $0 }
            var matched: [(EntityFact, Double)] = []
            for fact in facts {
                let text = fact.fact.lowercased()
                let overlap = distinguishing.filter { containsWord(text, $0) }.count
                if overlap > 0 {
                    // Two matching terms in one fact is a far better signal than
                    // one term matching in two facts.
                    let factScore = Double(overlap * overlap)
                    matched.append((fact, factScore))
                    score += factScore
                }
            }

            guard score > 0 else { continue }
            // Facts that matched come first; if the question only named the
            // entity, fall back to its most recent facts.
            let ordered: [EntityFact] = matched.isEmpty
                ? Array(facts.sorted { ($0.assertedOn ?? "") > ($1.assertedOn ?? "") }.prefix(4))
                : matched.sorted { $0.1 > $1.1 }.map(\.0)
            hits.append(Hit(entity: entity, facts: Array(ordered.prefix(5)), score: score))
        }

        return Array(hits.sorted { $0.score > $1.score }.prefix(limit))
    }

    /// The full sweep: what the camp said, what the camp wrote down, and how
    /// the event works. Three different kinds of authority, kept apart so Lucy
    /// can say which one she is speaking from.
    /// `previous` is the question before this one, used only when this one is
    /// too thin to retrieve on.
    ///
    /// "You do, it's in the camp manual" reduces to "camp" and "manual" — the
    /// subject is in the previous turn and nowhere in this one, so retrieval
    /// searched for the wrong thing and she answered "I don't know" while
    /// holding the answer. Blending is deliberately conditional: a question
    /// that stands on its own must not be contaminated by the last one, which
    /// is the failure mode that makes chat assistants answer the question you
    /// asked a minute ago.
    @MainActor
    static func answer(_ query: String, store: EntityStore,
                       previous: String? = nil) -> Answer {
        var t = terms(query)
        if t.count < 2, let previous {
            let carried = terms(previous).filter { !t.contains($0) }
            t += carried
        }
        // What the camp answered comes first, ahead of what shipped.
        //
        // Someone typed these BECAUSE she got it wrong or had nothing, so a
        // shipped row that was already losing should not now outrank the
        // correction. They are still ordinary CampFact rows -- retrieval
        // supplies the fact and the model phrases it, exactly as before. The
        // only thing that changed is where some rows came from.
        // A term that is somebody's other name contributes their real one.
        //
        // The corpus writes "Piotr" and dictation produces "Peet"; the corpus
        // writes "Cece Garland" and the camp says "Cece". Widening the question
        // here means every layer below matches exactly as it always has,
        // instead of each search growing its own idea of what a name is.
        //
        // Added, never substituted: the spoken word stays in the list, because
        // it may be right and an alias table is a guess about people, not a
        // correction of them.
        for name in store.canonicalNames(matching: t) {
            for token in terms(name) where !t.contains(token) {
                t.append(token)
            }
        }

        // E7. Encoded once per turn from the ORIGINAL question, not from the
        // stripped terms: the stoplist exists to stop "what"/"is"/"the"
        // dominating a word-count, and an encoder wants the sentence. Nil
        // whenever the encoder is not on this phone, which leaves every
        // search below exactly as it was.
        let queryVector = Embedder.shared?.embed(query)
        let answered = AnswerStore.shared.search(terms: t)
        let camp = answered + store.searchCampFacts(terms: t,
                                                    queryVector: queryVector)
        // Documents are always searched, not only when nothing else matched.
        //
        // They used to be a fallback, on the reasoning that an extracted fact
        // beats the passage it came from. That holds fact-for-fact and fails
        // in aggregate: "how do I get into Empire storage" pulled twelve
        // camp facts about packing Doris during strike, which is enough to
        // count as "something matched", and so the manual was never opened —
        // even though its QUICK START section, the first thing in it, says
        // where the lot is and where the key lives.
        //
        // Any facts at all suppressing the whole document layer is too blunt.
        // Retrieval offers both and the answer comes from whichever actually
        // addresses the question.
        // The blended terms, not the raw question — otherwise entity matching
        // is the one layer that still cannot see what the follow-up is about.
        return Answer(hits: search(t.joined(separator: " "), store: store),
                      camp: camp,
                      docs: store.searchDocs(terms: t),
                      general: store.searchGeneral(terms: t),
                      people: store.searchPeople(terms: t),
                      about: store.portrait(terms: t),
                      // Default limit is 2; loose asks LucyVoice.factSheet
                      // for up to 3 stories, so retrieval has to fetch enough
                      // for it to pick from. Scoring inside searchLore is
                      // untouched -- this only widens how many of its
                      // already-ranked results come back.
                      lore: store.searchLore(terms: t, queryVector: queryVector,
                                            limit: 3),
                      terms: t)
    }
}
