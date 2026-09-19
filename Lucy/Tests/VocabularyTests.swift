import XCTest
@testable import Lucy

// The names the recogniser is primed with.
//
// On-device dictation works from a general English vocabulary, so a question
// about a camper is mostly a word the recogniser has never heard.
// `contextualStrings` is the hook, and what goes in it decides whether the
// question survives being spoken.
//
// Measured against the owner's phone: 16 of 105 distinct questions arrived
// mistranscribed. The largest single cause was one character.
@MainActor
final class VocabularyTests: XCTestCase {

    private var store: EntityStore!

    override func setUpWithError() throws {
        store = EntityStore(path: PreviewRoot.fixturePath)
        try XCTSkipIf(store == nil, "no fixture database")
    }

    /// The regression this file exists for.
    ///
    /// `add` guarded `s.count > 2`, so every two-letter name was dropped
    /// before it reached the recogniser. docs/learnings-lucy-2026-08-19.md
    /// records the same rule causing the same damage in `Retrieval.terms`,
    /// where it was fixed; this copy was missed.
    func testTwoLetterNamesReachTheRecogniser() {
        let terms = CampVocabulary.terms.map { $0.lowercased() }
        XCTAssertFalse(terms.isEmpty, "no vocabulary at all")
        // Oz has a camp tradition named after him and is one of the most
        // referenced people in the corpus. Without him primed, the journal
        // shows "Oz hole" transcribed as "arsehole" six times.
        let hasTwoLetter = terms.contains { $0.count == 2 }
        XCTAssertTrue(hasTwoLetter,
                      "no two-letter name survived; the length filter is back")
    }

    func testSingleLettersAreStillExcluded() {
        // An initial matches everything and is genuinely noise. Two letters
        // is a person; one is not.
        for term in CampVocabulary.terms {
            XCTAssertGreaterThan(term.trimmingCharacters(in: .whitespaces).count, 1,
                                 "a single letter reached the recogniser: \(term)")
        }
    }

    func testTheListIsBoundedAndUnique() {
        // Apple's guidance is roughly a hundred phrases; past that, weighting
        // them all dilutes each one.
        XCTAssertLessThanOrEqual(CampVocabulary.terms.count, 100)
        let lowered = CampVocabulary.terms.map { $0.lowercased() }
        XCTAssertEqual(Set(lowered).count, lowered.count,
                       "a duplicate wastes one of a hundred slots")
    }

    func testNothingBlankOrPaddedGetsIn() {
        for term in CampVocabulary.terms {
            XCTAssertFalse(term.isEmpty)
            XCTAssertEqual(term, term.trimmingCharacters(in: .whitespaces),
                           "untrimmed phrase: \(term.debugDescription)")
        }
    }
}
