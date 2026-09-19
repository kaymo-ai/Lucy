import XCTest
@testable import Lucy

// E7 on the read side, tested without the encoder.
//
// The 333 MB encoder is not in the test bundle and should not be: what can go
// wrong here is reading 3,072 bytes back as 768 floats, normalising them,
// and comparing them, and none of that needs a model. A vector already in the
// database is a perfectly good query vector -- and a better one for testing,
// because its own row must come back at cosine 1 and anything else is a bug in
// the arithmetic rather than an opinion about relevance.
@MainActor
final class SemanticRecallTests: XCTestCase {

    private var store: EntityStore!

    override func setUpWithError() throws {
        store = EntityStore(path: PreviewRoot.fixturePath)
        try XCTSkipIf(store == nil, "no fixture database")
        waterRow = store.campFactID(matching: "%service voucher%")
            ?? store.campFactID(matching: "%water%")
        try XCTSkipUnless(waterRow != nil, "no water fact in this database")
        try XCTSkipIf(storedVector(waterRow!) == nil,
                      "database predates claim_embedding; run embed_claims.py")
    }

    /// A camp fact about water, found by its TEXT rather than by an id.
    ///
    /// This test used to hardcode row 48. camp_fact is rebuilt from scratch
    /// on every ingest and the ids are autoincrements, so 48 was the
    /// service-vouchers row on one build and a note about a waste driver on
    /// the next -- the test failed on a rebuild that had broken nothing.
    /// A test pinned to an id is pinned to a build.
    private var waterRow: Int64?

    /// One stored vector, straight out of the shipped database.
    private func storedVector(_ id: Int64) -> [Float]? {
        store.rawClaimVector(table: "camp_fact", id: id)
    }

    // MARK: - The arithmetic

    func testARowIsItsOwnNearestNeighbour() {
        guard let id = waterRow, let v = storedVector(id) else {
            return XCTFail("no water row")
        }
        let near = store.similarClaims(table: "camp_fact", to: normalise(v),
                                       limit: 5, floor: 0.0)
        XCTAssertEqual(near.keys.sorted().isEmpty, false)
        let best = near.max { $0.value < $1.value }
        XCTAssertEqual(best?.key, id, "a vector must be closest to itself")
        XCTAssertEqual(best?.value ?? 0, 1.0, accuracy: 0.001,
                       "cosine with itself is 1; anything else is a packing "
                       + "or normalisation bug")
    }

    func testTheFloorActuallyExcludes() {
        guard let id = waterRow, let v = storedVector(id) else {
            return XCTFail("no water row")
        }
        let all = store.similarClaims(table: "camp_fact", to: normalise(v),
                                      limit: 2000, floor: -1)
        let strict = store.similarClaims(table: "camp_fact", to: normalise(v),
                                         limit: 2000, floor: 0.99)
        XCTAssertGreaterThan(all.count, strict.count,
                             "a floor that excludes nothing is not a floor")
        XCTAssertEqual(strict.count, 1, "only the row itself clears 0.99")
    }

    func testVectorsFromAnotherEncoderAreNotUsed() {
        // Cosine between vectors from different models is just a number --
        // the failure package_db.py was written about. The read side filters
        // on the model name the pipeline wrote, so a table full of vectors
        // from something else returns nothing rather than nonsense.
        guard let id = waterRow, let v = storedVector(id) else {
            return XCTFail("no water row")
        }
        XCTAssertFalse(store.similarClaims(table: "camp_fact", to: normalise(v),
                                           limit: 5, floor: 0.0).isEmpty)
        XCTAssertTrue(store.similarClaims(table: "not_a_table", to: normalise(v),
                                          limit: 5, floor: 0.0).isEmpty)
    }

    func testEveryShippedVectorIsTheRightSize() {
        // A short or long BLOB is skipped rather than read past its end. If
        // the pipeline ever wrote a different dimension this is where it
        // surfaces, instead of as quietly worse answers.
        XCTAssertEqual(store.claimVectorCount(bytes: Embedder.dimensions * 4),
                       store.claimVectorCount(bytes: nil),
                       "some shipped vector is not \(Embedder.dimensions) floats")
    }

    // MARK: - Recall reaches what words cannot

    func testSemanticRecallReachesARowWithNoSharedWords() {
        // The case from docs/learnings-lucy-2026-08-09.md, run against the
        // shipped database. The water row shares no content word with a
        // question about water being DELIVERED, so lexical retrieval cannot
        // reach it and semantic retrieval must.
        guard let id = waterRow, let v = storedVector(id) else {
            return XCTFail("no water row")
        }
        let lexicalOnly = store.searchCampFacts(terms: ["delivered"])
        XCTAssertFalse(lexicalOnly.contains { $0.id == id },
                       "if words alone already reached it, this test proves nothing")

        let withVectors = store.searchCampFacts(terms: ["delivered"],
                                                queryVector: normalise(v))
        XCTAssertTrue(withVectors.contains { $0.id == id },
                      "semantic recall did not reach the row it exists for")
    }

    func testSimilarityIsCarriedOntoTheRow() {
        // The fact sheet ranks sections on this. A row admitted semantically
        // but reported with similarity 0 would be ranked as if it had matched
        // nothing, which is the bug that would make E7 look like a no-op.
        guard let id = waterRow, let v = storedVector(id) else {
            return XCTFail("no water row")
        }
        let rows = store.searchCampFacts(terms: ["delivered"],
                                         queryVector: normalise(v))
        let row = rows.first { $0.id == id }
        XCTAssertNotNil(row)
        XCTAssertGreaterThan(row?.similarity ?? 0, 0.9)
    }

    func testWithoutAQueryVectorNothingChanges() {
        // The encoder is optional. A phone without it must retrieve exactly
        // as it did before E7 -- not worse, and not differently.
        let before = store.searchCampFacts(terms: ["water", "barrels"])
        let after = store.searchCampFacts(terms: ["water", "barrels"],
                                          queryVector: nil)
        XCTAssertEqual(before.map(\.id), after.map(\.id))
    }
}
