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
from .models import (AnswerList, AnswerStored, AnswerUpload, ApprovedAnswer,
                     GapList, GapSummary)

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
        # The brief's original query put the EXISTS subquery directly in the
        # SELECT list of the grouped query, correlated on c.question. Postgres
        # matches a subquery's outer reference against GROUP BY as a bare
        # column, so lucy_gap_key(c.question) in GROUP BY never matches the
        # correlated c.question and every run fails with "subquery uses
        # ungrouped column c.question from outer query" -- not a corner case,
        # every single call. Wrapping the aggregation in a subquery gives the
        # EXISTS a real output column (t.gap_key) to correlate on instead.
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
            # The phone records whether retrieval found anything, so new
            # rows are classified by what happened rather than by how the
            # answer was worded. NULL means the turn predates the column;
            # its wording is the only evidence about it, so lucy_is_refusal
            # stays as the decoder for those. Matches chatlog_gap_idx in
            # 006 exactly -- a partial index is only used when the query
            # predicate is the same expression.
            "   WHERE COALESCE(c.unanswered, lucy_is_refusal(c.answer))"
            "   GROUP BY lucy_gap_key(c.question)"
            " ) t"
            " ORDER BY t.asked DESC, t.last_asked DESC"
            " LIMIT %s", (limit,)).fetchall()
    gaps = [GapSummary(gapKey=r[0], question=r[1], asked=r[2], answered=r[3])
            for r in rows]
    return GapList(gaps=gaps, cursor=since)


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
