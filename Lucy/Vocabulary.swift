import Foundation

// The names the recogniser has never heard of.
//
// On-device dictation works from a general English vocabulary, so "Ozgur",
// "Bartkowski" and "Shiftpod" come out as whatever ordinary words they sound
// closest to — and a question about a person is mostly that person's name, so
// getting it wrong loses the whole question.
//
// `contextualStrings` is the hook for this: a list of phrases the recogniser
// should weight toward. Apple recommends keeping it around a hundred, so this
// is a prioritised selection rather than the whole roster: the things the camp
// talks about, then the people most likely to be mentioned — anyone on a
// recent sheet or with enough chat presence to have a portrait.
//
// Loaded once. It reads the same database the app already opens.

enum CampVocabulary {
    /// At most this many phrases. Beyond roughly a hundred, weighting them all
    /// dilutes each one.
    private static let limit = 100

    /// Reserved for the camp's own things, so people cannot consume the lot.
    private static let thingsBudget = 25

    static let terms: [String] = load()

    private static func load() -> [String] {
        guard let store = EntityStore(path: PreviewRoot.fixturePath) else { return [] }
        var out: [String] = []
        var seen = Set<String>()

        func add(_ raw: String) {
            let s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
            // A SINGLE letter is noise -- an initial matches everything. Two
            // is not, and the difference is a person.
            //
            // This guard read `> 2` until 2026-08-20, which is the exact rule
            // docs/learnings-lucy-2026-08-19.md was written about: "oz" is two
            // characters, and a length filter cannot tell a filler word from a
            // name. That was fixed in Retrieval.terms and missed here, so Oz
            // -- who has a camp tradition named after him -- was never handed
            // to the recogniser at all. The journal shows what it cost: "Oz
            // hole" came back as "arsehole" six times, plus "Is in Oz hole",
            // "No in Oz hole" and "Oh in Oz hole". A question about a person
            // is mostly that person's name, so losing the name loses the
            // question before retrieval ever sees it.
            guard s.count > 1, out.count < limit else { return }
            let key = s.lowercased()
            guard !seen.contains(key) else { return }
            seen.insert(key)
            out.append(s)
        }

        // People first, and by a long way.
        //
        // The obvious order — things, then people — spent the entire budget
        // on entities before a single name got in, and the entity list is
        // alphabetical and full of lore: "36hr Acid Hookah Sunset Cruise",
        // "Book of Faces", "@psatbm". Nobody dictates those. Names are what
        // the recogniser actually fails on.
        //
        // A nickname goes in beside the name because it is often what someone
        // is really called, and the given name alone because "where is Opal"
        // is asked far more than the full name.
        // BY HOW MUCH THEY TALK, not alphabetically.
        //
        // allPeople() walks camp_member first, ORDER BY name, which is right
        // for the People screen and wrong here. That table went from 0 rows
        // to 291 on 2026-08-21 when the roster finally installed, and an
        // alphabetical walk spent the whole 75-name budget somewhere around
        // G. Oz -- two letters, a camp tradition named after him, one of the
        // most referenced people in the corpus -- stopped reaching the
        // recogniser, and VocabularyTests caught it.
        //
        // The budget is small and the recogniser only fails on names it has
        // never seen, so it should be spent on the people who actually appear
        // in the chat. A roster member with no messages is someone nobody
        // dictates a question about.
        for names in store.speakableNames() {
            if out.count >= limit - thingsBudget { break }
            for name in names {
                add(name)
                // The given name alone: "where is Opal" is asked far more
                // often than the full name.
                if let first = name.split(separator: " ").first {
                    add(String(first))
                }
            }
        }

        // Then the OTHER spellings of those same people.
        //
        // After the canonical names rather than beside them, because if the
        // budget runs out it should run out on a variant rather than on
        // somebody's actual name. These are what the corpus itself used
        // before dedupe folded them together -- "CeCe", "CeCe Garlan",
        // "Saami" -- plus first and last names split off full ones. The
        // recogniser fails on exactly these: a question about a person is
        // mostly that person's name, and the name somebody says is often not
        // the one the corpus wrote down.
        for alias in store.personAliases() {
            if out.count >= limit - thingsBudget { break }
            add(alias)
        }

        // Then the things the camp owns and maintains — Doris, Boris, the
        // yurts, the swamp cooler. Only kinds that name an object: traditions
        // are parties and running jokes, and they are what filled the list
        // with noise the first time.
        let useful: Set<String> = ["vehicle", "structure", "tool", "place", "asset"]
        for entity in store.allEntities() where useful.contains(entity.kind.lowercased()) {
            // Nothing with punctuation or digits in it: those are event names
            // and handles, not words anyone says to a phone.
            guard entity.name.allSatisfy({ $0.isLetter || $0.isWhitespace }) else { continue }
            add(entity.name)
        }
        return out
    }
}
