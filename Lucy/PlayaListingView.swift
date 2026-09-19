import SwiftUI

// One screen, used for the camps and for the art.
//
// They are the same shape -- a name, one line under it, a thousand of them in
// alphabetical order -- and writing the screen twice would have meant two
// search boxes that drift apart. The events get their own screen because they
// are not this shape: they are grouped by day and each one carries a time.
//
// `note` exists for the art, whose locations the city withholds until the
// Sunday -- a list that never says where anything is reads as a broken fetch
// unless something tells you otherwise. Rows with an `about` open in place on
// a tap, the same gesture the events screen uses.

struct PlayaListingRow: Identifiable {
    let id: Int
    let title: String
    let subtitle: String
    /// Shown when the row is tapped open. Empty for the camps, whose listing
    /// is an address and nothing else.
    let about: String

    /// Lowercased title, subtitle and description, joined. Built once at
    /// load: the camps list is 1,181 rows and lowercasing all of them per
    /// keystroke is work done over and over for an answer that never
    /// changes. The description is in here so "mushroom" finds the mushroom
    /// piece by what it is, not only by what it is called.
    let haystack: String

    init(id: Int, title: String, subtitle: String, about: String = "") {
        self.id = id
        self.title = title
        self.subtitle = subtitle
        self.about = about
        self.haystack = "\(title) \(subtitle) \(about)".lowercased()
    }

    /// `#` for anything that does not start with a letter, so "8-bit Bunny"
    /// and "1/1000 - A Wish for Recovery" have somewhere to live.
    var initial: String {
        guard let c = title.first, c.isLetter else { return "#" }
        return String(c).uppercased()
    }
}

struct PlayaListingSection: Identifiable {
    let id: String
    let rows: [PlayaListingRow]
}

/// Groups rows under their initial, keeping the order they arrive in. The
/// converter already sorted them, so this never re-sorts: two sorts with
/// different ideas about case or punctuation would disagree, and the one
/// that ships should be the one a human can read in the JSON.
func playaSections(_ rows: [PlayaListingRow]) -> [PlayaListingSection] {
    var out: [PlayaListingSection] = []
    var initial: String?
    var bucket: [PlayaListingRow] = []

    func flush() {
        guard let initial, !bucket.isEmpty else { return }
        out.append(PlayaListingSection(id: initial, rows: bucket))
        bucket = []
    }

    for row in rows {
        if row.initial != initial {
            flush()
            initial = row.initial
        }
        bucket.append(row)
    }
    flush()
    return out
}

struct PlayaListingView: View {
    let face: Face
    let title: String
    let placeholder: String
    /// Shown when a search finds nothing.
    let nothing: String
    /// A standing caveat about the data, shown above the list. Nil for camps.
    var note: String?
    /// Called once, off the main actor. Returns nil when the resource is not
    /// in this build.
    let load: @Sendable () -> [PlayaListingRow]?
    /// Renders only: seeds the search box and opens the first hit, so a
    /// screenshot shows a description rather than a column of names. Real
    /// use never sets either.
    var previewQuery: String?
    var previewExpandsFirst = false

    @Environment(\.dismiss) private var dismiss
    @State private var query = ""
    @State private var rows: [PlayaListingRow]?
    @State private var failed = false
    @State private var expanded: Set<Int> = []

    private var shown: [PlayaListingRow] {
        guard let rows else { return [] }
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        guard !q.isEmpty else { return rows }
        return rows.filter { $0.haystack.contains(q) }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            if rows != nil { search }
            content
        }
        .padding(22)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(face.ground)
        .preferredColorScheme(face.night ? .dark : .light)
        .task {
            guard rows == nil, !failed else { return }
            let loaded = await Task.detached(priority: .userInitiated) {
                load()
            }.value
            if let loaded {
                rows = loaded
                if let previewQuery { query = previewQuery }
                if previewExpandsFirst {
                    let q = (previewQuery ?? "").lowercased()
                    let hit = loaded.first {
                        !$0.about.isEmpty && (q.isEmpty || $0.haystack.contains(q))
                    }
                    if let hit { expanded = [hit.id] }
                }
            } else { failed = true }
        }
    }

    @ViewBuilder
    private var content: some View {
        if failed {
            Text("This listing did not make it into this build.")
                .font(.body_(16)).foregroundStyle(face.muted)
                .padding(.top, 18)
            Spacer()
        } else if rows == nil {
            HStack(spacing: 10) {
                ProgressView().tint(face.muted)
                Text("Reading the listing…")
                    .font(.data(13)).foregroundStyle(face.muted)
            }
            .padding(.top, 18)
            Spacer()
        } else if shown.isEmpty {
            Text(nothing)
                .font(.body_(16)).foregroundStyle(face.muted)
                .padding(.top, 18)
            Spacer()
        } else {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    if let note, query.isEmpty {
                        Text(note)
                            .font(.data(13)).foregroundStyle(face.muted)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.top, 12).padding(.bottom, 16)
                    }
                    ForEach(playaSections(shown)) { section in
                        Text(Style.current.label(section.id))
                            .font(.eyebrow(12))
                            .tracking(Style.current.eyebrowTracking)
                            .foregroundStyle(face.accent)
                            .padding(.top, 20).padding(.bottom, 6)
                        ForEach(section.rows) { row in
                            entry(row)
                        }
                    }
                }
                .padding(.bottom, 40)
            }
            .scrollIndicators(.hidden)
        }
    }

    private func entry(_ row: PlayaListingRow) -> some View {
        Button {
            guard !row.about.isEmpty else { return }
            if expanded.contains(row.id) { expanded.remove(row.id) }
            else { expanded.insert(row.id) }
        } label: {
            VStack(alignment: .leading, spacing: 2) {
                Text(row.title)
                    .font(.body_(18)).foregroundStyle(face.text)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
                if !row.subtitle.isEmpty {
                    Text(row.subtitle)
                        .font(.data(14)).foregroundStyle(face.muted)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                }
                if expanded.contains(row.id) {
                    Text(row.about)
                        .font(.body_(15)).foregroundStyle(face.muted)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                        .padding(.top, 4)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, 7)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(Style.current.label(title))
                .font(.eyebrow(15))
                .tracking(Style.current.eyebrowTracking + Style.current.boost(0.5))
                .foregroundStyle(face.accent)
            Spacer()
            if let rows, !query.isEmpty {
                Text("\(shown.count) of \(rows.count)")
                    .font(.data(12)).foregroundStyle(face.muted)
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
            TextField(placeholder, text: $query)
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
        .padding(.bottom, 4)
    }
}

// MARK: - The two listings

extension PlayaListingView {
    static func camps(face: Face) -> PlayaListingView {
        PlayaListingView(
            face: face,
            title: "Camps",
            placeholder: "Search a camp",
            nothing: "No camp by that name in the listing.",
            load: {
                guard let file: [String: [PlayaCamp]] = Playa.decode(Playa.campsFile),
                      let camps = file["camps"]
                else { return nil }
                return camps.enumerated().map { i, c in
                    PlayaListingRow(id: i, title: c.name, subtitle: c.address)
                }
            })
    }

    static func art(face: Face) -> PlayaListingView {
        PlayaListingView(
            face: face,
            title: "Art",
            placeholder: "Search a piece or an artist",
            nothing: "No piece by that name in the listing.",
            note: "Tap a piece for what it is. The city does not release "
                + "art locations until the Sunday.",
            load: {
                guard let file: [String: [PlayaArt]] = Playa.decode(Playa.artFile),
                      let art = file["art"]
                else { return nil }
                return art.enumerated().map { i, a in
                    PlayaListingRow(id: i, title: a.name, subtitle: a.artist,
                                    about: a.about ?? "")
                }
            })
    }
}
