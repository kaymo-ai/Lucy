import XCTest
@testable import Lucy

// The register experiment: one named preset (`Experiments.shared.register`)
// moving three dials together -- lore count, manner wording, sampler. These
// tests cover what is checkable without a model loaded: the dial values
// themselves, that the setting persists the way `gatedPortrait` does, and
// that DIAL 2's manner paragraphs obey the no-quoted-example rule and that
// `default_` is pinned to what shipped before this file existed.
@MainActor
final class RegisterTests: XCTestCase {
    override func setUp() {
        Experiments.shared.register = .default_
    }

    override func tearDown() {
        Experiments.shared.register = .default_
    }

    // MARK: - Dial values (DIAL 1 lore count, DIAL 3 sampler)

    func testDryDials() {
        XCTAssertEqual(Register.dry.loreCount, 0)
        XCTAssertEqual(Register.dry.temperature, 0.5)
        XCTAssertEqual(Register.dry.topK, 20)
    }

    func testDefaultDials() {
        XCTAssertEqual(Register.default_.loreCount, 1)
        XCTAssertEqual(Register.default_.temperature, 0.7)
        XCTAssertEqual(Register.default_.topK, 40)
    }

    func testWarmDials() {
        XCTAssertEqual(Register.warm.loreCount, 2)
        XCTAssertEqual(Register.warm.temperature, 0.9)
        XCTAssertEqual(Register.warm.topK, 60)
    }

    func testLooseDials() {
        XCTAssertEqual(Register.loose.loreCount, 3)
        XCTAssertEqual(Register.loose.temperature, 1.2)
        XCTAssertEqual(Register.loose.topK, 80)
    }

    // Past ~1.5 a 1B model stops producing parseable sentences, which makes
    // the setting useless rather than fun -- pinned so a future preset can't
    // drift past that ceiling by accident.
    func testNoRegisterExceedsTheTemperatureCeiling() {
        for register in Register.allCases {
            XCTAssertLessThanOrEqual(register.temperature, 1.5)
        }
    }

    // The actual wiring, not just the enum's own data: `LucyVoice.factSheet`
    // has to read `Experiments.shared.register` and slice `answer.lore` by
    // its `loreCount`, and this is the path that would stay green if that
    // wiring were ever deleted while the dial-value tests above kept passing.
    func testFactSheetLoreCountFollowsTheActiveRegister() {
        let stories = (1...3).map { i in
            LoreHit(id: Int64(i), title: "Story \(i)", story: "It happened.",
                   year: nil, people: "")
        }
        let answer = Answer(hits: [], camp: [], docs: [], general: [],
                            people: [], about: nil, lore: stories)

        func storyCount(in text: String) -> Int {
            text.components(separatedBy: "A story we tell —").count - 1
        }

        Experiments.shared.register = .dry
        XCTAssertEqual(storyCount(in: LucyVoice.factSheet(answer)), 0)

        Experiments.shared.register = .default_
        XCTAssertEqual(storyCount(in: LucyVoice.factSheet(answer)), 1)

        Experiments.shared.register = .warm
        XCTAssertEqual(storyCount(in: LucyVoice.factSheet(answer)), 2)

        Experiments.shared.register = .loose
        XCTAssertEqual(storyCount(in: LucyVoice.factSheet(answer)), 3)
    }

    // MARK: - Persistence, same contract as `gatedPortrait`

    func testRegisterPersistsAcrossReads() {
        Experiments.shared.register = .loose
        // Experiments is a singleton, so "another read" is simulated the
        // same way its own init() reads -- straight out of UserDefaults,
        // under the key the didSet is supposed to have written to.
        let stored = UserDefaults.standard.string(forKey: "experiment-register")
        XCTAssertEqual(stored, "loose")
        XCTAssertEqual(Register(rawValue: stored ?? ""), .loose)
        // And the live object agrees with what got persisted.
        XCTAssertEqual(Experiments.shared.register, .loose)
    }

    // MARK: - Manner paragraph (DIAL 2)

    func testMannerParagraphDiffersBetweenRegisters() {
        let manners = Set(Register.allCases.map { LucyBrain.manner(for: $0) })
        XCTAssertEqual(manners.count, Register.allCases.count,
                       "two registers produced the same manner paragraph")
    }

    // The failure mode this guards: a rule that illustrates itself with a
    // camp-flavoured example sentence -- "the barrels live in Doris" -- gets
    // emitted verbatim as an answer. No manner paragraph, in any register,
    // may carry a quoted example. Absence of quote characters is the cheap
    // proxy: a paragraph describing tone in prose has no reason to open one.
    func testNoMannerParagraphContainsAQuotedExample() {
        for register in Register.allCases {
            let text = LucyBrain.manner(for: register)
            XCTAssertFalse(text.contains("\""),
                           "\(register) manner paragraph has a straight quote")
            XCTAssertFalse(text.contains("\u{201C}"),
                           "\(register) manner paragraph has a curly open quote")
            XCTAssertFalse(text.contains("\u{201D}"),
                           "\(register) manner paragraph has a curly close quote")
        }
    }

    // "default" only means "today's shipped behaviour, unchanged" if it
    // stays pinned. This used to be a byte-identical pin of what shipped
    // before the register experiment existed, but the length dial
    // (`LengthTests`) moved every fullness claim -- "weave every detail
    // that bears on the question into one telling", "a full answer" -- out
    // of this paragraph and into `LucyBrain.lengthGuidance(.full)`, since
    // those were claims about HOW MUCH to say, not about voice. What's
    // pinned now is what's left: voice only.
    func testDefaultRegisterMannerIsUnchanged() {
        let expected = """
        Your manner: warm, quick, a bit playful — a giant snail with a sound \
        system, not a reference desk. Dry humour welcome; be delighted by the \
        camp. Short sentences. Start with the answer, never with the \
        question restated.
        """
        XCTAssertEqual(LucyBrain.manner(for: .default_), expected)
    }

    // The assembled prompt still carries rules 1-5 untouched and now also
    // carries whichever manner paragraph the active register picked --
    // catches a wiring mistake where `prompt` ignores the register entirely.
    func testPromptCarriesTheActiveRegistersManner() {
        Experiments.shared.register = .dry
        let dryPrompt = LucyBrain.prompt(question: "where is the medkit", context: "")
        XCTAssertTrue(dryPrompt.contains(LucyBrain.manner(for: .dry)))
        XCTAssertFalse(dryPrompt.contains(LucyBrain.manner(for: .loose)))
        XCTAssertTrue(dryPrompt.contains("RULES, in order"))
    }
}
