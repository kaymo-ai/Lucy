import SwiftUI
import Speech

/// Reports what speech can actually do here, rather than assuming. Rendered via
/// `--screen speechcheck` so the offline claim can be checked with a screenshot
/// instead of asserted.
struct SpeechDiagnostics: View {
    let face: Face

    private var recognizer: SFSpeechRecognizer? {
        SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("SPEECH").font(.display(40)).foregroundStyle(face.text)

            row("recogniser exists", recognizer != nil)
            row("isAvailable", recognizer?.isAvailable == true)
            row("supportsOnDeviceRecognition", recognizer?.supportsOnDeviceRecognition == true)
            row("locales include en-US",
                SFSpeechRecognizer.supportedLocales().contains(Locale(identifier: "en-US")))

            Text("authorization: \(String(describing: SFSpeechRecognizer.authorizationStatus()))")
                .font(.data(12)).foregroundStyle(face.muted)

            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .padding(22)
        .background(face.ground)
        .preferredColorScheme(face.night ? .dark : .light)
    }

    private func row(_ label: String, _ ok: Bool) -> some View {
        HStack(spacing: 10) {
            Image(systemName: ok ? "checkmark.circle.fill" : "xmark.circle.fill")
                .foregroundStyle(ok ? face.keep : face.accent)
            Text(label).font(.data(13)).foregroundStyle(face.text)
        }
    }
}
