"""The gate around the gate: case validation, scoring, goldens, exits."""
import json
import sqlite3
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import eval_lucy
import lucy_mirror

REPO = Path(__file__).resolve().parents[2]


def write_cases(tmp_path, text):
    p = tmp_path / "questions.yaml"
    p.write_text(textwrap.dedent(text))
    return p


class TestCaseLoading:
    def test_minimal_case_fills_defaults(self, tmp_path):
        cases = eval_lucy.load_cases(write_cases(tmp_path, """
            - id: water-how
              q: how do we get water
              category: logistics
        """))
        c = cases[0]
        assert c.id == "water-how" and c.must_hit == [] and not c.may_refuse

    def test_duplicate_ids_rejected(self, tmp_path):
        with pytest.raises(SystemExit):
            eval_lucy.load_cases(write_cases(tmp_path, """
                - {id: a, q: x, category: c}
                - {id: a, q: y, category: c}
            """))

    def test_unknown_keys_rejected(self, tmp_path):
        # A typo like "must_mentions" must be an error, not a silent skip —
        # a check that never runs reads as a check that passed.
        with pytest.raises(SystemExit):
            eval_lucy.load_cases(write_cases(tmp_path, """
                - id: a
                  q: x
                  category: c
                  answer: {must_mentions: [y]}
            """))

    def test_shipped_case_file_loads(self):
        cases = eval_lucy.load_cases(REPO / "evals" / "questions.yaml")
        assert len(cases) >= 25


def tiny_db(tmp_path) -> Path:
    p = tmp_path / "tiny.db"
    conn = sqlite3.connect(p)
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
        INSERT INTO camp_fact VALUES (1, 'water',
            'We pick up our service vouchers at the USS Camp', NULL, 2024);
        INSERT INTO person VALUES (1, 'Walterine Example');
        INSERT INTO expertise VALUES (1, 1, 'water logistics', 'strong');
    """)
    conn.commit()
    conn.close()
    return p


def one_case(**kw) -> eval_lucy.Case:
    base = dict(id="water-how", q="how do we get water", category="logistics")
    base.update(kw)
    return eval_lucy.Case(**base)


class TestLayer1:
    def test_must_hit_passes_when_sheet_has_it(self, tmp_path):
        store = lucy_mirror.Store.open(str(tiny_db(tmp_path)))
        [r] = eval_lucy.run_layer1([one_case(must_hit=["voucher"])], store)
        assert r.failures == []

    def test_must_hit_fails_and_names_the_string(self, tmp_path):
        store = lucy_mirror.Store.open(str(tiny_db(tmp_path)))
        [r] = eval_lucy.run_layer1([one_case(must_hit=["barrels"])], store)
        assert any("barrels" in f for f in r.failures)

    def test_must_not_hit(self, tmp_path):
        store = lucy_mirror.Store.open(str(tiny_db(tmp_path)))
        [r] = eval_lucy.run_layer1([one_case(must_not_hit=["voucher"])], store)
        assert any("voucher" in f for f in r.failures)


class TestGoldens:
    def test_update_writes_ids_and_hashes_only(self, tmp_path):
        db = tiny_db(tmp_path)
        store = lucy_mirror.Store.open(str(db))
        results = eval_lucy.run_layer1([one_case()], store)
        gdir = tmp_path / "golden"
        db_sha = eval_lucy.sha256_file(db)
        fails = eval_lucy.check_goldens(results, gdir, db_sha, update=True)
        assert fails == []
        g = json.loads((gdir / "water-how.json").read_text())
        assert g["camp_ids"] == [1]
        assert len(g["fact_sheet_sha256"]) == 64
        # The contract: no db text in the golden. "voucher" appears only in
        # the shipped fact row, never in the golden file.
        assert "voucher" not in (gdir / "water-how.json").read_text().lower()

    def test_matching_rerun_is_clean(self, tmp_path):
        db = tiny_db(tmp_path)
        store = lucy_mirror.Store.open(str(db))
        gdir = tmp_path / "golden"
        db_sha = eval_lucy.sha256_file(db)
        results = eval_lucy.run_layer1([one_case()], store)
        eval_lucy.check_goldens(results, gdir, db_sha, update=True)
        fails = eval_lucy.check_goldens(results, gdir, db_sha, update=False)
        assert fails == []

    def test_behaviour_change_is_caught(self, tmp_path):
        db = tiny_db(tmp_path)
        store = lucy_mirror.Store.open(str(db))
        gdir = tmp_path / "golden"
        db_sha = eval_lucy.sha256_file(db)
        results = eval_lucy.run_layer1([one_case()], store)
        eval_lucy.check_goldens(results, gdir, db_sha, update=True)
        g = json.loads((gdir / "water-how.json").read_text())
        g["camp_ids"] = [99]
        (gdir / "water-how.json").write_text(json.dumps(g))
        fails = eval_lucy.check_goldens(results, gdir, db_sha, update=False)
        assert any("water-how" in f and "camp_ids" in f for f in fails)

    def test_db_drift_is_named_not_buried(self, tmp_path):
        db = tiny_db(tmp_path)
        store = lucy_mirror.Store.open(str(db))
        gdir = tmp_path / "golden"
        results = eval_lucy.run_layer1([one_case()], store)
        eval_lucy.check_goldens(results, gdir, "aaaa", update=True)
        fails = eval_lucy.check_goldens(results, gdir, "bbbb", update=False)
        assert any("database changed" in f for f in fails)
        assert not any("golden mismatch" in f for f in fails)

    def test_people_are_hashed_not_named(self, tmp_path):
        # The golden contract is ids and hashes, never text from the db.
        # A person's name is db text like any other.
        db = tiny_db(tmp_path)
        store = lucy_mirror.Store.open(str(db))
        results = eval_lucy.run_layer1([one_case()], store)
        gdir = tmp_path / "golden"
        eval_lucy.check_goldens(results, gdir, eval_lucy.sha256_file(db),
                                update=True)
        raw = (gdir / "water-how.json").read_text()
        assert "Walterine" not in raw
        g = json.loads(raw)
        assert len(g["people_sha256"]) == 64


class TestMainExitCodes:
    def _write_min_cases(self, tmp_path, body):
        p = tmp_path / "cases.yaml"
        p.write_text(textwrap.dedent(body))
        return p

    def test_green_run_exits_zero(self, tmp_path):
        db = tiny_db(tmp_path)
        cases = self._write_min_cases(tmp_path, """
            - id: water-how
              q: how do we get water
              category: logistics
              retrieval: {must_hit: [voucher]}
        """)
        rc = eval_lucy.main(["--db", str(db), "--cases", str(cases),
                            "--golden-dir", str(tmp_path / "g"),
                            "--out-dir", str(tmp_path / "out"),
                            "--update-golden"])
        assert rc == 0

    def test_failure_exits_one_and_xfail_does_not(self, tmp_path):
        db = tiny_db(tmp_path)
        failing = self._write_min_cases(tmp_path, """
            - id: barrels
              q: how do we get water
              category: logistics
              retrieval: {must_hit: [barrels]}
        """)
        rc = eval_lucy.main(["--db", str(db), "--cases", str(failing),
                            "--golden-dir", str(tmp_path / "g1"),
                            "--out-dir", str(tmp_path / "o1"),
                            "--update-golden"])
        assert rc == 1
        excused = self._write_min_cases(tmp_path, """
            - id: barrels
              q: how do we get water
              category: logistics
              retrieval: {must_hit: [barrels]}
              xfail: known gap
        """)
        rc = eval_lucy.main(["--db", str(db), "--cases", str(excused),
                            "--golden-dir", str(tmp_path / "g2"),
                            "--out-dir", str(tmp_path / "o2"),
                            "--update-golden"])
        assert rc == 0


class TestAnswerScoring:
    def test_refusal_detection_covers_contracted_and_not(self):
        for text in ("I don't know that one.", "I do not know.",
                     "That's not something I've been told."):
            assert eval_lucy.is_refusal(text)
        assert not eval_lucy.is_refusal("The vouchers are at the USS Camp.")

    def test_unwanted_refusal_fails(self):
        case = one_case(must_mention=["voucher"])
        fails = eval_lucy.score_answer(case, "I don't know that one.")
        assert fails and "refused" in fails[0]

    def test_must_refuse_accepts_refusal_and_rejects_invention(self):
        case = one_case(must_refuse=True)
        assert eval_lucy.score_answer(case, "I don't know that one.") == []
        fails = eval_lucy.score_answer(case, "The wifi password is hunter2.")
        assert fails

    def test_mention_is_whole_word(self):
        case = one_case(must_mention=["voucher"])
        assert eval_lucy.score_answer(case, "Grab your voucher early.") == []
        assert eval_lucy.score_answer(case, "The vouchersx pile.") != []

    def test_empty_generation_is_a_failure_not_an_answer(self):
        # An empty string from the model is a failed generation (the app
        # falls back rather than showing it); the eval must not score it
        # as a refusal or a pass.
        case = one_case(may_refuse=True)
        fails = eval_lucy.score_answer(case, "")
        assert fails == ["empty generation"]

    def test_mention_matches_a_natural_plural(self):
        # The db fact says "service vouchers"; an answer using the plural is
        # right, not wrong. A bare suffix is still not a word match.
        case = one_case(must_mention=["voucher"])
        assert eval_lucy.score_answer(case, "Grab the vouchers early.") == []
        case2 = one_case(must_mention=["box"])
        assert eval_lucy.score_answer(case2, "The boxes are stacked.") == []


class TestModelRunner:
    def test_generators_are_injected_and_transcripts_written(self, tmp_path):
        db = tiny_db(tmp_path)
        store = lucy_mirror.Store.open(str(db))
        results = eval_lucy.run_layer1(
            [one_case(must_mention=["voucher"])], store)
        rc = eval_lucy.run_models(
            results,
            {"light": lambda p: "Vouchers at the USS Camp, lovely.",
             "full": lambda p: "I don't know that one."},
            tmp_path, compare=True)
        assert rc == 1  # the full model refused with the facts present
        compare = (tmp_path / "compare.md").read_text()
        assert "USS Camp" in compare and "don't know" in compare
        light_transcript = (tmp_path / "answers-light.md").read_text()
        assert "— ok" in light_transcript   # the plural mention now passes

    def test_wrapped_prompt_uses_the_models_turn_style(self, tmp_path):
        db = tiny_db(tmp_path)
        store = lucy_mirror.Store.open(str(db))
        results = eval_lucy.run_layer1([one_case()], store)
        seen = {}
        eval_lucy.run_models(
            results, {"light": lambda p: seen.setdefault("p", p) or "ok"},
            tmp_path, compare=False)
        # "light" is Gemma 3: <start_of_turn> markers, never <|turn>.
        assert seen["p"].startswith("<start_of_turn>user\n")
        assert "<|turn>" not in seen["p"]
