import SwiftUI

// =====================================================================
// Lucy PT — design prototype, v2
//
// Two modes: ASK and REMEMBER.
// They are shape-coded, not just colour-coded — a circle and a square —
// so you can tell them apart in glare, through goggles, without reading
// the label. Ask is a question you speak. Remember is a thing you file.
// =====================================================================

extension Color {
    init(_ hex: UInt32) {
        self.init(.sRGB,
                  red: Double((hex >> 16) & 0xFF) / 255,
                  green: Double((hex >> 8) & 0xFF) / 255,
                  blue: Double(hex & 0xFF) / 255,
                  opacity: 1)
    }
}

struct Face {
    let ground: Color
    let raised: Color
    let text: Color
    let muted: Color
    let accent: Color      // ask
    let keep: Color        // remember
    let hairline: Color
    let night: Bool

    /// Alkali dust — cool grey-green. The playa bleaches everything to a
    /// dead tone; nothing out there is warm at noon.
    static let day = Face(
        ground:   Color(0xDFE2DB),
        raised:   Color(0xF2F4EE),
        text:     Color(0x111419),
        muted:    Color(0x676C63),
        accent:   Color(0xDE320A),
        keep:     Color(0x1B4D5A),
        hairline: Color(0x111419).opacity(0.15),
        night: false
    )

    /// Amber and deep teal on near-black. Red-shifted light preserves dark
    /// adaptation and emits less; you can't charge for a week out there.
    static let night = Face(
        ground:   Color(0x07080A),
        raised:   Color(0x131519),
        text:     Color(0xEFE9DE),
        muted:    Color(0x8B7A61),
        accent:   Color(0xFF9A3C),
        keep:     Color(0x4FC4D6),
        hairline: Color(0xFF9A3C).opacity(0.20),
        night: true
    )
}

extension Font {
    /// Compressed black — industrial signage, not editorial serif.
    static func display(_ size: CGFloat) -> Font {
        .system(size: size, weight: .black).width(.compressed)
    }
    static func eyebrow(_ size: CGFloat = 12) -> Font {
        .system(size: size, weight: .heavy).width(.compressed)
    }
    /// Measured facts get monospace: it reads as recorded, not generated.
    static func data(_ size: CGFloat = 15) -> Font {
        .system(size: size, weight: .medium, design: .monospaced)
    }
    static func body_(_ size: CGFloat = 17) -> Font {
        .system(size: size, weight: .medium)
    }
}

// MARK: - Signature: burn-week dial
// Nobody on playa knows what day it is, and every shift is indexed to the
// burn week rather than the calendar. This answers "when am I" unasked.

struct BurnDial: View {
    let face: Face
    let index: Int
    private let days = 9

    var body: some View {
        Canvas { ctx, size in
            let radius = min(size.width, size.height) / 2 - 5
            let centre = CGPoint(x: size.width / 2, y: size.height / 2)
            let step = 360.0 / Double(days)

            for i in 0..<days {
                let start = Angle(degrees: -90 + Double(i) * step + 2.5)
                let end   = Angle(degrees: -90 + Double(i + 1) * step - 2.5)
                var path = Path()
                path.addArc(center: centre, radius: radius,
                            startAngle: start, endAngle: end, clockwise: false)
                // Days behind you read solid; days ahead are barely there.
                // The gap between them is what makes the dial glanceable.
                let colour: Color = i < index  ? face.text.opacity(0.75)
                                  : i == index ? face.accent
                                  : face.muted.opacity(0.16)
                ctx.stroke(path, with: .color(colour),
                           style: StrokeStyle(lineWidth: i == index ? 8 : 4, lineCap: .butt))
            }
        }
    }
}

struct Eyebrow: View {
    let text: String
    let face: Face
    var body: some View {
        Text(text).font(.eyebrow()).tracking(1.7).foregroundStyle(face.muted)
    }
}

// MARK: - The two modes, side by side

struct ModeBar: View {
    let face: Face
    /// Mechanism icons (waveform / camera) vs intent icons (question / brain).
    var intentIcons = true

    private var askSymbol: String { intentIcons ? "questionmark" : "waveform" }
    private var keepSymbol: String { "camera.fill" }

    var body: some View {
        HStack(spacing: 16) {

            // Equal footprint, so shape is the only difference between them.
            // ASK — a circle. You speak into it.
            VStack(spacing: 10) {
                ZStack {
                    Circle().fill(face.accent)
                    Image(systemName: askSymbol)
                        .font(.system(size: intentIcons ? 46 : 34, weight: .bold))
                        .foregroundStyle(face.night ? Color(0x07080A) : .white)
                }
                .aspectRatio(1, contentMode: .fit)
                Text("ASK").font(.eyebrow(15)).tracking(2.2)
                    .foregroundStyle(face.text)
            }

            // REMEMBER — a square. A thing you file.
            VStack(spacing: 10) {
                ZStack {
                    RoundedRectangle(cornerRadius: 14).fill(face.keep)
                    Image(systemName: keepSymbol)
                        .font(.system(size: 38, weight: .bold))
                        .foregroundStyle(face.night ? Color(0x07080A) : .white)
                }
                .aspectRatio(1, contentMode: .fit)
                Text("REMEMBER").font(.eyebrow(15)).tracking(2.2)
                    .foregroundStyle(face.text)
            }
        }
        .padding(.horizontal, 22)
        .padding(.bottom, 30)
    }
}


/// Real retrieved content and real similarity scores from the rebuilt corpus.
struct Ask {
    let question: String
    let headline: String?
    let subhead: String
    let quote: String
    let attribution: String
    let aside: String?

    /// 0.843 — an unambiguous hit. The quote carries the whole answer.
    static let strong = Ask(
        question: "what socket for the lag bolts",
        headline: "9/16",
        subhead: "socket, for the lag bolts",
        quote: "\u{201C}You will need a 9/16 socket as well for the lag bolts. I am bringing some extra sets of both.\u{201D}",
        attribution: "Nick Hadley · PS BUILD 22 · 24 Aug 2022",
        aside: "He also said don\u{2019}t lose the ones already in Doris."
    )

    /// 0.54 — nothing relevant came back. Saying so is the honest answer,
    /// and showing the near miss lets the camper judge it themselves.
    static let none_ = Ask(
        question: "how do we deal with grey water",
        headline: nil,
        subhead: "I don\u{2019}t have a good answer.",
        quote: "\u{201C}did we ever consult about how easy it would be to drill a well? is it just impossible?\u{201D}",
        attribution: "Nicky · PS · closest match, weak",
        aside: "Nothing in the camp docs covers grey water. Worth asking in the PS chat \u{2014} this is a gap Lucy should learn."
    )
}

// MARK: - Home

struct HomeScreen: View {
    let face: Face
    let answering: Bool
    var intentIcons = true
    var ask: Ask = .strong

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Rectangle().fill(face.hairline).frame(height: 1).padding(.top, 16)
            if answering { answer } else { context }
            Spacer(minLength: 0)
            ModeBar(face: face, intentIcons: intentIcons)
        }
        .background(face.ground)
        .preferredColorScheme(face.night ? .dark : .light)
    }

    private var header: some View {
        HStack(spacing: 13) {
            BurnDial(face: face, index: 4).frame(width: 46, height: 46)
            VStack(alignment: .leading, spacing: 1) {
                Text("WEDNESDAY").font(.eyebrow(14)).tracking(1.9)
                    .foregroundStyle(face.text)
                Text("burn week · day 5 of 9").font(.data(12))
                    .foregroundStyle(face.muted)
            }
            Spacer()
            Image(systemName: "line.3.horizontal")
                .font(.system(size: 19, weight: .semibold))
                .foregroundStyle(face.muted)
        }
        .padding(.horizontal, 22)
        .padding(.top, 6)
    }

    private var context: some View {
        VStack(alignment: .leading, spacing: 0) {
            Eyebrow(text: "NEXT", face: face).padding(.top, 22)

            Text("Bar shift").font(.display(50))
                .foregroundStyle(face.text).padding(.top, 3)

            Text("14:00–18:00 · with Jill, Piotr").font(.data(15))
                .foregroundStyle(face.muted).padding(.top, 5)

            HStack(spacing: 7) {
                Circle().fill(face.accent).frame(width: 7, height: 7)
                Text("in 40 minutes").font(.body_(15)).foregroundStyle(face.text)
            }
            .padding(.top, 12)

            Eyebrow(text: "YOU REMEMBERED", face: face).padding(.top, 30)

            VStack(alignment: .leading, spacing: 11) {
                saved("Grey water valve", "7:59 & C · 09:12")
                saved("Shade panel count", "yesterday · 16:40")
                saved("Generator hours", "Mon · 21:05")
            }
            .padding(.top, 11)
        }
        .padding(.horizontal, 22)
    }

    private func saved(_ title: String, _ meta: String) -> some View {
        HStack(spacing: 12) {
            RoundedRectangle(cornerRadius: 5)
                .fill(face.keep.opacity(face.night ? 0.22 : 0.14))
                .frame(width: 40, height: 40)
                .overlay(
                    Image(systemName: "photo")
                        .font(.system(size: 15, weight: .medium))
                        .foregroundStyle(face.keep)
                )
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.body_(16)).foregroundStyle(face.text)
                Text(meta).font(.data(12)).foregroundStyle(face.muted)
            }
            Spacer()
        }
    }

    // Lucy quotes; she does not paraphrase. The strongest material in the
    // corpus is what people actually said, with a name and a date on it —
    // so the quote is the answer, and the attribution is the proof.
    private var answer: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(ask.question)
                .font(.body_(16)).foregroundStyle(face.muted).padding(.top, 22)

            if let headline = ask.headline {
                Text(headline).font(.display(74))
                    .foregroundStyle(face.text).padding(.top, 1)
                Text(ask.subhead)
                    .font(.body_(17)).foregroundStyle(face.text)
            } else {
                Text(ask.subhead).font(.display(38))
                    .foregroundStyle(face.text).padding(.top, 8)
            }

            // The quote block. A rule down the left, not a bubble: this is
            // testimony being cited, not Lucy speaking.
            VStack(alignment: .leading, spacing: 8) {
                Text(ask.quote)
                    .font(.body_(17)).foregroundStyle(face.text)
                    .fixedSize(horizontal: false, vertical: true)
                Text(ask.attribution)
                    .font(.data(12)).foregroundStyle(face.muted)
            }
            .padding(.leading, 15)
            .overlay(alignment: .leading) {
                Rectangle().fill(face.accent).frame(width: 3)
            }
            .padding(.top, 20)

            if let aside = ask.aside {
                Text(aside)
                    .font(.body_(15)).foregroundStyle(face.muted)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 18)
            }
        }
        .padding(.horizontal, 22)
    }

    private func chip(_ t: String) -> some View {
        Text(t).font(.data(12)).foregroundStyle(face.muted)
            .padding(.horizontal, 9).padding(.vertical, 5)
            .overlay(RoundedRectangle(cornerRadius: 4).stroke(face.hairline, lineWidth: 1))
    }
}

// MARK: - Remember mode
// Full screen, because the mode should be unmistakable. What makes this
// worth building is the strip: time, playa address and which shift you
// were on get attached without anyone typing them.

struct RememberScreen: View {
    let face: Face

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text("REMEMBER").font(.eyebrow(15)).tracking(2.2)
                    .foregroundStyle(face.keep)
                Spacer()
                Text("Close").font(.body_(16)).foregroundStyle(face.muted)
            }
            .padding(.horizontal, 22).padding(.top, 8).padding(.bottom, 14)

            // Viewfinder stand-in — the simulator has no camera.
            ZStack {
                Rectangle().fill(Color(0x1A1D21))
                VStack(spacing: 10) {
                    Image(systemName: "camera.viewfinder")
                        .font(.system(size: 40, weight: .light))
                        .foregroundStyle(face.keep.opacity(0.55))
                    Text("viewfinder").font(.data(12))
                        .foregroundStyle(face.keep.opacity(0.4))
                }
            }
            .aspectRatio(3.0/4.0, contentMode: .fit)
            .clipShape(RoundedRectangle(cornerRadius: 14))
            .padding(.horizontal, 22)

            // Context attached automatically. This is the actual value.
            VStack(alignment: .leading, spacing: 7) {
                Eyebrow(text: "ATTACHED", face: face)
                Text("7:59 & C · Wed 14:22 · during Bar shift")
                    .font(.data(13)).foregroundStyle(face.text)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 22).padding(.top, 18)

            Spacer(minLength: 0)

            VStack(spacing: 12) {
                ZStack {
                    RoundedRectangle(cornerRadius: 14).fill(face.keep)
                    Image(systemName: "camera.fill")
                        .font(.system(size: 30, weight: .bold))
                        .foregroundStyle(face.night ? Color(0x07080A) : .white)
                }
                .frame(width: 116, height: 96)

                Text("Hold to add a voice note")
                    .font(.body_(15)).foregroundStyle(face.muted)
            }
            .padding(.bottom, 34)
        }
        .background(face.ground)
        .preferredColorScheme(face.night ? .dark : .light)
    }
}

// MARK: - Cycles states so every screen can be captured

struct ContentView: View {
    @State private var step = 0

    var body: some View {
        Group {
            switch step {
            case 0: HomeScreen(face: .day, answering: false, intentIcons: true)
            case 1: HomeScreen(face: .day, answering: true, ask: .strong)
            case 2: HomeScreen(face: .day, answering: true, ask: .none_)
            case 3: HomeScreen(face: .night, answering: true, ask: .strong)
            default: RememberScreen(face: .day)
            }
        }
        .onAppear {
            Timer.scheduledTimer(withTimeInterval: 4, repeats: true) { _ in
                step = (step + 1) % 5
            }
        }
    }
}
