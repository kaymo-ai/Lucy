import XCTest
@testable import Lucy

// `showsThinkingDots` decides between the animated placeholder and the
// streaming answer. The owner's ask was "better UI during thinking" -- the
// bug it must not reintroduce is the dots covering or delaying real tokens:
// the instant `brain.partial` is non-empty, the streamed text is the good
// part and must win outright, never share the frame with the dots.
final class ThinkingDotsPredicateTests: XCTestCase {

    func testNotThinkingNeverShowsDots() {
        XCTAssertFalse(showsThinkingDots(thinking: false, partial: ""))
        XCTAssertFalse(showsThinkingDots(thinking: false, partial: "some text"))
    }

    func testThinkingWithNoTokensYetShowsDots() {
        XCTAssertTrue(showsThinkingDots(thinking: true, partial: ""))
    }

    func testThinkingWithRealTokensSwapsToTheStreamingTextImmediately() {
        XCTAssertFalse(showsThinkingDots(thinking: true, partial: "The barrels are"))
    }
}
