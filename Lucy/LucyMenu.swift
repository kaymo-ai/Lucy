import SwiftUI

// The app's one menu, on every tab.
//
// It used to hang off the chat screen only, which meant Sync — the thing you
// press after a day of noting — was three taps and a tab switch away from the
// tab you were noting on. A menu that exists on one screen is a menu people
// forget the app has.

extension Notification.Name {
    /// Posted when the model is switched from the menu. The chat screen owns
    /// the loaded model but the menu can now be opened from any tab, so the
    /// two cannot talk through a closure any more.
    static let lucyModelChanged = Notification.Name("lucy.model.changed")
}

struct LucyMenu: View {
    let face: Face

    @State private var showingSync = false
    @State private var conversationCount = 0
    @State private var showingSettings = false

    var body: some View {
        Menu {
            Section("The camp") {
                Button("Sync") { showingSync = true }
            }
            // She keeps every exchange for as long as the app is installed;
            // the count keeps that visible. The "Forget everything" button
            // that used to sit here was removed at Marcus's call (2026-08-11):
            // her memory of the conversation IS her usefulness across the
            // week, and a one-tap wipe in a shared-tool menu lost a week of
            // context to a single curious press. Transparency stays; the
            // destruction goes.
            Section("Your conversations") {
                Text(conversationCount == 0 ? "Nothing yet"
                     : "\(conversationCount) exchange\(conversationCount == 1 ? "" : "s")")
            }
            // The picker is back in release, where it was debug-only from
            // b7e7f6b until an 8 GB iPhone 16 Pro was classed as a 6 GB phone
            // and handed the 1B model with no way back. Auto is still the
            // default and still right for most phones; this is the escape
            // hatch for when it is wrong in either direction -- a phone that
            // can run Full and wasn't offered it, and a phone that was given
            // Full and gets too hot.
            //
            // Switching back is instant: both files stay in Documents, so
            // Light is still there after a phone has fetched Full.
            Section("Her brain") {
                Text(ModelDownload.shared.loadedDescription)
                Button(pick("Auto", on: Device.override == nil)) { switchModel(nil) }
                Button(pick("Full — Gemma 4 E2B", on: Device.override == .full)) {
                    switchModel(.full)
                }
                Button(pick("Light — Gemma 3 1B", on: Device.override == .small)) {
                    switchModel(.small)
                }
                // The thermal report, on the hardware and not on the choice.
                if Device.thermallyMarginal {
                    Text("This phone may run hot on Full.")
                }
            }
            // Everything that used to be the Lab section, on a screen that
            // has room to say what each dial does. The menu grew one row per
            // experiment and had already been pushed off the bottom of the
            // screen once; a submenu per dial bought a build's worth of time
            // and no more.
            //
            // Ships now. The screen carries the two voice dials, which are the
            // only lever that moves how she sounds without touching the rules
            // that keep her grounded; the retrieval A/B switches on it stay
            // behind DEBUG. See the header of `SettingsView`.
            //
            // Above "This build" and in its own Section, because as the last
            // row under a submenu it was missed. Not because it failed to
            // render: it rendered, and MenuRenderTests photographed it doing
            // so. The bottom of a menu is simply where a row goes unread.
            Section {
                Button("Settings") { showingSettings = true }
            }
            // Which build, from the build itself. Two apps named Lucy live
            // on the dev phone and the cable one changes ten times an
            // afternoon; "is this the one with X" was being settled by
            // re-installing. Same reasoning as naming the loaded model: the
            // app says, so nobody argues. The database line rides along
            // because knowledge changes on a different schedule from code.
            //
            // A submenu, after two visibly wrong shapes: three full-size
            // rows read as more buttons and pushed the menu off the bottom
            // of the screen, and a header-only section is silently dropped
            // by Menu — both are in MenuRenderTests screenshots. One row in
            // the menu's own style; the detail costs nothing until asked.
            Section {
                Menu("This build") {
                    Text(BuildInfo.stamp)
                    Text(BuildInfo.knowledge)
                }
            }
        } label: {
            Image(systemName: "line.3.horizontal")
                .font(.system(size: 17, weight: .semibold))
                .foregroundStyle(face.muted)
                .frame(width: 34, height: 34)
                .contentShape(Rectangle())
        }
        .accessibilityLabel("Menu")
        .onAppear { conversationCount = ChatLog.shared.count }
        .sheet(isPresented: $showingSync) { SyncView(face: face) }
        .sheet(isPresented: $showingSettings) { SettingsView(face: face) }
    }

    /// A tick in the label rather than a real checkmark. `Menu` will not show
    /// selection state on a plain Button, and the three rows have to read as
    /// one choice or nobody can tell which model they are on.
    private func pick(_ label: String, on: Bool) -> String {
        (on ? "✓ " : "   ") + label
    }

    /// Repoints the app at another model and reloads.
    ///
    /// `check()` re-reads the manifest and compares it to the file already in
    /// Documents, so choosing a model the phone does not have yet lands on the
    /// gate with a download offer rather than on a missing file. Both models
    /// stay in Documents once fetched, so coming back is instant.
    private func switchModel(_ choice: Device.Override?) {
        Device.override = choice
        Task {
            await ModelDownload.shared.check()
            NotificationCenter.default.post(name: .lucyModelChanged, object: nil)
        }
    }
}
