import SwiftUI

/// Press acknowledgement for the playful language: scale to
/// `Style.pressScale` while held, spring back with the language spring, and
/// tick the Taptic Engine on the way down. Industrial resolves pressScale to
/// 1.0, so applying this style there gives a plain-style-like press dim
/// instead of the squish — call sites don't branch.
struct SquishButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? Style.current.pressScale : 1)
            .opacity(configuration.isPressed && !Style.current.isPlayful ? 0.75 : 1)
            .animation(Style.current.spring, value: configuration.isPressed)
            .onChange(of: configuration.isPressed) { _, pressed in
                if pressed, Style.current.isPlayful { Haptics.press() }
            }
    }
}
