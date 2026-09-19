import XCTest
@testable import Lucy

// The length experiment: `Experiments.shared.answerLength` picks how much
// she says, independent of `register` (how she says it). The owner's ask --
// "she doesn't always need to give such long answers" -- was that length was
// a constant, not that the tone was wrong, so this dial only ever touches the
// paragraph `LucyBrain.lengthGuidance(for:)` produces. It never rewrites
// rules 1-5, the persona line, or a word of `manner`.
//
// The two dials were only ever STRUCTURALLY orthogonal at first: every
// `manner` variant separately argued for fullness ("weave every detail...
// into one telling"), which fought `.conversational` at the shipping
// default. That argument has since moved out of `manner` entirely and into
// `lengthGuidance(.full)`, so `.full` is no longer the empty string --
// `RegisterTests` now pins `manner(.default_)` as voice only, and the tests
// below pin the fullness claims as living in `.full`'s guidance instead.
//
// Same structure as `RegisterTests`: dial values/persistence, the
// no-quoted-example rule on every variant, and that `register` and
// `answerLength` compose without either touching the other's contribution --
// checked against the ASSEMBLED prompt, not by comparing the two pure
// functions to themselves, since a swap or leak only shows up once the two
// paragraphs are actually sitting next to each other.
@MainActor
final class LengthTests: XCTestCase {
    override func setUp() {
        Experiments.shared.answerLength = .conversational
        Experiments.shared.register = .default_
    }

    override func tearDown() {
        Experiments.shared.answerLength = .conversational
        Experiments.shared.register = .default_
    }

    // MARK: - Dial identity

    func testAllThreeLengthsExist() {
        XCTAssertEqual(Set(AnswerLength.allCases), [.conversational, .full, .terse])
    }

    // Calls the actual function `Experiments.init` calls to resolve an unset
    // default, not a re-description of it inline -- a version of this test
    // that re-implements the `?? .conversational` fallback locally would
    // still pass if `init` itself defaulted to something else.
    func testConversationalIsTheDefault() {
        XCTAssertEqual(Experiments.resolveAnswerLength(nil), .conversational)
        XCTAssertEqual(Experiments.resolveAnswerLength("not-a-real-case"), .conversational)
    }

    // MARK: - Persistence, same contract as `register`

    func testAnswerLengthPersistsAcrossReads() {
        Experiments.shared.answerLength = .terse
        let stored = UserDefaults.standard.string(forKey: "experiment-answer-length")
        XCTAssertEqual(stored, "terse")
        XCTAssertEqual(AnswerLength(rawValue: stored ?? ""), .terse)
        XCTAssertEqual(Experiments.shared.answerLength, .terse)
    }

    // MARK: - Length paragraph (DIAL 4)

    func testEachLengthYieldsADifferentPrompt() {
        let prompts = Set(AnswerLength.allCases.map { length -> String in
            Experiments.shared.answerLength = length
            return LucyBrain.prompt(question: "where is the medkit", context: "")
        })
        XCTAssertEqual(prompts.count, AnswerLength.allCases.count,
                       "two length settings produced the same prompt")
    }

    // Same failure mode `RegisterTests` guards against: a rule that
    // illustrates itself with a camp-flavoured example gets emitted verbatim
    // as an answer. No length variant may carry a quoted example.
    func testNoLengthGuidanceContainsAQuotedExample() {
        for length in AnswerLength.allCases {
            let text = LucyBrain.lengthGuidance(for: length)
            XCTAssertFalse(text.contains("\""),
                           "\(length) length guidance has a straight quote")
            XCTAssertFalse(text.contains("\u{201C}"),
                           "\(length) length guidance has a curly open quote")
            XCTAssertFalse(text.contains("\u{201D}"),
                           "\(length) length guidance has a curly close quote")
        }
    }

    // `.full` used to be the empty string, back when every `manner` variant
    // already argued for fullness on its own. That argument has moved out
    // of `manner` and into this paragraph instead (see the compose test
    // below), so `.full` is no longer empty -- it is the one carrying the
    // claim now.
    func testFullLengthGuidanceIsNotEmpty() {
        XCTAssertFalse(LucyBrain.lengthGuidance(for: .full).isEmpty)
    }

    func testAllThreeLengthGuidancesAreNonEmpty() {
        for length in AnswerLength.allCases {
            XCTAssertFalse(LucyBrain.lengthGuidance(for: length).isEmpty,
                          "\(length) length guidance is empty")
        }
    }

    // MARK: - `.full` carries the fullness claims that used to live in `manner`

    // The four registers used to each argue for fullness on their own --
    // `default_` had "weave every detail that bears on the question into
    // one telling, with its names and dates"; `warm` had "woven through a
    // full answer that still carries every detail the question needs";
    // `loose` had its own "full answer underneath the noise" line; `dry`
    // pulled the opposite way with "in the fewest words". All of that has
    // moved out of `manner` -- which is voice only now -- and collapsed
    // into this one `.full` paragraph, so it should exist exactly once
    // instead of once per register.
    //
    // This is the pin that replaces the old byte-identical prompt capture:
    // that pin broke on contact, because the text moved paragraphs, not
    // because it regressed. What actually has to hold is the SUBSTANCE --
    // every fullness claim still reachable somewhere in the assembled
    // prompt for `.full`, and none of it leaking into `.conversational`,
    // the shipping default the owner asked for. Checked on the ASSEMBLED
    // prompt (`LucyBrain.prompt`, not `lengthGuidance` in isolation) so a
    // wiring mistake that drops `lengthBlock` from `prompt()` would also
    // be caught here.
    private static let fullnessClaims = [
        "weave every detail that bears on the question into one telling",
        "with its names and dates",
        "every detail the question needs",
        "Prefer that longer answer to a clipped one",
    ]

    func testFullLengthCarriesEveryFullnessClaim() {
        for register in Register.allCases {
            Experiments.shared.register = register
            Experiments.shared.answerLength = .full
            let prompt = LucyBrain.prompt(question: "where is the medkit", context: "")
            for claim in Self.fullnessClaims {
                XCTAssertTrue(prompt.contains(claim),
                             "\(register)/.full is missing the fullness claim: \(claim)")
            }
        }
    }

    func testConversationalLengthCarriesNoFullnessClaim() {
        for register in Register.allCases {
            Experiments.shared.register = register
            Experiments.shared.answerLength = .conversational
            let prompt = LucyBrain.prompt(question: "where is the medkit", context: "")
            for claim in Self.fullnessClaims {
                XCTAssertFalse(prompt.contains(claim),
                              "\(register)/.conversational leaked the fullness claim: \(claim)")
            }
        }
    }

    // MARK: - Composition with `register`

    // All 4 registers x 3 lengths produce a prompt, and each dial's
    // contribution survives independent of the other's setting: the active
    // manner paragraph is always present regardless of length, the active
    // length paragraph is always present regardless of register, neither a
    // different register's manner nor a different length's guidance leaks
    // in, and rules 1-5 are untouched throughout. This is the orthogonality
    // that used to be only structural -- now that `.full` carries real
    // content instead of "", a leak between the two dials would actually
    // show up as a false positive here instead of trivially passing because
    // there was nothing to leak.
    func testRegisterAndAnswerLengthCompose() {
        for register in Register.allCases {
            for length in AnswerLength.allCases {
                Experiments.shared.register = register
                Experiments.shared.answerLength = length
                let prompt = LucyBrain.prompt(question: "where is the medkit", context: "")

                XCTAssertTrue(prompt.contains(LucyBrain.manner(for: register)),
                             "\(register)/\(length): manner paragraph missing")
                XCTAssertTrue(prompt.contains("RULES, in order"),
                             "\(register)/\(length): rules missing")
                XCTAssertTrue(prompt.contains(LucyBrain.lengthGuidance(for: length)),
                             "\(register)/\(length): length guidance missing")

                // The other three manner paragraphs must NOT leak in --
                // proves the length dial didn't accidentally pick up a
                // second register's text.
                for other in Register.allCases where other != register {
                    XCTAssertFalse(prompt.contains(LucyBrain.manner(for: other)),
                                  "\(register)/\(length): a different register's manner leaked in")
                }

                // And the other two lengths' guidance must NOT leak in --
                // proves the register dial didn't accidentally pick up a
                // second length's text.
                for other in AnswerLength.allCases where other != length {
                    XCTAssertFalse(prompt.contains(LucyBrain.lengthGuidance(for: other)),
                                  "\(register)/\(length): a different length's guidance leaked in")
                }
            }
        }
    }

    // Changing `register` while `answerLength` is held fixed must not alter
    // the length paragraph's contribution to the ASSEMBLED prompt -- checked
    // by taking one register's full prompt, swapping its manner paragraph
    // for another register's, and asserting the result is byte-identical to
    // that other register's own prompt. If register leaked into the length
    // block (or vice versa), this single substitution would stop being
    // enough to reproduce the other prompt and the assertion would fail.
    // Runs for every length, including `.full`, since manner is always
    // present in the prompt regardless of what the length dial contributes.
    func testChangingRegisterDoesNotAlterTheLengthContribution() {
        let question = "where is the medkit"
        for length in AnswerLength.allCases {
            Experiments.shared.answerLength = length
            for a in Register.allCases {
                for b in Register.allCases where b != a {
                    Experiments.shared.register = a
                    let promptA = LucyBrain.prompt(question: question, context: "")
                    Experiments.shared.register = b
                    let promptB = LucyBrain.prompt(question: question, context: "")
                    let swapped = promptA.replacingOccurrences(
                        of: LucyBrain.manner(for: a), with: LucyBrain.manner(for: b))
                    XCTAssertEqual(swapped, promptB,
                                   "\(length): swapping \(a) manner for \(b) did not reproduce " +
                                   "\(b)'s prompt -- something besides manner changed with register")
                }
            }
        }
    }

    // Same idea in the other direction: swap one length's guidance paragraph
    // for another's inside an assembled prompt and confirm it reproduces
    // that other length's own prompt exactly, for every register and every
    // pair of lengths. Used to be restricted to the two non-empty lengths,
    // back when `.full`'s guidance was "" and swapping in an empty
    // substring wasn't a meaningful check; now that `.full` carries the
    // fullness claims for real, this is exactly the "orthogonality that was
    // previously only structural and is now real" this dial exists to prove.
    func testChangingAnswerLengthDoesNotAlterTheManner() {
        let question = "where is the medkit"
        for register in Register.allCases {
            Experiments.shared.register = register
            for a in AnswerLength.allCases {
                for b in AnswerLength.allCases where b != a {
                    Experiments.shared.answerLength = a
                    let promptA = LucyBrain.prompt(question: question, context: "")
                    Experiments.shared.answerLength = b
                    let promptB = LucyBrain.prompt(question: question, context: "")
                    let swapped = promptA.replacingOccurrences(
                        of: LucyBrain.lengthGuidance(for: a),
                        with: LucyBrain.lengthGuidance(for: b))
                    XCTAssertEqual(swapped, promptB,
                                   "\(register): swapping \(a) guidance for \(b) did not " +
                                   "reproduce \(b)'s prompt -- manner or rules changed with length")
                }
            }
        }
    }
}
