import XCTest
@testable import Lucy

// `Experiments.shared.rankByRelevance`: order the camp's own-record sections
// of `LucyVoice.factSheet` by how much of the question each source's best row
// actually matched, instead of always leading with camp_fact.
//
// The bug this fixes, from the device journal: asked "Does Piotr hate water",
// retrieval found the right thing -- a Book of Lucy passage naming Piotr and
// the flood -- but it sat four sections below a run of camp_fact rows that
// only matched the single common word "water", under a header that told the
// model to answer from those first. The fix is ORDER and FRAMING only: no
// source, no fact, no scoring formula changes here.
@MainActor
final class FactSheetRankingTests: XCTestCase {
    override func setUp() {
        Experiments.shared.rankByRelevance = true
        Experiments.shared.register = .default_
    }

    override func tearDown() {
        Experiments.shared.rankByRelevance = true
        Experiments.shared.register = .default_
    }

    // MARK: - Fixtures

    /// Five camp_fact rows that only ever match "water" -- the shape of the
    /// journal's actual bug: plentiful, but each answers one word of a
    /// three-word question.
    private func waterCampFacts() -> [CampFact] {
        (1...5).map { i in
            CampFact(id: Int64(i), topic: "water",
                    fact: "We store water barrel number \(i) in the camp truck.",
                    category: nil, year: nil)
        }
    }

    /// The Book of Lucy entity fact, matching two of the question's terms --
    /// "piotr" and "ark" -- whole word, the way `containsWord` requires.
    private func arkHit() -> Hit {
        let entity = Entity(id: 1, name: "Book of Lucy", kind: "tradition",
                            summary: "", firstSeen: nil, lastSeen: nil, mentionCount: 0)
        let fact = EntityFact(id: 1,
                              fact: "It includes a passage where the Lord commands "
                                  + "Piotr to build an ark to save his family.",
                              category: "history", assertedOn: nil)
        return Hit(entity: entity, facts: [fact], score: 0)
    }

    private let questionTerms = ["piotr", "ark", "water"]

    // MARK: - The decisive test

    func testEntityMatchingTwoTermsLeadsOverCampFactsMatchingOne() {
        let answer = Answer(hits: [arkHit()], camp: waterCampFacts(), docs: [],
                            general: [], people: [], about: nil, lore: [],
                            terms: questionTerms)
        let sheet = LucyVoice.factSheet(answer)

        guard let entityRange = sheet.range(of: "About Book of Lucy") else {
            return XCTFail("entity section missing from the sheet")
        }
        guard let campRange = sheet.range(of: "We store water barrel") else {
            return XCTFail("camp section missing from the sheet")
        }
        XCTAssertTrue(entityRange.lowerBound < campRange.lowerBound,
                     "the two-term entity match should lead over the one-term camp matches")

        // The framing moved with the leader -- camp no longer claims primacy,
        // and the section that does is the one actually carrying it.
        XCTAssertTrue(sheet.contains("answer from these first"))
        guard let framingRange = sheet.range(of: "answer from these first") else {
            return XCTFail("no primacy framing found at all")
        }
        XCTAssertTrue(framingRange.lowerBound < campRange.lowerBound,
                     "the primacy framing should sit with the leading section")
        XCTAssertFalse(sheet.contains("OUR CAMP'S OWN RECORDS — answer from these first"),
                       "camp is not leading here and must not claim primacy")
    }

    // MARK: - BACKGROUND stays last, on or off

    func testBackgroundStaysLastRegardlessOfRanking() {
        let general = [GeneralFact(id: 1, topic: "water", fact: "Bring 1.5 gallons a day.",
                                   source: "Survival Guide")]
        let answer = Answer(hits: [arkHit()], camp: waterCampFacts(), docs: [],
                            general: general, people: [], about: nil, lore: [],
                            terms: questionTerms)

        for flag in [true, false] {
            Experiments.shared.rankByRelevance = flag
            let sheet = LucyVoice.factSheet(answer)
            guard let bg = sheet.range(of: "=== BACKGROUND"),
                  let entity = sheet.range(of: "About Book of Lucy"),
                  let camp = sheet.range(of: "We store water barrel") else {
                return XCTFail("expected sections missing (flag=\(flag))")
            }
            XCTAssertTrue(bg.lowerBound > entity.lowerBound, "flag=\(flag)")
            XCTAssertTrue(bg.lowerBound > camp.lowerBound, "flag=\(flag)")
        }
    }

    // MARK: - Flag off: the old order returns exactly

    func testFlagOffReturnsTodaysFixedOrder() {
        let answer = Answer(hits: [arkHit()], camp: waterCampFacts(), docs: [],
                            general: [], people: [], about: nil, lore: [],
                            terms: questionTerms)
        Experiments.shared.rankByRelevance = false
        let sheet = LucyVoice.factSheet(answer)

        // Camp still leads unconditionally, and still claims primacy, even
        // though the entity fact is the stronger match.
        XCTAssertTrue(sheet.hasPrefix("=== OUR CAMP'S OWN RECORDS — answer from these first ==="))
        guard let camp = sheet.range(of: "We store water barrel"),
              let entity = sheet.range(of: "About Book of Lucy") else {
            return XCTFail("expected sections missing")
        }
        XCTAssertTrue(camp.lowerBound < entity.lowerBound,
                     "camp must precede entities in the untouched fixed order")
    }

    // MARK: - Stability: a tie keeps today's order

    func testATieKeepsTodaysOrder() {
        // Both sources match exactly one term ("water") -- a genuine tie in
        // coverage. Camp (index 0) must still lead over entities (index 2).
        let entity = Entity(id: 2, name: "Doris", kind: "trailer",
                            summary: "", firstSeen: nil, lastSeen: nil, mentionCount: 0)
        let fact = EntityFact(id: 2, fact: "We keep the water dispensers in Doris.",
                              category: "contents", assertedOn: nil)
        let hit = Hit(entity: entity, facts: [fact], score: 0)
        let answer = Answer(hits: [hit], camp: waterCampFacts(), docs: [],
                            general: [], people: [], about: nil, lore: [],
                            terms: ["water"])
        let sheet = LucyVoice.factSheet(answer)
        guard let camp = sheet.range(of: "We store water barrel"),
              let entities = sheet.range(of: "About Doris") else {
            return XCTFail("expected sections missing")
        }
        XCTAssertTrue(camp.lowerBound < entities.lowerBound,
                     "a coverage tie should keep camp leading, same as today")
        XCTAssertTrue(sheet.hasPrefix("=== OUR CAMP'S OWN RECORDS — answer from these first ==="))
    }

    // MARK: - The budget cannot let a weak section push out a strong one

    func testAHighRelevanceSectionSurvivesAnOversizedLowRelevanceSection() {
        // Camp matches only "water", but there is enough of it to blow the
        // whole 4000-character budget on its own. The entity fact matches
        // two terms and must still make it onto the sheet.
        let bigFact = "We store water barrels along the back wall of the truck, "
            + "refilled every few days when the run allows it. "
        let hugeCampFacts = (1...60).map { i in
            CampFact(id: Int64(i), topic: "water", fact: "\(bigFact) (barrel \(i))",
                    category: nil, year: nil)
        }
        let totalCampChars = hugeCampFacts.reduce(0) { $0 + $1.fact.count }
        XCTAssertGreaterThan(totalCampChars, 4000, "fixture must actually exceed the budget")

        let answer = Answer(hits: [arkHit()], camp: hugeCampFacts, docs: [],
                            general: [], people: [], about: nil, lore: [],
                            terms: questionTerms)
        let sheet = LucyVoice.factSheet(answer)

        XCTAssertTrue(sheet.contains("About Book of Lucy"),
                     "the two-term entity match must not be crowded out")
        XCTAssertTrue(sheet.contains("build an ark"))
        // And the weak, oversized section is the one that gave way.
        XCTAssertFalse(sheet.contains("barrel 1)"),
                       "the one-term camp block should be the one dropped whole")
    }
}
