import Foundation

// What Black Rock City is doing this year, as data rather than as prose.
//
// The manual, the build guide and the schedule are markdown, because they are
// things the camp WROTE. These three are things the city PUBLISHED: 4,224
// events over 8,655 occurrences, 1,181 camps and 326 art pieces, all records
// with the same fields.
// Rendering 4,224 events as markdown and parsing the camp and the time back
// out at open time would be re-deriving what we already had.
//
// Built by `scripts/playa_data.py` out of three webapps saved to `Playa
// data/`. Nothing here is model-written: the guide ships a per-event summary
// its author generated with a model and the converter drops it, because a
// screen in THIS app is not a place to put a sentence nobody said.
//
// Loading is deliberately explicit and off the main actor -- the events file
// is 1.1 MB and decoding it on the Info tab's draw would stall the tab. The
// Info captions come from `playa-counts.json`, a few hundred bytes written by
// the same script from the same data, so they cost nothing and still cannot
// drift.

// MARK: - The files

struct PlayaEvent: Decodable {
    let title: String
    let camp: String
    let address: String
    let tags: [String]
    /// The lineup, where the listing named one.
    let who: String?
    /// The listing's own description. Absent for 384 of the 4,224.
    let about: String?
}

/// One occurrence of an event. `at` is absent for the 48 all-day listings and
/// for the 32 the guide gives no date for at all.
struct PlayaSlot: Decodable {
    let e: Int
    let at: String?
    let to: String?
}

struct PlayaDay: Decodable {
    let name: String
    /// `MM-dd`, or empty for the "No date given" section at the end.
    let date: String
    let slots: [PlayaSlot]
}

struct PlayaEventsFile: Decodable {
    let events: [PlayaEvent]
    let days: [PlayaDay]
}

struct PlayaCamp: Decodable {
    let name: String
    let address: String
}

struct PlayaArt: Decodable {
    let name: String
    let artist: String
    /// The piece's own description, from playamap's API. The saved DOM had
    /// none of these -- the page bulk-fetches them after load -- so they were
    /// re-fetched from the API on 2026-08-26. Locations are still absent:
    /// the city does not release those until the Sunday.
    let about: String?
}

// MARK: - Counts

/// Read on the Info tab to caption the three rows. Its own file for one
/// reason: counting the events means decoding 1.1 MB, and the Info tab is
/// drawn on every launch.
struct PlayaCounts: Decodable {
    struct Events: Decodable {
        let events: Int
        let occurrences: Int
        let days: Int
    }
    struct Camps: Decodable { let camps: Int }
    struct Art: Decodable { let art: Int }

    let year: Int
    let events: Events
    let camps: Camps
    let art: Art

    /// Decoded once. A miss is cached too -- a bundle that dropped the file
    /// will not start carrying it halfway through a run, and retrying the
    /// lookup on every draw only hides the fact that it is missing.
    private static let loaded: PlayaCounts? = Playa.decode("playa-counts")
    static var shared: PlayaCounts? { loaded }
}

// MARK: - Loading

enum Playa {
    /// Reads a bundled JSON resource.
    ///
    /// Returns nil rather than trapping, and every caller has a "not in this
    /// build" path, because xcodegen drops a misplaced resource entry without
    /// saying so -- which is why `InfoView.soon` exists at all.
    static func decode<T: Decodable>(_ name: String) -> T? {
        guard let url = Bundle.main.url(forResource: name, withExtension: "json"),
              let data = try? Data(contentsOf: url)
        else { return nil }
        return try? JSONDecoder().decode(T.self, from: data)
    }

    /// Is the resource in this build at all? Cheap: no decode, so the Info
    /// tab can ask before offering a row.
    static func has(_ name: String) -> Bool {
        Bundle.main.url(forResource: name, withExtension: "json") != nil
    }

    static let eventsFile = "playa-events-2026"
    static let campsFile = "playa-camps-2026"
    static let artFile = "playa-art-2026"
}

// MARK: - The events, indexed for the screen

/// The events file with the two things the screen needs and the file does not
/// carry: a lowercased haystack per event, and the days flattened into rows.
///
/// The haystack is built once, per EVENT rather than per occurrence -- 4,224
/// strings instead of 8,843, because an event that runs six days is the same
/// text six times over.
struct PlayaEventIndex {
    struct Row: Identifiable {
        let id: Int
        let event: Int
        let at: String?
        let to: String?

        /// "21:00–01:00", "14:00", or "All day". No spaces around the dash:
        /// spaced, it is 13 monospaced characters and wraps the time column
        /// onto two lines, which leaves the place line floating below a gap.
        var when: String {
            guard let at, !at.isEmpty else { return "All day" }
            guard let to, !to.isEmpty else { return at }
            return "\(at)–\(to)"
        }

        /// The hour this row files under -- "21:00" for a 21:30 start -- or
        /// nil for an all-day listing. Every time in the file is zero-padded
        /// HH:MM (verified across all 8,575), so the prefix comparison the
        /// screen sorts these with is safe.
        var hourLabel: String? {
            guard let at, at.count >= 2 else { return nil }
            return at.prefix(2) + ":00"
        }
    }

    struct Day: Identifiable {
        let id: Int
        let name: String
        let date: String
        let rows: [Row]
    }

    let events: [PlayaEvent]
    let days: [Day]
    /// The tags worth a filter chip, most-used first. Cut at 40 uses: the
    /// listing has 18 real categories (music at 1,672 down to parade at 41)
    /// and then six strays used exactly once each -- "tea", "beverages",
    /// "running-order-only" -- which are typos of taxonomy, not categories.
    /// The strays stay searchable through the search box; they just do not
    /// each get a chip.
    let filters: [String]
    private let haystacks: [String]

    init(_ file: PlayaEventsFile) {
        events = file.events
        var tagCount: [String: Int] = [:]
        for e in file.events {
            for t in e.tags { tagCount[t, default: 0] += 1 }
        }
        filters = tagCount.filter { $0.value >= 40 }
            .sorted { $0.value == $1.value ? $0.key < $1.key : $0.value > $1.value }
            .map(\.key)
        // The description is in here too. It is another 500 KB lowercased and
        // it is worth it: "pancakes" and "bike repair" are how people look for
        // a thing to do, and neither is in any title.
        haystacks = file.events.map { e in
            ([e.title, e.camp, e.address, e.who ?? "", e.about ?? ""] + e.tags)
                .joined(separator: " ")
                .lowercased()
        }
        var id = 0
        var out: [Day] = []
        for (i, day) in file.days.enumerated() {
            var rows: [Row] = []
            for slot in day.slots where slot.e >= 0 && slot.e < file.events.count {
                rows.append(Row(id: id, event: slot.e, at: slot.at, to: slot.to))
                id += 1
            }
            out.append(Day(id: i, name: day.name, date: day.date, rows: rows))
        }
        days = out
    }

    /// Loads and indexes. Call from a background context: this decodes 1.1 MB.
    static func load() -> PlayaEventIndex? {
        guard let file: PlayaEventsFile = Playa.decode(Playa.eventsFile),
              !file.events.isEmpty
        else { return nil }
        return PlayaEventIndex(file)
    }

    func matches(_ row: Row, _ query: String) -> Bool {
        haystacks[row.event].contains(query)
    }

    func matches(_ row: Row, tag: String?) -> Bool {
        guard let tag else { return true }
        return events[row.event].tags.contains(tag)
    }

    /// A day has to be worth opening on. The listing runs from a stray
    /// training session on the Thursday before the gate through to the Monday
    /// after, and the first two days carry one event each.
    static let quiet = 10

    /// The day to leave open on arrival: today if the burn is on, else the
    /// first day the city is actually doing something.
    ///
    /// Everything else starts folded -- unlike the camp's own schedule, which
    /// opens flat because 52 shifts fit that way. 8,843 listings do not, and a
    /// screen that opens on all of them is a screen nobody scrolls to the
    /// bottom of.
    ///
    /// Falling back to day 0 was the first cut and it opened on "Thursday 27
    /// August", one LNT training session, three days before the gate. Right
    /// by the rule and useless on the screen.
    func openingDay(today: Date = Date()) -> Int {
        let f = DateFormatter()
        f.dateFormat = "MM-dd"
        let stamp = f.string(from: today)
        if let match = days.firstIndex(where: { $0.date == stamp }) {
            return match
        }
        return days.firstIndex { $0.rows.count >= Self.quiet } ?? 0
    }

    /// The hour to open the day AT, or nil to open it from the top.
    ///
    /// Non-nil only when today is a listed day: mid-burn, a screen that opens
    /// on midnight's listings is 15 taps of scrolling from "what is on right
    /// now", which is the only question anyone is asking it. Off-season the
    /// whole day is future, so the top is right.
    func openingHour(today: Date = Date()) -> String? {
        let f = DateFormatter()
        f.dateFormat = "MM-dd"
        guard days.contains(where: { $0.date == f.string(from: today) })
        else { return nil }
        let hour = Calendar.current.component(.hour, from: today)
        return String(format: "%02d:00", hour)
    }
}
