import XCTest
@testable import Lucy

// Commit cd2f4cf changed SpeechListener so a press too short to open the
// microphone sets a `.failed` status and journals it, instead of silently
// leaving a live mic/session for the NEXT press to trip over. But ChatView
// called `speech.reset()` -- which wipes status back to `.idle` -- before
// checking it, so the new `.failed` state was thrown away before anything
// could react. `missedPressMessage` is the predicate that must be read
// BEFORE reset(), and is what decides whether the person is told plainly
// that nothing was heard.
final class MissedPressMessageTests: XCTestCase {

    func testNothingHeardAndFailedSurfacesItsMessage() {
        XCTAssertEqual("too quick", missedPressMessage(heard: "", status: .failed("too quick")))
    }

    func testNothingHeardAndUnavailableSurfacesItsMessage() {
        XCTAssertEqual("no offline speech",
                        missedPressMessage(heard: "", status: .unavailable("no offline speech")))
    }

    func testNothingHeardButDeniedStaysSilentHere() {
        // Denied is a permissions problem, not the "too short a press" gap
        // this job covers, and it carries no message of its own to show.
        XCTAssertNil(missedPressMessage(heard: "", status: .denied))
    }

    func testNothingHeardAndIdleOrListeningHaveNothingToSay() {
        XCTAssertNil(missedPressMessage(heard: "", status: .idle))
        XCTAssertNil(missedPressMessage(heard: "", status: .listening))
    }

    func testAnythingActuallyHeardNeverShowsACueEvenIfStatusFailedAfterward() {
        // e.g. the recognizer errored after delivering a partial -- whatever
        // was heard already wins; there is nothing to tell the person about.
        XCTAssertNil(missedPressMessage(heard: "where's the medkit", status: .failed("late error")))
    }
}
