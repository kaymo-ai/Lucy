import XCTest
@testable import Lucy

// Recency as a weight, not a tiebreaker.
//
// 45% of the camp's facts are 2019 or older -- 2018 and 2019 alone are 606
// rows, more than every year since 2022 combined. The old rule added a capped
// bonus, `min((year - 2016) * 0.4, 4)`, against an exact topic match worth 8
// and a whole-word hit worth 3. A well-matched 2018 row beat a decently
// matched 2026 row every time, and the camp buys different water, parks
// somewhere else and runs different shifts now.
@MainActor
final class RecencyTests: XCTestCase {

    private let now = 2026

    // MARK: - The curve

    func testThisYearIsUnpenalised() {
        XCTAssertEqual(EntityStore.recency(2026, now: now), 1.0, accuracy: 0.001)
    }

    func testAnOldFactIsHeavilyDiscountedButNeverErased() {
        let old = EntityStore.recency(2018, now: now)
        XCTAssertLessThan(old, 0.5, "2018 should cost a lot")
        XCTAssertGreaterThanOrEqual(old, 0.35,
                                    "being stale is a reason to rank lower, "
                                    + "never a reason to vanish")
    }

    func testTheFloorHoldsForAnythingAncient() {
        XCTAssertEqual(EntityStore.recency(2015, now: now), 0.35, accuracy: 0.001)
        XCTAssertEqual(EntityStore.recency(1999, now: now), 0.35, accuracy: 0.001)
    }

    func testItDecreasesMonotonically() {
        var previous = 2.0
        for year in stride(from: 2026, through: 2015, by: -1) {
            let w = EntityStore.recency(year, now: now)
            XCTAssertLessThanOrEqual(w, previous,
                                     "\(year) scored above the year after it")
            previous = w
        }
    }

    func testAnUndatedClaimGetsATypicalYearNotAFreePass() {
        // The first version returned 1.0 here, and that was a real defect:
        // 160 camp facts carry no year, so it gave every one of them a 3x
        // advantage over anything dated 2019 -- rewarding missing metadata.
        // It surfaced as "how do we get water delivered" preferring an
        // undated vouchers row over the 2019 row that is the ONLY row in the
        // table containing the word "deliver".
        let undated = EntityStore.recency(nil, now: now)
        XCTAssertEqual(undated, EntityStore.undatedWeight, accuracy: 0.001)
        XCTAssertLessThan(undated, EntityStore.recency(2026, now: now),
                          "unknown age must not beat known-current")
        XCTAssertGreaterThan(undated, EntityStore.recency(2016, now: now),
                             "unknown age must not be treated as ancient")
        XCTAssertEqual(EntityStore.recency(0, now: now), undated, accuracy: 0.001)
    }

    func testADatedRecentFactStillBeatsAnUndatedOne() {
        XCTAssertGreaterThan(10.0 * EntityStore.recency(2026, now: now),
                             10.0 * EntityStore.recency(nil, now: now))
    }

    func testAFutureYearIsNotRewarded() {
        // A typo in a year field should not out-rank the present.
        XCTAssertEqual(EntityStore.recency(2030, now: now), 1.0, accuracy: 0.001)
    }

    // MARK: - What it actually changes

    func testRecentBeatsOldAtEqualMatchQuality() {
        // The whole point, in the units the scorer uses.
        let equalMatch = 10.0
        let new = equalMatch * EntityStore.recency(2026, now: now)
        let old = equalMatch * EntityStore.recency(2018, now: now)
        XCTAssertGreaterThan(new, old * 2,
                             "an equally-matched current fact should win "
                             + "decisively, not by a hair")
    }

    func testAnOldFactCanStillWinIfItIsGenuinelyBetterMatched() {
        // Recency must not become censorship. A 2018 row that matches the
        // question far better than anything current still has to surface.
        let strongOld = 30.0 * EntityStore.recency(2018, now: now)
        let weakNew = 6.0 * EntityStore.recency(2026, now: now)
        XCTAssertGreaterThan(strongOld, weakNew)
    }

    func testTheOldAdditiveRuleWouldHaveGotThisWrong() {
        // The regression this replaces, stated as arithmetic. Under the old
        // rule a 2018 fact matching the topic exactly (8) plus its bonus
        // (0.8) scored 8.8, while a 2026 fact with two word hits (6) plus its
        // bonus (4) scored 10 -- close enough that one more matched word
        // flipped it. Under the new rule the gap is not close.
        let oldRuleStale = 8.0 + min(Double(2018 - 2016) * 0.4, 4)
        let oldRuleFresh = 6.0 + min(Double(2026 - 2016) * 0.4, 4)
        XCTAssertLessThan(abs(oldRuleStale - oldRuleFresh), 1.5,
                          "the old rule really was this close")

        let newStale = 8.0 * EntityStore.recency(2018, now: now)
        let newFresh = 6.0 * EntityStore.recency(2026, now: now)
        XCTAssertGreaterThan(newFresh, newStale,
                             "the current fact should now win despite the "
                             + "weaker match")
    }
}
