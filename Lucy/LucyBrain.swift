import Foundation

// Gemma, doing the phrasing and nothing else.
//
// The spine of this app has always been "the database answers; the LLM only
// phrases it". Until now there was no LLM, so replies were assembled by string
// templates — correct, and obviously mechanical after the second one.
//
// This puts Gemma 4 E2B behind the same rule rather than around it. Retrieval
// chooses the facts. Gemma is handed exactly those facts and told to answer
// using nothing else. The receipts the UI shows come from retrieval, not from
// the model, so a claim can never acquire a citation it did not earn.
//
// The model is not in the app bundle — it is 3.1 GB. It is pushed to the app's
// Documents directory, which is also how it will be delivered before playa:
// once, on signal, deliberately.

/// Appends to a file in Documents as well as the console.
///
/// Console capture needs the phone unlocked at the moment a question is asked,
/// and three diagnoses in a row were lost to that window closing first. A file
/// can be pulled hours later with devicectl, so the evidence outlives the
/// session that produced it.
enum Journal {
    private static var url: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("lucy-journal.txt")
    }

    static func write(_ text: String) {
        print(text)
        let stamped = "[\(ISO8601DateFormatter().string(from: Date()))] \(text)\n"
        guard let data = stamped.data(using: .utf8) else { return }
        if let handle = try? FileHandle(forWritingTo: url) {
            defer { try? handle.close() }
            _ = try? handle.seekToEnd()
            try? handle.write(contentsOf: data)
        } else {
            try? data.write(to: url)
        }
    }
}

@MainActor
final class LucyBrain: ObservableObject {
    enum State: Equatable {
        case absent          // no model on the device
        case loading
        case ready
        case failed(String)
    }

    @Published private(set) var state: State = .absent
    /// Streams in as she writes, so the screen fills rather than freezing.
    @Published private(set) var partial = ""

    private var handle: LlamaHandle?
    private let queue = DispatchQueue(label: "lucy.brain", qos: .userInitiated)

    /// Where the model lives once pushed. Documents, not the bundle: a 3.1 GB
    /// resource makes the app impossible to install over the air, and the model
    /// changes on a different schedule from the code.
    /// Nonisolated: this reads a path and touches no actor state, and the
    /// download delegate -- which runs on the session's own queue -- needs to
    /// know where to put the file it just finished fetching.
    /// The model that was actually installed, which is no longer always the
    /// same file: 8 GB phones get Gemma 4 E2B and 6 GB phones get something
    /// smaller. ModelDownload records the name it wrote.
    ///
    /// The fallback is the original hardcoded name, and it has to stay: phones
    /// that installed before this existed have that file in Documents and no
    /// recorded name, and without the fallback they would silently re-download
    /// 3.11 GB.
    nonisolated static let installedKey = "installed-model-file"
    nonisolated static let legacyModelFile = "gemma-4-E2B-it-Q4_K_M.gguf"

    /// A model pushed over the cable rather than downloaded, for judging a
    /// tuned build on the phone. Dev builds only, and deliberately a fixed
    /// name: setting `installedKey` would need UserDefaults on the device,
    /// which a cable cannot reach, and reusing the manifest's own filename
    /// would make `ModelDownload.check()` see a size mismatch and offer to
    /// replace the tuned file with the stock one -- a swap that looks like
    /// nothing happening and would leave us judging stock while believing it
    /// was tuned. Delete the file to go back to the downloaded model.
    #if DEBUG
    nonisolated static let sideloadedModelFile = "lucy-tuned.gguf"

    /// A model has to be model-sized. `devicectl` has no delete-file command,
    /// so the way to retire a sideload over the cable is to overwrite it with
    /// something small -- and a half-finished 3.4 GB copy would otherwise be
    /// taken for a whole one and fail deep inside llama with a bad-magic
    /// error rather than here.
    nonisolated static let smallestPlausibleModel: Int64 = 100_000_000

    nonisolated static var sideloadedModelPath: String? {
        let docs = FileManager.default.urls(for: .documentDirectory,
                                            in: .userDomainMask)[0]
        let p = docs.appendingPathComponent(sideloadedModelFile).path
        let size = (try? FileManager.default
            .attributesOfItem(atPath: p)[.size] as? Int64) ?? nil
        guard let bytes = size, bytes >= smallestPlausibleModel else { return nil }
        return p
    }
    #endif

    nonisolated static var modelPath: String {
        #if DEBUG
        if let sideloaded = sideloadedModelPath { return sideloaded }
        #endif
        let docs = FileManager.default.urls(for: .documentDirectory,
                                            in: .userDomainMask)[0]
        let name = UserDefaults.standard.string(forKey: installedKey)
            ?? legacyModelFile
        return docs.appendingPathComponent(name).path
    }

    nonisolated static var modelPresent: Bool {
        FileManager.default.fileExists(atPath: modelPath)
    }

    /// Drops the current model and loads whatever `modelPath` now points at.
    /// Switching between the two on a dev build would otherwise need the app
    /// killed and relaunched, since `load()` refuses to run twice.
    func reload() {
        handle = nil
        state = .absent
        load()
    }

    func load() {
        guard state == .absent, Self.modelPresent else {
            if !Self.modelPresent { state = .absent }
            return
        }
        state = .loading
        let path = Self.modelPath
        queue.async { [weak self] in
            do {
                // 4096 is enough for her persona, a dozen facts and an answer.
                // The spike measured 3600 MB resident at this size with 2544 MB
                // still free, so this is the configuration known not to be killed.
                let h = try LlamaHandle(modelPath: path, contextTokens: 4096)
                // Which brain answered, written above the answers it gave.
                // "The UI says I'm on the smaller model but it's not clear to
                // me" could not be settled from the app at all -- it took
                // reading the container over a cable. Every journal now says
                // which file was loaded, how big it was, and which turn markers
                // it understands, so a strange answer can be attributed to a
                // model rather than argued about.
                let size = (try? FileManager.default
                    .attributesOfItem(atPath: path)[.size] as? Int64) ?? nil
                let mb = size.map { String(format: "%.0f MB", Double($0) / 1_000_000) }
                    ?? "unknown size"
                Journal.write("MODEL loaded: \((path as NSString).lastPathComponent) "
                              + "(\(mb), \(h.turnStyle == .gemma3 ? "gemma3" : "gemma4") turns)")
                // And WHY that file and not the other one. The line above says
                // which model answered; it does not say whether the phone was
                // classed as marginal, whether an override was in force, or
                // whether the file on disk is even the one the tier now wants.
                // A tester sat on the 1B model for two weeks and the only
                // clue available was a filename in a menu -- every input to
                // that decision is written down now, so the next one is read
                // rather than reasoned about.
                Journal.write("DEVICE: " + Device.decisionLine
                              + " installed=\((path as NSString).lastPathComponent)")
                Task { @MainActor in
                    self?.handle = h
                    self?.state = .ready
                }
            } catch {
                Task { @MainActor in
                    self?.state = .failed(error.localizedDescription)
                }
            }
        }
    }

    /// Answers in Lucy's voice using only `context`. Returns nil if the model
    /// is not loaded, so the caller can fall back to the composed reply rather
    /// than showing nothing.
    /// `colour` is set only when retrieval found nothing: it carries lore rows
    /// to talk with instead of facts to answer from, and switches which prompt
    /// gets built. Passing it and `context` together is meaningless and the
    /// caller never does -- one is the answer, the other is what you say when
    /// there isn't one.
    func answer(question: String, context: String, colour: String? = nil,
                done: @escaping (String) -> Void) -> Bool {
        guard state == .ready, let handle else { return false }
        partial = ""

        let body = colour.map { Self.emptyHandedPrompt(question: question, colour: $0) }
            ?? Self.prompt(question: question, context: context)
        let prompt = LlamaHandle.chatWrap(body, style: handle.turnStyle)
        // Logged so a wrong answer can be attributed. If the facts are wrong,
        // retrieval is at fault and the model is faithfully repeating them; if
        // the facts are right and the answer is not, the model ignored them.
        // Those are different bugs with different fixes.
        Journal.write("QUESTION: \(question)")
        // Which experiment flags were live, so two transcripts pulled off
        // the same phone on different days can be told apart without asking
        // which build made them.
        Journal.write("FLAGS: gatedPortrait=\(Experiments.shared.gatedPortrait) "
                      + "register=\(Experiments.shared.register.rawValue) "
                      + "rankByRelevance=\(Experiments.shared.rankByRelevance) "
                      + "answerLength=\(Experiments.shared.answerLength.rawValue)")
        Journal.write(colour == nil
                      ? "FACTS GIVEN:\n\(context.isEmpty ? "(none)" : context)"
                      : "EMPTY-HANDED, COLOUR GIVEN:\n\(colour ?? "")")
        // DIAL 3, and the reason it is read here rather than inside
        // `LlamaHandle.generate`: that call runs on `queue`, a plain
        // DispatchQueue with no actor of its own, and `Experiments` is
        // `@MainActor`. Reading it there would need either an `await` this
        // synchronous GCD closure cannot make or an `assumeIsolated` that
        // would be a lie about which thread is running. `answer` is already
        // on the main actor -- and already reads `Experiments.shared` two
        // lines up for the journal -- so the register is captured here,
        // where it is actually safe, and carried down as a value.
        let register = Experiments.shared.register
        queue.async { [weak self] in
            // The KV cache is cleared between turns: each answer is grounded in
            // the facts retrieved for that question, and carrying the previous
            // turn's tokens invites her to reuse a fact that no longer applies.
            handle.clearContext()
            var text = ""
            do {
                try handle.generate(prompt: prompt, maxTokens: 320, register: register) { piece in
                    text += piece
                    let snapshot = text
                    Task { @MainActor in self?.partial = snapshot }
                }
            } catch {
                text = ""
            }
            let final = text.trimmingCharacters(in: .whitespacesAndNewlines)
            Journal.write("ANSWERED:\n\(final)\n----")
            Task { @MainActor in
                self?.partial = ""
                done(final)
            }
        }
        return true
    }

    /// Everything that keeps her honest is in here.
    ///
    /// Rule 1 is scoped to CAMP PARTICULARS, not to all claims. It used to
    /// read "say only what the facts below say", which is the right rule for
    /// the failure it was written for -- she invented lock codes, addresses
    /// and shift times, and someone acting on one of those drives to the
    /// wrong warehouse. It is the wrong rule for everything else. It also
    /// forbade her from knowing that the desert is hot, and the stand-in for
    /// all general knowledge was a 16-row table. Asked why Burning Man is hot
    /// she answered that the facts provided do not contain the information.
    ///
    /// So the line is drawn where the harm is: a name, a date, a number, a
    /// place, who did what, what the camp owns or decided needs a row. The
    /// rest she may know, provided rule 2's separation holds -- what she
    /// worked out must never read as something the camp wrote down. Rule 2
    /// absorbed the old rule 4, which said the same thing in terms of one
    /// section of the fact sheet rather than in terms of the claim.
    ///
    /// NO QUOTED EXAMPLE SENTENCES. Rules used to illustrate themselves with
    /// camp-flavoured lines -- "the barrels live in Doris", "we meet the
    /// driver" -- and she emitted them verbatim as answers, "the barrels live
    /// in Doris" opening four consecutive replies about entirely other things.
    /// It got worse once the turn markers were fixed, because a model that
    /// finally parses the prompt correctly copies its examples faithfully.
    /// Describe the transformation; never write a sentence she could paste.
    ///
    /// The instruction to use only the given facts is the whole guard. It is
    /// stated first, stated plainly, and repeated at the end, because a model
    /// asked to be characterful will otherwise fill gaps with plausible camp
    /// life — and plausible is indistinguishable from true to someone reading
    /// this in the desert.
    ///
    /// DIAL 2 of the register experiment: only the "Your manner:" paragraph
    /// changes per register, chosen by `manner(for:)` below. Same
    /// no-quoted-example rule as everywhere else in this file applies to
    /// every variant, not just the default one.
    ///
    /// The length dial (`Experiments.shared.answerLength`, DIAL 4) is
    /// composed the same way, from `lengthGuidance(for:)` below, into its own
    /// paragraph -- never folded into `manner`, so the two stay orthogonal.
    /// They did not start out that way: every `manner` variant used to argue
    /// for fullness on its own -- "weave every detail... into one telling"
    /// -- which fought the length dial at its own shipping default and made
    /// `.conversational` look like it did nothing. Those claims have moved
    /// out of `manner`, which now carries only voice (register, warmth,
    /// dryness, appetite for the camp's own colour, whether an aside is
    /// welcome), and into `lengthGuidance(.full)` -- the length setting that
    /// means "today's behaviour". `.conversational` and `.terse` narrow how
    /// much she says the same way they always did, without touching rules
    /// 1-5, the persona line, or a word of `manner`. `LengthTests` checks
    /// that the substance moved rather than pinning a byte-identical
    /// prompt, since the text now sits in a different paragraph than it
    /// used to.
    static func prompt(question: String, context: String) -> String {
        let manner = Self.manner(for: Experiments.shared.register)
        let length = Self.lengthGuidance(for: Experiments.shared.answerLength)
        let lengthBlock = length.isEmpty ? "" : "\n\n\(length)"
        return """
        You are Lucy, the Preservation Society's snail art car. You have been to \
        Burning Man since 2018, you carry the camp's sound system, and you are \
        the camp's memory. You belong to the camp and speak about it: the \
        build, the bike fleet, who meets the driver.

        RULES, in order — the earlier the rule, the more it matters:
        1. Anything particular to this camp comes only from the facts below: \
        names, dates, numbers, places, who did what, what the camp owns, what \
        it decided, when it happens. If one of those is not below, you do not \
        know it, and you say so rather than filling it in.
        2. What you know about the desert, the event and the ordinary world \
        is yours to use, and reaching for it is not a failure. Keep it plainly \
        apart from the camp's own record, so nobody takes a thing you worked \
        out for a thing the camp wrote down. Where both bear on the question, \
        the camp's record leads and yours follows it.
        3. Answer the question that was asked. If the asked-for detail — a \
        kind, a place, a time, a number — is not in the facts, say so first, \
        plainly, then give what the facts DO hold about the thing. Never \
        answer around a gap.
        4. The camp's work is the camp's. The facts say "we" because the camp \
        wrote them; you were not there. Credit doing to the camp or a named \
        person — "I" is only for what you remember and say, never for \
        building, storing, hauling or fixing.
        5. Vague question, several possible subjects: ask which they mean, \
        naming the options in one sentence. Facts disagree: give both with \
        dates. Someone asks who to ask: lead with the name. Asked to mock a \
        campmate: decline warmly in your own words, without repeating the \
        mockery or the name inside it.

        \(manner)\(lengthBlock)

        The camp's own words inside the facts are yours to reuse — folded \
        into your sentences, without quotation marks, without the @ that \
        chat handles carry. That colour is on the record; using it is \
        quoting, not inventing. Play with the facts, never with the truth.

        FACTS YOU MAY USE:
        \(context.isEmpty ? "(nothing found)" : context)

        QUESTION: \(question)

        Answer as Lucy, using only the facts above.
        """
    }

    /// The one paragraph that changes per register. Same rule as the doc
    /// comment on `prompt` above: describe the transformation, never write a
    /// sentence she could paste. `default_` is pinned by
    /// `RegisterTests.testDefaultRegisterMannerIsUnchanged` against
    /// whatever it currently reads -- update that pin deliberately if you
    /// touch this again, since "default" only means something if it stays
    /// the same. Internal, not private: `RegisterTests` pins each variant
    /// directly rather than scraping it back out of the assembled prompt.
    ///
    /// Voice only. Every claim about HOW MUCH TO SAY -- weaving every
    /// detail into one telling, a full answer, fewest words -- has moved
    /// out of here and into `lengthGuidance(for:)` below, so a register no
    /// longer argues with whatever the length dial just chose.
    static func manner(for register: Register) -> String {
        switch register {
        case .dry:
            return """
            Your manner: terse and deadpan, no flourish. State the answer, \
            then whatever else bears on it. No aside, no colour beyond what \
            the facts already carry. Start with the answer, never with the \
            question restated.
            """
        case .default_:
            return """
            Your manner: warm, quick, a bit playful — a giant snail with a \
            sound system, not a reference desk. Dry humour welcome; be \
            delighted by the camp. Short sentences. Start with the answer, \
            never with the question restated.
            """
        case .warm:
            return """
            Your manner: warm and openly delighted, a giant snail with a \
            sound system glad to be asked. Lean harder into the camp's own \
            colour than usual — its names, its running jokes, its history. \
            Start with the answer, never with the question restated.
            """
        case .loose:
            return """
            Your manner: loud, giddy, unreasonably delighted — treat the \
            question as the best thing that has happened all week and let \
            it show, piling on the camp's own colour without holding back. \
            Start with the answer, never with the question restated.
            """
        }
    }

    /// DIAL 4, the length experiment: how much she says, independent of
    /// `manner` above (how she says it). Same no-quoted-example rule as
    /// `manner` -- describe the transformation, never write a sentence she
    /// could paste. Internal, not private, for the same reason as `manner`:
    /// `LengthTests` pins each variant directly.
    ///
    /// `.full` is today's shipped behaviour. It used to be the empty string,
    /// because every `manner` variant already argued for fullness on its
    /// own; now that those claims have moved out of `manner` (see the doc
    /// comment there), `.full` is where they live instead -- weaving every
    /// detail that bears on the question into one telling, with its names
    /// and dates, and the guard against stopping early just to be brief.
    /// Said once here rather than duplicated across four `manner` variants,
    /// and no longer fighting `.conversational` or `.terse` for the same
    /// register.
    static func lengthGuidance(for length: AnswerLength) -> String {
        switch length {
        case .full:
            return """
            Your length: weave every detail that bears on the question into \
            one telling, with its names and dates — not just the first \
            detail that answers it, but every detail the question needs. \
            When the records are rich, a full answer runs to a few \
            sentences rather than one. Prefer that longer answer to a \
            clipped one: stopping early to save words is not a reason to \
            leave a supporting detail out.
            """
        case .conversational:
            return """
            Your length: match the answer to the question, not to how much \
            the facts could support. Answer what was asked and stop there; a \
            small question earns a small answer. Only carry a further detail \
            forward when it genuinely bears on what was asked — never leave \
            out something the person needed for the sake of brevity, and \
            never add length the question didn't ask for either.
            """
        case .terse:
            return """
            Your length: the shortest true answer, a sentence or two at \
            most. Give the thing that was actually asked for and stop; leave \
            out supporting detail unless the answer would be wrong or \
            unusable without it.
            """
        }
    }

    /// What she is handed when retrieval came back with nothing.
    ///
    /// There used to be no prompt here at all: an empty fact sheet went into
    /// the ordinary prompt, Rule 1 said say only what the facts say, and she
    /// produced one of three flat sentences. The app then string-matched those
    /// sentences to discover it had refused -- generating a refusal in order to
    /// detect one.
    ///
    /// Nothing is a dead end. She cannot answer the question and says so, and
    /// then she has something to talk with instead: real lore rows, picked at
    /// random, handed over as colour. The invariant is untouched -- every
    /// specific she can utter still comes out of a row. It is only that the
    /// row no longer has to be an answer.
    ///
    /// Same standing rule as `prompt`: NO QUOTED EXAMPLE SENTENCES. Describe
    /// the transformation; a line written here is a line she will paste.
    static func emptyHandedPrompt(question: String, colour: String) -> String {
        """
        You are Lucy, the Preservation Society's snail art car. You have been \
        to Burning Man since 2018, you carry the camp's sound system, and you \
        are the camp's memory.

        Nothing the camp has written down bears on this question. That is the \
        whole situation. It is not a failure and it is not an apology.

        RULES, in order — the earlier the rule, the more it matters:
        1. Do not answer the question, and do not guess at it. Say plainly, \
        in your own words and in one short clause, that the camp has not \
        written this one down.
        2. Then turn to the camp's own colour below and say something with \
        it. Tease the camp, tease yourself, enjoy what these people are \
        like. Affection, never contempt, and never a lesson.
        3. Every specific — a name, a date, a number, a place, a thing that \
        happened — comes from the colour below and nowhere else. What is not \
        below is not yours to state.
        4. Do not pretend the colour answers the question. It is a change of \
        subject and you may let that show.
        5. Two or three sentences in total, once.
        6. Asked to mock a campmate: decline warmly in your own words, \
        without repeating the mockery or the name inside it.

        CAMP COLOUR YOU MAY USE:
        \(colour.isEmpty ? "(nothing)" : colour)

        THE QUESTION YOU CANNOT ANSWER: \(question)

        Answer as Lucy.
        """
    }
}
