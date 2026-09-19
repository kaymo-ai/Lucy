import Foundation

// One home for on-device experiment flags.
//
// A flag here is a switch to turn a fix OFF for comparison, not a coin toss
// on whether the fix ships. It has to be readable from plain retrieval code
// (`PeopleStore`, `LucyBrain`) that runs nowhere near a View, which is why
// this is backed by `UserDefaults` directly rather than `@AppStorage` --
// that property wrapper only resolves inside a View's body. `ObservableObject`
// stays so the Lab menu can bind a `Toggle` to it the same way any other
// screen binds to a store.

@MainActor
final class Experiments: ObservableObject {
    static let shared = Experiments()

    private static let gatedPortraitKey = "experiment-gated-portrait"
    private static let registerKey = "experiment-register"
    private static let rankByRelevanceKey = "experiment-rank-by-relevance"
    private static let answerLengthKey = "experiment-answer-length"

    /// Gate the person portrait to questions actually ABOUT the person, not
    /// merely questions that happen to NAME them. Off, "why does Piotr not
    /// like water" and "who is Piotr" look identical to `PeopleStore` and
    /// both get his whole admin biography -- which is the bug this flag
    /// exists to let us turn off and compare against. Defaults on: this is
    /// the behaviour believed correct, and the flag is here for an A/B read
    /// of the journal, not because the change is in doubt.
    @Published var gatedPortrait: Bool {
        didSet { UserDefaults.standard.set(gatedPortrait, forKey: Self.gatedPortraitKey) }
    }

    /// Her personality "temperature", picked as one named preset rather than
    /// three independent sliders -- the point is a control the owner can A/B
    /// on the phone, not dozens of dial combinations nobody can tell apart in
    /// a journal. Each case moves three things together: how much lore
    /// `LucyVoice.factSheet` includes, the manner paragraph `LucyBrain.prompt`
    /// writes, and the sampler `LlamaHandle.generate` runs with.
    @Published var register: Register {
        didSet { UserDefaults.standard.set(register.rawValue, forKey: Self.registerKey) }
    }

    /// Order the camp's own-record sections on `LucyVoice.factSheet` by how
    /// much of the question each source's best row actually matched, instead
    /// of always leading with camp_fact regardless of fit. Off restores the
    /// old fixed order -- camp, docs, entities, people, lore, every time --
    /// which is the order that put five camp_fact rows matching only "water"
    /// ahead of the one entity fact that named Piotr, under a header telling
    /// the model to answer from the weaker match first. Defaults on for the
    /// same reason `gatedPortrait` does: this is the believed-correct
    /// behaviour, and the flag is here for an A/B read of the journal, not
    /// because the change is in doubt.
    @Published var rankByRelevance: Bool {
        didSet { UserDefaults.standard.set(rankByRelevance, forKey: Self.rankByRelevanceKey) }
    }

    /// How much she says, independent of `register` (how she says it). The
    /// owner's ask -- "she doesn't always need to give such long answers" --
    /// was a length problem, not a tone problem, and the two dials have to
    /// compose: a dry-terse answer and a loose-terse answer should differ in
    /// manner, not in how short they both are. Defaults to `.conversational`,
    /// the new believed-correct behaviour, so the dial ships changing what
    /// the owner asked to change; `.full` is here so the previous constant-
    /// length behaviour is a menu tap away for the A/B, not a revert.
    @Published var answerLength: AnswerLength {
        didSet { UserDefaults.standard.set(answerLength.rawValue, forKey: Self.answerLengthKey) }
    }

    private init() {
        gatedPortrait = UserDefaults.standard.object(forKey: Self.gatedPortraitKey) as? Bool ?? true
        register = (UserDefaults.standard.string(forKey: Self.registerKey))
            .flatMap(Register.init(rawValue:)) ?? .default_
        rankByRelevance = UserDefaults.standard.object(forKey: Self.rankByRelevanceKey) as? Bool ?? true
        answerLength = Self.resolveAnswerLength(
            UserDefaults.standard.string(forKey: Self.answerLengthKey))
    }

    /// The default-resolution `init` actually runs, pulled out so
    /// `LengthTests` can prove `.conversational` is the fallback by calling
    /// the real code path rather than re-describing it inline.
    static func resolveAnswerLength(_ stored: String?) -> AnswerLength {
        stored.flatMap(AnswerLength.init(rawValue:)) ?? .conversational
    }
}

/// How much she says. Orthogonal to `Register`, which governs how she says it
/// -- `LucyBrain.prompt` composes the two from separate paragraphs, one owned
/// by each dial, so changing one never rewrites the other's contribution.
enum AnswerLength: String, CaseIterable, Hashable {
    case conversational, full, terse

    var displayName: String {
        switch self {
        case .conversational: return "Conversational"
        case .full: return "Full"
        case .terse: return "Terse"
        }
    }

    /// One line for the settings screen, because "Terse" on its own tells the
    /// person choosing nothing. Screen copy only -- it never reaches the model,
    /// so the no-quotable-example rule that governs `LucyBrain.prompt` does not
    /// apply to it.
    var blurb: String {
        switch self {
        case .conversational:
            return "As long as the question needs and no longer. A small "
                 + "question earns a small answer."
        case .full:
            return "Every detail on the fact sheet that bears on the question. "
                 + "What she did before this dial existed."
        case .terse:
            return "The shortest true answer, and nothing carried forward."
        }
    }
}

/// One named preset, moving colour budget, manner wording, and sampler
/// together. `default_` (not `default`, a reserved word) is today's
/// behaviour, unchanged -- it exists in the enum so the menu and the journal
/// have a name for "no experiment active", not because its dials differ from
/// what shipped before this file did.
enum Register: String, CaseIterable, Hashable {
    case dry, default_, warm, loose

    var displayName: String {
        switch self {
        case .dry: return "Dry"
        case .default_: return "Default"
        case .warm: return "Warm"
        case .loose: return "Loose"
        }
    }

    /// One line for the settings screen. Same reasoning as
    /// `AnswerLength.blurb`, and the same limit: screen copy, never prompt.
    var blurb: String {
        switch self {
        case .dry:
            return "No stories, tight sampler. What the records say and "
                 + "nothing around it."
        case .default_:
            return "One story when one fits. What shipped before this dial "
                 + "existed."
        case .warm:
            return "Two stories, and she sounds like she enjoys them."
        case .loose:
            return "Three stories and a hot sampler. Deliberately too much."
        }
    }

    /// DIAL 1 -- how many lore stories `LucyVoice.factSheet` includes. Colour
    /// must not crowd out operations even at the loose end, which is why this
    /// tops out at 3 rather than "all of them".
    var loreCount: Int {
        switch self {
        case .dry: return 0
        case .default_: return 1
        case .warm: return 2
        case .loose: return 3
        }
    }

    /// DIAL 3 -- the sampler's temperature. Capped well under the point
    /// (~1.5) where the 1B model stops producing parseable sentences; past
    /// that the setting is not fun, it is broken.
    var temperature: Float {
        switch self {
        case .dry: return 0.5
        case .default_: return 0.7
        case .warm: return 0.9
        case .loose: return 1.2
        }
    }

    /// DIAL 3 -- the sampler's top-k, moved alongside temperature so the two
    /// read as one "looser" or "tighter" knob rather than fighting each other.
    var topK: Int32 {
        switch self {
        case .dry: return 20
        case .default_: return 40
        case .warm: return 60
        case .loose: return 80
        }
    }
}
