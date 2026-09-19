import XCTest
@testable import Lucy

// Did E7 change anything, on the questions actually asked?
//
// "I can't say that I noticed much of a difference" is the only verdict that
// matters, and it is not answerable by asserting that the machinery works.
// These are the 19 real questions from Documents/lucy-journal.txt asked after
// the encoder loaded on the owner's phone. Each is run through retrieval
// twice -- once as it would have been before E7, once with the question's
// vector -- and the fact sheets are compared.
//
// This prints rather than only asserting. The number worth knowing is how
// many of nineteen real questions got different material, and no assertion
// carries that.
@MainActor
final class E7ImpactTests: XCTestCase {

    private var store: EntityStore!

    static let asked = [
        "What's an Oz hole",
        "Does Piotr like water",
        "Who are our djs",
        "What's a JUUL",
        "Why do people make fun of Ed",
        "Do people make fun of Ed",
        "Do we cook our food",
        "Lucy originally come from",
        "What kind of sound system do you carry",
        "What are some camp stories",
        "Some preservation Society camp stories",
        "What's the camp placed in 2024",
        "The story about Holmar",
        "The story about Seth Sander",
        "how do we get water delivered",
    ]

    override func setUpWithError() throws {
        try XCTSkipIf(Embedder.shared == nil, "no encoder in Documents")
        store = EntityStore(path: PreviewRoot.fixturePath)
        try XCTSkipIf(store == nil, "no fixture database")
    }

    /// Where does signal stop and noise start? Prints the top semantic
    /// matches with their cosines, for every real question, so the floor can
    /// be set from data instead of chosen.
    func testPrintSimilarityDistributionForCalibration() {
        print("\n===== SEMANTIC MATCHES BY COSINE =====")
        for question in Self.asked {
            guard let q = Embedder.shared?.embed(question) else { continue }
            let near = store.similarClaims(table: "camp_fact", to: q,
                                           limit: 8, floor: -1)
            print("\nQ: \(question)")
            for (id, cos) in near.sorted(by: { $0.value > $1.value }) {
                guard let row = store.campFact(id: id) else { continue }
                print(String(format: "   %.3f  %@: %@", cos, row.topic,
                             String(row.fact.prefix(72))))
            }
        }
        print("===== END =====\n")
    }

    func testHowManyRealQuestionsGetDifferentMaterial() {
        var changed = 0
        var report: [String] = []

        for question in Self.asked {
            let terms = Retrieval.terms(question)
            let before = store.searchCampFacts(terms: terms)
            let loreBefore = store.searchLore(terms: terms, limit: 3)

            guard let q = Embedder.shared?.embed(question) else {
                report.append("\(question) — ENCODE FAILED")
                continue
            }
            let after = store.searchCampFacts(terms: terms, queryVector: q)
            let loreAfter = store.searchLore(terms: terms, queryVector: q, limit: 3)

            let addedFacts = Set(after.map(\.id)).subtracting(before.map(\.id))
            let addedLore = Set(loreAfter.map(\.id)).subtracting(loreBefore.map(\.id))
            if addedFacts.isEmpty && addedLore.isEmpty {
                report.append("· \(question) — no change "
                              + "(\(before.count) facts, \(loreBefore.count) lore)")
                continue
            }
            changed += 1
            report.append("✓ \(question) — +\(addedFacts.count) facts, "
                          + "+\(addedLore.count) lore")
            for id in addedFacts.sorted() {
                if let row = store.campFact(id: id) {
                    report.append("      fact: \(row.topic): "
                                  + String(row.fact.prefix(90)))
                }
            }
            for id in addedLore.sorted() {
                if let row = store.loreHit(id: id) {
                    report.append("      lore: \(row.title)")
                }
            }
        }

        print("\n===== E7 IMPACT ON \(Self.asked.count) REAL QUESTIONS =====")
        report.forEach { print($0) }
        print("===== \(changed) of \(Self.asked.count) got different material =====\n")

        XCTAssertGreaterThan(changed, 0,
                             "E7 changed nothing on any real question")
    }
}
