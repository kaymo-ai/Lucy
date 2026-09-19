import SwiftUI

// One way to record, on both tabs.
//
// ASK and NOTE had drifted into two different gestures for the same act: a
// round button you held on the chat, a square one you pressed twice on notes.
// Two models for one thing means the first thing you do on the tab you use
// less is the wrong thing.
//
// Hold, everywhere. The deciding argument is that a two-press toggle asks you
// to acquire a target twice, and the second acquisition is the hard one --
// you have been talking, your hand has moved, and the phone is somewhere in
// front of you in the dust. A hold asks for one acquisition and a release,
// and a release lands anywhere. It also cannot be left running in a pocket,
// which a toggle can.
//
// The cost is that holding is awkward for a long note, which is what LOCK is
// for: slide up while holding and it keeps recording without you.

/// The button. Big enough to find without looking -- the old one was sized for
/// a fingertip on a clean screen, and this is used through gloves.
struct HoldToRecord: View {
    let face: Face
    /// Drives the ring. 0…1, from the microphone.
    let level: CGFloat
    let recording: Bool
    let locked: Bool
    var onStart: () -> Void
    var onStop: () -> Void
    var onLock: () -> Void

    /// 96pt against the old 64. Comfortably past Apple's 44pt minimum, because
    /// the minimum assumes a bare fingertip and good light.
    static let size: CGFloat = 96
    private static let lockDistance: CGFloat = 64

    @State private var holding = false
    @State private var dragUp: CGFloat = 0
    /// True for the rest of the gesture that locked it. Without this, lifting
    /// the finger after sliding up reads as "released" and stops the recording
    /// you just asked to keep running.
    @State private var justLocked = false

    var body: some View {
        ZStack {
            // Expands with your voice, behind the button, so the thumb never
            // covers the only moving thing.
            Circle()
                .stroke(face.accent.opacity(0.35), lineWidth: 2)
                .frame(width: Self.size + 26 + level * 46,
                       height: Self.size + 26 + level * 46)
                .opacity(recording ? 1 : 0)

            // Always accent: this is the primary act on both tabs, and a grey
            // circle reads as disabled. Recording brightens it rather than
            // changing it -- the aura and the ring carry the state, because
            // they are the parts a thumb is not sitting on.
            Circle()
                .fill(face.accent.opacity(recording ? 1 : 0.9))
                .frame(width: Self.size, height: Self.size)
                .scaleEffect(holding ? 1.06 : 1)

            Image(systemName: locked ? "lock.fill" : "mic.fill")
                .font(.system(size: 32, weight: .bold))
                .foregroundStyle(face.night ? face.ground : .white)
        }
        // Pinned to the button's own size so the ring, which grows with your
        // voice, overflows instead of pushing the label under it down the
        // screen every time you speak.
        .frame(width: Self.size, height: Self.size)
        .animation(Style.current.spring, value: holding)
        .animation(.easeOut(duration: 0.12), value: level)
        .contentShape(Circle())
        .offset(y: locked ? 0 : -dragUp * 0.35)
        .gesture(
            DragGesture(minimumDistance: 0)
                .onChanged { value in
                    // While locked the button is a stop button, and does
                    // nothing until it is released.
                    guard !locked || justLocked else { return }
                    if !holding {
                        holding = true
                        // A quiet tick, not the cue.
                        //
                        // This says the button registered, which a control
                        // must do immediately or it feels broken. It does NOT
                        // say the microphone is open, because for a measured
                        // median of 0.284s after this line it is not --
                        // SpeechListener fires the unmistakable double thump
                        // when the tap actually opens, and that is the one
                        // people learn to speak after.
                        //
                        // The tone is still the caller's, and still immediate:
                        // the chat wants it covering the moment the microphone
                        // is opening, while a memo needs the beep to finish
                        // before the recorder starts or it is the first thing
                        // on the recording.
                        Haptics.press()
                        onStart()
                    }
                    dragUp = max(0, -value.translation.height)
                    if dragUp > Self.lockDistance && !justLocked {
                        justLocked = true
                        onLock()
                        dragUp = 0
                    }
                }
                .onEnded { _ in
                    dragUp = 0
                    // The gesture that locked it must not also end it.
                    if justLocked {
                        justLocked = false
                        holding = false
                        return
                    }
                    holding = false
                    Haptics.recordStop()
                    onStop()
                }
        )
        .accessibilityLabel(recording ? "Recording. Release to send." : "Hold to speak")
        .accessibilityHint("Slide up while holding to keep recording hands-free.")
    }
}

/// A tap-once round button, sized and shaped to match HoldToRecord so the two
/// read as alternatives rather than as a sequence. Used for the shutter, which
/// genuinely is a single press -- a photo has no duration to hold.
struct RoundAction: View {
    let face: Face
    let system: String
    let tint: Color
    var action: () -> Void

    @State private var pressed = false

    var body: some View {
        Circle()
            .fill(tint)
            .frame(width: HoldToRecord.size, height: HoldToRecord.size)
            .overlay(
                Image(systemName: system)
                    .font(.system(size: 32, weight: .bold))
                    .foregroundStyle(face.night ? face.ground : .white)
            )
            .scaleEffect(pressed ? 0.94 : 1)
            .animation(Style.current.spring, value: pressed)
            .contentShape(Circle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { _ in pressed = true }
                    .onEnded { _ in
                        pressed = false
                        action()
                    }
            )
    }
}

/// What you can see past your thumb.
///
/// A button that changes colour under the finger pressing it communicates
/// nothing -- the finger is on top of it. This puts the recording state at the
/// edges of the screen, where a hand cannot cover it, and moves it with the
/// microphone's own level so that it doubles as proof the tap is live. A
/// screen that answers your voice is the one thing that cannot be faked by a
/// microphone which never opened.
struct RecordingAura: View {
    let face: Face
    let level: CGFloat
    let active: Bool
    /// True for "I have your press" before the microphone has confirmed it
    /// can hear -- ChatView's quieter phase between touch-down and
    /// `speech.status == .listening`. Defaults false so NoteView, which has
    /// no such in-between state (its recorder is the camera, not speech
    /// recognition), keeps its one full-strength aura unchanged.
    var ready: Bool = false

    /// Runs whenever it is on. Silence should still look alive -- a screen that
    /// goes flat while you pause reads as "it stopped listening".
    @State private var breath: CGFloat = 0

    var body: some View {
        GeometryReader { geo in
            // Never fully at rest: the floor is the breathing pulse, and your
            // voice rides on top of it.
            let energy = min(1, level * 0.75 + breath * 0.25)
            // `ready` scales the whole aura down rather than hiding it: still
            // visibly alive the instant a finger lands, but plainly not yet
            // the "I can hear you" state -- a dimmer, thinner, tighter
            // version of the same shape, not a different one. Colour and
            // motion language stay identical in both styles on purpose; only
            // the amplitude changes, so soft and industrial each stay exactly
            // as deliberate as they already were.
            let scale: CGFloat = ready ? 0.4 : 1.0
            ZStack {
                // Bloom from the bottom, under the button, so the gesture and
                // the feedback are visibly the same event. Large on purpose --
                // this is the part seen from outside the thumb's shadow.
                RadialGradient(
                    colors: [face.accent.opacity((0.42 + energy * 0.38) * scale), .clear],
                    center: .init(x: 0.5, y: 1.04),
                    startRadius: 0,
                    endRadius: geo.size.width * (0.85 + energy * 0.55 * scale)
                )

                // The soft edge, doing the shouting.
                RoundedRectangle(cornerRadius: 46, style: .continuous)
                    .stroke(face.accent, lineWidth: (10 + energy * 30) * scale)
                    .blur(radius: (12 + energy * 16) * scale)
                    .opacity((0.55 + energy * 0.45) * scale)

                // A hard edge inside it, which is what survives full sun when
                // the blurred one washes out to nothing.
                RoundedRectangle(cornerRadius: 44, style: .continuous)
                    .stroke(face.accent.opacity(0.85 * scale), lineWidth: 3 + energy * 4 * scale)
                    .padding(3)
            }
            .ignoresSafeArea()
        }
        .allowsHitTesting(false)
        .opacity(active ? 1 : 0)
        .animation(.easeOut(duration: 0.1), value: level)
        .animation(.easeInOut(duration: 0.22), value: active)
        .animation(.easeInOut(duration: 0.22), value: ready)
        .onChange(of: active) { _, on in
            breath = 0
            guard on else { return }
            withAnimation(.easeInOut(duration: 0.85).repeatForever(autoreverses: true)) {
                breath = 1
            }
        }
    }
}
