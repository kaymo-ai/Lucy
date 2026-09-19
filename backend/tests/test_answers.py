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


def ask(client, tok, question, answer=REFUSAL, asked_at="2026-08-28T19:04:00Z",
        unanswered=None):
    """Puts one turn into chatlog, which is where gaps come from.

    `unanswered=None` is what an app built before 006 sends, and what every
    turn already on the camp VM carries. Those rows are still classified by
    their wording, which is why REFUSAL is the default answer here.
    """
    entry = {"id": str(uuid.uuid4()), "question": question, "answer": answer,
             "model": "gemma-4-E2B", "askedAt": asked_at}
    if unanswered is not None:
        entry["unanswered"] = unanswered
    return client.post("/v1/chatlog", json={"entries": [entry]},
                       headers={"Authorization": f"Bearer {tok}"})


class TestSchema:
    def test_refusal_function_matches_the_phones_deadEnd_shapes(self):
        # This is lucy_is_refusal() being asserted against deadEnd() in
        # ChatView.swift. If that grows a phrase, this test is where it is
        # noticed. The five journal strings below are verbatim from
        # Documents/lucy-journal.txt on the owner's phone -- the evidence
        # that the old, three-prefix rule (33 of 99 real answered turns)
        # missed genuine refusals, not invented test data.
        with db.pool.connection() as conn:
            def refusal(s):
                return conn.execute(
                    "SELECT lucy_is_refusal(%s)", (s,)).fetchone()[0]
            assert refusal("I don't know where that is")
            assert refusal("I do not know where that is")      # uncontracted
            assert refusal("I don’t know where that is")  # curly
            assert refusal("Nothing in what I've got says so")
            assert refusal("That's not something I've been told")
            # Verbatim from the journal. Leading filler in front of a known
            # phrase -- the hasPrefix -> contains defect.
            assert refusal("OK ho. I don't know what a K is.")
            # Verbatim. A refusal shape the phrase list never had.
            assert refusal(
                "I do not have any facts about why anyone might be an idiot.")
            assert refusal(
                "I do not have any facts about Sammy being sheep.")
            assert refusal(
                "I do not have a fact that says that is not helpful.")
            assert refusal(
                "I can only say what the facts state. The facts provided do "
                "not contain information about why it is so hot at Burning "
                "Man.")
            assert not refusal(
                "The Reno storage unit is at Northwest Self Storage; "
                "5275 W. 4th Street; Reno, NV 89523 in the West Lot. "
                "The code for the lock is 3132.")
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
                           "status", "created_at", "approved_seq"}
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


def send_answer(client, tok, question, body, answer_id=None):
    return client.post("/v1/answers", json={"answers": [{
        "id": answer_id or str(uuid.uuid4()),
        "question": question, "body": body}]},
        headers={"Authorization": f"Bearer {tok}"})


class TestTheGapSignalIsRecordedNotGuessed:
    """006 moved the gap signal off the answer's wording.

    The phone knows whether retrieval returned rows before a word is
    generated, and now says so. These tests are the two directions in which
    that disagrees with reading the text -- which it must, because an
    empty-handed answer is deliberately a joke about the camp now, and a joke
    contains none of the phrases lucy_is_refusal() knows.
    """

    JOKE = ("Nobody wrote that one down. The camp did once burn a yurt rather "
            "than pack it, so standards here are what they are.")

    def test_a_joke_is_a_gap_when_the_phone_says_it_was(self, client):
        tok = token(client)
        ask(client, tok, "who is the best DJ", answer=self.JOKE, unanswered=True)
        gaps = client.get("/v1/gaps",
                          headers={"Authorization": f"Bearer {tok}"}).json()["gaps"]
        assert [g["question"] for g in gaps] == ["who is the best DJ"], (
            "the wording carries no refusal phrase, so only the recorded "
            "signal can find this gap")

    def test_an_answer_that_merely_sounds_like_a_refusal_is_not_a_gap(self, client):
        # She can say "I don't know" inside a real answer -- naming the part
        # she is unsure of. Retrieval found rows, so the camp must not be
        # asked to fill a gap that is not there.
        tok = token(client)
        ask(client, tok, "when does the truck leave",
            answer="I don't know the hour, but the load-out is Wednesday.",
            unanswered=False)
        gaps = client.get("/v1/gaps",
                          headers={"Authorization": f"Bearer {tok}"}).json()["gaps"]
        assert gaps == []

    def test_turns_from_before_006_are_still_read_by_their_wording(self, client):
        # The camp VM is full of these. Dropping lucy_is_refusal() would have
        # discarded every gap collected before today.
        tok = token(client)
        ask(client, tok, "where is the medkit")          # unanswered stays NULL
        gaps = client.get("/v1/gaps",
                          headers={"Authorization": f"Bearer {tok}"}).json()["gaps"]
        assert [g["question"] for g in gaps] == ["where is the medkit"]

    def test_the_partial_index_is_usable_for_the_query_the_endpoint_runs(self, client):
        # 005's lesson, one migration on. A partial index stores the RESULT of
        # its predicate, evaluated at index time -- which is why replacing
        # lucy_is_refusal() in 005 needed a REINDEX, and why 006 drops the old
        # index rather than trying to reindex a changed predicate into place.
        #
        # The question worth asking is whether the index CAN serve this
        # predicate, not whether the planner picks it: on a test table of a
        # few hundred rows a sequential scan is genuinely cheaper and always
        # wins, so an unforced EXPLAIN would pass with no index at all and
        # prove nothing. enable_seqscan off removes that escape route.
        #
        # The predicate here is copy-pasted from answers.py on purpose. A
        # partial index only applies when the query's predicate matches the
        # index's, so the two drifting apart is exactly the failure this
        # catches -- and it is silent, costing a full scan of every turn the
        # camp has ever taken rather than an error.
        tok = token(client)
        ask(client, tok, "where is the medkit")
        with db.pool.connection() as conn:
            # Not SET LOCAL: the pooled connection is not inside an explicit
            # transaction, so SET LOCAL is silently a no-op and the plan comes
            # back as a sequential scan whether or not the index is usable --
            # which looks exactly like the bug this test is for. Reset in the
            # finally, or the next test to borrow this connection plans with
            # sequential scans still disabled.
            conn.execute("SET enable_seqscan = off")
            try:
                plan = "\n".join(r[0] for r in conn.execute(
                    "EXPLAIN SELECT lucy_gap_key(c.question) FROM chatlog c"
                    " WHERE COALESCE(c.unanswered, lucy_is_refusal(c.answer))"
                    " GROUP BY lucy_gap_key(c.question)").fetchall())
            finally:
                conn.execute("RESET enable_seqscan")
        assert "chatlog_gap_idx" in plan, plan


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
        # Without this, an empty `answer` table (e.g. the route silently
        # dropping every write) makes flattened == "" and both assertions
        # below pass vacuously, proving nothing. The two writes above must
        # actually have landed before absence-of-device-id means anything.
        assert len(rows) == 2
        flattened = " ".join(str(v) for row in rows for v in row)
        assert DEVICE_A not in flattened
        assert "99999999" not in flattened

    def test_requires_a_token(self, client):
        assert client.post("/v1/answers", json={"answers": []}).status_code == 401


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

    def test_a_row_flipped_to_rejected_after_approval_is_not_served(self, client):
        # In every OTHER test in this class, approved_seq is NULL unless
        # status is also 'approved', so "approved_seq > since" alone already
        # excludes everything not approved and "AND status = 'approved'" in
        # the route's WHERE clause never has to do any work -- the suite
        # would pass identically with that conjunct deleted. Task 5's reject
        # flow can flip status on a row that was already approved (and so
        # already has an approved_seq) without clearing the column, and this
        # is the only state where the status conjunct is load-bearing.
        tok = token(client)
        aid = str(uuid.uuid4())
        send_answer(client, tok, "q", "a joke", answer_id=aid)
        approve(aid)
        with db.pool.connection() as conn:
            conn.execute("UPDATE answer SET status = 'rejected' WHERE id = %s",
                        (aid,))
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
        # As the brief wrote it, this passes even with the reject route
        # missing entirely -- a pending answer was never served either.
        # Assert the row actually flipped to 'rejected' so a 404 or a
        # no-op can't pass this silently.
        tok = token(client)
        answer_id = self._pending_id(client, tok)
        self._admin(client).post(f"/admin/answers/{answer_id}/reject")
        with db.pool.connection() as conn:
            status = conn.execute(
                "SELECT status FROM answer WHERE id = %s",
                (answer_id,)).fetchone()[0]
        assert status == "rejected"
        assert download(client, tok)["answers"] == []

    def test_rejecting_sets_status_and_does_not_delete_the_row(self, client):
        # The brief's own test only checks the phone never sees it, which
        # would pass identically if reject deleted the row outright. A
        # rejected answer is supposed to be the record that somebody tried
        # and the camp said no -- so assert the row, and its status, survive.
        tok = token(client)
        answer_id = self._pending_id(client, tok)
        self._admin(client).post(f"/admin/answers/{answer_id}/reject")
        with db.pool.connection() as conn:
            row = conn.execute(
                "SELECT status FROM answer WHERE id = %s",
                (answer_id,)).fetchone()
        assert row is not None
        assert row[0] == "rejected"

    def test_approving_twice_does_not_move_the_cursor(self, client):
        # Otherwise a double-click re-delivers the same answer to every phone.
        # As the brief wrote it, `first is None == second is None` passes
        # even with the approve route missing (both reads are NULL). Assert
        # the first approval actually stamped a cursor before checking the
        # second one held it.
        tok = token(client)
        answer_id = self._pending_id(client, tok)
        admin = self._admin(client)
        admin.post(f"/admin/answers/{answer_id}/approve")
        with db.pool.connection() as conn:
            first = conn.execute("SELECT approved_seq FROM answer").fetchone()[0]
        assert first is not None
        admin.post(f"/admin/answers/{answer_id}/approve")
        with db.pool.connection() as conn:
            assert conn.execute(
                "SELECT approved_seq FROM answer").fetchone()[0] == first

    def test_approving_needs_the_admin_cookie(self, client):
        tok = token(client)
        answer_id = self._pending_id(client, tok)
        r = client.post(f"/admin/answers/{answer_id}/approve")
        # Not 401/403: _require deliberately answers 404 for a missing or
        # wrong cookie (admin.py:52-68), so nobody who doesn't already know
        # this page exists learns that it does.
        assert r.status_code == 404
        with db.pool.connection() as conn:
            status, seq = conn.execute(
                "SELECT status, approved_seq FROM answer").fetchone()
        assert status == "pending"
        assert seq is None

    def test_rejecting_needs_the_admin_cookie(self, client):
        # The brief only wrote this check for approve. Reject changes state
        # the same way and needs the same gate, or an unauthenticated caller
        # can silently kill an answer nobody has reviewed yet.
        tok = token(client)
        answer_id = self._pending_id(client, tok)
        r = client.post(f"/admin/answers/{answer_id}/reject")
        assert r.status_code == 404
        with db.pool.connection() as conn:
            status = conn.execute(
                "SELECT status FROM answer WHERE id = %s",
                (answer_id,)).fetchone()[0]
        assert status == "pending"

    def test_the_pending_list_shows_the_question_and_disappears_after_review(
            self, client):
        # A test that only hits the route and checks for 200 would pass
        # identically against a page that always renders "Nothing waiting" --
        # the brief's brief for this task never wrote that test at all.
        # Assert the actual question text is in the page, and that approving
        # it removes it from the list.
        tok = token(client)
        answer_id = self._pending_id(client, tok)
        admin = self._admin(client)
        page = admin.get("/admin/answers")
        assert page.status_code == 200
        assert "where is the medkit" in page.text
        assert "In Doris." in page.text
        admin.post(f"/admin/answers/{answer_id}/approve")
        after = admin.get("/admin/answers").text
        assert "where is the medkit" not in after

    def test_the_pending_list_needs_the_admin_cookie(self, client):
        tok = token(client)
        self._pending_id(client, tok)
        r = client.get("/admin/answers")
        assert r.status_code == 404

    def test_a_typed_answer_cannot_inject_markup_into_the_page(self, client):
        # A camp member's typed answer is data, never markup. If it were
        # dropped into the page unescaped, this closes the <div> early and
        # opens a live <script> tag in an admin's browser.
        tok = token(client)
        send_answer(client, tok, "<script>evil()</script>",
                    "<img src=x onerror=alert(1)>")
        page = self._admin(client).get("/admin/answers")
        assert "<script>evil()</script>" not in page.text
        assert "<img src=x onerror=alert(1)>" not in page.text
        assert "&lt;script&gt;" in page.text
