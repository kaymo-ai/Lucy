import XCTest
@testable import Lucy

// A question that uses the name people say, against a corpus that wrote a
// different one down.
//
// The portrait has matched aliases since person_alias existed, but nothing
// else did -- so "why does Peet hate water" would find the man and none of
// his facts, because camp_fact says "Piotr" and the question does not.
// Retrieval widens the question once instead, and every layer below matches
// as it always has.
@MainActor
final class AliasTests: XCTestCase {

    private var store: EntityStore!

    override func setUpWithError() throws {
        store = EntityStore(path: PreviewRoot.fixturePath)
        try XCTSkipIf(store == nil, "no fixture database")
        try XCTSkipIf(store.canonicalNames(matching: ["piot"]).isEmpty,
                      "no person_alias rows; run dedupe_people.py")
    }

    // MARK: - The lookup

    func testAManuallyCuratedAliasResolves() {
        // Piotr gets nothing from the derived passes: one word, so no name
        // parts, and the corpus spells him correctly every time. These three
        // were added by hand in scripts/manual_aliases.json.
        for spoken in ["Piot", "Peet", "Pioter"] {
            XCTAssertEqual(store.canonicalNames(matching: [spoken.lowercased()]),
                           ["Piotr"], "\(spoken) did not resolve")
        }
    }

    func testADerivedNamePartResolves() {
        XCTAssertEqual(store.canonicalNames(matching: ["cece"]), ["Cece Garland"])
        XCTAssertEqual(store.canonicalNames(matching: ["saami"]), ["Saami Khoury"])
    }

    func testAnOrdinaryWordResolvesToNobody() {
        // The guard that keeps this from breaking more than it fixes. "pure"
        // is a real mishearing of Piotr and is deliberately NOT an alias,
        // because "is the water pure" is a real camp question.
        for word in ["pure", "water", "kitchen", "shift", "the"] {
            XCTAssertTrue(store.canonicalNames(matching: [word]).isEmpty,
                          "\(word) claimed to be somebody's name")
        }
    }

    func testTheLookupIsCaseInsensitive() {
        XCTAssertEqual(store.canonicalNames(matching: ["PEET"]), ["Piotr"])
        XCTAssertEqual(store.canonicalNames(matching: ["peet"]), ["Piotr"])
    }

    func testAnEmptyQuestionAsksNothing() {
        XCTAssertTrue(store.canonicalNames(matching: []).isEmpty)
    }

    // MARK: - What it changes for a real question

    func testAMisheardNameNowReachesThatPersonsFacts() {
        // The point. "Peet" appears nowhere in the corpus, so without the
        // widening this question searches for a word no row contains.
        let heard = Retrieval.answer("why does Peet hate water", store: store)
        let spelled = Retrieval.answer("why does Piotr hate water", store: store)

        XCTAssertTrue(heard.terms.contains("piotr"),
                      "the real name was never added to the question")
        XCTAssertFalse(heard.isEmpty, "a misheard name found nothing at all")

        // Not identical -- "peet" is still in the term list, and it should be,
        // because an alias table is a guess about people rather than a
        // correction of them. But the facts it reaches must overlap.
        let heardIDs = Set(heard.camp.map(\.id))
        let spelledIDs = Set(spelled.camp.map(\.id))
        XCTAssertFalse(heardIDs.intersection(spelledIDs).isEmpty,
                       "the misheard question reached none of the same facts")
    }

    func testTheSpokenWordIsKeptNotReplaced() {
        let answer = Retrieval.answer("who is Peet", store: store)
        XCTAssertTrue(answer.terms.contains("peet"),
                      "the word actually spoken was thrown away")
        XCTAssertTrue(answer.terms.contains("piotr"))
    }

    func testWideningDoesNotFireForEveryQuestion() {
        // If it expanded on ordinary words it would quietly widen every
        // question ever asked, which is the failure mode of a fix like this.
        let answer = Retrieval.answer("where do we keep the water barrels",
                                      store: store)
        XCTAssertFalse(answer.terms.contains("piotr"))
    }
}
