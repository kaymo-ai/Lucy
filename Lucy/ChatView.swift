import SwiftUI
import UIKit

// ASK as a conversation.
//
// The one-shot version answered a question and stopped. Real use is not like
// that: you ask about Doris, then about the padlock, then about who would know.
// Each of those is a follow-up, and starting over each time is the interface
// getting in the way.
//
// Lucy's replies keep their receipts. Every message she sends that asserts
// something carries the facts it came from, and tapping one opens the message
// it was taken from — the same guarantee the record screen makes, in a form you
// can hold a conversation in.

/// Whether a turn was a gap, for turns the app answered before it started
/// recording that structurally.
///
/// This used to be `isDeadEnd`, and it used to be the mechanism: the app
/// produced a refusal and then matched the text back against a phrase list to
/// discover it had refused -- three of whose phrases the app had written
/// itself. Retrieval already knows. `ChatLog.turn.unanswered` records it at
/// the moment the question is answered, and every live path reads that.
///
/// What is left here is a decoder for history. Turns written before the
/// column existed carry nil, and their words are the only evidence available
/// about them. Nothing new is ever classified this way.
func wasGapByWording(_ answer: String) -> Bool {
    let norm = answer.lowercased()
        .replacingOccurrences(of: "\u{2019}", with: "'")
        .replacingOccurrences(of: "do not", with: "don't")
    return norm.contains("i don't know")
        || norm.contains("nothing in what i've got")
        || norm.contains("that's not something i've been told")
        || norm.contains("i don't have any facts")
        || norm.contains("i don't have a fact")
        || norm.contains("the facts provided don't contain")
        || norm.contains("i can only say what the facts state")
}

/// The one question every caller actually wants answered. Prefers what was
/// recorded; falls back to the wording only when nothing was.
func wasGap(_ turn: Remembered) -> Bool {
    turn.unanswered ?? wasGapByWording(turn.answer)
}

/// What a released press should tell the person, if anything. Reads
/// `SpeechListener.status` rather than inventing new copy: `.failed` and
/// `.unavailable` already carry a sentence written in the app's own voice
/// (see SpeechListener.swift), including the one commit cd2f4cf added for a
/// press too short to open the microphone at all. `.denied` is deliberately
/// silent here -- it carries no message of its own and is a different
/// problem (permissions), not this one. `.finished`/`.idle`/`.listening`
/// have nothing to report; the caller only asks this when `heard` came back
/// empty in the first place.
func missedPressMessage(heard: String, status: SpeechListener.Status) -> String? {
    guard heard.isEmpty else { return nil }
    switch status {
    case .failed(let message), .unavailable(let message):
        return message
    default:
        return nil
    }
}

/// The recording aura's phase -- distinct from `isRecording`, which is pure
/// touch state and still drives the button itself. A finger landing on the
/// button does not mean the microphone can hear yet (measured median 0.284s,
/// max 0.449s of dead air while SpeechListener.start() brings the tap up);
/// this is what lets the aura say "I have your press" without claiming "I can
/// hear you" until `speech.status` agrees.
///
/// A pure mapping so it is testable without a running view -- see
/// RecordingAuraPhaseTests.
enum RecordingAuraPhase: Equatable {
    /// Nothing is happening.
    case off
    /// Touched, but the microphone has not confirmed it is open.
    case ready
    /// `speech.status == .listening`: the microphone is actually live.
    case listening

    static func derive(touching: Bool, status: SpeechListener.Status) -> RecordingAuraPhase {
        guard touching else { return .off }
        if case .listening = status { return .listening }
        return .ready
    }
}

/// Whether the "thinking" placeholder should render as the animated dots
/// rather than the streaming answer. The streaming text is the good part --
/// the moment real tokens exist it must replace the dots, never sit under or
/// behind them, so this is a straight swap keyed on `partial` being empty.
func showsThinkingDots(thinking: Bool, partial: String) -> Bool {
    thinking && partial.isEmpty
}

/// Replaces the old static "…" while Gemma is still working. On a 3.11 GB
/// model on a phone the gap before the first token is long, and a character
/// that never moves reads as frozen rather than thinking.
///
/// Cheap on purpose -- this runs while the model is saturating the CPU: one
/// `@State` bool flipped once in `onAppear`, driving three implicit
/// `repeatForever` animations staggered by `.delay()`. No `TimelineView`, no
/// per-frame timer. It leaves the hierarchy the moment `showsThinkingDots`
/// goes false (the streaming text takes its place), and SwiftUI cancels a
/// repeating animation when its view disappears, so it stops cleanly with no
/// teardown code of its own to get wrong.
private struct ThinkingDots: View {
    let face: Face

    @State private var animating = false

    // Playful gets more overshoot and a touch more time, matching every
    // other spring/duration split in Style.swift; industrial stays tighter.
    // Colour and shape language is identical in both -- only amplitude and
    // pace change, so neither style reads as an afterthought.
    private var peak: CGFloat { Style.current.isPlayful ? 1.35 : 1.15 }
    private var duration: Double { Style.current.isPlayful ? 0.55 : 0.4 }

    var body: some View {
        HStack(spacing: 8) {
            ForEach(0..<3, id: \.self) { i in
                Circle()
                    .fill(face.warm)
                    .frame(width: 10, height: 10)
                    .scaleEffect(animating ? peak : 0.7)
                    .opacity(animating ? 1 : 0.45)
                    .animation(
                        .easeInOut(duration: duration)
                            .repeatForever(autoreverses: true)
                            .delay(Double(i) * 0.15),
                        value: animating
                    )
            }
        }
        .frame(height: 18)
        .onAppear { animating = true }
        .accessibilityLabel("Thinking")
    }
}

struct ChatTurn: Identifiable {
    let id = UUID()
    let mine: Bool
    let text: String
    var facts: [EntityFact] = []
    var entity: Entity? = nil
    /// A thing Lucy was asked to remember. Captures live in the thread rather
    /// than in a separate list — the conversation is the memory, so scrolling
    /// back is how you find what you saved.
    var docs: [DocHit] = []
    var general: [GeneralFact] = []
    var camp: [CampFact] = []
    /// What she was asked, carried only on her own reply turns. Set at the two
    /// places a reply is appended in `ask(_:)`, so the offer to be told can
    /// find the question it belongs to without hunting back through `turns`
    /// for the nearest `mine` turn — a search that a filtered or re-sorted
    /// transcript could get wrong. Greeting turns leave it nil, which is also
    /// why the offer never appears under one: a greeting has no question
    /// behind it, so there is no gap to offer to fill.
    var question: String? = nil
    /// Set when retrieval found nothing for this turn. The offer to be told
    /// keys off this and nothing else. It is the same fact `ChatLog` records,
    /// carried on the view's own turn so the offer does not have to go back to
    /// the database to ask about the reply it is sitting under.
    var unanswered: Bool = false
    /// A quiet centred label rather than a bubble: "Earlier", above restored
    /// turns. Not a message, so it carries no side and no colour.
    var marker: String? = nil
}

struct ChatView: View {
    let face: Face
    let store: EntityStore
    var onClose: (() -> Void)? = nil
    /// Questions to run on appear, for screenshots. Real use never sets this.
    var seed: [String] = []
    /// Forces the recording state on, for screenshots. There is no other way to
    /// photograph it: it exists only while a finger is on the button, and the
    /// simulator cannot hold one down.
    var previewRecording: Bool = false

    @StateObject private var speech = SpeechListener()
    @StateObject private var brain = LucyBrain()
    @State private var thinking = false
    @State private var turns: [ChatTurn] = []
    @State private var typed = ""
    @State private var holding = false
    /// Slid up to keep recording without holding. Rare on the chat -- a
    /// question is short -- but the gesture is the same on both tabs, so it
    /// behaves the same on both.
    @State private var locked = false
    @State private var openFact: Int64?
    @FocusState private var typingFocused: Bool
    // The offer to be told, keyed by turn id rather than one flat flag: the
    // transcript can hold several refusals at once (scroll back and see three
    // "I don't know"s from one session) and each needs to open, fill in and
    // confirm on its own — a single shared flag would make typing an answer
    // for the second one also open the box under the first.
    @State private var tellingLucyFor: Set<UUID> = []
    @State private var toldAnswerFor: [UUID: String] = [:]
    @State private var toldConfirmedFor: Set<UUID> = []
    /// Set when a released press came back with nothing heard -- too short to
    /// open the microphone, denied, whatever `missedPressMessage` found.
    /// Cleared the moment a new press starts, and also fades on its own after
    /// a couple of seconds so it never lingers as stale chrome.
    @State private var missedCue: String? = nil
    /// How many past turns have been pulled into the thread by the "earlier"
    /// control. Counts TURNS as `ChatLog` stores them -- one question and its
    /// answer -- not the two bubbles each becomes.
    @State private var restored = 0
    /// The bottom-most turn last time the thread scrolled itself. Loading
    /// earlier messages grows `turns.count` without changing what is at the
    /// end, and the scroll must not fire then: it would throw you back to the
    /// bottom the instant you asked to read further up.
    @State private var lastEnd: UUID? = nil

    private var isRecording: Bool { holding || locked || previewRecording }
    /// A plausible mid-syllable level, so the screenshot shows the ring and the
    /// edge at a size someone would actually see.
    private var micLevel: CGFloat { previewRecording ? 0.62 : speech.level }
    /// Drives the aura only. `isRecording` above stays pure touch state for
    /// the button itself, which must react the instant a finger lands --
    /// gating the BUTTON on `speech.status` would recreate the same "does
    /// nothing for a third of a second" complaint this fix exists to remove,
    /// just moved from the aura to the button.
    private var auraPhase: RecordingAuraPhase {
        // The screenshot harness holds no real finger and has no real
        // SpeechListener session to report .listening -- it needs the aura's
        // full state on demand.
        if previewRecording { return .listening }
        return .derive(touching: holding || locked, status: speech.status)
    }

    var body: some View {
        VStack(spacing: 0) {
            bar
            transcript
            composer
        }
        .background(face.ground)
        // Tap anywhere off the field to put the keyboard away. The transcript
        // is a ScrollView and its own recogniser eats taps in empty space, so
        // this hangs on the whole screen instead; the receipts and the composer
        // are children and still win their own taps.
        .contentShape(Rectangle())
        .onTapGesture { typingFocused = false }
        // The menu lives on every tab now, so a model switch can happen while
        // this screen is not even on screen. It cannot be a closure any more.
        .onReceive(NotificationCenter.default.publisher(for: .lucyModelChanged)) { _ in
            brain.reload()
            turns = [ChatTurn(mine: false, text: Greetings.line())]
        }
        // Over everything, including the composer, because the point is to be
        // visible past the thumb that is causing it. `active` covers both
        // `.ready` and `.listening` -- the aura must still appear the instant
        // a finger lands, just in a visibly quieter state until `speech
        // .status` says the microphone is actually open; see `auraPhase`.
        .overlay(RecordingAura(face: face, level: micLevel,
                               active: auraPhase != .off,
                               ready: auraPhase == .ready))
        .preferredColorScheme(face.night ? .dark : .light)
        .task {
            guard !PreviewRoot.skipsPermissionPrompts else { return }
            await speech.prewarm()
        }
        .onAppear {
            guard turns.isEmpty else { return }
            // Said every time the thread opens, not once at install. Someone
            // downloads this at home and asks her something three weeks later
            // in the dust; a disclaimer they read in August is not one they
            // remember on playa, and an answer that is worse for a knowable
            // reason should say which reason.
            let greeting = ModelDownload.shared.usingSmallModel
                ? Greetings.line() + "\n\nI'm running light on this phone — "
                  + "the full me needs more memory than it has. Same records, "
                  + "blunter answers. Ask me plainly."
                : Greetings.line()
            turns = [ChatTurn(mine: false, text: greeting)]
            brain.load()
            // Past captures no longer open the thread. Replaying every photo
            // and voice memo above the greeting meant the conversation began
            // with a week of things you already knew, and you scrolled to
            // reach the part where you could ask something. Notes live in the
            // Note tab, where they are kept.
            for question in seed { ask(question) }
        }
    }

    // MARK: - Chrome

    private var bar: some View {
        HStack(spacing: 10) {
            // The same title treatment as every other tab (Snails, Kept,
            // Memory): display at 34, ground text colour.
            Text(Style.current.label("Lucy"))
                .font(.display(34))
                .foregroundStyle(face.text)
            Spacer()
            if let onClose {
                Button("Close", action: onClose)
                    .font(.body_(16)).foregroundStyle(face.muted)
            }
            LucyMenu(face: face)
        }
        .padding(.horizontal, 22).padding(.top, 8).padding(.bottom, 10)
    }

    // MARK: - Earlier

    /// Above the greeting, and only when there is something behind it.
    ///
    /// The thread still OPENS on a greeting and nothing else. Captures used to
    /// replay into it and were taken out because a week of things you already
    /// knew stood between you and the place you type; restoring the
    /// conversation automatically would be the same mistake wearing different
    /// clothes. So the history is one tap away rather than in the way, and it
    /// arrives a few turns at a time.
    @ViewBuilder
    private var earlierControl: some View {
        if restored < ChatLog.shared.count {
            Button {
                let older = ChatLog.shared.page(skipping: restored)
                guard !older.isEmpty else { return }
                restored += older.count
                turns.insert(contentsOf: older.flatMap(Self.bubbles), at: 0)
            } label: {
                Text(Style.current.label("Earlier"))
                    .font(.eyebrow(11))
                    .tracking(Style.current.eyebrowTracking)
                    .foregroundStyle(face.muted)
                    .padding(.horizontal, 14).padding(.vertical, 7)
                    .overlay(Capsule().stroke(face.muted.opacity(0.35), lineWidth: 1))
                    .frame(maxWidth: .infinity, alignment: .center)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .padding(.bottom, 4)
        }
    }

    /// A stored turn as the two bubbles it was.
    ///
    /// Facts, entity cards and documents are NOT restored: `ChatLog` keeps the
    /// words, not the rows behind them, and re-running retrieval against
    /// today's database could hang different evidence under an answer she gave
    /// on Tuesday. History shows what was said.
    ///
    /// `unanswered` is dropped for the same kind of reason. It drives the offer
    /// to be told, and an offer under a week-old gap invites answering a
    /// question nobody is asking any more.
    private static func bubbles(_ r: Remembered) -> [ChatTurn] {
        [ChatTurn(mine: true, text: r.question),
         ChatTurn(mine: false, text: r.answer)]
    }

    // MARK: - The conversation

    private var transcript: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 18) {
                    earlierControl
                    ForEach(turns) { turn in
                        bubble(turn).id(turn.id)
                    }
                    if thinking {
                        VStack(alignment: .leading, spacing: 6) {
                            // A straight swap, not an overlay: the moment real
                            // tokens exist the dots are gone, never sitting
                            // under or delaying the streaming text, which is
                            // the part actually worth seeing.
                            if showsThinkingDots(thinking: thinking, partial: brain.partial) {
                                ThinkingDots(face: face)
                            } else {
                                Text(brain.partial)
                                    .font(.body_(18))
                                    .foregroundStyle(face.text)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .id("thinking")
                    }
                    if case .listening = speech.status {
                        Text(speech.transcript.isEmpty ? "listening…" : speech.transcript)
                            .font(.body_(17))
                            .foregroundStyle(face.muted)
                            .frame(maxWidth: .infinity, alignment: .trailing)
                    } else if let missedCue {
                        // Quiet, in her voice, gone on its own -- not an
                        // alert or a banner. This is the too-short-press path
                        // (job 2): SpeechListener already wrote the sentence,
                        // this just stops it from being thrown away before
                        // anyone sees it.
                        Text(missedCue)
                            .font(.body_(17))
                            .foregroundStyle(face.muted)
                            .frame(maxWidth: .infinity, alignment: .trailing)
                            .transition(.opacity)
                    }
                }
                .padding(.horizontal, 22)
                .padding(.vertical, 10)
            }
            .scrollIndicators(.hidden)
            // Drag the thread down and the keyboard follows your thumb, the way
            // it does in Messages. `.interactively` rather than PEOPLE's
            // `.immediately`: there, scrolling means you are done typing and
            // reading results; here you often scroll back to re-read an answer
            // mid-question, and having the keyboard vanish under you loses your
            // place. This alone is not enough — a thread with one greeting in it
            // has nothing to scroll, hence the tap and the Done button too.
            .scrollDismissesKeyboard(.interactively)
            .onChange(of: turns.count) { _, _ in
                // Only when something arrived at the END. "Earlier" inserts at
                // the front, which grows the count without moving the last
                // turn -- and scrolling then would throw you to the bottom the
                // instant you asked to read further up.
                guard turns.last?.id != lastEnd else { return }
                lastEnd = turns.last?.id
                withAnimation(Style.current.arrival) {
                    proxy.scrollTo(turns.last?.id, anchor: .bottom)
                }
            }
        }
    }

    @ViewBuilder
    private func bubble(_ turn: ChatTurn) -> some View {
        if let marker = turn.marker {
            Text(Style.current.label(marker))
                .font(.eyebrow(11))
                .tracking(Style.current.eyebrowTracking)
                .foregroundStyle(face.muted)
                .frame(maxWidth: .infinity, alignment: .center)
                .padding(.vertical, 6)
        } else if turn.mine {
            // Yours is the same card as hers — raised ground, a coloured rule
            // on the outside edge — mirrored to the right and keyed teal
            // instead of her coral, so the two sides of the conversation share
            // one pattern and differ only in colour and side. The filled
            // bubble this replaces read as a third design on the screen, and
            // its accent fill made your words the same colour as the record
            // button. At night the rule uses night's existing keep teal and
            // the bright filled bubble goes away — strictly less light.
            Text(turn.text)
                .font(.body_(18))
                .foregroundStyle(face.text)
                .multilineTextAlignment(.leading)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 16).padding(.vertical, 14)
                .background(
                    RoundedRectangle(cornerRadius: 18)
                        .fill(face.raised)
                )
                .overlay(alignment: .trailing) {
                    RoundedRectangle(cornerRadius: 2)
                        .fill(face.keep)
                        .frame(width: 3)
                        .padding(.vertical, 14)
                }
                .frame(maxWidth: .infinity, alignment: .trailing)
                .padding(.leading, 44)
        } else {
            // Hers sits on `raised` rather than on the ground. The palette has
            // carried a raised colour from the beginning and the conversation
            // never used it, which is most of why this screen read as one flat
            // sheet with text on it.
            VStack(alignment: .leading, spacing: 10) {
                Text(turn.text)
                    .font(.body_(18))
                    .foregroundStyle(face.text)
                    .fixedSize(horizontal: false, vertical: true)

                // The receipts. Present on every factual reply, one tap away —
                // this is the difference between Lucy and a chatbot.
                ForEach(turn.facts) { fact in
                    receipt(fact)
                }
                ForEach(turn.camp) { fact in
                    sourceLine(fact.sourceTitle.isEmpty ? "camp records" : fact.sourceTitle,
                               detail: fact.year.map(String.init))
                }
                ForEach(turn.docs) { doc in
                    sourceLine(doc.title, detail: doc.year.map(String.init))
                }
                ForEach(uniqueSources(turn.general), id: \.self) { source in
                    sourceLine(source, detail: nil)
                }

                // Only after a refusal, and only on her own reply turns —
                // `turn.question` is nil on the greeting, so it can never show
                // up there even if a greeting happened to start with "I don't
                // know". An offer under every answer would train people to
                // ignore it, and the answers worth having are the ones
                // somebody gives while the gap is still annoying them.
                if let question = turn.question, turn.unanswered {
                    offer(for: turn, question: question)
                }
            }
            .padding(.horizontal, 16).padding(.vertical, 14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: 18)
                    .fill(face.raised)
            )
            .overlay(alignment: .leading) {
                // A thin warm edge, so an answer is hers at a glance from
                // across a dusty tent without reading a word of it. Warm, not
                // accent: the coral belongs to the record/ask actions, and her
                // voice gets the gold that carries the personality.
                RoundedRectangle(cornerRadius: 2)
                    .fill(face.warm)
                    .frame(width: 3)
                    .padding(.vertical, 14)
            }
            .padding(.trailing, 28)
        }
    }

    private func receipt(_ fact: EntityFact) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Button {
                withAnimation(Style.current.arrival) {
                    openFact = openFact == fact.id ? nil : fact.id
                }
            } label: {
                HStack(spacing: 6) {
                    Text(fact.assertedOn.flatMap(Self.shortDate) ?? "undated")
                        .font(.data(12)).foregroundStyle(face.muted)
                    Text("\(fact.evidence.count) source\(fact.evidence.count == 1 ? "" : "s")")
                        .font(.data(12)).foregroundStyle(face.warm)
                    Image(systemName: openFact == fact.id ? "chevron.up" : "chevron.down")
                        .font(.system(size: 10, weight: .bold))
                        .foregroundStyle(face.muted)
                }
            }
            .buttonStyle(.plain)

            if openFact == fact.id {
                ForEach(fact.evidence) { ev in
                    VStack(alignment: .leading, spacing: 3) {
                        Text(ev.quote)
                            .font(.body_(15))
                            .foregroundStyle(face.text)
                            .fixedSize(horizontal: false, vertical: true)
                        Text(ev.attribution)
                            .font(.data(11))
                            .foregroundStyle(face.muted)
                    }
                    .padding(.leading, 12)
                    .overlay(alignment: .leading) {
                        Rectangle().fill(face.warm).frame(width: 3)
                    }
                }
            }
        }
    }

    /// Where a non-chat claim came from. Same visual weight as a fact's
    /// receipt: every line Lucy says can be traced to something.
    private func sourceLine(_ title: String, detail: String?) -> some View {
        HStack(spacing: 6) {
            Image(systemName: "doc.text")
                .font(.system(size: 10, weight: .bold))
                .foregroundStyle(face.muted)
            Text(detail.map { "\(title) · \($0)" } ?? title)
                .font(.data(12))
                .foregroundStyle(face.muted)
                .lineLimit(1)
        }
    }

    /// The offer to be told, shown under a refusal. One of three states, kept
    /// per turn id in `tellingLucyFor` / `toldConfirmedFor` so several
    /// refusals in the same scrollback act independently.
    @ViewBuilder
    private func offer(for turn: ChatTurn, question: String) -> some View {
        if tellingLucyFor.contains(turn.id) {
            VStack(alignment: .leading, spacing: 8) {
                TextField("What's the answer?", text: toldAnswerBinding(for: turn.id), axis: .vertical)
                    .lineLimit(1...4)
                    .textFieldStyle(.plain)
                    .font(.body_(16))
                    .foregroundStyle(face.text)
                HStack {
                    Button("Tell her") {
                        let body = toldAnswerFor[turn.id] ?? ""
                        AnswerStore.shared.record(question: question, body: body)
                        toldAnswerFor[turn.id] = nil
                        tellingLucyFor.remove(turn.id)
                        toldConfirmedFor.insert(turn.id)
                    }
                    .disabled((toldAnswerFor[turn.id] ?? "")
                        .trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    Button("Not now") {
                        tellingLucyFor.remove(turn.id)
                        toldAnswerFor[turn.id] = nil
                    }
                }
                .font(.body_(15))
            }
        } else if toldConfirmedFor.contains(turn.id) {
            // What actually happened, not a thank-you. Two separate truths,
            // both stated: it works for this phone right away, and it only
            // reaches the rest of the camp after a Sync AND a human review --
            // review defaults on, so a Sync alone does not get it there. This
            // is the same mistake the Sync screen already made once ("stays
            // on your phone", retired the day chatlog upload shipped); the
            // fix there was changing the words to match reality, same as here.
            Text("She's got it, and she'll use it here right away. The camp "
                 + "sees it after you Sync, once someone's looked it over.")
                .font(.body_(15)).foregroundStyle(face.muted)
        } else {
            Button("Do you know?") { tellingLucyFor.insert(turn.id) }
                .font(.body_(15)).foregroundStyle(face.muted)
        }
    }

    private func toldAnswerBinding(for id: UUID) -> Binding<String> {
        Binding(get: { toldAnswerFor[id] ?? "" },
                set: { toldAnswerFor[id] = $0 })
    }

    private func uniqueSources(_ general: [GeneralFact]) -> [String] {
        var seen: [String] = []
        for f in general where !seen.contains(f.source) { seen.append(f.source) }
        return seen
    }

    private static func shortDate(_ iso: String) -> String? {
        let parser = DateFormatter(); parser.dateFormat = "yyyy-MM-dd"
        guard let d = parser.date(from: iso) else { return nil }
        let out = DateFormatter(); out.dateFormat = "d MMM yyyy"
        return out.string(from: d)
    }

    // MARK: - Composer

    private var composer: some View {
        HStack(spacing: 12) {
            // No shutter here any more. Noting something and asking something
            // are different acts and they have their own tabs now; a camera
            // button on every question was the cost of pretending otherwise.
            // The invitation is an overlay, not the TextField's own
            // placeholder: it sits centred and vanishes the moment the box is
            // focused, while typing stays left-aligned with the cursor at the
            // left edge as in any text field.
            TextField("", text: $typed, axis: .vertical)
                .font(.body_(24))
                .foregroundStyle(face.text)
                .focused($typingFocused)
                .submitLabel(.send)
                .onSubmit(sendTyped)
                .overlay {
                    if typed.isEmpty && !typingFocused {
                        Text(Style.current.isPlayful ? "Talk to me baby" : "Ask Lucy")
                            .font(.body_(24))
                            .foregroundStyle(face.muted)
                            .allowsHitTesting(false)
                    }
                }
                .padding(.horizontal, 16).padding(.vertical, 12)
                // As tall as the record button beside it, so the composer
                // reads as one bar rather than a small pill next to a big
                // circle. The field grows into the height before wrapping.
                .frame(minHeight: HoldToRecord.size)
                // The bubbles' radius, not a capsule: at this height a
                // capsule's 48pt ends read as a lozenge, and this needs to
                // read as a box you type into.
                .background(
                    RoundedRectangle(cornerRadius: 18, style: .continuous)
                        .fill(face.muted.opacity(0.14))
                )

            // One control, two jobs — the pattern every messaging app uses.
            // With text in the field it sends; empty, it is the mic you hold.
            // A multiline TextField swallows the return key (onSubmit never
            // fires when axis is .vertical), so without an explicit send
            // button there was no way to ask a typed question at all.
            if hasText {
                Button(action: sendTyped) {
                    ZStack {
                        Circle().fill(face.accent).frame(width: 52, height: 52)
                        Image(systemName: "arrow.up")
                            .font(.system(size: 22, weight: .bold))
                            .foregroundStyle(face.night ? face.ground : .white)
                    }
                }
                .buttonStyle(SquishButtonStyle())
                .transition(.scale.combined(with: .opacity))
                .accessibilityLabel("Send")
            } else {
                // The same control the NOTE tab uses. A cue IS fired here, on
                // purpose, on touch-down -- see `Sounds.ready()` below and
                // `Haptics.recordStart()` in HoldToRecord's own gesture. Both
                // fire before the microphone is confirmed live, deliberately:
                // the tone and the thump cover the measured ~0.3s
                // SpeechListener.start() takes to open the tap, so whoever
                // waits for either is speaking into a live microphone by the
                // time they start. What must NOT claim the mic is open that
                // early is the aura -- see `auraPhase` below, which only
                // reaches `.listening` once `speech.status` says so.
                HoldToRecord(
                    face: face,
                    level: micLevel,
                    recording: isRecording,
                    locked: locked,
                    onStart: {
                        holding = true
                        // A fresh press retires whatever the last one said.
                        missedCue = nil
                        // Immediately, not after the beep: a question is not
                        // kept, so the tone landing in the first moment costs
                        // nothing, and its ~0.3s covers the ~0.3s the engine
                        // takes to open the tap.
                        Sounds.ready()
                        Task { await speech.start() }
                    },
                    onStop: {
                        holding = false
                        locked = false
                        Sounds.end()
                        // `finish` waits for the recogniser's final result
                        // rather than the last partial, so the end of the
                        // question survives the button coming up.
                        Task {
                            let heard = SpeechOverrides.apply(to: await speech.finish())
                            // Read the status BEFORE reset() wipes it back to
                            // .idle. `finish()` (commit cd2f4cf) now sets
                            // `.failed` for a press too short to open the mic,
                            // instead of silently leaving a live session for
                            // the next press to trip over -- but reading it
                            // after reset() threw that state away before
                            // anything downstream could react to it.
                            let cue = missedPressMessage(heard: heard, status: speech.status)
                            speech.reset()
                            guard !heard.isEmpty else {
                                missedCue = cue
                                if cue != nil {
                                    Task {
                                        try? await Task.sleep(for: .seconds(2.2))
                                        // Only clear it if nothing newer has
                                        // replaced or retired it already.
                                        if missedCue == cue { missedCue = nil }
                                    }
                                }
                                return
                            }
                            missedCue = nil
                            ask(heard)
                        }
                    },
                    onLock: {
                        locked = true
                        Haptics.filed()
                    }
                )
                .transition(.scale.combined(with: .opacity))
            }
        }
        .padding(.horizontal, 18)
        .padding(.top, 8)
        .padding(.bottom, 10)
        .animation(Style.current.spring, value: hasText)
    }

    private var hasText: Bool {
        !typed.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private func sendTyped() {
        let q = typed.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty else { return }
        typed = ""
        typingFocused = false
        Haptics.shutter()
        ask(q)
    }

    // MARK: - Answering
    private func ask(_ question: String) {
        turns.append(ChatTurn(mine: true, text: question))

        // No keyword. "Remember when we lost the generator" is a question
        // about the camp's past, not a request to open a camera, and the
        // keyword could not tell the two apart. Noting lives in its own tab.
        // A follow-up carries almost no searchable words of its own -- "you do,
        // it's in the camp manual" reduced to "camp" and "manual" and lost the
        // subject entirely. The previous question is blended in when this one
        // is too thin to retrieve on.
        let recent = ChatLog.shared.recent()
        let answer = Retrieval.answer(question, store: store,
                                      previous: recent.last?.question)
        let reply = LucyVoice.reply(to: question, answer: answer)
        let recalled = ChatLog.shared.recall(matching: question)
        // A refusal replayed as history teaches her to refuse again. The
        // 2026-08-11 journal has a perfect transcript and a full fact sheet
        // answered "I don't know" three times running, because mis-heard
        // dead ends rode along as JUST NOW context under "do not repeat an
        // answer you already gave". Dead ends still count for follow-up
        // term blending above; they are just never offered back to the
        // model as things she said.
        // Normalised prefix match: she phrases refusals freely — "I don't
        // know", "I do not know" — and a missed variant re-opens the refusal
        // cascade (journal 15:12Z: a wall of "I do not know why..." rode
        // straight past the contracted-form check).
        func deadEnd(_ r: Remembered) -> Bool { wasGap(r) }
        // When the SAME question comes again, its previous answer is the
        // strongest thing in the prompt — the model copies it verbatim far
        // more reliably than it obeys "do not repeat" (five identical
        // generations in the 2026-08-11 journal, surviving a prompt change
        // and a seed change). Matching the exact string missed variants —
        // "What is an Oz hole" recalled against "What's an Oz hole" — so a
        // repeat is judged by content words: same subject, however phrased.
        func contentWords(_ s: String) -> Set<String> {
            let stop: Set<String> = ["what", "whats", "what's", "is", "an", "a",
                                     "the", "who", "how", "do", "does", "we",
                                     "where", "when", "why", "are", "our", "my"]
            return Set(s.lowercased()
                .components(separatedBy: CharacterSet.alphanumerics.inverted)
                .filter { $0.count > 1 && !stop.contains($0) })
        }
        let askedNow = contentWords(question)
        func nearAsk(_ r: Remembered) -> Bool {
            let then = contentWords(r.question)
            guard !then.isEmpty, !askedNow.isEmpty else { return false }
            return then == askedNow
                || then.isSubset(of: askedNow) || askedNow.isSubset(of: then)
        }
        // A detected repeat gets one instruction line instead of its old
        // answer: with identical facts and no history, the phrasing is
        // near-deterministic (two byte-identical four-sentence answers in
        // the 2026-08-11 journal even with a random seed) — recited quotes
        // leave the sampler nothing to vary. An instruction is not content;
        // she still cannot say anything the facts don't hold.
        let isRepeat = recent.contains(where: nearAsk) || recalled.contains(where: nearAsk)
        let context = LucyVoice.factSheet(answer)
            + LucyVoice.chatLogSection(recent: recent.filter { !deadEnd($0) && !nearAsk($0) },
                                       recalled: recalled.filter { !deadEnd($0) && !nearAsk($0) })
                .joined(separator: "\n")
            + (isRepeat
               ? "\n=== THEY HAVE ASKED THIS BEFORE — tell it again fresh, in "
                 + "different words, leading with a different detail ===\n"
               : "")

        // Gemma phrases the retrieved facts in her voice. The receipts come
        // from retrieval either way, so a claim cannot pick up a citation the
        // model invented for it. With no model on the device she falls back to
        // the composed reply, which is plainer but says the same things.
        thinking = true
        // The same file name the journal's "MODEL loaded" line records, for
        // the same reason: an unattributed answer cannot be argued about.
        // Recorded only when the model actually spoke; a template answer
        // carries no model.
        let modelFile = (LucyBrain.modelPath as NSString).lastPathComponent
        // Nothing is a dead end.
        //
        // When retrieval came back empty she used to be handed an empty fact
        // sheet and a rule saying to use only the facts, which produced one of
        // three flat sentences. Now she is handed different material instead:
        // real lore rows, picked at random, to talk with while admitting she
        // has nothing on what was actually asked. The claim stays row-backed,
        // so being funny here still cannot become inventing here.
        //
        // `unanswered` is the same condition, recorded rather than recovered
        // from her wording afterwards.
        let unanswered = answer.isEmpty
        let colour = unanswered
            ? store.colour(limit: 2)
                .map { "- \($0.title): \($0.story)" }
                .joined(separator: "\n")
            : nil
        let started = brain.answer(question: question,
                                   context: context,
                                   colour: colour) { spoken in
            thinking = false
            let text = spoken.isEmpty ? reply.text : spoken
            // Kept whether the model answered or the templates did: what she
            // said is what she has to remember saying.
            ChatLog.shared.record(question: question, answer: text,
                                  model: spoken.isEmpty ? nil : modelFile,
                                  unanswered: unanswered)
            // No receipts attached. They were four blocks of quoted source
            // under every answer, which is a citation apparatus for a
            // conversation — and it buried the answer. The grounding is
            // unchanged: retrieval still chooses the facts and Gemma still
            // only phrases them. What went away is the showing of the
            // working, which is still in the journal when something looks
            // wrong.
            turns.append(ChatTurn(mine: false, text: text, question: question,
                                  unanswered: unanswered))
        }
        if !started {
            thinking = false
            ChatLog.shared.record(question: question, answer: reply.text,
                                  unanswered: unanswered)
            turns.append(ChatTurn(mine: false, text: reply.text, question: question,
                                  unanswered: unanswered))
        }
    }
}
