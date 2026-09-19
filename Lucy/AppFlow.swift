import SwiftUI

// The whole app as one navigation graph:
//
//   CHAT ──ask──────▶ Lucy answers, receipts attached, tap one to see the
//     │                message it came from
//     └──camera / "remember this"──▶ capture ──▶ appears as a turn in the thread
//
// One surface. The conversation is also the memory: scrolling back through it
// is how you find what you saved, so there is no separate list to keep in step.

struct AppFlow: View {
    let face: Face

    /// The tab labels, up from the system's 10pt whisper. SwiftUI offers no
    /// per-tabItem font, so this is UIKit appearance, set once -- rounded
    /// and semibold to match everything above the bar.
    init(face: Face) {
        self.face = face
        let base = UIFont.systemFont(ofSize: 12, weight: .semibold)
        let font: UIFont
        if let rounded = base.fontDescriptor.withDesign(.rounded) {
            font = UIFont(descriptor: rounded, size: 12)
        } else {
            font = base
        }
        let appearance = UITabBarAppearance()
        appearance.configureWithDefaultBackground()
        for item in [appearance.stackedLayoutAppearance,
                     appearance.inlineLayoutAppearance,
                     appearance.compactInlineLayoutAppearance] {
            item.normal.titleTextAttributes[.font] = font
            item.selected.titleTextAttributes[.font] = font
        }
        UITabBar.appearance().standardAppearance = appearance
        UITabBar.appearance().scrollEdgeAppearance = appearance
    }

    var body: some View {
        // The app is still the conversation — you land talking to Lucy, and
        // asking her to remember something is a button in the composer rather
        // than a place you go.
        //
        // PEOPLE is the one thing that earns a tab of its own. Everything else
        // is a question, and a question belongs in the chat; but "who is that,
        // and have they camped before" is browsing, not asking, and browsing
        // through a conversation is miserable. Chat stays the first tab and
        // the one you open on.
        if let store = EntityStore(path: PreviewRoot.fixturePath) {
            // Four tabs: ask, browse, keep, read. Asking and keeping were one
            // surface for a while and it never sat right — a question you want
            // answered now and a thing you want handed to the camp later are
            // different acts, and folding them together put a shutter button
            // on every question.
            TabView {
                // (Tab label type is set once in `init` below -- SwiftUI has
                // no per-tabItem font, so it goes through UIKit appearance.)
                // SnailTab, not Snail. The same artwork as the app icon, but
                // rasterised into a 25pt box at 1x/2x/3x -- the Snail imageset
                // is a bare SVG with a viewBox and no width or height, so a
                // tab bar renders it at its natural size, which is the whole
                // screen. It filled the bar and pushed Info off the edge.
                // Marked template so it tints with the accent alongside the
                // SF Symbols beside it rather than sitting there as a sticker.
                ChatView(face: face, store: store)
                    .tabItem { Label("Lucy", image: "SnailTab") }
                PeopleView(face: face, store: store)
                    .tabItem { Label("Snails", systemImage: "person.2") }
                NoteView(face: face)
                    .tabItem { Label("Memory", systemImage: "brain") }
                // The manual and the schedule, when they exist. It is a tab
                // rather than a menu item because those are the two things
                // people will go looking for, and looking for something in a
                // hamburger is how you conclude it isn't there.
                InfoView(face: face)
                    .tabItem { Label("Info", systemImage: "info.circle") }
            }
            .tint(face.accent)
        } else {
            Text("KNOWLEDGE NOT LOADED").font(.display(24))
        }
    }
}

/// ASK with a way back out. AskView itself has no chrome so it can also be
/// embedded, but as a mode it needs a close.
struct AskFlowScreen: View {
    let face: Face
    let store: EntityStore?
    var onClose: () -> Void = {}

    init(face: Face, onClose: @escaping () -> Void = {}) {
        self.face = face
        self.onClose = onClose
        self.store = EntityStore(path: PreviewRoot.fixturePath)
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text(Style.current.label("Ask"))
                    .font(.eyebrow(15))
                    .tracking(Style.current.eyebrowTracking + Style.current.boost(0.5))
                    .foregroundStyle(face.accent)
                Spacer()
                Button("Close", action: onClose)
                    .font(.body_(16))
                    .foregroundStyle(face.muted)
            }
            .padding(.horizontal, 22).padding(.top, 8).padding(.bottom, 6)

            if let store {
                AskView(face: face, store: store, showsEyebrow: false)
            } else {
                Text("FIXTURE NOT LOADED").font(.display(24)).foregroundStyle(face.text)
            }
        }
        .background(face.ground)
        .preferredColorScheme(face.night ? .dark : .light)
    }
}

/// REMEMBER walks framing → captured → filed. The stages are driven here so
/// the screen itself stays a pure function of its stage.
struct RememberFlow: View {
    let face: Face
    var onClose: () -> Void = {}

    @State private var stage: CaptureStage = .framing
    @StateObject private var camera = CameraController()
    @Environment(\.scenePhase) private var scenePhase

    /// Kept in step with the strip the screen shows, so what is written to
    /// meta.json is what the person actually saw attached.
    private static let stamp: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "EEE HH:mm"
        return f
    }()

    var body: some View {
        RememberScreen(
            face: face,
            stage: stage,
            onShutter: {
                switch stage {
                case .framing:
                    camera.capturePhoto()
                    stage = .captured
                case .captured:
                    // Releasing the shutter mid-recording would otherwise strand
                    // a half-written file.
                    if camera.recording { camera.stopRecording() }
                    Haptics.filed()
                    Sounds.filed()
                    camera.finish(attached: ["7:59 & C",
                                             Self.stamp.string(from: Date()),
                                             "during Bar shift"])
                    stage = .saved
                case .saved:
                    onClose()
                }
            },
            onClose: {
                if camera.recording { camera.stopRecording() }
                camera.stop()
                onClose()
            },
            camera: camera
        )
        .task {
            Haptics.prepare()
            await camera.start()
            // Asked here too, not just in ASK: REMEMBER may be the first screen
            // someone opens, and a recorder with no permission fails silently.
            if !PreviewRoot.skipsPermissionPrompts { await camera.primeMic() }
        }
        .onDisappear {
            // Leaving the screen mid-recording would strand an unfinalised file.
            if camera.recording { camera.stopRecording() }
            camera.stop()
        }
        // An m4a is only playable once stop() has written its index. If the
        // phone is pocketed, a call arrives, or the app is killed while
        // recording, the file is 68KB of audio nothing can open. Finalise the
        // moment we stop being the active scene.
        .onChange(of: scenePhase) { _, phase in
            if phase != .active, camera.recording { camera.stopRecording() }
        }
    }
}


/// ASK as a mode, voice first.
struct AskVoiceFlow: View {
    let face: Face
    var onClose: () -> Void = {}

    var body: some View {
        if let store = EntityStore(path: PreviewRoot.fixturePath) {
            ChatView(face: face, store: store, onClose: onClose)
        } else {
            Text("FIXTURE NOT LOADED").font(.display(24)).foregroundStyle(face.text)
        }
    }
}
