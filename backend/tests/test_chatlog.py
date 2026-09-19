"""Chat logs: upload, idempotency, and the promise.

The promise is "chat logs go up private -- nobody reads anybody's questions".
Half of these tests are about a retry on a bad connection not duplicating a
turn; the last one is about the promise being a column that does not exist,
so it cannot erode without someone deleting a test that says why it is there.
"""
import uuid

from app import db

DEVICE_A = "11111111-2222-3333-4444-555555555555"


def token(client, device=DEVICE_A):
    return client.post("/v1/join", json={
        "code": "test-code", "deviceId": device}).json()["token"]


def entry(question="where are the barrels", answer="in the doris container",
          model="gemma-4-E2B", asked_at="2026-08-28T19:04:00Z", entry_id=None):
    return {"id": entry_id or str(uuid.uuid4()), "question": question,
            "answer": answer, "model": model, "askedAt": asked_at}


def upload(client, tok, entries):
    return client.post("/v1/chatlog", json={"entries": entries},
                       headers={"Authorization": f"Bearer {tok}"})


def count(sql="SELECT count(*) FROM chatlog", params=()):
    with db.pool.connection() as conn:
        return conn.execute(sql, params).fetchone()[0]


class TestUpload:
    def test_stores_a_batch_and_counts_it(self, client):
        r = upload(client, token(client), [entry(), entry(), entry()])
        assert r.status_code == 200
        assert r.json() == {"stored": 3}
        assert count() == 3

    def test_asked_at_is_parsed_and_kept(self, client):
        upload(client, token(client),
               [entry(asked_at="2026-08-28T19:04:00Z")])
        with db.pool.connection() as conn:
            asked = conn.execute(
                "SELECT asked_at AT TIME ZONE 'UTC' FROM chatlog").fetchone()[0]
        assert asked.isoformat() == "2026-08-28T19:04:00"

    def test_model_may_be_null(self, client):
        # The turn is worth keeping even when the phone did not record which
        # model answered it.
        r = upload(client, token(client), [entry(model=None)])
        assert r.status_code == 200
        assert count("SELECT count(*) FROM chatlog WHERE model IS NULL") == 1

    def test_requires_a_token(self, client):
        assert client.post("/v1/chatlog",
                           json={"entries": [entry()]}).status_code == 401

    def test_an_empty_batch_is_a_success(self, client):
        # A phone with nothing queued syncing anyway is not an error.
        r = upload(client, token(client), [])
        assert r.status_code == 200
        assert r.json() == {"stored": 0}


class TestIdempotency:
    def test_a_full_retry_stores_nothing_and_succeeds(self, client):
        # The whole point of the phone choosing the id: a retry that finds
        # everything already there is a success, not an error.
        tok = token(client)
        entries = [entry(), entry()]
        upload(client, tok, entries)
        r = upload(client, tok, entries)
        assert r.status_code == 200
        assert r.json() == {"stored": 0}
        assert count() == 2

    def test_a_partial_retry_stores_only_what_is_missing(self, client):
        # The bad-connection shape: the first attempt half-landed before the
        # connection died, and the retry carries the whole batch again.
        tok = token(client)
        landed = entry()
        upload(client, tok, [landed])
        r = upload(client, tok, [landed, entry()])
        assert r.json() == {"stored": 1}
        assert count() == 2

    def test_a_retry_does_not_rewrite_what_was_stored(self, client):
        tok = token(client)
        turn_id = str(uuid.uuid4())
        upload(client, tok, [entry(entry_id=turn_id, answer="original")])
        upload(client, tok, [entry(entry_id=turn_id, answer="rewritten")])
        assert count("SELECT count(*) FROM chatlog"
                     " WHERE answer = 'original'") == 1


class TestLimits:
    def test_a_batch_past_500_is_refused(self, client):
        r = upload(client, token(client), [entry() for _ in range(501)])
        assert r.status_code == 422
        assert count() == 0

    def test_a_batch_of_exactly_500_is_not(self, client):
        r = upload(client, token(client), [entry() for _ in range(500)])
        assert r.status_code == 200
        assert r.json() == {"stored": 500}

    def test_an_oversize_question_is_refused(self, client):
        r = upload(client, token(client), [entry(question="q" * 8001)])
        assert r.status_code == 422
        assert count() == 0

    def test_an_oversize_answer_is_refused(self, client):
        # A fact sheet never reaches the log: a turn is a question and an
        # answer, and 8000 characters holds any real one of either.
        r = upload(client, token(client), [entry(answer="a" * 8001)])
        assert r.status_code == 422
        assert count() == 0


class TestThePromise:
    def test_the_table_cannot_say_who_asked(self, client):
        # "Nobody reads anybody's questions" is strongest as a column that
        # does not exist: a copy of this database cannot say who asked what.
        # Whoever adds attribution has to come through this test and the
        # comment in migrations/002_chatlog.sql, on purpose, in the open.
        with db.pool.connection() as conn:
            columns = {r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name = 'chatlog'").fetchall()}
        # `unanswered` was added deliberately, through this test, in 006. It
        # says whether retrieval found anything -- a fact about the camp's
        # knowledge, not about the person. It is the same for everyone who
        # asks the same question, so it carries no attribution.
        assert columns == {"id", "question", "answer", "model", "asked_at",
                           "uploaded_at", "unanswered"}
        assert not any("member" in c or "device" in c for c in columns)

    def test_the_uploader_is_not_recorded_anywhere_in_the_row(self, client):
        # Belt to the schema test's braces: two members upload, and the rows
        # are indistinguishable.
        a = token(client, DEVICE_A)
        b = token(client, "99999999-8888-7777-6666-555555555555")
        upload(client, a, [entry(question="from A")])
        upload(client, b, [entry(question="from B")])
        with db.pool.connection() as conn:
            rows = conn.execute("SELECT * FROM chatlog").fetchall()
        flattened = " ".join(str(v) for row in rows for v in row)
        assert DEVICE_A not in flattened
        assert "99999999" not in flattened
