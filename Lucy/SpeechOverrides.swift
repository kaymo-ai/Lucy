import Foundation
import UIKit

// What the recogniser hears when someone says a name it cannot spell.
//
// CampVocabulary weights the recogniser toward the roster, but weighting
// cannot conjure "Piotr" out of the sound "pee-ott" — on-device dictation
// settles on the nearest English word it knows. (The journal from
// 2026-08-11 has it settling on "Pita", "Pura" and "peanut", four presses
// in a row.) This is the other half of the fix: a per-name table of the
// wrong words it settles on, replaced whole-word and case-insensitively in
// a final transcript before it becomes a question.
//
// Questions only. A voice note is a record of what was said and stays
// verbatim — the note flows never call this.
//
// Add forms conservatively: every entry must be a word nobody would mean
// literally in a question to Lucy. "Peanut" is also misheard for Piotr and
// is NOT here, because one day someone will ask about actual peanuts.

enum SpeechOverrides {
    private static let names: [String: [String]] = [
        "Piotr": ["pita", "pura", "peeot", "peot", "pee ot",
                  "pyotr", "pjotr", "piotre", "petr", "piot",
                  // "Pierce" observed live 2026-08-11 13:33Z; no Pierce in
                  // the camp (roster checked), so the word is his.
                  "pierce", "piers",
                  // "po tate" / "potate" / "poe tate" observed live
                  // 2026-08-19 ("Why does PO Tate water" for "Why does
                  // Piotr hate water"). Nobody asks Lucy about a potate,
                  // so the word is his. "pure" ("Why does pure hate
                  // water", same session) is deliberately NOT mapped —
                  // "is the water pure" is a plausible camp question, the
                  // same reason "peanut" stays out.
                  "po tate", "potate", "poe tate"],
        // The possessive gets mangled as its own single token — "Piotr's"
        // came through as "piots" on the first live try.
        "Piotr's": ["piots", "peeots", "peots", "pierces", "pierce's",
                     // "po tates" observed live 2026-08-19, same session as
                     // the "po tate" family above. Filed here rather than
                     // under "Piotr" bare: it is the s-ending member of the
                     // pair, same as "pierces"/"pierce's" above it, and the
                     // trailing s is the sound of the possessive, not a
                     // plural potate.
                     "po tates"],
        // The tradition, not the insult: nobody asks Lucy what an asshole
        // is, and the journal shows every one of these was a swing at the
        // Oz hole. "k hole" stays unmapped — the khole agenda is its own
        // real thing in the records.
        "Oz hole": ["asshole", "arsehole", "oz whole", "ozzole", "oz all"],
        // The camp's JUUL (Piotr can never find it — a running joke worth
        // asking about) always transcribes as the English word. "jewelry"
        // survives the word boundary; the plural "jewels" is left alone.
        "JUUL": ["jewel", "jewell", "jool", "joule", "chu", "chul", "jewl"],
    ]

    static func apply(to transcript: String) -> String {
        var out = transcript
        for (canonical, heard) in names {
            for wrong in heard {
                let pattern = "\\b" + NSRegularExpression.escapedPattern(for: wrong) + "\\b"
                guard let regex = try? NSRegularExpression(pattern: pattern,
                                                           options: [.caseInsensitive]) else { continue }
                let range = NSRange(out.startIndex..., in: out)
                let corrected = regex.stringByReplacingMatches(in: out, range: range,
                                                               withTemplate: canonical)
                if corrected != out {
                    Journal.write("SPEECH corrected: \(wrong) -> \(canonical)")
                    out = corrected
                }
            }
        }
        return phoneticPass(out)
    }

    // MARK: - Phonetic pass

    // The hand table covers real English words the recogniser reaches for
    // ("pita", "pierce", "jewel") — words a spellchecker would bless, which
    // only a human can safely claim for a name. This pass covers the other
    // kind: inventions like "piotre" or "ozzol" that are not words at all.
    // Any misspelled token close enough to a camp vocabulary term — by
    // consonant skeleton or by edit distance scaled to length — becomes that
    // term. Valid English words are never touched here, so the worst case of
    // a bad match is bounded by the recogniser already having failed.

    /// Injectable for tests; UITextChecker on the device.
    static var isRealWord: (String) -> Bool = { word in
        let checker = UITextChecker()
        let range = NSRange(location: 0, length: word.utf16.count)
        let miss = checker.rangeOfMisspelledWord(in: word, range: range,
                                                 startingAt: 0, wrap: false,
                                                 language: "en_US")
        return miss.location == NSNotFound
    }

    private static func phoneticPass(_ transcript: String) -> String {
        let candidates = CampVocabulary.terms.filter { !$0.contains(" ") }
        guard !candidates.isEmpty else { return transcript }

        var words = transcript.components(separatedBy: " ")
        for (i, raw) in words.enumerated() {
            let token = raw.trimmingCharacters(in: .punctuationCharacters)
            guard token.count > 3,
                  names[token] == nil,                       // already canonical
                  !candidates.contains(where: { $0.caseInsensitiveCompare(token) == .orderedSame }),
                  !isRealWord(token.lowercased())
            else { continue }

            let best = candidates
                .map { (term: $0, d: distance(token.lowercased(), $0.lowercased())) }
                .min { $0.d < $1.d }
            guard let match = best else { continue }

            let closeEnough = match.d <= (token.count <= 5 ? 1 : 2)
                || skeleton(token) == skeleton(match.term)
            guard closeEnough, match.d <= 3 else { continue }

            Journal.write("SPEECH corrected (phonetic): \(token) -> \(match.term)")
            words[i] = raw.replacingOccurrences(of: token, with: match.term)
        }
        return words.joined(separator: " ")
    }

    /// Consonant skeleton: "piotre" and "Piotr" both reduce to "ptr".
    static func skeleton(_ word: String) -> String {
        var out = ""
        for ch in word.lowercased() where ch.isLetter {
            if "aeiouy".contains(ch) { continue }
            if out.last != ch { out.append(ch) }
        }
        return out
    }

    /// Plain Levenshtein; the candidate list is ~100 short words.
    static func distance(_ a: String, _ b: String) -> Int {
        let a = Array(a), b = Array(b)
        var row = Array(0...b.count)
        for i in 1...max(a.count, 1) where !a.isEmpty {
            var prev = row[0]; row[0] = i
            for j in 1...b.count {
                let cur = row[j]
                row[j] = min(row[j] + 1, row[j - 1] + 1,
                             prev + (a[i - 1] == b[j - 1] ? 0 : 1))
                prev = cur
            }
        }
        return a.isEmpty ? b.count : row[b.count]
    }
}
