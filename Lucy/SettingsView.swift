import SwiftUI

// The dials, on a screen instead of in the menu.
//
// Every experiment flag started life as one more row in `LucyMenu`'s Lab
// section, and the menu had already been pushed off the bottom of the screen
// once by exactly that growth — which is why Register and Length ended up as
// submenus, and why a fifth dial would have had to be a submenu too. A menu
// is a list of things to DO. These are things to SET, and each one needs a
// sentence saying what it does, because "Loose" and "Terse" mean nothing on
// their own to the person deciding between them at 2am in the dust.
//
// Most of it ships now. It used to be debug-only on the reasoning that a
// tester should see the shipped defaults and nothing else -- and that held
// while the defaults were the only thing anyone had measured.
//
// What changed is that the defaults turned out to be a floor rather than an
// answer. Rule 1 buys grounding and pays for it in voice: with no rules she
// invents a camp specific in 82% of answers, with them 2%, and the flat
// listing everyone dislikes is that price. `register` and `answerLength` are
// the two dials that move voice WITHOUT touching the rules, so they are the
// only lever that is safe to hand over, and a dial nobody can reach is the
// same as a dial that does not exist. `loose` is still deliberately too much;
// that is what a dial is for.
//
// The retrieval A/B switches stay behind DEBUG. Those turn a fix OFF for
// comparison, both default on because that is the behaviour believed correct,
// and a tester flipping one is running an experiment whose result reaches
// nobody.
//
// The two Experiments sections are rendered by iterating `SettingsCatalog`
// rather than by hand, so `SettingsCatalogTests` can hold the screen to
// covering every flag on `Experiments`. A dial nobody can reach is the same
// as a dial that does not exist.

struct SettingsView: View {
    let face: Face
    @ObservedObject private var experiments = Experiments.shared
    @Environment(\.dismiss) private var dismiss

    /// `Device.override` is a plain static that publishes nothing, so the
    /// rows read it directly and this only exists to force a repaint. It is
    /// deliberately NOT a copy of the value: `LucyMenu` is on every tab, so
    /// each tab presents its own `SettingsView`, and a mirror seeded in
    /// `onAppear` would show the Memory tab's sheet a choice made on the
    /// Chat tab's. Reading the static every time cannot be stale.
    @State private var repaint = 0

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                header
                brain
                voice
                // The retrieval A/B switches stay on dev builds. See
                // `SettingsCatalog.flags` for why.
                #if DEBUG
                retrieval
                #endif
                install
            }
            .padding(22)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .scrollIndicators(.hidden)
        .background(face.ground)
        .preferredColorScheme(face.night ? .dark : .light)
        // Any instance, on any tab, when any of them switches the model.
        .onReceive(NotificationCenter.default.publisher(for: .lucyModelChanged)) { _ in
            repaint += 1
        }
    }

    private var header: some View {
        HStack {
            Text(Style.current.label("Settings"))
                .font(.eyebrow(15))
                .tracking(Style.current.eyebrowTracking + Style.current.boost(0.5))
                .foregroundStyle(face.accent)
            Spacer()
            Button("Done") { dismiss() }
                .font(.body_(16)).foregroundStyle(face.muted)
        }
        .padding(.bottom, 22)
    }

    // MARK: - Her brain

    /// Not an `Experiments` flag: this writes `Device.override`, which lives
    /// in `ModelDownload` because the download decision needs it before any
    /// of this exists. It gets the same shape as the dials below so the
    /// screen reads as one list, and it posts `.lucyModelChanged` because the
    /// chat screen owns the loaded model and cannot see this sheet.
    private var brain: some View {
        VStack(alignment: .leading, spacing: 0) {
            heading("Her brain")
            choiceRow(name: "Auto", blurb: "Let the phone decide from its RAM.",
                      picked: Device.override == nil) { switchModel(nil) }
            choiceRow(name: "Full", blurb: "Gemma 4 E2B, 3.1 GB.",
                      picked: Device.override == .full) { switchModel(.full) }
            choiceRow(name: "Light", blurb: "Gemma 3 1B, 806 MB.",
                      picked: Device.override == .small) { switchModel(.small) }
            footnote(ModelDownload.shared.loadedDescription)
        }
        .padding(.bottom, 30)
    }

    private func switchModel(_ choice: Device.Override?) {
        Device.override = choice
        repaint += 1
        Task {
            await ModelDownload.shared.check()
            NotificationCenter.default.post(name: .lucyModelChanged, object: nil)
        }
    }

    // MARK: - The dials

    private var voice: some View {
        group("Her voice", choices: SettingsCatalog.voice)
    }

    #if DEBUG
    private var retrieval: some View {
        VStack(alignment: .leading, spacing: 0) {
            heading("Retrieval")
            ForEach(SettingsCatalog.flags) { flag in
                flagRow(flag)
            }
        }
        .padding(.bottom, 30)
    }
    #endif

    private func group(_ title: String, choices: [SettingsChoice]) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            heading(title)
            ForEach(choices) { choice in
                // A sub-heading inside the section, not a row: gold and
                // tracked so "Register" and "Length" read as two dials under
                // "Her voice" rather than as two more options to pick.
                Text(Style.current.label(choice.title))
                    .font(.eyebrow(11))
                    .tracking(Style.current.eyebrowTracking)
                    .foregroundStyle(face.warm)
                    .padding(.bottom, 6)
                ForEach(choice.options) { option in
                    choiceRow(name: option.name, blurb: option.blurb,
                              picked: choice.selected(experiments) == option.name) {
                        choice.select(experiments, option.name)
                    }
                }
                Spacer(minLength: 16)
            }
        }
        .padding(.bottom, 14)
    }

    // MARK: - Rows

    private func heading(_ text: String) -> some View {
        Text(Style.current.label(text))
            .font(.eyebrow(12))
            .tracking(Style.current.eyebrowTracking)
            .foregroundStyle(face.muted)
            .padding(.bottom, 12)
    }

    /// One option of a picker. A full-width row rather than a pill, because
    /// every option carries a sentence and the sentences are the point —
    /// a row of four capsules reading Dry/Default/Warm/Loose tells the person
    /// choosing precisely nothing.
    private func choiceRow(name: String, blurb: String, picked: Bool,
                           tap: @escaping () -> Void) -> some View {
        Button(action: tap) {
            HStack(alignment: .top, spacing: 11) {
                Circle()
                    .strokeBorder(picked ? face.accent : face.muted.opacity(0.5),
                                  lineWidth: picked ? 5 : 1.5)
                    .frame(width: 15, height: 15)
                    .padding(.top, 2)
                VStack(alignment: .leading, spacing: 2) {
                    Text(name)
                        .font(.body_(16))
                        .foregroundStyle(picked ? face.text : face.muted)
                    Text(blurb)
                        .font(.data(12)).foregroundStyle(face.muted)
                        .fixedSize(horizontal: false, vertical: true)
                        .multilineTextAlignment(.leading)
                }
                Spacer(minLength: 0)
            }
            .contentShape(Rectangle())
            .padding(.vertical, 6)
        }
        .buttonStyle(.plain)
    }

    #if DEBUG
    private func flagRow(_ flag: SettingsFlag) -> some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(flag.title)
                    .font(.body_(16)).foregroundStyle(face.text)
                Text(flag.blurb)
                    .font(.data(12)).foregroundStyle(face.muted)
                    .fixedSize(horizontal: false, vertical: true)
                    .multilineTextAlignment(.leading)
            }
            Spacer(minLength: 0)
            Toggle("", isOn: flag.binding(experiments))
                .labelsHidden()
                .tint(face.accent)
        }
        .padding(.vertical, 8)
    }
    #endif

    private func footnote(_ text: String) -> some View {
        Text(text)
            .font(.data(12)).foregroundStyle(face.muted)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.top, 8)
    }

    // MARK: - This install

    /// Deliberately the same three facts, in the same words, as the menu's
    /// "This build" submenu and `InfoView.about`. Someone reading a bug report
    /// aloud should not have to work out which of three screens they are on.
    private var install: some View {
        VStack(alignment: .leading, spacing: 10) {
            heading("This install")
            line("Build", BuildInfo.stamp)
            line("Knowledge", BuildInfo.knowledge)
            line("Her brain", ModelDownload.shared.loadedDescription)
        }
    }

    private func line(_ label: String, _ value: String) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Text(label)
                .font(.data(13)).foregroundStyle(face.muted)
                .frame(width: 82, alignment: .leading)
            Text(value)
                .font(.data(13)).foregroundStyle(face.text)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
    }
}
