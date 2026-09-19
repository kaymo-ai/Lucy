import XCTest
@testable import Lucy

// The schedule is a document that ships in the bundle, and these tests hold
// it to arriving and to parsing into the shape the screen draws.
//
// Two failures are guarded, and both are silent rather than loud:
//
// 1. xcodegen drops a misplaced resource entry without a word, so the file
//    can simply not be in the bundle. `InfoView.scheduleFile` returns nil and
//    the row quietly says "not yet" -- which reads like a decision rather
//    than a broken build.
// 2. The two halves of the file use different markup, because they came from
//    two different Google Sheets exports. A parser that stops distinguishing
//    an event from its roster still renders SOMETHING, just with every name
//    promoted to its own event. That looks populated, which is why it needs
//    an assertion on the shape and not on the count alone.
final class ScheduleTests: XCTestCase {

    private var file: String!

    override func setUpWithError() throws {
        file = InfoView.scheduleFile
        try XCTSkipIf(file == nil,
                      "ps-2026-shifts.md is not in the bundle; check the "
                      + "sources entry in project.yml has buildPhase: resources")
    }

    // MARK: - It is there at all

    func testTheSheetIsInTheBundle() {
        XCTAssertFalse(file.isEmpty)
        XCTAssertTrue(file.contains("PS 2026 Shifts"),
                      "the bundled file is not the sign-up sheet")
    }

    // MARK: - The shape the screen draws

    func testEveryDayOfTheWeekIsADay() {
        let days = parseSchedule(file)
            .filter { $0.kind == .day }
            .map(\.text)
        XCTAssertEqual(days, ["Sunday", "Monday", "Tuesday", "Wednesday",
                              "Thursday", "Friday", "Saturday"],
                       "the week is the spine of this screen; a day parsed as "
                       + "anything else drops it out of the fold structure")
    }

    /// "What's Involved In A Shift" is a `##` heading like a day is, and the
    /// only thing separating them is the day-name check. If that check ever
    /// goes, the reference half becomes an eighth day.
    func testTheReferenceHalfIsNotADay() {
        let blocks = parseSchedule(file)
        let sections = blocks.filter { $0.kind == .section }.map(\.text)
        XCTAssertTrue(sections.contains { $0.contains("What's Involved") },
                      "the reference half should be a section, not a day")
        XCTAssertFalse(blocks.contains { $0.kind == .day
                                         && $0.text.contains("Involved") })
    }

    /// An event is a top-level bullet; the people on it are indented under it.
    /// Collapsing that distinction is the failure that still looks populated.
    func testEventsAndTheirPeopleStayApart() {
        let blocks = parseSchedule(file)
        let events = blocks.filter { $0.kind == .event }
        let details = blocks.filter { $0.kind == .detail }

        XCTAssertGreaterThan(events.count, 40)
        XCTAssertGreaterThan(details.count, events.count,
                             "every event has at least one person, so rosters "
                             + "must outnumber events")

        // An event line carries a time; a roster line carries names. If a
        // roster line ever parses as an event this is what catches it.
        for e in events where e.text.contains(" (Lucy)") {
            XCTAssertTrue(e.text.contains(":") || e.text.contains("-"),
                          "a Lucy outing should carry its time: \(e.text)")
        }
    }

    func testNoBlockIsEmpty() {
        for b in parseSchedule(file) {
            XCTAssertFalse(b.text.trimmingCharacters(in: .whitespaces).isEmpty,
                           "an empty block draws as a gap for no reason")
        }
    }

    // MARK: - Folding

    /// Every day must own its events. A day that groups to an empty body is a
    /// fold that opens onto nothing.
    func testEveryDayOwnsItsEvents() {
        let groups = groupSchedule(parseSchedule(file))
        let days = groups.filter { $0.heading?.kind == .day }
        XCTAssertEqual(days.count, 7)
        for d in days {
            let events = d.body.filter { $0.kind == .event }
            XCTAssertFalse(events.isEmpty,
                           "\(d.heading?.text ?? "?") folds open onto nothing")
        }
    }

    /// The line telling everyone to sign up for three shifts sits before the
    /// first heading. Grouping must not swallow it.
    func testThePreambleSurvivesGrouping() {
        let groups = groupSchedule(parseSchedule(file))
        let all = groups.flatMap { $0.preceding + $0.body }
        XCTAssertTrue(all.contains { $0.text.contains("3 shifts") },
                      "the sign-up instruction was lost in grouping")
    }

    /// Nobody may be dropped between the file and the parsed blocks: a name
    /// missing from the schedule means somebody does not know they have a
    /// shift.
    func testNoNameIsLostBetweenFileAndBlocks() {
        let rosterLines = file.components(separatedBy: .newlines)
            .filter { $0.hasPrefix("    * ") }
        let details = parseSchedule(file).filter { $0.kind == .detail }
        XCTAssertEqual(details.count, rosterLines.count,
                       "every indented line in the file is a roster line and "
                       + "must survive as a .detail block")
    }
}
