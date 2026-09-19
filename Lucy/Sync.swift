import CryptoKit
import Foundation

// Talking to the camp server.
//
// Everything here is optional at every point. There is no signal on playa
// except sometimes, so sync is a thing you press and never a thing that
// happens on its own — and every path through this file has to fail without
// making the app look broken. The backend being down for the whole week must
// cost nothing except that nobody syncs.

enum Backend {
    static let baseURL = URL(string: "https://lucy.marcusfoster.com/v1")!

    /// What the app tells the server to prove it is the camp's app.
    ///
    /// Nobody types this. Being on the TestFlight invite list *is* the
    /// authentication — Marcus adds people by Apple ID, and asking someone who
    /// already had to be invited to also type a password is friction that buys
    /// nothing. The alternative was a code shared in the camp WhatsApp thread,
    /// which would have leaked the same way with an extra step.
    ///
    /// It is extractable from the binary by anyone holding an IPA, and that is
    /// the accepted level: this keeps strangers out of a camp feed, and it is
    /// not protecting anything that would survive a determined attacker who is
    /// also a camp member.
    ///
    /// Rotating it needs a new build, which is the one real cost. The server
    /// accepts a list, so an old code can keep working while builds roll out.
    static let campSecret = "replace-me-with-the-camp-secret"
    /// Short on purpose. The phone is either on Starlink or it is not, and
    /// making someone watch a spinner for sixty seconds before being told
    /// "no signal" is worse than telling them in six.
    static let probeTimeout: TimeInterval = 8
    /// A photo and a minute of audio over a shared satellite link.
    static let uploadTimeout: TimeInterval = 180
}

/// Who this phone is, as far as the camp is concerned.
@MainActor
final class Identity: ObservableObject {
    static let shared = Identity()

    private static let deviceKey = "device-id"
    private static let tokenKey = "camp-token"

    let deviceID: UUID
    @Published private(set) var token: String?

    var joined: Bool { token != nil }

    private init() {
        if let existing = Keychain.read(Self.deviceKey),
           let id = UUID(uuidString: existing) {
            deviceID = id
        } else {
            let id = UUID()
            Keychain.write(Self.deviceKey, id.uuidString)
            deviceID = id
        }
        token = Keychain.read(Self.tokenKey)
    }

    func store(token: String) {
        Keychain.write(Self.tokenKey, token)
        self.token = token
    }

    func leave() {
        Keychain.delete(Self.tokenKey)
        token = nil
    }
}

enum SyncError: LocalizedError {
    case unreachable
    case badCode
    case unauthorised
    case tooBig
    case server(Int)

    var errorDescription: String? {
        switch self {
        case .unreachable:
            return Style.current.isPlayful
                ? "Couldn't reach camp — I'll keep everything safe here."
                : "Couldn't reach the camp server. Try again when you have signal."
        case .badCode:
            // Nobody types a code, so this is never the user's mistake: it
            // means the secret baked into this build is not one the server
            // still accepts.
            return "This version of Lucy is too old for the camp server. "
                 + "Ask Marcus for a new build."
        case .unauthorised:
            return "This phone isn't joined to the camp any more."
        case .tooBig:
            return "That note is too big to send."
        case .server(let code):
            return "The camp server said \(code)."
        }
    }
}

actor SyncClient {
    static let shared = SyncClient()

    private let session: URLSession = {
        let config = URLSessionConfiguration.default
        // False on purpose. waitsForConnectivity turns "no signal" into a
        // request that hangs until the timeout instead of failing now, and
        // the whole point of the probe is a fast, honest answer.
        config.waitsForConnectivity = false
        return URLSession(configuration: config)
    }()

    // MARK: - Reachability

    /// Never throws: "can I sync" is yes or no, and an error type here would
    /// only get mapped back to a bool by every caller.
    func health() async -> Bool {
        var request = URLRequest(url: Backend.baseURL.appendingPathComponent("health"))
        request.timeoutInterval = Backend.probeTimeout
        guard let (_, response) = try? await session.data(for: request),
              let http = response as? HTTPURLResponse else { return false }
        return http.statusCode == 200
    }

    // MARK: - Joining

    /// Silent. Nothing is asked of anyone: the app carries the camp secret,
    /// and notes are anonymous so there is no name to collect.
    func join() async throws -> String {
        let deviceID = await Identity.shared.deviceID
        var request = URLRequest(url: Backend.baseURL.appendingPathComponent("join"))
        request.httpMethod = "POST"
        request.timeoutInterval = Backend.probeTimeout
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "code": Backend.campSecret,
            "deviceId": deviceID.uuidString,
        ])

        guard let (data, response) = try? await session.data(for: request),
              let http = response as? HTTPURLResponse else {
            throw SyncError.unreachable
        }
        if http.statusCode == 403 { throw SyncError.badCode }
        guard http.statusCode == 200,
              let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let token = body["token"] as? String else {
            throw SyncError.server(http.statusCode)
        }
        return token
    }

    // MARK: - Notes

    /// Uploads one capture. Returns false when the server already had it,
    /// which is still a success — it means an earlier attempt got further than
    /// this phone realised.
    func upload(_ capture: Capture, noteID: UUID, takenAt: Date,
                transcript: String, token: String) async throws -> Bool {
        let boundary = "lucy-\(UUID().uuidString)"
        var body = Data()

        func part(name: String, filename: String, type: String, data: Data) {
            body.append(Data("--\(boundary)\r\n".utf8))
            let disposition = "Content-Disposition: form-data; name=\"\(name)\";"
                + " filename=\"\(filename)\"\r\n"
            body.append(Data(disposition.utf8))
            body.append(Data("Content-Type: \(type)\r\n\r\n".utf8))
            body.append(data)
            body.append(Data("\r\n".utf8))
        }

        let meta: [String: Any] = [
            "noteId": noteID.uuidString,
            "takenAt": ISO8601DateFormatter().string(from: takenAt),
            "transcript": transcript,
            "attached": [],
            "appVersion": Bundle.main.infoDictionary?["CFBundleShortVersionString"] ?? "dev",
        ]
        part(name: "meta", filename: "meta.json", type: "application/json",
             data: try JSONSerialization.data(withJSONObject: meta))
        if let photo = try? Data(contentsOf: capture.photo) {
            part(name: "photo", filename: "photo.jpg", type: "image/jpeg", data: photo)
        }
        if capture.hasMemo, let memo = try? Data(contentsOf: capture.memo) {
            part(name: "memo", filename: "memo.m4a", type: "audio/mp4", data: memo)
        }
        body.append(Data("--\(boundary)--\r\n".utf8))

        var request = URLRequest(url: Backend.baseURL.appendingPathComponent("notes"))
        request.httpMethod = "POST"
        request.timeoutInterval = Backend.uploadTimeout
        request.setValue("multipart/form-data; boundary=\(boundary)",
                         forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")

        guard let (data, response) = try? await session.upload(for: request, from: body),
              let http = response as? HTTPURLResponse else { throw SyncError.unreachable }
        if http.statusCode == 401 { throw SyncError.unauthorised }
        if http.statusCode == 413 { throw SyncError.tooBig }
        guard http.statusCode == 200 else { throw SyncError.server(http.statusCode) }
        let parsed = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        return (parsed?["duplicate"] as? Bool) == false
    }

    /// How many notes the camp has that this phone has not seen, and the new
    /// cursor. The media is not fetched: the list is cheap and the media is
    /// not, and knowing a note exists is what stops two people noting the same
    /// thing.
    func list(since: Int, token: String) async throws -> (count: Int, cursor: Int) {
        var comps = URLComponents(url: Backend.baseURL.appendingPathComponent("notes"),
                                  resolvingAgainstBaseURL: false)!
        comps.queryItems = [URLQueryItem(name: "since", value: String(since))]
        var request = URLRequest(url: comps.url!)
        request.timeoutInterval = Backend.probeTimeout
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")

        guard let (data, response) = try? await session.data(for: request),
              let http = response as? HTTPURLResponse else { throw SyncError.unreachable }
        if http.statusCode == 401 { throw SyncError.unauthorised }
        guard http.statusCode == 200,
              let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let notes = body["notes"] as? [[String: Any]],
              let cursor = body["cursor"] as? Int else {
            throw SyncError.server(http.statusCode)
        }
        return (notes.count, cursor)
    }

    // MARK: - Chatlog

    /// One turn of the phone's chat log, shaped for /v1/chatlog.
    ///
    /// The id is derived by the caller from (device, turn rowid) with the same
    /// idiom notes use, so an upload that dies halfway retries with identical
    /// ids and the server deduplicates instead of double-storing.
    struct ChatlogEntry {
        let id: UUID
        let question: String
        let answer: String
        let model: String?
        let askedAt: Date
        /// Whether retrieval found nothing for this turn. Sent so the camp's
        /// gap list is built from what happened rather than from how the
        /// answer reads -- she is meant to be funny when she has nothing now,
        /// and a joke matches none of the refusal phrases the server used to
        /// look for. nil for turns logged before the column existed; the
        /// server falls back to their wording.
        let unanswered: Bool?
    }

    /// Sends a batch of turns (the server takes at most 500) and returns how
    /// many were newly stored. A retry that stores 0 is a success: it means an
    /// earlier attempt got further than this phone realised.
    func uploadChatlog(entries: [ChatlogEntry], token: String) async throws -> Int {
        let iso = ISO8601DateFormatter()
        var request = URLRequest(url: Backend.baseURL.appendingPathComponent("chatlog"))
        request.httpMethod = "POST"
        request.timeoutInterval = Backend.uploadTimeout
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "entries": entries.map { entry -> [String: Any] in
                [
                    "id": entry.id.uuidString,
                    "question": entry.question,
                    "answer": entry.answer,
                    "model": entry.model.map { $0 as Any } ?? NSNull(),
                    // camelCase like every other field on this wire —
                    // models.py renames nothing and neither do we.
                    "askedAt": iso.string(from: entry.askedAt),
                    "unanswered": entry.unanswered.map { $0 as Any } ?? NSNull(),
                ]
            },
        ])

        guard let (data, response) = try? await session.data(for: request),
              let http = response as? HTTPURLResponse else { throw SyncError.unreachable }
        if http.statusCode == 401 { throw SyncError.unauthorised }
        if http.statusCode == 413 { throw SyncError.tooBig }
        guard http.statusCode == 200,
              let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let stored = body["stored"] as? Int else {
            throw SyncError.server(http.statusCode)
        }
        return stored
    }

    // MARK: - Answers

    /// One answer a member typed, shaped for /v1/answers.
    ///
    /// No gapKey. The server normalises the question and assigns the key --
    /// see the spec. Swift folds case through Unicode and splits on
    /// CharacterSet.alphanumerics; Postgres does neither the same way, and a
    /// key that disagreed on one codepoint would file this against a gap that
    /// does not exist, silently.
    struct AnswerEntry {
        let id: UUID
        let question: String
        let body: String
    }

    /// Returns how many were newly stored. A retry that stores 0 is a success.
    func uploadAnswers(entries: [AnswerEntry], token: String) async throws -> Int {
        var request = URLRequest(url: Backend.baseURL.appendingPathComponent("answers"))
        request.httpMethod = "POST"
        request.timeoutInterval = Backend.uploadTimeout
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "answers": entries.map { entry -> [String: Any] in
                ["id": entry.id.uuidString,
                 "question": entry.question,
                 "body": entry.body]
            },
        ])

        guard let (data, response) = try? await session.data(for: request),
              let http = response as? HTTPURLResponse else { throw SyncError.unreachable }
        if http.statusCode == 401 { throw SyncError.unauthorised }
        if http.statusCode == 413 { throw SyncError.tooBig }
        guard http.statusCode == 200,
              let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let stored = body["stored"] as? Int else {
            throw SyncError.server(http.statusCode)
        }
        return stored
    }

    /// Answers the camp approved that this phone has not seen.
    ///
    /// Its own cursor, separate from the notes cursor: the two advance for
    /// unrelated reasons and sharing one would make a quiet week of notes hide
    /// a week of answers.
    func fetchApproved(since: Int, token: String) async throws
        -> (answers: [CampAnswer], cursor: Int)
    {
        var comps = URLComponents(url: Backend.baseURL.appendingPathComponent("answers"),
                                  resolvingAgainstBaseURL: false)!
        comps.queryItems = [URLQueryItem(name: "since", value: String(since))]
        var request = URLRequest(url: comps.url!)
        request.timeoutInterval = Backend.probeTimeout
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")

        guard let (data, response) = try? await session.data(for: request),
              let http = response as? HTTPURLResponse else { throw SyncError.unreachable }
        if http.statusCode == 401 { throw SyncError.unauthorised }
        guard http.statusCode == 200,
              let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let raw = body["answers"] as? [[String: Any]],
              let cursor = body["cursor"] as? Int else {
            throw SyncError.server(http.statusCode)
        }
        // A malformed row is skipped, not thrown on. One bad answer must not
        // cost the phone every good one in the same batch.
        let answers = raw.compactMap { row -> CampAnswer? in
            guard let idString = row["id"] as? String, let id = UUID(uuidString: idString),
                  let gapKey = row["gapKey"] as? String,
                  let question = row["question"] as? String,
                  let text = row["body"] as? String else { return nil }
            return CampAnswer(id: id, gapKey: gapKey, question: question, body: text)
        }
        if answers.count != raw.count {
            // A dropped row and a quiet week look identical from the Sync
            // screen. If a key name ever drifts this is the only thing that
            // says so.
            print("LUCY: \(raw.count - answers.count) of \(raw.count) answers "
                + "dropped — a field the phone expects is missing or renamed")
        }
        return (answers, cursor)
    }
}

// MARK: - A capture's identity on the server

extension Capture {
    /// A marker file beside the media. Plain files, like everything else in a
    /// capture folder: the post-burn pipeline can read the whole state of a
    /// note without this app's cooperation, and a database that disagreed with
    /// the folder would be a third source of truth.
    var uploadedMarker: URL { folder.appendingPathComponent("uploaded") }
    var uploaded: Bool { FileManager.default.fileExists(atPath: uploadedMarker.path) }

    func markUploaded() {
        FileManager.default.createFile(atPath: uploadedMarker.path, contents: Data())
    }

    private var meta: [String: Any]? {
        guard let data = try? Data(contentsOf: folder.appendingPathComponent("meta.json")),
              let parsed = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return nil }
        return parsed
    }

    /// The note's identity on the camp server, chosen here so an upload that
    /// dies halfway can be retried without creating a second note.
    ///
    /// Captures taken before sync existed carry no id, and there are already
    /// some on every tester's phone. Deriving one from the device id and the
    /// folder name gives those the same id on every retry — a UUIDv5 by hand,
    /// because Foundation has no namespace UUIDs.
    func noteID(device: UUID) -> UUID {
        if let stored = meta?["note_id"] as? String, let id = UUID(uuidString: stored) {
            return id
        }
        return Capture.derivedID(device: device, folder: id)
    }

    static func derivedID(device: UUID, folder: String) -> UUID {
        var d = Array(SHA256.hash(data: Data("\(device.uuidString):\(folder)".utf8)))
        d[6] = (d[6] & 0x0F) | 0x50      // version 5
        d[8] = (d[8] & 0x3F) | 0x80      // RFC 4122 variant
        return UUID(uuid: (d[0], d[1], d[2], d[3], d[4], d[5], d[6], d[7],
                           d[8], d[9], d[10], d[11], d[12], d[13], d[14], d[15]))
    }

    /// When the shutter was pressed.
    ///
    /// The folder name is an ISO timestamp with the colons swapped for
    /// hyphens, so it is the fallback when meta.json is missing — a capture
    /// whose app was killed before it was filed still knows its own time.
    /// Only the time portion is repaired: the date contains hyphens of its
    /// own, and replacing those too corrupts it.
    var takenAt: Date {
        if let stamp = meta?["taken_at"] as? String,
           let date = ISO8601DateFormatter().date(from: stamp) {
            return date
        }
        let parts = id.split(separator: "T", maxSplits: 1)
        if parts.count == 2 {
            let repaired = parts[0] + "T"
                + parts[1].replacingOccurrences(of: "-", with: ":")
            if let date = ISO8601DateFormatter().date(from: String(repaired)) {
                return date
            }
        }
        return Date()
    }

    var savedTranscript: String { meta?["transcript"] as? String ?? text ?? "" }
}
