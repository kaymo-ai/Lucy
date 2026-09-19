import SwiftUI

// ASK, voice first.
//
// Typing is the worst possible input on playa: gloves, dust, glare, one hand
// holding something else. The prototype already called ASK "a circle you speak
// into" -- this makes that literal. Hold the circle, say the thing, let go.
//
// The keyboard stays available, because voice fails in wind and near a sound
// camp, and a mode with no fallback is a mode that strands people.

enum ListenState: Equatable {
    case idle
    case listening
    case heard(String)      // transcript captured, resolving
    case answered(Entity)
    case missed(String)
}

struct AskVoiceView: View {
    let face: Face
    let store: EntityStore
    var state: ListenState = .idle
    var onClose: (() -> Void)? = nil

    @StateObject private var speech = SpeechListener()
    @State private var live: ListenState = .idle
    @State private var pulse = false
    @State private var typing = false
    @State private var typed = ""

    private var current: ListenState {
        if case .idle = live { return state }
        return live
    }

    var body: some View {
        VStack(spacing: 0) {
            bar

            switch current {
            case .answered(let entity):
                EntityRecordView(face: face,
                                 entity: entity,
                                 factsByCategory: store.facts(entityID: entity.id))
            case .missed(let asked):
                noAnswer(asked)
                Spacer(minLength: 0)
                listener
            default:
                heardLine
                Spacer(minLength: 0)
                listener
            }
        }
        .background(face.ground)
        .preferredColorScheme(face.night ? .dark : .light)
        // Ask for microphone and speech access when the screen opens, not when
        // the mic is first held. The trigger is press-and-hold: requesting
        // inside `start()` puts the system dialog up mid-gesture, so releasing
        // ends the hold against an empty transcript and the first question is
        // always swallowed. On a fresh install that reads as a dead button.
        .task {
            guard !PreviewRoot.skipsPermissionPrompts else { return }
            await speech.prewarm()
        }
    }

    // MARK: - Chrome

    private var bar: some View {
        HStack {
            Text(Style.current.label("Ask"))
                .font(.eyebrow(15))
                .tracking(Style.current.eyebrowTracking + Style.current.boost(0.5))
                .foregroundStyle(face.accent)
            Spacer()
            if let onClose {
                Button("Close", action: onClose)
                    .font(.body_(16)).foregroundStyle(face.muted)
            }
        }
        .padding(.horizontal, 22).padding(.top, 8).padding(.bottom, 10)
    }

    /// What Lucy currently thinks you said, big enough to read at arm's length
    /// so a misheard question is caught before it becomes a wrong answer.
    @ViewBuilder
    private var heardLine: some View {
        switch current {
        case .listening:
            Text(speech.transcript.isEmpty ? "listening…" : speech.transcript)
                .font(.display(34))
                .foregroundStyle(speech.transcript.isEmpty ? face.muted : face.text)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 22).padding(.top, 14)
        case .heard(let t):
            Text(t)
                .font(.display(34))
                .foregroundStyle(face.text)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 22).padding(.top, 14)
        default:
            VStack(alignment: .leading, spacing: 10) {
                Text("Hold the circle and ask.")
                    .font(.display(34))
                    .foregroundStyle(face.text)
                    .fixedSize(horizontal: false, vertical: true)

                // Said plainly and early, because the fix needs signal and
                // playa has none -- discovering it out there is discovering it
                // too late.
                switch speech.status {
                case .denied:
                    problem("Lucy needs the microphone and speech access. "
                            + "Settings ▸ Lucy PT.")
                case .unavailable(let why):
                    problem(why)
                case .failed(let why):
                    problem(why)
                default:
                    EmptyView()
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 22).padding(.top, 14)
        }
    }

    private func problem(_ text: String) -> some View {
        Text(text)
            .font(.body_(15))
            .foregroundStyle(face.accent)
            .fixedSize(horizontal: false, vertical: true)
    }

    // The "OR TAP" chips were removed. They could only ever show the handful of
    // entities that fit on screen — the corpus knows about 129 — so they read as
    // a menu while quietly hiding most of it. Voice and "Type instead" reach
    // everything, which leaves one instruction and one target.

    // MARK: - The listener

    private var listening: Bool {
        if case .listening = current { return true }
        return false
    }

    private var listener: some View {
        VStack(spacing: 12) {
            ZStack {
                // Rings only while listening: motion is the feedback that the
                // phone is actually hearing you, which matters when you cannot
                // see a level meter through dust.
                if listening {
                    ForEach(0..<3, id: \.self) { i in
                        Circle()
                            .stroke(face.accent.opacity(0.30 - Double(i) * 0.09), lineWidth: 2)
                            .frame(width: 132 + CGFloat(i) * 34)
                            .scaleEffect(pulse ? 1.10 : 0.94)
                            .animation(
                                .easeInOut(duration: 1.1)
                                .repeatForever(autoreverses: true)
                                .delay(Double(i) * 0.16),
                                value: pulse
                            )
                    }
                }

                Circle()
                    .fill(face.accent)
                    .frame(width: 132, height: 132)
                    .overlay(
                        Image(systemName: listening ? "waveform" : "questionmark")
                            .font(.system(size: listening ? 44 : 50, weight: .bold))
                            .foregroundStyle(face.night ? face.ground : .white)
                    )
                    .scaleEffect(listening ? 1.06 : 1.0)
                    .animation(Style.current.spring, value: listening)
            }
            .frame(height: 190)
            .contentShape(Circle())
            .gesture(
                // Press and hold, release to send. No target to hit twice, and
                // letting go is the one gesture that always works in gloves.
                DragGesture(minimumDistance: 0)
                    .onChanged { _ in
                        guard !listening else { return }
                        live = .listening
                        pulse = true
                        Task { await speech.start() }
                    }
                    .onEnded { _ in
                        pulse = false
                        // Waits for the final transcription, not the last
                        // partial -- see SpeechListener.finish().
                        Task {
                            let heard = SpeechOverrides.apply(to: await speech.finish())
                            // An empty transcript is reported, not silently
                            // treated as a question nobody asked.
                            if heard.isEmpty {
                                live = .idle
                            } else {
                                live = .heard(heard)
                                resolve(heard)
                            }
                            speech.reset()
                        }
                    }
            )

            Button {
                typing = true
            } label: {
                Label("Type instead", systemImage: "keyboard")
                    .font(.body_(15))
                    .foregroundStyle(face.muted)
            }
            .buttonStyle(.plain)
        }
        .padding(.bottom, 26)
    }

    private func resolve(_ q: String) {
        let stripped = q.replacingOccurrences(
            of: #"(?i)\b(what|who|where|is|are|the|a|an|about|tell|me)\b"#,
            with: " ", options: .regularExpression
        ).trimmingCharacters(in: .whitespacesAndNewlines)

        if let hit = store.findEntity(named: stripped) ?? store.findEntity(named: q) {
            live = .answered(hit)
        } else {
            live = .missed(q)
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
                 + "Worth asking in the PS chat — or use REMEMBER to capture "
                 + "the answer when you find it.")
                .font(.body_(16))
                .foregroundStyle(face.muted)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 22).padding(.top, 20)
    }
}
