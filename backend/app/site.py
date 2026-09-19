"""The camp instructions page, behind a codeword.

Not HTTP basic auth. Basic auth means a browser dialog with a username field,
and the camp does not have usernames -- forty people would be typing "camp" in
a box they did not expect, on a phone, in a chrome-less popup that looks like
a phishing attempt. A page that says "enter the codeword" is the same security
and a tenth of the confusion.

Not a JavaScript gate either. The check is here, on the server, and the page
itself is never sent to a browser that has not passed it. A client-side prompt
in front of a static file is decoration: the file is one `curl` away.

Caddy asks `/v1/site-auth` about every request for the site and serves the
codeword page instead whenever it says no.
"""
import base64
import hashlib
import hmac
import time

from fastapi import APIRouter, Form, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from .settings import settings

router = APIRouter()

COOKIE = "lucy_site"
# Long, because the alternative is a camp member re-typing a codeword every
# time they reopen the page to check a step. The content is install
# instructions, not a bank.
TTL_SECONDS = 180 * 24 * 3600


def _secret() -> bytes:
    """Derived from the codeword itself, so there is no second secret to keep
    in step -- and changing the codeword invalidates every cookie already
    issued, which is what rotating it is supposed to mean."""
    return hashlib.sha256(("lucy-site-v1:" + settings.site_code).encode()).digest()


def _sign(expires: int) -> str:
    mac = hmac.new(_secret(), str(expires).encode(), hashlib.sha256).digest()
    return f"{expires}.{base64.urlsafe_b64encode(mac).decode().rstrip('=')}"


def _valid(value: str | None) -> bool:
    if not value or "." not in value:
        return False
    expires, _ = value.split(".", 1)
    if not expires.isdigit() or int(expires) < time.time():
        return False
    return hmac.compare_digest(value, _sign(int(expires)))


@router.get("/v1/site-auth")
def site_auth(request: Request) -> Response:
    """Whether this browser has passed the codeword. 200 or 401.

    No WWW-Authenticate header, deliberately: that header is the thing that
    makes a browser open the username-and-password dialog, which is the whole
    behaviour being replaced here.
    """
    if _valid(request.cookies.get(COOKIE)):
        return Response(status_code=200)
    return Response(status_code=401)


# What a browser is allowed to fetch once it has passed. An allow-list rather
# than a path-traversal check, because the site is five files and a directory
# of screenshots, and "is this path safe" is a question with a long history of
# wrong answers.
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".css": "text/css; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
}


@router.get("/{path:path}")
def serve_site(path: str, request: Request) -> Response:
    """The camp instructions, or the codeword page.

    Served from here rather than from Caddy. The Caddy version used
    forward_auth with a handle_response block, which silently never fired --
    the browser got a bare 401 and no page at all, and no amount of reading
    the config explained why. This is fifteen lines, it is covered by the test
    suite, and when it misbehaves the reason is in Python where it can be
    read.
    """
    root = settings.site_root

    # The link preview card, served to anyone who asks.
    #
    # A crawler has no cookie. WhatsApp, iMessage and Signal fetch the URL
    # the moment somebody pastes it into the camp thread, get the codeword
    # page, and read its meta tags -- so og:image has to resolve without
    # passing the gate or the preview is a grey box. It is one file and it is
    # the same words that are already on the outside of the door.
    if path in ("og.png", "favicon.ico"):
        target = root / path
        if target.is_file():
            return Response(content=target.read_bytes(),
                            media_type=CONTENT_TYPES.get(target.suffix,
                                                         "application/octet-stream"))
        raise HTTPException(status_code=404, detail="not here")

    # The public case study, deliberately outside the gate.
    #
    # /tech is Marcus's portfolio page about how the app is built. It shares
    # a domain with the camp site because it shares a deploy, but not the
    # gate: it carries no campmate data, no install instructions, and no
    # codeword hints -- that is a property of its CONTENT, kept true by hand
    # whenever tech.html or tech/ changes, not something this route can
    # check. Everything else in the site directory stays behind the cookie.
    if path == "tech" or path.startswith("tech/"):
        rel = "tech.html" if path == "tech" else path
        target = (root / rel).resolve()
        # Confined to tech.html and tech/ specifically, not merely to the
        # site root: everything ELSE under the root is the gated camp site,
        # so for this branch "inside the root" is precisely the escape that
        # matters. `tech/%2e%2e/index.html` decodes to a path this prefix
        # matches and must still find nothing.
        if target != (root / "tech.html").resolve() \
                and (root / "tech").resolve() not in target.parents:
            raise HTTPException(status_code=404, detail="not here")
        if not target.is_file() or target.suffix not in CONTENT_TYPES:
            raise HTTPException(status_code=404, detail="not here")
        return Response(content=target.read_bytes(),
                        media_type=CONTENT_TYPES[target.suffix])

    if not _valid(request.cookies.get(COOKIE)):
        gate = root / "gate.html"
        if not gate.is_file():
            raise HTTPException(status_code=503, detail="no site installed")
        # 200, not 401. A browser renders either, but 401 makes every proxy
        # and link preview in between treat the page as an error.
        return Response(content=gate.read_bytes(),
                        media_type=CONTENT_TYPES[".html"])

    rel = path or "index.html"
    if rel.endswith("/"):
        rel += "index.html"
    target = (root / rel).resolve()
    # Anything that resolves outside the site directory is not the site.
    if root.resolve() not in target.parents and target != root.resolve():
        raise HTTPException(status_code=404, detail="not here")
    if not target.is_file() or target.suffix not in CONTENT_TYPES:
        raise HTTPException(status_code=404, detail="not here")
    return Response(content=target.read_bytes(),
                    media_type=CONTENT_TYPES[target.suffix])


@router.post("/v1/site-unlock")
def site_unlock(code: str = Form(...)) -> Response:
    if not hmac.compare_digest(code.strip().lower(), settings.site_code.lower()):
        # Back to the form with a flag it can read. The codeword is never
        # echoed back into the page.
        return RedirectResponse("/?wrong=1", status_code=303)
    expires = int(time.time()) + TTL_SECONDS
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(COOKIE, _sign(expires), max_age=TTL_SECONDS,
                        httponly=True, secure=True, samesite="lax", path="/")
    return response
