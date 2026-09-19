import XCTest

/// Taps "Earlier" and photographs what arrives.
///
/// The paging is covered by unit tests; what they cannot see is whether the
/// restored turns actually appear, and whether tapping throws the thread back
/// to the bottom. Loading inserts at the FRONT, which grows `turns.count`
/// without moving the last turn -- and the scroll-to-bottom used to fire on
/// count alone, which would have bounced you away from the thing you asked to
/// read the instant it loaded.
final class EarlierRenderTests: XCTestCase {

    func testEarlierLoadsAndStays() {
        let app = XCUIApplication()
        app.launchArguments = ["--screen", "chat-earlier", "--no-prompt"]
        app.launch()

        let earlier = app.buttons["Earlier"].firstMatch
        XCTAssertTrue(earlier.waitForExistence(timeout: 8),
                      "the Earlier control is missing above the greeting")

        let before = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        before.name = "earlier-before"
        before.lifetime = .keepAlways
        add(before)

        earlier.tap()
        // The insert animates in; photographing immediately catches it
        // half-arrived, which reads as a row that failed to render.
        Thread.sleep(forTimeInterval: 1.5)

        let after = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        after.name = "earlier-after"
        after.lifetime = .keepAlways
        add(after)

        // A restored question, by its text. If paging or the bubble split is
        // wrong this is the assertion that says so.
        XCTAssertTrue(app.staticTexts["What time is dinner"].firstMatch
                        .waitForExistence(timeout: 5),
                      "the most recent restored question did not appear")
    }
}
