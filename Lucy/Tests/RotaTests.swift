import XCTest
@testable import Lucy

// The rota is a table, and these tests hold it to being one.
//
// The failure this guards is not a crash. `shift_grid` is the second table to
// hold shifts; the first, `shifts`, had 2,140 rows of which not one was a real
// assignment -- person_name and role were the same spreadsheet cell, because a
// row-oriented parser read a wall-planner grid a row at a time. It looked
// populated, and that is exactly why nobody noticed for two years. So the
// tests here check the SHAPE of what came out, not just that rows exist.
@MainActor
final class RotaTests: XCTestCase {

    private var store: EntityStore!
    private var rota: [ShiftAssignment] = []

    override func setUpWithError() throws {
        store = EntityStore(path: PreviewRoot.fixturePath)
        try XCTSkipIf(store == nil, "no fixture database")
        rota = store.rota()
        try XCTSkipIf(rota.isEmpty,
                      "database predates parse_shift_grid.py; a database "
                      + "without a rota is valid, so this is a skip")
    }

    // MARK: - The shape the old table got wrong

    func testAPersonIsNotTheirShift() {
        // The whole `shifts` bug in one assertion: if these ever match, a
        // grid has been read as rows again.
        for a in rota {
            XCTAssertNotEqual(a.person.lowercased(), a.shift.lowercased(),
                              "\(a.person) is not a job title")
        }
    }

    func testEveryRowNamesADay() {
        for a in rota {
            XCTAssertFalse(a.day.trimmingCharacters(in: .whitespaces).isEmpty,
                           "a shift nobody can attend is not a shift")
        }
    }

    func testTheDaysAreDays() {
        let week = Set(["monday", "tuesday", "wednesday", "thursday",
                        "friday", "saturday", "sunday"])
        for a in rota {
            XCTAssertTrue(week.contains(a.day.lowercased()),
                          "\(a.day) is not a day of the week")
        }
    }

    func testShiftsAreSharedAcrossPeople() {
        // A grid read correctly puts many names against few jobs. A grid read
        // as rows produces one "shift" per cell, so this ratio collapses
        // toward 1 -- which is what the old table looked like.
        let people = Set(rota.map(\.person))
        let jobs = Set(rota.map(\.shift))
        XCTAssertGreaterThan(people.count, jobs.count,
                             "more distinct job titles than campers means the "
                             + "cells are being read as job names")
    }

    // MARK: - What the screen leans on

    func testEveryRowCarriesItsDescription() {
        // RotaView shows this once somebody filters to their own name, and
        // "Burn Barrel" alone does not tell you what to do.
        let described = rota.filter { !$0.detail.isEmpty }
        XCTAssertEqual(described.count, rota.count,
                       "the sheet describes every job; the parse should keep it")
    }

    func testSomeRowsHaveNoTimeAndThatIsFine() {
        // The bus crew sign up for a run, not for an hour. The view must not
        // assume a time exists -- it used to hold a 90pt column open to print
        // an em dash.
        let untimed = rota.filter { $0.timeSlot.isEmpty }
        XCTAssertFalse(untimed.isEmpty,
                       "if every row is timed, the empty-time path is dead "
                       + "code and this test should be deleted")
    }

    func testOnlyOneYearComesBack() {
        // The rota accumulates by design: parse_shift_grid deletes per source
        // file, and the corpus holds 2024, 2023, 2019, 2018 and 2016 shift
        // sheets that are one header row away from parsing. Two years reaching
        // the screen put a 2024 Sunday under the same "Sunday" heading as a
        // 2026 one, with nothing between them.
        let years = Set(rota.compactMap(\.year))
        XCTAssertLessThanOrEqual(years.count, 1,
                                 "rota() must scope to the newest year; got "
                                 + "\(years.sorted())")
    }

    func testTheRotaKnowsWhichBurnItIsFor() {
        // The screen prints this. A rota that cannot say which year it is
        // cannot be checked against the sheet on the wall.
        XCTAssertNotNil(rota.first?.year,
                        "the sheet's file name carries the year; keep it")
    }

    func testDaysArriveGroupedRatherThanInterleaved() {
        // RotaView groups by walking the array once and trusting the order.
        // If the store ever returned Sunday, Monday, Sunday, the screen would
        // silently show two Sunday blocks.
        var seen: Set<String> = []
        var previous = ""
        for a in rota where a.day != previous {
            XCTAssertFalse(seen.contains(a.day),
                           "\(a.day) appears in two separate runs")
            seen.insert(a.day)
            previous = a.day
        }
    }
}
