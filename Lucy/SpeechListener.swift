import Foundation
import Speech
import AVFoundation

// On-device speech recognition.
//
// There is no signal on playa, so `requiresOnDeviceRecognition` is forced on.
// That is not a preference -- with it off, Apple sends audio to a server and
// the whole feature silently stops working at the exact moment it is needed.
// The cost is that the locale's recognition assets must already be downloaded,
// which is why `unavailable` is a real, reported state rather than an error to
// swallow.

@MainActor
final class SpeechListener: ObservableObject {

    enum Status: Equatable {
        case idle
        case denied                 // user said no to mic or recognition
        case unavailable(String)    // on-device assets missing, or no recogniser
        case listening
        case finished(String)
        case failed(String)
    }

    @Published private(set) var status: Status = .idle
    @Published private(set) var transcript: String = ""

    /// 0…1, from the microphone itself. This is what the recording animation
    /// moves to, and the point is that it cannot lie: a screen that responds to
    /// your voice is proof the tap is live and hearing you. Speaking into a
    /// microphone that was not yet open is the failure this app already had,
    /// and nothing on screen disagreed with it.
    ///
    /// Deliberately the same curve and smoothing as Capture.level, so the two
    /// tabs animate identically.
    @Published private(set) var level: CGFloat = 0

    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private let engine = AVAudioEngine()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?

    /// Set while `finish()` is waiting for the recogniser's final result.
    /// Resumed exactly once, by whichever of the final result, an error or the
    /// deadline arrives first.
    private var pendingFinal: CheckedContinuation<String, Never>?

    /// Bumped by `start()` on every press and by `finish()` whenever it finds
    /// nothing yet listening. `start()` is `async` and suspends at
    /// `await requestAccess()`; a press short enough to be released before
    /// that suspension resumes hits `finish()`'s `status == .listening` guard
    /// while the mic is still opening. Without this, that in-flight `start()`
    /// finishes afterwards anyway -- opens the tap, activates the recording
    /// audio session, sets `status = .listening` -- for a press nobody will
    /// ever call `finish()` for again: the next press's `start()` sees
    /// `status == .listening` and no-ops, and the press after that drains the
    /// stale task's transcript instead of its own. `start()` checks this
    /// after its own suspension point and tears itself down rather than going
    /// live for a press that already ended.
    private var generation = 0

    /// How long to wait for the final transcription after the button is
    /// released. It normally lands in a fraction of this; the deadline exists
    /// so a recogniser that never finalises cannot leave the button dead.
    private static let finalDeadline: Duration = .milliseconds(1200)

    /// How long to keep the tap open after the finger lifts.
    ///
    /// Enough for the tail of a word, not enough to feel like a lag
    /// before the answer. The opening deaf window is 0.284s median and
    /// costs a whole word; this end costs a syllable, so it is smaller.
    static let tailCapture = Duration.milliseconds(250)

    /// Permission is asked for once per launch, not once per question.
    /// `requestAccess` round-trips to a system daemon even when the answer is
    /// already yes, and on the button-down path that delay is speech the
    /// microphone is not yet recording -- "how can I get access to the Empire
    /// storage lot" reached retrieval as "I get access to the Empire storage
    /// lot".
    private var accessGranted = false
    private var sessionConfigured = false

    /// True only when speech can actually run with no network.
    var canRunOffline: Bool {
        recognizer?.supportsOnDeviceRecognition == true
    }

    // MARK: - Permission

    /// Does the part of setup that does not need the button: permission, and
    /// the audio category. Call it when the view that owns the microphone
    /// appears, so pressing the button does not have to wait for a permission
    /// round-trip that has already been answered.
    ///
    /// The engine is deliberately NOT prepared here. `AVAudioEngine.prepare()`
    /// queries the input node's hardware format, and with no active session
    /// that format is 0 Hz / 0 channels -- AVAudioEngine then raises an
    /// Objective-C exception, which Swift cannot catch, and the app dies on
    /// launch. It must stay in `start()`, after `setActive(true)`.
    func prewarm() async {
        guard !sessionConfigured else { return }
        guard await requestAccess() else { return }
        // The category is deliberately NOT set here, only in start().
        //
        // Setting it on appear was the one structural difference between this
        // tab and NOTE, which sets its category only when recording begins --
        // and NOTE's haptic worked while this one did not. A recording category
        // standing from the moment the screen opens appears to be enough for
        // iOS to stop honouring the Taptic Engine, even with the session never
        // activated. The permission round-trip is the expensive half anyway,
        // and that is still cached.
        //
        // An unprepared generator fires weakly or not at all the first time,
        // which Capture.swift's own comment warns about. It was prepared for
        // the camera and never for the microphone.
        Haptics.prepare()
        sessionConfigured = true
    }

    func requestAccess() async -> Bool {
        if accessGranted { return true }
        let speech = await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { continuation.resume(returning: $0) }
        }
        guard speech == .authorized else {
            status = .denied
            return false
        }

        let mic = await withCheckedContinuation { continuation in
            if #available(iOS 17.0, *) {
                AVAudioApplication.requestRecordPermission { continuation.resume(returning: $0) }
            } else {
                AVAudioSession.sharedInstance().requestRecordPermission {
                    continuation.resume(returning: $0)
                }
            }
        }
        guard mic else {
            status = .denied
            return false
        }
        accessGranted = true
        return true
    }

    // MARK: - Listening

    func start() async {
        guard status != .listening else { return }
        generation += 1
        let myGeneration = generation
        transcript = ""
        // Everything between the button going down and the tap being installed
        // is speech that is simply not recorded. Logged so the cost of this
        // path is a measured number rather than an assumption.
        let pressedAt = ContinuousClock.now

        guard await requestAccess() else { return }

        // The button may already have come up while the line above was
        // suspended -- see `generation`'s comment. Opening the mic for a
        // press that is already over is worse than doing nothing: it leaves
        // a live tap and an active recording session with no `finish()`
        // coming to close either.
        guard myGeneration == generation else {
            Journal.write("SPEECH start abandoned; press ended before the tap opened")
            return
        }

        guard let recognizer, recognizer.isAvailable else {
            status = .unavailable("Speech recognition is not available on this device.")
            return
        }
        guard recognizer.supportsOnDeviceRecognition else {
            // Deliberately refuse rather than fall back to the network: a
            // feature that works in the tent and fails on playa is worse than
            // one that says so up front.
            status = .unavailable(
                "Offline speech isn't installed for English. Add it in "
                + "Settings ▸ General ▸ Keyboard ▸ Dictation, on signal, before the burn."
            )
            return
        }

        // The "speak now" cue is NOT fired here. It used to be, and neither the
        // tap nor the tone ever arrived: this function is async, the cue sat
        // after `await requestAccess()`, and by the time that suspension
        // resumed the audio session work had begun and iOS had stopped
        // honouring either. NOTE fired the same call synchronously from its
        // view and worked, which is what identified this.
        //
        // HoldToRecord fires it on touch-down instead, before anything async
        // happens. The ~0.3s tone then covers the ~0.2-0.35s this function
        // takes to open the tap, so whoever waits for the beep is speaking
        // into a live microphone.

        do {
            let session = AVAudioSession.sharedInstance()
            // Always set here, never on appear -- see prewarm(). A recording
            // category left standing while the screen is merely open costs the
            // haptic and ducks whatever else is playing the whole time.
            try session.setCategory(.playAndRecord, mode: .measurement,
                                    options: [.duckOthers, .defaultToSpeaker])
            try session.setActive(true, options: .notifyOthersOnDeactivation)

            let request = SFSpeechAudioBufferRecognitionRequest()
            request.shouldReportPartialResults = true
            request.requiresOnDeviceRecognition = true
            // Camp names, or the recogniser guesses at ordinary words that
            // sound similar — and a question about someone is mostly their
            // name, so mishearing it loses the question rather than a word.
            request.contextualStrings = CampVocabulary.terms
            self.request = request

            let input = engine.inputNode
            let format = input.outputFormat(forBus: 0)
            input.removeTap(onBus: 0)
            input.installTap(onBus: 0, bufferSize: 1024, format: format) { [weak self] buffer, _ in
                request.append(buffer)
                // ~47 Hz at 1024 frames / 48 kHz, close enough to Capture's
                // 20 Hz timer that the two read as the same motion.
                guard let samples = buffer.floatChannelData?[0] else { return }
                let count = Int(buffer.frameLength)
                guard count > 0 else { return }
                var sum: Float = 0
                for i in 0..<count { sum += samples[i] * samples[i] }
                let rms = (sum / Float(count)).squareRoot()
                // dBFS, floored so log10 cannot go to negative infinity on a
                // buffer of digital silence.
                let db = 20 * log10(max(rms, 1e-7))
                let normalised = CGFloat(max(0, (db + 50) / 50))
                Task { @MainActor in self?.settle(level: normalised) }
            }

            engine.prepare()
            try engine.start()

            // Same abandonment check as above the recogniser guards, placed
            // here too as a second line of defence: this is the earliest
            // point after the tap and the recording session are both really
            // live, so it is the last chance to tear them straight back down
            // if the press ended while this line was reached. Nothing in the
            // code above suspends between the first check and here today,
            // but that is a fact about the current code, not a guarantee --
            // this check costs nothing and does not depend on it staying true.
            guard myGeneration == generation else {
                Journal.write("SPEECH start abandoned after the tap opened; press already ended")
                teardown()
                return
            }

            status = .listening
            // The "speak now" cue, fired HERE rather than on touch-down.
            //
            // The microphone is deaf for a measured median of 0.284s after the
            // press (min 0.237, max 0.449, n=107) while the category is set,
            // the session activated and the tap installed. Cueing on
            // touch-down told people to start talking into a microphone that
            // was not open yet, and the first word was the one that went
            // missing.
            //
            // HoldToRecord still ticks quietly on touch-down, because a
            // control that does nothing for a third of a second feels broken.
            // What moved is the unmistakable one -- the double thump that
            // means the phone is listening, which is now true when it fires.
            //
            // The old comment warned that a haptic fired from inside this
            // function never arrived, because iOS stops honouring the Taptic
            // Engine once the audio session is coming up. That is why
            // recordStart() ALSO goes through AudioServicesPlaySystemSound,
            // which the same investigation found is not subject to that
            // suppression. If the motor is silent here, the system sound is
            // not, and the journal line below says which moment it was asked
            // at.
            Journal.write("SPEECH tap open; cueing now")
            Haptics.recordStart()
            let deaf = ContinuousClock.now - pressedAt
            Journal.write("SPEECH deaf for \(deaf) after press")

            task = recognizer.recognitionTask(with: request) { [weak self] result, error in
                Task { @MainActor in
                    guard let self else { return }
                    if let result {
                        self.transcript = result.bestTranscription.formattedString
                        // The final result is the whole utterance. Everything
                        // before it is a partial that stops at the last word
                        // the recogniser had settled on, which is why the old
                        // code lost the end of every question.
                        if result.isFinal {
                            Journal.write("SPEECH final: \(self.transcript)")
                            self.deliverFinal(self.transcript)
                        }
                    }
                    if let error {
                        // Whatever was heard beats nothing; a failure after the
                        // button is released must still commit the partial.
                        self.deliverFinal(self.transcript)
                        if self.status == .listening {
                            self.status = .failed(error.localizedDescription)
                            self.teardown()
                        }
                    }
                }
            }
        } catch {
            status = .failed(error.localizedDescription)
            teardown()
        }
    }

    /// Ends the utterance and settles on whatever was heard. Releasing the
    /// button is the commit gesture, so this always produces a final answer --
    /// an empty transcript included, which the caller reports rather than
    /// pretending a question was asked.
    ///
    /// This waits for the recogniser's *final* transcription rather than
    /// taking the last partial. `endAudio()` is what asks for that final; the
    /// previous version called it and then cancelled the task in the same
    /// breath, so the final never arrived and the tail of the sentence went
    /// with it -- "do Marcus and Sammy like each" was the whole question as
    /// far as the rest of the app was concerned.
    func finish() async -> String {
        guard status == .listening else {
            // Invalidate any start() still mid-flight for this press -- see
            // `generation`'s comment -- so it tears itself down instead of
            // opening the mic for a press that is already over.
            generation += 1
            let leftover = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
            // `.idle` is the only state this can be in while start() has not
            // yet reached a terminal status of its own (.listening included)
            // -- the seven presses in 107 that produced no "SPEECH final"
            // line were, going by the journal, this. Any other status here
            // is start() having already failed on its own terms (denied,
            // unavailable, an engine error), which already explains itself
            // and should not be overwritten.
            if status == .idle {
                Journal.write("SPEECH lift before the tap opened; too short to catch")
                status = .failed("That was too quick to catch -- hold a beat longer and try again.")
            } else {
                Journal.write("SPEECH lift with nothing listening (status: \(status))")
            }
            return leftover
        }

        Journal.write("SPEECH lift")

        // Keep listening for a beat after the lift.
        //
        // The other half of the truncation. A finger comes off the button as
        // the last word is being said, not after it, so stopping the engine
        // synchronously on release cuts the tail -- and the recogniser is
        // then asked to finalise a word it only half heard, which is worse
        // than not hearing it at all.
        //
        // Deliberately shorter than the opening deaf window: the cost of
        // waiting here is a fraction of a second before the answer starts,
        // where the cost of cutting early is a wrong transcript.
        try? await Task.sleep(for: Self.tailCapture)

        // Stop feeding audio, but leave the recognition task alive: it still
        // owes us the final result.
        if engine.isRunning {
            engine.stop()
            engine.inputNode.removeTap(onBus: 0)
        }
        request?.endAudio()

        let heard = await withCheckedContinuation { continuation in
            pendingFinal = continuation
            Task { @MainActor [weak self] in
                try? await Task.sleep(for: Self.finalDeadline)
                guard let self, self.pendingFinal != nil else { return }
                Journal.write("SPEECH final timed out; using partial")
                self.deliverFinal(self.transcript)
            }
        }

        teardown()

        // The "got it" cue is fired by HoldToRecord on the lift, for the same
        // reason as the start cue: synchronously, in the view, rather than
        // after an await in here.
        let settled = heard.trimmingCharacters(in: .whitespacesAndNewlines)
        status = .finished(settled)
        return settled
    }

    /// Rises immediately and falls slowly. Tracking the meter symmetrically
    /// makes the animation flicker out in every gap between syllables, which
    /// reads as a fault rather than as listening.
    private func settle(level normalised: CGFloat) {
        level = normalised > level ? normalised : level * 0.82 + normalised * 0.18
    }

    /// Resumes `finish()`, once. The final result, an error and the deadline
    /// race each other and all three call this.
    private func deliverFinal(_ text: String) {
        guard let continuation = pendingFinal else { return }
        pendingFinal = nil
        continuation.resume(returning: text)
    }

    func reset() {
        teardown()
        transcript = ""
        status = .idle
    }

    private func teardown() {
        // A teardown from anywhere else -- reset(), an error -- must not leave
        // `finish()` awaiting a continuation that can no longer be resumed.
        deliverFinal(transcript)
        level = 0
        if engine.isRunning {
            engine.stop()
            engine.inputNode.removeTap(onBus: 0)
        }
        request?.endAudio()
        task?.cancel()
        request = nil
        task = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }
}
