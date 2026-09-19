import SwiftUI

// REMEMBER — capture a photo plus a voice memo now; analysis happens after the
// burn, back on signal.
//
// The mode's whole value is the strip: time, playa address and which shift you
// were on get attached without anyone typing them. Someone in dust and gloves
// will not fill in a form, so the only required act is press the shutter.

enum CaptureStage {
    case framing      // viewfinder, nothing captured yet
    case captured     // photo taken, voice note optional
    case saved        // filed, with everything that got attached
}

struct RememberScreen: View {
    let face: Face
    var stage: CaptureStage = .framing
    var onShutter: () -> Void = {}
    var onClose: (() -> Void)? = nil
    /// The standalone screenshot screens get a controller that is never
    /// started, so the viewfinder falls back to its placeholder.
    @ObservedObject var camera: CameraController = CameraController()

    /// Whether a finger is currently down on the mic. Tracked separately from
    /// `camera.recording` so the control reacts to the touch itself — if the
    /// recorder never starts, a pressed ring with no timer says exactly that,
    /// instead of the screen looking inert.
    @State private var holding = false

    /// Attached automatically. Never typed. The time is real; the playa address
    /// and shift are still placeholders — both need data the app does not have
    /// yet (a BRC coordinate fix, and the roster), so they are shown as the
    /// shape of the answer rather than invented values.
    private var attached: [String] {
        ["7:59 & C", Self.stamp.string(from: Date()), "during Bar shift"]
    }

    private static let stamp: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "EEE HH:mm"
        return f
    }()

    private var voiceLabel: String {
        if camera.recording { return "Recording — release to keep" }
        // Held, but the recorder has not opened. Naming this state is what
        // turns "nothing happens" into a reportable symptom.
        if holding { return "Holding — waiting for the microphone…" }
        return stage == .captured ? "Hold to add a voice note"
                                  : "Photo first — voice note after"
    }

    var body: some View {
        VStack(spacing: 0) {
            bar

            switch stage {
            case .framing, .captured: capture
            case .saved:              confirmation
            }
        }
        .background(face.ground)
        .preferredColorScheme(face.night ? .dark : .light)
    }

    // MARK: - Title bar

    private var bar: some View {
        HStack {
            Text(Style.current.label("Remember"))
                .font(.eyebrow(15))
                .tracking(Style.current.eyebrowTracking + Style.current.boost(0.5))
                .foregroundStyle(face.keep)
            Spacer()
            // Only drawn when there is something to close. A Close button that
            // does nothing is worse than no Close button, and that is exactly
            // what the standalone screens were shipping.
            if let onClose {
                Button("Close", action: onClose)
                    .font(.body_(16))
                    .foregroundStyle(face.muted)
            }
        }
        .padding(.horizontal, 22).padding(.top, 8).padding(.bottom, 14)
    }

    // MARK: - Capture

    private var capture: some View {
        VStack(spacing: 0) {
            viewfinder

            attachedStrip
                .padding(.horizontal, 22)
                .padding(.top, 18)

            Spacer(minLength: 0)

            // Shutter and voice sit together: a photo without a word about it
            // is a photo nobody can act on in six months.
            VStack(spacing: 14) {
                Button {
                    Haptics.shutter()
                    if stage == .framing { Sounds.shutter() }
                    onShutter()
                } label: {
                    ZStack {
                        RoundedRectangle(cornerRadius: Style.current.cornerRadius,
                                         style: .continuous)
                            .fill(face.keep)
                        Image(systemName: stage == .captured ? "checkmark" : "camera.fill")
                            .font(.system(size: 30, weight: .bold))
                            .foregroundStyle(face.ground)
                    }
                    .frame(width: 116, height: 96)
                }
                .buttonStyle(.plain)

                voiceControl
            }
            .padding(.bottom, 34)
        }
    }

    // MARK: - Voice note
    //
    // Three things have to be obvious without reading: that this is a control,
    // that it is listening, and that it heard you. The ring is driven by the
    // recorder's own meter, so it moves with your voice rather than animating
    // on a timer — an animation that runs regardless proves nothing.

    private var voiceControl: some View {
        VStack(spacing: 10) {
            ZStack {
                // Grows with what the microphone is actually hearing.
                Circle()
                    .fill(face.accent.opacity(0.22))
                    .frame(width: 78 + camera.level * 54, height: 78 + camera.level * 54)
                    .animation(.easeOut(duration: 0.06), value: camera.level)

                // Filled the instant a finger lands, before the recorder has
                // done anything.
                Circle()
                    .fill(holding ? face.accent.opacity(0.28) : Color.clear)
                    .frame(width: 78, height: 78)

                Circle()
                    .strokeBorder(holding || camera.recording
                                  ? face.accent : face.muted.opacity(0.55),
                                  lineWidth: holding || camera.recording ? 4 : 2)
                    .frame(width: 78, height: 78)

                if camera.recording {
                    Text(Self.duration(camera.elapsed))
                        .font(.data(17))
                        .foregroundStyle(face.accent)
                        .monospacedDigit()
                } else {
                    Image(systemName: "mic.fill")
                        .font(.system(size: 26, weight: .semibold))
                        .foregroundStyle(stage == .captured ? face.text : face.muted.opacity(0.55))
                }
            }
            .scaleEffect(holding || camera.recording ? 1.08 : 1.0)
            .animation(.spring(response: 0.26, dampingFraction: 0.7), value: holding)
            .animation(.spring(response: 0.26, dampingFraction: 0.7), value: camera.recording)
            .contentShape(Circle())
            // Hold to record, release to keep — the same gesture as ASK, and
            // the one that still works in gloves.
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { _ in
                        guard stage == .captured else { return }
                        if !holding { holding = true }
                        guard !camera.recording else { return }
                        camera.startRecording()
                    }
                    .onEnded { _ in
                        holding = false
                        guard camera.recording else { return }
                        camera.stopRecording()
                    }
            )
            .disabled(stage != .captured)

            Text(camera.problem ?? voiceLabel)
                .font(.body_(15))
                .foregroundStyle(camera.problem != nil ? face.accent
                                 : (camera.recording ? face.text : face.muted))
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 22)

            transcriptBlock
        }
    }

    /// What the memo said, read back. Shown while it is still being recognised
    /// so the wait is visible rather than silent — and shown at all because a
    /// recording nobody can skim is a recording nobody revisits.
    @ViewBuilder
    private var transcriptBlock: some View {
        if camera.transcribing || !camera.transcript.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 7) {
                    Eyebrow(text: camera.transcribing ? "TRANSCRIBING" : "YOU SAID",
                            face: face)
                    if camera.transcribing {
                        ProgressView().scaleEffect(0.6).tint(face.muted)
                    }
                }
                Text(camera.transcript.isEmpty ? "…" : camera.transcript)
                    .font(.body_(16))
                    .foregroundStyle(face.text)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 22)
            .padding(.top, 4)
        }
    }

    private static func duration(_ t: TimeInterval) -> String {
        String(format: "%d:%02d", Int(t) / 60, Int(t) % 60)
    }

    private var viewfinder: some View {
        ZStack {
            RoundedRectangle(cornerRadius: Style.current.cornerRadius, style: .continuous)
                .fill(Color(hex: 0x0D0F12))

            if stage == .captured, let shot = camera.lastPhoto {
                Image(uiImage: shot)
                    .resizable()
                    .scaledToFill()
            } else if stage == .framing, camera.ready {
                CameraPreview(session: camera.session)
            } else if camera.denied {
                // A dead black rectangle is indistinguishable from a camera
                // that is simply pointed at the dark.
                VStack(spacing: 10) {
                    Image(systemName: "camera.badge.ellipsis")
                        .font(.system(size: 36, weight: .light))
                        .foregroundStyle(face.keep.opacity(0.7))
                    Text("no camera access — Settings ▸ Lucy")
                        .font(.data(12))
                        .foregroundStyle(face.keep.opacity(0.55))
                        .multilineTextAlignment(.center)
                }
                .padding(.horizontal, 18)
            } else {
                VStack(spacing: 10) {
                    Image(systemName: stage == .captured ? "photo.fill" : "camera.viewfinder")
                        .font(.system(size: 40, weight: .light))
                        .foregroundStyle(face.keep.opacity(0.55))
                    Text(stage == .captured ? "captured" : "viewfinder").font(.data(12))
                        .foregroundStyle(face.keep.opacity(0.4))
                }
            }
        }
        .aspectRatio(3.0 / 4.0, contentMode: .fit)
        .clipShape(RoundedRectangle(cornerRadius: Style.current.cornerRadius,
                                    style: .continuous))
        .padding(.horizontal, 22)
    }

    /// The context that makes a photo findable later, gathered for free.
    private var attachedStrip: some View {
        VStack(alignment: .leading, spacing: 8) {
            Eyebrow(text: "ATTACHED", face: face)
            FlowRow(spacing: 7) {
                ForEach(attached, id: \.self) { chip($0) }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func chip(_ t: String) -> some View {
        Text(t)
            .font(.data(12))
            .foregroundStyle(face.text)
            .padding(.horizontal, 10).padding(.vertical, 6)
            .background(
                RoundedRectangle(cornerRadius: Style.current.smallCornerRadius,
                                 style: .continuous)
                    .fill(face.keep.opacity(face.night ? 0.22 : 0.14))
            )
    }

    // MARK: - Saved

    /// What happens next is stated plainly. On playa there is no signal, so the
    /// honest promise is "filed now, understood later" -- not a spinner
    /// pretending something is being processed.
    private var confirmation: some View {
        VStack(alignment: .leading, spacing: 0) {
            Spacer(minLength: 0)

            Image(systemName: "checkmark")
                .font(.system(size: 34, weight: .bold))
                .foregroundStyle(face.keep)

            Text("Filed.")
                .font(.display(52))
                .foregroundStyle(face.text)
                .padding(.top, 6)

            Text("Photo and voice note saved to the phone. Lucy will read it "
                 + "properly after the burn, when there's signal again.")
                .font(.body_(16))
                .foregroundStyle(face.muted)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 8)

            if !camera.transcript.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Eyebrow(text: "YOU SAID", face: face)
                    Text(camera.transcript)
                        .font(.body_(16))
                        .foregroundStyle(face.text)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.top, 22)
            }

            attachedStrip.padding(.top, 26)

            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 22)
    }
}

/// Chips wrap instead of overflowing — playa addresses and shift names are not
/// a fixed width, and a clipped chip loses the context that is the whole point.
struct FlowRow: Layout {
    var spacing: CGFloat = 7

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let maxWidth = proposal.width ?? .infinity
        var x: CGFloat = 0, y: CGFloat = 0, rowHeight: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x + size.width > maxWidth, x > 0 {
                x = 0; y += rowHeight + spacing; rowHeight = 0
            }
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
        return CGSize(width: maxWidth, height: y + rowHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize,
                       subviews: Subviews, cache: inout ()) {
        var x = bounds.minX, y = bounds.minY, rowHeight: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x + size.width > bounds.maxX, x > bounds.minX {
                x = bounds.minX; y += rowHeight + spacing; rowHeight = 0
            }
            view.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
    }
}
