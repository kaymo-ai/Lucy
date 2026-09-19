import Foundation
import CryptoKit

// Fetching Lucy's brain.
//
// The full model is 3.11 GB (2.89 GiB). Far too large for the app bundle -- App Store
// Connect will not take it, and an over-the-air install of that size is not
// something anyone does standing in a parking lot -- so it lives in Documents
// and arrives separately, once, on signal.
//
// Until now "separately" meant a cable and `devicectl copy to`, which works for
// exactly one phone: Marcus's. Every other tester would install the app, get no
// model, and see LucyBrain.answer return false -- at which point the app falls
// back to the string templates that predate the LLM, silently. That does not
// look like a missing model. It looks like a bad one. It cost most of an
// evening on 2026-08-09 and it would have cost every alpha tester the same.
//
// So: a manifest in a bucket, a background download, and a gate in front of the
// app that will not let you in until she has a brain.

/// What the bucket says is current. Kept deliberately small -- a version, a
/// URL, a size and a hash is everything the phone needs to decide whether what
/// it already has is the right thing.
struct ModelManifest: Decodable, Equatable {
    struct Model: Decodable, Equatable {
        let file: String
        let url: URL
        let bytes: Int64
        let sha256: String
    }
    /// The full model, for 8 GB phones. This key must keep its name and its
    /// meaning forever: build 131 is already on testers' phones and cannot
    /// parse a manifest without it. That failure presents as "can't reach the
    /// model list" and leaves them with no way forward.
    let model: Model
    /// A smaller, lower-compute model for 6 GB phones. Optional in both
    /// directions -- an older app ignores the key, a newer app survives its
    /// absence.
    let small: Model?
    /// E7's question encoder, 318 MB. Optional in both directions for exactly
    /// the same reason `small` is: build 131 is on testers' phones and must
    /// keep parsing this manifest, and a phone that never gets this file must
    /// keep working. Without it `Embedder.shared` is nil and retrieval is
    /// lexical, which is what it was before E7 -- a degradation, never a
    /// failure to answer.
    ///
    /// Not gated on `Device.marginal`. The encoder is 318 MB and runs once per
    /// question against a 512-token context; the thermal argument that splits
    /// the chat model in two does not apply to it, and a 6 GB phone benefits
    /// from better recall more than an 8 GB one, not less.
    let encoder: Model?

    /// What this phone should fetch.
    ///
    /// 6 GB devices thermally panic on the full model; an iPhone 15 tester
    /// reported exactly that within a day of the alpha going out. The fix is
    /// fewer active parameters rather than a smaller quantisation of the same
    /// model: the heat is sustained compute, not memory pressure, and a lighter
    /// quant does the same arithmetic per token.
    var forThisDevice: Model {
        (Device.marginal ? small : nil) ?? model
    }

    /// True when this phone is being given the lighter model, which answers
    /// less well and which the app says out loud rather than hiding.
    var isSmallForThisDevice: Bool {
        Device.marginal && small != nil
    }
}

/// Whether this phone can hold Gemma 4 E2B.
///
/// The model sits at ~3.6 GB resident, measured. With the
/// increased-memory-limit entitlement an 8 GB phone had 6,131 MB available, so
/// it fits with room; a 6 GB phone gets proportionally less -- roughly 4.3 GB
/// -- which fits with almost none, and a photo capture or a backgrounded app
/// could tip it into a jetsam kill mid-answer. Nobody has measured that case.
///
/// This matters before the download, not after. The deployment target is iOS
/// 17, which installs on a 4 GB iPhone XS, and without a check that phone
/// spends twenty minutes fetching 3.11 GB and is then killed on load with no
/// explanation at all.
enum Device {
    static var memoryGiB: Double {
        // --pretend-memory 6 renders the warning states. The Simulator reports
        // the Mac's memory, so neither the block nor the warning can otherwise
        // be seen before a tester on a small phone sees them first.
        let args = ProcessInfo.processInfo.arguments
        if let i = args.firstIndex(of: "--pretend-memory"), i + 1 < args.count,
           let gb = Double(args[i + 1]) {
            return gb
        }
        return Double(ProcessInfo.processInfo.physicalMemory) / 1_073_741_824
    }

    /// Below this it cannot work: 4 GB phones cannot hold the model at any
    /// allowance, so the download is refused rather than wasted.
    static var tooSmall: Bool { memoryGiB < 5.5 }

    /// What the HARDWARE says, with no choice applied. This is the thermal
    /// fact: a 6 GB phone panicked on the full model within a day of the alpha
    /// going out, and that stays true no matter what the owner picks. Keep the
    /// warning attached to this, never to `marginal` -- otherwise choosing
    /// Full silently removes the warning that the choice is risky, which is
    /// exactly backwards.
    ///
    /// 6.5, not 7.5. **iOS does not report the nominal figure**: an 8 GB
    /// iPhone 16 Pro reports `memoryGiB=7.46`, measured off the device on
    /// 2026-08-25 and now written to the journal on every model load. The old
    /// 7.5 was set to the number on the box, so it caught every 8 GB phone by
    /// 0.04 GiB and handed the camp's newest hardware the 1B model. Marcus's
    /// phone sat there for two weeks answering questions about RV power cords.
    ///
    /// The band this has to split, at the ~0.93 ratio that 7.46/8 implies:
    ///
    ///     4 GB -> ~3.7 GiB   blocked by `tooSmall`
    ///     6 GB -> ~5.6 GiB   marginal, gets Light by default
    ///     8 GB -> 7.46 GiB   measured; gets Full by default
    ///
    /// 6.5 sits clear of both edges. Do not tighten it towards either without
    /// a `DEVICE:` line from the phone in question -- that is the mistake this
    /// comment exists to prevent, and the line exists so it costs a minute.
    static var thermallyMarginal: Bool { memoryGiB < 6.5 && !tooSmall }

    /// Which model to actually use: the hardware's answer unless the owner
    /// has overridden it.
    static var marginal: Bool {
        if let forced = override { return forced == .small }
        return thermallyMarginal
    }

    // MARK: - Forcing a model, on dev builds only

    /// Every input to the model choice, on one line, for the journal.
    ///
    /// Kept here rather than assembled at the call site because the inputs are
    /// private to this decision and a caller that reassembles them can drift
    /// from what the decision actually used.
    static var decisionLine: String {
        let raw = UserDefaults.standard.string(forKey: overrideKey) ?? "nil"
        return String(format: "memoryGiB=%.2f", memoryGiB)
            + " marginal=\(marginal) tooSmall=\(tooSmall)"
            + " overrideKey=\(raw) overrideInForce=\(override.map(\.rawValue) ?? "nil")"
    }

    enum Override: String { case small, full }

    /// Lets the dev build run either model on a phone that would otherwise get
    /// only one. Testing the 6 GB experience currently requires a 6 GB phone,
    /// which is a poor reason not to test it.
    ///
    /// Now readable in release too. It was debug-only on the reasoning that a
    /// release build "must decide from the hardware and nothing else, or a
    /// tester ends up on the wrong model with no way to tell" -- and the
    /// second half of that came true through the first. `memoryGiB` put an
    /// 8 GB iPhone 16 Pro under the 7.5 line, so Auto handed it the 1B model,
    /// and with no picker in release there was no way back: retrieval passed
    /// the model eight Oz-hole facts and it answered about RV power cords for
    /// two weeks. Hardware alone is a good default and a bad only-option.
    ///
    /// No stale-value risk from ungating the getter. The setter has always
    /// written unconditionally, but the only UI that called it was debug-only,
    /// and a debug build is a different bundle identifier with its own
    /// container -- so no release container has ever had this key written.
    static var override: Override? {
        get {
            guard let raw = UserDefaults.standard.string(forKey: overrideKey) else { return nil }
            return Override(rawValue: raw)
        }
        set {
            UserDefaults.standard.set(newValue?.rawValue, forKey: overrideKey)
        }
    }
    private static let overrideKey = "model-override"

    /// Said out loud, because the choice was previously silent -- which is why
    /// "it downloaded the small one" could not be checked without going through
    /// the app's container over a cable.
    static func describeChoice(small: Bool) -> String {
        let why = override.map { "forced \($0.rawValue)" }
            ?? String(format: "%.1f GB", memoryGiB)
        return "MODEL choice: \(small ? "small" : "full") (\(why))"
    }
}

@MainActor
final class ModelDownload: NSObject, ObservableObject {

    enum State: Equatable {
        case checking                       // reading the manifest
        case ready                          // the model is on the phone and verified
        case needed(Int64)                  // absent; this many bytes to fetch
        case waitingForWiFi(Int64)
        case downloading(received: Int64, total: Int64)
        case verifying
        case failed(String)

        var isReady: Bool { self == .ready }
    }

    @Published private(set) var state: State = .checking

    /// The one instance. A background `URLSession` is identified by a string,
    /// and creating a second session with the same identifier is a runtime
    /// error -- so this cannot be per-view.
    static let shared = ModelDownload()

    private static let sessionID = "ai.kaymo.Lucy.model"
    private static let manifestURL = URL(
        string: "https://storage.googleapis.com/lucy-snails-releases/manifest.json")!

    /// Set when the system relaunches us to say a background download finished.
    var backgroundCompletion: (() -> Void)?

    private var manifest: ModelManifest?

    /// Whether this phone is on the lighter model. Read by the gate to say so
    /// before the download, and by the chat to keep saying so afterwards --
    /// nobody remembers a disclaimer they read once, three weeks earlier, and
    /// an answer that is worse for a knowable reason should say which reason.
    var usingSmallModel: Bool { manifest?.isSmallForThisDevice ?? Device.marginal }

    /// What is actually loaded, named from the file on disk rather than from
    /// what the manifest thinks -- those can disagree, and the file wins.
    var loadedDescription: String {
        let name = (LucyBrain.modelPath as NSString).lastPathComponent
        let bytes = (try? FileManager.default
            .attributesOfItem(atPath: LucyBrain.modelPath)[.size] as? Int64) ?? nil
        guard let bytes else { return "No model on this phone" }
        return "\(name) · \(String(format: "%.2f GB", Double(bytes) / 1_000_000_000))"
    }

    private lazy var session: URLSession = {
        // Background transfers do not work in the Simulator: every download
        // fails immediately with NSURLErrorDomain -1 (unknown), which is what
        // the first end-to-end run of this hit. The Simulator is also the only
        // place the fresh-install path can be exercised, since a real phone
        // already has the model -- so the config is chosen at runtime and the
        // real one still ships to devices.
        #if targetEnvironment(simulator)
        let config = URLSessionConfiguration.default
        #else
        let config = URLSessionConfiguration.background(withIdentifier: Self.sessionID)
        config.sessionSendsLaunchEvents = true
        #endif
        // Wi-Fi only, and not negotiable. 3.11 GB over cellular is somebody's
        // month, and this is a thing you do once, at home, before you leave.
        config.allowsCellularAccess = false
        // Discretionary lets iOS defer the transfer to a "better" time, which
        // on a screen the user is watching means a progress bar that never
        // moves.
        config.isDiscretionary = false
        return URLSession(configuration: config, delegate: self, delegateQueue: nil)
    }()

    /// Where the model must end up: the path LucyBrain already looks in.
    private var destination: URL {
        URL(fileURLWithPath: LucyBrain.modelPath)
    }

    // MARK: - Deciding what to do

    /// Reads the manifest, compares it to what is on the phone, and either
    /// settles on `.ready` or works out how much has to be fetched.
    ///
    /// A model already in Documents is trusted on size alone. Re-hashing 3.11 GB
    /// on every launch costs seconds of spinning for a file that nothing but
    /// this class writes; the hash is checked once, when it lands.
    func check() async {
        state = .checking

        // A model pushed over the cable wins outright. It is not in the
        // manifest, so its size will never match, and the gate shows for
        // anything that is not `.ready` -- without this the whole app is
        // locked behind an offer to download the very file we are trying to
        // compare against. See `LucyBrain.sideloadedModelFile`.
        #if DEBUG
        if LucyBrain.sideloadedModelPath != nil {
            self.manifest = try? await fetchManifest()
            if let manifest { EncoderDownload.shared.consider(manifest.encoder) }
            state = .ready
            return
        }
        #endif

        // Offline, with a model already present, is the playa case. It must
        // not block on a manifest it cannot fetch, so this branch comes first
        // and answers with whatever is installed.
        guard let manifest = try? await fetchManifest() else {
            state = installedSize() ?? 0 > 0
                ? .ready
                : .failed("Can't reach the model list.")
            return
        }
        self.manifest = manifest
        EncoderDownload.shared.consider(manifest.encoder)

        // What this phone should be running, which is not necessarily what it
        // IS running: the owner can now switch between the two from the menu.
        let wanted = manifest.forThisDevice

        // Adopt a file that is already in Documents under the wanted name.
        // Without this, switching back to a model the phone has already
        // fetched offers to download it again -- `destination` follows
        // `installedKey`, so it points at the model being switched AWAY from,
        // whose size can never match. Both models stay on disk by design, and
        // this is what makes coming back instant rather than another 806 MB.
        let docs = FileManager.default.urls(for: .documentDirectory,
                                            in: .userDomainMask)[0]
        let wantedPath = docs.appendingPathComponent(wanted.file).path
        let wantedSize = (try? FileManager.default
            .attributesOfItem(atPath: wantedPath)[.size] as? Int64) ?? nil
        if wantedSize == wanted.bytes {
            UserDefaults.standard.set(wanted.file, forKey: LucyBrain.installedKey)
            state = .ready
            return
        }

        // Wrong size means a newer model, a truncated one, or one this phone
        // has not fetched yet.
        noteChoice(manifest)
        state = .needed(wanted.bytes)
        return
    }

    /// The size of the model currently pointed at by `installedKey`, or nil.
    private func installedSize() -> Int64? {
        guard FileManager.default.fileExists(atPath: destination.path) else { return nil }
        return (try? FileManager.default
            .attributesOfItem(atPath: destination.path)[.size] as? Int64) ?? nil
    }

    /// Records which model this phone settled on. Silent choices are the ones
    /// that cannot be argued with later.
    private func noteChoice(_ manifest: ModelManifest) {
        Journal.write(Device.describeChoice(small: manifest.isSmallForThisDevice)
                      + " -> \(manifest.forThisDevice.file)")
    }

    private func fetchManifest() async throws -> ModelManifest {
        var request = URLRequest(url: Self.manifestURL)
        // The manifest is small and changes; the model is huge and does not.
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.timeoutInterval = 15
        let (data, _) = try await URLSession.shared.data(for: request)
        return try JSONDecoder().decode(ModelManifest.self, from: data)
    }

    // MARK: - Fetching

    func begin() {
        guard let model = manifest?.forThisDevice else { return }
        // Recorded before the download rather than after, so that a file which
        // lands while the app is not running is still findable: the delegate
        // writes to LucyBrain.modelPath, which reads this.
        UserDefaults.standard.set(model.file, forKey: LucyBrain.installedKey)
        state = .downloading(received: 0, total: model.bytes)
        let task = session.downloadTask(with: model.url)
        // Lets iOS show a sensible size in Settings, and schedule properly.
        task.countOfBytesClientExpectsToReceive = model.bytes
        task.resume()
    }

    /// Throws away a half-finished or corrupt download and starts again.
    func retry() {
        session.getAllTasks { tasks in
            tasks.forEach { $0.cancel() }
            Task { @MainActor in self.begin() }
        }
    }

    // MARK: - Verifying

    /// Hashes the file in 4 MB chunks. Reading 3.11 GB into a Data to hash it
    /// is a way to be killed by the memory limiter on the last step of a
    /// twenty-minute download.
    nonisolated private static func sha256(of url: URL) -> String? {
        guard let handle = try? FileHandle(forReadingFrom: url) else { return nil }
        defer { try? handle.close() }
        var hasher = SHA256()
        while true {
            guard let chunk = try? handle.read(upToCount: 4 * 1024 * 1024),
                  !chunk.isEmpty else { break }
            hasher.update(data: chunk)
        }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }
}

// MARK: - URLSession delegate
//
// These arrive on the session's own queue, not the main actor, which is why
// each hops back explicitly.

extension ModelDownload: URLSessionDownloadDelegate {

    nonisolated func urlSession(_ session: URLSession,
                               downloadTask: URLSessionDownloadTask,
                               didWriteData bytesWritten: Int64,
                               totalBytesWritten: Int64,
                               totalBytesExpectedToWrite: Int64) {
        Task { @MainActor in
            self.state = .downloading(received: totalBytesWritten,
                                      total: totalBytesExpectedToWrite)
        }
    }

    nonisolated func urlSession(_ session: URLSession,
                               downloadTask: URLSessionDownloadTask,
                               didFinishDownloadingTo location: URL) {
        // This runs before the delegate returns or the temp file is gone, so
        // the move has to happen here, synchronously, not in a Task.
        let fm = FileManager.default
        let target = URL(fileURLWithPath: LucyBrain.modelPath)
        let staged = target.appendingPathExtension("part")
        try? fm.removeItem(at: staged)
        do {
            try fm.moveItem(at: location, to: staged)
        } catch {
            Task { @MainActor in self.state = .failed("Couldn't save it: \(error.localizedDescription)") }
            return
        }

        Task { @MainActor in self.state = .verifying }

        let expected = downloadTask.originalRequest?.url
        let digest = Self.sha256(of: staged)

        Task { @MainActor in
            guard let manifest = self.manifest else {
                // No manifest to check against; the bytes arrived, take them.
                try? fm.removeItem(at: target)
                try? fm.moveItem(at: staged, to: target)
                self.state = .ready
                return
            }
            guard digest == manifest.forThisDevice.sha256 else {
                try? fm.removeItem(at: staged)
                // A hash mismatch is a corrupt or truncated file. Saying so is
                // the difference between "try again" and "the model is bad".
                self.state = .failed(
                    "The download arrived damaged. \(expected?.lastPathComponent ?? "The file") "
                    + "didn't match its checksum. Tap to try again.")
                return
            }
            try? fm.removeItem(at: target)
            do {
                try fm.moveItem(at: staged, to: target)
                self.state = .ready
            } catch {
                self.state = .failed("Couldn't put it in place: \(error.localizedDescription)")
            }
        }
    }

    nonisolated func urlSession(_ session: URLSession,
                               task: URLSessionTask,
                               didCompleteWithError error: Error?) {
        guard let error else { return }
        let ns = error as NSError
        // The domain and code, always. "unknown error" was what this showed the
        // first time it was ever run, which is worth precisely nothing to
        // whoever is standing in a car park trying to make it work.
        print("LUCY: download failed — \(ns.domain) \(ns.code): \(ns.localizedDescription)")
        print("LUCY: userInfo \(ns.userInfo)")
        Task { @MainActor in
            // Cancelled by `retry()` is not a failure worth reporting.
            if ns.code == NSURLErrorCancelled { return }
            if case .verifying = self.state { return }
            if case .ready = self.state { return }
            if ns.code == NSURLErrorNotConnectedToInternet
                || ns.code == NSURLErrorDataNotAllowed {
                let total = self.manifest?.forThisDevice.bytes ?? 0
                self.state = .waitingForWiFi(total)
                return
            }
            self.state = .failed("\(error.localizedDescription) "
                                 + "(\(ns.domain) \(ns.code))")
        }
    }

    nonisolated func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) {
        Task { @MainActor in
            self.backgroundCompletion?()
            self.backgroundCompletion = nil
        }
    }
}
