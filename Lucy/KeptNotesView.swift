import SwiftUI

// Everything this phone has kept.
//
// It used to sit under the camera on the MEMORY tab, which meant the screen
// you go to in order to *make* a note opened on a list of notes you already
// made, and the viewfinder — the only part you came for — was squeezed above
// it. A list of a week's captures is worth having and is not worth the top of
// the screen.

struct KeptNotesView: View {
    let face: Face
    @Environment(\.dismiss) private var dismiss

    @State private var notes: [Capture] = []

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .firstTextBaseline) {
                Text(Style.current.label("Kept"))
                    .font(.display(34))
                    .foregroundStyle(face.text)
                Spacer()
                Button("Done") { dismiss() }
                    .font(.body_(16)).foregroundStyle(face.muted)
            }
            .padding(.horizontal, 22)
            .padding(.top, 18)
            .padding(.bottom, 12)

            Rectangle().fill(face.hairline).frame(height: 1)

            if notes.isEmpty {
                Text(Style.current.isPlayful
                     ? "Nothing kept yet — hold the button and tell me something."
                     : "Nothing kept yet.")
                    .font(.body_(17)).foregroundStyle(face.muted)
                    .padding(22)
                Spacer()
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 0) {
                        ForEach(notes, id: \.id) { note in
                            row(note)
                            Rectangle().fill(face.hairline).frame(height: 1)
                        }
                    }
                    .padding(.horizontal, 22)
                }
                .scrollIndicators(.hidden)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(face.ground)
        .preferredColorScheme(face.night ? .dark : .light)
        .onAppear { notes = CaptureStore.all() }
    }

    private func row(_ note: Capture) -> some View {
        HStack(alignment: .top, spacing: 12) {
            // What is in it, and whether the camp has it. A note that has not
            // synced is not lost, but it is only on this phone, and that is
            // worth being able to see at a glance.
            VStack(spacing: 5) {
                if FileManager.default.fileExists(atPath: note.photo.path) {
                    Image(systemName: "camera.fill")
                        .font(.system(size: 12)).foregroundStyle(face.muted)
                }
                if note.hasMemo {
                    Image(systemName: "waveform")
                        .font(.system(size: 12)).foregroundStyle(face.muted)
                }
            }
            .frame(width: 16)

            VStack(alignment: .leading, spacing: 4) {
                let said = note.savedTranscript
                    .trimmingCharacters(in: .whitespacesAndNewlines)
                Text(said.isEmpty ? "Photo, no words" : said)
                    .font(.body_(16))
                    .foregroundStyle(said.isEmpty ? face.muted : face.text)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 8) {
                    Text(Self.when.string(from: note.takenAt))
                        .font(.data(12)).foregroundStyle(face.muted)
                    if note.uploaded {
                        Text(Style.current.label("Sent"))
                            .font(.eyebrow(9))
                            .tracking(Style.current.eyebrowTracking)
                            .foregroundStyle(face.keep)
                    }
                }
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 12)
    }

    private static let when: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "EEE d MMM, HH:mm"
        return f
    }()
}
