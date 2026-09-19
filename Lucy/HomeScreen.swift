import SwiftUI
import UIKit

// Ported from the two-mode prototype so the entity record has a home to be
// reached from. The two modes are shape-coded, not just colour-coded -- a
// circle and a square -- so they are distinguishable in dust, in gloves, at
// night, without reading the label.

// MARK: - Signature: burn-week dial
// Nobody on playa knows what day it is, and every shift is indexed to the burn
// week rather than the calendar. This answers "when am I" unasked.

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

// MARK: - The two modes, side by side

struct ModeBar: View {
    let face: Face
    /// SF Symbol for REMEMBER. Parameterised so candidates can be compared in
    /// the real button treatment rather than argued about in the abstract.
    var keepSymbol: String = "brain"
    var onAsk: () -> Void = {}
    var onRemember: () -> Void = {}

    /// A circle inscribed in a square always reads smaller than the square --
    /// equal bounding boxes are not equal visual weight. The correction insets
    /// the SQUARE rather than scaling up the circle: scaleEffect scales about
    /// the centre and pushed the circle past the screen margin, leaving the
    /// two buttons with visibly unequal gutters.
    /// A square reads larger than a circle sharing its bounding box, so the
    /// square is inset slightly. 7pt on a ~165pt button was 8.5% and overshot —
    /// the circle ended up visibly dominant. ~3% is the standard correction.
    private let squareOpticalInset: CGFloat = 2.5

    var body: some View {
        HStack(spacing: 16) {

            // Equal footprint, so shape is the only difference between them.
            // ASK — a circle. A question you put to the camp.
            Button(action: onAsk) {
                VStack(spacing: 10) {
                    ZStack {
                        Circle().fill(face.accent)
                        Image(systemName: "questionmark")
                            .font(.system(size: 46, weight: .bold))
                            .foregroundStyle(face.night ? Color(hex: 0x07080A) : .white)
                    }
                    .aspectRatio(1, contentMode: .fit)
                    .shadow(color: .black.opacity(Style.current.isPlayful ? 0.18 : 0),
                            radius: 0, y: 4)
                    Text(Style.current.label("Ask")).font(Style.current.tileLabel(15))
                        .tracking(Style.current.eyebrowTracking + Style.current.boost(0.5))
                        .foregroundStyle(face.text)
                }
            }
            .buttonStyle(SquishButtonStyle())
            .accessibilityLabel("Ask")
            .accessibilityHint("Ask the camp's knowledge a question by voice")

            // REMEMBER — a square. A thing you file.
            // Brain rather than camera: the camera is the mechanism, but what
            // the mode is FOR is putting something into the camp's memory.
            Button(action: onRemember) {
                VStack(spacing: 10) {
                    ZStack {
                        RoundedRectangle(cornerRadius: Style.current.cornerRadius, style: .continuous).fill(face.keep)
                        Image(systemName: keepSymbol)
                            .font(.system(size: 42, weight: .bold))
                            .foregroundStyle(face.keepText)
                    }
                    .aspectRatio(1, contentMode: .fit)
                    .padding(squareOpticalInset)
                    .shadow(color: .black.opacity(Style.current.isPlayful ? 0.18 : 0),
                            radius: 0, y: 4)
                    Text(Style.current.label("Remember")).font(Style.current.tileLabel(15))
                        .tracking(Style.current.eyebrowTracking + Style.current.boost(0.5))
                        .foregroundStyle(face.text)
                }
            }
            .buttonStyle(SquishButtonStyle())
            .accessibilityLabel("Remember")
            .accessibilityHint("Photograph something and add a voice note")
        }
        .padding(.horizontal, 22)
        .padding(.top, 12)
        .padding(.bottom, 12)
    }
}

// MARK: - Home

/// Side-by-side comparison of REMEMBER icon candidates in the real button
/// treatment, at the real size, on the real ground colour. Comparing symbols
/// in a picker lies about how they read at 42pt on teal.
struct IconCandidatesScreen: View {
    let face: Face
    static let candidates = ["brain", "brain.head.profile", "bookmark.fill", "tray.and.arrow.down.fill"]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Eyebrow(text: "REMEMBER ICON — CANDIDATES", face: face)
                .padding(.horizontal, 22).padding(.top, 14)

            ForEach(Self.candidates, id: \.self) { symbol in
                VStack(alignment: .leading, spacing: 2) {
                    // Label above the pair it names, or it reads as belonging
                    // to the row below it.
                    Text(symbol).font(.data(11)).foregroundStyle(face.muted)
                        .padding(.horizontal, 22)
                    ModeBar(face: face, keepSymbol: symbol)
                }
                .padding(.top, 12)
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(face.ground)
        .preferredColorScheme(face.night ? .dark : .light)
    }
}

struct HomeScreen: View {
    let face: Face
    /// With no captures there is genuinely nothing to list, so the message
    /// centres in the space instead of leaving a screen of void beneath it.
    private var emptyMinHeight: CGFloat { CaptureStore.all().isEmpty ? 380 : 0 }
    var keepSymbol: String = "brain"
    var onAsk: () -> Void = {}
    var onRemember: () -> Void = {}

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Rectangle().fill(face.hairline).frame(height: 1).padding(.top, 16)

            // Scrolls, so a long capture list is reachable and a short one does
            // not leave the modes marooned at the bottom of an empty screen.
            ScrollView {
                context.padding(.bottom, 20)
                    .frame(maxWidth: .infinity, minHeight: emptyMinHeight,
                           alignment: CaptureStore.all().isEmpty ? .center : .top)
            }
            .scrollIndicators(.hidden)
        }
        .background(face.ground)
        .preferredColorScheme(face.night ? .dark : .light)
        // safeAreaInset keeps the modes above the home indicator and clear of
        // the scrolling content, which a Spacer in a VStack does not.
        .safeAreaInset(edge: .bottom) {
            ModeBar(face: face, keepSymbol: keepSymbol, onAsk: onAsk, onRemember: onRemember)
                .background(face.ground)
        }
    }

    private var header: some View {
        HStack(spacing: 13) {
            BurnDial(face: face, index: 4).frame(width: 46, height: 46)
            VStack(alignment: .leading, spacing: 1) {
                Text(Style.current.label("Wednesday")).font(.eyebrow(14))
                    .tracking(Style.current.eyebrowTracking + Style.current.boost(0.2))
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
            // The "NEXT Bar shift · with Jill, Piotr · in 40 minutes" card that
            // used to sit here was invented — a fabricated time with real
            // campmates' names attached. It needs the roster, which is stale and
            // deliberately out of scope, so it is gone rather than lying.

            // Real captures off the disk. This list used to be four invented
            // rows with an SF Symbol where the photo should be, which looked
            // like a working feature and was not one.
            let captures = CaptureStore.all()
            HStack {
                Eyebrow(text: "YOU REMEMBERED", face: face)
                Spacer()
                if !captures.isEmpty {
                    Text("\(captures.count) saved").font(.data(11))
                        .foregroundStyle(Style.current.isPlayful ? face.moment : face.muted)
                }
            }
            .padding(.top, 22)

            if captures.isEmpty {
                // An honest empty state beats a fake list. It also tells a new
                // user what the mode is for without a tutorial.
                VStack(alignment: .leading, spacing: 6) {
                    Text(Style.current.isPlayful ? "Nothing saved yet" : "Nothing saved yet.")
                        .font(.display(28))
                        .foregroundStyle(face.text)
                    Text(Style.current.isPlayful
                         ? "Hold Remember, point at a thing, and say what it is — "
                           + "the camp will still know about it in a year."
                         : "REMEMBER photographs a thing and takes a voice note, "
                           + "so the camp still knows about it in a year.")
                        .font(.body_(15))
                        .foregroundStyle(face.muted)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.top, 10)
            } else {
                VStack(alignment: .leading, spacing: 13) {
                    ForEach(captures.prefix(4)) { capture in
                        saved(capture)
                    }
                }
                .padding(.top, 12)
            }
        }
        .padding(.horizontal, 22)
    }

    /// The thumbnail is the photo that was taken, and the title is what the
    /// voice note said. Both come off disk — nothing here is placeholder.
    private func saved(_ capture: Capture) -> some View {
        HStack(spacing: 12) {
            Group {
                if let data = try? Data(contentsOf: capture.photo),
                   let image = UIImage(data: data) {
                    Image(uiImage: image).resizable().scaledToFill()
                } else {
                    RoundedRectangle(cornerRadius: Style.current.smallCornerRadius,
                                     style: .continuous)
                        .fill(face.keep.opacity(face.night ? 0.22 : 0.14))
                        .overlay(Image(systemName: "photo")
                            .font(.system(size: 15, weight: .medium))
                            .foregroundStyle(face.keep))
                }
            }
            .frame(width: 44, height: 44)
            .clipShape(RoundedRectangle(cornerRadius: Style.current.smallCornerRadius,
                                        style: .continuous))

            VStack(alignment: .leading, spacing: 2) {
                Text(capture.text?.isEmpty == false ? capture.text! : "No voice note")
                    .font(.body_(16))
                    .foregroundStyle(capture.text?.isEmpty == false ? face.text : face.muted)
                    .lineLimit(1)
                Text(Self.when(capture.id)).font(.data(12)).foregroundStyle(face.muted)
            }
            Spacer()
        }
        .accessibilityElement(children: .combine)
    }

    /// Folder names are ISO timestamps with colons swapped for dashes.
    private static func when(_ id: String) -> String {
        let iso = id.replacingOccurrences(of: "-", with: ":")
            .replacingOccurrences(of: "20:26:", with: "2026-")
        let parser = ISO8601DateFormatter()
        guard let date = parser.date(from: iso) else { return id }
        let out = DateFormatter()
        out.dateFormat = Calendar.current.isDateInToday(date) ? "HH:mm" : "EEE HH:mm"
        return out.string(from: date)
    }
}
