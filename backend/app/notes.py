"""Notes: upload, list, fetch media.

Notes are immutable once uploaded. A member will later be able to delete their
own, which tombstones the row; nothing here ever edits one, and a retry of an
upload already stored returns what is stored rather than replacing it.
"""
import json
import uuid

from fastapi import (APIRouter, Depends, File, HTTPException, Response,
                     UploadFile)

from . import db, media
from .auth import current_member
from .models import NoteList, NoteMeta, NoteSummary, NoteUploaded

router = APIRouter()

# A photo off an iPhone is ~2-4 MB and a minute of AAC is ~500 KB. These caps
# are generous for both and small enough that a bug cannot fill the disk in an
# afternoon. Caddy enforces a 30 MB request body on top of this.
MAX_PHOTO = 12 * 1024 * 1024
MAX_MEMO = 25 * 1024 * 1024

# Any constant. It only has to be the same number in every process that inserts
# a note, and there is exactly one.
NOTE_SEQ_LOCK = 4242


@router.post("/v1/notes", response_model=NoteUploaded)
async def upload_note(
    meta: UploadFile = File(...),
    photo: UploadFile | None = File(None),
    memo: UploadFile | None = File(None),
    member_id: uuid.UUID = Depends(current_member),
) -> NoteUploaded:
    try:
        parsed = NoteMeta.model_validate(json.loads(await meta.read()))
    except Exception:
        raise HTTPException(status_code=400, detail="meta.json is not readable")

    photo_bytes = await photo.read() if photo is not None else None
    memo_bytes = await memo.read() if memo is not None else None

    # A voice note on its own is a whole note: half the useful ones are said,
    # not photographed, because your hands are full of the thing you are
    # describing. A note with neither is a bug on the phone.
    if not photo_bytes and not memo_bytes:
        raise HTTPException(status_code=400,
                            detail="a note needs a photo or a memo")
    if photo_bytes and len(photo_bytes) > MAX_PHOTO:
        raise HTTPException(status_code=413, detail="photo too large")
    if memo_bytes and len(memo_bytes) > MAX_MEMO:
        raise HTTPException(status_code=413, detail="memo too large")

    with db.pool.connection() as conn:
        existing = conn.execute("SELECT seq FROM note WHERE id = %s",
                                (str(parsed.noteId),)).fetchone()
    if existing is not None:
        return NoteUploaded(noteId=parsed.noteId, seq=existing[0],
                            duplicate=True)

    photo_sha = media.put(photo_bytes) if photo_bytes else None
    memo_sha = media.put(memo_bytes) if memo_bytes else None

    with db.pool.connection() as conn:
        with conn.transaction():
            # seq comes from a BIGSERIAL, and a sequence hands out numbers in
            # request order while transactions commit in whatever order they
            # finish. A client polling ?since=N could therefore step over a
            # lower seq that committed later and never see that note again.
            # This lock serialises assignment with commit, which at a camp's
            # write rate costs nothing.
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (NOTE_SEQ_LOCK,))
            row = conn.execute(
                "INSERT INTO note (id, member_id, taken_at, transcript,"
                " attached, photo_sha, memo_sha, app_version)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (id) DO NOTHING RETURNING seq",
                (str(parsed.noteId), str(member_id), parsed.takenAt,
                 parsed.transcript, parsed.attached, photo_sha, memo_sha,
                 parsed.appVersion)).fetchone()
            if row is None:
                # Two uploads of the same note raced. The other one won.
                seq = conn.execute("SELECT seq FROM note WHERE id = %s",
                                   (str(parsed.noteId),)).fetchone()[0]
                return NoteUploaded(noteId=parsed.noteId, seq=seq,
                                    duplicate=True)
            return NoteUploaded(noteId=parsed.noteId, seq=row[0],
                                duplicate=False)


@router.get("/v1/notes", response_model=NoteList)
def list_notes(since: int = 0, limit: int = 200,
               member_id: uuid.UUID = Depends(current_member)) -> NoteList:
    """Everyone's notes, not just yours, and with nobody's name on them.

    That is the point of sharing them: so nobody notes the same thing twice,
    which only works if you can see what is already there.
    """
    limit = max(1, min(limit, 500))
    with db.pool.connection() as conn:
        # No join to member. The author is not selected here at all, so it
        # cannot be leaked by a later edit that adds a field to the response
        # without thinking about it.
        rows = conn.execute(
            "SELECT id, seq, taken_at, transcript, attached, photo_sha, memo_sha"
            " FROM note WHERE seq > %s ORDER BY seq LIMIT %s",
            (since, limit)).fetchall()
    notes = [NoteSummary(id=r[0], seq=r[1], takenAt=r[2], transcript=r[3],
                         attached=r[4], hasPhoto=r[5] is not None,
                         hasMemo=r[6] is not None)
             for r in rows]
    # The caller's cursor holds when nothing is new, rather than resetting to
    # zero and re-reporting the whole week as unseen.
    return NoteList(notes=notes, cursor=notes[-1].seq if notes else since)


def _blob(note_id: uuid.UUID, column: str, content_type: str) -> Response:
    # `column` is interpolated into SQL. That is safe here and only here: this
    # is called from two places with two hardcoded literals and is not
    # reachable from a request parameter. Do not extend it to take a
    # caller-supplied column.
    with db.pool.connection() as conn:
        row = conn.execute(f"SELECT {column} FROM note WHERE id = %s",
                           (str(note_id),)).fetchone()
    if row is None or row[0] is None:
        raise HTTPException(status_code=404, detail="not here")
    blob = media.get(row[0])
    if blob is None:
        raise HTTPException(status_code=404, detail="not here")
    return Response(content=blob, media_type=content_type)


@router.get("/v1/notes/{note_id}/photo")
def note_photo(note_id: uuid.UUID,
               member_id: uuid.UUID = Depends(current_member)) -> Response:
    return _blob(note_id, "photo_sha", "image/jpeg")


@router.get("/v1/notes/{note_id}/memo")
def note_memo(note_id: uuid.UUID,
              member_id: uuid.UUID = Depends(current_member)) -> Response:
    return _blob(note_id, "memo_sha", "audio/mp4")
