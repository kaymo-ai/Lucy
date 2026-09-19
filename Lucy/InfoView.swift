import SwiftUI

// The camp's paperwork.
//
// The manual and the schedule are the two things people ask for that Lucy
// cannot usefully answer in a sentence -- "what time is dinner" wants a table,
// and "how do we strike Doris" wants the section, not a paraphrase of it.
// Both are here now; a row that cannot open says so plainly rather than
// showing an empty list that reads like a bug.
//
// Below them, under their own heading, are three listings that are not the
// camp's: the city's events, camps and art. Kept visibly separate because the
// distinction matters -- the manual is what PS wrote down and is answerable
// for, and those three are what Black Rock City published. Same tab, because
// "what is on tonight" and "what time is dinner" are the same errand; two
// headings, because they are not the same authority.
//
// There was a third row, "Shifts", which opened `RotaView` over `shift_grid`.
// The schedule replaced it: the rota was the same question asked of an older
// sheet, and two rows both promising "who is on what, and when" is a choice
// nobody should have to make. `shift_grid` and `EntityStore.rota()` still
// exist -- only the way in from this screen is gone.

/// What the sheet is currently showing. An `Identifiable` item rather than a
/// boolean plus three pieces of state, so the sheet cannot be open while
/// holding the previous document's text.
struct ReadableDocument: Identifiable {
    let id = UUID()
    let title: String
    let subtitle: String
    let content: String
}

struct InfoView: View {
    let face: Face
    /// Opened lazily: the tab is drawn on every launch and the manual is
    /// 63,000 characters, so it is read when somebody asks for it.
    private var store: EntityStore? { EntityStore(path: PreviewRoot.fixturePath) }

    @State private var reading: ReadableDocument?
    @State private var showingSchedule = false
    @State private var showingEvents = false
    @State private var showingCamps = false
    @State private var showingArt = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                HStack(alignment: .firstTextBaseline) {
                    Text(Style.current.label("Info"))
                        .font(.display(34))
                        .foregroundStyle(face.text)
                    Spacer()
                    LucyMenu(face: face)
                }
                .padding(.bottom, 22)

                manualRow
                buildRow
                scheduleRow

                Divider().overlay(face.muted.opacity(0.25))
                    .padding(.bottom, 20)

                Text(Style.current.label("Black Rock City"))
                    .font(.eyebrow(12))
                    .tracking(Style.current.eyebrowTracking)
                    .foregroundStyle(face.muted)
                    .padding(.bottom, 16)

                eventsRow
                campsRow
                artRow

                Divider().overlay(face.muted.opacity(0.25))
                    .padding(.vertical, 22)

                about
            }
            .padding(22)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .scrollIndicators(.hidden)
        .background(face.ground)
        .preferredColorScheme(face.night ? .dark : .light)
        .sheet(item: $reading) { doc in
            DocumentView(face: face, title: doc.title,
                         subtitle: doc.subtitle, content: doc.content)
        }
        .sheet(isPresented: $showingSchedule) {
            ScheduleView(face: face, content: Self.scheduleFile ?? "")
        }
        .sheet(isPresented: $showingEvents) { PlayaEventsView(face: face) }
        .sheet(isPresented: $showingCamps) { PlayaListingView.camps(face: face) }
        .sheet(isPresented: $showingArt) { PlayaListingView.art(face: face) }
    }

    // MARK: - The camp's paperwork

    /// The manual, if this database carries one.
    ///
    /// Still a `soon` row when it does not, rather than a button that opens
    /// nothing: a database built before the manual was ingested is a valid
    /// database, and the honest thing is to say the camp has not written it
    /// down here yet.
    @ViewBuilder
    private var manualRow: some View {
        if let store, let manual = store.campManual() {
            openRow(title: "The manual",
                    detail: "Everything the camp has written down about how "
                          + "it runs — setup, strike, who owns what. Lucy "
                          + "already answers out of it; this is where you "
                          + "read it yourself.",
                    caption: manual.year.map { "\(manual.title) · \($0)" }
                             ?? manual.title) {
                if let doc = store.document(id: manual.id) {
                    reading = ReadableDocument(title: "The manual",
                                               subtitle: doc.title,
                                               content: doc.content)
                }
            }
        } else {
            soon(title: "The manual",
                 detail: "Everything the camp has written down about how it "
                       + "runs. Not in this database yet.")
        }
    }

    /// How the camp gets built, with the pictures.
    ///
    /// Its own row rather than a section of the manual: the manual is what
    /// the camp knows, and this is what four days in the dust before anyone
    /// else arrives actually involves. It is also the only document here with
    /// figures that carry the instruction rather than decorate it -- the
    /// 2026 camp layout plan is a drawing, and no paraphrase of it is any use
    /// to somebody placing Doris.
    @ViewBuilder
    private var buildRow: some View {
        if let file = Self.buildFile {
            openRow(title: "Build",
                    detail: "Setting the camp up, in order, with the layout "
                          + "plan and photos of how each piece goes together.",
                    caption: Self.buildCaption(file)) {
                reading = ReadableDocument(title: "Build",
                                           subtitle: "Build (2026)",
                                           content: file)
            }
        } else {
            soon(title: "Build",
                 detail: "How the camp gets built. Not in this app yet.")
        }
    }

    /// The build guide, or nil when the resource did not make it in.
    static var buildFile: String? {
        guard let url = Bundle.main.url(forResource: "build-2026",
                                        withExtension: "md"),
              let text = try? String(contentsOf: url, encoding: .utf8),
              !text.isEmpty
        else { return nil }
        return text
    }

    /// Counted from the file, so it cannot drift from what shipped.
    private static func buildCaption(_ file: String) -> String {
        let sections = splitIntoSections(file).filter { !$0.heading.isEmpty }
        let figures = splitOutFigures(file).filter { $0.kind == .figure }
        return "\(sections.count) sections · \(figures.count) figures"
    }

    /// The camp's own sign-up sheet, by day.
    ///
    /// Read from a bundled file rather than `camp_knowledge`. The corpus has
    /// exactly one document filed under `events` and it is a marketing
    /// playbook, so the old version of this row opened a page headed "The
    /// schedule" showing something that was not one. The sheet is also 2026
    /// data that arrived after the corpus was built, and waiting for a
    /// rebuild to show somebody their shift is the wrong trade.
    ///
    /// Still a `soon` row if the resource is missing, rather than a button
    /// that opens an empty page.
    @ViewBuilder
    private var scheduleRow: some View {
        if let file = Self.scheduleFile {
            openRow(title: "The schedule",
                    detail: "Every shift and event, day by day. Search your "
                          + "own name to see just your week.",
                    caption: Self.scheduleCaption(file)) {
                showingSchedule = true
            }
        } else {
            soon(title: "The schedule",
                 detail: "When things happen, on and off playa. No sign-up "
                       + "sheet has been built into this app yet.")
        }
    }

    /// The sign-up sheet, or nil when the resource did not make it into the
    /// bundle. xcodegen drops a top-level `resources:` key silently, so this
    /// failing quietly is a real possibility and the caller has to handle it.
    static var scheduleFile: String? {
        guard let url = Bundle.main.url(forResource: "ps-2026-shifts",
                                        withExtension: "md"),
              let text = try? String(contentsOf: url, encoding: .utf8),
              !text.isEmpty
        else { return nil }
        return text
    }

    /// Counts what is actually in the file, so the caption cannot drift from
    /// the sheet the way a hardcoded "7 days" would.
    private static func scheduleCaption(_ file: String) -> String {
        let blocks = parseSchedule(file)
        let days = blocks.filter { $0.kind == .day }.count
        let events = blocks.filter { $0.kind == .event }.count
        return "\(days) days · \(events) shifts and events"
    }

    // MARK: - What the city published

    /// Every event in Black Rock City, by day.
    ///
    /// Its own row rather than part of "The schedule", which is the camp's
    /// sign-up sheet: one says when you are on the bar, the other says what
    /// 859 other camps are doing, and folding them together would mean a
    /// camper searching for their shift wading through 4,224 listings.
    ///
    /// The counts come from `playa-counts.json` rather than from the file
    /// they describe, because this row is drawn on every launch and the
    /// events are 1.1 MB. See `PlayaCounts`.
    @ViewBuilder
    private var eventsRow: some View {
        if Playa.has(Playa.eventsFile), let n = PlayaCounts.shared?.events {
            openRow(title: "Events",
                    detail: "What the whole city is doing, day by day. "
                          + "Search a camp, a DJ or a thing you fancy.",
                    caption: "\(grouped(n.events)) events · "
                           + "\(grouped(n.occurrences)) times · \(n.days) days") {
                showingEvents = true
            }
        } else {
            soon(title: "Events",
                 detail: "What the whole city is doing. Not in this build.")
        }
    }

    /// Every camp and where it is.
    @ViewBuilder
    private var campsRow: some View {
        if Playa.has(Playa.campsFile), let n = PlayaCounts.shared?.camps {
            openRow(title: "Camps",
                    detail: "Who is out there and what corner they are on.",
                    caption: "\(grouped(n.camps)) camps") {
                showingCamps = true
            }
        } else {
            soon(title: "Camps",
                 detail: "Who is out there and where. Not in this build.")
        }
    }

    /// Every art piece, who made it, and what it is.
    ///
    /// The detail line still says what is missing on purpose: locations are
    /// not released until the Sunday, and a listing that never says where
    /// anything is reads as a bug rather than as the shape of the data.
    @ViewBuilder
    private var artRow: some View {
        if Playa.has(Playa.artFile), let n = PlayaCounts.shared?.art {
            openRow(title: "Art",
                    detail: "Every piece, who made it, and what it is. "
                          + "The city does not release locations until the "
                          + "Sunday.",
                    caption: "\(n.art) pieces") {
                showingArt = true
            }
        } else {
            soon(title: "Art",
                 detail: "Every piece and who made it. Not in this build.")
        }
    }

    /// Thousands separated. Four digits of listing count is a number you
    /// read twice otherwise, and the day headings on the events screen group
    /// theirs already, by way of `Text`'s own formatting.
    private func grouped(_ n: Int) -> String {
        n.formatted(.number.grouping(.automatic))
    }

    /// A row that opens something, as against `soon`, which admits it cannot.
    private func openRow(title: String, detail: String, caption: String,
                         tap: @escaping () -> Void) -> some View {
        Button(action: tap) {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 9) {
                    Text(title)
                        .font(.display(22)).foregroundStyle(face.text)
                    Image(systemName: "chevron.right")
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(face.muted)
                    Spacer(minLength: 0)
                }
                Text(detail)
                    .font(.body_(16)).foregroundStyle(face.muted)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                Text(caption)
                    .font(.data(11)).foregroundStyle(face.muted.opacity(0.8))
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.bottom, 24)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private func soon(title: String, detail: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 9) {
                Text(title)
                    .font(.display(22)).foregroundStyle(face.text)
                Text(Style.current.label("Not yet"))
                    .font(.eyebrow(10))
                    .tracking(Style.current.eyebrowTracking)
                    .foregroundStyle(face.ground)
                    .padding(.horizontal, 7).padding(.vertical, 3)
                    .background(Capsule().fill(face.muted))
            }
            Text(detail)
                .font(.body_(16)).foregroundStyle(face.muted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.bottom, 24)
    }

    /// What this install actually is. Worth a screenful of nothing else when a
    /// tester says "she gave me a bad answer" -- the first two questions are
    /// always which build and which model, and neither was reachable from
    /// inside the app before.
    private var about: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("This install")
                .font(.eyebrow(12))
                .tracking(Style.current.eyebrowTracking)
                .foregroundStyle(face.muted)
            line("Version", Self.version)
            line("Her brain", ModelDownload.shared.loadedDescription)
            line("Notes waiting", "\(CaptureStore.all().filter { !$0.uploaded }.count)")
        }
    }

    private func line(_ label: String, _ value: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Text(label)
                .font(.data(13)).foregroundStyle(face.muted)
                .frame(width: 110, alignment: .leading)
            Text(value)
                .font(.data(13)).foregroundStyle(face.text)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
    }

    private static var version: String {
        let info = Bundle.main.infoDictionary
        let short = info?["CFBundleShortVersionString"] as? String ?? "dev"
        let build = info?["CFBundleVersion"] as? String ?? "?"
        return "\(short) (\(build))"
    }
}
