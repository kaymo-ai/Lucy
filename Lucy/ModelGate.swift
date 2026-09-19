import SwiftUI

// The screen that stands between a fresh install and a working Lucy.
//
// Without the model, `LucyBrain.answer` returns false and every reply comes
// from the string templates that predate the LLM. Nothing said so. The app
// looked like it was working and was quietly answering from a worse brain --
// which is indistinguishable, from the outside, from a model that got worse.
//
// So the absence is now the loudest thing on the screen, and you cannot get
// past it by accident.

struct ModelGate<Content: View>: View {
    let face: Face
    @ViewBuilder var content: () -> Content

    @StateObject private var download = ModelDownload.shared

    var body: some View {
        Group {
            switch download.state {
            case .ready:
                content()
            case .checking:
                waiting("Looking for her brain…")
            default:
                gate
            }
        }
        .task {
            // A model already on the phone settles this in milliseconds; only
            // a fresh install ever sees the gate.
            if !download.state.isReady { await download.check() }
            // --auto-download starts the fetch without a tap, so the one path
            // every tester walks first can be exercised in the simulator. It
            // is the only way to test it here: the button needs a finger.
            if ProcessInfo.processInfo.arguments.contains("--auto-download"),
               case .needed = download.state {
                download.begin()
            }
        }
    }

    // MARK: - Pieces

    private func waiting(_ label: String) -> some View {
        VStack(spacing: 14) {
            ProgressView().tint(face.accent)
            Text(label).font(.data(13)).foregroundStyle(face.muted)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(face.ground)
    }

    private var gate: some View {
        VStack(alignment: .leading, spacing: 0) {
            Spacer()

            Text(Style.current.label(Device.tooSmall ? "This phone\nis too small" : "Lucy needs\nher brain"))
                .font(.display(38))
                .foregroundStyle(face.text)
                .lineSpacing(2)

            Text(explanation)
                .font(.body_(16))
                .foregroundStyle(face.muted)
                .padding(.top, 14)
                .fixedSize(horizontal: false, vertical: true)

            if case .downloading(let received, let total) = download.state {
                progress(received: received, total: total).padding(.top, 26)
            }

            if let action {
                Button(action: action.run) {
                    Text(action.label)
                        .font(.display(17))
                        .foregroundStyle(face.ground)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 16)
                        .background(face.accent, in: RoundedRectangle(cornerRadius: 14))
                }
                .padding(.top, 26)
                .buttonStyle(SquishButtonStyle())
            }

            #if DEBUG
            devModelPicker.padding(.top, 22)
            #endif

            Spacer()

            // The one thing someone in a parking lot needs to know -- but not
            // on a phone that cannot download it at all, where advice about
            // wi-fi is advice about something they cannot do.
            if !Device.tooSmall {
                Text(Style.current.isPlayful
                     ? "Getting Lucy's brain on board is a one-time, on-wi-fi job — "
                       + "there's no signal on playa, and she keeps it once she has it."
                     : "Do this on wi-fi, before you leave. There is no signal on "
                       + "playa, and she keeps her brain once she has it.")
                    .font(.data(12))
                    .foregroundStyle(face.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.horizontal, 26)
        .padding(.vertical, 34)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        .background(face.ground)
    }

    /// Dev builds only. Testing the 6 GB experience otherwise requires a 6 GB
    /// phone, and the choice is invisible until you read the app's container
    /// over a cable -- which is how a false alarm about "it downloaded the
    /// small one" cost twenty minutes.
    #if DEBUG
    @ViewBuilder
    private var devModelPicker: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("DEV — WHICH BRAIN")
                .font(.eyebrow(10))
                .tracking(Style.current.eyebrowTracking)
                .foregroundStyle(face.muted)
            HStack(spacing: 8) {
                ForEach([Device.Override?.none, .some(.full), .some(.small)], id: \.self) { opt in
                    let label = opt.map { $0 == .full ? "Full" : "Light" } ?? "Auto"
                    Button {
                        Device.override = opt
                        Task { await download.check() }
                    } label: {
                        Text(label)
                            .font(.data(12))
                            .foregroundStyle(Device.override == opt ? face.ground : face.text)
                            .padding(.horizontal, 14)
                            .padding(.vertical, 8)
                            .background(Device.override == opt ? face.accent : face.raised,
                                        in: Capsule())
                    }
                    .buttonStyle(.plain)
                }
            }
            Text(Device.describeChoice(small: download.usingSmallModel))
                .font(.data(11))
                .foregroundStyle(face.muted)
        }
    }
    #endif

    private func progress(received: Int64, total: Int64) -> some View {
        let fraction = total > 0 ? Double(received) / Double(total) : 0
        return VStack(alignment: .leading, spacing: 8) {
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(face.hairline)
                    Capsule().fill(face.accent)
                        .frame(width: max(6, geo.size.width * fraction))
                }
            }
            .frame(height: 8)

            HStack {
                Text("\(gb(received)) of \(gb(total))")
                Spacer()
                Text("\(Int(fraction * 100))%")
            }
            .font(.data(12))
            .foregroundStyle(face.muted)
        }
    }

    // MARK: - Words

    private var explanation: String {
        if Device.tooSmall {
            return "This phone has \(String(format: "%.0f", Device.memoryGiB)) GB "
                 + "of memory and Lucy's model needs about 4 GB free to run. It "
                 + "would download for twenty minutes and then be shut down by "
                 + "iOS, so there is no point starting. She needs an iPhone 15 "
                 + "Pro or newer."
        }
        switch download.state {
        case .needed(let bytes):
            // The lighter model is not a smaller download of the same thing;
            // it is a different, less capable model, and saying so here is the
            // only honest moment. Someone installing tonight will not remember
            // this by the time they are asking her something in the dust.
            let warning = download.usingSmallModel
                ? "\n\nYour phone has \(String(format: "%.0f", Device.memoryGiB)) GB "
                  + "of memory, so you get her lighter brain. The full one "
                  + "overheats a phone this size. She will still answer from the "
                  + "camp's own records, but she will be blunter, and more likely "
                  + "to miss the point of an unclear question. Ask her plainly."
                : ""
            return "She answers from a language model that runs on this phone, with "
                 + "no signal and nothing sent anywhere. It is \(gb(bytes)) and it "
                 + "downloads once." + warning
        case .waitingForWiFi:
            return "Waiting for wi-fi. This is too big to pull over cellular, and "
                 + "it will start on its own once you're connected."
        case .downloading:
            return "Downloading. You can leave this screen and come back — it keeps "
                 + "going, and it picks up where it left off if it's interrupted."
        case .verifying:
            return "Checking it arrived intact."
        case .failed(let why):
            return why
        case .checking, .ready:
            return ""
        }
    }

    private struct Action { let label: String; let run: () -> Void }

    private var action: Action? {
        // No button at all on a phone that cannot run her. An offered button is
        // an invitation, and this one leads only to a wasted download.
        if Device.tooSmall { return nil }
        switch download.state {
        case .needed:
            return Action(label: Device.marginal ? "Download anyway"
                                                 : "Download her brain") {
                download.begin()
            }
        case .failed:
            return Action(label: "Try again") { download.retry() }
        case .waitingForWiFi, .downloading, .verifying, .checking, .ready:
            return nil
        }
    }

    /// Sizes people can hold in their head: 3.11 GB, not 3,106,738,272 bytes.
    /// Decimal GB, because that is what iOS reports for free space -- the same
    /// file is 2.89 GiB in binary units and quoting both reads as two files.
    private func gb(_ bytes: Int64) -> String {
        String(format: "%.2f GB", Double(bytes) / 1_000_000_000)
    }
}
