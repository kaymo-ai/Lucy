import XCTest
@testable import Lucy

// The encoder actually running.
//
// Everything else about E7 is testable without the model, and is tested that
// way. This is the part that is not: whether `Embedder.embed` sets up the
// batch correctly, whether mean pooling produces anything at all, and whether
// what comes out lands in the same space as the vectors the pipeline wrote.
// Those fail silently -- a null return means "no semantic candidates", which
// is indistinguishable from "nothing was close" -- so they need a real
// encoder to check.
//
// Skipped when the model is not in the simulator's Documents, so the ordinary
// suite stays runnable without a 318 MB file. To run these:
//
//   xcrun simctl get_app_container <sim> ai.kaymo.Lucy.dev data
//   cp scripts/models/embeddinggemma-300M-Q8_0.gguf <that>/Documents/
@MainActor
final class EmbedderLiveTests: XCTestCase {

    private var embedder: Embedder!
    private var store: EntityStore!

    override func setUpWithError() throws {
        try XCTSkipIf(Embedder.shared == nil,
                      "no encoder in Documents — see the comment above")
        embedder = Embedder.shared
        store = EntityStore(path: PreviewRoot.fixturePath)
        try XCTSkipIf(store == nil, "no fixture database")
    }

    func testAQuestionEncodesToAUnitVector() {
        guard let v = embedder.embed("how do we get water delivered") else {
            return XCTFail("embed returned nil — the batch or pooling is wrong")
        }
        XCTAssertEqual(v.count, Embedder.dimensions)
        XCTAssertEqual(dot(v, v), 1.0, accuracy: 0.001, "not unit length")
        XCTAssertFalse(v.allSatisfy { $0 == 0 }, "an all-zero vector is a dead encoder")
    }

    func testTwoDifferentQuestionsDoNotEncodeIdentically() {
        // A pooling failure that returns the same vector for everything would
        // pass the test above and rank every row identically forever.
        let a = embedder.embed("where do we store the bikes")
        let b = embedder.embed("who cooks dinner")
        XCTAssertNotNil(a); XCTAssertNotNil(b)
        XCTAssertLessThan(dot(a!, b!), 0.99,
                          "two unrelated questions encoded the same")
    }

    func testTheQuestionLandsInTheSameSpaceAsTheShippedVectors() {
        // The real risk: the phone encodes into a space the corpus is not in,
        // because of a different prefix, pooling or normalisation. Cosine
        // would still return numbers and ranking would still happen. If the
        // two sides agree, a question about water is far closer to the water
        // rows than to the median row.
        guard let q = embedder.embed("how do we get water delivered") else {
            return XCTFail("embed returned nil")
        }
        let near = store.similarClaims(table: "camp_fact", to: q,
                                       limit: 2000, floor: -1)
        XCTAssertGreaterThan(near.count, 100, "expected the whole table scored")

        let best = near.max { $0.value < $1.value }!
        let values = near.values.sorted()
        let median = values[values.count / 2]
        XCTAssertGreaterThan(best.value, 0.40,
                             "nothing in the camp's own records came close to a "
                             + "question about water — the two sides disagree")
        XCTAssertGreaterThan(best.value - median, 0.20,
                             "no separation between the best row and the median; "
                             + "the encoding conventions do not match")
    }

    func testSemanticRecallReachesTheRowLexicalCannotWithARealQuestion() {
        // The end of the thread that started in learnings 2026-08-09: the
        // whole point of E7, run through the real encoder against the shipped
        // database rather than with a stored vector standing in for a query.
        let question = "how do we get water delivered"
        let terms = Retrieval.terms(question)
        let lexical = store.searchCampFacts(terms: terms).map(\.id)
        guard let q = embedder.embed(question) else { return XCTFail("embed nil") }
        let hybrid = store.searchCampFacts(terms: terms, queryVector: q).map(\.id)

        XCTAssertGreaterThan(hybrid.count, lexical.count,
                             "semantic recall added nothing at all")
        let added = Set(hybrid).subtracting(lexical)
        XCTAssertFalse(added.isEmpty)
        // Whatever it added must actually be about water, or the floor is too
        // low and this is noise rather than recall.
        for id in added {
            guard let row = store.campFact(id: id) else { continue }
            let text = (row.topic + " " + row.fact).lowercased()
            XCTAssertTrue(text.contains("water") || text.contains("voucher")
                          || text.contains("service") || text.contains("supply"),
                          "semantic recall added an unrelated row: \(row.fact)")
        }
    }
}
