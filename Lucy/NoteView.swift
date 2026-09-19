import SwiftUI

// NOTE: a photo, a voice note, and what the voice note said.
//
// Separate from the chat on purpose. Asking and noting are different acts —
// one is a question you want answered now, the other is a thing you want
// handed to the camp later — and folding them into one composer meant every
// question carried a shutter button it did not need.
//
// The whole surface is two buttons and a save. Neither the photo nor the
// voice note is required, because half the useful notes are a photo of a
// broken thing with nothing to say about it, and the other half are someone
// talking while both hands are busy.

struct NoteView: View {
    let face: Face
    /// Forces the staged state on, for screenshots. There is no other way to
    /// photograph KEEP and DISCARD: they appear only once something has been
    /// captured, and the Simulator has no camera and no microphone worth the
    /// name.
    var previewStaged: Bool = false

    @StateObject private var camera = CameraController()
    @Environment(\.scenePhase) private var scenePhase

    @State private var saved: [Capture] = []
    @State private var justSaved = false
    /// Slid up to keep recording with the hand free -- the case a note has and
    /// a question does not.
    @State private var recordingLocked = false
    @State private var showingKept = false

    /// The corner every button on this screen shares. 3pt read as a square
    /// with a rendering error rather than as a deliberate edge.
    private static let corner: CGFloat = 12

    private var hasSomething: Bool {
        previewStaged || camera.lastPhoto != nil || !camera.transcript.isEmpty
            || camera.recording
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            Rectangle().fill(face.hairline).frame(height: 1)

            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    viewfinder
                    controls
                    if previewStaged || camera.transcribing
                        || !camera.transcript.isEmpty {
                        transcript
                    }
                    if let problem = camera.problem {
                        Text(problem.uppercased())
                            .font(.eyebrow(11))
                            .tracking(Style.current.eyebrowTracking)
                            .foregroundStyle(face.accent)
                    }
                    if hasSomething { saveRow }
                    if previewStaged || !saved.isEmpty { keptButton }
                }
                .padding(.horizontal, 22)
                .padding(.top, 16)
                .padding(.bottom, 40)
            }
        }
        .background(face.ground)
        .overlay(RecordingAura(face: face, level: camera.level,
                               active: camera.recording))
        .preferredColorScheme(face.night ? .dark : .light)
        .task {
            Haptics.prepare()
            await camera.start()
            if !PreviewRoot.skipsPermissionPrompts { await camera.primeMic() }
            saved = CaptureStore.all()
        }
        .onDisappear {
            // Leaving mid-recording would strand an m4a with no index — the
            // file is unplayable and the failure is silent.
            if camera.recording { camera.stopRecording() }
            camera.stop()
        }
        .onChange(of: scenePhase) { _, phase in
            if phase != .active, camera.recording { camera.stopRecording() }
        }
        .sheet(isPresented: $showingKept) {
            KeptNotesView(face: face)
        }
    }

    // MARK: - Pieces

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(Style.current.label("Memory"))
                .font(.display(34))
                .foregroundStyle(face.text)
            Spacer()
            LucyMenu(face: face)
        }
        .padding(.horizontal, 22)
        .padding(.top, 8)
        .padding(.bottom, 10)
    }

    private var viewfinder: some View {
        ZStack {
            if let photo = camera.lastPhoto {
                Image(uiImage: photo)
                    .resizable()
                    .scaledToFill()
            } else if camera.ready {
                CameraPreview(session: camera.session)
            } else {
                Rectangle().fill(face.hairline.opacity(0.35))
                Text(camera.denied ? "NO CAMERA ACCESS" : "STARTING CAMERA")
                    .font(.eyebrow(11))
                    .tracking(Style.current.eyebrowTracking)
                    .foregroundStyle(face.muted)
            }
        }
        .frame(height: 380)
        .frame(maxWidth: .infinity)
        .clipShape(RoundedRectangle(cornerRadius: Self.corner))
        .overlay(alignment: .topTrailing) {
            if camera.lastPhoto != nil {
                Button {
                    Haptics.tap()
                    camera.lastPhoto = nil
                } label: {
                    Image(systemName: "xmark")
                        .font(.system(size: 12, weight: .bold))
                        .foregroundStyle(face.ground)
                        .padding(7)
                        .background(Circle().fill(face.text.opacity(0.75)))
                }
                .padding(10)
            }
        }
        .overlay(alignment: .bottomLeading) {
            if camera.recording { meter.padding(10) }
        }
    }

    /// Only while recording. A level bar that is always there reads as decor;
    /// one that appears when the microphone opens is the confirmation that
    /// something is actually being heard.
    private var meter: some View {
        HStack(spacing: 8) {
            Circle().fill(face.accent).frame(width: 8, height: 8)
            Text(Self.clock(camera.elapsed))
                .font(.data(13))
                .foregroundStyle(face.ground)
            RoundedRectangle(cornerRadius: 1)
                .fill(face.ground.opacity(0.35))
                .frame(width: 60, height: 3)
                .overlay(alignment: .leading) {
                    RoundedRectangle(cornerRadius: 1)
                        .fill(face.ground)
                        .frame(width: max(2, 60 * camera.level), height: 3)
                }
        }
        .padding(.horizontal, 10).padding(.vertical, 7)
        .background(Capsule().fill(face.text.opacity(0.75)))
    }

    /// Two acts, side by side and the same size, because they are alternatives
    /// and not steps. A full-width TAKE PHOTO above a smaller microphone said
    /// the photo came first and the note second -- neither is required, and a
    /// voice note on its own is the common case when your hands are dirty.
    private var controls: some View {
        HStack(alignment: .top, spacing: 22) {
            VStack(spacing: 12) {
                RoundAction(face: face,
                            system: camera.lastPhoto == nil ? "camera.fill" : "arrow.counterclockwise",
                            tint: face.keep) {
                    Haptics.shutter()
                    Sounds.shutter()
                    camera.capturePhoto()
                }
                Text(camera.lastPhoto == nil ? "PHOTO" : "RETAKE")
                    .font(.eyebrow(10))
                    .tracking(Style.current.eyebrowTracking)
                    .foregroundStyle(face.muted)
            }
            // Equal halves, so each button is centred in its own and the two
            // labels sit on the same line however long the words get.
            .frame(maxWidth: .infinity)

            // The same control the chat uses. This was a square button you
            // pressed twice; two gestures for one act meant the first thing you
            // did on whichever tab you use less was the wrong thing.
            //
            // A note is the case that justifies LOCK: describing a thing while
            // pointing at it needs the hand back, and holding for a minute is
            // not something anyone does twice.
            VStack(spacing: 12) {
            HoldToRecord(
                face: face,
                level: camera.level,
                recording: camera.recording,
                locked: recordingLocked,
                onStart: {
                    guard !camera.recording else { return }
                    camera.startRecording()
                },
                onStop: {
                    recordingLocked = false
                    guard camera.recording else { return }
                    Sounds.end()
                    camera.stopRecording()
                },
                onLock: {
                    recordingLocked = true
                    Haptics.filed()
                }
            )

                Text(recordingLocked ? "TAP TO STOP"
                     : camera.recording ? "SLIDE UP TO LOCK" : "HOLD TO TALK")
                    .font(.eyebrow(10))
                    .tracking(Style.current.eyebrowTracking)
                    .foregroundStyle(face.muted)
                    .multilineTextAlignment(.center)
            }
            .frame(maxWidth: .infinity)
        }
        .frame(maxWidth: .infinity)
    }

    private var transcript: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(camera.transcribing ? "TRANSCRIBING" : "WHAT YOU SAID")
                .font(.eyebrow(10))
                .tracking(Style.current.eyebrowTracking)
                .foregroundStyle(face.muted)
            Text(camera.transcript.isEmpty
                 ? (previewStaged
                    ? "the spare regulator is in the blue tub behind Doris"
                    : "…")
                 : camera.transcript)
                .font(.body_(15))
                .foregroundStyle(face.text)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// KEEP and DISCARD are the same size and the same shape.
    ///
    /// DISCARD used to be bare text beside a filled bar, which made it look
    /// like a caption rather than a control -- and it is the one that throws
    /// work away. Equal weight, outlined rather than filled: same target, and
    /// the fill still says which one is the ordinary answer.
    private var saveRow: some View {
        HStack(spacing: 12) {
            Button {
                if camera.recording { camera.stopRecording() }
                Haptics.filed()
                Sounds.filed()
                camera.finish(attached: [])
                saved = CaptureStore.all()
                justSaved = true
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.6) {
                    justSaved = false
                }
            } label: {
                buttonFace(justSaved ? "KEPT" : "KEEP THIS", filled: true)
            }
            .buttonStyle(.plain)
            .disabled(camera.recording)
            .opacity(camera.recording ? 0.4 : 1)

            Button {
                if camera.recording { camera.stopRecording() }
                Haptics.tap()
                camera.lastPhoto = nil
                camera.transcript = ""
            } label: {
                buttonFace("DISCARD", filled: false)
            }
            .buttonStyle(.plain)
        }
    }

    private func buttonFace(_ title: String, filled: Bool) -> some View {
        Text(title)
            .font(.eyebrow(13))
            .tracking(Style.current.eyebrowTracking)
            .foregroundStyle(filled ? face.ground : face.text)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 15)
            .background {
                RoundedRectangle(cornerRadius: Self.corner)
                    .fill(filled ? face.accent : Color.clear)
                    .overlay {
                        RoundedRectangle(cornerRadius: Self.corner)
                            .stroke(filled ? Color.clear : face.muted.opacity(0.55),
                                    lineWidth: 1.5)
                    }
            }
    }

    /// The list moved behind a button. The screen you come to in order to make
    /// a note should open on a viewfinder, not on the notes you already made.
    private var keptButton: some View {
        Button {
            Haptics.tap()
            showingKept = true
        } label: {
            HStack(spacing: 8) {
                Text(Style.current.label("Show notes"))
                    .font(.eyebrow(13))
                    .tracking(Style.current.eyebrowTracking)
                Text("\(previewStaged ? 7 : saved.count)")
                    .font(.data(13))
                    .foregroundStyle(face.muted)
                Spacer()
                Image(systemName: "chevron.right")
                    .font(.system(size: 12, weight: .bold))
                    .foregroundStyle(face.muted)
            }
            .foregroundStyle(face.text)
            .padding(.horizontal, 16)
            .padding(.vertical, 15)
            .background {
                RoundedRectangle(cornerRadius: Self.corner)
                    .stroke(face.muted.opacity(0.35), lineWidth: 1)
            }
        }
        .buttonStyle(.plain)
    }

    private static func clock(_ t: TimeInterval) -> String {
        String(format: "%d:%02d", Int(t) / 60, Int(t) % 60)
    }

    /// A capture's id is its folder name — an ISO-8601 stamp with the colons
    /// swapped out, because a colon is not legal in a path component. Putting
    /// them back is how the date is recovered; there is no index to consult.
    private static func when(_ capture: Capture) -> String {
        let iso = capture.id.replacingOccurrences(
            of: "-", with: ":", range: capture.id.range(of: "T").map {
                $0.upperBound..<capture.id.endIndex
            })
        guard let date = ISO8601DateFormatter().date(from: iso) else {
            return String(capture.id.prefix(10))
        }
        return stamp.string(from: date)
    }

    private static let stamp: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "EEE HH:mm"
        return f
    }()
}
