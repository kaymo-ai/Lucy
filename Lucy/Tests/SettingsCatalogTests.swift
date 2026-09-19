import XCTest
@testable import Lucy

// The settings screen has to reach every dial.
//
// A flag added to `Experiments` with no row on the screen is a dial that
// exists in the journal's FLAGS line and nowhere a thumb can get to -- which
// is the same as not shipping it, except it looks shipped. `SettingsView`
// renders `SettingsCatalog` and nothing else, so holding the catalog to the
// class holds the screen to it too: delete an entry here and the row really
// does vanish from the phone.
//
// The mirror is over `Experiments.shared` rather than a fresh instance
// because the class is a singleton by design -- the retrieval code reads it
// from nowhere near a View.
@MainActor
final class SettingsCatalogTests: XCTestCase {

    /// `@Published var gatedPortrait` reflects as `_gatedPortrait`; the
    /// wrapper's underscore is the storage name, not the property's.
    private var experimentProperties: Set<String> {
        Set(Mirror(reflecting: Experiments.shared).children.compactMap { child in
            child.label.map { $0.hasPrefix("_") ? String($0.dropFirst()) : $0 }
        })
    }

    // MARK: - Coverage

    func testEveryExperimentFlagIsReachableFromSettings() {
        let onTheClass = experimentProperties
        XCTAssertFalse(onTheClass.isEmpty,
                       "the mirror found no properties, so this test proves nothing")
        XCTAssertEqual(
            onTheClass.subtracting(SettingsCatalog.keys), [],
            "these dials exist on Experiments with no row on the settings screen")
    }

    func testSettingsOffersNothingExperimentsDoesNotHave() {
        XCTAssertEqual(
            SettingsCatalog.keys.subtracting(experimentProperties), [],
            "these rows name Experiments properties that no longer exist")
    }

    // MARK: - The rows do what the row says

    func testFlagBindingsReadAndWriteTheirOwnProperty() {
        let before = (Experiments.shared.gatedPortrait, Experiments.shared.rankByRelevance)
        defer {
            Experiments.shared.gatedPortrait = before.0
            Experiments.shared.rankByRelevance = before.1
        }

        for flag in SettingsCatalog.flags {
            let binding = flag.binding(Experiments.shared)
            let start = binding.wrappedValue
            binding.wrappedValue = !start
            XCTAssertEqual(binding.wrappedValue, !start,
                           "\(flag.key)'s toggle did not write through")
            binding.wrappedValue = start
            XCTAssertEqual(binding.wrappedValue, start)
        }
    }

    func testFlagBindingsAreNotAliasedToEachOther() {
        let before = (Experiments.shared.gatedPortrait, Experiments.shared.rankByRelevance)
        defer {
            Experiments.shared.gatedPortrait = before.0
            Experiments.shared.rankByRelevance = before.1
        }
        // Put every flag in a known state, flip exactly one, and require the
        // rest to sit still.
        for flag in SettingsCatalog.flags {
            for f in SettingsCatalog.flags { f.binding(Experiments.shared).wrappedValue = true }
            flag.binding(Experiments.shared).wrappedValue = false
            for other in SettingsCatalog.flags where other.key != flag.key {
                XCTAssertTrue(other.binding(Experiments.shared).wrappedValue,
                              "flipping \(flag.key) also moved \(other.key)")
            }
        }
    }

    func testChoiceSelectionRoundTripsThroughEveryOption() {
        let before = (Experiments.shared.register, Experiments.shared.answerLength)
        defer {
            Experiments.shared.register = before.0
            Experiments.shared.answerLength = before.1
        }

        for choice in SettingsCatalog.voice {
            XCTAssertFalse(choice.options.isEmpty, "\(choice.key) offers nothing")
            for option in choice.options {
                choice.select(Experiments.shared, option.name)
                XCTAssertEqual(choice.selected(Experiments.shared), option.name,
                               "picking \(option.name) on \(choice.key) did not stick")
            }
        }
    }

    func testEveryChoiceOffersEveryCaseOfItsEnum() {
        let offered = Dictionary(uniqueKeysWithValues:
            SettingsCatalog.voice.map { ($0.key, Set($0.options.map(\.name))) })
        XCTAssertEqual(offered["register"], Set(Register.allCases.map(\.displayName)))
        XCTAssertEqual(offered["answerLength"], Set(AnswerLength.allCases.map(\.displayName)))
    }

    // MARK: - The copy is the point

    /// Every option carries a sentence, because the names alone do not tell
    /// the person choosing anything -- "Terse" and "Loose" are the whole
    /// reason this screen exists rather than four more menu rows.
    ///
    /// This checks that copy is PRESENT and distinct, which is all a test can
    /// check. Whether the sentence is any good is read off the render.
    func testEveryRowSaysWhatItDoes() {
        func words(_ s: String) -> Int {
            s.split(whereSeparator: \.isWhitespace)
                .filter { $0.contains(where: \.isLetter) }.count
        }
        for flag in SettingsCatalog.flags {
            XCTAssertFalse(flag.title.isEmpty, "\(flag.key) has no title")
            XCTAssertGreaterThanOrEqual(words(flag.blurb), 6,
                                        "\(flag.key)'s blurb is not a sentence")
        }
        for choice in SettingsCatalog.voice {
            XCTAssertFalse(choice.title.isEmpty, "\(choice.key) has no title")
            for option in choice.options {
                XCTAssertGreaterThanOrEqual(words(option.blurb), 6,
                    "\(choice.key)/\(option.name)'s blurb is not a sentence")
            }
        }
        // Distinct, too: two options sharing a sentence means one of them is
        // unexplained and the screen is lying about the difference.
        for choice in SettingsCatalog.voice {
            XCTAssertEqual(Set(choice.options.map(\.blurb)).count, choice.options.count,
                           "\(choice.key) reuses a blurb across options")
        }
    }
}
