import XCTest

/// Reproduces "the buttons don't work" as an automated tap, because the
/// simulator cannot be tapped from the shell and inspection alone was guessing.
final class ButtonTests: XCTestCase {

    private func launch(_ args: [String]) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchArguments = args
        app.launch()
        return app
    }

    /// Tapping an entity chip on the ASK screen should land on its record.
    func testAskChipOpensRecord() {
        let app = launch(["--screen", "voice", "--soft"])
        let chip = app.buttons["Doris"]
        XCTAssertTrue(chip.waitForExistence(timeout: 5), "Doris chip missing")
        chip.tap()
        XCTAssertTrue(app.staticTexts["GETTING IN"].waitForExistence(timeout: 5),
                      "tapping the Doris chip did not open the record")
    }

    /// The default launch -- no --screen at all -- is what the device actually
    /// got, because devicectl passed the literal "--arg" tokens through and the
    /// screen name never matched. Home rendered standalone with no handlers, so
    /// both mode buttons were dead. This is that bug.
    func testDefaultLaunchModeButtonsWork() {
        let app = launch([])
        let ask = app.buttons["ASK"].firstMatch
        XCTAssertTrue(ask.waitForExistence(timeout: 5), "ASK button missing on home")
        ask.tap()
        XCTAssertTrue(app.buttons["Close"].waitForExistence(timeout: 5),
                      "tapping ASK on the default launch did nothing")
    }

    /// Close on the ASK screen should dismiss back to home.
    func testAskCloseReturnsHome() {
        let app = launch(["--screen", "flow", "--soft"])
        app.buttons["ASK"].firstMatch.tap()
        let close = app.buttons["Close"]
        XCTAssertTrue(close.waitForExistence(timeout: 5), "Close missing on ASK")
        close.tap()
        XCTAssertTrue(app.staticTexts["NEXT"].waitForExistence(timeout: 5),
                      "Close on ASK did not return to home")
    }

    /// Close on REMEMBER should dismiss, not advance the capture stage.
    func testRememberCloseReturnsHome() {
        let app = launch(["--screen", "flow", "--soft"])
        app.buttons["REMEMBER"].firstMatch.tap()
        let close = app.buttons["Close"]
        XCTAssertTrue(close.waitForExistence(timeout: 5), "Close missing on REMEMBER")
        close.tap()
        XCTAssertTrue(app.staticTexts["NEXT"].waitForExistence(timeout: 5),
                      "Close on REMEMBER did not return to home")
    }
}
