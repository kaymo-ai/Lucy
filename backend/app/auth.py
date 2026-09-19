"""Identity: one phone, one member, one long-lived token.

No accounts, no email, no password, and nothing for a member to type. The app
carries the camp secret and presents it on its own; being on the TestFlight
invite list is the authentication, because Marcus adds people to it by Apple ID
and asking someone who already had to be invited to also type a password is
friction that buys nothing.

This is weak authentication and it is the correct amount. It keeps strangers
out of a camp feed; it is not protecting anything that would survive a
determined attacker who is also a Burning Man camp member.

LUCY_JOIN_CODE is a comma-separated list, and that is the whole rotation
story: the secret lives in the app binary, so changing it needs a new build,
and builds reach phones over days. Adding the new code beside the old one lets
both work while TestFlight catches up, and dropping the old one afterwards is
what actually rotates it.

The server always knows who said what, including for the features that will
later present as anonymous. That is a promise being made to the camp and it
should be stated plainly rather than implied.
"""
import hashlib
import hmac
import secrets
import uuid

from fastapi import APIRouter, Header, HTTPException

from . import db
from .models import JoinRequest, JoinResponse
from .settings import settings

router = APIRouter()


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def mint_token(member_id: uuid.UUID) -> str:
    token = secrets.token_urlsafe(32)
    with db.pool.connection() as conn:
        conn.execute(
            "INSERT INTO device_token (token_sha256, member_id) VALUES (%s, %s)",
            (_digest(token), str(member_id)))
    return token


@router.post("/v1/join", response_model=JoinResponse)
def join(req: JoinRequest) -> JoinResponse:
    # compare_digest, not ==. String equality returns on the first differing
    # byte, which hands the code out one character at a time to anyone patient
    # enough to time the responses. Every accepted code is compared, without
    # short-circuiting, so the number of comparisons does not leak either.
    accepted = [c.strip() for c in settings.join_code.split(",") if c.strip()]
    if not any([hmac.compare_digest(req.code, c) for c in accepted]):
        raise HTTPException(status_code=403,
                            detail="that code is not this year's")

    with db.pool.connection() as conn:
        conn.execute(
            "INSERT INTO member (id, display_name) VALUES (%s, %s)"
            " ON CONFLICT (id) DO UPDATE SET display_name = EXCLUDED.display_name",
            (str(req.deviceId), req.displayName))
    return JoinResponse(token=mint_token(req.deviceId), memberId=req.deviceId)


def current_member(authorization: str | None = Header(None)) -> uuid.UUID:
    """The dependency every protected route takes.

    401 and nothing else. A 403 for a well-formed but unknown token would tell
    an attacker their token shape was right, which is the only thing they
    cannot already see.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="no token")
    token = authorization.removeprefix("Bearer ").strip()
    with db.pool.connection() as conn:
        row = conn.execute(
            "UPDATE device_token SET last_seen_at = now()"
            " WHERE token_sha256 = %s RETURNING member_id",
            (_digest(token),)).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="no token")
    return row[0]
