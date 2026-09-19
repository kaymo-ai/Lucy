#!/usr/bin/env python3
"""The app's answer path, mirrored in Python for the eval gate.

Every function here is a COPY of Swift logic and names its source file.
Editing one side without the other is the failure mode this project has
already paid for once (the stop-word incident, learnings 2026-08-09), so
tests/test_lucy_mirror.py parses the Swift sources and fails on drift.

The app's model context is factSheet + chatLogSection (ChatView.swift:495).
Eval cases are fresh sessions with empty chat logs, so context == fact sheet.

Known divergences the goldens accept (see the eval plan, "Known
divergences"): Swift Dictionary iteration order is randomized where this
file uses insertion order; Swift counts grapheme clusters where this file
counts code points; Swift's sort is unstable where Python's is stable.
Python .strip() in _clean/_passage trims all whitespace where Swift's
.whitespaces trims space/tab only; Python isalpha/isnumeric vs Swift
isLetter/isNumber differ on some unicode edge cases — both irrelevant for
this corpus's prose, accepted.
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
    "know",
    "so", "no", "up", "oh", "be", "he", "us", "ok",
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
    # Retrieval.terms: split on non-alphanumerics, keep len > 1, drop stop.
    # The floor used to be > 2 and silently deleted "Oz" from every question
    # naming him; the two-letter noise words now sit in STOP explicitly.
    words = re.findall(r"[^\W_]+", query.lower())
    return [w for w in words if len(w) > 1 and w not in STOP]


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
        # Use chr(0x201C) and chr(0x201D) for left and right double quotation marks
        lines.append(f"Our {chr(0x201C)}{doc.title}{chr(0x201D)} says:")
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
        "1. Anything particular to this camp comes only from the facts "
        "below: names, dates, numbers, places, who did what, what the camp "
        "owns, what it decided, when it happens. If one of those is not "
        "below, you do not know it, and you say so rather than filling it "
        "in.\n"
        "2. What you know about the desert, the event and the ordinary "
        "world is yours to use, and reaching for it is not a failure. Keep "
        "it plainly apart from the camp's own record, so nobody takes a "
        "thing you worked out for a thing the camp wrote down. Where both "
        "bear on the question, the camp's record leads and yours follows "
        "it.\n"
        "3. Answer the question that was asked. If the asked-for detail — "
        "a kind, a place, a time, a number — is not in the facts, say so "
        "first, plainly, then give what the facts DO hold about the thing. "
        "Never answer around a gap.\n"
        "4. The camp's work is the camp's. The facts say \"we\" because "
        "the camp wrote them; you were not there. Credit doing to the camp "
        "or a named person — \"I\" is only for what you remember and say, "
        "never for building, storing, hauling or fixing.\n"
        "5. Vague question, several possible subjects: ask which they "
        "mean, naming the options in one sentence. Facts disagree: give "
        "both with dates. Someone asks who to ask: lead with the name. "
        "Asked to mock a campmate: decline warmly in your own words, "
        "without repeating the mockery or the name inside it.\n"
        "\n"
        # LucyBrain.manner(for: .default_) then lengthGuidance(for:
        # .conversational): the two dials at their shipped defaults
        # (Experiments.init falls back to exactly these). The eval mirrors
        # what ships, not the lab switches.
        "Your manner: warm, quick, a bit playful — a giant snail with a "
        "sound system, not a reference desk. Dry humour welcome; be "
        "delighted by the camp. Short sentences. Start with the answer, "
        "never with the question restated.\n"
        "\n"
        "Your length: match the answer to the question, not to how much "
        "the facts could support. Answer what was asked and stop there; a "
        "small question earns a small answer. Only carry a further detail "
        "forward when it genuinely bears on what was asked — never leave "
        "out something the person needed for the sake of brevity, and "
        "never add length the question didn't ask for either.\n"
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
