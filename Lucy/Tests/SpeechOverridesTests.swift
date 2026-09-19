import XCTest
@testable import Lucy

// SpeechOverrides is pure string transformation -- no device, no model -- so
// every test here checks the hand table (`SpeechOverrides.apply`'s first
// pass) directly.
//
// `isRealWord` is stubbed to always answer "real word" for every test in this
// file. Without that, the second pass (`phoneticPass`, driven by
// `CampVocabulary.terms` and the on-device `UITextChecker`) could quietly
// produce the same output as the hand table on a device where it happens to
// have the right vocabulary loaded, and a broken hand-table entry would still
// pass. Stubbing it out means every assertion here is about the hand table
// and only the hand table -- confirmed by neutering `names` to `[:]` during
// development: every test in this file failed, and no other suite in the
// project noticed.
@MainActor
final class SpeechOverridesTests: XCTestCase {
    private var savedIsRealWord: ((String) -> Bool)!

    override func setUp() {
        savedIsRealWord = SpeechOverrides.isRealWord
        SpeechOverrides.isRealWord = { _ in true }
    }

    override func tearDown() {
        SpeechOverrides.isRealWord = savedIsRealWord
    }

    // MARK: - The "tate" family, observed live 2026-08-19

    func testPoTateRestoresPiotr() {
        XCTAssertEqual(SpeechOverrides.apply(to: "why does po tate hate water"),
                       "why does Piotr hate water")
    }

    func testPotateAndPoeTateAlsoRestorePiotr() {
        XCTAssertEqual(SpeechOverrides.apply(to: "ask potate about the shade structure"),
                       "ask Piotr about the shade structure")
        XCTAssertEqual(SpeechOverrides.apply(to: "poe tate said he'd fix the bikes"),
                       "Piotr said he'd fix the bikes")
    }

    // The s-ending member of the pair files under the possessive, same as
    // "pierces" / "pierce's" do -- the trailing s is the sound of "Piotr's",
    // not a plural potate.
    func testPoTatesRestoresPiotrsPossessive() {
        XCTAssertEqual(SpeechOverrides.apply(to: "have you seen po tates bike"),
                       "have you seen Piotr's bike")
    }

    // MARK: - The guard against over-eager mapping

    // "pure" is also heard for Piotr, and is deliberately NOT in the table --
    // "is the water pure" is a plausible camp question, so mapping it would
    // break that question, the same reason "peanut" is excluded.
    func testPureIsNotReplaced() {
        XCTAssertEqual(SpeechOverrides.apply(to: "why does pure hate water"),
                       "why does pure hate water")
    }

    // Vacuity-proof version of the above: one sentence carrying both "pure"
    // and a live disguise. A neutered table fails this by leaving "pura"
    // unconverted, not by wrongly touching "pure" -- so this test cannot pass
    // by accident the way a bare non-replacement assertion could.
    func testPureSurvivesAlongsideALiveDisguiseInTheSameSentence() {
        let out = SpeechOverrides.apply(to: "is the water pure, or does pura hate it")
        XCTAssertTrue(out.contains("Piotr"), "the live disguise \"pura\" did not convert: \(out)")
        XCTAssertTrue(out.contains("pure"), "\"pure\" was touched: \(out)")
        XCTAssertFalse(out.contains("pura"), "\"pura\" was left unconverted: \(out)")
    }

    // MARK: - Existing disguises still map

    func testJewelStillRestoresJUUL() {
        XCTAssertEqual(SpeechOverrides.apply(to: "he lost his jewel again"),
                       "he lost his JUUL again")
    }

    func testOzzoleStillRestoresOzHole() {
        XCTAssertEqual(SpeechOverrides.apply(to: "what an ozzole thing to do"),
                       "what an Oz hole thing to do")
    }

    // MARK: - Whole-word matching

    // "pita" is a real disguise for Piotr, but "pitas" (the plural, an
    // ordinary English word) merely contains it -- \b...\b must not fire
    // inside a longer word.
    func testAWordMerelyContainingADisguiseIsUntouched() {
        XCTAssertEqual(SpeechOverrides.apply(to: "we ordered pitas for the potluck"),
                       "we ordered pitas for the potluck")
    }

    // Same check against the new entries: "potatoes" does not contain
    // "potate" as a substring, but it is exactly the shape of word this
    // table must never reach into.
    func testPotatoesIsUntouched() {
        XCTAssertEqual(SpeechOverrides.apply(to: "we ran out of potatoes"),
                       "we ran out of potatoes")
    }
}
