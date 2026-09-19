import SwiftUI

// Sync, as a thing you press.
//
// Not a background task and not something that happens on launch. There is no
// signal on playa except sometimes, and an app that quietly tries and quietly
// fails is one whose sync state nobody can reason about. You press it, it tells
// you what happened, and when it cannot reach the server it says so in a
// sentence about the desert rather than about the app.
//
// Nothing to sign in to and nothing to type. Being on the TestFlight list is
// the authentication, and notes carry no name, so the screen is what is
// waiting to go and one button.

struct SyncView: View {
    let face: Face
    @ObservedObject private var identity = Identity.shared
    @Environment(\.dismiss) private var dismiss

    @State private var busy = false
    @State private var status: String?
    @State private var problem: String?
    @State private var waiting: [Capture] = []
    @AppStorage("sync-cursor") private var cursor = 0
    // Separate from the notes cursor on purpose: the two advance for unrelated
    // reasons, and one cursor would let a quiet week of notes hide a week of
    // answers.
    @AppStorage("answers-cursor") private var answersCursor = 0

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text(Style.current.label("Sync"))
                    .font(.eyebrow(15))
                    .tracking(Style.current.eyebrowTracking + Style.current.boost(0.5))
                    .foregroundStyle(face.accent)
                Spacer()
                Button("Done") { dismiss() }
                    .font(.body_(16)).foregroundStyle(face.muted)
            }
            .padding(.bottom, 18)

            anonymity.padding(.bottom, 20)

            if waiting.isEmpty {
                Text(Style.current.isPlayful ? "All caught up — nothing waiting to send."
                                             : "Nothing waiting to send.")
                    .font(.body_(17)).foregroundStyle(face.muted)
            } else {
                Text(waiting.count == 1 ? "1 note waiting"
                     : "\(waiting.count) notes waiting")
                    .font(.eyebrow(12))
                    .tracking(Style.current.eyebrowTracking)
                    .foregroundStyle(face.muted)
                    .padding(.bottom, 8)
                list
            }

            Spacer(minLength: 18)

            if let status {
                Text(status)
                    .font(.body_(17)).foregroundStyle(face.text)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.bottom, 12)
            }
            if let problem {
                Text(problem)
                    .font(.body_(17)).foregroundStyle(face.accent)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.bottom, 12)
            }

            syncButton
        }
        .padding(22)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(face.ground)
        .preferredColorScheme(face.night ? .dark : .light)
        .onAppear(perform: refresh)
    }

    /// Said before anything is sent, not buried in a settings screen.
    ///
    /// It is worded to be true rather than reassuring. Other campers cannot
    /// tell who wrote a note -- the list carries no name and no id. The server
    /// still records which phone sent what, because deleting your own note
    /// later requires it, and claiming otherwise would be a promise the code
    /// does not keep.
    private var anonymity: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Notes go to the camp anonymously")
                .font(.display(22)).foregroundStyle(face.text)
                .fixedSize(horizontal: false, vertical: true)
            Text("No name is attached and nobody can tell whose note is "
                 + "whose. They are shared so the camp doesn't note the same "
                 + "thing twice.")
                .font(.body_(16)).foregroundStyle(face.muted)
                .fixedSize(horizontal: false, vertical: true)
            // The one sentence the camp hears before anything leaves the
            // phone. It has been rewritten twice now, both times because it
            // stopped being true: "stays on your phone" went when chatlog
            // upload shipped, and "privately" went when the gap list opened.
            // Three true things and no more -- the conversations go up, the
            // questions she couldn't answer come back for the camp to answer,
            // and no row anywhere says who asked. The last one is structural,
            // not policy: the chatlog table has no member column.
            Text("Sync sends your notes to everyone in camp, and your "
                 + "conversations with Lucy to the camp's server. Questions she "
                 + "couldn't answer go on a list the camp can answer. The question "
                 + "goes on that list, never who asked it.")
                .font(.body_(16)).foregroundStyle(face.muted)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var list: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                ForEach(waiting, id: \.id) { capture in
                    row(capture)
                    Divider().overlay(face.muted.opacity(0.25))
                }
            }
        }
        .scrollIndicators(.hidden)
    }

    private func row(_ capture: Capture) -> some View {
        HStack(alignment: .top, spacing: 12) {
            // What is actually in the note, so you can see what you are about
            // to hand the camp before you hand it over.
            VStack(spacing: 4) {
                if FileManager.default.fileExists(atPath: capture.photo.path) {
                    Image(systemName: "camera.fill")
                        .font(.system(size: 12)).foregroundStyle(face.muted)
                }
                if capture.hasMemo {
                    Image(systemName: "waveform")
                        .font(.system(size: 12)).foregroundStyle(face.muted)
                }
            }
            .frame(width: 16)

            VStack(alignment: .leading, spacing: 3) {
                let said = capture.savedTranscript
                    .trimmingCharacters(in: .whitespacesAndNewlines)
                Text(said.isEmpty ? "No transcript" : said)
                    .font(.body_(16))
                    .foregroundStyle(said.isEmpty ? face.muted : face.text)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                Text(Self.when.string(from: capture.takenAt))
                    .font(.data(12)).foregroundStyle(face.muted)
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 11)
    }

    private static let when: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "d MMM, HH:mm"
        return f
    }()

    private var syncButton: some View {
        Button {
            Task { await doSync() }
        } label: {
            Text(busy ? "Syncing…" : "Sync")
                .font(.eyebrow(17))
                .tracking(Style.current.eyebrowTracking)
                .foregroundStyle(face.night ? face.ground : .white)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 16)
                .background(Capsule().fill(busy ? face.muted : face.accent))
        }
        .buttonStyle(SquishButtonStyle())
        .disabled(busy)
    }

    // MARK: - Doing it

    private func refresh() {
        waiting = CaptureStore.all().filter { !$0.uploaded }
    }

    private func doSync() async {
        busy = true; problem = nil; status = nil
        defer { busy = false; refresh() }

        // Probed first, so the common failure is one clear sentence rather
        // than a partial upload that stops halfway with a network error.
        guard await SyncClient.shared.health() else {
            problem = SyncError.unreachable.errorDescription
            return
        }

        // Joining is silent and happens the first time it is needed. There is
        // nothing to ask anyone: the app carries the camp secret, and there is
        // no name to collect.
        if !identity.joined {
            do {
                identity.store(token: try await SyncClient.shared.join())
            } catch {
                problem = error.localizedDescription
                return
            }
        }
        guard let token = identity.token else { return }

        let device = identity.deviceID
        var sent = 0
        for capture in waiting.reversed() {
            do {
                let fresh = try await SyncClient.shared.upload(
                    capture,
                    noteID: capture.noteID(device: device),
                    takenAt: capture.takenAt,
                    transcript: capture.savedTranscript,
                    token: token)
                // Marked whether it was newly stored or already there. Both
                // mean the camp has it, and a note that keeps re-uploading
                // because the phone did not believe the server is how a slow
                // connection becomes no connection.
                capture.markUploaded()
                if fresh { sent += 1 }
            } catch {
                problem = error.localizedDescription
                break
            }
        }

        var fromCamp: Int?
        do {
            let (count, newCursor) = try await SyncClient.shared.list(
                since: cursor, token: token)
            cursor = newCursor
            fromCamp = count
        } catch {
            if problem == nil { problem = error.localizedDescription }
        }

        // The chat log, after the notes. Its failure is caught here and never
        // reaches `problem`: the notes went, the sync succeeded, and a batch
        // of turns that will go next time is not a reason to un-say that.
        var chatSent = 0
        var chatFailed = false
        do {
            var lastFirst: Int64 = -1
            while true {
                let pending = ChatLog.shared.pendingUpload()
                guard !pending.isEmpty, pending.first?.rowid != lastFirst else { break }
                lastFirst = pending.first?.rowid ?? -1
                let entries = pending.map { turn in
                    SyncClient.ChatlogEntry(
                        // The same derivation notes use, keyed on the rowid,
                        // so a retry offers the server ids it has already
                        // seen instead of a second copy of every turn.
                        id: Capture.derivedID(device: device,
                                              folder: "chatlog/\(turn.rowid)"),
                        question: turn.question,
                        answer: turn.answer,
                        model: turn.model,
                        askedAt: turn.askedAt,
                        unanswered: turn.unanswered)
                }
                _ = try await SyncClient.shared.uploadChatlog(
                    entries: entries, token: token)
                ChatLog.shared.markUploaded(rowids: pending.map(\.rowid))
                chatSent += pending.count
                if pending.count < 500 { break }
            }
        } catch {
            chatFailed = true
        }

        // Answers after the chat log, for the same reason the chat log goes
        // after the notes: the half that helps everyone else goes first, and a
        // failure here never un-succeeds what already landed.
        var answersSent = 0
        do {
            let pending = AnswerStore.shared.pendingUpload()
            if !pending.isEmpty {
                let entries = pending.map {
                    SyncClient.AnswerEntry(id: $0.id, question: $0.question, body: $0.body)
                }
                answersSent = try await SyncClient.shared.uploadAnswers(
                    entries: entries, token: token)
                // Marked whether newly stored or already there. Both mean the
                // camp has it.
                AnswerStore.shared.markUploaded(rowids: pending.map(\.rowid))
            }
        } catch {
            // Deliberately not surfaced. The notes went; a batch of answers
            // that will go next time is not a reason to un-say that.
            print("LUCY: answer upload deferred — \(error.localizedDescription)")
        }

        var answersIn = 0
        do {
            let (approved, newCursor) = try await SyncClient.shared.fetchApproved(
                since: answersCursor, token: token)
            AnswerStore.shared.store(approved: approved)
            answersCursor = newCursor
            answersIn = approved.count
        } catch {
            print("LUCY: answer fetch deferred — \(error.localizedDescription)")
        }

        if let fromCamp {
            let mine = sent == 1 ? "1 note sent" : "\(sent) notes sent"
            let theirs = fromCamp == 1 ? "1 new from the camp"
                                       : "\(fromCamp) new from the camp"
            var line = "\(mine), \(theirs)"
            if chatSent > 0 {
                line += chatSent == 1 ? ", 1 Lucy question along too"
                                      : ", \(chatSent) Lucy questions along too"
            }
            line += "."
            if chatFailed {
                // Said plainly, not raised as a failure: the flag on each turn
                // is still 0, so the next sync sends exactly these.
                line += " Your Lucy questions didn't get through this time; "
                      + "they'll go with the next sync."
            }
            status = line
        }

        // Approved answers are not a list to browse -- the only visible sign
        // is that she stops saying she does not know, so the line says that.
        if answersIn > 0 {
            let noun = answersIn == 1 ? "answer" : "answers"
            status = (status.map { $0 + ". " } ?? "")
                + "\(answersIn) new \(noun) from camp. She knows more now."
        }

        // Closes itself when it worked, and stays put when it didn't. A sheet
        // that shuts on failure takes the reason with it, and the reason is
        // the only thing worth having when sync fails in the desert. The pause
        // is long enough to read the line it just wrote — dismissing instantly
        // is indistinguishable from the button doing nothing.
        if problem == nil {
            // Longer when the chat log stayed behind: that extra sentence
            // deserves the time it takes to read.
            try? await Task.sleep(for: .milliseconds(chatFailed ? 2200 : 900))
            dismiss()
        }
    }
}
