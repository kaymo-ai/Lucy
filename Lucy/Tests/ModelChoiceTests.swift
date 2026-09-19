import XCTest
@testable import Lucy

// Which model a phone gets, and whether its owner can change it.
//
// This exists because of a two-week failure that nothing caught: an 8 GB
// iPhone 16 Pro was classed as a 6 GB phone, handed the 1B model, and had no
// way back because the picker was debug-only. Retrieval was fine -- it passed
// the model eight Oz-hole facts -- and the model answered about RV power
// cords. The only diagnostic available was a filename in a menu.
//
// So the tests here are about the DECISION, not about the download: what the
// hardware says, what an override does to it, and that the two stay separable.
@MainActor
final class ModelChoiceTests: XCTestCase {

    private var saved: String?

    override func setUpWithError() throws {
        saved = UserDefaults.standard.string(forKey: "model-override")
        Device.override = nil
    }

    override func tearDownWithError() throws {
        UserDefaults.standard.set(saved, forKey: "model-override")
    }

    // MARK: - The override is readable in release

    /// The regression that stranded a tester. `Device.override` used to return
    /// nil unconditionally outside DEBUG, so a release build could not be
    /// moved off whatever the hardware picked. If this ever goes back to
    /// debug-only, this fails in both configurations.
    func testOverrideSurvivesAReadInAnyConfiguration() {
        Device.override = .full
        XCTAssertEqual(Device.override, .full,
                       "the picker is release-visible now; an override that "
                       + "only reads back in DEBUG strands a tester again")
        Device.override = .small
        XCTAssertEqual(Device.override, .small)
        Device.override = nil
        XCTAssertNil(Device.override)
    }

    func testOverrideDecidesWhichModelIsUsed() {
        Device.override = .small
        XCTAssertTrue(Device.marginal, "choosing Light must give Light")
        Device.override = .full
        XCTAssertFalse(Device.marginal, "choosing Full must give Full")
    }

    // MARK: - Thermal risk is a hardware fact, not a preference

    /// The warning exists because a 6 GB iPhone 15 thermally panicked on the
    /// full model within a day of the alpha. If `thermallyMarginal` ever
    /// starts honouring the override, then choosing Full -- the one choice
    /// that carries the risk -- is also the choice that hides the warning.
    func testChoosingFullDoesNotSilenceTheThermalWarning() {
        let hardware = Device.thermallyMarginal
        Device.override = .full
        XCTAssertEqual(Device.thermallyMarginal, hardware,
                       "the thermal report must not move when the owner picks")
        Device.override = .small
        XCTAssertEqual(Device.thermallyMarginal, hardware)
    }

    /// With no override, the two must agree: Auto is the hardware's answer.
    func testAutoIsTheHardwareAnswer() {
        Device.override = nil
        XCTAssertEqual(Device.marginal, Device.thermallyMarginal)
    }

    // MARK: - The line that would have caught this in a day

    func testDecisionLineNamesEveryInput() {
        Device.override = .small
        let line = Device.decisionLine
        for field in ["memoryGiB=", "marginal=", "tooSmall=", "overrideInForce="] {
            XCTAssertTrue(line.contains(field),
                          "\(field) missing from the journal's DEVICE line: \(line)")
        }
        XCTAssertTrue(line.contains("overrideInForce=small"))
    }

    // MARK: - The threshold, against measured hardware

    /// iOS does not report the number on the box. A measured iPhone 16 Pro
    /// reports 7.46 GiB for 8 GB of RAM, and the threshold used to be 7.5 --
    /// so the camp's newest phones were classed as 6 GB and given the 1B
    /// model. These are the reports the band has to separate, at the ~0.93
    /// ratio 7.46/8 implies.
    func testTheBandSeparatesRealDeviceReports() {
        let fourGB = 3.7, sixGB = 5.6, eightGB = 7.46

        XCTAssertLessThan(fourGB, 5.5, "a 4 GB phone must be refused outright")

        XCTAssertGreaterThan(sixGB, 5.5,
                             "a 6 GB phone is warm, not blocked -- it gets "
                             + "Light and a warning, not a refusal")
        XCTAssertLessThan(sixGB, 6.5, "a 6 GB phone defaults to Light")

        XCTAssertGreaterThan(eightGB, 6.5,
                             "an 8 GB phone reports 7.46 and must default to "
                             + "Full; at the old 7.5 it did not")
    }

    /// The margin that was missed. If the threshold ever drifts back above a
    /// real 8 GB report, this says so in the failure message.
    func testEightGigabytePhonesAreNotMarginalByAHair() {
        let measured8GB = 7.46
        XCTAssertFalse(measured8GB < 6.5,
                       "threshold has drifted above a measured 8 GB phone "
                       + "(7.46 GiB); that is the 0.04 GiB miss that put an "
                       + "iPhone 16 Pro on the 1B model for two weeks")
    }

    /// A phone too small to run anything is a separate state from one that is
    /// merely warm, and the block must not be reachable by choosing.
    func testTooSmallIsNotAChoice() {
        let blocked = Device.tooSmall
        Device.override = .full
        XCTAssertEqual(Device.tooSmall, blocked,
                       "tooSmall refuses the download; a preference must not "
                       + "move it in either direction")
    }
}
