import XCTest
@testable import Lucy

// The aura used to be driven by pure touch state (`isRecording`), which meant
// it lit up to its full state the instant a finger landed on the button --
// while SpeechListener.start() was still, measured on device, deaf for a
// median 0.284s (up to 0.449s) while it set the audio category, activated
// the session and installed the tap. That taught people to start talking
// immediately, and the front of sentences was lost.
//
// `RecordingAuraPhase.derive` is the fix's pure core: it must distinguish
// "I have your press" (.ready) from "I can hear you" (.listening), and it
// must never light up at all with no press in progress.
final class RecordingAuraPhaseTests: XCTestCase {

    func testNoTouchIsOffRegardlessOfSpeechStatus() {
        XCTAssertEqual(.off, RecordingAuraPhase.derive(touching: false, status: .idle))
        XCTAssertEqual(.off, RecordingAuraPhase.derive(touching: false, status: .listening))
    }

    func testTouchWithoutListeningIsTheQuietReadyState() {
        // This is the bug: a finger down, mic not yet open. Must NOT be
        // `.listening` -- that is exactly what lied before.
        XCTAssertEqual(.ready, RecordingAuraPhase.derive(touching: true, status: .idle))
    }

    func testTouchWithListeningIsTheFullState() {
        XCTAssertEqual(.listening, RecordingAuraPhase.derive(touching: true, status: .listening))
    }

    func testTouchWithATerminalStatusFromAnEarlierPressIsStillJustReady() {
        // A `.failed`/`.finished` left over from the previous press (before
        // the next press's start() has caught up to .listening) must not
        // read as though the CURRENT press is already heard.
        XCTAssertEqual(.ready, RecordingAuraPhase.derive(touching: true, status: .failed("x")))
        XCTAssertEqual(.ready, RecordingAuraPhase.derive(touching: true, status: .finished("x")))
    }
}
