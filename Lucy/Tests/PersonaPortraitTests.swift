import XCTest
@testable import Lucy

// A question about a person should be answered with what they are LIKE.
//
// `personality` has held voice, shows_up_as, cares_about and a signature
// quote since the first enrichment run. The portrait query selected none of
// the first two, so the two fields that describe a character rather than a
// job never reached the model at all -- and the fact sheet led with
// person_profile.summary, which is the administration paragraph the
// 2026-08-19 pass caught her reciting back as an answer.
@MainActor
final class PersonaPortraitTests: XCTestCase {

    private var store: EntityStore!

    override func setUpWithError() throws {
        store = EntityStore(path: PreviewRoot.fixturePath)
        try XCTSkipIf(store == nil, "no fixture database")
        Experiments.shared.gatedPortrait = true
    }

    override func tearDown() {
        Experiments.shared.gatedPortrait = true
    }

    private func portrait(_ name: String) throws -> PersonPortrait {
        try XCTUnwrap(store.portrait(terms: [name.lowercased()]),
                      "no portrait for \(name)")
    }

    func testTheCardCarriesHowTheyWrite() throws {
        // The only place the camp's tone survives enrichment. camp_fact holds
        // zero emoji across 1,745 rows; a voice line holds them.
        let piotr = try portrait("piotr")
        XCTAssertFalse(piotr.voice.isEmpty,
                       "voice is in the database and never reached the card")
    }

    func testTheCardCarriesHowTheyShowUp() throws {
        let piotr = try portrait("piotr")
        XCTAssertFalse(piotr.showsUpAs.isEmpty,
                       "shows_up_as is in the database and never reached the card")
    }

    func testCharacterLeadsAndTheJobTrails() throws {
        let piotr = try portrait("piotr")
        let sheet = LucyVoice.factSheet(
            Answer(hits: [], camp: [], docs: [], general: [], people: [],
                   about: piotr, lore: [], terms: ["piotr"]))
        let lines = sheet.split(separator: "\n").map(String.init)
        guard let header = lines.firstIndex(where: { $0.contains("ABOUT PIOTR") })
        else { return XCTFail("no person section on the sheet") }

        // Whatever comes first after the header must be a line about the
        // person, not their administrative summary.
        let first = lines[(header + 1)...].first { $0.hasPrefix("- ") }
        XCTAssertNotNil(first)
        if !piotr.role.isEmpty {
            XCTAssertNotEqual(first, "- \(piotr.role)",
                              "the admin biography is leading again")
        }
    }

    func testTheAdminSummaryIsNotShownWhenCharacterExists() throws {
        let piotr = try portrait("piotr")
        try XCTSkipIf(piotr.role.isEmpty, "no admin summary to suppress")
        let sheet = LucyVoice.factSheet(
            Answer(hits: [], camp: [], docs: [], general: [], people: [],
                   about: piotr, lore: [], terms: ["piotr"]))
        XCTAssertFalse(sheet.contains(piotr.role),
                       "the administration paragraph is on the sheet even "
                       + "though the personality rows describe him")
    }

    func testTheHeaderPromisesCharacterNotAJobDescription() throws {
        let piotr = try portrait("piotr")
        let sheet = LucyVoice.factSheet(
            Answer(hits: [], camp: [], docs: [], general: [], people: [],
                   about: piotr, lore: [], terms: ["piotr"]))
        XCTAssertTrue(sheet.contains("what they are like"),
                      "the section still promises something else")
    }

    // MARK: - The stories are the person

    func testTheCardCarriesTheStoriesTheyAreIn() throws {
        // All 163 lore rows name their cast, and none of it reached this card:
        // lore was only ever found by matching words in the question, so "who
        // is Piotr" got his summary and never "Piotr's Red Sweater".
        let piotr = try portrait("piotr")
        XCTAssertFalse(piotr.stories.isEmpty,
                       "no lore reached the person card")
    }

    func testAStoryListMatchesWholeNamesOnly() {
        // lore.people is comma separated, so a LIKE '%Ed%' would hand Ed the
        // stories belonging to Eden and Edd. Whole entries, or a first name
        // against a full one.
        let eden = store.storiesAbout("Eden")
        let edd = store.storiesAbout("Edd")
        XCTAssertNotEqual(Set(eden), Set(edd),
                          "two different people got the same stories")
    }

    func testNobodyGetsStoriesFromAnEmptyName() {
        XCTAssertTrue(store.storiesAbout("").isEmpty)
    }

    func testTheStoryListIsCapped() {
        // A well-known camper appears in a great many. This section is colour
        // on a card, not the camp's whole history.
        for name in ["Piotr", "Oz", "Marcus"] {
            XCTAssertLessThanOrEqual(store.storiesAbout(name).count, 3,
                                     "\(name)'s story list is unbounded")
        }
    }

    func testTheCardCarriesTheShortIdentifier() throws {
        // person_profile.known_for is one line where `role` is four
        // sentences saying the same thing.
        let piotr = try portrait("piotr")
        XCTAssertFalse(piotr.knownFor.isEmpty)
        XCTAssertLessThan(piotr.knownFor.count, piotr.role.count,
                          "known_for should be the short one")
    }

    func testTheCardSaysWhatToAskThemAbout() throws {
        let piotr = try portrait("piotr")
        XCTAssertFalse(piotr.expertise.isEmpty)
        XCTAssertLessThanOrEqual(piotr.expertise.count, 3)
    }

    func testTheGateStillHoldsForAQuestionAboutSomethingElse() {
        // None of this reopens the bug the gate closed: "why does Piotr not
        // like water" must still not get a character sketch instead of an
        // answer.
        XCTAssertNil(store.portrait(terms: ["piotr", "water"]),
                     "a question about water got the person card back")
    }
}
