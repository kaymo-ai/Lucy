import SwiftUI

// ASK — retrieve what the camp already knows.
//
// A question resolves to an entity RECORD. When it resolves to nothing, that
// is said plainly rather than dressed up with the nearest weak match: an
// answer with no evidence behind it must look different from one with
// evidence, never silently identical.

struct AskView: View {
    let face: Face
    let store: EntityStore

    var showsEyebrow: Bool = true

    @State private var query: String = ""
    @State private var resolved: Entity?
    @State private var missed: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            field

            if let entity = resolved {
                EntityRecordView(face: face,
                                 entity: entity,
                                 factsByCategory: store.facts(entityID: entity.id))
            } else if let missed {
                noAnswer(missed)
                Spacer(minLength: 0)
            } else {
                suggestions
                Spacer(minLength: 0)
            }
        }
        .background(face.ground)
        .preferredColorScheme(face.night ? .dark : .light)
    }

    // MARK: - The question

    private var field: some View {
        VStack(alignment: .leading, spacing: 0) {
            if showsEyebrow { Eyebrow(text: "ASK", face: face).padding(.top, 10) }

            HStack(spacing: 10) {
                TextField("what is Doris", text: $query)
                    .font(.display(30))
                    .foregroundStyle(face.text)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .onSubmit(resolve)

                Button(action: resolve) {
                    Image(systemName: "arrow.right")
                        .font(.system(size: 17, weight: .bold))
                        .foregroundStyle(face.night ? Color(hex: 0x07080A) : .white)
                        .frame(width: 42, height: 42)
                        .background(Circle().fill(face.accent))
                }
                .buttonStyle(.plain)
            }
            .padding(.top, 4)

            Rectangle().fill(face.hairline).frame(height: 1).padding(.top, 14)
        }
        .padding(.horizontal, 22)
    }

    /// Lookup goes through entity_alias too — the camp calls the same thing
    /// several names. Words like "what is" are stripped so a spoken question
    /// resolves the same way a bare noun does.
    private func resolve() {
        let stripped = query
            .replacingOccurrences(of: #"(?i)\b(what|who|where|is|are|the|a|an|about|tell|me)\b"#,
                                  with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)

        if let hit = store.findEntity(named: stripped) ?? store.findEntity(named: query) {
            resolved = hit
            missed = nil
        } else {
            resolved = nil
            missed = query
        }
    }

    // MARK: - Nothing found

    /// Deliberately unlike a record: no headline, no fact rows, no receipts.
    /// The shape of the screen itself says the camp has not answered this.
    private func noAnswer(_ asked: String) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("I don't have a good answer.")
                .font(.display(34))
                .foregroundStyle(face.text)
                .fixedSize(horizontal: false, vertical: true)

            Text("Nothing in the camp's records covers \u{201C}\(asked)\u{201D}. "
                 + "That is a gap worth filling — ask in the PS chat, or use REMEMBER "
                 + "to capture what you find.")
                .font(.body_(16))
                .foregroundStyle(face.muted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 22)
        .padding(.top, 26)
    }

    // MARK: - What the camp does know

    private var suggestions: some View {
        VStack(alignment: .leading, spacing: 0) {
            Eyebrow(text: "THE CAMP KNOWS ABOUT", face: face).padding(.top, 24)

            VStack(alignment: .leading, spacing: 0) {
                ForEach(store.allEntities()) { entity in
                    Button {
                        query = entity.name
                        resolved = entity
                        missed = nil
                    } label: {
                        HStack(spacing: 12) {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(entity.name).font(.display(28))
                                    .foregroundStyle(face.text)
                                Text("\(entity.kind) · \(entity.mentionCount) mentions")
                                    .font(.data(12)).foregroundStyle(face.muted)
                            }
                            Spacer()
                            Image(systemName: "arrow.up.right")
                                .font(.system(size: 13, weight: .bold))
                                .foregroundStyle(face.muted)
                        }
                        .padding(.vertical, Style.current.rowSpacing)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)

                    Rectangle().fill(face.hairline).frame(height: 1)
                }
            }
            .padding(.top, 10)
        }
        .padding(.horizontal, 22)
    }
}
