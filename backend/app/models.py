"""The shapes that cross the wire.

Field names are camelCase because the other end is Swift and JSONSerialization
does no renaming. The database is snake_case; the translation happens here and
nowhere else.
"""
import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class JoinRequest(BaseModel):
    code: str
    deviceId: uuid.UUID
    # Optional, and in practice never sent. Notes are anonymous to the camp,
    # so there is no name to collect and nothing to ask anyone for. The field
    # survives because build 143 and earlier send it, and a phone that cannot
    # join is a phone whose notes are stuck on it.
    displayName: str = Field(default="", max_length=80)


class JoinResponse(BaseModel):
    token: str
    memberId: uuid.UUID


class NoteMeta(BaseModel):
    """The meta.json part of a note upload. Written by the phone."""
    noteId: uuid.UUID
    takenAt: datetime
    transcript: str = ""
    attached: list[str] = []
    appVersion: str = ""


class NoteUploaded(BaseModel):
    noteId: uuid.UUID
    seq: int
    duplicate: bool


class NoteSummary(BaseModel):
    """What the list carries.

    Deliberately not the media: the list is cheap and the media is not, and on
    a Starlink connection shared by fifty people that difference is the
    feature.

    Deliberately not the author either. Notes are anonymous to the camp -- no
    name, no member id, nothing a reader could join against. The server still
    records which phone sent what, because deleting your own note requires it,
    and that distinction is stated in the app rather than left implied.
    """
    id: uuid.UUID
    seq: int
    takenAt: datetime
    transcript: str
    attached: list[str]
    hasPhoto: bool
    hasMemo: bool


class NoteList(BaseModel):
    notes: list[NoteSummary]
    cursor: int


class ChatlogEntry(BaseModel):
    """One turn: a question and what Lucy answered.

    8000 characters holds any real turn several times over -- a question is a
    sentence and an answer is a few. What it does not hold is a fact sheet or
    a whole prompt, which is the failure this cap is for: the log stores what
    was said, never what the model was fed.
    """
    id: uuid.UUID
    question: str = Field(max_length=8000)
    answer: str = Field(max_length=8000)
    model: str | None = None
    askedAt: datetime
    # Whether retrieval found nothing, decided on the phone at the moment the
    # question was answered. Optional because an older app does not send it,
    # and because NULL means "not recorded" rather than "had material" -- the
    # gap query falls back to reading the answer's wording for those rows.
    unanswered: bool | None = None


class ChatlogUpload(BaseModel):
    # 500 a request. The phone batches whatever accumulated since the last
    # Sync; a week offline is a few hundred turns, so one request usually
    # clears the backlog, and anything past the cap is a bug or an attack.
    entries: list[ChatlogEntry] = Field(max_length=500)


class ChatlogStored(BaseModel):
    stored: int


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
