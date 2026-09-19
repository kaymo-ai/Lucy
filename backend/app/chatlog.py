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
migrations/005_refusal_shapes.sql (originally defined in
migrations/003_help_lucy.sql; 005 replaces its body, see that file for why it
is a separate migration). It is the same phrase list as deadEnd() in
Lucy/ChatView.swift, matched and normalised the same way. If deadEnd() grows a
phrase, this function grows the same one, or the new refusal shape silently
stops counting as a gap.
"""
import uuid

from fastapi import APIRouter, Depends

from . import db
from .auth import current_member
from .models import ChatlogStored, ChatlogUpload

router = APIRouter()


@router.post("/v1/chatlog", response_model=ChatlogStored)
def upload_chatlog(
    body: ChatlogUpload,
    member_id: uuid.UUID = Depends(current_member),
) -> ChatlogStored:
    # No advisory lock, unlike notes: there is no seq here because nothing
    # polls chatlog, so there is no assignment order to protect.
    stored = 0
    with db.pool.connection() as conn:
        with conn.transaction():
            for entry in body.entries:
                # The id came from the phone. A retry of a batch that already
                # landed -- or of a batch that half-landed before the
                # connection died -- inserts only what is missing, and a
                # response of stored=0 is a success, not an error.
                result = conn.execute(
                    "INSERT INTO chatlog (id, question, answer, model,"
                    " asked_at, unanswered)"
                    " VALUES (%s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (id) DO NOTHING",
                    (str(entry.id), entry.question, entry.answer, entry.model,
                     entry.askedAt, entry.unanswered))
                stored += result.rowcount
    return ChatlogStored(stored=stored)
