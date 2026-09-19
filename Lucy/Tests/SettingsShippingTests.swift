import XCTest
@testable import Lucy

// What the settings screen is allowed to show a tester.
//
// The screen ships now, because `register` and `answerLength` are the only
// lever that moves how Lucy sounds without touching the rules that keep her
// grounded -- and a dial nobody can reach is the same as a dial that does not
// exist. The retrieval A/B switches on the same screen do NOT ship: they turn
// a fix off for comparison, both default on because that is the behaviour
// believed correct, and a tester flipping one is running an experiment whose
// result reaches nobody.
//
// The compiler already proves the split (the Release build fails if a
// debug-only symbol is referenced outside its guard). These tests hold the
// INTENT, so that widening the screen later is a decision rather than an
// accident.
@MainActor
final class SettingsShippingTests: XCTestCase {

    /// The voice dials are the reason the screen ships. If this ever empties,
    /// the screen has nothing on it a tester should see.
    func testTheVoiceDialsAreOnTheScreen() {
        let keys = Set(SettingsCatalog.voice.map(\.key))
        XCTAssertEqual(keys, ["register", "answerLength"],
                       "the shipped screen must carry exactly the two dials "
                       + "that move voice without touching the rules")
    }

    func testEveryVoiceOptionSaysWhatItDoes() {
        for choice in SettingsCatalog.voice {
            XCTAssertFalse(choice.title.isEmpty)
            XCTAssertGreaterThan(choice.options.count, 1,
                                 "\(choice.key) is a picker with one option")
            for option in choice.options {
                XCTAssertFalse(option.blurb.trimmingCharacters(in: .whitespaces).isEmpty,
                               "\(choice.key)/\(option.name) has no sentence. "
                               + "\"Loose\" and \"Terse\" mean nothing on their "
                               + "own at 2am in the dust.")
            }
        }
    }

    /// Round-trips every option through `Experiments`, so a dial that renders
    /// but does not take is caught. `select` and `selected` are separate
    /// closures and nothing else makes them agree.
    func testEveryVoiceOptionSelects() {
        let e = Experiments.shared
        let register = e.register
        let length = e.answerLength
        defer { e.register = register; e.answerLength = length }

        for choice in SettingsCatalog.voice {
            for option in choice.options {
                choice.select(e, option.name)
                XCTAssertEqual(choice.selected(e), option.name,
                               "\(choice.key): picking \(option.name) did not stick")
            }
        }
    }

    /// The register has to reach the prompt, or the dial is decorative. Guards
    /// the seam between the screen and `LucyBrain`, which nothing else spans.
    func testPickingARegisterChangesTheProm() {
        let e = Experiments.shared
        let saved = e.register
        defer { e.register = saved }

        e.register = .dry
        let dry = LucyBrain.prompt(question: "q", context: "c")
        e.register = .loose
        let loose = LucyBrain.prompt(question: "q", context: "c")

        XCTAssertNotEqual(dry, loose,
                          "the register dial did not change the prompt")
        XCTAssertTrue(dry.contains(LucyBrain.manner(for: .dry)))
        XCTAssertTrue(loose.contains(LucyBrain.manner(for: .loose)))
    }

    #if DEBUG
    /// Present here, and deliberately absent from a release build. If this
    /// ever fails, the flags moved and the shipping split needs rethinking
    /// rather than the test relaxing.
    func testTheRetrievalFlagsExistOnDevBuilds() {
        XCTAssertEqual(Set(SettingsCatalog.flags.map(\.key)),
                       ["gatedPortrait", "rankByRelevance"])
    }
    #endif
}
