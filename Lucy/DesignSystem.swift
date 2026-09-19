import SwiftUI

// Colour faces. The day/night decision was made separately from the design
// LANGUAGE (see Style.swift) and is not changed here:
//   - day ground stays cool alkali dust, deliberately NOT warm beige
//   - night stays amber on near-black to protect dark adaptation
// Only the accents soften, and only under Style.soft.

extension Color {
    init(hex: UInt32) {
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
    /// Secondary text: subtitles, captions, the body of a manual section.
    ///
    /// Raised on 2026-08-20 across every face. It was chosen for a screen
    /// being read indoors, and this one is read in direct desert sun through
    /// dust and sunglasses, where a dim grey is not quiet -- it is gone.
    /// `muted` should read as *secondary*, never as *faint*.
    let muted: Color
    let accent: Color      // ask
    let keep: Color        // remember
    let keepText: Color    // text/icon on a keep-filled surface
    let warm: Color        // gold: eyebrows, chips, small friendly moments
    let moment: Color      // violet: rare accents; twice on one screen is once too many
    let hairline: Color
    /// True when the face prefers the dark colour scheme — TRUE FOR BOTH the
    /// everyday dark face and the 3am night face. Shared icon/text treatments
    /// key off this. It does NOT identify the night face; use `dim` for that.
    let night: Bool
    /// True only for the red-shifted dark-adaptation night face. Playful
    /// recolouring must check this, not `night`, or it silently skips the
    /// everyday dark theme (that bug has now happened twice on this branch).
    let dim: Bool

    /// Alkali dust — cool grey-green. The playa bleaches everything to a
    /// dead tone; nothing out there is warm at noon.
    static var day: Face {
        let soft = Style.current.isPlayful
        return Face(
            ground:   Color(hex: 0xDFE2DB),
            raised:   Color(hex: 0xF2F4EE),
            text:     Color(hex: soft ? 0x1B1F24 : 0x111419),
            // 0x676C63 measured about 4.6:1 against this ground, which is
            // the floor for body text indoors and under it outside.
            muted:    Color(hex: 0x474C44),
            // Softer is a less shouting red -- still unmistakably the ASK
            // colour, with the fire turned down.
            accent:   Color(hex: soft ? 0xE84D1F : 0xDE320A),
            keep:     Color(hex: soft ? 0x1F93AC : 0x1B4D5A),
            // Day playful pairs the darker aqua with ink rather than white:
            // white on 0x1F93AC is ~3.6:1 at body sizes, marginal in noon glare.
            keepText: soft ? Color(hex: 0x06272E) : .white,
            warm:     Color(hex: soft ? 0xC17F1F : 0x676C63),
            moment:   Color(hex: soft ? 0x6248E0 : 0x1B4D5A),
            hairline: Color(hex: 0x111419).opacity(Style.current.hairlineOpacity),
            night: false,
            dim: false
        )
    }

    /// The default face. Charcoal rather than near-black, so surfaces can be
    /// distinguished from ground without a border, and the two mode colours
    /// survive intact -- ASK red and REMEMBER teal still read as themselves.
    ///
    /// This is NOT the same as `night`: night is a red-shifted dark-adaptation
    /// mode for 3am on playa, which trades colour fidelity for night vision.
    /// Dark is just the everyday theme.
    static var dark: Face {
        let soft = Style.current.isPlayful
        return Face(
            ground:   Color(hex: 0x16181C),
            raised:   Color(hex: 0x22262C),
            text:     Color(hex: 0xECEDE9),
            // The default face, and so the one actually held up at noon.
            // 0x8B9199 was a mid grey on near-black -- fine at a desk.
            muted:    Color(hex: 0xB4BAC2),
            accent:   Color(hex: soft ? 0xFF5326 : 0xF04A1E),
            keep:     Color(hex: soft ? 0x2BC7E3 : 0x3E8B9C),
            // Industrial dark must stay 0x07080A: the dark face has night:true,
            // so the pre-keepText idiom (face.night ? 0x07080A : .white)
            // rendered a near-black icon here — .white would be a regression.
            keepText: Color(hex: soft ? 0x07222A : 0x07080A),
            warm:     Color(hex: soft ? 0xFFB23E : 0x8B9199),
            moment:   Color(hex: soft ? 0x8B72FF : 0x3E8B9C),
            hairline: Color(hex: 0xECEDE9).opacity(Style.current.hairlineOpacity + 0.03),
            night: true,
            dim: false
        )
    }

    /// Amber and deep teal on near-black. Red-shifted light preserves dark
    /// adaptation and emits less; you can't charge for a week out there.
    static var night: Face {
        return Face(
            ground:   Color(hex: 0x07080A),
            raised:   Color(hex: 0x131519),
            text:     Color(hex: 0xEFE9DE),
            // Raised least. The night face exists to preserve dark
            // adaptation at 3am, and contrast is the thing it is
            // deliberately trading away -- but 0x8B7A61 was unreadable
            // rather than merely dim.
            muted:    Color(hex: 0xA08D71),
            // No soft variants at night, deliberately: the playful language
            // must not change what the night face emits. (Task 7 flip check —
            // the old dormant soft values were a visibly different amber.)
            accent:   Color(hex: 0xFF9A3C),
            keep:     Color(hex: 0x4FC4D6),
            keepText: Color(hex: 0x07080A),
            warm:     Color(hex: 0x8B7A61),
            // Moment stays muted at night on purpose: playful adds warmth
            // and rounding, not a new emissive element competing with the
            // amber the dark-adaptation design depends on.
            moment:   Color(hex: 0x8B7A61),
            hairline: Color(hex: 0xFF9A3C).opacity(Style.current.hairlineOpacity + 0.05),
            night: true,
            dim: true
        )
    }
}

extension Style: Equatable {}

struct Eyebrow: View {
    let text: String
    let face: Face
    var body: some View {
        Text(Style.current.isPlayful ? sentenceCased : text)
            .font(.eyebrow())
            .tracking(Style.current.eyebrowTracking)
            .foregroundStyle(Style.current.isPlayful ? face.warm : face.muted)
    }
    /// "YOU REMEMBERED" -> "You remembered". Good enough for the chrome's
    /// short all-caps labels; anything needing real casing passes it already.
    private var sentenceCased: String {
        let lower = text.lowercased()
        return lower.prefix(1).uppercased() + lower.dropFirst()
    }
}
