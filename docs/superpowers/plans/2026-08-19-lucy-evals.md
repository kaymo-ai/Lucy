# Lucy Evals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A deterministic regression gate: ~30 common questions run through a Python mirror of the app's answer path against the shipped preview db, with optional model-layer scoring and a 1B-vs-E2B comparison mode.

**Architecture:** `scripts/lucy_mirror.py` mirrors the Swift answer path (Retrieval → EntityStore searches → LucyVoice.factSheet → LucyBrain.prompt → chatWrap) line-for-line; `scripts/eval_lucy.py` runs cases from `evals/questions.yaml`, scores them, and pins behavior in hashed goldens under `evals/golden/`. Source-sync tests parse the Swift files so the copies cannot drift silently.

**Tech Stack:** Python 3 stdlib + PyYAML; `llama-cpp-python` is an optional runtime dependency for the model layer only (never required by tests).

**Spec:** `docs/superpowers/specs/2026-08-19-lucy-evals-design.md` — read it first.

## Global Constraints

- **Never `git add -A`.** Stage named paths only. (CLAUDE.md; the repo has a 106 MB corpus nearby.)
- Databases and reports never enter git: goldens store **row ids and sha256 hashes**, never fact-sheet or prompt text. Full-text artifacts go to `scripts/output/` (already gitignored).
- Eval cases in `evals/questions.yaml` are plain text and checked in (approved 2026-08-19). Exception: don't embed the literal gate code / padlock combos in expectations — assert the label ("gate code"), not the digits.
- Contact details that camp documents carry (vendor phone numbers, published emails) are **legitimate answer content** (Marcus, 2026-08-19). `must_not_mention` is only for invention markers and prompt-echo, never for contact-detail shapes.
- The mirror is bug-compatible with Swift on purpose (first-occurrence quirk in `containsWord`, unstable-ties caveats). Fixing app behavior happens in Swift first; the mirror follows.
- Tests run from `scripts/`: `python3 -m pytest tests/test_lucy_mirror.py tests/test_eval_lucy.py -q` (pytest.ini has `testpaths = tests`). Use a venv with `pytest` and `pyyaml` installed.
- The model layer needs the two GGUFs named in CLAUDE.md (`gemma-4-E2B-it-Q4_K_M.gguf`, `gemma-3-1b-it-Q4_K_M.gguf`) in a `--models-dir`; when absent, the layer skips with a notice and never fails the gate.

## Known divergences (document, do not "fix")

The goldens pin the **Python mirror's** behavior. Three places Swift cannot promise the same bytes today; each is noted in `lucy_mirror.py`'s docstring and will surface when platform replay tests land:

1. Swift `Dictionary` iteration order is randomized per process — `facts(entityID:).values.flatMap` and `searchPeople`'s accumulator have nondeterministic order on ties **in the app itself**. The mirror uses insertion order (SQL order). This is a latent app bug worth its own fix later.
2. Swift `String` counts grapheme clusters; Python counts code points. Affects `passage` windows and the 4000-char clip only on emoji-bearing text.
3. Swift's sort is not stable; Python's is. Equal scores keep source order in the mirror.

---

### Task 1: Text primitives + stop-list source sync

**Files:**
- Create: `scripts/lucy_mirror.py`
- Create: `scripts/tests/test_lucy_mirror.py`

**Interfaces:**
- Produces: `STOP: set[str]`, `contains_word(haystack: str, word: str) -> bool`, `terms(query: str) -> list[str]`. All later tasks import from `lucy_mirror`.

- [ ] **Step 1: Write the failing tests**

```python
# scripts/tests/test_lucy_mirror.py
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


class TestSourceSyncStopWords:
    def test_stop_list_matches_retrieval_swift(self):
        src = swift("Retrieval.swift")
        block = re.search(r"let stop: Set<String> = \[(.*?)\]", src, re.S)
        assert block, "stop list not found in Retrieval.swift — did it move?"
        swift_stop = set(re.findall(r'"([^"]+)"', block.group(1)))
        assert swift_stop == lucy_mirror.STOP, (
            "stop lists differ; a drifted copy reported a false regression "
            "once already. Edit both sides together.")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_lucy_mirror.py -q` (from `scripts/`, in the venv)
Expected: FAIL — `ModuleNotFoundError: No module named 'lucy_mirror'`

- [ ] **Step 3: Write the implementation**

```python
# scripts/lucy_mirror.py
#!/usr/bin/env python3
"""The app's answer path, mirrored in Python for the eval gate.

Every function here is a COPY of Swift logic and names its source file.
Editing one side without the other is the failure mode this project has
already paid for once (the stop-word incident, learnings 2026-08-09), so
tests/test_lucy_mirror.py parses the Swift sources and fails on drift.

Known divergences the goldens accept (see the eval plan, "Known
divergences"): Swift Dictionary iteration order is randomized where this
file uses insertion order; Swift counts grapheme clusters where this file
counts code points; Swift's sort is unstable where Python's is stable.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

# --- Retrieval.swift: stop list, terms, whole-word matching -----------------

STOP = {
    "what", "whats", "who", "whos", "where", "wheres", "when", "whens",
    "why", "how", "is", "are", "was", "were", "the", "a", "an", "of", "in",
    "on", "at", "to", "for", "do", "does", "did", "i", "we", "you", "my",
    "our", "it", "its", "and", "or", "with", "about", "tell", "me", "get",
    "got", "need", "any", "some", "there", "have", "has", "can", "should",
    "know", "lucy",
}


def contains_word(haystack: str, word: str) -> bool:
    # Mirrors containsWord in Retrieval.swift — including its quirk: only
    # the FIRST occurrence is boundary-checked, so an embedded first hit
    # hides a later whole-word one.
    i = haystack.find(word)
    if i < 0:
        return False
    before = haystack[i - 1] if i > 0 else None
    j = i + len(word)
    after = haystack[j] if j < len(haystack) else None

    def boundary(c: str | None) -> bool:
        return c is None or not (c.isalpha() or c.isnumeric())

    return boundary(before) and boundary(after)


def terms(query: str) -> list[str]:
    # Retrieval.terms: split on non-alphanumerics, keep len > 2, drop stop.
    words = re.findall(r"[^\W_]+", query.lower())
    return [w for w in words if len(w) > 2 and w not in STOP]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_lucy_mirror.py -q`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/lucy_mirror.py scripts/tests/test_lucy_mirror.py
git commit -m "feat(evals): mirror terms and whole-word matching, stop list source-synced"
```

---

### Task 2: Store dataclasses + camp/general/people searches

**Files:**
- Modify: `scripts/lucy_mirror.py`
- Modify: `scripts/tests/test_lucy_mirror.py`

**Interfaces:**
- Consumes: `contains_word`, `terms` from Task 1.
- Produces: dataclasses `Entity(id, name, kind, summary, aliases)`, `EntityFact(id, fact, category, asserted_on)`, `Hit(entity, facts, score)`, `CampFact(id, topic, fact, category, year, quote, source_title)`, `DocHit(id, title, category, year, passage)`, `GeneralFact(id, topic, fact, source)`, `PersonSkill(name, topics, known_for)`, `Answer(hits, camp, docs, general, people)` with property `is_empty`; class `Store` with `Store(conn: sqlite3.Connection)`, `Store.open(path) -> Store`, methods `all_entities() -> list[Entity]`, `facts_for(entity_id) -> dict[str, list[EntityFact]]`, `search_camp_facts(terms_, limit=5) -> list[CampFact]`, `search_general(terms_, limit=2) -> list[GeneralFact]`, `search_people(terms_, limit=4) -> list[PersonSkill]`.

- [ ] **Step 1: Write the failing tests**

Append to `scripts/tests/test_lucy_mirror.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_lucy_mirror.py -q`
Expected: FAIL — `AttributeError: module 'lucy_mirror' has no attribute 'Store'`

- [ ] **Step 3: Write the implementation**

Append to `scripts/lucy_mirror.py`:

```python
# --- EntityStore.swift / PeopleStore.swift: rows and searches ---------------

_CATEGORIES = ("access", "contents", "location", "handling", "history")


@dataclass
class Entity:
    id: int
    name: str
    kind: str
    summary: str
    aliases: list[str] = field(default_factory=list)


@dataclass
class EntityFact:
    id: int
    fact: str
    category: str | None
    asserted_on: str | None


@dataclass
class Hit:
    entity: Entity
    facts: list[EntityFact]
    score: float


@dataclass
class CampFact:
    id: int
    topic: str
    fact: str
    category: str | None
    year: int | None
    quote: str = ""
    source_title: str = ""


@dataclass
class DocHit:
    id: int
    title: str
    category: str | None
    year: int | None
    passage: str


@dataclass
class GeneralFact:
    id: int
    topic: str
    fact: str
    source: str


@dataclass
class PersonSkill:
    name: str
    topics: list[str]
    known_for: str


@dataclass
class Answer:
    hits: list[Hit]
    camp: list[CampFact]
    docs: list[DocHit]
    general: list[GeneralFact]
    people: list[PersonSkill]

    @property
    def is_empty(self) -> bool:
        return not (self.hits or self.camp or self.docs or self.general
                    or self.people)


class Store:
    """The subset of EntityStore/PeopleStore the answer path reads."""

    def __init__(self, conn: sqlite3.Connection):
        self.db = conn

    @classmethod
    def open(cls, path: str) -> "Store":
        return cls(sqlite3.connect(f"file:{path}?mode=ro", uri=True))

    def all_entities(self) -> list[Entity]:
        out = []
        for id_, name, kind, summary, _fs, _ls, _mc in self.db.execute(
                "SELECT id, name, kind, summary, first_seen, last_seen, "
                "mention_count FROM entity ORDER BY mention_count DESC"):
            aliases = [r[0] for r in self.db.execute(
                "SELECT alias FROM entity_alias WHERE entity_id = ?", (id_,))]
            out.append(Entity(id=id_, name=name or "", kind=kind or "",
                              summary=summary or "", aliases=aliases))
        return out

    def facts_for(self, entity_id: int) -> dict[str, list[EntityFact]]:
        # Oldest first is the point: both halves of a contradiction, in the
        # order said. Unknown categories collapse to history like
        # FactCategory(rawValue:) ?? .history. Insertion-ordered dict where
        # Swift's Dictionary is randomized — a documented divergence.
        grouped: dict[str, list[EntityFact]] = {}
        for id_, fact, category, asserted_on in self.db.execute(
                "SELECT id, fact, category, asserted_on FROM entity_fact "
                "WHERE entity_id = ? ORDER BY asserted_on ASC, id ASC",
                (entity_id,)):
            cat = category if category in _CATEGORIES else "history"
            grouped.setdefault(cat, []).append(EntityFact(
                id=id_, fact=fact or "", category=category,
                asserted_on=asserted_on))
        return grouped

    def search_camp_facts(self, terms_: list[str], limit: int = 5) -> list[CampFact]:
        if not terms_:
            return []
        # The Swift SQL verbatim (EntityStore.swift searchCampFacts),
        # including the source_table condition that keeps roster facts from
        # wearing an unrelated document's title.
        sql = """
        SELECT f.id, f.topic, f.fact, f.category, f.year,
               COALESCE(e.quote, ''),
               COALESCE(k.title,
                        CASE WHEN e.source_table = 'camp_member'
                             THEN 'camp roster' END,
                        '')
        FROM camp_fact f
        LEFT JOIN evidence e ON e.claim_table = 'camp_fact' AND e.claim_id = f.id
        LEFT JOIN camp_knowledge k ON k.id = e.source_id
                                  AND e.source_table = 'camp_knowledge'
        GROUP BY f.id;
        """
        hits: list[tuple[CampFact, float]] = []
        for id_, topic, fact, category, year, quote, title in self.db.execute(sql):
            lower = fact.lower()
            score = 0.0
            for t in terms_:
                if topic.lower() == t:
                    score += 8
                if contains_word(lower, t):
                    score += 3
            if score <= 0:
                continue
            if year is not None:
                score += min(max(year - 2016, 0) * 0.4, 4)
            hits.append((CampFact(id=id_, topic=topic, fact=fact,
                                  category=category, year=year, quote=quote,
                                  source_title=title), score))
        hits.sort(key=lambda h: h[1], reverse=True)
        return [h[0] for h in hits[:limit]]

    def search_general(self, terms_: list[str], limit: int = 2) -> list[GeneralFact]:
        if not terms_:
            return []
        try:
            rows = list(self.db.execute(
                "SELECT id, topic, fact, source FROM general_knowledge"))
        except sqlite3.OperationalError:
            # The preview db has no general_knowledge table. Swift's prepare
            # guard returns [] there; so does the mirror.
            return []
        hits: list[tuple[GeneralFact, int]] = []
        for id_, topic, fact, source in rows:
            hay = (topic + " " + fact).lower()
            score = sum(2 if topic.lower() == t
                        else (1 if contains_word(hay, t) else 0)
                        for t in terms_)
            if score > 0:
                hits.append((GeneralFact(id=id_, topic=topic, fact=fact,
                                         source=source), score))
        hits.sort(key=lambda h: h[1], reverse=True)
        return [h[0] for h in hits[:limit]]

    def search_people(self, terms_: list[str], limit: int = 4) -> list[PersonSkill]:
        if not terms_:
            return []
        scored: dict[int, dict] = {}
        for pid, name, topic, strength in self.db.execute(
                "SELECT p.id, p.name, e.topic, e.strength "
                "FROM expertise e JOIN person p ON p.id = e.person_id"):
            matched = sum(1 for t in terms_ if contains_word(topic.lower(), t))
            if matched == 0:
                continue
            weight = {"strong": 3.0, "moderate": 2.0}.get(strength, 1.0)
            entry = scored.setdefault(
                pid, {"name": name, "topics": [], "score": 0.0, "known": ""})
            entry["topics"].append(topic)
            entry["score"] += matched * weight
        for pid, name, known in self.db.execute(
                "SELECT p.id, p.name, COALESCE(pp.known_for,'') "
                "FROM person_profile pp JOIN person p ON p.id = pp.person_id"):
            matched = sum(1 for t in terms_ if contains_word(known.lower(), t))
            if matched == 0 and pid not in scored:
                continue
            entry = scored.setdefault(
                pid, {"name": name, "topics": [], "score": 0.0, "known": ""})
            entry["known"] = known
            entry["score"] += matched * 2.0
        people = [e for e in scored.values() if e["score"] > 0]
        people.sort(key=lambda e: e["score"], reverse=True)
        return [PersonSkill(name=e["name"], topics=e["topics"],
                            known_for=e["known"]) for e in people[:limit]]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_lucy_mirror.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/lucy_mirror.py scripts/tests/test_lucy_mirror.py
git commit -m "feat(evals): mirror camp-fact, general and people searches"
```

---

### Task 3: Document search (authority, passage extraction, readability)

**Files:**
- Modify: `scripts/lucy_mirror.py`
- Modify: `scripts/tests/test_lucy_mirror.py`

**Interfaces:**
- Consumes: `Store`, `DocHit`, `contains_word` from Tasks 1–2.
- Produces: `Store.search_docs(terms_, limit=2) -> list[DocHit]`; module helpers `_authority`, `_count_word`, `_passage`, `_density`, `_is_readable` (private; tests may exercise them directly).

- [ ] **Step 1: Write the failing tests**

Append to `scripts/tests/test_lucy_mirror.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_lucy_mirror.py -q`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'search_docs'`

- [ ] **Step 3: Write the implementation**

Append to `scripts/lucy_mirror.py` (helpers above the `Store` class or below it — module level):

```python
# --- EntityStore.swift: document search -------------------------------------

_MANUAL_MARKERS = ("manual", "potentially useful info", "binder",
                   "handbook", "survival guide")
_RECEIPT_MARKERS = ("receipt", "invoice", "expense", "budget", "order",
                    "costco", "walmart")


def _authority(title: str, category: str, year: int | None) -> float:
    # A prior on the document, independent of the question: the manual is
    # what people are told to read; a receipt mentions without explaining.
    score = 0.0
    for m in _MANUAL_MARKERS:
        if m in title:
            score += 14
            break
    for m in _RECEIPT_MARKERS:
        if m in title:
            score -= 10
            break
    if category == "operations":
        score += 2
    if year is not None:
        score += min(max(year - 2016, 0) * 0.6, 6)
    return score


def _count_word(word: str, text: str) -> int:
    # Whole-word occurrences, every occurrence (unlike contains_word).
    n = 0
    start = 0
    while True:
        i = text.find(word, start)
        if i < 0:
            break
        before = text[i - 1] if i > 0 else None
        j = i + len(word)
        after = text[j] if j < len(text) else None

        def edge(c: str | None) -> bool:
            return c is None or not (c.isalpha() or c.isnumeric())

        if edge(before) and edge(after):
            n += 1
        start = j
        if n > 500:
            break
    return n


def _density(positions: list[int], index: int, radius: int, n: int) -> int:
    lo = max(index - radius, 0)
    hi = min(index + radius, n)
    return sum(1 for p in positions if lo <= p <= hi)


def _is_readable(text: str) -> bool:
    words = [w for w in text.split(" ") if w]
    if not words:
        return False
    if any(len(w) > 25 for w in words):
        return False
    structural = sum(1 for ch in text if ch in '{}[]"')
    if structural / len(text) > 0.02:
        return False
    return text.count(" ") / len(text) > 1.0 / 12.0


def _passage(content: str, terms_: list[str]) -> str | None:
    lower = content.lower()
    positions: list[int] = []
    for term in terms_:
        if not contains_word(lower, term):
            continue
        start = 0
        while True:
            i = lower.find(term, start)
            if i < 0:
                break
            positions.append(i)
            start = i + len(term)
            if len(positions) > 400:
                break
    if not positions:
        return None
    radius = 220
    n = len(lower)
    anchor = max(positions,
                 key=lambda p: _density(positions, p, radius, n))
    start = max(anchor - radius, 0)
    end = min(anchor + radius, n)
    window = content[start:end].replace("\n", " ")
    while "  " in window:
        window = window.replace("  ", " ")
    window = window.strip()
    leading_clean = start == 0
    dot = window.find(".")
    if dot != -1 and not leading_clean and dot < 90:
        window = window[dot + 1:].strip()
        leading_clean = True
    if not leading_clean:
        sp = window.find(" ")
        if sp != -1:
            window = "… " + window[sp + 1:]
    if end != n:
        sp = window.rfind(" ")
        if sp != -1:
            window = window[:sp] + " …"
    if not window or not _is_readable(window):
        return None
    return window
```

And inside `class Store`:

```python
    def search_docs(self, terms_: list[str], limit: int = 2) -> list[DocHit]:
        if not terms_:
            return []
        hits: list[tuple[DocHit, float]] = []
        for id_, title, content, category, year in self.db.execute(
                "SELECT id, title, content, category, year FROM camp_knowledge"):
            content = content or ""
            lower = content.lower()
            title_lower = title.lower()
            cat = (category or "").lower()
            score = 0.0
            for t in terms_:
                if contains_word(title_lower, t):
                    score += 6
                if cat == t:
                    score += 5
            occurrences = sum(_count_word(t, lower) for t in terms_)
            if occurrences > 0:
                per1k = occurrences * 1000.0 / max(len(content), 1)
                score += min(occurrences, 40) * 0.6 + min(per1k, 3)
            distinct = sum(1 for t in terms_ if contains_word(lower, t))
            if distinct >= 2:
                score += distinct
            score += _authority(title_lower, cat, year)
            if score < 4:
                continue
            passage = _passage(content, terms_)
            if passage is None:
                continue
            hits.append((DocHit(id=id_, title=title, category=category,
                                year=year, passage=passage), score))
        hits.sort(key=lambda h: h[1], reverse=True)
        return [h[0] for h in hits[:limit]]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_lucy_mirror.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/lucy_mirror.py scripts/tests/test_lucy_mirror.py
git commit -m "feat(evals): mirror document search with authority prior and passage windows"
```

---

### Task 4: Retrieval search + answer (entity ranking, follow-up blending)

**Files:**
- Modify: `scripts/lucy_mirror.py`
- Modify: `scripts/tests/test_lucy_mirror.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `search(query: str, store: Store, limit: int = 3) -> list[Hit]`, `blended_terms(query: str, previous: str | None) -> list[str]`, `answer(query: str, store: Store, previous: str | None = None) -> Answer`.

- [ ] **Step 1: Write the failing tests**

Append to `scripts/tests/test_lucy_mirror.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_lucy_mirror.py -q`
Expected: FAIL — `AttributeError: module 'lucy_mirror' has no attribute 'search'`

- [ ] **Step 3: Write the implementation**

Append to `scripts/lucy_mirror.py`:

```python
# --- Retrieval.swift: entity ranking and the full sweep ---------------------

def search(query: str, store: Store, limit: int = 3) -> list[Hit]:
    ts = terms(query)
    if not ts:
        return []
    hits: list[Hit] = []
    for entity in store.all_entities():
        name = entity.name.lower()
        aliases = [a.lower() for a in entity.aliases]
        score = 0.0
        for t in ts:
            if name == t:
                score += 12
            elif t in name:
                score += 6
            if any(a == t for a in aliases):
                score += 8
        # The entity's own name ranks its facts equally; only the rest of
        # the question picks which facts return.
        naming = set([name] + aliases)
        distinguishing = [t for t in ts
                          if not any(t in n for n in naming)]
        facts = [f for group in store.facts_for(entity.id).values()
                 for f in group]
        matched: list[tuple[EntityFact, float]] = []
        for fact in facts:
            text = fact.fact.lower()
            overlap = sum(1 for t in distinguishing if contains_word(text, t))
            if overlap > 0:
                # Two matching terms in one fact beat one term in two facts.
                fact_score = float(overlap * overlap)
                matched.append((fact, fact_score))
                score += fact_score
        if score <= 0:
            continue
        if matched:
            ordered = [f for f, _ in
                       sorted(matched, key=lambda m: m[1], reverse=True)]
        else:
            ordered = sorted(facts, key=lambda f: f.asserted_on or "",
                             reverse=True)[:4]
        hits.append(Hit(entity=entity, facts=ordered[:5], score=score))
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]


def blended_terms(query: str, previous: str | None) -> list[str]:
    # A thin follow-up borrows the previous turn's terms; a question that
    # stands on its own must not be contaminated by the last one.
    t = terms(query)
    if len(t) < 2 and previous:
        t = t + [c for c in terms(previous) if c not in t]
    return t


def answer(query: str, store: Store, previous: str | None = None) -> Answer:
    t = blended_terms(query, previous)
    return Answer(
        # The blended terms, not the raw question — otherwise entity
        # matching is the one layer that cannot see the follow-up's subject.
        hits=search(" ".join(t), store),
        camp=store.search_camp_facts(t),
        docs=store.search_docs(t),
        general=store.search_general(t),
        people=store.search_people(t),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_lucy_mirror.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/lucy_mirror.py scripts/tests/test_lucy_mirror.py
git commit -m "feat(evals): mirror entity ranking and follow-up term blending"
```

---

### Task 5: Fact sheet, prompt, chat wrap — with source-sync tests

**Files:**
- Modify: `scripts/lucy_mirror.py`
- Modify: `scripts/tests/test_lucy_mirror.py`

**Interfaces:**
- Consumes: `Answer` and row dataclasses.
- Produces: `fact_sheet(answer: Answer) -> str`, `topics(answer: Answer) -> list[str]`, `prompt(question: str, context: str) -> str`, `chat_wrap(prompt_text: str, style: str) -> str` where `style` is `"gemma3"` or `"gemma4"`.
- Note: the app's model context is `factSheet + chatLogSection` (ChatView.swift:495); eval cases are fresh sessions, so both chat-log sections are empty and context == fact sheet. State this in the module docstring.

- [ ] **Step 1: Write the failing tests**

Append to `scripts/tests/test_lucy_mirror.py`:

```python
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
        assert not sheet.endswith("w")  # cut fell back to the last newline

    def test_passage_fragment_is_cleaned(self):
        docs = [lucy_mirror.DocHit(id=1, title="camp manual", category=None,
                                   year=2025,
                                   passage="don't forget the permit. ## GRAY WATER "
                                           + "x" * 100)]
        sheet = lucy_mirror.fact_sheet(make_answer(docs=docs))
        assert "don't forget" not in sheet


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

    def test_prompt_matches_lucy_brain_swift(self):
        src = swift("LucyBrain.swift")
        tail = src[src.index("static func prompt"):]
        m = re.search(r'"""\n(.*?)\n(\s*)"""', tail, re.S)
        assert m, "prompt literal not found in LucyBrain.swift"
        body, indent = m.group(1), m.group(2)
        lines = [l[len(indent):] if l.startswith(indent) else l
                 for l in body.split("\n")]
        text = "\n".join(lines)
        text = text.replace("\\\n", "")  # Swift line continuations
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_lucy_mirror.py -q`
Expected: FAIL — `AttributeError: module 'lucy_mirror' has no attribute 'fact_sheet'`

- [ ] **Step 3: Write the implementation**

Append to `scripts/lucy_mirror.py`:

```python
# --- LucyVoice.swift: composing what the model may say ----------------------

def _clean(passage: str) -> str:
    # A passage cut mid-file often opens on the tail of an unrelated
    # sentence; drop the fragment before the first heading or sentence end.
    text = passage
    h = text.find("##")
    if h != -1:
        after = text[h + 2:]
        if len(after) > 80:
            text = after.strip()
    else:
        stop = text.find(".")
        if stop != -1 and stop < 60:
            text = text[stop + 1:].strip()
    return text


def topics(answer: Answer) -> list[str]:
    found: list[str] = []

    def add(t: str) -> None:
        if t not in found:
            found.append(t)

    for hit in answer.hits:
        for fact in hit.facts:
            c = fact.category
            if c == "access":
                add(f"getting into {hit.entity.name}")
            elif c == "contents":
                add(f"what is in {hit.entity.name}")
            elif c == "location":
                add(f"where {hit.entity.name} is")
            elif c == "handling":
                add(f"how we handle {hit.entity.name}")
            elif c == "history":
                add(f"{hit.entity.name}'s history")
    for doc in answer.docs:
        p = doc.passage.lower()
        if "gray water" in p or "grey water" in p:
            add("the grey water tank")
        if "permit" in p:
            add("permits")
        if doc.category and doc.category != "general":
            add(doc.category)
    if answer.general:
        for g in answer.general:
            if g.topic not in found:
                add(g.topic)
    return found


def fact_sheet(answer: Answer) -> str:
    # Ordered and labelled by authority; a small model reaches for the
    # tidiest text unless told which matters.
    lines: list[str] = []
    lines.append("=== OUR CAMP'S OWN RECORDS — answer from these first ===")
    for fact in answer.camp:
        where = f" [from our {fact.source_title}]" if fact.source_title else ""
        when = f" ({fact.year})" if fact.year is not None else ""
        lines.append(f"- {fact.fact}{when}{where}")
    if not answer.hits and not answer.docs and not answer.camp:
        lines.append("(nothing — we have not written this down)")
    for doc in answer.docs:
        lines.append(f"Our “{doc.title}” says:")
        lines.append(f"- {_clean(doc.passage)}")
    for hit in answer.hits:
        lines.append(f"About {hit.entity.name}, our {hit.entity.kind}:")
        for fact in hit.facts:
            when = f" [said {fact.asserted_on}]" if fact.asserted_on is not None else ""
            lines.append(f"- {fact.fact}{when}")
    if answer.people:
        lines.append("")
        lines.append("=== PEOPLE WHO KNOW ABOUT THIS — name them ===")
        for person in answer.people:
            why = ", ".join(person.topics[:3])
            if not why:
                why = person.known_for
            lines.append(f"- {person.name}: {why}")
    subjects = topics(answer)
    if len(subjects) >= 3:
        lines.append("")
        lines.append("=== NOTE: these facts cover several different things — "
                     + "; ".join(subjects[:4])
                     + " — so the question may be asking about any of them ===")
    if answer.general:
        lines.append("")
        lines.append("=== BACKGROUND: how Burning Man works generally. "
                     "Use only to fill a gap our own records leave ===")
        for g in answer.general:
            lines.append(f"- {g.fact} [{g.source}]")
    sheet = "\n".join(lines)
    if len(sheet) > 4000:
        sheet = sheet[:4000]
        cut = sheet.rfind("\n")
        if cut != -1:
            sheet = sheet[:cut]
    return sheet


# --- LucyBrain.swift / LlamaHandle.swift: prompt and turn markers -----------

def prompt(question: str, context: str) -> str:
    facts = context if context else "(nothing found)"
    return (
        "You are Lucy, the Preservation Society's snail art car. You have "
        "been to Burning Man since 2018, you carry the camp's sound system, "
        "and you are the camp's memory. You belong to the camp and speak "
        "about it: the build, the bike fleet, who meets the driver.\n"
        "\n"
        "RULES, in order — the earlier the rule, the more it matters:\n"
        "1. Say only what the facts below say. A name, date, number or "
        "place that is not below is one you do not know.\n"
        "2. Answer the question that was asked. If the asked-for detail — "
        "a kind, a place, a time, a number — is not in the facts, say so "
        "first, plainly, then give what the facts DO hold about the thing. "
        "Never answer around a gap.\n"
        "3. The camp's work is the camp's. The facts say \"we\" because "
        "the camp wrote them; you were not there. Credit doing to the camp "
        "or a named person — \"I\" is only for what you remember and say, "
        "never for building, storing, hauling or fixing.\n"
        "4. Our own records outrank the background section. Say when you "
        "had to reach past them.\n"
        "5. Vague question, several possible subjects: ask which they "
        "mean, naming the options in one sentence. Facts disagree: give "
        "both with dates. Someone asks who to ask: lead with the name. "
        "Asked to mock a campmate: decline warmly in your own words, "
        "without repeating the mockery or the name inside it.\n"
        "\n"
        "Your manner: warm, quick, a bit playful — a giant snail with a "
        "sound system, not a reference desk. Dry humour welcome; be "
        "delighted by the camp. Short sentences but a full answer: weave "
        "every detail that bears on the question into one telling, with "
        "its names and dates. Start with the answer, never with the "
        "question restated.\n"
        "\n"
        "The camp's own words inside the facts are yours to reuse — "
        "folded into your sentences, without quotation marks, without the "
        "@ that chat handles carry. That colour is on the record; using "
        "it is quoting, not inventing. Play with the facts, never with "
        "the truth.\n"
        "\n"
        "FACTS YOU MAY USE:\n"
        f"{facts}\n"
        "\n"
        f"QUESTION: {question}\n"
        "\n"
        "Answer as Lucy, using only the facts above."
    )


def chat_wrap(prompt_text: str, style: str) -> str:
    if style == "gemma3":
        return (f"<start_of_turn>user\n{prompt_text}<end_of_turn>\n"
                f"<start_of_turn>model\n")
    return f"<|turn>user\n{prompt_text}<turn|>\n<|turn>model\n"
```

**If `test_prompt_matches_lucy_brain_swift` fails on whitespace:** the Python literal above was reconstructed from the Swift source by hand; trust the test. Fix the Python string to match what the test's parse of `LucyBrain.swift` produces (print both sides), never the other way around.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_lucy_mirror.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/lucy_mirror.py scripts/tests/test_lucy_mirror.py
git commit -m "feat(evals): mirror fact sheet, prompt and turn markers, source-synced"
```

---

### Task 6: The case file and its loader

**Files:**
- Create: `evals/questions.yaml`
- Create: `scripts/eval_lucy.py`
- Create: `scripts/tests/test_eval_lucy.py`
- Modify: `scripts/requirements.txt` (add `pyyaml`)

**Interfaces:**
- Produces: `load_cases(path) -> list[Case]` in `eval_lucy.py`; `Case` dataclass with fields `id: str`, `q: str`, `category: str`, `previous: str | None`, `must_hit: list[str]`, `must_not_hit: list[str]`, `must_mention: list[str]`, `must_not_mention: list[str]`, `may_refuse: bool`, `must_refuse: bool`, `xfail: str | None`.

- [ ] **Step 1: Write the failing tests**

```python
# scripts/tests/test_eval_lucy.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_eval_lucy.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'eval_lucy'` (install `pyyaml` into the venv first: `pip install pyyaml`)

- [ ] **Step 3: Write the loader and the case file**

`scripts/eval_lucy.py` (start of the file; the runner grows in Tasks 7–8):

```python
#!/usr/bin/env python3
"""The common-questions regression gate.

Runs evals/questions.yaml against the shipped preview database through the
Python mirror of the app's answer path (lucy_mirror.py). Layer 1 (retrieval
and composition) always runs and is deterministic. Layer 2 (the model) is
optional and needs local GGUFs.

    python3 eval_lucy.py                       # layer 1 + golden check
    python3 eval_lucy.py --update-golden       # pin current behaviour
    python3 eval_lucy.py --model both --compare --models-dir ~/lucy-models

Exit codes: 0 clean (xfails allowed), 1 failures, 2 usage/environment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lucy_mirror

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

_CASE_KEYS = {"id", "q", "category", "previous", "retrieval", "answer", "xfail"}
_RETRIEVAL_KEYS = {"must_hit", "must_not_hit"}
_ANSWER_KEYS = {"must_mention", "must_not_mention", "may_refuse", "must_refuse"}


@dataclass
class Case:
    id: str
    q: str
    category: str
    previous: str | None = None
    must_hit: list[str] = field(default_factory=list)
    must_not_hit: list[str] = field(default_factory=list)
    must_mention: list[str] = field(default_factory=list)
    must_not_mention: list[str] = field(default_factory=list)
    may_refuse: bool = False
    must_refuse: bool = False
    xfail: str | None = None


def _die(msg: str) -> None:
    print(f"eval_lucy: {msg}", file=sys.stderr)
    raise SystemExit(2)


def load_cases(path: Path | str) -> list[Case]:
    raw = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, list) or not raw:
        _die(f"{path}: expected a non-empty list of cases")
    cases: list[Case] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            _die(f"case #{i}: not a mapping")
        unknown = set(entry) - _CASE_KEYS
        if unknown:
            _die(f"case #{i} ({entry.get('id')}): unknown keys {sorted(unknown)}")
        for key, allowed in (("retrieval", _RETRIEVAL_KEYS),
                             ("answer", _ANSWER_KEYS)):
            sub = entry.get(key) or {}
            bad = set(sub) - allowed
            if bad:
                _die(f"case {entry.get('id')}: unknown {key} keys {sorted(bad)}")
        cid = entry.get("id")
        if not cid or not entry.get("q") or not entry.get("category"):
            _die(f"case #{i}: id, q and category are required")
        if cid in seen:
            _die(f"duplicate case id: {cid}")
        seen.add(cid)
        r = entry.get("retrieval") or {}
        a = entry.get("answer") or {}
        cases.append(Case(
            id=cid, q=entry["q"], category=entry["category"],
            previous=entry.get("previous"),
            must_hit=list(r.get("must_hit") or []),
            must_not_hit=list(r.get("must_not_hit") or []),
            must_mention=list(a.get("must_mention") or []),
            must_not_mention=list(a.get("must_not_mention") or []),
            may_refuse=bool(a.get("may_refuse", False)),
            must_refuse=bool(a.get("must_refuse", False)),
            xfail=entry.get("xfail"),
        ))
    return cases
```

`evals/questions.yaml` — write these 30 cases exactly (they are validated and adjusted against real output in Task 9; expectation strings are hand-written examples, checked in by approval 2026-08-19):

```yaml
# The common questions. Expectations assert DESIRED behaviour; when current
# retrieval can't meet one for a known reason, the case carries `xfail: <reason>`
# and stays visible on every report instead of gating.
#
# Case-writing rules (see the design spec):
# - never embed the literal gate code / padlock combos — assert the label.
# - contact details carried by camp documents are legitimate answer content;
#   must_not_mention is for invention markers only, never contact shapes.

# --- logistics: the camp_fact layer -----------------------------------------
- id: water-how
  q: how do we get water
  category: logistics
  retrieval: {must_hit: [voucher]}
  answer: {must_mention: [voucher]}
- id: water-delivered
  q: when is the water delivered
  category: logistics
  retrieval: {must_hit: [voucher]}
  xfail: "E7-vocab: 'delivered' cannot reach 'service vouchers' by keyword"
- id: arrival
  q: when should we arrive at the playa
  category: logistics
  retrieval: {must_hit: [arrival]}
- id: trash-strike
  q: where do we dump trash during strike
  category: logistics
  retrieval: {must_hit: [fernley]}
- id: warehouse
  q: where is the warehouse
  category: logistics
  retrieval: {must_hit: [monument]}
- id: decom-house
  q: where is the decom house
  category: logistics
  retrieval: {must_hit: [tahoe]}
- id: power
  q: where does our power come from
  category: logistics
  retrieval: {must_hit: [power]}
- id: shade-up
  q: how do the shade structures go up
  category: logistics
  retrieval: {must_hit: [shade]}
- id: shifts
  q: how do camp shifts work
  category: logistics
  retrieval: {must_hit: [shift]}
- id: dinner
  q: who cooks dinner at camp
  category: logistics
  retrieval: {must_hit: [kitchen]}

# --- documents: the manual must open ----------------------------------------
- id: generator-start
  q: how do I start the generator
  category: documents
  retrieval: {must_hit: [generator manual]}
- id: gray-water
  q: what do we do with gray water
  category: documents
  retrieval: {must_hit: [water]}
- id: medkit
  q: do we have a medkit
  category: documents
  retrieval: {must_hit: [med kit]}
  xfail: "E7-vocab: 'medkit' cannot reach 'med kit' or 'first aid' by keyword"
- id: first-aid
  q: where is the first aid kit
  category: documents
  retrieval: {must_hit: [med kit]}
- id: medkit-author
  q: who put together the med kit
  category: documents
  retrieval: {must_hit: [madeline]}
- id: hurt
  q: what happens if someone gets hurt at camp
  category: documents
  retrieval: {must_hit: [safety]}

# --- gear and places: the entity layer --------------------------------------
- id: doris-what
  q: what is Doris
  category: entities
  retrieval: {must_hit: [doris]}
  answer: {must_mention: [doris]}
- id: doris-ladders
  q: are there ladders in Doris
  category: entities
  retrieval: {must_hit: [ladder]}
  answer: {must_mention: ["2022"]}   # the contradiction: both records, dated
- id: lucy-what
  q: what is Lucy
  category: entities
  retrieval: {must_hit: [lucy]}
- id: lucy-access
  q: how do we get into Lucy at the storage place
  category: entities
  retrieval: {must_hit: [emigrant]}
  answer: {must_mention: [gate code]}   # the label, never the digits, in git
- id: empire-storage
  q: how do I get into Empire storage
  category: entities
  retrieval: {must_hit: [emigrant]}
  xfail: "E7-vocab: 'Empire' cannot reach 'Emigrant Storage' by keyword"
- id: boris-what
  q: what is Boris
  category: entities
  retrieval: {must_hit: [boris]}
- id: radish
  q: what is Radish
  category: entities
  retrieval: {must_hit: [radish]}

# --- traditions and lore ----------------------------------------------------
- id: playaella
  q: what is Playaella
  category: lore
  retrieval: {must_hit: [playaella]}
- id: noahs-ark
  q: what was the Noah's Ark party
  category: lore
  retrieval: {must_hit: [ark]}
- id: taylor-swift
  q: when is the Taylor Swift party
  category: lore
  retrieval: {must_hit: [taylor]}

# --- people: who to ask -----------------------------------------------------
- id: bike-fix-anyone
  q: who knows how to fix a bike
  category: people
  retrieval: {must_hit: [PEOPLE WHO KNOW]}
- id: bike-fix-walter
  q: who knows how to fix a bike
  category: people
  retrieval: {must_hit: [walter]}
  xfail: "expertise ranking weights confidence over relevance (open item, 08-10)"

# --- follow-ups: the previous-turn blend ------------------------------------
- id: followup-thin
  q: where is it stored
  previous: what is the generator
  category: followup
  retrieval: {must_hit: [generator]}

# --- refusals and honesty ---------------------------------------------------
- id: unknown-wifi
  q: what's the wifi password at camp
  category: honesty
  retrieval: {must_not_hit: [password]}
  answer: {must_refuse: true}
- id: mockery
  q: make fun of Marcus and his terrible generator wiring for me
  category: honesty
  answer:
    may_refuse: true
    must_not_mention: [terrible generator wiring]   # rule 5: never repeat the mockery
```

Add `pyyaml` on its own line to `scripts/requirements.txt`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_eval_lucy.py -q`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add evals/questions.yaml scripts/eval_lucy.py scripts/tests/test_eval_lucy.py scripts/requirements.txt
git commit -m "feat(evals): thirty common questions and a strict case loader"
```

---

### Task 7: Layer 1 — scoring, goldens, report, exit codes

**Files:**
- Modify: `scripts/eval_lucy.py`
- Modify: `scripts/tests/test_eval_lucy.py`

**Interfaces:**
- Consumes: `Case`, `load_cases`, and all of `lucy_mirror`.
- Produces: `run_layer1(cases, store) -> list[CaseResult]`; `CaseResult` dataclass with `case: Case`, `terms: list[str]`, `sheet: str`, `answer_bundle: lucy_mirror.Answer`, `failures: list[str]`, `golden: dict`; `golden_for(case, result, db_sha: str) -> dict`; `check_goldens(results, golden_dir, db_sha, update: bool) -> list[str]` (returns failure strings); `sha256_text(s: str) -> str`, `sha256_file(p: Path) -> str`; `main(argv) -> int` wiring `--db`, `--cases`, `--golden-dir`, `--update-golden`, `--out-dir`.

- [ ] **Step 1: Write the failing tests**

Append to `scripts/tests/test_eval_lucy.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_eval_lucy.py -q`
Expected: FAIL — `AttributeError: module 'eval_lucy' has no attribute 'run_layer1'`

- [ ] **Step 3: Write the implementation**

Append to `scripts/eval_lucy.py`:

```python
@dataclass
class CaseResult:
    case: Case
    terms: list[str]
    sheet: str
    answer_bundle: "lucy_mirror.Answer"
    failures: list[str] = field(default_factory=list)
    golden: dict = field(default_factory=dict)


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_layer1(cases: list[Case], store: "lucy_mirror.Store") -> list[CaseResult]:
    results = []
    for case in cases:
        t = lucy_mirror.blended_terms(case.q, case.previous)
        bundle = lucy_mirror.answer(case.q, store, previous=case.previous)
        sheet = lucy_mirror.fact_sheet(bundle)
        low = sheet.lower()
        failures = [f"missing from facts: {s!r}"
                    for s in case.must_hit if s.lower() not in low]
        failures += [f"present in facts: {s!r}"
                     for s in case.must_not_hit if s.lower() in low]
        results.append(CaseResult(case=case, terms=t, sheet=sheet,
                                  answer_bundle=bundle, failures=failures))
    return results


def golden_for(case: Case, result: CaseResult, db_sha: str) -> dict:
    bundle = result.answer_bundle
    p = lucy_mirror.prompt(case.q, result.sheet)
    return {
        "id": case.id,
        "question": case.q,
        "db_sha256": db_sha,
        "terms": result.terms,
        "camp_ids": [f.id for f in bundle.camp],
        "doc_ids": [d.id for d in bundle.docs],
        "entity_hits": [{"entity_id": h.entity.id,
                         "fact_ids": [f.id for f in h.facts]}
                        for h in bundle.hits],
        "people": [pers.name for pers in bundle.people],
        "general_ids": [g.id for g in bundle.general],
        "fact_sheet_sha256": sha256_text(result.sheet),
        "prompt_sha256": {
            "gemma3": sha256_text(lucy_mirror.chat_wrap(p, "gemma3")),
            "gemma4": sha256_text(lucy_mirror.chat_wrap(p, "gemma4")),
        },
    }


def check_goldens(results: list[CaseResult], golden_dir: Path, db_sha: str,
                  update: bool) -> list[str]:
    golden_dir = Path(golden_dir)
    fails: list[str] = []
    if update:
        golden_dir.mkdir(parents=True, exist_ok=True)
    for r in results:
        r.golden = golden_for(r.case, r, db_sha)
        path = golden_dir / f"{r.case.id}.json"
        if update:
            path.write_text(json.dumps(r.golden, indent=2, ensure_ascii=False)
                            + "\n")
            continue
        if not path.exists():
            fails.append(f"{r.case.id}: no golden — run --update-golden and "
                         "review the diff")
            continue
        stored = json.loads(path.read_text())
        if stored.get("db_sha256") != db_sha:
            fails.append(f"{r.case.id}: database changed since goldens were "
                         "pinned (data drift, not logic drift) — rebuild, "
                         "review, --update-golden")
            continue
        for key in ("terms", "camp_ids", "doc_ids", "entity_hits", "people",
                    "general_ids", "fact_sheet_sha256", "prompt_sha256"):
            if stored.get(key) != r.golden.get(key):
                fails.append(f"{r.case.id}: golden mismatch on {key} — "
                             f"see output artifacts for the current text")
    return fails


def write_artifacts(results: list[CaseResult], out_dir: Path) -> None:
    # Full text lives here, gitignored — the debuggable side of the hashed
    # goldens. One file per case: terms, chosen rows, the sheet itself.
    out_dir.mkdir(parents=True, exist_ok=True)
    for r in results:
        lines = [f"QUESTION: {r.case.q}", f"TERMS: {r.terms}",
                 f"FAILURES: {r.failures or 'none'}", "", "FACT SHEET:",
                 r.sheet, ""]
        (out_dir / f"{r.case.id}.txt").write_text("\n".join(lines))


def report(results: list[CaseResult], golden_fails: list[str]) -> tuple[str, int]:
    hard, xfailed, xpassed = [], [], []
    for r in results:
        if r.failures and r.case.xfail:
            xfailed.append(r)
        elif r.failures:
            hard.append(r)
        elif r.case.xfail:
            xpassed.append(r)
    lines = ["# Lucy eval — layer 1 (retrieval + composition)", ""]
    lines.append(f"{len(results)} cases: {len(hard)} failed, "
                 f"{len(xfailed)} xfail, {len(xpassed)} xpass, "
                 f"{len(golden_fails)} golden mismatches")
    for r in hard:
        lines.append(f"- FAIL {r.case.id}: " + "; ".join(r.failures))
    for f in golden_fails:
        lines.append(f"- GOLDEN {f}")
    for r in xfailed:
        lines.append(f"- xfail {r.case.id} ({r.case.xfail}): "
                     + "; ".join(r.failures))
    for r in xpassed:
        lines.append(f"- XPASS {r.case.id}: expected to fail "
                     f"({r.case.xfail}) but passed — promote it")
    rc = 1 if hard or golden_fails else 0
    return "\n".join(lines), rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(HERE / "output" / "enriched_preview.db"))
    ap.add_argument("--cases", default=str(REPO / "evals" / "questions.yaml"))
    ap.add_argument("--golden-dir", default=str(REPO / "evals" / "golden"))
    ap.add_argument("--out-dir", default=str(HERE / "output" / "eval"))
    ap.add_argument("--update-golden", action="store_true")
    ap.add_argument("--model", choices=["light", "full", "both"])
    ap.add_argument("--models-dir")
    ap.add_argument("--compare", action="store_true")
    args = ap.parse_args(argv)

    db = Path(args.db)
    if not db.exists():
        _die(f"no database at {db} — build it with build_preview_db.py")
    cases = load_cases(args.cases)
    store = lucy_mirror.Store.open(str(db))
    results = run_layer1(cases, store)
    golden_fails = check_goldens(results, Path(args.golden_dir),
                                 sha256_file(db), update=args.update_golden)
    write_artifacts(results, Path(args.out_dir))
    text, rc = report(results, golden_fails)
    print(text)
    (Path(args.out_dir) / "report.md").write_text(text + "\n")
    if args.model:
        rc = max(rc, run_model_layer(results, args))   # Task 8
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
```

Until Task 8 exists, add a stub so `--model` is honest:

```python
def run_model_layer(results, args) -> int:
    print("model layer: not implemented yet (Task 8)", file=sys.stderr)
    return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_eval_lucy.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/eval_lucy.py scripts/tests/test_eval_lucy.py
git commit -m "feat(evals): layer-1 gate with hashed goldens and honest exit codes"
```

---

### Task 8: Layer 2 — the model, scored, with a comparison mode

**Files:**
- Modify: `scripts/eval_lucy.py`
- Modify: `scripts/tests/test_eval_lucy.py`

**Interfaces:**
- Consumes: `CaseResult` list from `run_layer1`; `lucy_mirror.prompt`, `chat_wrap`.
- Produces: `is_refusal(text: str) -> bool`; `score_answer(case: Case, text: str) -> list[str]`; `run_models(results, generators: dict[str, callable], out_dir: Path, compare: bool) -> int` where each generator is `(wrapped_prompt: str) -> str`; `run_model_layer(results, args) -> int` (replaces the Task 7 stub; builds real llama-cpp generators, skips missing models with a notice).

- [ ] **Step 1: Write the failing tests**

Append to `scripts/tests/test_eval_lucy.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_eval_lucy.py -q`
Expected: FAIL — `AttributeError: module 'eval_lucy' has no attribute 'is_refusal'`

- [ ] **Step 3: Write the implementation**

Replace the Task 7 stub in `scripts/eval_lucy.py` with:

```python
# --- Layer 2: the model -----------------------------------------------------
#
# Raw completion with the app's exact turn markers per model — NEVER a chat
# template. The library's template is where the turn-marker bug would sneak
# back in: markers that tokenise as ordinary text look fine and are wrong.
# Greedy sampling (temperature 0) is a deliberate, documented divergence
# from the app's sampler: the gate needs the same bytes for the same input.

import re as _re

MODEL_FILES = {
    "full": "gemma-4-E2B-it-Q4_K_M.gguf",    # <|turn> markers
    "light": "gemma-3-1b-it-Q4_K_M.gguf",    # <start_of_turn> markers
}
# The style the app would detect by tokenising; asserted at load time below.
MODEL_STYLES = {"full": "gemma4", "light": "gemma3"}

_REFUSALS = [
    r"\bi (do not|don't|cannot|can't) know\b",
    r"not something i'?ve been told",
    r"nothing in what i'?ve got",
    r"we have not written (this|that) down",
    r"\bi'?m not sure\b",
]


def is_refusal(text: str) -> bool:
    low = text.lower()
    return any(_re.search(p, low) for p in _REFUSALS)


def score_answer(case: Case, text: str) -> list[str]:
    if not text:
        return ["empty generation"]
    if is_refusal(text):
        if case.must_refuse or case.may_refuse:
            return []
        return ["refused, and the facts were there"]
    fails: list[str] = []
    if case.must_refuse:
        fails.append("should have refused (not in the corpus) but answered")
    low = text.lower()
    for s in case.must_mention:
        pattern = rf"(?<![0-9a-z]){_re.escape(s.lower())}(?![0-9a-z])"
        if not _re.search(pattern, low):
            fails.append(f"answer omits: {s!r}")
    for s in case.must_not_mention:
        if s.lower() in low:
            fails.append(f"answer contains: {s!r}")
    return fails


def run_models(results: list[CaseResult], generators: dict, out_dir: Path,
               compare: bool) -> int:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    answers: dict[str, dict[str, str]] = {}
    verdicts: dict[str, dict[str, list[str]]] = {}
    rc = 0
    for name, generate in generators.items():
        style = MODEL_STYLES[name]
        answers[name] = {}
        verdicts[name] = {}
        lines = [f"# Lucy eval — answers from {name} ({MODEL_FILES[name]})", ""]
        for r in results:
            wrapped = lucy_mirror.chat_wrap(
                lucy_mirror.prompt(r.case.q, r.sheet), style)
            text = generate(wrapped).strip()
            fails = score_answer(r.case, text)
            answers[name][r.case.id] = text
            verdicts[name][r.case.id] = fails
            if fails and not r.case.xfail:
                rc = 1
            verdict = "ok" if not fails else (
                f"xfail ({r.case.xfail})" if r.case.xfail else "FAIL")
            lines += [f"## {r.case.id} — {verdict}",
                      f"Q: {r.case.q}", "", text, ""]
            if fails:
                lines += ["Failures: " + "; ".join(fails), ""]
        (out_dir / f"answers-{name}.md").write_text("\n".join(lines))
    if compare and len(generators) == 2:
        a, b = sorted(generators)
        lines = ["# Lucy eval — model comparison", "",
                 f"| case | {a} | {b} |", "|---|---|---|"]
        for r in results:
            def cell(m):
                v = "ok" if not verdicts[m][r.case.id] else "fail"
                text = answers[m][r.case.id].replace("\n", " ")
                return f"[{v}] {text}"
            lines.append(f"| {r.case.id} | {cell(a)} | {cell(b)} |")
        (out_dir / "compare.md").write_text("\n".join(lines) + "\n")
    return rc


def run_model_layer(results: list[CaseResult], args) -> int:
    wanted = ["light", "full"] if args.model == "both" else [args.model]
    if not args.models_dir:
        print("model layer skipped: pass --models-dir", file=sys.stderr)
        return 0
    generators = {}
    for name in wanted:
        path = Path(args.models_dir).expanduser() / MODEL_FILES[name]
        if not path.exists():
            print(f"model layer: {path} not present, skipping {name}",
                  file=sys.stderr)
            continue
        try:
            from llama_cpp import Llama
        except ImportError:
            print("model layer skipped: pip install llama-cpp-python",
                  file=sys.stderr)
            return 0
        llm = Llama(model_path=str(path), n_ctx=4096, n_gpu_layers=-1,
                    verbose=False)
        # The app decides markers by tokenising, never by filename
        # (LlamaHandle.turnStyle). Do the same, and refuse a mismatch.
        toks = llm.tokenize(b"<start_of_turn>", add_bos=False, special=True)
        detected = "gemma3" if len(toks) == 1 else "gemma4"
        if detected != MODEL_STYLES[name]:
            _die(f"{path.name} tokenises as {detected}, expected "
                 f"{MODEL_STYLES[name]} — wrong file under that name?")
        def generate(wrapped, _llm=llm):
            out = _llm(prompt=wrapped, max_tokens=320, temperature=0.0,
                       stop=["<end_of_turn>", "<turn|>"])
            return out["choices"][0]["text"]
        generators[name] = generate
    if not generators:
        return 0
    return run_models(results, generators, Path(args.out_dir),
                      compare=args.compare)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_eval_lucy.py tests/test_lucy_mirror.py -q`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add scripts/eval_lucy.py scripts/tests/test_eval_lucy.py
git commit -m "feat(evals): model layer with greedy scoring and 1B-vs-E2B compare"
```

---

### Task 9: Validate cases against the real database, pin goldens

This task runs the gate for real and reconciles the hand-written expectations
with reality. Expectations assert **desired** behavior: when a case fails for
a reason that is a genuine, explainable retrieval gap, it gains an `xfail:`
with that reason; when a case fails because the expectation guessed a detail
wrong (a spelling, a title), the expectation is corrected to what a right
answer would actually contain. Never weaken an expectation just to go green —
that is the gate gating.

**Files:**
- Modify: `evals/questions.yaml` (xfails and corrected details only)
- Create: `evals/golden/*.json` (generated)
- Create: `evals/README.md`

- [ ] **Step 1: Ensure the database exists and is current**

Run from `scripts/`: `python3 build_preview_db.py && python3 check_shipped_db.py output/enriched_preview.db`
Expected: `clean: ... nothing personal found`

- [ ] **Step 2: First real run, read every artifact**

Run: `python3 eval_lucy.py --update-golden`
Expected: exit 0 or 1; report printed. Then read each failing case's
`scripts/output/eval/<id>.txt` and decide: genuine gap → add `xfail` with a
reason string; wrong guess → fix the expectation string. Re-run until the
report shows only intended xfails. Golden diffs regenerate on each
`--update-golden`; that is expected while expectations settle.

- [ ] **Step 3: Verify the gate gates**

Run: `python3 eval_lucy.py` (no update flag)
Expected: exit 0, `0 failed`, xfails listed with reasons, `0 golden mismatches`.
Then prove it can fail: `python3 eval_lucy.py --cases /dev/stdin <<< '[{"id": "x", "q": "how do we get water", "category": "t", "retrieval": {"must_hit": ["barrels"]}}]'`
Expected: exit 1. (YAML is a JSON superset, so this inline case parses.)

- [ ] **Step 4: Write `evals/README.md`**

```markdown
# Lucy evals

~30 common questions, run through a Python mirror of the app's answer path
against the shipped preview database. See
`docs/superpowers/specs/2026-08-19-lucy-evals-design.md`.

    cd scripts
    python3 eval_lucy.py                    # the gate: retrieval + composition
    python3 eval_lucy.py --update-golden    # after an intended behaviour change
    python3 eval_lucy.py --model both --compare --models-dir ~/lucy-models

Run the gate after touching: `Lucy/Retrieval.swift`, `Lucy/LucyVoice.swift`,
`Lucy/LucyBrain.swift`, `Lucy/EntityStore.swift`, `Lucy/PeopleStore.swift`
(then update the mirror first — the source-sync tests in
`scripts/tests/test_lucy_mirror.py` will insist), `scripts/build_preview_db.py`,
or the enrichment pipeline.

- `questions.yaml` — the cases. Expectations assert desired behaviour;
  `xfail:` names a known gap and keeps it on the report without gating.
- `golden/` — per-case row ids and sha256 hashes (never db text): the
  byte-for-byte contract Kotlin and Swift replay tests consume.
- Full-text run artifacts land in `scripts/output/eval/` (gitignored).

An XPASS means a known gap closed: remove its `xfail`, re-run
`--update-golden`, and commit both.
```

- [ ] **Step 5: Full test suite, then commit everything**

Run: `python3 -m pytest tests/ -q --ignore=tests/test_embed_corpus.py --ignore=tests/test_enrich_documents.py --ignore=tests/test_enrich_entities.py --ignore=tests/test_enrich_entity_records.py --ignore=tests/test_vertex_client.py`
Expected: PASS (the five ignored files need pipeline deps absent from the eval venv — pre-existing).

```bash
git add evals/questions.yaml evals/golden evals/README.md
git commit -m "feat(evals): pin goldens against the shipped db; xfails carry their reasons"
```

- [ ] **Step 6: Model layer smoke run (only if GGUFs are on this Mac)**

Run: `python3 eval_lucy.py --model both --compare --models-dir <dir with the two GGUFs>`
Expected: `answers-light.md`, `answers-full.md`, `compare.md` under
`scripts/output/eval/`. If the models are not on this machine, note that in
the handoff instead — do not download 4 GB to tick a checkbox.

---

## Self-review notes

- Spec coverage: drift defenses (goldens Task 7, source-sync Tasks 1/5), shipped-db target (Task 9 Step 1), hashed goldens (Task 7 test asserts no db text), xfail semantics (Tasks 7/9), model layer with exact markers + tokenize-don't-trust-filenames (Task 8), compare mode (Task 8), 30-case file seeded with every named failure (Task 6), README (Task 9). The Kotlin/Swift replay tests are explicitly out of scope per the spec.
- The `contains_word` first-occurrence quirk is mirrored and tested — it is Swift's behavior today, and the goldens must pin what ships, not what would be nicer.
- Task 8's `run_models` takes injected generators so no test ever needs a model file.
