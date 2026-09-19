import Foundation
import CryptoKit

// Fetching E7's question encoder.
//
// Deliberately its own class with its own background session, rather than a
// second file threaded through `ModelDownload`. That class is the gate: it
// decides whether the app can work at all, its delegate hardcodes one
// destination and checks one hash, and every phone in the alpha depends on it.
// Adding a second file to it would put an optional 318 MB nicety inside the
// machinery that decides whether Lucy runs, and a bug in the plumbing would
// present as "the model is broken".
//
// This one can fail all day. Without the encoder `Embedder.shared` is nil,
// retrieval is exactly what it was before E7, and nobody is blocked. So there
// is no gate, no UI, no retry button -- it tries on launch, and if it does not
// land it tries again next launch. The journal says which happened.

@MainActor
final class EncoderDownload: NSObject, ObservableObject {
    static let shared = EncoderDownload()

    /// Its own identifier, so iOS never confuses these tasks with the model's.
    private static let sessionID = "ai.kaymo.Lucy.encoder"

    private var expected: ModelManifest.Model?
    private var started = false

    private lazy var session: URLSession = {
        let config = URLSessionConfiguration.background(withIdentifier: Self.sessionID)
        // Unlike the model, this one IS discretionary: it is an improvement to
        // recall, and it can wait for wifi and power rather than competing
        // with the download the app is actually gated on.
        config.isDiscretionary = true
        config.sessionSendsLaunchEvents = false
        return URLSession(configuration: config, delegate: self, delegateQueue: nil)
    }()

    /// Called with whatever the manifest said. Does nothing at all when the
    /// key is absent, when the file is already here at the right size, or
    /// when a fetch is already running.
    func consider(_ model: ModelManifest.Model?) {
        guard let model else {
            Journal.write("ENCODER manifest carries no encoder — lexical only")
            return
        }
        expected = model
        let path = Embedder.path
        if let size = (try? FileManager.default
            .attributesOfItem(atPath: path)[.size]) as? Int64 {
            if size == model.bytes {
                Journal.write("ENCODER present, \(size) bytes")
                return
            }
            // Wrong size is a truncated or superseded file. It is not usable
            // and it is not worth keeping.
            Journal.write("ENCODER wrong size (\(size) vs \(model.bytes)) — refetching")
            try? FileManager.default.removeItem(atPath: path)
        }
        guard !started else { return }
        started = true
        Journal.write("ENCODER fetching \(model.bytes) bytes")
        let task = session.downloadTask(with: model.url)
        task.countOfBytesClientExpectsToReceive = model.bytes
        task.resume()
    }

    /// Hashed in chunks, for the same reason the model is: reading 318 MB into
    /// a Data to hash it is a way to be killed on the last step.
    nonisolated static func sha256(of url: URL) -> String? {
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

extension EncoderDownload: URLSessionDownloadDelegate {
    nonisolated func urlSession(_ session: URLSession,
                                downloadTask: URLSessionDownloadTask,
                                didFinishDownloadingTo location: URL) {
        // Synchronous, before the delegate returns and the temp file goes.
        let fm = FileManager.default
        let target = URL(fileURLWithPath: Embedder.path)
        let staged = target.appendingPathExtension("part")
        try? fm.removeItem(at: staged)
        guard (try? fm.moveItem(at: location, to: staged)) != nil else {
            Journal.write("ENCODER could not be staged")
            return
        }
        let digest = Self.sha256(of: staged)
        Task { @MainActor in
            defer { self.started = false }
            guard let want = self.expected?.sha256 else {
                try? fm.removeItem(at: staged)
                Journal.write("ENCODER arrived with nothing to check it against")
                return
            }
            guard digest == want else {
                // A mismatched encoder is worse than none: its vectors would
                // not agree with the corpus and cosine between them is just a
                // number, which is the failure package_db.py was written
                // about. Delete rather than keep.
                try? fm.removeItem(at: staged)
                Journal.write("ENCODER checksum mismatch — discarded")
                return
            }
            try? fm.removeItem(at: target)
            do {
                try fm.moveItem(at: staged, to: target)
                // Loaded next launch, not now: Embedder.shared is a `let` that
                // resolves once, and swapping a model out from under a live
                // llama context is not worth the risk for a recall
                // improvement that can wait for the next cold start.
                Journal.write("ENCODER installed — semantic recall from next launch")
            } catch {
                Journal.write("ENCODER could not be put in place: \(error)")
            }
        }
    }

    nonisolated func urlSession(_ session: URLSession, task: URLSessionTask,
                                didCompleteWithError error: Error?) {
        guard let error else { return }
        Task { @MainActor in
            self.started = false
            Journal.write("ENCODER download failed: \(error.localizedDescription)")
        }
    }
}
