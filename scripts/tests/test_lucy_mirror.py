"""The Python mirror of the app's answer path, held honest two ways:
unit tests for the behaviour the Swift comments call out, and source-sync
tests that parse the Swift files and fail when either copy is edited alone.
That second kind exists because a drifted harness already produced a false
regression once (one missing stop word, 2026-08-09)."""
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import lucy_mirror
from lucy_mirror import contains_word, terms

REPO = Path(__file__).resolve().parents[2]


def swift(name: str) -> str:
    return (REPO / "Lucy" / name).read_text()


class TestContainsWord:
    def test_whole_word_matches(self):
        assert contains_word("the water order", "water")

    def test_substring_does_not(self):
        # "water" in "floodwaters" pulled a Noah's Ark party into a
        # drinking-water answer. The whole reason this function exists.
        assert not contains_word("the floodwaters rose", "water")

    def test_first_occurrence_only_like_swift(self):
        # Swift's containsWord boundary-checks only the FIRST occurrence:
        # if that one is embedded, a later whole-word occurrence is missed.
        # Bug-compatible on purpose; the goldens pin app behaviour.
        assert not contains_word("floodwaters and water", "water")

    def test_edges_of_string(self):
        assert contains_word("water", "water")
        assert contains_word("water rises", "water")

    def test_punctuation_is_a_boundary(self):
        assert contains_word("got water?", "water")


class TestTerms:
    def test_stop_words_and_short_words_drop(self):
        assert terms("how do we get water") == ["water"]

    def test_case_and_punctuation(self):
        assert terms("Where's DORIS parked?") == ["doris", "parked"]

    def test_three_letter_words_survive(self):
        # count > 2, so "kit" stays and "we" goes.
        assert terms("where is the med kit") == ["med", "kit"]
        # Two letters is not noise: "Oz" is a camper and "DJ" is a question.
        assert terms("Who is Oz") == ["oz"]
        assert terms("the best DJ in camp") == ["best", "dj", "camp"]


class TestSourceSyncStopWords:
    def test_stop_list_matches_retrieval_swift(self):
        src = swift("Retrieval.swift")
        block = re.search(r"let stop: Set<String> = \[(.*?)\]", src, re.S)
        assert block, "stop list not found in Retrieval.swift — did it move?"
        swift_stop = set(re.findall(r'"([^"]+)"', block.group(1)))
        assert swift_stop == lucy_mirror.STOP, (
            "stop lists differ; a drifted copy reported a false regression "
            "once already. Edit both sides together.")


def make_store(script: str = "") -> "lucy_mirror.Store":
    """An in-memory db with the app's schema (the columns the mirror reads)."""
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE entity (id INTEGER PRIMARY KEY, name TEXT, kind TEXT,
            summary TEXT, first_seen TEXT, last_seen TEXT,
            mention_count INTEGER DEFAULT 0);
        CREATE TABLE entity_alias (entity_id INTEGER, alias TEXT);
        CREATE TABLE entity_fact (id INTEGER PRIMARY KEY, entity_id INTEGER,
            fact TEXT, category TEXT, asserted_on TEXT);
        CREATE TABLE camp_fact (id INTEGER PRIMARY KEY, topic TEXT, fact TEXT,
            category TEXT, year INTEGER);
        CREATE TABLE evidence (id INTEGER PRIMARY KEY, claim_table TEXT,
            claim_id INTEGER, source_table TEXT, source_id INTEGER, quote TEXT);
        CREATE TABLE camp_knowledge (id INTEGER PRIMARY KEY, title TEXT,
            content TEXT, source_file TEXT, category TEXT, year INTEGER);
        CREATE TABLE person (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE expertise (id INTEGER PRIMARY KEY, person_id INTEGER,
            topic TEXT, strength TEXT);
        CREATE TABLE person_profile (person_id INTEGER, summary TEXT,
            known_for TEXT);
    """)
    if script:
        conn.executescript(script)
    return lucy_mirror.Store(conn)


class TestCampFacts:
    def test_topic_match_outranks_word_match(self):
        # "We cook the pasta in boiling salted water" is filed under kitchen
        # and must not beat a water-topic fact for a water question.
        store = make_store("""
            INSERT INTO camp_fact VALUES (1, 'kitchen',
                'We cook the pasta in boiling salted water', NULL, 2024);
            INSERT INTO camp_fact VALUES (2, 'water',
                'We pick up our service vouchers at the USS Camp', NULL, 2024);
        """)
        got = store.search_camp_facts(["water"])
        assert [f.id for f in got] == [2, 1]

    def test_year_bonus_breaks_ties(self):
        store = make_store("""
            INSERT INTO camp_fact VALUES (1, 'shade', 'We bolt the poles', NULL, 2019);
            INSERT INTO camp_fact VALUES (2, 'shade', 'We rope the poles', NULL, 2025);
        """)
        got = store.search_camp_facts(["shade"])
        assert [f.id for f in got] == [2, 1]

    def test_source_title_joins_through_evidence(self):
        store = make_store("""
            INSERT INTO camp_knowledge VALUES (7, 'camp manual', 'x', NULL, NULL, 2025);
            INSERT INTO camp_fact VALUES (1, 'water', 'Vouchers at USS Camp', NULL, 2024);
            INSERT INTO evidence VALUES (1, 'camp_fact', 1, 'camp_knowledge', 7, 'q');
        """)
        got = store.search_camp_facts(["water"])
        assert got[0].source_title == "camp manual"

    def test_roster_evidence_gets_the_roster_title(self):
        # camp_member ids overlap camp_knowledge ids; joining on id alone once
        # gave roster facts an unrelated document's title.
        store = make_store("""
            INSERT INTO camp_knowledge VALUES (3, 'a receipt', 'x', NULL, NULL, NULL);
            INSERT INTO camp_fact VALUES (1, 'roster', 'Forty of us this year', NULL, 2025);
            INSERT INTO evidence VALUES (1, 'camp_fact', 1, 'camp_member', 3, 'q');
        """)
        got = store.search_camp_facts(["forty"])
        assert got[0].source_title == "camp roster"


class TestGeneralKnowledge:
    def test_absent_table_returns_empty_like_swifts_prepare_guard(self):
        # The preview db has no general_knowledge table; the Swift guard
        # returns [] when prepare fails, and so does the mirror.
        store = make_store()
        assert store.search_general(["water"]) == []


class TestPeople:
    def test_strength_weights_and_known_for(self):
        store = make_store("""
            INSERT INTO person VALUES (1, 'Walter Lindell');
            INSERT INTO person VALUES (2, 'Kat Chau');
            INSERT INTO expertise VALUES (1, 1, 'bike repair', 'mentioned');
            INSERT INTO expertise VALUES (2, 2, 'Bike Inventory Management', 'moderate');
        """)
        got = store.search_people(["bike"])
        # mentioned(1) vs moderate(2): the confidence-over-relevance ranking
        # the learnings flag as an open issue. Mirror it, don't fix it here.
        assert [p.name for p in got] == ["Kat Chau", "Walter Lindell"]

    def test_known_for_reaches_people_without_expertise_rows(self):
        store = make_store("""
            INSERT INTO person VALUES (1, 'Sam Olmos');
            INSERT INTO person_profile VALUES (1, 's', 'keeps the generator alive');
        """)
        got = store.search_people(["generator"])
        assert got[0].name == "Sam Olmos"
        assert got[0].known_for == "keeps the generator alive"


class TestEntityFacts:
    def test_unknown_category_collapses_to_history(self):
        # FactCategory(rawValue:) ?? .history in EntityStore.swift.
        store = make_store("""
            INSERT INTO entity VALUES (1, 'Doris', 'vehicle', '', NULL, NULL, 5);
            INSERT INTO entity_fact VALUES (1, 1, 'Bought in 2019', 'provenance', '2019-01-01');
        """)
        grouped = store.facts_for(1)
        assert list(grouped.keys()) == ["history"]

    def test_facts_ordered_oldest_first(self):
        # Both halves of the ladder contradiction, in the order said.
        store = make_store("""
            INSERT INTO entity VALUES (1, 'Doris', 'vehicle', '', NULL, NULL, 5);
            INSERT INTO entity_fact VALUES (1, 1, 'at least three ladders', 'contents', '2022-08-24');
            INSERT INTO entity_fact VALUES (2, 1, 'no ladders found', 'contents', '2022-08-19');
        """)
        facts = store.facts_for(1)["contents"]
        assert [f.id for f in facts] == [2, 1]


class TestDocSearch:
    def test_manual_beats_receipt_on_authority(self):
        manual_text = ("## WATER. We order water from the water company and "
                       "the water truck fills our tank with water each year. "
                       "Water is shared across camp all week long.")
        receipt_text = ("Order water bottles. Costco run: water, water, "
                        "water, water, water, water flats for the crew here.")
        store = make_store(f"""
            INSERT INTO camp_knowledge VALUES (1, 'PS 2025 camp manual',
                '{manual_text}', NULL, 'operations', 2025);
            INSERT INTO camp_knowledge VALUES (2, 'Costco receipt',
                '{receipt_text}', NULL, NULL, 2025);
        """)
        got = store.search_docs(["water"])
        assert got and got[0].id == 1

    def test_score_floor_drops_incidental_mentions(self):
        # The generator manual mentions "mud, water, etc." once; one word
        # must not put it in a water answer.
        store = make_store("""
            INSERT INTO camp_knowledge VALUES (1, 'wiring notes',
                'Keep the cooling holes clear of mud, water, etc. and check often.',
                NULL, NULL, NULL);
        """)
        assert store.search_docs(["water"]) == []

    def test_unreadable_extraction_yields_nothing(self):
        # PDF text that lost its spacing matches searches and must never
        # reach a screen.
        store = make_store("""
            INSERT INTO camp_knowledge VALUES (1, 'PS 2025 camp manual',
                'waterisstoredneartheshadestructureandthepumpisbehinditallweek',
                NULL, 'operations', 2025);
        """)
        assert store.search_docs(["water"]) == []

    def test_passage_is_a_trimmed_window(self):
        filler = "The build starts Friday and the crew arrives in waves. " * 20
        store = make_store(f"""
            INSERT INTO camp_knowledge VALUES (1, 'PS 2025 camp manual',
                '{filler}We pick up water vouchers at the USS Camp on arrival day. {filler}',
                NULL, 'operations', 2025);
        """)
        got = store.search_docs(["water", "vouchers"])
        assert got and "USS Camp" in got[0].passage
        assert len(got[0].passage) < 500


class TestEntitySearch:
    def test_naming_terms_do_not_pick_facts(self):
        # "Doris" appears in most Doris facts; only the REST of the question
        # may choose which facts return.
        store = make_store("""
            INSERT INTO entity VALUES (1, 'Doris', 'vehicle', '', NULL, NULL, 5);
            INSERT INTO entity_fact VALUES (1, 1, 'Doris holds the ladders', 'contents', '2022-08-24');
            INSERT INTO entity_fact VALUES (2, 1, 'Doris gets new tires', 'history', '2023-01-01');
        """)
        got = lucy_mirror.search("ladders in doris", store)
        assert got[0].entity.name == "Doris"
        assert [f.id for f in got[0].facts] == [1]

    def test_overlap_squared_prefers_two_terms_in_one_fact(self):
        store = make_store("""
            INSERT INTO entity VALUES (1, 'Lucy', 'vehicle', '', NULL, NULL, 9);
            INSERT INTO entity_fact VALUES (1, 1, 'the gate code opens the lot', NULL, '2024-01-01');
            INSERT INTO entity_fact VALUES (2, 1, 'the gate is green', NULL, '2024-01-02');
            INSERT INTO entity_fact VALUES (3, 1, 'the code is taped inside', NULL, '2024-01-03');
        """)
        got = lucy_mirror.search("lucy gate code", store)
        assert got[0].facts[0].id == 1  # 2 terms in one fact: score 4 beats 1+1

    def test_name_only_question_falls_back_to_recent_facts(self):
        store = make_store("""
            INSERT INTO entity VALUES (1, 'Playaella', 'tradition', '', NULL, NULL, 3);
            INSERT INTO entity_fact VALUES (1, 1, 'started small', NULL, '2019-01-01');
            INSERT INTO entity_fact VALUES (2, 1, 'now a whole ball', NULL, '2024-01-01');
        """)
        got = lucy_mirror.search("playaella", store)
        assert got[0].facts[0].id == 2  # newest first in the fallback


class TestAnswerBlending:
    def test_thin_followup_carries_previous_terms(self):
        assert lucy_mirror.blended_terms("where is it stored",
                                         "what is the generator") \
            == ["stored", "generator"]

    def test_standalone_question_is_not_contaminated(self):
        # Two terms of its own: the previous turn must not leak in.
        assert lucy_mirror.blended_terms("ladders in doris",
                                         "what is the generator") \
            == ["ladders", "doris"]


def make_answer(**kw) -> "lucy_mirror.Answer":
    base = dict(hits=[], camp=[], docs=[], general=[], people=[])
    base.update(kw)
    return lucy_mirror.Answer(**base)


class TestFactSheet:
    def test_empty_answer_says_nothing_written_down(self):
        sheet = lucy_mirror.fact_sheet(make_answer())
        assert "(nothing — we have not written this down)" in sheet

    def test_camp_facts_lead_with_source_and_year(self):
        camp = [lucy_mirror.CampFact(id=1, topic="water",
                                     fact="Vouchers at USS Camp",
                                     category=None, year=2024,
                                     source_title="camp manual")]
        sheet = lucy_mirror.fact_sheet(make_answer(camp=camp))
        assert "- Vouchers at USS Camp (2024) [from our camp manual]" in sheet

    def test_people_section_names_them(self):
        people = [lucy_mirror.PersonSkill(name="Walter Lindell",
                                          topics=["bike repair"],
                                          known_for="")]
        sheet = lucy_mirror.fact_sheet(make_answer(people=people))
        assert "=== PEOPLE WHO KNOW ABOUT THIS — name them ===" in sheet
        assert "- Walter Lindell: bike repair" in sheet

    def test_sheet_clips_at_a_line_boundary(self):
        camp = [lucy_mirror.CampFact(id=i, topic="water",
                                     fact="w" * 120, category=None,
                                     year=None, source_title="")
                for i in range(60)]
        sheet = lucy_mirror.fact_sheet(make_answer(camp=camp))
        assert len(sheet) <= 4000
        # Clip is exclusive of the newline (matches Swift behavior)
        assert not sheet.endswith("\n")

    def test_passage_fragment_is_cleaned(self):
        docs = [lucy_mirror.DocHit(id=1, title="camp manual", category=None,
                                   year=2025,
                                   passage="don't forget the permit. ## GRAY WATER "
                                           + "x" * 100)]
        sheet = lucy_mirror.fact_sheet(make_answer(docs=docs))
        assert "don't forget" not in sheet
        # Check for curly quotes (U+201C and U+201D)
        expected = f"Our {chr(0x201C)}camp manual{chr(0x201D)} says:"
        assert expected in sheet


class TestPromptAndWrap:
    def test_empty_context_reads_nothing_found(self):
        p = lucy_mirror.prompt("q", "")
        assert "FACTS YOU MAY USE:\n(nothing found)" in p

    def test_chat_wrap_styles(self):
        assert lucy_mirror.chat_wrap("P", "gemma3") \
            == "<start_of_turn>user\nP<end_of_turn>\n<start_of_turn>model\n"
        assert lucy_mirror.chat_wrap("P", "gemma4") \
            == "<|turn>user\nP<turn|>\n<|turn>model\n"


class TestSourceSyncVoiceAndPrompt:
    def test_turn_markers_match_llama_handle_swift(self):
        src = swift("LlamaHandle.swift")
        assert ('"<start_of_turn>user\\n\\(prompt)<end_of_turn>\\n'
                '<start_of_turn>model\\n"') in src
        assert '"<|turn>user\\n\\(prompt)<turn|>\\n<|turn>model\\n"' in src

    @staticmethod
    def _literal(src: str, after: str) -> str:
        """The first triple-quoted Swift literal following `after`,
        unindented, with line continuations joined."""
        tail = src[src.index(after):]
        m = re.search(r'"""\n(.*?)\n(\s*)"""', tail, re.S)
        assert m, f"no string literal after {after!r}"
        body, indent = m.group(1), m.group(2)
        lines = [l[len(indent):] if l.startswith(indent) else l
                 for l in body.split("\n")]
        return "\n".join(lines).replace("\\\n", "")

    def test_prompt_matches_lucy_brain_swift(self):
        src = swift("LucyBrain.swift")
        text = self._literal(src, "static func prompt")
        # The two dials are interpolated; the mirror renders their shipped
        # defaults (Experiments.init: .default_ and .conversational), read
        # from the same source so a reworded default cannot drift past here.
        manner_src = src[src.index("static func manner(for"):]
        length_src = src[src.index("static func lengthGuidance(for"):]
        manner = self._literal(manner_src, "case .default_:")
        length = self._literal(length_src, "case .conversational:")
        text = text.replace("\\(manner)\\(lengthBlock)",
                            manner + "\n\n" + length)
        text = text.replace(
            '\\(context.isEmpty ? "(nothing found)" : context)', "«CTX»")
        text = text.replace("\\(question)", "«Q»")
        assert lucy_mirror.prompt("«Q»", "«CTX»") == text, (
            "prompt text differs from LucyBrain.swift — edit both together; "
            "prompt tuning against a drifted copy is tuning against "
            "malformed input")

    def test_fact_sheet_strings_appear_in_lucy_voice_swift(self):
        src = swift("LucyVoice.swift")
        for fragment in (
            "=== OUR CAMP'S OWN RECORDS — answer from these first ===",
            "=== PEOPLE WHO KNOW ABOUT THIS — name them ===",
            "(nothing — we have not written this down)",
            "=== BACKGROUND: how Burning Man works generally. ",
            "=== NOTE: these facts cover several different things — ",
        ):
            assert fragment in src, f"missing from LucyVoice.swift: {fragment!r}"
