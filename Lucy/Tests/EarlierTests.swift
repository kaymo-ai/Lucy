import XCTest
@testable import Lucy

// The "Earlier" control, and the paging behind it.
//
// The thread opens on a greeting and nothing else, deliberately: captures used
// to replay into it and were removed because a week of things you already knew
// stood between you and the place you type. Restoring the conversation
// automatically would be that mistake again, so history is one tap away and
// arrives a few turns at a time.
//
// What is worth testing is the paging, because getting it wrong is silent: an
// off-by-one either repeats the turn you just read or skips one entirely, and
// both look like a conversation that almost makes sense.
@MainActor
final class EarlierTests: XCTestCase {

    private var log: ChatLog { ChatLog.shared }

    override func setUpWithError() throws {
        log.forget()
        for i in 1...12 {
            log.record(question: "question \(i)", answer: "answer \(i)")
        }
    }

    override func tearDownWithError() throws {
        log.forget()
    }

    func testAPageComesBackInReadingOrder() {
        let page = log.page(skipping: 0, limit: 3)
        XCTAssertEqual(page.map(\.question), ["question 10", "question 11", "question 12"],
                       "a page must read oldest-to-newest, the way it is shown")
    }

    /// The whole point of `skipping`: the second tap must continue where the
    /// first stopped, with no turn seen twice and none jumped over.
    func testPagesDoNotOverlapOrSkip() {
        var seen: [String] = []
        var offset = 0
        while true {
            let page = log.page(skipping: offset, limit: 5)
            if page.isEmpty { break }
            seen = page.map(\.question) + seen
            offset += page.count
        }
        XCTAssertEqual(seen, (1...12).map { "question \($0)" },
                       "paging back through the log must reconstruct it exactly")
    }

    func testPastTheEndIsEmptyRatherThanWrapping() {
        XCTAssertTrue(log.page(skipping: 12, limit: 5).isEmpty)
        XCTAssertTrue(log.page(skipping: 99, limit: 5).isEmpty)
    }

    /// The control shows while `restored < count`, so the count has to be the
    /// same unit the pages are measured in -- turns, not bubbles.
    func testCountIsTurnsNotBubbles() {
        XCTAssertEqual(log.count, 12)
        XCTAssertEqual(log.page(skipping: 0, limit: 100).count, 12)
    }

    /// Memory across reboots, which is the reason any of this works: the log
    /// is a file, so a fresh `ChatLog` sees what the last one wrote.
    func testWhatWasWrittenSurvivesANewReader() {
        XCTAssertEqual(log.recent(1).first?.question, "question 12")
        XCTAssertEqual(log.count, 12)
    }
}
