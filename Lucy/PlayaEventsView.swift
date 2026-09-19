import SwiftUI

// Everything happening in Black Rock City, by day, by hour.
//
// Modelled on `ScheduleView` and different from it in two deliberate ways.
// Every day starts FOLDED except one: the camp's own schedule opens flat
// because 52 shifts read as a week you can scroll, and 8,655 listings do not.
// And rows group under HOUR headings instead of carrying a time column --
// the column reserved 92pt of every row for text that repeats its own first
// five characters down the whole screen.
//
// Mid-burn, the open day is today and it opens AT the current hour, with the
// morning behind an "Earlier today" pill -- the only question anyone asks
// this screen in the dust is "what is on right now", and midnight's listings
// were 15 screens of scrolling above the answer.
//
// Tapping an event opens it in place rather than pushing a screen: the
// description is a sentence or two and losing your place in a 1,600-event day
// to read it would be the worse trade.

struct PlayaEventsView: View {
    let face: Face
    /// Renders only: seeds the search box and opens the first hit, so a
    /// screenshot shows a result with its description rather than a column of
    /// folded day headings. Real use never sets it.
    var previewQuery: String?
    var previewExpandsFirst = false
    /// Renders only: a fixed "now", so a screenshot of the today-cut shows
    /// the same thing tomorrow. Real use never sets it.
    var previewNow: Date?

    @Environment(\.dismiss) private var dismiss
    @State private var index: PlayaEventIndex?
    @State private var failed = false
    @State private var query = ""
    /// One category or none. Chips are single-choice on purpose: "music OR
    /// party" is what search is for, and a multi-select chip row needs a
    /// clear-all affordance this screen would then have to explain.
    @State private var tag: String?
    /// Open, not folded -- the inverse of `ScheduleView`, because here the
    /// exceptions are the days you want rather than the days you are done
    /// with. Seeded once from `openingDay()`.
    @State private var open: Set<Int> = []
    @State private var seeded = false
    @State private var expanded: Set<Int> = []
    /// The hour today opened at, and the day it applies to. Nil off-season.
    @State private var cutHour: String?
    @State private var todayDay: Int?
    /// Days whose "Earlier today" pill has been tapped.
    @State private var revealed: Set<Int> = []
    /// Folded hour groups, keyed "day-hour". Hours fold separately from
    /// days: a day is 1,700 rows, and "not the workshops, get me to the
    /// evening" is a real way to read one.
    @State private var foldedHours: Set<String> = []

    private var searching: Bool {
        !query.trimmingCharacters(in: .whitespaces).isEmpty
    }

    /// Days with their rows filtered by the search box and the chip row
    /// together. A day with no hit is dropped entirely, so searching "yoga"
    /// gives the days that have yoga rather than twelve headings you have to
    /// open one at a time to find out.
    private var shown: [PlayaEventIndex.Day] {
        guard let index else { return [] }
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        guard !q.isEmpty || tag != nil else { return index.days }
        return index.days.compactMap { day in
            let rows = day.rows.filter {
                (q.isEmpty || index.matches($0, q)) && index.matches($0, tag: tag)
            }
            guard !rows.isEmpty else { return nil }
            return PlayaEventIndex.Day(id: day.id, name: day.name,
                                       date: day.date, rows: rows)
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            if index != nil {
                search
                chips
            }
            content
        }
        .padding(22)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(face.ground)
        .preferredColorScheme(face.night ? .dark : .light)
        .task {
            guard index == nil, !failed else { return }
            let loaded = await Task.detached(priority: .userInitiated) {
                PlayaEventIndex.load()
            }.value
            guard let loaded else { failed = true; return }
            index = loaded
            if !seeded {
                seeded = true
                let now = previewNow ?? Date()
                let day = loaded.openingDay(today: now)
                open = [day]
                // Non-nil only when today is a listed day, in which case
                // `openingDay` returned today and the cut belongs to it.
                if let hour = loaded.openingHour(today: now) {
                    cutHour = hour
                    todayDay = day
                }
                if let previewQuery { query = previewQuery }
                if previewExpandsFirst {
                    let q = (previewQuery ?? "").lowercased()
                    let hit = loaded.days.flatMap(\.rows).first {
                        q.isEmpty || loaded.matches($0, q)
                    }
                    if let hit { expanded = [hit.id] }
                }
            }
        }
    }

    @ViewBuilder
    private var content: some View {
        if failed {
            Text("The city's event listing did not make it into this build.")
                .font(.body_(16)).foregroundStyle(face.muted)
                .padding(.top, 18)
            Spacer()
        } else if index == nil {
            HStack(spacing: 10) {
                ProgressView().tint(face.muted)
                Text("Reading four thousand events…")
                    .font(.data(13)).foregroundStyle(face.muted)
            }
            .padding(.top, 18)
            Spacer()
        } else if shown.isEmpty {
            Text("Nothing in the listing matches that.")
                .font(.body_(16)).foregroundStyle(face.muted)
                .padding(.top, 18)
            Spacer()
        } else {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    ForEach(shown) { day in
                        dayHeading(day)
                        // A search result is always shown open. Hiding a hit
                        // behind a fold you have to remember to tap would
                        // read as "not on the listing". A chip filter keeps
                        // the folds: category counts on the day headings are
                        // the overview, and one tap opens the day you want.
                        if searching || open.contains(day.id) {
                            dayBody(day)
                        }
                    }
                }
                .padding(.bottom, 40)
            }
            .scrollIndicators(.hidden)
        }
    }

    // MARK: - One day

    /// A day's rows, grouped under their hour. The converter already sorted
    /// each day by start time with the all-day rows first, so the groups fall
    /// out of one pass over consecutive labels.
    private struct HourGroup: Identifiable {
        let label: String
        let rows: [PlayaEventIndex.Row]
        var id: String { label }
    }

    private func hourGroups(_ rows: [PlayaEventIndex.Row]) -> [HourGroup] {
        var out: [HourGroup] = []
        for row in rows {
            let label = row.hourLabel ?? "All day"
            if out.last?.label == label {
                out[out.count - 1] = HourGroup(label: label,
                                               rows: out[out.count - 1].rows + [row])
            } else {
                out.append(HourGroup(label: label, rows: [row]))
            }
        }
        return out
    }

    @ViewBuilder
    private func dayBody(_ day: PlayaEventIndex.Day) -> some View {
        let groups = hourGroups(day.rows)
        // Mid-burn, today opens at the current hour. The all-day group stays
        // above the cut -- those listings are as "on right now" as anything
        // -- and a filtered or searched view never cuts, because then the
        // person said what they want and hiding some of it would read as
        // "not on the listing".
        let cutting = day.id == todayDay && cutHour != nil
            && !revealed.contains(day.id) && !searching && tag == nil
        let kept = cutting
            ? groups.filter { $0.label == "All day" || $0.label >= cutHour! }
            : groups
        let hidden = groups.reduce(0) { $0 + $1.rows.count }
            - kept.reduce(0) { $0 + $1.rows.count }

        if cutting && hidden > 0 {
            Button { revealed.insert(day.id) } label: {
                Text("Earlier today · \(hidden)")
                    .font(.data(13)).foregroundStyle(face.muted)
                    .padding(.horizontal, 12).padding(.vertical, 6)
                    .background(Capsule().fill(face.muted.opacity(0.12)))
            }
            .buttonStyle(.plain)
            .padding(.top, 6).padding(.bottom, 2)
        }
        ForEach(kept) { group in
            hourHeading(group, day: day)
            if searching || !foldedHours.contains(hourKey(day, group)) {
                ForEach(group.rows) { row in
                    entry(row)
                }
            }
        }
    }

    private func hourKey(_ day: PlayaEventIndex.Day, _ group: HourGroup) -> String {
        "\(day.id)-\(group.label)"
    }

    /// An hour: a rule right across the page with the time sitting on it,
    /// foldable on its own. The rule is what separates 21:00's fourteen
    /// events from 22:00's -- without it the hours read as one undivided
    /// column and the heading as just another row.
    private func hourHeading(_ group: HourGroup,
                             day: PlayaEventIndex.Day) -> some View {
        let key = hourKey(day, group)
        let isFolded = !searching && foldedHours.contains(key)
        return Button {
            if foldedHours.contains(key) { foldedHours.remove(key) }
            else { foldedHours.insert(key) }
        } label: {
            HStack(spacing: 10) {
                Text(group.label)
                    .font(.data(15)).foregroundStyle(face.warm)
                Rectangle()
                    .fill(face.muted.opacity(0.25))
                    .frame(height: 1)
                Image(systemName: "chevron.down")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(face.muted)
                    .rotationEffect(.degrees(isFolded ? -90 : 0))
                if isFolded {
                    Text("\(group.rows.count)")
                        .font(.data(12)).foregroundStyle(face.muted)
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(searching)
        .padding(.top, 18).padding(.bottom, 4)
    }

    private func dayHeading(_ day: PlayaEventIndex.Day) -> some View {
        let isOpen = searching || open.contains(day.id)
        return Button {
            if open.contains(day.id) { open.remove(day.id) }
            else { open.insert(day.id) }
        } label: {
            HStack(spacing: 8) {
                Text(day.name)
                    .font(.display(24)).foregroundStyle(face.accent)
                Image(systemName: "chevron.down")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(face.muted)
                    .rotationEffect(.degrees(isOpen ? 0 : -90))
                Spacer(minLength: 0)
                Text("\(day.rows.count)")
                    .font(.data(12)).foregroundStyle(face.muted)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(searching)
        .padding(.top, 22).padding(.bottom, 8)
    }

    // MARK: - One event

    @ViewBuilder
    private func entry(_ row: PlayaEventIndex.Row) -> some View {
        if let event = index?.events[row.event] {
            let isOpen = expanded.contains(row.id)
            Button {
                if isOpen { expanded.remove(row.id) } else { expanded.insert(row.id) }
            } label: {
                VStack(alignment: .leading, spacing: 2) {
                    Text(event.title)
                        .font(.body_(18)).foregroundStyle(face.text)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                    meta(row, event)
                        .font(.data(13))
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                    if isOpen {
                        detail(event)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.vertical, 7)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
    }

    /// "21:30–23:00 · Camp X · 4:45 & A", the exact span leading because the
    /// hour heading only says where the row is filed. Concatenated `Text`s
    /// rather than an HStack so a long camp name wraps as one line of prose.
    private func meta(_ row: PlayaEventIndex.Row, _ event: PlayaEvent) -> Text {
        let place = place(event)
        guard row.at != nil else {
            return Text(place).foregroundColor(face.muted)
        }
        guard !place.isEmpty else {
            return Text(row.when).foregroundColor(face.warm)
        }
        return Text(row.when).foregroundColor(face.warm)
            + Text(" · \(place)").foregroundColor(face.muted)
    }

    @ViewBuilder
    private func detail(_ event: PlayaEvent) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            if let about = event.about, !about.isEmpty {
                Text(about)
                    .font(.body_(15)).foregroundStyle(face.muted)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
            }
            if let who = event.who, !who.isEmpty {
                Text(who)
                    .font(.data(13)).foregroundStyle(face.warm)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if !event.tags.isEmpty {
                Text(event.tags.joined(separator: " · "))
                    .font(.data(12)).foregroundStyle(face.muted.opacity(0.8))
            }
            if event.about == nil && event.who == nil && event.tags.isEmpty {
                Text("The listing says no more than that.")
                    .font(.data(13)).foregroundStyle(face.muted.opacity(0.8))
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.top, 5)
    }

    /// Camp and address, whichever of the two the listing gave.
    private func place(_ event: PlayaEvent) -> String {
        [event.camp, event.address]
            .filter { !$0.isEmpty }
            .joined(separator: " · ")
    }

    // MARK: - Chrome

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(Style.current.label("Events"))
                .font(.eyebrow(15))
                .tracking(Style.current.eyebrowTracking + Style.current.boost(0.5))
                .foregroundStyle(face.accent)
            Spacer()
            if !searching, index != nil, !open.isEmpty {
                Button { open = [] } label: {
                    Text("Fold all")
                        .font(.data(12)).foregroundStyle(face.muted)
                }
                .buttonStyle(.plain)
                .padding(.trailing, 14)
            }
            Button { dismiss() } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(face.muted)
            }
            .buttonStyle(.plain)
        }
        .padding(.bottom, 14)
    }

    private var search: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 13)).foregroundStyle(face.muted)
            TextField("Search a camp, a DJ, or a thing to do", text: $query)
                .font(.body_(15)).foregroundStyle(face.text)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
            if !query.isEmpty {
                Button { query = "" } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: 14)).foregroundStyle(face.muted)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 12).padding(.vertical, 9)
        .background(RoundedRectangle(cornerRadius: 10)
            .fill(face.muted.opacity(0.12)))
        .padding(.bottom, 10)
    }

    /// The category chips, from the listing's own tags. One row, scrolled
    /// sideways: 18 chips stacked would be half the screen before any event.
    private var chips: some View {
        ScrollView(.horizontal) {
            HStack(spacing: 8) {
                chip("All", on: tag == nil) { tag = nil }
                ForEach(index?.filters ?? [], id: \.self) { t in
                    chip(pretty(t), on: tag == t) {
                        tag = tag == t ? nil : t
                    }
                }
            }
        }
        .scrollIndicators(.hidden)
        .padding(.bottom, 4)
    }

    private func chip(_ label: String, on: Bool,
                      tap: @escaping () -> Void) -> some View {
        Button(action: tap) {
            Text(label)
                .font(.data(13))
                .foregroundStyle(on ? face.ground : face.muted)
                .padding(.horizontal, 12).padding(.vertical, 6)
                .background(Capsule().fill(on ? face.accent
                                              : face.muted.opacity(0.12)))
        }
        .buttonStyle(.plain)
    }

    /// "live-music" reads as a database key; the chip says "Live music".
    private func pretty(_ tag: String) -> String {
        tag.replacingOccurrences(of: "-", with: " ").capitalized
    }
}
