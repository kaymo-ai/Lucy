import SwiftUI

// What the settings screen offers, as data.
//
// `SettingsView` renders these two lists and nothing else — no hand-written
// row per flag. That is the whole reason this file exists separately from the
// view: `SettingsCatalogTests` can then mirror `Experiments` and fail when a
// dial gets added there with no way to reach it, and the test is load-bearing
// rather than decorative, because deleting an entry here really does delete
// the row from the screen.
//
// The enum dials are erased to strings deliberately. `Register` and
// `AnswerLength` are unrelated types with nothing in common but the shape of
// the decision, and one `SettingsChoice` describing both beats two bespoke
// pickers that drift apart.

struct SettingsFlag: Identifiable {
    /// The property name on `Experiments`, spelled exactly. This is what the
    /// coverage test matches against, so a rename that misses this file is a
    /// failing test rather than a dial that quietly stops being reachable.
    let key: String
    let title: String
    let blurb: String
    let binding: @MainActor (Experiments) -> Binding<Bool>

    var id: String { key }
}

struct SettingsChoice: Identifiable {
    struct Option: Identifiable {
        let name: String
        let blurb: String
        var id: String { name }
    }

    /// As `SettingsFlag.key`: the property name on `Experiments`.
    let key: String
    let title: String
    let options: [Option]
    let selected: @MainActor (Experiments) -> String
    let select: @MainActor (Experiments, String) -> Void

    var id: String { key }
}

enum SettingsCatalog {
    /// Booleans, rendered as toggles. Both of these turn a fix OFF for
    /// comparison, which is why the blurbs describe what being on does rather
    /// than naming the bug — the person reading is choosing, not debugging.
    ///
    /// DEBUG only, while the rest of this screen ships. They are A/B switches
    /// for retrieval changes still being judged against the journal, both
    /// default ON because that is the behaviour believed correct — a tester
    /// turning one off is not expressing a preference, they are running an
    /// experiment without knowing it, and the result reaches nobody.
    #if DEBUG
    static let flags: [SettingsFlag] = [
        SettingsFlag(
            key: "gatedPortrait",
            title: "Gate the person card",
            blurb: "Hand her someone's whole record only when the question is "
                 + "about them, not whenever it happens to name them.",
            binding: { e in
                Binding(get: { e.gatedPortrait }, set: { e.gatedPortrait = $0 })
            }),
        SettingsFlag(
            key: "rankByRelevance",
            title: "Rank facts by relevance",
            blurb: "Lead the fact sheet with whichever source matched most of "
                 + "the question, instead of always leading with camp facts.",
            binding: { e in
                Binding(get: { e.rankByRelevance }, set: { e.rankByRelevance = $0 })
            }),
    ]
    #endif

    /// The two dials that shape an answer. They are orthogonal on purpose:
    /// register picks how she says it, length picks how much she says, and a
    /// dry-terse answer differs from a loose-terse one in manner alone.
    static let voice: [SettingsChoice] = [
        SettingsChoice(
            key: "register",
            title: "Register: how she says it",
            options: Register.allCases.map {
                SettingsChoice.Option(name: $0.displayName, blurb: $0.blurb)
            },
            selected: { $0.register.displayName },
            select: { e, name in
                if let r = Register.allCases.first(where: { $0.displayName == name }) {
                    e.register = r
                }
            }),
        SettingsChoice(
            key: "answerLength",
            title: "Length: how much she says",
            options: AnswerLength.allCases.map {
                SettingsChoice.Option(name: $0.displayName, blurb: $0.blurb)
            },
            selected: { $0.answerLength.displayName },
            select: { e, name in
                if let l = AnswerLength.allCases.first(where: { $0.displayName == name }) {
                    e.answerLength = l
                }
            }),
    ]

    /// Every `Experiments` property this screen can reach. The coverage test
    /// compares this against the class itself, and runs in DEBUG, where the
    /// retrieval flags are present -- so the mirror stays complete.
    static var keys: Set<String> {
        #if DEBUG
        return Set(flags.map(\.key)).union(voice.map(\.key))
        #else
        return Set(voice.map(\.key))
        #endif
    }
}
