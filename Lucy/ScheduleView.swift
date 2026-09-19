import SwiftUI

// The camp's shifts, on one page, with each day foldable.
//
// Deliberately NOT `DocumentView`. That screen collapses a 63,000-character
// manual into sixty headings you tap open, which is right for a reference you
// arrive at knowing what you want. A schedule is read the other way round:
// you scroll to your day and look down it. So everything starts OPEN and the
// whole week is one scroll -- folding is there to get a finished day out of
// the way, not to make you hunt for the day you want.
//
// Nothing here is generated. The file is the camp's own sign-up sheet,
// converted from the Google Sheets export -- no model in the path, so there
// is no invariant to worry about.

/// One rendered block. The source is markdown-ish: headings, bullets nested
/// one level, bold run-in labels, and plain paragraphs. Inline `**bold**` and
/// `*italic*` are left to `AttributedString`, so this only has to decide what
/// KIND of block a line is, never how to style the words inside it.
struct ScheduleBlock: Identifiable {
    enum Kind {
        case title          // "# PS 2026 Shifts"
        case section        // "# Schedule", "## What's Involved In A Shift"
        case day            // "## Sunday"
        case group          // "### Camp Maintenance"
        case event          // "* Veg Curry, ~5-9p"
        case detail         // "    * Dinner: Amara, Jess"
        case bullet         // "- Empty trash and recycling"
        case lead           // "**Trash, recycling and ice**" or "At camp:"
        case paragraph
    }
    let id: Int
    let kind: Kind
    let text: String

    /// Days and groups own the blocks beneath them and can be folded.
    /// Titles and section rules are separators and always show.
    var isFoldable: Bool { kind == .day || kind == .group }
}

/// A foldable heading and everything under it. `heading == nil` is the
/// material before the first one, which must not be swallowed: it is the
/// "sign up for 3 shifts" line.
struct ScheduleGroup: Identifiable {
    let id: Int
    let heading: ScheduleBlock?
    let body: [ScheduleBlock]
    /// Separators that render above the heading, in order.
    let preceding: [ScheduleBlock]
}

/// Parses the shifts file into blocks.
///
/// The two halves use different markup because they came from two different
/// sheets: the schedule writes events as `*` with four-space nested `*`, and
/// What's Involved writes `-` bullets under `**bold**` leads. Rather than
/// normalise the file and lose the distinction, this reads both -- the nesting
/// depth of a `*` is what separates an event from its roster.
func parseSchedule(_ text: String) -> [ScheduleBlock] {
    var out: [ScheduleBlock] = []

    func add(_ kind: ScheduleBlock.Kind, _ s: String) {
        let trimmed = s.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { return }
        out.append(ScheduleBlock(id: out.count, kind: kind, text: trimmed))
    }

    for raw in text.components(separatedBy: .newlines) {
        let line = raw.trimmingCharacters(in: .whitespaces)
        if line.isEmpty { continue }

        if line.hasPrefix("### ") {
            add(.group, String(line.dropFirst(4)))
        } else if line.hasPrefix("## ") {
            let title = String(line.dropFirst(3))
            // A day is a day; anything else at this level is a section head.
            add(isScheduleDay(title) ? .day : .section, title)
        } else if line.hasPrefix("# ") {
            let title = String(line.dropFirst(2))
            add(out.isEmpty ? .title : .section, title)
        } else if raw.hasPrefix("    * ") || raw.hasPrefix("  * ") {
            // Indented: the people on an event.
            add(.detail, String(line.dropFirst(2)))
        } else if line.hasPrefix("* ") {
            add(.event, String(line.dropFirst(2)))
        } else if line.hasPrefix("- ") {
            add(.bullet, String(line.dropFirst(2)))
        } else if line.hasPrefix("**") && line.hasSuffix("**") {
            add(.lead, String(line.dropFirst(2).dropLast(2)))
        } else if line.hasSuffix(":") {
            add(.lead, line)
        } else if let last = out.last, last.kind == .paragraph {
            // A hard-wrapped sentence is one paragraph, not two. Splitting it
            // drew "...at least 1 maintenance or" and "cooking shift." as two
            // separate blocks with a gap between them.
            out[out.count - 1] = ScheduleBlock(id: last.id, kind: .paragraph,
                                               text: last.text + " " + line)
        } else {
            add(.paragraph, line)
        }
    }
    return out
}

/// Folds a flat block list into foldable groups.
func groupSchedule(_ blocks: [ScheduleBlock]) -> [ScheduleGroup] {
    var out: [ScheduleGroup] = []
    var heading: ScheduleBlock?
    var body: [ScheduleBlock] = []
    var preceding: [ScheduleBlock] = []

    /// Emits only when there is something to hang on the separators. A
    /// separator with nothing yet under it must STAY pending rather than be
    /// emitted and then carried forward, which drew the document title twice.
    func flush() {
        guard heading != nil || !body.isEmpty else { return }
        out.append(ScheduleGroup(id: out.count, heading: heading,
                                 body: body, preceding: preceding))
        heading = nil
        body = []
        preceding = []
    }

    for b in blocks {
        if b.isFoldable {
            flush()
            heading = b
        } else if b.kind == .title || b.kind == .section {
            // A separator closes the open group and rides above the next one.
            flush()
            preceding.append(b)
        } else {
            body.append(b)
        }
    }
    flush()
    // Separators trailing the last group would otherwise be dropped.
    if !preceding.isEmpty {
        out.append(ScheduleGroup(id: out.count, heading: nil,
                                 body: [], preceding: preceding))
    }
    return out
}

private let scheduleDayNames: Set<String> = [
    "sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday",
]

/// Internal rather than private so `PlayaDataTests` can hold the city's
/// event days to the same names. Two entries on one tab both answering "when
/// does the thing happen" should not head their days differently.
func isScheduleDay(_ s: String) -> Bool {
    scheduleDayNames.contains(s.trimmingCharacters(in: .whitespaces).lowercased())
}

struct ScheduleView: View {
    let face: Face
    let content: String
    /// Screenshots only: starts everything folded, so a render shows the
    /// week and the reference headings at once instead of one open Sunday.
    /// Real use never sets it.
    var previewStartsFolded: Bool = false

    @Environment(\.dismiss) private var dismiss
    @State private var query = ""
    /// Folded, not opened: everything starts open, so this holds the
    /// exceptions. An empty set is the default state and needs no seeding.
    @State private var folded: Set<Int> = []
    @State private var didPreview = false

    private var groups: [ScheduleGroup] { groupSchedule(parseSchedule(content)) }

    /// Search keeps a heading when the heading itself matches, or when
    /// anything under it does -- so searching your own name gives your week
    /// rather than seven empty days.
    private var shown: [ScheduleGroup] {
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        guard !q.isEmpty else { return groups }

        var out: [ScheduleGroup] = []
        for g in groups {
            let headingHit = g.heading?.text.lowercased().contains(q) ?? false
            if headingHit {
                out.append(g)
                continue
            }
            // Keep an event when it or any of its people match, so a matched
            // roster line never appears without the event it belongs to.
            var kept: [ScheduleBlock] = []
            var lastEvent: ScheduleBlock?
            for b in g.body {
                if b.kind == .event {
                    lastEvent = b
                    if b.text.lowercased().contains(q) {
                        kept.append(b)
                        lastEvent = nil
                    }
                } else if b.text.lowercased().contains(q) {
                    if let e = lastEvent { kept.append(e); lastEvent = nil }
                    kept.append(b)
                }
            }
            if !kept.isEmpty {
                out.append(ScheduleGroup(id: g.id, heading: g.heading,
                                         body: kept, preceding: g.preceding))
            }
        }
        return out
    }

    private var searching: Bool {
        !query.trimmingCharacters(in: .whitespaces).isEmpty
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            search
            if shown.isEmpty {
                Text("Nobody by that name is on the sheet.")
                    .font(.body_(16)).foregroundStyle(face.muted)
                    .padding(.top, 18)
                Spacer()
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(shown) { group in
                            ForEach(group.preceding) { sep in
                                row(sep)
                            }
                            if let h = group.heading {
                                foldableHeading(h, group: group)
                            }
                            // A search result is always shown open: hiding a
                            // hit behind a fold you have to remember to tap
                            // would read as "not on the sheet".
                            if searching || !folded.contains(group.id) {
                                ForEach(group.body) { b in
                                    row(b)
                                }
                            }
                        }
                    }
                    .padding(.bottom, 40)
                }
                .scrollIndicators(.hidden)
            }
        }
        .padding(22)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(face.ground)
        .preferredColorScheme(face.night ? .dark : .light)
        .onAppear {
            guard previewStartsFolded, !didPreview else { return }
            didPreview = true
            folded = Set(groups.compactMap { $0.heading == nil ? nil : $0.id })
        }
    }

    // MARK: - Pieces

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(Style.current.label("The schedule"))
                .font(.eyebrow(15))
                .tracking(Style.current.eyebrowTracking + Style.current.boost(0.5))
                .foregroundStyle(face.accent)
            Spacer()
            if !folded.isEmpty {
                Button { folded = [] } label: {
                    Text("Open all")
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
            TextField("Search a name or an event", text: $query)
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
        .padding(.bottom, 16)
    }

    private func foldableHeading(_ b: ScheduleBlock,
                                 group: ScheduleGroup) -> some View {
        let isFolded = !searching && folded.contains(group.id)
        let isDay = b.kind == .day
        return Button {
            if folded.contains(group.id) { folded.remove(group.id) }
            else { folded.insert(group.id) }
        } label: {
            HStack(spacing: 8) {
                Text(b.text)
                    .font(isDay ? .display(24) : .display(18))
                    .foregroundStyle(isDay ? face.accent : face.text)
                Image(systemName: "chevron.down")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(face.muted)
                    .rotationEffect(.degrees(isFolded ? -90 : 0))
                Spacer(minLength: 0)
                if isFolded {
                    Text("\(group.body.filter { $0.kind == .event }.count)")
                        .font(.data(12)).foregroundStyle(face.muted)
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(searching)
        .padding(.top, isDay ? 22 : 18)
        .padding(.bottom, isDay ? 8 : 6)
    }

    /// Inline `**bold**` and `*italic*` without a markdown library.
    private func styled(_ s: String) -> Text {
        if let a = try? AttributedString(markdown: s) { return Text(a) }
        return Text(s)
    }

    @ViewBuilder
    private func row(_ b: ScheduleBlock) -> some View {
        switch b.kind {
        case .title:
            Text(b.text)
                .font(.display(30)).foregroundStyle(face.text)
                .padding(.bottom, 6)

        case .section:
            Text(Style.current.label(b.text))
                .font(.eyebrow(12))
                .tracking(Style.current.eyebrowTracking)
                .foregroundStyle(face.muted)
                .padding(.top, 26).padding(.bottom, 4)

        case .day, .group:
            // Drawn by `foldableHeading`; never reached through `row`.
            EmptyView()

        case .event:
            styled(b.text)
                .font(.body_(17)).foregroundStyle(face.text)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, 10)

        case .detail:
            styled(b.text)
                .font(.data(14)).foregroundStyle(face.muted)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.leading, 14).padding(.top, 2)

        case .lead:
            styled(b.text)
                .font(.body_(15).weight(.semibold)).foregroundStyle(face.text)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, 12).padding(.bottom, 2)

        case .bullet:
            HStack(alignment: .top, spacing: 8) {
                Text("·").font(.body_(15)).foregroundStyle(face.muted)
                styled(b.text)
                    .font(.body_(15)).foregroundStyle(face.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.leading, 4).padding(.top, 3)

        case .paragraph:
            styled(b.text)
                .font(.body_(15)).foregroundStyle(face.muted)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, 6)
        }
    }
}
