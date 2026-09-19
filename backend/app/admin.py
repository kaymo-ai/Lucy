"""The QA page: everything that has synced, as it actually arrived.

Behind its own code, not the camp codeword. That one is shared with forty
people and unlocks a page of install instructions; this one shows every note
in the camp and which install sent it, and the two should not be the same
secret.

It shows the sender, which the app deliberately does not. That is the
distinction the app states out loud rather than implies: notes are anonymous
to campers, and the server knows. This page is the server knowing.

Server-rendered, no JavaScript. The whole point is to see what arrived without
a layer in between that could be the thing that is wrong.
"""
import hashlib
import hmac
import html
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from . import db, media
from .answers import ANSWER_SEQ_LOCK
from .settings import settings

router = APIRouter()

COOKIE = "lucy_admin"
# Short, unlike the camp site's. This is the page that shows everything.
TTL_SECONDS = 14 * 24 * 3600


def _secret() -> bytes:
    return hashlib.sha256(("lucy-admin-v1:" + settings.admin_code).encode()).digest()


def _sign(expires: int) -> str:
    mac = hmac.new(_secret(), str(expires).encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{mac}"


def valid(value: str | None) -> bool:
    if not value or "." not in value:
        return False
    expires, _ = value.split(".", 1)
    if not expires.isdigit() or int(expires) < time.time():
        return False
    return hmac.compare_digest(value, _sign(int(expires)))


def _require(request: Request) -> None:
    if not valid(request.cookies.get(COOKIE)):
        raise HTTPException(status_code=404, detail="not here")


@router.post("/v1/admin-unlock")
def unlock(code: str = Form(...)) -> Response:
    if not hmac.compare_digest(code.strip(), settings.admin_code):
        return RedirectResponse("/admin?wrong=1", status_code=303)
    expires = int(time.time()) + TTL_SECONDS
    response = RedirectResponse("/admin", status_code=303)
    response.set_cookie(COOKIE, _sign(expires), max_age=TTL_SECONDS,
                        httponly=True, secure=True, samesite="lax", path="/")
    return response


# 404 rather than 401 for a wrong or missing admin cookie, everywhere except
# the gate itself. There is no reason for anyone who does not already know
# about this page to learn that it exists.
@router.get("/admin", response_class=HTMLResponse)
def admin(request: Request, wrong: int = 0) -> HTMLResponse:
    if not valid(request.cookies.get(COOKIE)):
        return HTMLResponse(_gate(wrong == 1))

    with db.pool.connection() as conn:
        rows = conn.execute(
            "SELECT id, seq, member_id, taken_at, received_at, transcript,"
            " photo_sha, memo_sha, app_version"
            " FROM note ORDER BY seq DESC LIMIT 300").fetchall()
        members = conn.execute("SELECT count(*) FROM member").fetchone()[0]
    return HTMLResponse(_page(rows, members))


@router.get("/admin/notes/{note_id}/photo")
def photo(note_id: str, request: Request) -> Response:
    _require(request)
    return _blob(note_id, "photo_sha", "image/jpeg")


@router.get("/admin/notes/{note_id}/memo")
def memo(note_id: str, request: Request) -> Response:
    _require(request)
    return _blob(note_id, "memo_sha", "audio/mp4")


def _blob(note_id: str, column: str, content_type: str) -> Response:
    # Two hardcoded literals, never a request parameter. See notes.py.
    with db.pool.connection() as conn:
        row = conn.execute(f"SELECT {column} FROM note WHERE id = %s",
                           (note_id,)).fetchone()
    if row is None or row[0] is None:
        raise HTTPException(status_code=404, detail="not here")
    blob = media.get(row[0])
    if blob is None:
        raise HTTPException(status_code=404, detail="not here")
    return Response(content=blob, media_type=content_type)


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


# --- rendering ---------------------------------------------------------------

CSS = """
:root{--ground:#16181C;--raised:#22262C;--text:#ECEDE9;--muted:#8B9199;
--accent:#F04A1E;--keep:#3E8B9C;--hair:rgba(236,237,233,0.15)}
*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--text);padding:0 20px 80px;
font:450 16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1100px;margin:0 auto}
h1{font-weight:900;font-stretch:condensed;text-transform:uppercase;
letter-spacing:-.02em;font-size:44px;margin:44px 0 6px}
.sub{color:var(--muted);font:400 14px ui-monospace,SFMono-Regular,Menlo,monospace;
margin-bottom:30px}
.note{display:flex;gap:18px;padding:18px 0;border-top:1px solid var(--hair)}
.shot{width:190px;flex:none}
.shot img{width:190px;border-radius:10px;display:block;background:var(--raised)}
.shot .none{width:190px;height:130px;border-radius:10px;background:var(--raised);
display:flex;align-items:center;justify-content:center;color:var(--muted);
font:600 11px ui-monospace,Menlo,monospace;letter-spacing:.1em}
.body{flex:1;min-width:0}
.said{font-size:17px;margin:0 0 10px;overflow-wrap:anywhere}
.said.empty{color:var(--muted)}
.said.warn{color:var(--accent)}
.meta{font:400 12px ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--muted);
display:flex;flex-wrap:wrap;gap:14px;margin-bottom:10px}
.meta b{color:var(--text);font-weight:600}
audio{width:100%;max-width:420px;height:34px}
.seq{color:var(--accent);font-weight:700}
form{max-width:340px;margin:80px auto 0;display:flex;flex-direction:column;gap:12px}
input,button{font:inherit;padding:14px 16px;border-radius:12px;border:1px solid var(--hair)}
input{background:var(--raised);color:var(--text)}
button{background:var(--accent);color:#fff;border:0;font-weight:800;
text-transform:uppercase;letter-spacing:.06em;cursor:pointer}
.wrong{color:var(--accent);text-align:center;margin-top:16px}
.empty-state{color:var(--muted);padding:40px 0}
"""


def _gate(wrong: bool) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>QA</title><style>{CSS}</style></head><body><div class="wrap">
<form method="post" action="/v1/admin-unlock">
<input type="text" name="code" placeholder="Code" autocomplete="off"
 autocapitalize="none" autocorrect="off" spellcheck="false" required autofocus>
<button type="submit">Open</button>
</form>{'<p class="wrong">No.</p>' if wrong else ''}
</div></body></html>"""


def _ago(then: datetime) -> str:
    seconds = (datetime.now(timezone.utc) - then).total_seconds()
    if seconds < 90:
        return f"{int(seconds)}s ago"
    if seconds < 5400:
        return f"{int(seconds // 60)}m ago"
    if seconds < 172800:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


def _page(rows, members: int) -> str:
    if not rows:
        body = '<p class="empty-state">Nothing has synced yet.</p>'
    else:
        body = "".join(_note(r) for r in rows)
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>QA — what has synced</title><style>{CSS}</style></head><body><div class="wrap">
<h1>What has synced</h1>
<p class="sub">{len(rows)} note{'' if len(rows) == 1 else 's'} ·
{members} install{'' if members == 1 else 's'} ·
server time {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}</p>
{body}</div></body></html>"""


def _note(r) -> str:
    (note_id, seq, member_id, taken_at, received_at, transcript,
     photo_sha, memo_sha, app_version) = r
    # A photo with nothing said is an ordinary note -- half of them are, when
    # your hands are full of the thing you are describing. A *memo* with no
    # transcript is not ordinary: it means on-device recognition produced
    # nothing, and that is worth the page saying so rather than dressing both
    # up as "no transcript" and letting a real failure look like a normal one.
    said = html.escape(transcript.strip()) if transcript.strip() else None
    if said is None:
        blank = ("Voice memo, not transcribed" if memo_sha else "Photo only")
    else:
        blank = None
    shot = (f'<img src="/admin/notes/{note_id}/photo" alt="">' if photo_sha
            else '<div class="none">NO PHOTO</div>')
    audio = (f'<audio controls preload="none" '
             f'src="/admin/notes/{note_id}/memo"></audio>' if memo_sha else "")
    # The install, not a name. Eight characters is enough to tell two phones
    # apart across a page and not enough to be mistaken for an identity.
    install = str(member_id)[:8]
    return f"""<div class="note">
<div class="shot">{shot}</div>
<div class="body">
<p class="said{'' if said else (' warn' if memo_sha else ' empty')}">{said or blank}</p>
<div class="meta">
<span class="seq">#{seq}</span>
<span>taken <b>{taken_at.strftime('%a %d %b %H:%M')}</b></span>
<span>synced <b>{_ago(received_at)}</b></span>
<span>install <b>{install}</b></span>
<span>app <b>{html.escape(app_version or '?')}</b></span>
</div>{audio}</div></div>"""
