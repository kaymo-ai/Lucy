import AVFoundation
import Speech
import SwiftUI
import UIKit

// REMEMBER's hardware and storage layer.
//
// Until now REMEMBER walked framing -> captured -> saved without touching a
// camera or writing a file: pressing the shutter advanced an enum. This makes
// it real, and keeps the same three stages so the screen is unchanged.
//
// Everything is written to Documents/captures/<ISO timestamp>/ as plain files:
//
//   photo.jpg       the still
//   memo.m4a        the voice note, if one was recorded
//   transcript.txt  what the voice note said, transcribed on the phone
//   meta.json       when it was taken, and what was attached without typing
//
// Plain files on purpose. A capture taken on playa has to survive the app
// crashing, the phone dying, and six months before anyone looks at it — and it
// has to be readable by the post-burn pipeline without this app's cooperation.

/// One capture on disk.
struct Capture: Identifiable, Hashable {
    let id: String            // the folder name: an ISO-8601 timestamp
    let folder: URL
    var photo: URL { folder.appendingPathComponent("photo.jpg") }
    var memo: URL { folder.appendingPathComponent("memo.m4a") }
    var transcript: URL { folder.appendingPathComponent("transcript.txt") }
    var hasMemo: Bool { FileManager.default.fileExists(atPath: memo.path) }
    var text: String? { try? String(contentsOf: transcript, encoding: .utf8) }
}

/// Where captures live, and how they are written.
enum CaptureStore {
    static var root: URL {
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        let dir = docs.appendingPathComponent("captures", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    /// A folder named for the moment the shutter was pressed. Sorting the
    /// folder names sorts the captures, with no index to keep in step.
    static func newFolder(at date: Date) -> URL {
        let stamp = ISO8601DateFormatter().string(from: date)
            .replacingOccurrences(of: ":", with: "-")
        let dir = root.appendingPathComponent(stamp, isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    static func writeMeta(_ folder: URL, taken: Date, attached: [String],
                          transcript: String = "", noteID: UUID = UUID()) {
        let meta: [String: Any] = [
            // The note's identity on the camp server, decided here rather than
            // there, so an upload that dies halfway can be retried without
            // creating a second note. Written even when there is no backend to
            // send it to: a capture from before sync existed has to have one
            // derived for it, and derived ids are the fallback, not the plan.
            "note_id": noteID.uuidString,
            "taken_at": ISO8601DateFormatter().string(from: taken),
            "attached": attached,
            "transcript": transcript,
            "app_version": Bundle.main.infoDictionary?["CFBundleShortVersionString"] ?? "dev",
        ]
        guard let data = try? JSONSerialization.data(withJSONObject: meta,
                                                     options: [.prettyPrinted]) else { return }
        try? data.write(to: folder.appendingPathComponent("meta.json"))
    }

    /// Newest first — what a review screen wants.
    static func all() -> [Capture] {
        let dirs = (try? FileManager.default.contentsOfDirectory(
            at: root, includingPropertiesForKeys: nil)) ?? []
        return dirs
            .filter { $0.hasDirectoryPath }
            .sorted { $0.lastPathComponent > $1.lastPathComponent }
            .map { Capture(id: $0.lastPathComponent, folder: $0) }
    }
}

/// Audible confirmation, to go with the taps. Sound carries when the phone is
/// in a pocket or a glove is on the screen, and a shutter that clicks is the
/// most universally understood "it fired" in any interface.
///
/// These are the system sounds iOS already uses for exactly these acts, so they
/// mean the same thing here as everywhere else on the phone.
enum Sounds {
    private static let shutterID: SystemSoundID = 1108   // photo shutter
    private static let beginID: SystemSoundID = 1113     // begin recording
    private static let endID: SystemSoundID = 1114       // end recording
    private static let filedID: SystemSoundID = 1394     // confirmation chime

    static func shutter() { AudioServicesPlaySystemSound(shutterID) }
    static func end() { AudioServicesPlaySystemSound(endID) }
    static func filed() { AudioServicesPlaySystemSound(filedID) }

    /// "Speak now." Unlike `begin(then:)` this does not wait for the tone to
    /// finish, because the microphone is already open by the time it plays --
    /// waiting would reintroduce the deaf window it exists to cover. The tone
    /// lands in the silence while the speaker is still reacting to it.
    static func ready() { AudioServicesPlaySystemSound(beginID) }

    /// The begin tone has to finish before the recorder opens the microphone,
    /// or the first thing every memo contains is the beep that started it.
    static func begin(then start: @escaping () -> Void) {
        AudioServicesPlaySystemSoundWithCompletion(beginID) {
            DispatchQueue.main.async(execute: start)
        }
    }
}

/// Taps, not decoration. On playa the phone is often the only thing you can
/// feel through gloves, and a shutter that does not thump has not obviously
/// fired. Generators are prepared before use — an unprepared generator can lag
/// by enough to feel disconnected from the touch that caused it.
enum Haptics {
    private static let impact = UIImpactFeedbackGenerator(style: .medium)
    private static let rigid = UIImpactFeedbackGenerator(style: .rigid)
    private static let heavy = UIImpactFeedbackGenerator(style: .heavy)
    private static let soft = UIImpactFeedbackGenerator(style: .soft)
    private static let notice = UINotificationFeedbackGenerator()

    static func prepare() {
        impact.prepare(); rigid.prepare(); heavy.prepare()
        soft.prepare(); notice.prepare()
    }
    static func shutter() { impact.impactOccurred(intensity: 1.0); impact.prepare() }

    /// The squish tick. Deliberately quiet — this fires on every primary
    /// button, and recordStart() must stay the unmistakable one.
    static func press() { soft.impactOccurred(intensity: 0.7); soft.prepare() }

    /// As hard as the Taptic Engine goes. This is felt through a glove, in the
    /// dark, while the phone is being held at arm's length -- .rigid at 0.9 was
    /// a polite tap for a quiet room, and the room is a desert.
    ///
    /// Two hits: a single heavy impact still reads as a bump against something
    /// rather than as the phone answering you, and the pair is unmistakable.
    static func recordStart() {
        heavy.impactOccurred(intensity: 1.0)
        heavy.prepare()
        // A second route to the same motor, and the one that actually works on
        // the chat. UIFeedbackGenerator is advisory: iOS drops it silently
        // under conditions it does not report, which is how three separate
        // explanations for the chat's dead haptic each fit the evidence and
        // each turned out to be wrong. This system sound is the older, blunter
        // path and is not subject to the same suppression. Do not "simplify"
        // it away -- the UIFeedbackGenerator call alone was silent here for a
        // whole afternoon.
        AudioServicesPlaySystemSound(1519)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.07) {
            heavy.impactOccurred(intensity: 1.0)
            heavy.prepare()
            AudioServicesPlaySystemSound(1520)
        }
    }
    static func recordStop() { rigid.impactOccurred(intensity: 1.0); rigid.prepare() }
    static func filed() { notice.notificationOccurred(.success); notice.prepare() }
    /// Opening or closing a row. Lighter than anything the camera does — this
    /// is a disclosure, not a commitment.
    static func tap() { soft.impactOccurred(intensity: 0.5); soft.prepare() }
    static func refused() { notice.notificationOccurred(.warning); notice.prepare() }
}

/// Owns the capture session, the photo output and the voice recorder.
@MainActor
final class CameraController: NSObject, ObservableObject {
    @Published var ready = false
    @Published var denied = false
    @Published var recording = false
    /// The still just taken, held for the `.captured` stage preview.
    @Published var lastPhoto: UIImage?
    /// 0…1, driven by the recorder's own metering. This is the feedback that
    /// proves the microphone is hearing you — a static "recording" label proves
    /// only that a flag was set.
    @Published var level: CGFloat = 0
    @Published var elapsed: TimeInterval = 0
    /// Set when recording could not start, so the screen can say why instead of
    /// looking broken.
    @Published var problem: String?
    /// What the memo said. Transcribed on the phone, so it works on playa.
    @Published var transcript: String = ""
    @Published var transcribing = false

    private var meterTimer: Timer?

    let session = AVCaptureSession()
    private let output = AVCapturePhotoOutput()
    private var recorder: AVAudioRecorder?
    private var folder: URL?
    private var takenAt: Date?

    /// The session is configured off the main thread — `startRunning` blocks,
    /// and doing it inline freezes the viewfinder's first frame.
    func start() async {
        guard await requestCamera() else { denied = true; return }
        guard !session.isRunning else { ready = true; return }

        session.beginConfiguration()
        session.sessionPreset = .photo
        if let device = AVCaptureDevice.default(for: .video),
           let input = try? AVCaptureDeviceInput(device: device),
           session.canAddInput(input) {
            session.addInput(input)
        }
        if session.canAddOutput(output) { session.addOutput(output) }
        session.commitConfiguration()

        let s = session
        await Task.detached { s.startRunning() }.value
        ready = true
    }

    func stop() {
        let s = session
        Task.detached { if s.isRunning { s.stopRunning() } }
    }

    private func requestCamera() async -> Bool {
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized: return true
        case .notDetermined:
            return await withCheckedContinuation { c in
                AVCaptureDevice.requestAccess(for: .video) { c.resume(returning: $0) }
            }
        default: return false
        }
    }

    // MARK: - Photo

    func capturePhoto() {
        let now = Date()
        takenAt = now
        // Reuses the folder if a voice note already opened one, so a memo
        // followed by a photo lands in the same capture rather than orphaning
        // the audio in a folder of its own.
        if folder == nil { folder = CaptureStore.newFolder(at: now) }
        let settings = AVCapturePhotoSettings(format: [
            AVVideoCodecKey: AVVideoCodecType.jpeg
        ])
        output.capturePhoto(with: settings, delegate: self)
    }

    // MARK: - Voice memo

    /// Recording needs the microphone, and REMEMBER may be the first screen the
    /// user opens — ASK is where mic access used to be requested, so someone who
    /// goes straight to REMEMBER has never been asked. AVAudioRecorder does not
    /// prompt; it simply produces silence.
    func primeMic() async {
        if #available(iOS 17.0, *) {
            guard AVAudioApplication.shared.recordPermission == .undetermined else { return }
            _ = await withCheckedContinuation { c in
                AVAudioApplication.requestRecordPermission { c.resume(returning: $0) }
            }
        }
    }

    /// Plays the begin tone, then opens the microphone once it has finished so
    /// the tone is not the first thing on the recording.
    func startRecording() {
        guard stagedToRecord() else { return }
        recording = true                 // the ring reacts on touch, not 300ms later
        // Opens NOW, with no tone in front of it.
        //
        // This used to be `Sounds.begin { openRecorder() }`, which waits for the
        // beep to finish so it is not the first thing on the recording. That is
        // right for a button you tap and then speak into, and wrong for one you
        // hold: the haptic says "go" on touch-down, people start talking
        // immediately, and the half-second of tone was eaten out of the front
        // of every memo. Losing the first words is far worse than losing a beep
        // nobody asked for -- the haptic is the cue, and it is felt, not heard.
        openRecorder()
    }

    /// The checks that must pass before we make any noise at all.
    private func stagedToRecord() -> Bool {
        // A voice note on its own is a whole note. This used to refuse with
        // "Take the photo first" because the folder was only ever created by
        // the shutter, which made the photo mandatory and silently threw away
        // the memo -- and half the useful notes are said, not photographed,
        // because your hands are full of the thing you are describing.
        if folder == nil {
            let now = Date()
            takenAt = now
            folder = CaptureStore.newFolder(at: now)
        }
        if #available(iOS 17.0, *),
           AVAudioApplication.shared.recordPermission != .granted {
            print("LUCY: record aborted — mic permission "
                  + "\(AVAudioApplication.shared.recordPermission.rawValue)")
            problem = "No microphone access — Settings ▸ Lucy."
            Haptics.refused()
            return false
        }
        return true
    }

    private func openRecorder() {
        guard let folder else {
            print("LUCY: record aborted — no capture folder (shutter not pressed?)")
            return
        }
        // The camera session owns the audio route while it runs. Recording has
        // to share it, not seize it, or setActive throws and the recorder is
        // handed a session it cannot use.
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(.playAndRecord, mode: .default,
                                    options: [.defaultToSpeaker, .allowBluetooth])
            try session.setActive(true, options: [])
        } catch {
            print("LUCY: audio session failed — \(error.localizedDescription)")
            problem = "The microphone is busy."
            recording = false
            Haptics.refused()
            return
        }

        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
            AVSampleRateKey: 44100,
            AVNumberOfChannelsKey: 1,
            AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue,
        ]
        do {
            let r = try AVAudioRecorder(
                url: folder.appendingPathComponent("memo.m4a"), settings: settings)
            r.isMeteringEnabled = true          // required before averagePower works
            r.prepareToRecord()
            let started = r.record()
            recorder = r
            recording = started
            problem = started ? nil : "The recorder would not start."
            if started {
                startMetering()
            } else {
                Haptics.refused()
            }
            print("LUCY: record started=\(started) -> \(folder.lastPathComponent)/memo.m4a")
        } catch {
            problem = error.localizedDescription
            Haptics.refused()
            print("LUCY: recorder init failed — \(error.localizedDescription)")
        }
    }

    /// Polls the recorder's meter. 20 Hz is enough to read as continuous motion
    /// without spending battery on a redraw nobody can perceive.
    private func startMetering() {
        elapsed = 0
        meterTimer?.invalidate()
        meterTimer = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self, let r = self.recorder, r.isRecording else { return }
                r.updateMeters()
                // averagePower is dBFS: -160 (silence) to 0 (clipping). Speech
                // lives in the top ~50 dB, so anything below -50 reads as zero.
                let db = r.averagePower(forChannel: 0)
                let normalised = CGFloat(max(0, (db + 50) / 50))
                // Ease upward fast and fall slowly, or the ring flickers on
                // every syllable gap and looks like a fault.
                self.level = normalised > self.level
                    ? normalised
                    : self.level * 0.82 + normalised * 0.18
                self.elapsed = r.currentTime
            }
        }
    }

    func stopRecording() {
        let url = recorder?.url
        recorder?.stop()
        recorder = nil
        recording = false
        meterTimer?.invalidate()
        meterTimer = nil
        level = 0
        try? AVAudioSession.sharedInstance().setActive(false, options: [.notifyOthersOnDeactivation])
        if let url {
            let bytes = (try? FileManager.default
                .attributesOfItem(atPath: url.path)[.size] as? Int) ?? 0
            let size = bytes ?? 0
            // A recorder that ran but heard nothing produces a tiny file. Say so
            // rather than reporting success.
            if size < 2_000 {
                problem = "Recorded, but the file is empty — check the microphone."
                Haptics.refused()
            } else {
                Haptics.recordStop()
                Sounds.end()
                transcribe(url)
            }
            print("LUCY: record stopped — memo.m4a \(size) bytes, \(elapsed)s")
        }
    }

    // MARK: - Transcription

    /// Turns the memo into text on the phone. `requiresOnDeviceRecognition` is
    /// not an optimisation here — it is the whole point. A transcript that needs
    /// Apple's servers is a transcript that does not exist on playa, which is
    /// the only place these recordings are made.
    ///
    /// The text is written beside the audio so the post-burn pipeline can read
    /// it without running speech recognition again, and the audio is kept either
    /// way: a transcript is a convenience, the recording is the record.
    private func transcribe(_ url: URL) {
        guard SFSpeechRecognizer.authorizationStatus() == .authorized else { return }
        guard let recogniser = SFSpeechRecognizer(locale: Locale(identifier: "en-US")),
              recogniser.isAvailable else { return }

        transcript = ""
        transcribing = true
        let request = SFSpeechURLRecognitionRequest(url: url)
        request.requiresOnDeviceRecognition = recogniser.supportsOnDeviceRecognition
        request.shouldReportPartialResults = true
        // A voice note is usually about a person or a thing by name, which is
        // exactly what general dictation gets wrong.
        request.contextualStrings = CampVocabulary.terms

        recogniser.recognitionTask(with: request) { [weak self] result, error in
            Task { @MainActor in
                guard let self else { return }
                if let result {
                    self.transcript = result.bestTranscription.formattedString
                    if result.isFinal {
                        self.transcribing = false
                        self.writeTranscript(url)
                        print("LUCY: transcript (\(self.transcript.count) chars)")
                    }
                }
                if let error {
                    self.transcribing = false
                    print("LUCY: transcription failed — \(error.localizedDescription)")
                }
            }
        }
    }

    private func writeTranscript(_ memoURL: URL) {
        guard !transcript.isEmpty else { return }
        let out = memoURL.deletingLastPathComponent()
            .appendingPathComponent("transcript.txt")
        try? transcript.write(to: out, atomically: true, encoding: .utf8)
    }

    /// Called when the user files the capture: writes the sidecar and clears
    /// state for the next one.
    func finish(attached: [String]) {
        if let folder, let takenAt {
            CaptureStore.writeMeta(folder, taken: takenAt, attached: attached,
                                   transcript: transcript)
        }
        folder = nil
        takenAt = nil
        lastPhoto = nil
        transcript = ""
        problem = nil
    }
}

extension CameraController: AVCapturePhotoCaptureDelegate {
    nonisolated func photoOutput(_ output: AVCapturePhotoOutput,
                                 didFinishProcessingPhoto photo: AVCapturePhoto,
                                 error: Error?) {
        guard let data = photo.fileDataRepresentation() else { return }
        Task { @MainActor in
            if let folder { try? data.write(to: folder.appendingPathComponent("photo.jpg")) }
            lastPhoto = UIImage(data: data)
        }
    }
}

/// The live viewfinder. A plain UIView whose backing layer is the preview layer,
/// so it resizes with the frame instead of needing manual layout.
struct CameraPreview: UIViewRepresentable {
    let session: AVCaptureSession

    final class PreviewView: UIView {
        override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
        var previewLayer: AVCaptureVideoPreviewLayer { layer as! AVCaptureVideoPreviewLayer }
    }

    func makeUIView(context: Context) -> PreviewView {
        let view = PreviewView()
        view.previewLayer.session = session
        view.previewLayer.videoGravity = .resizeAspectFill
        return view
    }

    func updateUIView(_ view: PreviewView, context: Context) {
        if view.previewLayer.session !== session { view.previewLayer.session = session }
    }
}
