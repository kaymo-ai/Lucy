# Help Lucy: the answer half — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let camp members answer the questions Lucy could not answer, from the app offline or from the camp site with signal, and get approved answers back onto every phone as retrievable rows.

**Architecture:** A gap is not stored — it is a query over `chatlog` rows whose answer is refusal-shaped, grouped by a server-side normalisation of the question. Members answer a gap; the answer uploads carrying the **verbatim question text**, and the server assigns the `gapKey`. Approved answers sync down on their own cursor into a local SQLite store that `Retrieval` reads as ordinary `CampFact` rows, so `LucyVoice` and the prompt need no change at all.

**Tech Stack:** FastAPI + psycopg + Postgres (backend), pytest against a real throwaway Postgres, SwiftUI + SQLite3 C API (phone), XCTest, XcodeGen via `Lucy/project.yml`.

**Spec:** `docs/superpowers/specs/2026-08-10-lucy-backend-design.md`, sections **The API**, **What the app does**, **Chat logs and the gap list**, and **Help Lucy: the answer half**.

## Global Constraints

- **The `answer` table has no member column and no device column.** Matches `chatlog` and the decision that notes are anonymous. A test asserts the column set, the way `test_chatlog.py:136` does.
- **The phone never computes a `gapKey`.** It uploads the verbatim question text; the server normalises and assigns. See the spec's "`gapKey` is assigned by the server".
- **An approved answer reaches `Retrieval` as a row, never as prompt text.** This is the invariant in `CLAUDE.md`. No task may add an answer to `LucyBrain.prompt` or to any string handed to the model outside a retrieved fact.
- **Never put a quotable example sentence in `LucyBrain.prompt`.** Unchanged rule from `CLAUDE.md`; this plan does not touch the prompt at all.
- **Wire field names are camelCase.** `backend/app/models.py` renames nothing; Swift's `JSONSerialization` does no renaming either.
- **Ids are chosen by the phone** for anything the phone uploads, so a retry on a bad connection is idempotent. `ON CONFLICT (id) DO NOTHING`.
- **`seq` is assigned under `pg_advisory_xact_lock`**, mirroring `note`. Without it a client polling `?since=N` steps over a row that committed late.
- **Review defaults on.** `answer.status` defaults to `'pending'`; only `'approved'` rows are ever served down.
- **Never `git add -A`.** Stage named paths. Build dirs and a 106 MB corpus live in this repo.
- **The Simulator does not prove the phone.** Phone-side tasks end with `Lucy/device.sh` and a look at `Documents/lucy-journal.txt`.

**Backend test loop, used by every backend task:**

```bash
cd ~/Snails/backend
docker compose -f compose.test.yml up -d     # throwaway Postgres on :55432
.venv/bin/python -m pytest                   # a real database, not a fake
```

---

## File Structure

**Created:**

| Path | Responsibility |
|---|---|
| `backend/migrations/003_help_lucy.sql` | The `answer` table, and the two SQL functions that define "is this a refusal" and "how is a question normalised" once each. |
| `backend/app/answers.py` | `GET /v1/gaps`, `POST /v1/answers`, `GET /v1/answers`. The gap query lives here. |
| `backend/tests/test_answers.py` | Gap grouping, idempotency, the pending default, the missing-member-column promise. |
| `Lucy/AnswerStore.swift` | The phone's local answer database in Documents: what you typed offline, and what the camp approved. |
| `Lucy/Tests/AnswerStoreTests.swift` | Unit tests for the store and for gap detection. |
| `scripts/export_answers.py` | Pulls approved answers into the enrichment pipeline as `camp_fact` rows. |

**Modified:**

| Path | Change |
|---|---|
| `backend/app/db.py:66-70` | `reset_for_tests()` truncates `answer` too. |
| `backend/app/main.py:32` | Register the answers router. |
| `backend/app/models.py` | `GapSummary`, `GapList`, `AnswerUpload`, `AnswerStored`, `ApprovedAnswer`, `AnswerList`. |
| `backend/app/admin.py` | An approve/reject surface for pending answers. |
| `backend/app/chatlog.py:1-30` | The docstring asserts there is no read side. Rewrite it to describe the one there now is. |
| `Lucy/ChatView.swift:458-465` | `deadEnd` is a nested local function; hoist it so the view and the store can both call it. |
| `Lucy/Retrieval.swift:143-172` | `answer()` gains approved camp answers, merged into `Answer.camp`. |
| `Lucy/Sync.swift` | `uploadAnswers` and `fetchApproved`. |
| `Lucy/SyncView.swift:100-105, 182-250` | Two new sync steps, and the privacy copy that stops being true. |
| `Lucy/project.yml:140` | A `LucyTests` unit-testing target. There is only a UI-testing bundle today, so store logic has nowhere to be tested. |
| `site/index.html` | The gap list and the answer box. |

---

## Task 1: The schema, and the two rules that must exist once

**Files:**
- Create: `backend/migrations/003_help_lucy.sql`
- Modify: `backend/app/db.py:66-70`
- Test: `backend/tests/test_answers.py`

**Interfaces:**
- Consumes: nothing.
- Produces: table `answer(id UUID, seq BIGSERIAL, gap_key TEXT, question TEXT, body TEXT, status TEXT, created_at TIMESTAMPTZ)`; SQL functions `lucy_is_refusal(TEXT) -> BOOLEAN` and `lucy_normalise_question(TEXT) -> TEXT`; `ANSWER_SEQ_LOCK = 4243`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_answers.py`:

```python
"""Gaps and answers.

The gap side is a query, not a table -- so most of what is worth testing here
is that the grouping holds when the camp asks one thing in six different ways,
and that the promise chatlog made survives a feature that reads from it.
"""
import uuid

from app import db

DEVICE_A = "11111111-2222-3333-4444-555555555555"
REFUSAL = "I don't know, nothing in the camp's notes covers that."


def token(client, device=DEVICE_A):
    return client.post("/v1/join", json={
        "code": "test-code", "deviceId": device}).json()["token"]


def ask(client, tok, question, answer=REFUSAL, asked_at="2026-08-28T19:04:00Z"):
    """Puts one turn into chatlog, which is where gaps come from."""
    return client.post("/v1/chatlog", json={"entries": [{
        "id": str(uuid.uuid4()), "question": question, "answer": answer,
        "model": "gemma-4-E2B", "askedAt": asked_at}]},
        headers={"Authorization": f"Bearer {tok}"})


class TestSchema:
    def test_refusal_function_matches_the_phones_deadEnd_prefixes(self):
        # The three prefixes are deadEnd() in ChatView.swift. If that grows a
        # fourth, this test is where it is noticed.
        with db.pool.connection() as conn:
            def refusal(s):
                return conn.execute(
                    "SELECT lucy_is_refusal(%s)", (s,)).fetchone()[0]
            assert refusal("I don't know where that is")
            assert refusal("I do not know where that is")      # uncontracted
            assert refusal("I don’t know where that is")  # curly
            assert refusal("Nothing in what I've got says so")
            assert refusal("That's not something I've been told")
            assert not refusal("The barrels are in Doris.")

    def test_normalisation_collapses_phrasing_but_not_meaning(self):
        with db.pool.connection() as conn:
            def norm(s):
                return conn.execute(
                    "SELECT lucy_normalise_question(%s)", (s,)).fetchone()[0]
            assert norm("Where's the medkit?") == norm("where is the medkit")
            assert norm("WHERE IS THE MEDKIT!!") == norm("where is the medkit")
            assert norm("where is the medkit") != norm("where is the generator")
            # The contraction case. Stripping punctuation alone leaves
            # "where s the medkit", which never joins its own gap.
            assert norm("Where's the medkit?") == norm("Where is the medkit")
            # Two questions with no content words must not collapse together.
            assert norm("what is the?") != norm("who are we?")

    def test_answer_has_no_member_or_device_column(self):
        # The same promise chatlog makes, kept the same way: as a column that
        # does not exist. See test_chatlog.py.
        with db.pool.connection() as conn:
            columns = {r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = 'answer'").fetchall()}
        assert columns == {"id", "seq", "gap_key", "question", "body",
                           "status", "created_at"}
        assert not any("member" in c or "device" in c for c in columns)

    def test_status_defaults_to_pending(self):
        # Review defaults on. An answer nobody has looked at is not servable.
        with db.pool.connection() as conn:
            conn.execute(
                "INSERT INTO answer (id, gap_key, question, body)"
                " VALUES (%s, 'k', 'q', 'b')", (str(uuid.uuid4()),))
            assert conn.execute(
                "SELECT status FROM answer").fetchone()[0] == "pending"

    def test_status_rejects_a_value_nobody_defined(self):
        with db.pool.connection() as conn:
            try:
                conn.execute(
                    "INSERT INTO answer (id, gap_key, question, body, status)"
                    " VALUES (%s, 'k', 'q', 'b', 'maybe')", (str(uuid.uuid4()),))
            except Exception as e:
                assert "answer_status_check" in str(e) or "check" in str(e).lower()
            else:
                raise AssertionError("the CHECK constraint is missing")
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd ~/Snails/backend
docker compose -f compose.test.yml up -d
.venv/bin/python -m pytest tests/test_answers.py -v
```

Expected: FAIL — `relation "answer" does not exist`, `function lucy_is_refusal(text) does not exist`.

- [ ] **Step 3: Write the migration**

Create `backend/migrations/003_help_lucy.sql`:

```sql
-- Help Lucy: the answer half.
--
-- A gap is NOT a table. It is a query over chatlog rows whose answer is
-- refusal-shaped, grouped by a normalised question. Storing gaps would mean a
-- second copy of chatlog that drifts from it, and a nightly job to keep them
-- in step that nobody on playa is going to notice has stopped.
--
-- Two rules get a function each, because both already exist in more than one
-- place and the drift is silent rather than loud.

-- What counts as Lucy failing to answer.
--
-- These three prefixes ARE deadEnd() in Lucy/ChatView.swift. The normalisation
-- is that function's, character for character: lowercase, curly apostrophe to
-- straight, "do not" collapsed to "don't" -- which is how "i do not know" and
-- "i don't know" become one prefix. If deadEnd() grows a fourth phrase, it
-- grows here too, or the new refusal shape silently stops counting as a gap
-- and the camp never sees the question.
CREATE OR REPLACE FUNCTION lucy_is_refusal(a TEXT) RETURNS BOOLEAN AS $$
    SELECT replace(replace(lower(a), '’', ''''), 'do not', 'don''t')
           LIKE ANY (ARRAY['i don''t know%',
                           'nothing in what i''ve got%',
                           'that''s not something i''ve been told%']);
$$ LANGUAGE SQL IMMUTABLE;

-- How two phrasings of one question become one gap.
--
-- Server-side only, and deliberately NOT mirrored on the phone. The phone's
-- equivalent is contentWords() in ChatView.swift, which folds case through
-- Swift's Unicode rules, splits on CharacterSet.alphanumerics and returns an
-- unordered Set -- none of which this reproduces byte-for-byte, and all of
-- which the camp's own names and apostrophes will find. So the phone uploads
-- the verbatim question and this assigns the key. A hash that disagreed on one
-- codepoint would not throw; it would file an answer against a gap that does
-- not exist.
-- Mirrors contentWords() in Lucy/ChatView.swift in SPIRIT, not in bytes --
-- the phone never runs this and never has to agree with it. Same three moves:
-- split on non-alphanumerics, drop single characters, drop the same stopwords.
--
-- Dropping single characters is what makes "Where's the medkit?" and "where is
-- the medkit" one gap: the apostrophe splits "where's" into "where" and "s",
-- the stopword list takes "where", and the length test takes "s". Stripping
-- punctuation alone leaves "where s the medkit", which is its own gap forever.
--
-- Sorted, so word order does not turn one question into two.
CREATE OR REPLACE FUNCTION lucy_normalise_question(q TEXT) RETURNS TEXT AS $$
    SELECT coalesce(nullif(string_agg(w, ' ' ORDER BY w), ''),
                    -- A question with no content words at all ("what is the?").
                    -- Without this fallback every one of them hashes to the
                    -- empty string and the camp sees them as a single gap.
                    btrim(regexp_replace(lower(replace(q, '’', '''')),
                                         '[^a-z0-9]+', ' ', 'g')))
    FROM unnest(regexp_split_to_array(
             lower(replace(q, '’', '''')), '[^a-z0-9]+')) AS w
    WHERE length(w) > 1
      AND w <> ALL (ARRAY['what','whats','is','an','the','who','how','do',
                          'does','we','where','when','why','are','our','my']);
$$ LANGUAGE SQL IMMUTABLE;

CREATE OR REPLACE FUNCTION lucy_gap_key(q TEXT) RETURNS TEXT AS $$
    SELECT encode(sha256(lucy_normalise_question(q)::bytea), 'hex');
$$ LANGUAGE SQL IMMUTABLE;

-- Answers. Shaped like note, deliberately.
--
-- id comes from the phone: a retry on a bad connection must not double-store,
-- and the phone knowing the id is the whole idempotency mechanism.
--
-- seq is a cursor, assigned under an advisory lock in answers.py for the same
-- reason note.seq is: without it a phone polling ?since=N steps over a row
-- that committed late and never sees that answer again.
--
-- No member_id and no device_id. Same promise chatlog makes and the same way
-- of keeping it: a copy of this database cannot say who answered, any more
-- than it can say who asked. Adding attribution later should mean meeting this
-- comment and a failing test, not quietly extending an INSERT.
--
-- question is stored verbatim alongside gap_key, because gap_key is a hash and
-- a reviewer looking at a pending answer needs to see what was asked.
CREATE TABLE IF NOT EXISTS answer (
    id          UUID        PRIMARY KEY,
    seq         BIGSERIAL   NOT NULL UNIQUE,
    gap_key     TEXT        NOT NULL,
    question    TEXT        NOT NULL,
    body        TEXT        NOT NULL,
    status      TEXT        NOT NULL DEFAULT 'pending'
                CONSTRAINT answer_status_check
                CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS answer_gap_idx ON answer (gap_key);
-- Partial, because the down-sync only ever asks for approved rows in seq
-- order, and pending ones are the majority right after the burn.
CREATE INDEX IF NOT EXISTS answer_approved_seq_idx
    ON answer (seq) WHERE status = 'approved';
-- The gap query filters chatlog by refusal shape before it groups. Without
-- this it is a sequential scan over every turn the camp ever took.
CREATE INDEX IF NOT EXISTS chatlog_refusal_idx
    ON chatlog (lucy_gap_key(question)) WHERE lucy_is_refusal(answer);
```

- [ ] **Step 4: Let the test suite truncate the new table**

In `backend/app/db.py`, replace the body of `reset_for_tests()`:

```python
def reset_for_tests() -> None:
    """Empties every data table. Never called outside the test suite."""
    with pool.connection() as conn:
        conn.execute(
            "TRUNCATE answer, chatlog, note, device_token, member"
            " RESTART IDENTITY CASCADE")
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd ~/Snails/backend
.venv/bin/python -m pytest tests/test_answers.py -v
```

Expected: PASS, 5 tests.

- [ ] **Step 6: Run the whole suite, to prove the migration did not break sync**

```bash
.venv/bin/python -m pytest
```

Expected: PASS, everything.

- [ ] **Step 7: Commit**

```bash
cd ~/Snails
git add backend/migrations/003_help_lucy.sql backend/app/db.py backend/tests/test_answers.py
git commit -m "feat(server): the answer table, and two rules that existed twice"
```

---

## Task 2: `GET /v1/gaps` — the questions, in the words they were asked in

**Files:**
- Create: `backend/app/answers.py`
- Modify: `backend/app/models.py` (append), `backend/app/main.py:32`
- Test: `backend/tests/test_answers.py` (append `class TestGaps`)

**Interfaces:**
- Consumes: `lucy_is_refusal`, `lucy_gap_key` from Task 1; `auth.current_member`.
- Produces: `GET /v1/gaps?since=<int>&limit=<int>` returning `GapList(gaps: list[GapSummary], cursor: int)`, where `GapSummary` is `{gapKey: str, question: str, asked: int, answered: bool}`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_answers.py`:

```python
def gaps(client, tok, since=0):
    return client.get(f"/v1/gaps?since={since}",
                      headers={"Authorization": f"Bearer {tok}"}).json()


class TestGaps:
    def test_a_refused_turn_becomes_a_gap(self, client):
        tok = token(client)
        ask(client, tok, "where is the medkit")
        body = gaps(client, tok)
        assert len(body["gaps"]) == 1
        assert body["gaps"][0]["question"] == "where is the medkit"
        assert body["gaps"][0]["asked"] == 1
        assert body["gaps"][0]["answered"] is False

    def test_an_answered_turn_is_not_a_gap(self, client):
        tok = token(client)
        ask(client, tok, "where is the medkit", answer="In Doris, left side.")
        assert gaps(client, tok)["gaps"] == []

    def test_six_phrasings_are_one_gap_with_a_count_of_six(self, client):
        tok = token(client)
        for q in ["where is the medkit", "Where's the medkit?",
                  "WHERE IS THE MEDKIT", "where is the med kit".replace(" kit", "kit"),
                  "where  is   the medkit", "where is the medkit!!"]:
            ask(client, tok, q)
        body = gaps(client, tok)
        assert len(body["gaps"]) == 1
        assert body["gaps"][0]["asked"] == 6

    def test_the_verbatim_phrasing_shown_is_the_most_recent(self, client):
        # A normalised question reads like a database. The member sees words a
        # person actually typed -- the latest ones.
        tok = token(client)
        ask(client, tok, "where is the medkit", asked_at="2026-08-28T10:00:00Z")
        ask(client, tok, "Where's the medkit??", asked_at="2026-08-28T22:00:00Z")
        assert gaps(client, tok)["gaps"][0]["question"] == "Where's the medkit??"

    def test_a_gap_with_an_approved_answer_is_marked_answered(self, client):
        tok = token(client)
        ask(client, tok, "where is the medkit")
        key = gaps(client, tok)["gaps"][0]["gapKey"]
        with db.pool.connection() as conn:
            conn.execute(
                "INSERT INTO answer (id, gap_key, question, body, status)"
                " VALUES (%s, %s, 'where is the medkit', 'In Doris.', 'approved')",
                (str(uuid.uuid4()), key))
        assert gaps(client, tok)["gaps"][0]["answered"] is True

    def test_a_pending_answer_does_not_mark_it_answered(self, client):
        # Otherwise one unreviewed answer hides the gap from everyone else who
        # could have answered it properly.
        tok = token(client)
        ask(client, tok, "where is the medkit")
        key = gaps(client, tok)["gaps"][0]["gapKey"]
        with db.pool.connection() as conn:
            conn.execute(
                "INSERT INTO answer (id, gap_key, question, body)"
                " VALUES (%s, %s, 'where is the medkit', 'In Doris.')",
                (str(uuid.uuid4()), key))
        assert gaps(client, tok)["gaps"][0]["answered"] is False

    def test_requires_a_token(self, client):
        assert client.get("/v1/gaps").status_code == 401
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd ~/Snails/backend
.venv/bin/python -m pytest tests/test_answers.py::TestGaps -v
```

Expected: FAIL — 404, the route does not exist.

- [ ] **Step 3: Add the wire shapes**

Append to `backend/app/models.py`:

```python
class GapSummary(BaseModel):
    """One question the camp's knowledge does not cover.

    `question` is verbatim -- the most recent way somebody actually phrased it
    -- because a normalised question reads like a database and nobody wants to
    answer a database. `gapKey` is the grouping, and the phone never computes
    one: it uploads the question text and the server assigns this.
    """
    gapKey: str
    question: str
    asked: int
    answered: bool


class GapList(BaseModel):
    gaps: list[GapSummary]
    cursor: int
```

- [ ] **Step 4: Write the route**

Create `backend/app/answers.py`:

```python
"""Gaps, and the answers the camp gives them.

This is the read side that chatlog.py used to say did not exist. What changed
is narrower than it sounds: the promise was always about *who asked*, and that
is still a column that does not exist. What is served here is the question,
never the asker.

A gap is a query, not a table. Turning it into rows would mean a second copy of
chatlog that drifts from it and a job to keep them in step that nobody on playa
will notice has stopped.
"""
import uuid

from fastapi import APIRouter, Depends

from . import db
from .auth import current_member
from .models import GapList, GapSummary

router = APIRouter()

# Distinct from NOTE_SEQ_LOCK in notes.py. Two tables assigning seq under one
# lock would serialise against each other for no reason.
ANSWER_SEQ_LOCK = 4243


@router.get("/v1/gaps", response_model=GapList)
def list_gaps(since: int = 0, limit: int = 200,
              member_id: uuid.UUID = Depends(current_member)) -> GapList:
    """Every question Lucy could not answer, grouped and counted.

    `since` is the chatlog rowcount cursor a phone last saw, not a timestamp:
    phone clocks in the desert are not to be trusted for ordering. It is
    advisory here -- gaps are a rollup, so a client that passes 0 every time
    gets a correct answer and a slightly larger response.
    """
    limit = max(1, min(limit, 500))
    with db.pool.connection() as conn:
        # The EXISTS cannot go in the SELECT list of the grouped query
        # correlated on c.question. Postgres matches a subquery's outer
        # reference against GROUP BY as a bare column, so
        # `GROUP BY lucy_gap_key(c.question)` never satisfies a correlated
        # `c.question`, and every single call fails with "subquery uses
        # ungrouped column c.question from outer query". Wrapping the
        # aggregation in a subquery gives the EXISTS a real output column
        # (t.gap_key) to correlate on. Verified against Postgres 16.
        rows = conn.execute(
            "SELECT t.gap_key, t.question, t.asked,"
            "       EXISTS (SELECT 1 FROM answer a"
            "               WHERE a.gap_key = t.gap_key"
            "                 AND a.status = 'approved')"
            " FROM ("
            "   SELECT lucy_gap_key(c.question) AS gap_key,"
            # The most recent phrasing, not an arbitrary one. array_agg with
            # its own ORDER BY sorts inside the group, so [1] is the newest --
            # a bare max() would give the alphabetically largest question.
            "          (array_agg(c.question ORDER BY c.asked_at DESC))[1]"
            "              AS question,"
            "          count(*)::int AS asked,"
            "          max(c.asked_at) AS last_asked"
            "   FROM chatlog c"
            "   WHERE lucy_is_refusal(c.answer)"
            "   GROUP BY lucy_gap_key(c.question)"
            " ) t"
            " ORDER BY t.asked DESC, t.last_asked DESC"
            " LIMIT %s", (limit,)).fetchall()
    gaps = [GapSummary(gapKey=r[0], question=r[1], asked=r[2], answered=r[3])
            for r in rows]
    return GapList(gaps=gaps, cursor=since)
```

- [ ] **Step 5: Register the router**

In `backend/app/main.py`, change the import and add the include. The site router stays last — it ends in a catch-all `GET /{path:path}`.

```python
from . import admin, answers, auth, chatlog, db, notes, site
```

```python
app.include_router(auth.router)
app.include_router(notes.router)
app.include_router(chatlog.router)
app.include_router(answers.router)
app.include_router(admin.router)
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_answers.py -v
```

Expected: PASS, 12 tests.

- [ ] **Step 7: Commit**

```bash
cd ~/Snails
git add backend/app/answers.py backend/app/models.py backend/app/main.py backend/tests/test_answers.py
git commit -m "feat(server): the gap list — grouped, counted, in the camp's own words"
```

---

## Task 3: `POST /v1/answers` — the phone sends the question, the server assigns the key

**Files:**
- Modify: `backend/app/answers.py`, `backend/app/models.py`
- Test: `backend/tests/test_answers.py` (append `class TestUploadAnswers`)

**Interfaces:**
- Consumes: `ANSWER_SEQ_LOCK`, `lucy_gap_key` from Tasks 1-2.
- Produces: `POST /v1/answers` taking `AnswerUpload(answers: list[AnswerEntry])` where `AnswerEntry` is `{id: UUID, question: str, body: str}`, returning `AnswerStored(stored: int)`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_answers.py`:

```python
def send_answer(client, tok, question, body, answer_id=None):
    return client.post("/v1/answers", json={"answers": [{
        "id": answer_id or str(uuid.uuid4()),
        "question": question, "body": body}]},
        headers={"Authorization": f"Bearer {tok}"})


class TestUploadAnswers:
    def test_stores_an_answer_and_counts_it(self, client):
        r = send_answer(client, token(client), "where is the medkit", "In Doris.")
        assert r.status_code == 200
        assert r.json() == {"stored": 1}

    def test_the_server_assigns_the_gap_key_from_the_question(self, client):
        # The phone sent no key at all. This is the whole point: Swift and
        # Postgres never have to agree on a normalisation byte-for-byte.
        tok = token(client)
        ask(client, tok, "Where's the medkit?")
        key = gaps(client, tok)["gaps"][0]["gapKey"]
        send_answer(client, tok, "where is the medkit", "In Doris.")
        with db.pool.connection() as conn:
            stored = conn.execute("SELECT gap_key FROM answer").fetchone()[0]
        assert stored == key

    def test_a_retry_with_the_same_id_stores_nothing_and_succeeds(self, client):
        tok = token(client)
        fixed = str(uuid.uuid4())
        assert send_answer(client, tok, "q", "b", answer_id=fixed).json() == {"stored": 1}
        second = send_answer(client, tok, "q", "b", answer_id=fixed)
        assert second.status_code == 200
        assert second.json() == {"stored": 0}
        with db.pool.connection() as conn:
            assert conn.execute("SELECT count(*) FROM answer").fetchone()[0] == 1

    def test_it_lands_pending_not_approved(self, client):
        send_answer(client, token(client), "q", "b")
        with db.pool.connection() as conn:
            assert conn.execute("SELECT status FROM answer").fetchone()[0] == "pending"

    def test_two_members_answers_are_indistinguishable(self, client):
        # Belt to the schema test's braces, exactly as test_chatlog.py does it.
        a = token(client, DEVICE_A)
        b = token(client, "99999999-8888-7777-6666-555555555555")
        assert send_answer(client, a, "q", "from A").json() == {"stored": 1}
        assert send_answer(client, b, "q", "from B").json() == {"stored": 1}
        with db.pool.connection() as conn:
            rows = conn.execute("SELECT * FROM answer").fetchall()
        # Without this, an empty `answer` table -- the route silently dropping
        # every write -- makes flattened == "" and both assertions below pass
        # vacuously, proving nothing. The two writes must actually have landed
        # before the absence of a device id means anything at all.
        assert len(rows) == 2
        flattened = " ".join(str(v) for row in rows for v in row)
        assert DEVICE_A not in flattened
        assert "99999999" not in flattened

    def test_requires_a_token(self, client):
        assert client.post("/v1/answers", json={"answers": []}).status_code == 401
```

- [ ] **Step 2: Run it to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_answers.py::TestUploadAnswers -v
```

Expected: FAIL — 404.

- [ ] **Step 3: Add the wire shapes**

Append to `backend/app/models.py`:

```python
class AnswerEntry(BaseModel):
    """One answer a member gave to a gap.

    No gapKey. The phone sends the question it was answering, verbatim, and the
    server normalises once on arrival -- see the spec's "`gapKey` is assigned by
    the server, and the phone never computes one".

    4000 characters is a long paragraph and nowhere near a document. An answer
    longer than that is somebody pasting the camp manual into a text box, which
    belongs in the corpus, not here.
    """
    id: uuid.UUID
    question: str = Field(max_length=8000)
    body: str = Field(min_length=1, max_length=4000)


class AnswerUpload(BaseModel):
    answers: list[AnswerEntry] = Field(max_length=200)


class AnswerStored(BaseModel):
    stored: int
```

- [ ] **Step 4: Write the route**

Append to `backend/app/answers.py`:

```python
@router.post("/v1/answers", response_model=AnswerStored)
def upload_answers(body: AnswerUpload,
                   member_id: uuid.UUID = Depends(current_member)) -> AnswerStored:
    """Answers a member typed, here or on the site.

    `member_id` is a dependency and never a value that gets written. Auth opens
    the door and is then dropped on the floor, the same way chatlog does it.
    """
    stored = 0
    with db.pool.connection() as conn:
        with conn.transaction():
            # Serialised with commit, so a phone polling ?since=N cannot step
            # over a row that got a lower seq but committed later. At a camp's
            # write rate this costs nothing.
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (ANSWER_SEQ_LOCK,))
            for entry in body.answers:
                result = conn.execute(
                    "INSERT INTO answer (id, gap_key, question, body)"
                    " VALUES (%s, lucy_gap_key(%s), %s, %s)"
                    " ON CONFLICT (id) DO NOTHING",
                    (str(entry.id), entry.question, entry.question, entry.body))
                stored += result.rowcount
    return AnswerStored(stored=stored)
```

Update the imports at the top of `backend/app/answers.py`:

```python
from .models import AnswerStored, AnswerUpload, GapList, GapSummary
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_answers.py -v
```

Expected: PASS, 18 tests.

- [ ] **Step 6: Commit**

```bash
cd ~/Snails
git add backend/app/answers.py backend/app/models.py backend/tests/test_answers.py
git commit -m "feat(server): answers upload carrying the question, not a key"
```

---

## Task 4: `GET /v1/answers?since=` — approved only, on its own cursor

**Files:**
- Modify: `backend/app/answers.py`, `backend/app/models.py`
- Test: `backend/tests/test_answers.py` (append `class TestDownloadAnswers`)

**Interfaces:**
- Consumes: everything from Tasks 1-3.
- Produces: `GET /v1/answers?since=<int>&limit=<int>` returning `AnswerList(answers: list[ApprovedAnswer], cursor: int)`, where `ApprovedAnswer` is `{id: UUID, seq: int, gapKey: str, question: str, body: str}`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_answers.py`:

```python
def approve_all():
    with db.pool.connection() as conn:
        conn.execute("UPDATE answer SET status = 'approved'")


def download(client, tok, since=0):
    return client.get(f"/v1/answers?since={since}",
                      headers={"Authorization": f"Bearer {tok}"}).json()


class TestDownloadAnswers:
    def test_pending_answers_are_never_served(self, client):
        # Review defaults on. This is the test that says so.
        tok = token(client)
        send_answer(client, tok, "where is the medkit", "In Doris.")
        assert download(client, tok)["answers"] == []

    def test_approved_answers_are_served(self, client):
        tok = token(client)
        send_answer(client, tok, "where is the medkit", "In Doris.")
        approve_all()
        body = download(client, tok)
        assert len(body["answers"]) == 1
        assert body["answers"][0]["body"] == "In Doris."
        assert body["answers"][0]["question"] == "where is the medkit"

    def test_rejected_answers_are_never_served(self, client):
        tok = token(client)
        send_answer(client, tok, "q", "a joke")
        with db.pool.connection() as conn:
            conn.execute("UPDATE answer SET status = 'rejected'")
        assert download(client, tok)["answers"] == []

    def test_the_cursor_advances_and_the_next_call_is_empty(self, client):
        tok = token(client)
        send_answer(client, tok, "q1", "b1")
        send_answer(client, tok, "q2", "b2")
        approve_all()
        first = download(client, tok)
        assert len(first["answers"]) == 2
        assert download(client, tok, since=first["cursor"])["answers"] == []

    def test_the_cursor_holds_when_nothing_is_new(self, client):
        # Rather than resetting to zero and re-reporting the week as unseen,
        # exactly as notes.py:117 does it.
        tok = token(client)
        assert download(client, tok, since=99)["cursor"] == 99

    def test_a_row_flipped_to_rejected_after_approval_is_not_served(self, client):
        # Without this test the suite never exercises the status filter at all:
        # approved_seq is NULL on everything unapproved, `NULL > n` already
        # excludes it, and "AND status = 'approved'" in the route's WHERE
        # clause never has to do any work -- the suite would pass identically
        # with that conjunct deleted. Task 5's reject flow can flip status on a
        # row that was already approved (and so already has an approved_seq)
        # without clearing the column, and this is the only state where the
        # status conjunct is load-bearing.
        tok = token(client)
        aid = str(uuid.uuid4())
        send_answer(client, tok, "q", "a joke", answer_id=aid)
        approve(aid)
        with db.pool.connection() as conn:
            conn.execute("UPDATE answer SET status = 'rejected' WHERE id = %s",
                         (aid,))
        assert download(client, tok)["answers"] == []

    def test_approving_later_delivers_it_to_a_phone_already_past_it(self, client):
        # A is uploaded first, so A's seq is lower than B's. B is approved
        # first and synced, then A is approved afterward. If the route
        # filtered on upload seq instead of approved_seq, the cursor recorded
        # right after B's download would already sit above A's (lower) seq,
        # and A -- approved only afterward -- would be permanently invisible
        # to a phone that had already synced past it: exactly the loss this
        # design exists to prevent.
        #
        # A single-answer version of this test cannot show that: with nothing
        # else in the table, approved_seq(1) > 0 and seq(1) > 0 agree, so it
        # would pass identically whichever column the route ordered by. Two
        # answers, approved out of upload order, is what makes the two
        # designs disagree.
        tok = token(client)
        a_id, b_id = str(uuid.uuid4()), str(uuid.uuid4())
        send_answer(client, tok, "q1", "b1", answer_id=a_id)
        send_answer(client, tok, "q2", "b2", answer_id=b_id)
        approve(b_id)
        cursor = download(client, tok)["cursor"]
        approve(a_id)
        answers = download(client, tok, since=cursor)["answers"]
        assert len(answers) == 1
        assert answers[0]["body"] == "b1"

    def test_requires_a_token(self, client):
        assert client.get("/v1/answers").status_code == 401
```

- [ ] **Step 2: Run it to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_answers.py::TestDownloadAnswers -v
```

Expected: FAIL — 404.

- [ ] **Step 3: Note what `test_approving_later` forces**

An answer's `seq` is assigned when it is uploaded, but it only becomes servable when it is approved — which can be days later, after phones have already synced past that `seq`. Filtering on the upload `seq` alone would lose it forever. So the table needs an **approval** cursor, not an upload one. Add it to the migration:

Create `backend/migrations/004_approved_seq.sql`. A NEW file, not an edit to
`003_help_lucy.sql` — `db.migrate()` records applied migrations by filename, so
appending to a file that already ran means the new DDL never executes anywhere
it matters:

```sql
-- The cursor a phone polls is the moment of APPROVAL, not of upload.
--
-- seq is assigned when the answer arrives, but an answer becomes servable only
-- when a human approves it, which can be days later -- by then every phone has
-- synced past that seq and would never see it. approved_seq is assigned under
-- the same advisory lock at the moment of approval, so the down-sync orders by
-- the thing that actually changed.
ALTER TABLE answer ADD COLUMN IF NOT EXISTS approved_seq BIGINT;
CREATE SEQUENCE IF NOT EXISTS answer_approved_seq_counter;
CREATE INDEX IF NOT EXISTS answer_approved_cursor_idx
    ON answer (approved_seq) WHERE status = 'approved';
```

Update `test_answer_has_no_member_or_device_column` in `TestSchema` to expect the new column:

```python
        assert columns == {"id", "seq", "gap_key", "question", "body",
                           "status", "created_at", "approved_seq"}
```

- [ ] **Step 4: Add the wire shapes**

Append to `backend/app/models.py`:

```python
class ApprovedAnswer(BaseModel):
    """One approved answer, on its way down to a phone.

    `question` rides along so the phone can match it against what somebody
    asks, using the phone's own contentWords() -- the same content-word
    matching a repeat question already uses. `gapKey` is opaque to the phone;
    it is carried so an answer can be recognised as the same one on a later
    sync without comparing prose.
    """
    id: uuid.UUID
    seq: int
    gapKey: str
    question: str
    body: str


class AnswerList(BaseModel):
    answers: list[ApprovedAnswer]
    cursor: int
```

- [ ] **Step 5: Write the route**

Append to `backend/app/answers.py`:

```python
@router.get("/v1/answers", response_model=AnswerList)
def list_answers(since: int = 0, limit: int = 200,
                 member_id: uuid.UUID = Depends(current_member)) -> AnswerList:
    """Approved answers this phone has not seen.

    Ordered by approved_seq and not by seq: see the migration's comment. An
    answer approved a week after it was uploaded still reaches a phone that
    synced in between.
    """
    limit = max(1, min(limit, 500))
    with db.pool.connection() as conn:
        rows = conn.execute(
            "SELECT id, approved_seq, gap_key, question, body FROM answer"
            " WHERE status = 'approved' AND approved_seq > %s"
            " ORDER BY approved_seq LIMIT %s", (since, limit)).fetchall()
    answers = [ApprovedAnswer(id=r[0], seq=r[1], gapKey=r[2], question=r[3],
                              body=r[4]) for r in rows]
    # Holds when nothing is new, rather than resetting to zero and re-reporting
    # everything as unseen.
    return AnswerList(answers=answers,
                      cursor=answers[-1].seq if answers else since)
```

Update the imports at the top of `backend/app/answers.py`:

```python
from .models import (AnswerList, AnswerStored, AnswerUpload, ApprovedAnswer,
                     GapList, GapSummary)
```

- [ ] **Step 6: Make the test helper stamp `approved_seq`**

The `approve_all()` helper writes status directly, so it must assign the cursor the same way Task 5's real approval will:

```python
def approve_all():
    with db.pool.connection() as conn:
        conn.execute(
            "UPDATE answer SET status = 'approved',"
            " approved_seq = nextval('answer_approved_seq_counter')"
            " WHERE status <> 'approved'")


def approve(*answer_ids):
    """Approves specific rows, in the order given, one nextval() each --
    so the caller controls which row gets the higher approved_seq."""
    with db.pool.connection() as conn:
        for aid in answer_ids:
            conn.execute(
                "UPDATE answer SET status = 'approved',"
                " approved_seq = nextval('answer_approved_seq_counter')"
                " WHERE id = %s", (aid,))
```

- [ ] **Step 7: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_answers.py -v
```

Expected: PASS, 25 tests.

- [ ] **Step 8: Commit**

```bash
cd ~/Snails
git add backend/app/answers.py backend/app/models.py backend/migrations/003_help_lucy.sql backend/tests/test_answers.py
git commit -m "feat(server): approved answers ride an approval cursor, not an upload one"
```

---

## Task 5: Approving and rejecting, in the admin that already exists

**Files:**
- Modify: `backend/app/admin.py`
- Test: `backend/tests/test_answers.py` (append `class TestApproval`)

**Interfaces:**
- Consumes: `answer` table, `answer_approved_seq_counter`, `admin.valid`.
- Produces: `GET /admin/answers` (an HTML list of pending answers), `POST /admin/answers/{answer_id}/approve`, `POST /admin/answers/{answer_id}/reject`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_answers.py`:

```python
class TestApproval:
    def _pending_id(self, client, tok):
        send_answer(client, tok, "where is the medkit", "In Doris.")
        with db.pool.connection() as conn:
            return str(conn.execute("SELECT id FROM answer").fetchone()[0])

    def _admin(self, client):
        # Mirrors test_site.py:217. follow_redirects=False on purpose: the
        # cookie arrives on the 303 itself, and following it costs a request
        # that proves nothing. The assert is here so a cookie that did not
        # stick fails saying so, instead of every approval test failing with a
        # 401 that reads like a broken route.
        r = client.post("/v1/admin-unlock", data={"code": "test-admin-code"},
                        follow_redirects=False)
        assert r.status_code == 303, "admin unlock did not set the cookie"
        return client

    def test_approving_sets_status_and_stamps_the_cursor(self, client):
        tok = token(client)
        answer_id = self._pending_id(client, tok)
        r = self._admin(client).post(f"/admin/answers/{answer_id}/approve")
        assert r.status_code in (200, 303)
        with db.pool.connection() as conn:
            status, seq = conn.execute(
                "SELECT status, approved_seq FROM answer").fetchone()
        assert status == "approved"
        assert seq is not None

    def test_an_approved_answer_reaches_the_phone(self, client):
        tok = token(client)
        answer_id = self._pending_id(client, tok)
        self._admin(client).post(f"/admin/answers/{answer_id}/approve")
        assert len(download(client, tok)["answers"]) == 1

    def test_rejecting_never_reaches_the_phone(self, client):
        tok = token(client)
        answer_id = self._pending_id(client, tok)
        self._admin(client).post(f"/admin/answers/{answer_id}/reject")
        assert download(client, tok)["answers"] == []

    def test_approving_twice_does_not_move_the_cursor(self, client):
        # Otherwise a double-click re-delivers the same answer to every phone.
        tok = token(client)
        answer_id = self._pending_id(client, tok)
        admin = self._admin(client)
        admin.post(f"/admin/answers/{answer_id}/approve")
        with db.pool.connection() as conn:
            first = conn.execute("SELECT approved_seq FROM answer").fetchone()[0]
        admin.post(f"/admin/answers/{answer_id}/approve")
        with db.pool.connection() as conn:
            assert conn.execute(
                "SELECT approved_seq FROM answer").fetchone()[0] == first

    def test_approving_needs_the_admin_cookie(self, client):
        tok = token(client)
        answer_id = self._pending_id(client, tok)
        r = client.post(f"/admin/answers/{answer_id}/approve")
        assert r.status_code in (401, 403)
```

- [ ] **Step 2: Run it to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_answers.py::TestApproval -v
```

Expected: FAIL — 404.

- [ ] **Step 3: Add the routes**

Append to `backend/app/admin.py`, after the existing `memo` route:

```python
@router.get("/admin/answers", response_class=HTMLResponse)
def pending_answers(request: Request) -> HTMLResponse:
    """What the camp has offered and nobody has looked at yet.

    Review is not ceremony here. An answer becomes something Lucy states to
    forty people, and the camp's register is jokes -- the refusal filter in
    ChatView exists because of that. This page is the gate.
    """
    _require(request)
    with db.pool.connection() as conn:
        rows = conn.execute(
            "SELECT id, question, body, created_at FROM answer"
            " WHERE status = 'pending' ORDER BY created_at").fetchall()
    return HTMLResponse(_answers_page(rows))


@router.post("/admin/answers/{answer_id}/approve")
def approve_answer(answer_id: str, request: Request) -> Response:
    _require(request)
    with db.pool.connection() as conn:
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (ANSWER_SEQ_LOCK,))
            # `status <> 'approved'` makes a double-click harmless: without it
            # a second approval stamps a fresh approved_seq and every phone
            # downloads the same answer again.
            conn.execute(
                "UPDATE answer SET status = 'approved',"
                " approved_seq = nextval('answer_approved_seq_counter')"
                " WHERE id = %s AND status <> 'approved'", (answer_id,))
    return RedirectResponse("/admin/answers", status_code=303)


@router.post("/admin/answers/{answer_id}/reject")
def reject_answer(answer_id: str, request: Request) -> Response:
    """Rejected, not deleted. A rejected answer is the record that somebody
    tried and the camp said no, and deleting it invites the same answer again
    next week from the same person."""
    _require(request)
    with db.pool.connection() as conn:
        conn.execute("UPDATE answer SET status = 'rejected' WHERE id = %s",
                     (answer_id,))
    return RedirectResponse("/admin/answers", status_code=303)


def _answers_page(rows) -> str:
    if not rows:
        return ("<html><body style='font:16px system-ui;padding:2rem'>"
                "<h1>Nothing waiting</h1>"
                "<p>Every answer the camp has given has been looked at.</p>"
                "</body></html>")
    items = "".join(
        f"<li style='margin:0 0 1.5rem'>"
        f"<div style='color:#666'>{html.escape(r[1])}</div>"
        f"<div style='font-size:1.2em;margin:.3rem 0'>{html.escape(r[2])}</div>"
        f"<form method='post' action='/admin/answers/{r[0]}/approve'"
        f" style='display:inline'><button>Approve</button></form> "
        f"<form method='post' action='/admin/answers/{r[0]}/reject'"
        f" style='display:inline'><button>Reject</button></form>"
        f"</li>" for r in rows)
    return ("<html><body style='font:16px system-ui;padding:2rem;max-width:40rem'>"
            f"<h1>{len(rows)} to look at</h1><ul style='list-style:none;padding:0'>"
            f"{items}</ul></body></html>")
```

`backend/app/admin.py` already imports `html` and `HTMLResponse` / `RedirectResponse`
(lines 17 and 22) and already escapes with `html.escape` at lines 194 and 215 —
follow that, do not hand-roll an escaper. The one import to add:

```python
from .answers import ANSWER_SEQ_LOCK
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_answers.py -v
```

Expected: PASS, 30 tests.

- [ ] **Step 5: Run the whole suite**

```bash
.venv/bin/python -m pytest
```

Expected: PASS, everything.

- [ ] **Step 6: Rewrite the chatlog docstring, which now says something false**

`backend/app/chatlog.py:1-30` opens "Chat logs go up private -- nobody reads anybody's questions" and later "There is no GET." Both were true and are not. Replace the module docstring's first two paragraphs with:

```python
"""Chat logs: upload only, and the promise that survived.

Chat logs go up with no author. That is the whole of the privacy and it is
enforced by shape: the chatlog table has no member column (see
migrations/002_chatlog.sql), a schema test keeps it that way, and a copy of
this database cannot say who asked what. `member_id` below is a dependency,
never a value that gets written.

What is NOT private, since 2026-08-19, is the question. Refusal-shaped turns
are served back to the camp verbatim by GET /v1/gaps in answers.py, because a
question nobody can see is a question nobody can answer. There is still no GET
here: nothing serves a chat turn with its answer attached, and nothing ever
serves the asker.

The refusal test that decides what counts as a gap is lucy_is_refusal() in
migrations/003_help_lucy.sql. It is the same three prefixes as deadEnd() in
Lucy/ChatView.swift, normalised the same way. If deadEnd() grows a fourth, that
function grows the same one, or the new refusal shape silently stops counting
as a gap.
"""
```

Delete the SQL gap-mining query that follows it — `answers.py` is that query now, and a stale copy in a docstring is a second definition waiting to drift.

- [ ] **Step 7: Commit**

```bash
cd ~/Snails
git add backend/app/admin.py backend/app/chatlog.py backend/tests/test_answers.py
git commit -m "feat(server): the gate — approve, reject, and a docstring that stopped being true"
```

---

## Task 6: `AnswerStore` — the phone's own copy

**Files:**
- Create: `Lucy/AnswerStore.swift`, `Lucy/Tests/AnswerStoreTests.swift`
- Modify: `Lucy/project.yml:140`

**Interfaces:**
- Consumes: `Retrieval.terms(_:)` and `containsWord(_:_:)` from `Lucy/Retrieval.swift`.
- Produces:
  - `struct CampAnswer { let id: UUID; let gapKey: String; let question: String; let body: String }`
  - `AnswerStore.shared.record(question: String, body: String)` — an answer typed on this phone.
  - `AnswerStore.shared.pendingUpload(limit: Int = 200) -> [(rowid: Int64, id: UUID, question: String, body: String)]`
  - `AnswerStore.shared.markUploaded(rowids: [Int64])`
  - `AnswerStore.shared.store(approved: [CampAnswer])`
  - `AnswerStore.shared.search(terms: [String], limit: Int = 3) -> [CampFact]`

- [ ] **Step 1: Add a unit-testing target**

There is only a UI-testing bundle today (`Lucy/project.yml:140`), so store logic has nowhere to be tested. Add, after the `LucyUITests` block:

```yaml
  LucyTests:
    type: bundle.unit-testing
    platform: iOS
    sources: [Tests]
    dependencies:
      - target: Lucy
    settings:
      base:
        PRODUCT_BUNDLE_IDENTIFIER: ai.kaymo.LucyTests
        TEST_HOST: "$(BUILT_PRODUCTS_DIR)/Lucy.app/$(BUNDLE_EXECUTABLE_FOLDER_PATH)/Lucy"
        BUNDLE_LOADER: "$(TEST_HOST)"
```

- [ ] **Step 2: Write the failing test**

Create `Lucy/Tests/AnswerStoreTests.swift`:

```swift
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
```

- [ ] **Step 3: Run it to verify it fails**

```bash
cd ~/Snails/Lucy
xcodegen generate
xcodebuild test -scheme Lucy -destination 'platform=iOS Simulator,name=iPhone 16' \
  -only-testing:LucyTests 2>&1 | tail -30
```

Expected: FAIL — `cannot find 'AnswerStore' in scope`.

- [ ] **Step 4: Write the store**

Create `Lucy/AnswerStore.swift`:

```swift
import Foundation
import SQLite3

// What the camp told her.
//
// Two kinds of row live here and the difference matters. `mine` is an answer
// typed on this phone -- it works offline, immediately, unreviewed, because it
// is yours and Lucy saying it back to you crosses no trust boundary. Everything
// else arrived approved from the camp server and is a fact for everyone.
//
// Deliberately a separate database from lucy-memory.db and from the shipped
// enriched_preview.db. The shipped one is read-only and replaced wholesale by
// an update; this one must survive updates, like ChatLog's. Keeping it apart
// from ChatLog is the conceptual split: what you ASKED against what the camp
// ANSWERED.
//
// A row here reaches the model only through Retrieval, as a CampFact, which is
// the invariant in CLAUDE.md: retrieval supplies the facts, the model phrases
// them. Nothing in this file is ever pasted into a prompt.

struct CampAnswer {
    let id: UUID
    let gapKey: String
    let question: String
    let body: String
}

/// Answers, on disk.
@MainActor
final class AnswerStore {
    static let shared = AnswerStore()

    private var db: OpaquePointer?

    private static var path: String {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("lucy-answers.db").path
    }

    private init() {
        guard sqlite3_open_v2(Self.path, &db,
                              SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE, nil) == SQLITE_OK
        else {
            print("LUCY: answers open failed at \(Self.path)")
            db = nil
            return
        }
        // `answer_id` is the server's id for a row that came down, and NULL
        // for one typed here that has not been uploaded yet. UNIQUE on it is
        // what makes two syncs of the same answer store one row -- SQLite
        // treats NULLs as distinct, so locally-typed rows are unaffected.
        exec("""
            CREATE TABLE IF NOT EXISTS answer (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                answer_id  TEXT UNIQUE,
                gap_key    TEXT,
                question   TEXT NOT NULL,
                body       TEXT NOT NULL,
                mine       INTEGER NOT NULL DEFAULT 0,
                uploaded   INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            );
            """)
        exec("CREATE INDEX IF NOT EXISTS answer_pending ON answer(uploaded) WHERE mine = 1;")
    }

    private func exec(_ sql: String) {
        guard let db else { return }
        var err: UnsafeMutablePointer<CChar>?
        if sqlite3_exec(db, sql, nil, nil, &err) != SQLITE_OK, let err {
            print("LUCY: answers exec failed — \(String(cString: err))")
            sqlite3_free(err)
        }
    }

    // MARK: - Writing

    /// An answer typed on this phone. Serves this phone at once; reaches the
    /// camp only after a Sync and a human approving it.
    func record(question: String, body: String) {
        guard let db, !question.isEmpty, !body.isEmpty else { return }
        var stmt: OpaquePointer?
        let sql = """
            INSERT INTO answer (question, body, mine, uploaded, created_at)
            VALUES (?, ?, 1, 0, ?);
            """
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return }
        defer { sqlite3_finalize(stmt) }
        sqlite3_bind_text(stmt, 1, (question as NSString).utf8String, -1, nil)
        sqlite3_bind_text(stmt, 2, (body as NSString).utf8String, -1, nil)
        sqlite3_bind_double(stmt, 3, Date().timeIntervalSince1970)
        sqlite3_step(stmt)
    }

    /// Approved answers that came down on Sync. `mine` stays 0, so they are
    /// never queued back up.
    func store(approved: [CampAnswer]) {
        guard let db, !approved.isEmpty else { return }
        for a in approved {
            var stmt: OpaquePointer?
            let sql = """
                INSERT OR IGNORE INTO answer
                    (answer_id, gap_key, question, body, mine, uploaded, created_at)
                VALUES (?, ?, ?, ?, 0, 1, ?);
                """
            guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { continue }
            sqlite3_bind_text(stmt, 1, (a.id.uuidString as NSString).utf8String, -1, nil)
            sqlite3_bind_text(stmt, 2, (a.gapKey as NSString).utf8String, -1, nil)
            sqlite3_bind_text(stmt, 3, (a.question as NSString).utf8String, -1, nil)
            sqlite3_bind_text(stmt, 4, (a.body as NSString).utf8String, -1, nil)
            sqlite3_bind_double(stmt, 5, Date().timeIntervalSince1970)
            sqlite3_step(stmt)
            sqlite3_finalize(stmt)
        }
    }

    // MARK: - Uploading

    /// Answers typed here that the camp has not acked. The rowid rides along
    /// because `markUploaded` needs it back, same idiom as ChatLog.
    /// `device` is injectable so this is testable off a phone: the default
    /// reaches Identity.shared, which reads the keychain, and a unit-test host
    /// has no keychain entitlement. Same shape as the injectable clock the
    /// android port gave ChatLog.
    func pendingUpload(limit: Int = 200, device: UUID? = nil)
        -> [(rowid: Int64, id: UUID, question: String, body: String)]
    {
        guard let db else { return [] }
        var stmt: OpaquePointer?
        let sql = """
            SELECT id, question, body FROM answer
            WHERE mine = 1 AND uploaded = 0 ORDER BY id ASC LIMIT \(limit);
            """
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        defer { sqlite3_finalize(stmt) }
        var out: [(rowid: Int64, id: UUID, question: String, body: String)] = []
        while sqlite3_step(stmt) == SQLITE_ROW {
            let rowid = sqlite3_column_int64(stmt, 0)
            out.append((
                rowid: rowid,
                // Derived, not random: a retry after a dead connection must
                // send the same id or the server stores the answer twice.
                // Same UUIDv5-by-hand idiom as Capture.derivedID.
                id: Self.uploadID(rowid: rowid, device: device),
                question: sqlite3_column_text(stmt, 1).map { String(cString: $0) } ?? "",
                body: sqlite3_column_text(stmt, 2).map { String(cString: $0) } ?? ""))
        }
        return out
    }

    /// Stable across retries and unique across phones.
    static func uploadID(rowid: Int64, device: UUID? = nil) -> UUID {
        let id = device ?? Identity.shared.deviceID
        return Capture.derivedID(device: id, folder: "answer-\(rowid)")
    }

    /// Called only after the server said it stored the batch.
    func markUploaded(rowids: [Int64]) {
        guard !rowids.isEmpty else { return }
        let list = rowids.map(String.init).joined(separator: ",")
        exec("UPDATE answer SET uploaded = 1 WHERE id IN (\(list));")
    }

    // MARK: - Reading

    /// Answers bearing on this question, as CampFacts.
    ///
    /// CampFact and not a new type on purpose: LucyVoice already composes camp
    /// facts, so an answer needs no new branch anywhere downstream, and cannot
    /// accidentally become a second prose path into the prompt.
    ///
    /// Both sides are matched, and only the body is ever returned as the fact.
    ///
    /// Matching the question is not optional: somebody asked "where is the
    /// medkit" and the answer is "In Doris, left side" -- which contains no
    /// word of the question. Search the body alone and the answer is
    /// unreachable by the only words anyone will ever look for it with, which
    /// defeats the entire feature.
    ///
    /// What keeps her from parroting the question back is the CampFact shape,
    /// not the search: the question becomes `topic` and only the body becomes
    /// `fact`. LucyVoice states facts.
    func search(terms: [String], limit: Int = 3) -> [CampFact] {
        guard db != nil, !terms.isEmpty else { return [] }
        var scored: [(CampFact, Int)] = []
        for row in rows("SELECT id, question, body FROM answer ORDER BY id DESC LIMIT 400;") {
            let hay = (row.question + " " + row.body).lowercased()
            let hits = terms.filter { containsWord(hay, $0) }.count
            guard hits > 0 else { continue }
            scored.append((CampFact(id: row.rowid,
                                    topic: row.question,
                                    fact: row.body,
                                    category: "camp answer",
                                    year: nil,
                                    quote: "",
                                    // What LucyVoice cites. A camp answer must
                                    // never read as if it shipped in the
                                    // corpus.
                                    sourceTitle: "answered by the camp"), hits))
        }
        return scored.sorted { $0.1 > $1.1 }.prefix(limit).map(\.0)
    }

    func forget() {
        exec("DELETE FROM answer;")
    }

    private func rows(_ sql: String) -> [(rowid: Int64, question: String, body: String)] {
        guard let db else { return [] }
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return [] }
        defer { sqlite3_finalize(stmt) }
        var out: [(rowid: Int64, question: String, body: String)] = []
        while sqlite3_step(stmt) == SQLITE_ROW {
            out.append((rowid: sqlite3_column_int64(stmt, 0),
                        question: sqlite3_column_text(stmt, 1).map { String(cString: $0) } ?? "",
                        body: sqlite3_column_text(stmt, 2).map { String(cString: $0) } ?? ""))
        }
        return out
    }
}
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd ~/Snails/Lucy
xcodegen generate
xcodebuild test -scheme Lucy -destination 'platform=iOS Simulator,name=iPhone 16' \
  -only-testing:LucyTests 2>&1 | tail -30
```

Expected: PASS, 7 tests.

- [ ] **Step 6: Commit**

```bash
cd ~/Snails
git add Lucy/AnswerStore.swift Lucy/Tests/AnswerStoreTests.swift Lucy/project.yml
git commit -m "feat(answers): the phone's own store — yours works offline, the camp's arrives approved"
```

---

## Task 7: Hoist `deadEnd`, and offer to be told

**Files:**
- Modify: `Lucy/ChatView.swift:454-465`
- Test: `Lucy/Tests/AnswerStoreTests.swift` (append `final class DeadEndTests`)

**Interfaces:**
- Consumes: `Remembered` from `Lucy/ChatLog.swift`.
- Produces: `func isDeadEnd(_ answer: String) -> Bool` at file scope in `Lucy/ChatView.swift`, callable from the view body and from tests.

- [ ] **Step 1: Write the failing test**

Append to `Lucy/Tests/AnswerStoreTests.swift`:

```swift
final class DeadEndTests: XCTestCase {
    // These five must stay in step with lucy_is_refusal() in
    // backend/migrations/003_help_lucy.sql. A phrase that is a refusal here
    // and not there is a gap the camp never gets asked about.
    func testTheThreeRefusalShapes() {
        XCTAssertTrue(isDeadEnd("I don't know where that is"))
        XCTAssertTrue(isDeadEnd("Nothing in what I've got says so"))
        XCTAssertTrue(isDeadEnd("That's not something I've been told"))
    }

    func testUncontractedAndCurlyFormsCount() {
        // A wall of "I do not know why..." rode straight past the contracted
        // check once. Journal 15:12Z.
        XCTAssertTrue(isDeadEnd("I do not know why that is"))
        XCTAssertTrue(isDeadEnd("I don\u{2019}t know where that is"))
    }

    func testARealAnswerIsNotADeadEnd() {
        XCTAssertFalse(isDeadEnd("The barrels are in Doris, left side."))
    }
}
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd ~/Snails/Lucy
xcodebuild test -scheme Lucy -destination 'platform=iOS Simulator,name=iPhone 16' \
  -only-testing:LucyTests/DeadEndTests 2>&1 | tail -20
```

Expected: FAIL — `cannot find 'isDeadEnd' in scope`.

- [ ] **Step 3: Hoist the function to file scope**

In `Lucy/ChatView.swift`, add at file scope (outside any type, near the top after the imports):

```swift
/// Whether an answer is Lucy saying she does not have it.
///
/// She phrases refusals freely -- "I don't know", "I do not know" -- and a
/// missed variant re-opens the refusal cascade (journal 15:12Z: a wall of "I do
/// not know why..." rode straight past the contracted-form check).
///
/// At file scope rather than nested, because three things need it now: the
/// prompt filter that keeps refusals out of the chat log section, the offer to
/// be told, and a test. It is also mirrored server-side as lucy_is_refusal() in
/// backend/migrations/003_help_lucy.sql -- if a fourth phrase is added here it
/// is added there too, or the new refusal shape silently stops counting as a
/// gap and the camp never sees the question.
func isDeadEnd(_ answer: String) -> Bool {
    let norm = answer.lowercased()
        .replacingOccurrences(of: "\u{2019}", with: "'")
        .replacingOccurrences(of: "do not", with: "don't")
    return norm.hasPrefix("i don't know")
        || norm.hasPrefix("nothing in what i've got")
        || norm.hasPrefix("that's not something i've been told")
}
```

Then replace the nested function at `Lucy/ChatView.swift:458-465` with a one-line forwarder, so the two call sites at `:496-497` keep working unchanged:

```swift
        func deadEnd(_ r: Remembered) -> Bool { isDeadEnd(r.answer) }
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
xcodebuild test -scheme Lucy -destination 'platform=iOS Simulator,name=iPhone 16' \
  -only-testing:LucyTests 2>&1 | tail -20
```

Expected: PASS, 10 tests.

- [ ] **Step 5: Offer to be told, when she could not answer**

In `Lucy/ChatView.swift`, in the view that renders a finished turn, add below the answer bubble. Place it where `answer` is the string just rendered and `question` is what was asked:

```swift
// Only after a refusal, and only once per turn. An offer under every
// answer would train people to ignore it, and the answers worth having
// are the ones somebody gives while the gap is still annoying them.
if isDeadEnd(answer) {
    if tellingLucy {
        VStack(alignment: .leading, spacing: 8) {
            TextField("What's the answer?", text: $toldAnswer, axis: .vertical)
                .lineLimit(1...4)
                .textFieldStyle(.plain)
                .font(.body_(16))
                .foregroundStyle(face.text)
            HStack {
                Button("Tell her") {
                    AnswerStore.shared.record(question: question, body: toldAnswer)
                    toldAnswer = ""
                    tellingLucy = false
                    toldConfirmation = true
                }
                .disabled(toldAnswer.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                Button("Not now") { tellingLucy = false; toldAnswer = "" }
            }
            .font(.body_(15))
        }
    } else if toldConfirmation {
        // What actually happened, not a thank-you. She has it now; the
        // camp gets it after a Sync and after somebody looks at it.
        Text("She's got it. The camp will see it after you Sync.")
            .font(.body_(15)).foregroundStyle(face.muted)
    } else {
        Button("Do you know?") { tellingLucy = true }
            .font(.body_(15)).foregroundStyle(face.muted)
    }
}
```

Add the three state properties to the same view:

```swift
@State private var tellingLucy = false
@State private var toldAnswer = ""
@State private var toldConfirmation = false
```

- [ ] **Step 6: Build and push to the device**

The Simulator does not prove the phone.

```bash
cd ~/Snails
Lucy/device.sh
```

Ask her something the corpus does not cover, confirm the offer appears under the refusal and not under a real answer, type an answer, and confirm the same question now gets answered.

- [ ] **Step 7: Commit**

```bash
git add Lucy/ChatView.swift Lucy/Tests/AnswerStoreTests.swift
git commit -m "feat(answers): when she can't, she asks — the offer only follows a refusal"
```

---

## Task 8: Retrieval reads the answers

**Files:**
- Modify: `Lucy/Retrieval.swift:143-172`
- Test: `Lucy/Tests/AnswerStoreTests.swift` (append `RetrievalAnswerTests`)

**Interfaces:**
- Consumes: `AnswerStore.shared.search(terms:limit:)` from Task 6.
- Produces: `Retrieval.answer(_:store:previous:)` unchanged in signature; `Answer.camp` now carries approved and locally-typed answers ahead of shipped camp facts.

- [ ] **Step 1: Write the failing test**

Append to `Lucy/Tests/AnswerStoreTests.swift`:

```swift
@MainActor
final class RetrievalAnswerTests: XCTestCase {
    override func setUp() async throws { AnswerStore.shared.forget() }

    func testAnAnsweredGapReachesRetrieval() {
        AnswerStore.shared.record(question: "where is the medkit",
                                  body: "In Doris, left side by the door.")
        let result = Retrieval.answer("where is the medkit", store: EntityStore.shared)
        XCTAssertTrue(result.camp.contains { $0.fact.contains("left side by the door") })
    }

    func testACampAnswerSortsAheadOfShippedFacts() {
        // The camp corrected her on purpose. A shipped row that was already
        // losing should not now outrank the correction.
        AnswerStore.shared.record(question: "where is the medkit", body: "Moved to Bertha.")
        let result = Retrieval.answer("where is the medkit", store: EntityStore.shared)
        XCTAssertEqual(result.camp.first?.sourceTitle, "answered by the camp")
    }

    func testNothingAnsweredChangesNothing() {
        let before = Retrieval.answer("where is doris", store: EntityStore.shared)
        AnswerStore.shared.record(question: "unrelated", body: "unrelated body")
        let after = Retrieval.answer("where is doris", store: EntityStore.shared)
        XCTAssertEqual(before.camp.count, after.camp.count)
    }
}
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd ~/Snails/Lucy
xcodebuild test -scheme Lucy -destination 'platform=iOS Simulator,name=iPhone 16' \
  -only-testing:LucyTests/RetrievalAnswerTests 2>&1 | tail -20
```

Expected: FAIL — the camp answer is not in `result.camp`.

- [ ] **Step 3: Give camp answers their own id space, before merging anything**

`CampFact` is `Identifiable` on `id: Int64`, and `ChatView.swift:219` renders
`ForEach(turn.camp)`. `AnswerStore`'s ids are its own SQLite autoincrement
starting at 1; `EntityStore`'s camp facts carry `camp_knowledge` ids also
starting at 1. Merge the two lists and the FIRST camp answer collides with
`camp_knowledge` row 1 — SwiftUI then has two rows claiming one identity and
renders one of them wrong or not at all.

This is not hypothetical in this repo. `EntityStore.swift:547-552` documents the
same class of bug already hit once: "camp_member ids run 1-313 against
camp_knowledge's 1-616 — so joining on the id alone gave every roster fact the
title of whichever unrelated document happened to share its number."

In `Lucy/AnswerStore.swift`, in `search`, negate the id so the two spaces cannot
overlap:

```swift
            scored.append((CampFact(id: -row.rowid,
```

and put the reason above it:

```swift
            // Negative, so a camp answer can never collide with a
            // camp_knowledge id. CampFact is Identifiable and ChatView renders
            // ForEach(turn.camp); both id spaces are autoincrements starting at
            // 1, so the first answer would otherwise share an identity with
            // camp_knowledge row 1. EntityStore.swift:547 records what that
            // costs -- a citation naming the wrong source.
```

- [ ] **Step 3b: Test that the two id spaces cannot collide**

Append to `Lucy/Tests/AnswerStoreTests.swift`, inside `RetrievalAnswerTests`:

```swift
    func testCampAnswersCannotShareAnIdWithShippedFacts() {
        // Identifiable + ForEach means a duplicate id renders one row wrong or
        // not at all. Both id spaces are autoincrements from 1, so this is the
        // first answer, not an edge case.
        AnswerStore.shared.record(question: "where is the medkit", body: "Moved to Bertha.")
        let camp = Retrieval.answer("where is the medkit", store: EntityStore.shared).camp
        XCTAssertEqual(Set(camp.map(\.id)).count, camp.count,
                       "two facts share an id; ForEach(turn.camp) will drop one")
    }
```

- [ ] **Step 4: Merge them in**

In `Lucy/Retrieval.swift`, in `answer(_:store:previous:)`, replace the line `let camp = store.searchCampFacts(terms: t)` with:

```swift
        // What the camp answered comes first, ahead of what shipped.
        //
        // Someone typed these BECAUSE she got it wrong or had nothing, so a
        // shipped row that was already losing should not now outrank the
        // correction. They are still ordinary CampFact rows -- retrieval
        // supplies the fact and the model phrases it, exactly as before. The
        // only thing that changed is where some rows came from.
        let answered = AnswerStore.shared.search(terms: t)
        let camp = answered + store.searchCampFacts(terms: t)
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
xcodebuild test -scheme Lucy -destination 'platform=iOS Simulator,name=iPhone 16,OS=18.2' \
  -only-testing:LucyTests 2>&1 | tail -20
```

Expected: PASS — the 8 store tests, 3 deadEnd tests, and 4 retrieval tests.

- [ ] **Step 6: Push to the device and read the journal**

```bash
cd ~/Snails
Lucy/device.sh
```

Ask a gap question, answer it, ask again. Then read `Documents/lucy-journal.txt` and confirm the `FACTS GIVEN` line carries the typed answer as a fact — not the prompt carrying it as prose. If it is not in `FACTS GIVEN`, the invariant is broken and this task is not done.

- [ ] **Step 7: Commit**

```bash
git add Lucy/Retrieval.swift Lucy/AnswerStore.swift Lucy/Tests/AnswerStoreTests.swift
git commit -m "feat(answers): what the camp answered outranks what shipped"
```

---

## Task 9: `SyncClient` — send the answers, fetch the approved

**Files:**
- Modify: `Lucy/Sync.swift`

**Interfaces:**
- Consumes: `AnswerStore.pendingUpload()`, `CampAnswer` from Task 6; `POST /v1/answers`, `GET /v1/answers` from Tasks 3-4.
- Produces:
  - `SyncClient.AnswerEntry { let id: UUID; let question: String; let body: String }`
  - `SyncClient.shared.uploadAnswers(entries: [AnswerEntry], token: String) async throws -> Int`
  - `SyncClient.shared.fetchApproved(since: Int, token: String) async throws -> (answers: [CampAnswer], cursor: Int)`

- [ ] **Step 1: Add the upload call**

Append to `actor SyncClient` in `Lucy/Sync.swift`, after `uploadChatlog`:

```swift
    // MARK: - Answers

    /// One answer a member typed, shaped for /v1/answers.
    ///
    /// No gapKey. The server normalises the question and assigns the key --
    /// see the spec. Swift folds case through Unicode and splits on
    /// CharacterSet.alphanumerics; Postgres does neither the same way, and a
    /// key that disagreed on one codepoint would file this against a gap that
    /// does not exist, silently.
    struct AnswerEntry {
        let id: UUID
        let question: String
        let body: String
    }

    /// Returns how many were newly stored. A retry that stores 0 is a success.
    func uploadAnswers(entries: [AnswerEntry], token: String) async throws -> Int {
        var request = URLRequest(url: Backend.baseURL.appendingPathComponent("answers"))
        request.httpMethod = "POST"
        request.timeoutInterval = Backend.uploadTimeout
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "answers": entries.map { entry -> [String: Any] in
                ["id": entry.id.uuidString,
                 "question": entry.question,
                 "body": entry.body]
            },
        ])

        guard let (data, response) = try? await session.data(for: request),
              let http = response as? HTTPURLResponse else { throw SyncError.unreachable }
        if http.statusCode == 401 { throw SyncError.unauthorised }
        if http.statusCode == 413 { throw SyncError.tooBig }
        guard http.statusCode == 200,
              let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let stored = body["stored"] as? Int else {
            throw SyncError.server(http.statusCode)
        }
        return stored
    }

    /// Answers the camp approved that this phone has not seen.
    ///
    /// Its own cursor, separate from the notes cursor: the two advance for
    /// unrelated reasons and sharing one would make a quiet week of notes hide
    /// a week of answers.
    func fetchApproved(since: Int, token: String) async throws
        -> (answers: [CampAnswer], cursor: Int)
    {
        var comps = URLComponents(url: Backend.baseURL.appendingPathComponent("answers"),
                                  resolvingAgainstBaseURL: false)!
        comps.queryItems = [URLQueryItem(name: "since", value: String(since))]
        var request = URLRequest(url: comps.url!)
        request.timeoutInterval = Backend.probeTimeout
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")

        guard let (data, response) = try? await session.data(for: request),
              let http = response as? HTTPURLResponse else { throw SyncError.unreachable }
        if http.statusCode == 401 { throw SyncError.unauthorised }
        guard http.statusCode == 200,
              let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let raw = body["answers"] as? [[String: Any]],
              let cursor = body["cursor"] as? Int else {
            throw SyncError.server(http.statusCode)
        }
        // A malformed row is skipped, not thrown on. One bad answer must not
        // cost the phone every good one in the same batch.
        let answers = raw.compactMap { row -> CampAnswer? in
            guard let idString = row["id"] as? String, let id = UUID(uuidString: idString),
                  let gapKey = row["gapKey"] as? String,
                  let question = row["question"] as? String,
                  let text = row["body"] as? String else { return nil }
            return CampAnswer(id: id, gapKey: gapKey, question: question, body: text)
        }
        return (answers, cursor)
    }
```

- [ ] **Step 2: Build**

```bash
cd ~/Snails/Lucy
xcodebuild build -scheme Lucy -destination 'platform=iOS Simulator,name=iPhone 16' 2>&1 | tail -10
```

Expected: BUILD SUCCEEDED.

- [ ] **Step 3: Commit**

```bash
cd ~/Snails
git add Lucy/Sync.swift
git commit -m "feat(sync): answers up, approved answers down, on a cursor of their own"
```

---

## Task 10: The Sync screen — two new steps, and a sentence that stopped being true

**Files:**
- Modify: `Lucy/SyncView.swift:100-105` (the copy), `Lucy/SyncView.swift:182-250` (`doSync`)

**Interfaces:**
- Consumes: Task 9's `uploadAnswers` and `fetchApproved`; Task 6's `AnswerStore`.
- Produces: nothing other tasks depend on.

- [ ] **Step 1: Replace the privacy copy**

`Lucy/SyncView.swift:102-105` currently reads "conversations with Lucy privately to the camp's server". That is no longer true — refusal-shaped questions come back to the camp verbatim. Replace the `Text` and the comment above it:

```swift
            // The one sentence the camp hears before anything leaves the
            // phone. It has been rewritten twice now, both times because it
            // stopped being true: "stays on your phone" went when chatlog
            // upload shipped, and "privately" went when the gap list opened.
            // Three true things and no more -- the conversations go up, the
            // questions she couldn't answer come back for the camp to answer,
            // and no row anywhere says who asked. The last one is structural,
            // not policy: the chatlog table has no member column.
            Text("Sync sends your notes to everyone in camp, and your "
                 + "conversations with Lucy to the camp's server. Questions "
                 + "she couldn't answer go on a list the camp can answer — "
                 + "the question, never who asked it.")
                .font(.body_(16)).foregroundStyle(face.muted)
                .fixedSize(horizontal: false, vertical: true)
```

- [ ] **Step 2: Add the answers cursor**

Beside `@AppStorage("sync-cursor") private var cursor = 0` at `Lucy/SyncView.swift:24`:

```swift
    // Separate from the notes cursor on purpose: the two advance for unrelated
    // reasons, and one cursor would let a quiet week of notes hide a week of
    // answers.
    @AppStorage("answers-cursor") private var answersCursor = 0
```

- [ ] **Step 3: Upload answers after the chat log**

In `doSync()`, after the chat-log upload block, add:

```swift
        // Answers after the chat log, for the same reason the chat log goes
        // after the notes: the half that helps everyone else goes first, and a
        // failure here never un-succeeds what already landed.
        var answersSent = 0
        do {
            let pending = AnswerStore.shared.pendingUpload()
            if !pending.isEmpty {
                let entries = pending.map {
                    SyncClient.AnswerEntry(id: $0.id, question: $0.question, body: $0.body)
                }
                answersSent = try await SyncClient.shared.uploadAnswers(
                    entries: entries, token: token)
                // Marked whether newly stored or already there. Both mean the
                // camp has it.
                AnswerStore.shared.markUploaded(rowids: pending.map(\.rowid))
            }
        } catch {
            // Deliberately not surfaced. The notes went; a batch of answers
            // that will go next time is not a reason to un-say that.
            print("LUCY: answer upload deferred — \(error.localizedDescription)")
        }
```

- [ ] **Step 4: Fetch approved answers**

Immediately after, add:

```swift
        var answersIn = 0
        do {
            let (approved, newCursor) = try await SyncClient.shared.fetchApproved(
                since: answersCursor, token: token)
            AnswerStore.shared.store(approved: approved)
            answersCursor = newCursor
            answersIn = approved.count
        } catch {
            print("LUCY: answer fetch deferred — \(error.localizedDescription)")
        }
```

- [ ] **Step 5: Say what happened**

Where `status` is composed at the end of `doSync()`, add the answer counts. Approved answers are not a list to browse — the only visible sign is that she stops saying she does not know, so the line says that:

```swift
        if answersIn > 0 {
            let noun = answersIn == 1 ? "answer" : "answers"
            status = (status.map { $0 + ". " } ?? "")
                + "\(answersIn) new \(noun) from camp — she knows more now."
        }
```

- [ ] **Step 6: Push to the device and run the round trip**

```bash
cd ~/Snails
Lucy/device.sh
```

Then, end to end:
1. Ask something the corpus does not cover; confirm the refusal and the offer.
2. Answer it; confirm she uses it immediately.
3. Press Sync; confirm no error.
4. On the server, open `/admin/answers`, confirm the answer is listed with its question, approve it.
5. On a **second** phone or after `AnswerStore.shared.forget()`, press Sync and confirm the answer arrives and the same question is now answered.

- [ ] **Step 7: Commit**

```bash
git add Lucy/SyncView.swift
git commit -m "feat(sync): Help Lucy's answer half — and the sentence that stopped being true"
```

---

## Task 11: The gap list on the camp site

**Files:**
- Modify: `site/index.html`, `backend/app/site.py`
- Test: `backend/tests/test_answers.py` (append `class TestSiteGaps`)

**Interfaces:**
- Consumes: `GET /v1/gaps`, `POST /v1/answers` from Tasks 2-3; the `lucy_site` cookie from `backend/app/site.py`.
- Produces: `GET /help-lucy` behind the codeword cookie.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_answers.py`:

```python
class TestSiteGaps:
    def test_the_page_is_not_served_without_the_codeword(self, client, site_dir):
        # Same rule the rest of the site follows: the page is never sent to a
        # browser that has not passed, so it is not one curl away.
        assert client.get("/help-lucy").status_code in (401, 303, 307)

    def test_the_page_lists_gaps_once_past_the_codeword(self, client, site_dir):
        tok = token(client)
        ask(client, tok, "where is the medkit")
        client.post("/v1/site-unlock", data={"code": "test-site-code"},
                    follow_redirects=False)
        page = client.get("/help-lucy")
        assert page.status_code == 200
        assert "where is the medkit" in page.text

    def test_a_members_words_are_escaped_not_rendered(self, client, site_dir):
        # Camp members type into this. Their words are data, never markup.
        tok = token(client)
        ask(client, tok, "<script>alert(1)</script>")
        client.post("/v1/site-unlock", data={"code": "test-site-code"},
                    follow_redirects=False)
        page = client.get("/help-lucy")
        assert "<script>alert(1)</script>" not in page.text
        assert "&lt;script&gt;" in page.text
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd ~/Snails/backend
.venv/bin/python -m pytest tests/test_answers.py::TestSiteGaps -v
```

Expected: FAIL — 404 or the catch-all serving something else.

- [ ] **Step 3: Add the route**

In `backend/app/site.py`, add before the catch-all `GET /{path:path}`:

```python
@router.get("/help-lucy", response_class=HTMLResponse)
def help_lucy(request: Request) -> Response:
    """The questions Lucy could not answer, for anyone who can.

    Server-rendered behind the same codeword cookie as the rest of the site,
    for the same reason: a page that is never sent to a browser that has not
    passed is not one curl away.

    The asker is not here and cannot be. The gap query groups chatlog rows and
    chatlog has no member column -- there is nothing to leak even by accident.
    """
    if not _valid(request.cookies.get(COOKIE)):
        return RedirectResponse("/", status_code=303)
    with db.pool.connection() as conn:
        rows = conn.execute(
            "SELECT lucy_gap_key(c.question),"
            "       (array_agg(c.question ORDER BY c.asked_at DESC))[1],"
            "       count(*)::int"
            " FROM chatlog c"
            " WHERE lucy_is_refusal(c.answer)"
            # In WHERE, not the SELECT list. WHERE is evaluated per row
            # before GROUP BY, so `c.question` here is a plain column and the
            # correlation is legal -- unlike the same subquery in a grouped
            # SELECT list, which Postgres rejects (see answers.py). Verified.
            "   AND NOT EXISTS (SELECT 1 FROM answer a"
            "                   WHERE a.gap_key = lucy_gap_key(c.question)"
            "                     AND a.status = 'approved')"
            " GROUP BY lucy_gap_key(c.question)"
            " ORDER BY count(*) DESC LIMIT 100").fetchall()
    return HTMLResponse(_help_page(rows))


def _help_page(rows) -> str:
    if not rows:
        return ("<html><body style='font:16px/1.5 system-ui;padding:2rem;"
                "max-width:38rem;margin:auto'><h1>Nothing to answer</h1>"
                "<p>She's answered everything anyone has asked her so far.</p>"
                "</body></html>")
    items = "".join(
        "<li style='margin:0 0 2rem;list-style:none'>"
        f"<div style='font-size:1.15em'>{html.escape(r[1])}</div>"
        f"<div style='color:#777;font-size:.85em'>asked {r[2]} "
        f"{'time' if r[2] == 1 else 'times'}</div>"
        "<form method='post' action='/help-lucy' style='margin-top:.5rem'>"
        f"<input type='hidden' name='question' value='{html.escape(r[1])}'>"
        "<textarea name='body' rows='2' required "
        "style='width:100%;font:inherit;padding:.4rem'></textarea>"
        "<button style='margin-top:.4rem'>Tell her</button>"
        "</form></li>" for r in rows)
    return ("<html><head><meta name='viewport' content='width=device-width,"
            "initial-scale=1'></head>"
            "<body style='font:16px/1.5 system-ui;padding:2rem;max-width:38rem;"
            "margin:auto'><h1>Help Lucy</h1>"
            "<p style='color:#555'>Things people asked her that she couldn't "
            "answer. Nobody can see who asked.</p>"
            f"<ul style='padding:0'>{items}</ul></body></html>")
```

`html.escape` rather than a hand-rolled one, matching `admin.py:194`. Note it
needs `quote=True` — the default — because the question also goes into a
`value='...'` attribute above.

Add the POST handler that receives an answer from the page, immediately after:

```python
@router.post("/help-lucy")
def help_lucy_answer(request: Request, question: str = Form(...),
                     body: str = Form(...)) -> Response:
    """An answer typed in a browser.

    The id is minted here rather than by the browser: there is no retry story
    on a form post the way there is for a phone on a dying connection, and a
    hidden field holding a UUID is a field somebody can edit.
    """
    if not _valid(request.cookies.get(COOKIE)):
        return RedirectResponse("/", status_code=303)
    with db.pool.connection() as conn:
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (ANSWER_SEQ_LOCK,))
            conn.execute(
                "INSERT INTO answer (id, gap_key, question, body)"
                " VALUES (%s, lucy_gap_key(%s), %s, %s)",
                (str(uuid.uuid4()), question, question, body[:4000]))
    return RedirectResponse("/help-lucy", status_code=303)
```

Add to the imports at the top of `backend/app/site.py`:

`site.py` already imports `Form`, `Request`, `Response` and `RedirectResponse`
(lines 21-22). Add only what is missing:

```python
import html
import uuid

from fastapi.responses import HTMLResponse

from . import db
from .answers import ANSWER_SEQ_LOCK
```

No import cycle: `answers.py` imports `db`, `auth` and `models`, never `site`.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_answers.py -v
```

Expected: PASS, 38 tests.

- [ ] **Step 5: Run the whole suite**

```bash
.venv/bin/python -m pytest
```

Expected: PASS, everything. If the site tests fail on route ordering, the `/help-lucy` routes were added after the catch-all — move them above it.

- [ ] **Step 6: Commit**

```bash
cd ~/Snails
git add backend/app/site.py backend/tests/test_answers.py
git commit -m "feat(site): the gap list, behind the codeword the site already uses"
```

---

## Task 12: Approved answers become ordinary corpus rows

**Files:**
- Create: `scripts/export_answers.py`
- Test: `scripts/tests/test_export_answers.py`

**Interfaces:**
- Consumes: the `answer` table.
- Produces: `export_answers(conn, db_path) -> int`, writing `camp_fact` rows into `ps_knowledge.db`.

Without this, the sync-side `answer` table is a second permanent knowledge store that grows forever and never reaches the shipped database. With it, an answer given during the burn is an ordinary `camp_fact` row at the next enrichment pass.

- [ ] **Step 1: Write the failing test**

Create `scripts/tests/test_export_answers.py`:

```python
"""Approved answers into the corpus.

The sync-side answer table is a queue, not a knowledge store. This is the step
that empties it into the place retrieval actually reads.
"""
import sqlite3

import pytest

from export_answers import export_answers


@pytest.fixture
def knowledge(tmp_path):
    path = tmp_path / "ps_knowledge.db"
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE camp_fact (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT NOT NULL, fact TEXT NOT NULL,
            category TEXT, year INTEGER, source_title TEXT)
        """)
    conn.commit()
    conn.close()
    return path


def test_an_approved_answer_becomes_a_camp_fact(knowledge, pg):
    pg.execute("INSERT INTO answer (id, gap_key, question, body, status)"
               " VALUES (gen_random_uuid(), 'k', 'where is the medkit',"
               " 'In Doris, left side.', 'approved')")
    assert export_answers(pg, knowledge) == 1
    conn = sqlite3.connect(knowledge)
    row = conn.execute("SELECT topic, fact, source_title FROM camp_fact").fetchone()
    assert row[0] == "where is the medkit"
    assert row[1] == "In Doris, left side."
    assert row[2] == "answered by the camp"


def test_pending_and_rejected_answers_are_not_exported(knowledge, pg):
    pg.execute("INSERT INTO answer (id, gap_key, question, body)"
               " VALUES (gen_random_uuid(), 'k', 'q', 'pending body')")
    pg.execute("INSERT INTO answer (id, gap_key, question, body, status)"
               " VALUES (gen_random_uuid(), 'k2', 'q2', 'rejected body', 'rejected')")
    assert export_answers(pg, knowledge) == 0


def test_running_it_twice_does_not_duplicate(knowledge, pg):
    # It will be run again after the next burn, over a table that still holds
    # last year's answers.
    pg.execute("INSERT INTO answer (id, gap_key, question, body, status)"
               " VALUES (gen_random_uuid(), 'k', 'q', 'b', 'approved')")
    assert export_answers(pg, knowledge) == 1
    assert export_answers(pg, knowledge) == 0
    conn = sqlite3.connect(knowledge)
    assert conn.execute("SELECT count(*) FROM camp_fact").fetchone()[0] == 1
```

- [ ] **Step 2: Run it to verify it fails**

```bash
cd ~/Snails/scripts
.venv/bin/python -m pytest tests/test_export_answers.py -v
```

Expected: FAIL — `No module named 'export_answers'`.

- [ ] **Step 3: Write the exporter**

Create `scripts/export_answers.py`:

```python
"""Approved answers into ps_knowledge.db, as ordinary camp facts.

The sync-side `answer` table is a queue and not a second knowledge store. An
answer that stays there forever is a row retrieval reaches only through the
phone's local copy, which means it is lost the day somebody reinstalls.

Run this before an enrichment pass. Everything downstream then treats a camp
answer exactly like any other camp fact, which is the point: retrieval supplies
the facts, and where a fact came from is a source_title, not a code path.
"""
import sqlite3
from pathlib import Path

# What the row says it is, in the corpus and on the phone. AnswerStore.swift
# sets the same string, so an answer reads the same before and after a rebuild.
SOURCE = "answered by the camp"


def export_answers(conn, db_path: Path) -> int:
    """Copies approved answers into camp_fact. Returns how many were new.

    Idempotent by (topic, fact, source_title): this gets run again after the
    next burn, over a table that still holds the last one's answers.
    """
    rows = conn.execute(
        "SELECT question, body FROM answer WHERE status = 'approved'").fetchall()
    if not rows:
        return 0
    out = sqlite3.connect(db_path)
    try:
        existing = {(t, f) for t, f in out.execute(
            "SELECT topic, fact FROM camp_fact WHERE source_title = ?",
            (SOURCE,)).fetchall()}
        fresh = [(q, b) for q, b in rows if (q, b) not in existing]
        out.executemany(
            "INSERT INTO camp_fact (topic, fact, category, year, source_title)"
            " VALUES (?, ?, 'camp answer', NULL, ?)",
            [(q, b, SOURCE) for q, b in fresh])
        out.commit()
        return len(fresh)
    finally:
        out.close()


if __name__ == "__main__":
    import argparse
    import os

    import psycopg

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path,
                        default=Path(__file__).parent / "output" / "ps_knowledge.db")
    parser.add_argument("--dsn", default=os.environ.get("LUCY_DATABASE_URL"))
    args = parser.parse_args()
    if not args.dsn:
        raise SystemExit("Set LUCY_DATABASE_URL or pass --dsn. It is in "
                         "backend/.env on the VM and nowhere else.")
    with psycopg.connect(args.dsn) as pg:
        print(f"{export_answers(pg, args.db)} new camp answers -> {args.db}")
```

- [ ] **Step 4: Add the Postgres fixture the test needs**

Append to `scripts/tests/conftest.py` (create it if absent):

```python
import os

import psycopg
import pytest

# The same throwaway Postgres the backend suite uses:
#   cd backend && docker compose -f compose.test.yml up -d
#
# Credentials come from compose.test.yml, which sets POSTGRES_PASSWORD=lucytest
# and POSTGRES_DB=lucytest with the default `postgres` user. This is the same
# default backend/app/settings.py:19 carries -- if you change one, change both.
DSN = os.environ.get("DATABASE_URL",
                     "postgresql://postgres:lucytest@localhost:55432/lucytest")


@pytest.fixture
def pg():
    """A deliberately partial `answer` table.

    Only the four columns export_answers reads. It is not the migration's
    schema and must not pretend to be -- this suite tests the exporter, and a
    second full copy of the schema here is a copy that drifts. The real shape
    is backend/migrations/003_help_lucy.sql, exercised by the backend suite.
    """
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS answer (
                id UUID PRIMARY KEY, gap_key TEXT NOT NULL,
                question TEXT NOT NULL, body TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending')
            """)
        conn.execute("TRUNCATE answer")
        yield conn
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd ~/Snails/backend
docker compose -f compose.test.yml up -d
cd ../scripts
.venv/bin/python -m pytest tests/test_export_answers.py -v
```

Expected: PASS, 3 tests.

- [ ] **Step 6: Commit**

```bash
cd ~/Snails
git add scripts/export_answers.py scripts/tests/test_export_answers.py scripts/tests/conftest.py
git commit -m "feat(enrich): approved answers stop being a queue and become rows"
```

---

## Task 13: Deploy, and prove the round trip on the phone

**Files:** none created or modified.

- [ ] **Step 1: Run the whole backend suite**

```bash
cd ~/Snails/backend
.venv/bin/python -m pytest
```

Expected: PASS, everything. Do not deploy on a red suite.

- [ ] **Step 2: Deploy**

`push.sh` handles the two macOS traps: `tar` writes `._*` AppleDouble sidecars that match a `*.sql` glob and kill the container on a binary read, and the Caddyfile is a bind mount so `compose up -d` leaves the old config loaded. Do not hand-roll this.

```bash
./deploy/push.sh
```

- [ ] **Step 3: Confirm the migration applied**

```bash
curl -s https://lucy.marcusfoster.com/v1/health
```

Expected: `{"ok":true,...}`. Then check the container logs name `003_help_lucy.sql` as applied. If it did not apply, the api container is running old code and step 2 did not do what it looked like it did.

- [ ] **Step 4: Push to the device**

```bash
cd ~/Snails
Lucy/device.sh
```

- [ ] **Step 5: Run the full loop against the real server**

1. Ask a question the corpus does not cover. Confirm the refusal, and the offer under it.
2. Answer it. Confirm she uses it on the next ask, still offline.
3. Sync. Confirm no error and no complaint.
4. Open `https://lucy.marcusfoster.com/help-lucy`, pass the codeword, confirm the gap is listed with the ask count and **without** any hint of who asked.
5. Answer a different gap from the site.
6. Open `/admin/answers`, confirm both are listed with their questions, approve both.
7. Run `AnswerStore.shared.forget()` or use a second phone. Sync. Confirm both answers arrive and both questions are now answered.
8. Read `Documents/lucy-journal.txt` and confirm the answers appear under `FACTS GIVEN` — as retrieved rows, not as prompt prose. **If they are anywhere else, the invariant is broken.**

- [ ] **Step 6: Check the shipped-database gate still passes**

```bash
cd ~/Snails
scripts/.venv/bin/python scripts/check_shipped_db.py scripts/output/enriched_preview.db
```

Expected: PASS. Camp answers are ordinary rows and must not trip the redaction gate — if one does, a member typed something personal into an answer box and the gate is doing its job.

- [ ] **Step 7: Commit nothing, and write down what happened**

There is nothing to commit. Add a line to `docs/superpowers/plans/2026-08-19-help-lucy-answer-half.md` recording the date the round trip was verified on the phone and on which build, the way the sync slice's plan records 2026-08-10. A plan that does not say whether it was proven is a plan somebody has to prove again.

---

## Notes for whoever executes this

- **Tasks 1-10 are the loop.** After Task 10 the feature works end to end: ask, refuse, answer, sync, approve, sync, answered. Tasks 11-12 are the second surface and the durability, and each is independently useful.
- **Task 4 adds a second migration, and this is the one thing in the plan that was originally wrong.** Task 4's download test is what reveals that an upload cursor cannot carry an approval, so new DDL is needed — that part was always right. The first version of this plan told the implementer to APPEND it to `003_help_lucy.sql` and claimed the `IF NOT EXISTS` forms made that safe. They do not, because the file never runs a second time at all: `backend/app/db.py:52-59` skips any filename already recorded in `schema_migration`. On the camp VM, where `push.sh` runs `migrate()` on every restart, the appended DDL would never execute and `GET /v1/answers` would fail with "column approved_seq does not exist". Safe-to-re-run is irrelevant when it never re-runs. The DDL lives in its own `004_approved_seq.sql`, which is what 001, 002 and 003 each already do. **Never amend a migration that has been applied anywhere.**
- **The three refusal prefixes exist in two languages.** `isDeadEnd` in `Lucy/ChatView.swift` and `lucy_is_refusal` in `backend/migrations/003_help_lucy.sql`. Both files carry a comment pointing at the other. A fourth phrase added to one and not the other means the new refusal shape silently stops counting as a gap.
- **Do not add an answer to the prompt.** Every route an answer takes ends in a `CampFact` row. If a task seems to need prose in the prompt, the task is wrong.
