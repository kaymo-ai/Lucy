"""The Lucy camp server.

One HTTP service. It is deliberately plain -- FastAPI, Postgres, files on disk,
nothing that assumes a cloud -- because the same compose file has to run on a
box behind a camp router with no internet, and that is the whole reason this is
not Firebase.
"""
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI

from . import admin, answers, auth, chatlog, db, notes, site
from .settings import require_production_config


@asynccontextmanager
async def lifespan(app: FastAPI):
    require_production_config()
    db.migrate()
    yield


# docs_url off on purpose: an interactive API explorer on a public host is an
# invitation, and nobody in this camp is going to use it.
app = FastAPI(title="Lucy camp server", docs_url=None, redoc_url=None,
              lifespan=lifespan)

app.include_router(auth.router)
app.include_router(notes.router)
app.include_router(chatlog.router)
app.include_router(answers.router)
app.include_router(admin.router)


@app.get("/v1/health")
def health() -> dict:
    """The reachability probe the phone hits before anything else.

    It does not touch the database. A probe that failed because Postgres was
    slow would tell someone "no signal" while they are standing under
    Starlink, and the whole point of this endpoint is to distinguish those.
    """
    return {"ok": True, "time": datetime.now(timezone.utc).isoformat()}


@app.get("/v1/whoami")
def whoami(member_id: uuid.UUID = Depends(auth.current_member)) -> dict:
    """Lets the phone check its token is still good without uploading
    anything, and gives the auth dependency something to be tested against
    that has no other moving parts."""
    return {"memberId": str(member_id)}


# Last, deliberately. The site router ends in a catch-all GET /{path:path};
# registered any earlier it would swallow /v1/health and everything else.
app.include_router(site.router)
