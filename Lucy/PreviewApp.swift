import SwiftUI
import Speech
import AVFoundation
import UIKit

/// Exists for one reason: a background `URLSession` can finish while the app is
/// not running, and iOS relaunches us to say so. Without this the model
/// download completes and nothing hears about it until the next cold start.
final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(_ application: UIApplication,
                     handleEventsForBackgroundURLSession identifier: String,
                     completionHandler: @escaping () -> Void) {
        Task { @MainActor in
            ModelDownload.shared.backgroundCompletion = completionHandler
        }
    }
}

// Simulator harness for the Lucy PT screens. It exists so the design can be
// screenshotted against real-shaped data before any of it is wired into
// LucyPT proper -- the same approach the two-mode prototype used.
//
// Launch arguments:
//   --screen home|ask|entity   which screen to render (default: home)
//   --entity Doris             which entity, for --screen entity
//   --query "what is doris"    pre-run a question, for --screen ask
//   --night                    render the night face
//   --expand                   open every fact's evidence (entity screen)

@main
struct PreviewApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var delegate

    init() {
        // Must be set before any Face or Font is resolved.
        if ProcessInfo.processInfo.arguments.contains("--soft") { Style.current = .soft }

        // Device-side evidence: the simulator's buttons pass their UI tests, so
        // anything failing on the phone differs in launch args or permissions.
        let args = ProcessInfo.processInfo.arguments.dropFirst().joined(separator: " ")
        print("LUCY: args=[\(args)]")

        // --model full|small|auto, so the choice can be flipped over the cable
        // without tapping through the app. Debug only, like the picker itself.
        #if DEBUG
        let argv = ProcessInfo.processInfo.arguments
        if let i = argv.firstIndex(of: "--model"), i + 1 < argv.count {
            let want = argv[i + 1]
            Device.override = want == "auto" ? nil : Device.Override(rawValue: want)
            print("LUCY: model override -> \(want)")
        }
        #endif
        print("LUCY: speechAuth=\(SFSpeechRecognizer.authorizationStatus().rawValue) "
              + "onDevice=\(SFSpeechRecognizer(locale: Locale(identifier: "en-US"))?.supportsOnDeviceRecognition ?? false)")
        print("LUCY: micPermission=\(AVAudioApplication.shared.recordPermission.rawValue)")
    }

    var body: some Scene {
        WindowGroup { PreviewRoot() }
    }
}

struct PreviewRoot: View {
    private static let args = ProcessInfo.processInfo.arguments

    /// Screenshot runs pass --no-prompt. simctl can grant the microphone but
    /// not speech recognition, so without this every render is a photograph of
    /// a permission dialog.
    static var skipsPermissionPrompts: Bool {
        ProcessInfo.processInfo.arguments.contains("--no-prompt")
    }

    /// Dark is the default theme. --day gives the alkali light face,
    /// --night gives the red-shifted dark-adaptation mode.
    private var face: Face {
        if Self.args.contains("--day") { return .day }
        if Self.args.contains("--night") { return .night }
        return .dark
    }
    private var screen: String { Self.value(for: "--screen") ?? "home" }
    private var entityName: String { Self.value(for: "--entity") ?? "Doris" }

    /// `--now "MM-dd HH:mm"`, read as 2026. Renders only.
    static var previewNow: Date? {
        guard let raw = value(for: "--now") else { return nil }
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd HH:mm"
        return f.date(from: "2026-\(raw)")
    }

    private static func value(for flag: String) -> String? {
        guard let i = args.firstIndex(of: flag), i + 1 < args.count else { return nil }
        return args[i + 1]
    }

    var body: some View {
        if let store = EntityStore(path: Self.fixturePath) {
            switch screen {
            case "ask":
                AskView(face: face, store: store)
            case "speechcheck":
                SpeechDiagnostics(face: face)
            case "chat":
                ChatView(face: face, store: store,
                         seed: Self.value(for: "--query").map { [$0] } ?? [])
            case "chat-recording":
                ChatView(face: face, store: store, previewRecording: true)
            // The "Earlier" control needs something behind it, and a fresh
            // simulator's ChatLog is empty -- it lives in Documents and is
            // written by asking, not shipped in the bundle. So this route
            // plants a few turns first. Simulator only, by virtue of being a
            // render route.
            case "chat-earlier":
                ChatView(face: face, store: store)
                    .onAppear { Self.plantConversation() }
            case "voice":
                AskVoiceView(face: face, store: store)
            case "voice-listening":
                AskVoiceView(face: face, store: store, state: .listening)
            case "flow":
                AppFlow(face: face)
            case "remember":
                RememberFlow(face: face)
            case "remember-captured":
                RememberScreen(face: face, stage: .captured)
            case "remember-saved":
                RememberScreen(face: face, stage: .saved)
            case "note":
                NoteView(face: face)
            case "sync":
                SyncView(face: face)
            case "note-staged":
                NoteView(face: face, previewStaged: true)
            case "info":
                InfoView(face: face)
            case "manual":
                manualScreen(store)
            case "manual-open":
                manualScreen(store, open: true)
            // Opened, because the figures are the point and a render of
            // collapsed headings would not show one.
            case "build":
                DocumentView(face: face, title: "Build",
                             subtitle: "Build (2026)",
                             content: InfoView.buildFile ?? "",
                             previewOpensFirst: true)
            // The camp layout plan, which is the figure worth looking at.
            case "build-figure":
                DocumentView(face: face, title: "Build",
                             subtitle: "Build (2026)",
                             content: InfoView.buildFile ?? "",
                             previewQuery: "Poles Color Key")
            // Debug only, like the screen itself: `SettingsView` does not
            // exist in a release build.
            #if DEBUG
            case "settings":
                // The page content, not the sheet: on the phone this is
                // presented from the menu and gets a sheet's corners and
                // inset. A render of this screen shows the rows, and does
                // not show how the sheet sits.
                SettingsView(face: face)
            #endif
            // The schedule reads a bundled file, not the database, so this
            // route renders whatever actually shipped in the app -- which is
            // the thing worth looking at when the resource entry is the part
            // that silently fails.
            // The three city listings. Like the schedule, they read bundled
            // files rather than the database, so a render shows what actually
            // shipped -- which is the part that fails silently.
            case "events":
                PlayaEventsView(face: face)
            // A search with its first hit open: the only way a render shows
            // what an event actually says.
            case "events-found":
                PlayaEventsView(face: face,
                                previewQuery: Self.value(for: "--query")
                                              ?? "robot heart",
                                previewExpandsFirst: true)
            // Mid-burn, at a fixed clock: shows the today-cut and the
            // "Earlier today" pill. `--now "09-02 21:15"`.
            case "events-now":
                PlayaEventsView(face: face, previewNow: Self.previewNow)
            case "camps":
                PlayaListingView.camps(face: face)
            case "art":
                PlayaListingView.art(face: face)
            // A search with its first described hit open, so a render shows
            // that the descriptions actually arrived from the API files.
            case "art-open":
                artOpenScreen()
            case "schedule":
                ScheduleView(face: face, content: InfoView.scheduleFile ?? "")
            case "schedule-folded":
                ScheduleView(face: face, content: InfoView.scheduleFile ?? "",
                             previewStartsFolded: true)
            case "rota":
                RotaView(face: face, assignments: store.rota())
            case "rota-mine":
                RotaView(face: face, assignments: store.rota(),
                         filter: "Piotr")
            case "people":
                PeopleView(face: face, store: store,
                           expandsFirst: Self.args.contains("--expand"),
                           initialQuery: Self.value(for: "--query") ?? "")
            case "icons":
                IconCandidatesScreen(face: face)
            case "entity":
                entityScreen(store)
            default:
                // The only path a real launch takes -- `--screen` is passed by
                // render.sh and the UI tests, and those run in a simulator that
                // has no model and must not sit behind the download gate.
                ModelGate(face: face) { AppFlow(face: face) }
            }
        } else {
            // Loud on purpose: a silent empty screen would be indistinguishable
            // from a design with nothing in it.
            VStack(spacing: 8) {
                Text("FIXTURE NOT LOADED").font(.display(28))
                Text(Self.fixturePath).font(.data(11)).multilineTextAlignment(.center)
            }
            .padding(24)
        }
    }

    private func artOpenScreen() -> PlayaListingView {
        var art = PlayaListingView.art(face: face)
        art.previewQuery = Self.value(for: "--query")
        art.previewExpandsFirst = true
        return art
    }

    /// The manual reader, for a screenshot. `--screen manual-open` expands
    /// the first section, because a list of sixty collapsed headings does not
    /// show what the body looks like.
    @ViewBuilder
    private func manualScreen(_ store: EntityStore, open: Bool = false) -> some View {
        if let m = store.campManual(), let doc = store.document(id: m.id) {
            DocumentView(face: face, title: "The manual",
                         subtitle: doc.title, content: doc.content,
                         previewOpensFirst: open)
        } else {
            Text("NO MANUAL IN THIS DATABASE").font(.display(22))
        }
    }

    @ViewBuilder
    private func entityScreen(_ store: EntityStore) -> some View {
        if let entity = store.findEntity(named: entityName) {
            let facts = store.facts(entityID: entity.id)
            EntityRecordView(
                face: face,
                entity: entity,
                factsByCategory: facts,
                initiallyExpanded: Self.args.contains("--expand")
                    ? Set(facts.values.flatMap { $0 }.map(\.id))
                    : []
            )
        } else {
            Text("NO SUCH ENTITY: \(entityName)").font(.display(24))
        }
    }

    /// The enrichment database ships as a read-only bundle resource: 129
    /// entities and 484 dated facts, each carrying the message it came from.
    /// Regenerate with `python scripts/build_preview_db.py`.
    /// A short conversation in `ChatLog`, for photographing the "Earlier"
    /// control. Written only when the log is empty, so re-running a render
    /// does not stack six copies of the same exchange.
    @MainActor
    static func plantConversation() {
        guard ChatLog.shared.count == 0 else { return }
        let turns = [
            ("Where do the grey water jugs go",
             "The pump hose goes into the gray water tank fill port on top of "
             + "the tank. Always run dirty water through the filter first."),
            ("Who should I ask about the generator",
             "Roeland and Oswald take the power shifts. Either of them will "
             + "know."),
            ("What time is dinner",
             "Dinner runs about five to nine, cooked by whoever is on the "
             + "shift that day."),
        ]
        for (q, a) in turns {
            ChatLog.shared.record(question: q, answer: a)
        }
    }

    static var fixturePath: String {
        Bundle.main.path(forResource: "enriched_preview", ofType: "db") ?? "(not in bundle)"
    }
}
