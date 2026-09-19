import SwiftUI

// Initials in a coloured disc.
//
// The people list was three hundred rows of the same grey, and scrolling it
// felt like reading a spreadsheet. A camp is not a spreadsheet — you know
// these people — and the cheapest way to say so is to give every row something
// that is theirs and stays theirs.
//
// The colour comes from the name, so it is stable across launches, across
// devices and across rebuilds of the database, without storing anything.

struct Monogram: View {
    let face: Face
    let name: String
    var size: CGFloat = 38

    var body: some View {
        Circle()
            .fill(Self.tint(for: name, face: face))
            .frame(width: size, height: size)
            .overlay(
                Text(Self.initials(of: name))
                    .font(.eyebrow(size * 0.36))
                    .tracking(Style.current.eyebrowTracking * 0.5)
                    // Against a colour of fixed lightness, so one text colour
                    // works for every hue and nothing has to be measured.
                    .foregroundStyle(face.night ? Color.black.opacity(0.82)
                                               : Color.white.opacity(0.95))
            )
    }

    static func initials(of name: String) -> String {
        let words = name.split(separator: " ").filter { !$0.isEmpty }
        guard let first = words.first?.first else { return "?" }
        if words.count > 1, let last = words.last?.first {
            return "\(first)\(last)".uppercased()
        }
        return String(first).uppercased()
    }

    /// A hue from the name, at a saturation and brightness the palette can
    /// live with.
    ///
    /// Deliberately not the full wheel: a band from warm orange round to teal,
    /// which is the range the app already speaks in — ASK red at one end, KEEP
    /// teal at the other. Letting it run through pink and lime would give the
    /// list a colour scheme it does not otherwise have.
    static func tint(for name: String, face: Face) -> Color {
        var hash: UInt64 = 5381
        for byte in name.lowercased().utf8 {
            hash = (hash &* 33) &+ UInt64(byte)
        }
        // 0.02 (orange) through 0.55 (teal), the long way round the short arc.
        let hue = 0.02 + (Double(hash % 1000) / 1000.0) * 0.53
        return Color(hue: hue,
                     saturation: face.night ? 0.52 : 0.58,
                     brightness: face.night ? 0.78 : 0.62)
    }
}
