import XCTest

/// Opens the hamburger menu and keeps a screenshot, because a menu only
/// exists while a finger holds it open — render.sh can photograph every
/// screen and never this. Judging menu formatting from the code was how a
/// three-row build section shipped looking like a paragraph.
final class MenuRenderTests: XCTestCase {

    func testMenuScreenshot() {
        let app = XCUIApplication()
        // Straight to a screen that carries the menu; the default launch
        // lands on home, which has none, and a fresh simulator would sit on
        // the model gate anyway. --no-prompt for the same reason render.sh
        // passes it: simctl cannot grant speech recognition.
        app.launchArguments = ["--screen", "chat", "--no-prompt"]
        app.launch()
        let menu = app.buttons["Menu"].firstMatch
        XCTAssertTrue(menu.waitForExistence(timeout: 5), "menu button missing")
        menu.tap()
        // The menu animates open. Screenshot it immediately and you photograph
        // a half-expanded popup missing its last row or two -- which reads
        // exactly like a row that failed to render, and cost an investigation.
        Thread.sleep(forTimeInterval: 1)
        let shot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        shot.name = "menu-open"
        shot.lifetime = .keepAlways
        add(shot)

        // Settings is the only way to the dials, and a menu row that fails
        // to render looks like nothing at all -- no warning, no gap, just a
        // menu one row shorter than the code says. Only opening the menu can
        // catch that.
        #if DEBUG
        XCTAssertTrue(app.buttons["Settings"].firstMatch.waitForExistence(timeout: 3),
                      "the Settings row is missing from the menu")
        #endif

        // Into the build submenu, so the detail rows get photographed too.
        let build = app.buttons["This build"].firstMatch
        XCTAssertTrue(build.waitForExistence(timeout: 3), "build row missing")
        build.tap()
        Thread.sleep(forTimeInterval: 1)   // let the submenu settle
        let sub = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        sub.name = "menu-build-submenu"
        sub.lifetime = .keepAlways
        add(sub)
    }
}
