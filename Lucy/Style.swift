import SwiftUI

// The design LANGUAGE, separate from the colour faces.
//
// `Face` answers "day or night". `Style` answers "how hard-edged is this" --
// typeface, letter-spacing, corner radius, rule weight. Keeping them apart
// means the softer language can be tried without touching the day/night
// decision, which was made separately and is not in question here.
//
// Set once at launch. This is global mutable state, which is only acceptable
// because this target is a design harness: nothing switches language at
// runtime, and the winning preset gets hard-coded when one is chosen.

enum Style {
    case industrial   // the original: compressed black, tight rules, hard corners
    case soft

    static var current: Style = .soft

    /// Headlines. Compressed black reads as industrial signage; SF Rounded at
    /// heavy reads as something built for people rather than for a warehouse.
    /// Heavy, not bold, since the 2026-08-26 legibility pass -- the same
    /// weight the tiles always used, so the headings stopped being the
    /// lightest large thing on screen.
    func display(_ size: CGFloat) -> Font {
        switch self {
        case .industrial: return .system(size: size, weight: .black).width(.compressed)
        case .soft:       return .system(size: size, weight: .heavy, design: .rounded)
        }
    }

    func eyebrow(_ size: CGFloat) -> Font {
        switch self {
        case .industrial: return .system(size: size, weight: .heavy).width(.compressed)
        case .soft:       return .system(size: size, weight: .bold, design: .rounded)
        }
    }

    /// Wide letter-spacing is a large part of what makes the current language
    /// feel stencilled. Soft pulls it most of the way back.
    var eyebrowTracking: CGFloat {
        switch self {
        case .industrial: return 1.7
        case .soft:       return 0.6
        }
    }

    func body(_ size: CGFloat) -> Font {
        switch self {
        case .industrial: return .system(size: size, weight: .medium)
        case .soft:       return .system(size: size, weight: .medium, design: .rounded)
        }
    }

    /// Measured facts stay monospaced in both languages: it reads as recorded
    /// rather than generated, and that is a meaning, not a decoration.
    func data(_ size: CGFloat) -> Font {
        switch self {
        case .industrial: return .system(size: size, weight: .medium, design: .monospaced)
        case .soft:       return .system(size: size, weight: .medium, design: .monospaced)
        }
    }

    var cornerRadius: CGFloat {
        switch self {
        case .industrial: return 14
        case .soft:       return 30
        }
    }

    var smallCornerRadius: CGFloat {
        switch self {
        case .industrial: return 5
        case .soft:       return 12
        }
    }

    /// Hairlines at 0.15 read as ruled lines on a form. Softer means the
    /// separation is still there but stops dividing the page into cells.
    var hairlineOpacity: Double {
        switch self {
        case .industrial: return 0.15
        case .soft:       return 0.07
        }
    }

    /// The quote rule beside evidence.
    var quoteRuleWidth: CGFloat {
        switch self {
        case .industrial: return 3
        case .soft:       return 5
        }
    }

    var rowSpacing: CGFloat {
        switch self {
        case .industrial: return 13
        case .soft:       return 17
        }
    }

    /// True for the playful language. Copy and accent decisions key off this
    /// rather than comparing against the case directly, so a future rename
    /// of `.soft` touches one line.
    var isPlayful: Bool { self == .soft }

    /// The one spring. Industrial keeps today's tight feel; playful gets a
    /// small overshoot. New ad-hoc animation constants in views are a smell —
    /// see style-guide.md.
    var spring: Animation {
        switch self {
        case .industrial: return .spring(response: 0.25, dampingFraction: 0.8)
        case .soft:       return .spring(response: 0.35, dampingFraction: 0.65)
        }
    }

    /// Arrivals: a new answer or kept note entering the screen.
    var arrival: Animation {
        switch self {
        case .industrial: return .easeOut(duration: 0.25)
        case .soft:       return .spring(response: 0.4, dampingFraction: 0.7)
        }
    }

    /// Squish-on-press. 1.0 means no reaction (industrial).
    var pressScale: CGFloat {
        switch self {
        case .industrial: return 1.0
        case .soft:       return 0.96
        }
    }

    /// Labels are written in sentence case at the call site; industrial
    /// stencils them. "Ask" -> "ASK" under industrial, unchanged under soft.
    func label(_ text: String) -> String {
        switch self {
        case .industrial: return text.uppercased()
        case .soft:       return text
        }
    }

    /// Per-view tracking boosts (the `+ 0.5` sprinkled through views) only
    /// belong to the stencilled language. Soft neutralises them.
    func boost(_ value: CGFloat) -> CGFloat {
        switch self {
        case .industrial: return value
        case .soft:       return 0
        }
    }

    /// The big mode tiles' caption. Industrial matches today's eyebrow
    /// exactly; playful goes heavier than the eyebrow (spec: tiles at
    /// heavy/800-equivalent).
    func tileLabel(_ size: CGFloat) -> Font {
        switch self {
        case .industrial: return .system(size: size, weight: .heavy).width(.compressed)
        case .soft:       return .system(size: size, weight: .heavy, design: .rounded)
        }
    }
}

// Call sites stay unchanged; the language is resolved at use.
//
// `scale` is the one dial for the whole app's type. Every call site names
// the size it was designed at and this multiplies on the way through, so
// "make it all a bit bigger" (Marcus, 2026-08-26, for reading in the dust
// without glasses) is a number here and not two hundred edits. Weights rose
// with it -- regular text under desert sun at half brightness is the case
// that matters, not a lit desk.
extension Font {
    static let scale: CGFloat = 1.1
    static func display(_ size: CGFloat) -> Font { Style.current.display(size * scale) }
    static func eyebrow(_ size: CGFloat = 12) -> Font { Style.current.eyebrow(size * scale) }
    static func data(_ size: CGFloat = 15) -> Font { Style.current.data(size * scale) }
    static func body_(_ size: CGFloat = 17) -> Font { Style.current.body(size * scale) }
}
