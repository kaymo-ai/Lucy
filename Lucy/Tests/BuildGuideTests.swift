import XCTest
@testable import Lucy

// The build guide, and the figures that carry its instructions.
//
// The guide is instructions AROUND pictures: "Fig. 1 2026 Camp Plan" is a
// drawing of where Doris, the shade and the fire lane go, and no paraphrase
// of it helps somebody standing in the dust. So the thing worth testing is
// not that the text arrived -- it is that the pictures did.
//
// Both ways of losing them are silent. `UIImage(named:)` looks only in the
// asset catalog and `Bundle.main.path(forResource:ofType:)` only at the
// bundle root, while the figures ship in a folder reference; either returns
// nil and the page simply draws no pictures. And xcodegen drops a misplaced
// resource entry without a word, so the folder can fail to ship at all.
final class BuildGuideTests: XCTestCase {

    private var file: String!

    override func setUpWithError() throws {
        file = InfoView.buildFile
        try XCTSkipIf(file == nil,
                      "build-2026.md is not in the bundle; check the sources "
                      + "entry in project.yml has buildPhase: resources")
    }

    func testTheGuideIsInTheBundle() {
        XCTAssertFalse(file.isEmpty)
        XCTAssertTrue(file.contains("Welcome to Build"))
    }

    func testItHasSectionsAndFigures() {
        let sections = splitIntoSections(file).filter { !$0.heading.isEmpty }
        XCTAssertGreaterThan(sections.count, 10,
                             "the guide should arrive as a structure, not one "
                             + "wall of text")
        let figures = splitOutFigures(file).filter { $0.kind == .figure }
        XCTAssertGreaterThan(figures.count, 10,
                             "the figures are the instructions here")
    }

    /// The one that matters. Every figure the markdown names must actually
    /// load from the bundle -- not merely be listed in it.
    func testEveryFigureTheTextNamesActuallyLoads() {
        let figures = splitOutFigures(file).filter { $0.kind == .figure }
        XCTAssertFalse(figures.isEmpty)
        for f in figures {
            XCTAssertNotNil(DocumentView.bundledImage(named: f.value),
                            "\(f.value) is named in build-2026.md but will not "
                            + "load; the page draws '[figure missing]' where "
                            + "an instruction should be")
        }
    }

    /// The camp layout plan by name, because it is the single page somebody
    /// placing Doris needs and losing it quietly is the worst case.
    func testTheCampPlanIsThere() {
        XCTAssertTrue(file.contains("Camp Plan"),
                      "the 2026 camp layout plan's caption is missing")
        XCTAssertNotNil(DocumentView.bundledImage(named: "build-fig1.png"),
                        "the camp layout plan itself is missing")
    }

    // MARK: - The splitter

    func testProseAndFiguresKeepTheirOrder() {
        let pieces = splitOutFigures("before\n\n![figure](x.png)\n\nafter")
        XCTAssertEqual(pieces.count, 3)
        XCTAssertEqual(pieces[0].kind, .prose)
        XCTAssertEqual(pieces[0].value, "before")
        XCTAssertEqual(pieces[1].kind, .figure)
        XCTAssertEqual(pieces[1].value, "x.png")
        XCTAssertEqual(pieces[2].kind, .prose)
        XCTAssertEqual(pieces[2].value, "after")
    }

    /// The manual is 63,000 characters of prose that has never heard of
    /// markdown. A looser image rule would start eating its square brackets.
    func testBracketsInProseAreNotFigures() {
        for line in ["a note [see below] and more",
                     "![not closed",
                     "text ![figure](x.png) inline"] {
            let pieces = splitOutFigures(line)
            XCTAssertTrue(pieces.allSatisfy { $0.kind == .prose },
                          "treated as a figure: \(line)")
        }
    }
}

extension DocPiece.Kind: Equatable {}
