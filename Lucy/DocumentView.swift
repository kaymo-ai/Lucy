import SwiftUI

// The camp's own paperwork, to read rather than to ask about.
//
// InfoView promised these two screens with a "Not yet" chip from the day it
// existed, and the reasoning in that file is still the reason they belong
// here rather than in the chat: "what time is dinner" wants a table, and "how
// do we strike Doris" wants the section, not a paraphrase of it. Lucy answers
// OUT of these documents; this is where you read them yourself.
//
// Nothing here is generated. A document is text the camp wrote, rendered --
// so there is no invariant to worry about and no model in the path.

/// One heading and the text under it.
struct DocSection: Identifiable, Hashable {
    let id: Int
    let heading: String
    let body: String
    /// How deep the heading was, so the list can indent rather than pretend
    /// a sixty-item flat list is navigable.
    let level: Int
}

/// Splits a markdown-ish document into its sections.
///
/// The camp's manual is 63,000 characters under 60 headings, exported from a
/// Google Doc, so the markup is whatever that produced: `#`, `##`, `###`, and
/// the occasional `{#anchor}` suffix Docs adds. Anything before the first
/// heading is kept as an untitled opening section rather than dropped -- the
/// manual's first paragraph is the one that says where the storage unit is.
func splitIntoSections(_ text: String) -> [DocSection] {
    var out: [DocSection] = []
    var heading = ""
    var level = 1
    var body: [String] = []

    func flush() {
        let joined = body.joined(separator: "\n")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !heading.isEmpty || !joined.isEmpty else { return }
        out.append(DocSection(id: out.count, heading: heading,
                              body: joined, level: level))
    }

    for raw in text.components(separatedBy: .newlines) {
        let line = raw.trimmingCharacters(in: .whitespaces)
        guard line.hasPrefix("#") else { body.append(raw); continue }
        let hashes = line.prefix { $0 == "#" }.count
        // A line of hashes with no text is a rule, not a heading.
        let title = line.dropFirst(hashes).trimmingCharacters(in: .whitespaces)
        guard !title.isEmpty else { body.append(raw); continue }
        flush()
        heading = stripAnchor(title)
        level = min(hashes, 3)
        body = []
    }
    flush()
    return out
}

/// Google Docs exports headings as "BUILD {#build}". The anchor is machinery.
func stripAnchor(_ heading: String) -> String {
    guard let brace = heading.range(of: " {#") else {
        return heading.replacingOccurrences(of: "\\", with: "")
    }
    return String(heading[..<brace.lowerBound])
        .replacingOccurrences(of: "\\", with: "")
}

/// Where `docx_to_markdown.py` puts the figures, and where project.yml ships
/// them. One name, so the converter, the bundle and the lookup cannot drift.
let FIGURE_DIR = "BuildFigures"

/// A run of prose, or one figure, in document order.
struct DocPiece: Identifiable {
    enum Kind { case prose, figure }
    let id: Int
    let kind: Kind
    /// The text, or the bundled image's file name.
    let value: String
}

/// Splits a section body into prose and `![...](file)` figures.
///
/// Only markdown's image line is recognised, and only on a line of its own --
/// that is all `docx_to_markdown.py` emits, and a looser parser would start
/// eating square brackets out of the manual's prose.
func splitOutFigures(_ body: String) -> [DocPiece] {
    var pieces: [DocPiece] = []
    var prose: [String] = []

    func flushProse() {
        let joined = prose.joined(separator: "\n")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        prose = []
        guard !joined.isEmpty else { return }
        pieces.append(DocPiece(id: pieces.count, kind: .prose, value: joined))
    }

    for line in body.components(separatedBy: .newlines) {
        let t = line.trimmingCharacters(in: .whitespaces)
        if t.hasPrefix("!["), let open = t.firstIndex(of: "("),
           t.hasSuffix(")") {
            let name = String(t[t.index(after: open)..<t.index(before: t.endIndex)])
            if !name.isEmpty {
                flushProse()
                pieces.append(DocPiece(id: pieces.count, kind: .figure,
                                       value: name))
                continue
            }
        }
        prose.append(line)
    }
    flushProse()
    return pieces
}

struct DocumentView: View {
    let face: Face
    let title: String
    let subtitle: String
    let content: String
    /// Screenshots only: opens the first section so a render shows a body
    /// rather than sixty collapsed headings. Real use never sets it.
    var previewOpensFirst: Bool = false
    /// Screenshots only: starts with the search box filled, which opens every
    /// matching section. The figures in the build guide sit several sections
    /// down, and `previewOpensFirst` only reaches the first two -- so without
    /// this there is no way to photograph one.
    var previewQuery: String = ""

    @Environment(\.dismiss) private var dismiss
    /// Collapsed by default. Sixty open sections is a wall; the headings are
    /// the point of arrival and the body is what you went looking for.
    @State private var open: Set<Int> = []
    @State private var didPreview = false
    @State private var query = ""

    private var sections: [DocSection] { splitIntoSections(content) }

    private var shown: [DocSection] {
        let q = query.trimmingCharacters(in: .whitespaces).lowercased()
        guard !q.isEmpty else { return sections }
        return sections.filter {
            $0.heading.lowercased().contains(q) || $0.body.lowercased().contains(q)
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            search
            if shown.isEmpty {
                Text("Nothing in here matches that.")
                    .font(.body_(16)).foregroundStyle(face.muted)
                    .padding(.top, 18)
                Spacer()
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(shown) { section in
                            row(section)
                        }
                    }
                    .padding(.bottom, 30)
                }
                .scrollIndicators(.hidden)
            }
        }
        .padding(22)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(face.ground)
        .preferredColorScheme(face.night ? .dark : .light)
        .onAppear {
            guard !didPreview else { return }
            if !previewQuery.isEmpty {
                didPreview = true
                query = previewQuery
                return
            }
            guard previewOpensFirst else { return }
            didPreview = true
            open = Set(sections.prefix(2).map(\.id))
        }
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 3) {
                Text(Style.current.label(title))
                    .font(.eyebrow(15))
                    .tracking(Style.current.eyebrowTracking + Style.current.boost(0.5))
                    .foregroundStyle(face.accent)
                if !subtitle.isEmpty {
                    Text(subtitle)
                        .font(.data(12)).foregroundStyle(face.muted)
                }
            }
            Spacer()
            Button("Done") { dismiss() }
                .font(.body_(16)).foregroundStyle(face.muted)
        }
        .padding(.bottom, 14)
    }

    private var search: some View {
        TextField("Search", text: $query)
            .textFieldStyle(.plain)
            .font(.body_(16))
            .foregroundStyle(face.text)
            .padding(.horizontal, 12).padding(.vertical, 9)
            .background(RoundedRectangle(cornerRadius: 9).fill(face.raised))
            .padding(.bottom, 12)
            .autocorrectionDisabled()
    }

    /// The figures ship as a FOLDER REFERENCE, so they are inside
    /// `BuildFigures/` in the bundle rather than at its root.
    ///
    /// `Bundle.main.path(forResource:ofType:)` looks only at the root and
    /// `UIImage(named:)` looks only in the asset catalog, so both return nil
    /// here and the page draws no pictures at all -- which is exactly what
    /// the first render showed. Kept as a named lookup with the directory
    /// spelled out, because the failure is silent in every other form.
    static func bundledImage(named name: String) -> UIImage? {
        if let p = Bundle.main.path(forResource: name, ofType: nil,
                                    inDirectory: FIGURE_DIR),
           let ui = UIImage(contentsOfFile: p) {
            return ui
        }
        if let p = Bundle.main.path(forResource: name, ofType: nil),
           let ui = UIImage(contentsOfFile: p) {
            return ui
        }
        return UIImage(named: name)
    }

    /// One figure, loaded from the bundle.
    ///
    /// `UIImage(named:)` rather than `Image(name)`: the figures are loose
    /// files in a folder reference, not asset-catalog entries, and SwiftUI's
    /// initialiser looks only in the catalog -- it fails silently and draws
    /// nothing, which reads as a document that simply has no pictures. If a
    /// figure is missing this says so instead, because a build guide quietly
    /// missing its camp plan is the failure worth catching.
    @ViewBuilder
    private func figure(_ name: String) -> some View {
        if let ui = Self.bundledImage(named: name) {
            Image(uiImage: ui)
                .resizable()
                .scaledToFit()
                .frame(maxWidth: .infinity)
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .padding(.bottom, 14)
        } else {
            Text("[figure missing: \(name)]")
                .font(.data(12))
                .foregroundStyle(face.muted)
                .padding(.bottom, 14)
        }
    }

    @ViewBuilder
    private func row(_ section: DocSection) -> some View {
        let isOpen = open.contains(section.id) || !query.isEmpty
        VStack(alignment: .leading, spacing: 0) {
            Button {
                if open.contains(section.id) { open.remove(section.id) }
                else { open.insert(section.id) }
            } label: {
                HStack(alignment: .firstTextBaseline, spacing: 9) {
                    Text(section.heading.isEmpty ? "Opening" : section.heading)
                        .font(.display(section.level == 1 ? 21 : 18))
                        .foregroundStyle(face.text)
                        .multilineTextAlignment(.leading)
                    Spacer(minLength: 0)
                    Image(systemName: isOpen ? "chevron.down" : "chevron.right")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(face.muted)
                }
                // Deeper headings sit in from the margin, so sixty of them
                // read as a structure rather than a list.
                .padding(.leading, CGFloat(section.level - 1) * 14)
                .padding(.vertical, 11)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(!query.isEmpty)

            if isOpen && !section.body.isEmpty {
                // Prose and figures, in the order the document has them. The
                // build guide is instructions around photographs -- "the
                // bracket goes on like this" is a caption, and the picture is
                // the instruction. A text-only reader would drop the camp
                // layout plan, which is the one page somebody standing in the
                // dust actually needs.
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(splitOutFigures(section.body)) { piece in
                        switch piece.kind {
                        case .prose:
                            Text(piece.value)
                                // Read standing up, in sun, often through
                                // dust. This is the one screen in the app
                                // whose whole purpose is a wall of text
                                // somebody has to actually read, so it takes
                                // the primary colour rather than the
                                // secondary one and a size closer to a book
                                // than to a caption.
                                .font(.body_(18))
                                .foregroundStyle(face.text)
                                .lineSpacing(3)
                                .fixedSize(horizontal: false, vertical: true)
                                .padding(.bottom, 14)
                        case .figure:
                            figure(piece.value)
                        }
                    }
                }
                .padding(.leading, CGFloat(section.level - 1) * 14)
            }
            Divider().overlay(face.muted.opacity(0.18))
        }
    }
}
