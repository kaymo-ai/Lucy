import XCTest
@testable import Lucy

@MainActor
final class AnswerStoreTests: XCTestCase {
    override func setUp() async throws {
        AnswerStore.shared.forget()
    }

    func testAnAnswerYouTypedIsFoundImmediately() {
        // Offline, unreviewed, yours. No sync involved.
        AnswerStore.shared.record(question: "where is the medkit",
                                  body: "In Doris, left side by the door.")
        // "medkit" appears in the QUESTION and nowhere in the body. This is
        // the case that matters: the words someone searches with are the words
        // they asked with, and the answer shares none of them.
        let hits = AnswerStore.shared.search(terms: ["medkit"])
        XCTAssertEqual(hits.count, 1)
        XCTAssertEqual(hits.first?.fact, "In Doris, left side by the door.")
        // The question is the topic, never the fact. She states the answer.
        XCTAssertEqual(hits.first?.topic, "where is the medkit")
    }

    func testTheQuestionIsNeverReturnedAsTheFact() {
        AnswerStore.shared.record(question: "where is the medkit", body: "In Doris.")
        XCTAssertEqual(AnswerStore.shared.search(terms: ["medkit"]).first?.fact,
                       "In Doris.")
    }

    func testAnUnrelatedQuestionFindsNothing() {
        AnswerStore.shared.record(question: "where is the medkit", body: "In Doris.")
        XCTAssertTrue(AnswerStore.shared.search(terms: ["generator"]).isEmpty)
    }

    func testSearchIsWholeWordNotSubstring() {
        // "water" matching "floodwaters" pulled a Noah's Ark party into an
        // answer about drinking water once. Same rule here.
        AnswerStore.shared.record(question: "floodwaters", body: "Not drinking water.")
        XCTAssertTrue(AnswerStore.shared.search(terms: ["water"]).isEmpty)
    }

    func testAnAnswerCarriesWhereItCameFrom() {
        // LucyVoice cites sourceTitle. A camp answer must not read as if it
        // came out of the shipped corpus.
        AnswerStore.shared.record(question: "where is the medkit", body: "In Doris.")
        XCTAssertEqual(AnswerStore.shared.search(terms: ["doris"]).first?.sourceTitle,
                       "answered by the camp")
    }

    func testYourOwnAnswerIsPendingUploadUntilTheServerAcks() {
        // An explicit device, so this never reaches the keychain.
        let device = UUID(uuidString: "11111111-2222-3333-4444-555555555555")!
        AnswerStore.shared.record(question: "q", body: "b")
        let pending = AnswerStore.shared.pendingUpload(device: device)
        XCTAssertEqual(pending.count, 1)
        AnswerStore.shared.markUploaded(rowids: pending.map(\.rowid))
        XCTAssertTrue(AnswerStore.shared.pendingUpload().isEmpty)
    }

    func testAnAnswerFromTheCampIsNeverQueuedForUpload() {
        // It came down. Sending it back up would be a loop.
        let device = UUID(uuidString: "11111111-2222-3333-4444-555555555555")!
        AnswerStore.shared.store(approved: [
            CampAnswer(id: UUID(), gapKey: "k", question: "where is the medkit",
                       body: "In Doris, left side.")])
        XCTAssertTrue(AnswerStore.shared.pendingUpload(device: device).isEmpty)
        // Still searchable. Coming down from camp is what makes it a fact for
        // everyone -- only the upload queue is meant to be empty.
        XCTAssertEqual(AnswerStore.shared.search(terms: ["medkit"]).count, 1)
    }

    func testTheSameApprovedAnswerTwiceIsStoredOnce() {
        // Two syncs, one answer. Otherwise Lucy states it twice in one breath.
        let a = CampAnswer(id: UUID(), gapKey: "k", question: "where is the medkit",
                           body: "In Doris.")
        AnswerStore.shared.store(approved: [a])
        AnswerStore.shared.store(approved: [a])
        XCTAssertEqual(AnswerStore.shared.search(terms: ["medkit"]).count, 1)
        XCTAssertEqual(AnswerStore.shared.search(terms: ["doris"]).count, 1)
    }
}

final class DeadEndTests: XCTestCase {
    // These phrases must stay in step with lucy_is_refusal() in
    // backend/migrations/005_refusal_shapes.sql. A phrase that is a refusal
    // here and not there is a gap the camp never gets asked about.
    func testTheThreeRefusalShapes() {
        XCTAssertTrue(wasGapByWording("I don't know where that is"))
        XCTAssertTrue(wasGapByWording("Nothing in what I've got says so"))
        XCTAssertTrue(wasGapByWording("That's not something I've been told"))
    }

    func testUncontractedAndCurlyFormsCount() {
        // A wall of "I do not know why..." rode straight past the contracted
        // check once. Journal 15:12Z.
        XCTAssertTrue(wasGapByWording("I do not know why that is"))
        XCTAssertTrue(wasGapByWording("I don\u{2019}t know where that is"))
    }

    func testARealAnswerIsNotADeadEnd() {
        XCTAssertFalse(wasGapByWording("The barrels are in Doris, left side."))
    }

    // 2026-08-19: measured against 99 real answered turns from the device's
    // own journal, the rule above caught 33 of 99. These four shapes cover
    // the two defects that explain the miss: leading filler in front of a
    // known phrase (hasPrefix vs. contains), and three refusal shapes the
    // list never had at all.
    func testLeadingFillerNoLongerHidesAKnownPhrase() {
        XCTAssertTrue(wasGapByWording("Well, honestly, I don't know where that is"))
    }

    func testTheThreeMissingRefusalShapes() {
        XCTAssertTrue(wasGapByWording("I don't have any facts about that"))
        XCTAssertTrue(wasGapByWording("I don't have a fact that says so"))
        XCTAssertTrue(wasGapByWording("I can only say what the facts state"))
    }

    // Verbatim from Documents/lucy-journal.txt on the owner's phone -- five
    // genuine refusals the old hasPrefix, three-phrase rule missed. This is
    // the evidence the bug existed, not invented test data.
    func testTheFiveRefusalsMissedOnTheDevice() {
        XCTAssertTrue(wasGapByWording("OK ho. I don't know what a K is."))
        XCTAssertTrue(wasGapByWording(
            "I do not have any facts about why anyone might be an idiot."))
        XCTAssertTrue(wasGapByWording(
            "I do not have any facts about Sammy being sheep."))
        XCTAssertTrue(wasGapByWording(
            "I do not have a fact that says that is not helpful."))
        XCTAssertTrue(wasGapByWording(
            "I can only say what the facts state. The facts provided do not "
            + "contain information about why it is so hot at Burning Man."))
    }

    // MARK: - The recorded fact beats the wording

    // `wasGapByWording` is now only a decoder for turns written before the
    // column existed. Anything answered since carries the real answer, and it
    // has to win -- including when the two disagree, which they will: an
    // empty-handed reply is meant to be a joke about the camp now, and a joke
    // does not contain any of the phrases above.
    func testRecordedGapBeatsWordingThatLooksLikeAnAnswer() {
        let joke = Remembered(
            askedAt: Date(), question: "who is the best DJ",
            answer: "Not written down anywhere, though the camp did once burn "
                  + "a yurt rather than pack it.",
            unanswered: true)
        XCTAssertFalse(wasGapByWording(joke.answer),
                       "the wording alone cannot tell this was a gap")
        XCTAssertTrue(wasGap(joke), "the recorded fact must win")
    }

    func testRecordedAnswerBeatsWordingThatLooksLikeARefusal() {
        // She can say "I don't know" inside a real answer -- quoting someone,
        // or naming which part she is unsure of. Retrieval found rows, so it
        // is not a gap, and the camp must not be asked to fill it.
        let real = Remembered(
            askedAt: Date(), question: "when does the truck leave",
            answer: "Nobody wrote a time down, so I don't know the hour, but "
                  + "the load-out is Wednesday.",
            unanswered: false)
        XCTAssertTrue(wasGapByWording(real.answer),
                      "the wording alone would call this a refusal")
        XCTAssertFalse(wasGap(real), "the recorded fact must win")
    }

    func testTurnsFromBeforeTheColumnFallBackToTheirWording() {
        let old = Remembered(askedAt: Date(), question: "where is the medkit",
                             answer: "I don't know that one.", unanswered: nil)
        XCTAssertTrue(wasGap(old))
        let older = Remembered(askedAt: Date(), question: "where is Doris",
                               answer: "In the Monument warehouse.", unanswered: nil)
        XCTAssertFalse(wasGap(older))
    }
}

@MainActor
final class RetrievalAnswerTests: XCTestCase {
    override func setUp() async throws { AnswerStore.shared.forget() }

    // The brief's tests were written against `EntityStore.shared`, which does
    // not exist -- every real call site (PreviewApp, AppFlow, BuildInfo,
    // Vocabulary) builds one with `EntityStore(path: PreviewRoot.fixturePath)`,
    // the failable initializer over the bundled `enriched_preview.db`. LucyTests
    // is TEST_HOST-ed inside Lucy.app (see project.yml), so Bundle.main inside
    // the test process resolves to the app bundle and the fixture loads.
    // XCTUnwrap rather than `!` so a fixture miss reads as "did not load", not
    // a bare crash.
    //
    // A fresh EntityStore per call, not one held across a test: holding a
    // single connection across `testNothingAnsweredChangesNothing`'s two
    // calls was tried and made the second call return zero rows -- worse,
    // not better, than opening fresh each time. Reverted; see the task
    // report for what was actually observed.
    private func store() throws -> EntityStore {
        try XCTUnwrap(EntityStore(path: PreviewRoot.fixturePath))
    }

    func testAnAnsweredGapReachesRetrieval() throws {
        AnswerStore.shared.record(question: "where is the medkit",
                                  body: "In Doris, left side by the door.")
        let result = Retrieval.answer("where is the medkit", store: try store())
        XCTAssertTrue(result.camp.contains { $0.fact.contains("left side by the door") })
    }

    func testACampAnswerSortsAheadOfShippedFacts() throws {
        // The camp corrected her on purpose. A shipped row that was already
        // losing should not now outrank the correction. "medkit" is not a
        // literal word in the shipped corpus, but the build-time ask_word
        // vocabulary maps it onto several first-aid camp_fact rows, so this
        // query genuinely has shipped competition to outrank -- it is not
        // testing against an empty shipped side.
        AnswerStore.shared.record(question: "where is the medkit", body: "Moved to Bertha.")
        let result = Retrieval.answer("where is the medkit", store: try store())
        XCTAssertEqual(result.camp.first?.sourceTitle, "answered by the camp")
        // If the shipped side happened to be empty, the assertion above would
        // pass vacuously -- the camp answer would "sort ahead" of nothing.
        // Require an actual shipped fact to be present, just ranked behind.
        XCTAssertTrue(result.camp.contains { $0.sourceTitle != "answered by the camp" },
                      "no shipped camp fact matched \"medkit\" -- the ordering assertion above proves nothing")
    }

    func testNothingAnsweredChangesNothing() throws {
        let before = Retrieval.answer("where is doris", store: try store())
        AnswerStore.shared.record(question: "unrelated", body: "unrelated body")
        let after = Retrieval.answer("where is doris", store: try store())
        XCTAssertEqual(before.camp.count, after.camp.count)
    }

    func testCampAnswersCannotShareAnIdWithShippedFacts() throws {
        // Identifiable + ForEach means a duplicate id renders one row wrong or
        // not at all. `forget()` is `DELETE FROM answer`, which does not reset
        // SQLite's autoincrement high-water mark, so the answer's rowid is not
        // reliably 1 here -- checking the id spaces directly, rather than
        // hoping for a literal collision with camp_knowledge row 1, is what
        // actually proves the negation in AnswerStore.search is doing its job.
        AnswerStore.shared.record(question: "where is the medkit", body: "Moved to Bertha.")
        let camp = Retrieval.answer("where is the medkit", store: try store()).camp
        let answered = camp.filter { $0.sourceTitle == "answered by the camp" }
        let shipped = camp.filter { $0.sourceTitle != "answered by the camp" }
        XCTAssertFalse(answered.isEmpty, "no camp answer in the result -- nothing to check the id space of")
        XCTAssertFalse(shipped.isEmpty, "no shipped fact in the result -- collision has nothing to collide with")
        XCTAssertTrue(answered.allSatisfy { $0.id < 0 },
                      "a camp answer's id was not negated")
        XCTAssertEqual(Set(camp.map(\.id)).count, camp.count,
                       "two facts share an id; ForEach(turn.camp) will drop one")
    }
}

/// The Piotr/water bug: `PeopleStore.portrait(terms:)` used to return a
/// person's whole card the moment any question term named them, whether the
/// question was "who is Piotr" or "why does Piotr not like water". These
/// pin the gate against the owner's own real journal questions -- see the
/// task report for the vacuity check (what was neutered, what broke).
@MainActor
final class PortraitGateTests: XCTestCase {
    override func setUp() async throws {
        Experiments.shared.gatedPortrait = true
    }

    override func tearDown() async throws {
        Experiments.shared.gatedPortrait = true
    }

    // Same reasoning as RetrievalAnswerTests.store(): a fresh EntityStore per
    // call, opened against the bundled fixture.
    private func store() throws -> EntityStore {
        try XCTUnwrap(EntityStore(path: PreviewRoot.fixturePath))
    }

    func testWhoIsJayGivesTheFullCard() throws {
        let portrait = try XCTUnwrap(
            store().portrait(terms: Retrieval.terms("Who is Jay")))
        XCTAssertFalse(portrait.persona.isEmpty,
                       "fixture has no persona for Jay -- full and no-portrait would look identical")
    }

    func testWhoIsOzGivesTheFullCard() throws {
        // "oz" now survives Retrieval.terms() in its own right (Finding 2
        // below), so this goes through the real tokenizer rather than a
        // hand-built term list.
        let portrait = try XCTUnwrap(
            store().portrait(terms: Retrieval.terms("Who is Oz")))
        XCTAssertFalse(portrait.persona.isEmpty)
    }

    func testWhyDoesPiotrNotLikeWaterHasNoPortrait() throws {
        // `role` IS the biography she was reciting -- for Piotr it is the
        // "manages much of our administration and logistics..." paragraph
        // verbatim, and her bad phone answer was a near-verbatim paraphrase
        // of exactly that sentence. So a leftover question gets nothing from
        // this table, not a shorter version of the same paragraph.
        let portrait = try store().portrait(terms: Retrieval.terms("Why does Piotr not like water"))
        XCTAssertNil(portrait)
    }

    func testWhyDoPeopleMakeFunOfEddHasNoPortrait() throws {
        let portrait = try store().portrait(terms: Retrieval.terms("Why do people make fun of Edd"))
        XCTAssertNil(portrait)
    }

    func testIsOzCrazyHasNoPortrait() throws {
        let portrait = try store().portrait(terms: Retrieval.terms("Is Oz crazy"))
        XCTAssertNil(portrait)
    }

    func testDoMarcusAndPiotrGetOnStaysFull() throws {
        // The device journal's real example named "Sammy", who is not on
        // this fixture's roster or in its chat. Piotr substitutes: he exists
        // in enriched_preview.db AND has a relationship row to Marcus, so
        // the pair exception has actual pairLines to prove it kept, not an
        // empty list that would pass by accident.
        let portrait = try XCTUnwrap(
            store().portrait(terms: Retrieval.terms("Do Marcus and Piotr like each other")))
        XCTAssertFalse(portrait.pair.isEmpty,
                       "no pairLines came back -- the pair exception proves nothing without them")
    }

    func testFlagOffReturnsFullEvenWithLeftoverTerms() throws {
        Experiments.shared.gatedPortrait = false
        let portrait = try XCTUnwrap(
            store().portrait(terms: Retrieval.terms("Why does Piotr not like water")))
        XCTAssertFalse(portrait.persona.isEmpty)
    }
}

/// Finding 2: `Retrieval.terms` dropped every token two letters or shorter,
/// which reads as a harmless length heuristic and is not one -- "Oz" is two
/// letters, so every question that named him produced zero search terms.
/// Measured against the owner's real journal: six of 71 questions, "Who is
/// Oz" among them. A stoplist is the right mechanism for a word that carries
/// no signal; length is not, because it cannot tell a name from noise.
final class RetrievalTermsTests: XCTestCase {
    func testWhoIsOzKeepsOzAsATerm() {
        XCTAssertTrue(Retrieval.terms("Who is Oz").contains("oz"))
    }

    func testWhatsUpDropsUpAsNoise() {
        XCTAssertFalse(Retrieval.terms("What's up").contains("up"))
    }

    func testTheBestDJInCampKeepsDJAsATerm() {
        XCTAssertTrue(Retrieval.terms("The best DJ in camp").contains("dj"))
    }
}
