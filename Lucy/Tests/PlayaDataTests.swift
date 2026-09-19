import XCTest
@testable import Lucy

// What the city's three listings must be, in the bundle and on the screen.
//
// The failures these are aimed at are the quiet ones. A resource that
// xcodegen dropped shows an empty screen, not an error. A counts file that
// drifted from the data it counts shows a caption that is merely wrong. A
// slot index pointing past the end of the events array crashes on the day
// somebody opens the tab in the dust.
@MainActor
final class PlayaDataTests: XCTestCase {

    // MARK: - The bundle

    /// The whole reason `Playa.has` and `InfoView.soon` exist: a misplaced
    /// resource entry is dropped by xcodegen without a word.
    func testAllFourResourcesAreInTheBundle() {
        for name in [Playa.eventsFile, Playa.campsFile, Playa.artFile,
                     "playa-counts"] {
            XCTAssertTrue(Playa.has(name),
                          "\(name).json is not in the bundle — check the "
                          + "`buildPhase: resources` entry in project.yml")
        }
    }

    func testCountsDecode() throws {
        let counts = try XCTUnwrap(PlayaCounts.shared)
        XCTAssertEqual(counts.year, 2026)
        XCTAssertGreaterThan(counts.events.events, 4000)
        XCTAssertGreaterThan(counts.camps.camps, 1000)
        XCTAssertGreaterThan(counts.art.art, 300)
    }

    /// The counts file is a separate file so the Info tab need not decode
    /// 1.1 MB on every draw. That trade is only safe while the two agree,
    /// and nothing but this test makes them.
    func testCountsMatchTheFilesTheyCount() throws {
        let counts = try XCTUnwrap(PlayaCounts.shared)
        let index = try XCTUnwrap(PlayaEventIndex.load())
        XCTAssertEqual(counts.events.events, index.events.count)
        XCTAssertEqual(counts.events.days, index.days.count)
        XCTAssertEqual(counts.events.occurrences,
                       index.days.reduce(0) { $0 + $1.rows.count })

        let camps: [String: [PlayaCamp]] = try XCTUnwrap(Playa.decode(Playa.campsFile))
        XCTAssertEqual(counts.camps.camps, camps["camps"]?.count)
        let art: [String: [PlayaArt]] = try XCTUnwrap(Playa.decode(Playa.artFile))
        XCTAssertEqual(counts.art.art, art["art"]?.count)
    }

    // MARK: - The events

    /// Every slot points at an event that exists. The converter writes the
    /// index by position, so a change to how events are filtered would break
    /// this silently and crash on the first tap.
    func testEverySlotPointsAtARealEvent() throws {
        let file: PlayaEventsFile = try XCTUnwrap(Playa.decode(Playa.eventsFile))
        for day in file.days {
            for slot in day.slots {
                XCTAssertTrue(slot.e >= 0 && slot.e < file.events.count,
                              "\(day.name) has a slot pointing at event "
                              + "\(slot.e) of \(file.events.count)")
            }
        }
    }

    /// The day headings have to read the way the camp's own schedule reads,
    /// or the two entries look like two unrelated apps on one tab.
    func testDaysAreNamedTheWayTheScheduleNamesThem() throws {
        let index = try XCTUnwrap(PlayaEventIndex.load())
        let dated = index.days.filter { !$0.date.isEmpty }
        XCTAssertGreaterThan(dated.count, 6)
        for day in dated {
            let first = day.name.components(separatedBy: " ").first ?? ""
            XCTAssertTrue(isScheduleDay(first),
                          "\"\(day.name)\" does not start with a weekday the "
                          + "schedule screen would recognise")
        }
    }

    /// The 32 events the listing gives no date for are kept, in a section of
    /// their own. Robot Heart's DJ sets are among them, and an earlier cut of
    /// the converter dropped all 32 without saying so.
    func testUndatedEventsAreKeptRatherThanDropped() throws {
        let index = try XCTUnwrap(PlayaEventIndex.load())
        let last = try XCTUnwrap(index.days.last)
        XCTAssertEqual(last.date, "")
        XCTAssertFalse(last.rows.isEmpty)
        for row in last.rows {
            XCTAssertEqual(row.when, "All day")
        }
    }

    /// 48 listings carry a date and no clock time of their own. They must
    /// survive, and they must sort above the timed ones on their day. An
    /// earlier cut of the converter required a time and lost every one.
    func testAllDayListingsSurviveAndSortFirst() throws {
        let index = try XCTUnwrap(PlayaEventIndex.load())
        let dated = index.days.filter { !$0.date.isEmpty }
        let allDay = dated.flatMap { $0.rows }.filter { $0.at == nil }
        XCTAssertGreaterThan(allDay.count, 40,
                             "the all-day listings were dropped again")
        for day in dated {
            let times = day.rows.map { $0.at ?? "" }
            XCTAssertEqual(times, times.sorted(),
                           "\(day.name) is not in time order")
        }
    }

    /// The guide repeats its own slots -- 173 of 8,828 dated ones. Undeduped,
    /// "Dusi Bubbly Rose" drew four identical All-day rows under one Sunday,
    /// which reads as a rendering bug rather than as a listing.
    func testNoDayShowsTheSameEventTwiceAtTheSameTime() throws {
        let index = try XCTUnwrap(PlayaEventIndex.load())
        for day in index.days {
            var seen = Set<String>()
            for row in day.rows {
                let key = "\(row.event)@\(row.when)"
                XCTAssertFalse(seen.contains(key),
                               "\(day.name) lists "
                               + "\(index.events[row.event].title) twice at "
                               + "\(row.when)")
                seen.insert(key)
            }
        }
    }

    /// A bare date beside a real time on the same day is the same listing
    /// written twice, once without its hour -- not a separate all-day one.
    func testAnAllDayRowNeverSitsBesideATimedOneForTheSameEvent() throws {
        let index = try XCTUnwrap(PlayaEventIndex.load())
        for day in index.days {
            let allDay = Set(day.rows.filter { $0.at == nil }.map(\.event))
            let timed = Set(day.rows.filter { $0.at != nil }.map(\.event))
            XCTAssertTrue(allDay.isDisjoint(with: timed),
                          "\(day.name) shows an event as both all-day and timed")
        }
    }

    /// The events file is the guide's, and the guide repeats itself: 3,382 of
    /// its descriptions are the same text twice over. The converter cuts an
    /// exact doubling and leaves anything else alone.
    func testDescriptionsAreNotDoubled() throws {
        let index = try XCTUnwrap(PlayaEventIndex.load())
        var doubled = 0
        for event in index.events {
            guard let about = event.about, about.count > 40,
                  about.count % 2 == 0 else { continue }
            let half = about.index(about.startIndex, offsetBy: about.count / 2)
            if about[..<half] == about[half...] { doubled += 1 }
        }
        XCTAssertEqual(doubled, 0, "\(doubled) descriptions are still doubled")
    }

    /// The map pages are saved DOM. An address read straight out of them
    /// arrives as `9:15 &amp; G` and renders with the entity showing.
    func testNoHtmlEntitiesSurvivedIntoTheListings() throws {
        let camps: [String: [PlayaCamp]] = try XCTUnwrap(Playa.decode(Playa.campsFile))
        for camp in camps["camps"] ?? [] {
            XCTAssertFalse(camp.address.contains("&amp;"),
                           "\(camp.name) kept an HTML entity: \(camp.address)")
            XCTAssertFalse(camp.name.contains("&#"), camp.name)
        }
        // The art page is the same saved DOM through the same unescape.
        let art: [String: [PlayaArt]] = try XCTUnwrap(Playa.decode(Playa.artFile))
        for piece in art["art"] ?? [] {
            XCTAssertFalse(piece.name.contains("&amp;"), piece.name)
            XCTAssertFalse(piece.artist.contains("&amp;"), piece.artist)
            XCTAssertFalse(piece.name.contains("&#"), piece.name)
        }
        // `A&mpersand` is a real camp name, not a half-unescaped one: the
        // source says `A&amp;mpersand`. Kept here so nobody "fixes" it.
        XCTAssertTrue(camps["camps"]?.contains { $0.name == "A&mpersand" } ?? false)
    }

    /// A phrase that only ever appears in the guide's model-written summaries,
    /// never in the official listing text. If this starts matching, `sm` has
    /// been wired back in and the app is showing sentences nobody said.
    func testTheGuidesGeneratedSummariesAreNotShipped() throws {
        let index = try XCTUnwrap(PlayaEventIndex.load())
        // `sm` for the first event is "Hip hop music and party event."
        let first = try XCTUnwrap(index.events.first)
        XCTAssertNotEqual(first.about, "Hip hop music and party event.")
    }

    // MARK: - Opening state

    /// 8,843 listings cannot open flat the way 52 shifts do.
    func testTodayIsTheDayLeftOpenDuringTheBurn() throws {
        let index = try XCTUnwrap(PlayaEventIndex.load())
        var parts = DateComponents()
        parts.year = 2026; parts.month = 9; parts.day = 2
        let wednesday = try XCTUnwrap(Calendar.current.date(from: parts))
        let opened = index.openingDay(today: wednesday)
        XCTAssertEqual(index.days[opened].date, "09-02")
    }

    /// Off-season it falls back to the first day worth opening on, which is
    /// NOT the first day in the file: that one carries a single LNT training
    /// session three days before the gate.
    func testOffSeasonItSkipsTheNearlyEmptyLeadInDays() throws {
        let index = try XCTUnwrap(PlayaEventIndex.load())
        var parts = DateComponents()
        parts.year = 2027; parts.month = 3; parts.day = 3
        let offSeason = try XCTUnwrap(Calendar.current.date(from: parts))
        let opened = index.openingDay(today: offSeason)
        XCTAssertGreaterThanOrEqual(index.days[opened].rows.count,
                                    PlayaEventIndex.quiet)
        XCTAssertLessThan(index.days[0].rows.count, PlayaEventIndex.quiet,
                          "the lead-in day filled up; this test is now vacuous")
        XCTAssertEqual(index.days[opened].date, "08-30",
                       "off-season should open on the day the gate opens")
    }

    // MARK: - Hours and categories

    /// A 21:30 start files under 21:00; an all-day row files under no hour.
    /// Every time in the file is zero-padded HH:MM -- verified across all of
    /// them here, because the screen sorts hours by plain string comparison
    /// and "9:00" would sort after "10:00".
    func testEveryTimeIsPaddedAndFilesUnderItsHour() throws {
        let index = try XCTUnwrap(PlayaEventIndex.load())
        for day in index.days {
            for row in day.rows {
                guard let at = row.at else {
                    XCTAssertNil(row.hourLabel)
                    continue
                }
                XCTAssertTrue(at.range(of: #"^\d{2}:\d{2}$"#,
                                       options: .regularExpression) != nil,
                              "unpadded time \(at) breaks hour sorting")
                XCTAssertEqual(row.hourLabel, at.prefix(2) + ":00")
            }
        }
    }

    /// The chip row carries the listing's real categories and none of its
    /// six one-off strays -- "tea" and "running-order-only" are typos of
    /// taxonomy, not categories anyone would filter by.
    func testTheFilterChipsAreTheRealCategories() throws {
        let index = try XCTUnwrap(PlayaEventIndex.load())
        XCTAssertEqual(index.filters.first, "music",
                       "chips are ordered by use, and music is the most used")
        for expected in ["workshop", "party", "food", "kids", "parade"] {
            XCTAssertTrue(index.filters.contains(expected), expected)
        }
        for stray in ["tea", "yoga", "running-order-only", "beverages"] {
            XCTAssertFalse(index.filters.contains(stray),
                           "\(stray) is used once in 4,224 and gets no chip")
        }
    }

    func testFilteringByTagReachesTheEventsTags() throws {
        let index = try XCTUnwrap(PlayaEventIndex.load())
        let rows = index.days.flatMap(\.rows)
        let music = rows.filter { index.matches($0, tag: "music") }
        XCTAssertGreaterThan(music.count, 1000)
        XCTAssertLessThan(music.count, rows.count)
        for row in music.prefix(50) {
            XCTAssertTrue(index.events[row.event].tags.contains("music"))
        }
        // nil is "All": everything passes.
        XCTAssertEqual(rows.filter { index.matches($0, tag: nil) }.count,
                       rows.count)
    }

    /// Mid-burn the day opens at the current hour; off-season there is no
    /// cut, because the whole day is future.
    func testTheOpeningHourExistsOnlyDuringTheBurn() throws {
        let index = try XCTUnwrap(PlayaEventIndex.load())
        var parts = DateComponents()
        parts.year = 2026; parts.month = 9; parts.day = 2
        parts.hour = 21; parts.minute = 15
        let evening = try XCTUnwrap(Calendar.current.date(from: parts))
        XCTAssertEqual(index.openingHour(today: evening), "21:00")

        parts.year = 2027; parts.month = 3; parts.day = 3
        let offSeason = try XCTUnwrap(Calendar.current.date(from: parts))
        XCTAssertNil(index.openingHour(today: offSeason))
    }

    // MARK: - The listing screen

    func testSectionsGroupUnderTheirInitialAndKeepOrder() {
        let rows = ["8-bit Bunny", "Airpusher", "Alchemist", "Bike Gods"]
            .enumerated().map {
                PlayaListingRow(id: $0.offset, title: $0.element, subtitle: "x")
            }
        let sections = playaSections(rows)
        XCTAssertEqual(sections.map(\.id), ["#", "A", "B"])
        XCTAssertEqual(sections[1].rows.map(\.title), ["Airpusher", "Alchemist"])
    }

    /// Search has to reach the address, not just the name -- "where is
    /// everyone on 7:30" is a real question at a listing of 1,181.
    func testSearchReachesTheSubtitle() {
        let row = PlayaListingRow(id: 0, title: "Camp Juicy", subtitle: "4:45 & A")
        XCTAssertTrue(row.haystack.contains("4:45"))
        XCTAssertTrue(row.haystack.contains("juicy"))
    }

    func testCampsAndArtBothLoadFromTheBundle() throws {
        let camps = try XCTUnwrap(PlayaListingView.camps(face: Face.day).load())
        XCTAssertGreaterThan(camps.count, 1000)
        XCTAssertFalse(camps.contains { $0.title.isEmpty })

        let art = try XCTUnwrap(PlayaListingView.art(face: Face.day).load())
        XCTAssertGreaterThan(art.count, 300)
        XCTAssertFalse(art.contains { $0.title.isEmpty })
    }

    /// The saved DOM carried no descriptions at all -- the page bulk-fetches
    /// them after load -- so the converter reads playamap's API files
    /// instead. If this drops toward zero, the build fell back to the DOM
    /// scrape: the `api-art-*.json` files have gone missing from
    /// `Playa data/`.
    func testTheArtCarriesItsDescriptions() throws {
        let art = try XCTUnwrap(PlayaListingView.art(face: Face.day).load())
        let described = art.filter { !$0.about.isEmpty }
        XCTAssertEqual(described.count, art.count,
                       "\(art.count - described.count) pieces lost their "
                       + "description")
    }

    /// "mushroom" has to find the mushroom piece by what it is, not only by
    /// what it is called -- the description is in the haystack for that.
    func testArtSearchReachesTheDescription() {
        let row = PlayaListingRow(id: 0, title: "Nova", subtitle: "Someone",
                                  about: "A stellated dodecahedron, spinning.")
        XCTAssertTrue(row.haystack.contains("dodecahedron"))
    }

    /// Event search covers the camp and the lineup, not only the title. The
    /// haystack is built once at load and nothing else proves it is complete.
    func testEventSearchReachesCampAndLineup() throws {
        let index = try XCTUnwrap(PlayaEventIndex.load())
        let row = try XCTUnwrap(index.days.flatMap { $0.rows }.first {
            let e = index.events[$0.event]
            return !e.camp.isEmpty && !(e.who ?? "").isEmpty
        })
        let event = index.events[row.event]
        XCTAssertTrue(index.matches(row, event.camp.lowercased()))
        let dj = try XCTUnwrap(event.who?.components(separatedBy: ",").first)
        XCTAssertTrue(index.matches(row, dj.trimmingCharacters(in: .whitespaces)
                                        .lowercased()))
    }
}
