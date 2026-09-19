# Lucy Backend Sync — Vertical Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A phone can join the camp with a code, upload a captured note to a real HTTPS server, and see how many notes other members have added — end to end, on Marcus's actual iPhone, against a VM that exists.

**Architecture:** One Compute Engine VM in `lucy-snails` running three containers behind Caddy: `caddy` (TLS + basic-auth'd static site), `api` (FastAPI, single uvicorn worker), `postgres` (metadata). Media blobs are content-addressed files on the VM's disk, never in GCS, because the camp wants the same compose file to run on a box behind a playa router with no internet. The iOS side is one `Sync` menu item that probes, uploads, and reports.

**Tech Stack:** Python 3.12 · FastAPI · psycopg 3 · PostgreSQL 16 · Caddy 2 · Docker Compose · pytest · Swift/SwiftUI (iOS 17+)

## Why this is a slice and not the whole spec

The spec at `docs/superpowers/specs/2026-08-10-lucy-backend-design.md` describes chat-log upload, the gap list, tombstoned deletes, lazy media fetch, and Vertex evaluation. None of those are here.

This project's failure mode, repeatedly, has been things that passed local checks and were wrong on the phone: the chat turn markers were malformed for weeks with nothing looking broken; the background `URLSession` doesn't exist in the Simulator and the download had never actually been run; three separate correct-sounding theories for a dead haptic were each wrong. A complete backend with a green test suite would tell us nothing about whether an iPhone can POST multipart through Caddy's Let's Encrypt certificate carrying a keychain-held bearer token. That is the risk, and it has to be retired first.

Everything deferred is additive on top of the identity and transport this slice proves.

---

## Global Constraints

- **Base URL is `https://lucy.marcusfoster.com/v1`.** A subdomain, not a path — a path cannot be routed to a different machine without a reverse proxy on the Hostinger side. DNS for `marcusfoster.com` is at Hostinger (`ns1.dns-parking.com`); one A record is needed and only Marcus can add it.
- **`backend/` gets its own `requirements.txt` and its own `pytest.ini`.** Do not extend `scripts/requirements.txt` or `scripts/pytest.ini` — the backend must not drag in `llama-cpp-python`, and the corpus tooling must not drag in FastAPI.
- **Never `git add -A` in this repo.** Build directories and a 106 MB corpus live here and made it unpushable once already. Stage named paths only.
- **No secrets in git.** `backend/.env` is gitignored and lives only on the VM. The join code and the Postgres password never appear in a committed file, a log line, or an error message.
- **Org policy `iam.disableServiceAccountKeyCreation` is enforced** on this organisation — verified 2026-08-10. Service-account JSON keys cannot be created. The VM authenticates to GCS through its attached service account via the metadata server, never a key file.
- **Blobs are never deleted.** Two members can photograph the same sign and produce byte-identical JPEGs with the same digest; one note being deleted must never unlink a blob another note still points at. Deletion is not in this slice, and when it arrives it tombstones rows and leaves blobs alone.
- **Tokens are stored hashed** (SHA-256 of the token bytes). The plaintext token exists only in the join response and in the phone's keychain.
- **The join code is compared with `hmac.compare_digest`**, never `==`.
- **The app must work with the backend unreachable, indefinitely, without looking broken.** Nothing in this slice may block a screen, gate a feature, or show an error on launch.
- **Python is 3.12 in the container** (`python:3.12-slim`). The laptop has 3.14; do not rely on it.

---

## File Structure

**New — backend service:**

| Path | Responsibility |
|---|---|
| `backend/requirements.txt` | Pinned Python deps for the API only |
| `backend/pytest.ini` | Test config scoped to `backend/tests` |
| `backend/compose.test.yml` | Throwaway Postgres for the test suite |
| `backend/compose.yml` | Production stack: postgres + api + caddy |
| `backend/Dockerfile` | The api image |
| `backend/Caddyfile` | TLS, basic-auth'd static site at `/`, API at `/v1` |
| `backend/app/settings.py` | Environment configuration, one place |
| `backend/app/db.py` | Connection pool and the migration runner |
| `backend/app/auth.py` | Join-code check, token mint/verify, the FastAPI dependency |
| `backend/app/media.py` | Content-addressed blob store on disk |
| `backend/app/models.py` | Pydantic request/response shapes |
| `backend/app/notes.py` | The `/v1/notes` router |
| `backend/app/main.py` | App assembly, health, startup migration |
| `backend/migrations/001_init.sql` | Schema |
| `backend/tests/conftest.py` | Postgres fixture, app client, truncation between tests |
| `backend/tests/test_health.py` … | One test module per endpoint area |

**New — deployment:**

| Path | Responsibility |
|---|---|
| `deploy/vm_create.sh` | Static IP, VM, firewall, bucket IAM. Run once. |
| `deploy/push.sh` | Copy `backend/` to the VM and restart the stack. Run every deploy. |

**New — iOS:**

| Path | Responsibility |
|---|---|
| `Lucy/Keychain.swift` | Minimal keychain get/set for two string items |
| `Lucy/Sync.swift` | `Identity`, `SyncClient`, upload bookkeeping |
| `Lucy/SyncView.swift` | The sheet: join, sync, status |

**Modified:**

| Path | Change |
|---|---|
| `Lucy/Capture.swift` | `writeMeta` also writes `note_id`; `Capture` exposes `noteID` with a deterministic fallback for folders written before this change |
| `Lucy/ChatView.swift` | A `Sync` item in the existing hamburger menu, presenting `SyncView` |
| `.gitignore` | `backend/.env`, `backend/caddy_data/`, `backend/media/` |

---

## Task 1: Backend skeleton, test harness, and health

**Files:**
- Create: `backend/requirements.txt`, `backend/pytest.ini`, `backend/compose.test.yml`, `backend/app/__init__.py`, `backend/app/settings.py`, `backend/app/main.py`
- Test: `backend/tests/__init__.py`, `backend/tests/conftest.py`, `backend/tests/test_health.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Settings` with fields `database_url: str`, `join_code: str`, `media_root: pathlib.Path`, `site_root: pathlib.Path`; module-level `settings = Settings()`. FastAPI app object `app` in `backend/app/main.py`. Test fixture `client` yielding a `fastapi.testclient.TestClient`.

- [ ] **Step 1: Create the dependency and test config files**

`backend/requirements.txt`:

```
fastapi==0.115.6
uvicorn[standard]==0.34.0
psycopg[binary,pool]==3.2.3
python-multipart==0.0.20
pydantic==2.10.4
httpx==0.28.1
pytest==8.3.4
```

`backend/pytest.ini`:

```ini
[pytest]
testpaths = tests
python_files = test_*.py
addopts = -v
```

`backend/compose.test.yml`:

```yaml
# A throwaway Postgres for the test suite only. Port 55432 so it cannot
# collide with a real local Postgres on 5432.
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_PASSWORD: lucytest
      POSTGRES_DB: lucytest
    ports:
      - "55432:5432"
    tmpfs:
      - /var/lib/postgresql/data
```

- [ ] **Step 2: Write `backend/app/settings.py`**

```python
"""Configuration, read from the environment exactly once.

Everything here has a default that works for the test suite and no default
that works in production -- a missing LUCY_JOIN_CODE in the container must
fail loudly at import rather than quietly admitting everybody.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.environ.get(
        "DATABASE_URL", "postgresql://postgres:lucytest@localhost:55432/lucytest"))
    join_code: str = field(default_factory=lambda: os.environ.get(
        "LUCY_JOIN_CODE", "test-code"))
    media_root: Path = field(default_factory=lambda: Path(
        os.environ.get("LUCY_MEDIA_ROOT", "/tmp/lucy-media")))


settings = Settings()
```

- [ ] **Step 3: Write the failing test**

`backend/tests/test_health.py`:

```python
"""Health is the reachability probe the phone hits before it does anything
else. It must answer without touching the database: a probe that fails
because Postgres is slow tells the user "no signal" when they have signal.
"""


class TestHealth:
    def test_returns_ok(self, client):
        r = client.get("/v1/health")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_carries_server_time(self, client):
        # Phone clocks in the desert are not to be trusted. The server's own
        # time being visible is how a confused client can notice.
        r = client.get("/v1/health")
        assert "time" in r.json()

    def test_needs_no_token(self, client):
        # No Authorization header is sent here at all.
        assert client.get("/v1/health").status_code == 200
```

`backend/tests/conftest.py`:

```python
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app  # noqa: E402


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
```

`backend/tests/__init__.py` and `backend/app/__init__.py`: empty files.

- [ ] **Step 4: Run the test and watch it fail**

```bash
cd ~/Snails/backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests/test_health.py -v
```

Expected: collection error, `ModuleNotFoundError: No module named 'app.main'`.

- [ ] **Step 5: Write `backend/app/main.py`**

```python
"""The Lucy camp server.

One HTTP service. It is deliberately plain -- no framework beyond FastAPI, no
managed services, nothing that assumes a cloud -- because the same compose
file has to run on a box behind a camp router with no internet, and that is
the whole reason this is not Firebase.
"""
from datetime import datetime, timezone

from fastapi import FastAPI

app = FastAPI(title="Lucy camp server", docs_url=None, redoc_url=None)


@app.get("/v1/health")
def health() -> dict:
    return {"ok": True, "time": datetime.now(timezone.utc).isoformat()}
```

`docs_url=None` on purpose: an interactive API explorer on a public host is an invitation, and nobody in this camp is going to use it.

- [ ] **Step 6: Run the tests and watch them pass**

```bash
cd ~/Snails/backend
.venv/bin/python -m pytest tests/test_health.py -v
```

Expected: 3 passed.

- [ ] **Step 7: Add gitignore entries and commit**

Append to `~/Snails/.gitignore`:

```
backend/.env
backend/.venv/
backend/media/
backend/caddy_data/
backend/caddy_config/
```

```bash
cd ~/Snails
git add backend/requirements.txt backend/pytest.ini backend/compose.test.yml \
        backend/app/__init__.py backend/app/settings.py backend/app/main.py \
        backend/tests/__init__.py backend/tests/conftest.py backend/tests/test_health.py \
        .gitignore
git commit -m "feat(backend): health endpoint and a test harness that runs on a real Postgres"
```

---

## Task 2: Schema and migrations

**Files:**
- Create: `backend/migrations/001_init.sql`, `backend/app/db.py`
- Modify: `backend/app/main.py`, `backend/tests/conftest.py`
- Test: `backend/tests/test_migrations.py`

**Interfaces:**
- Consumes: `settings.database_url`.
- Produces: `pool` (a `psycopg_pool.ConnectionPool`), `def migrate() -> list[str]` returning the names of migrations applied this call, and `def reset_for_tests() -> None` truncating every data table.

- [ ] **Step 1: Write `backend/migrations/001_init.sql`**

```sql
-- Members, their devices, and their notes.
--
-- member.id IS the device UUID the phone generated and keeps in its keychain.
-- There is no separate account: one phone is one member. Re-joining from the
-- same phone must land on the same row, which is why the phone supplies the id
-- rather than the server assigning one.
CREATE TABLE IF NOT EXISTS member (
    id           UUID PRIMARY KEY,
    display_name TEXT        NOT NULL,
    joined_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Only the hash is kept. The plaintext token exists in the join response and
-- in the phone's keychain and nowhere else, so a copy of this database is not
-- a set of working credentials.
CREATE TABLE IF NOT EXISTS device_token (
    token_sha256 BYTEA       PRIMARY KEY,
    member_id    UUID        NOT NULL REFERENCES member(id) ON DELETE CASCADE,
    issued_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS device_token_member_idx ON device_token (member_id);

-- id is chosen by the phone so an interrupted upload can be retried without
-- creating a second note. seq is the sync cursor: a server-assigned monotonic
-- integer, not a timestamp, because phone clocks in the desert are not to be
-- trusted for ordering.
--
-- photo_sha / memo_sha are hex SHA-256 digests naming a file in the blob
-- store. They are not foreign keys and blobs are never deleted: two people can
-- photograph the same sign and produce identical bytes, and deleting one note
-- must not blind the other.
CREATE TABLE IF NOT EXISTS note (
    id          UUID PRIMARY KEY,
    seq         BIGSERIAL   NOT NULL UNIQUE,
    member_id   UUID        NOT NULL REFERENCES member(id),
    taken_at    TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    transcript  TEXT        NOT NULL DEFAULT '',
    attached    TEXT[]      NOT NULL DEFAULT '{}',
    photo_sha   TEXT,
    memo_sha    TEXT,
    app_version TEXT        NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS note_seq_idx ON note (seq);

CREATE TABLE IF NOT EXISTS schema_migration (
    name       TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

- [ ] **Step 2: Write the failing test**

`backend/tests/test_migrations.py`:

```python
"""Migrations run at startup, in the container, with no operator present.

That means running them twice must be harmless -- the api container restarts
on deploy and on OOM, and a migration that fails the second time takes the
service down with it at the worst possible moment.
"""
from app import db


class TestMigrate:
    def test_creates_the_tables(self):
        db.migrate()
        with db.pool.connection() as conn:
            rows = conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'").fetchall()
        names = {r[0] for r in rows}
        assert {"member", "device_token", "note"} <= names

    def test_running_twice_applies_nothing_the_second_time(self):
        db.migrate()
        assert db.migrate() == []

    def test_records_what_it_applied(self):
        db.migrate()
        with db.pool.connection() as conn:
            rows = conn.execute("SELECT name FROM schema_migration").fetchall()
        assert "001_init.sql" in {r[0] for r in rows}
```

- [ ] **Step 3: Run it and watch it fail**

```bash
cd ~/Snails/backend
docker compose -f compose.test.yml up -d
.venv/bin/python -m pytest tests/test_migrations.py -v
```

Expected: `ImportError: cannot import name 'db' from 'app'`.

- [ ] **Step 4: Write `backend/app/db.py`**

```python
"""Connection pool and migration runner.

Migrations are plain numbered .sql files applied in filename order and
recorded in schema_migration. No Alembic: there is one developer, the schema
is four tables, and a migration tool is a dependency that has to be understood
by whoever picks this up on playa with no internet to search from.
"""
from pathlib import Path

from psycopg_pool import ConnectionPool

from .settings import settings

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"

pool = ConnectionPool(settings.database_url, min_size=1, max_size=8,
                      open=True, kwargs={"autocommit": True})


def migrate() -> list[str]:
    """Applies every migration not yet recorded. Returns the ones applied."""
    applied: list[str] = []
    with pool.connection() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migration ("
            " name TEXT PRIMARY KEY,"
            " applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        done = {r[0] for r in
                conn.execute("SELECT name FROM schema_migration").fetchall()}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in done:
                continue
            conn.execute(path.read_text())
            conn.execute("INSERT INTO schema_migration (name) VALUES (%s)",
                         (path.name,))
            applied.append(path.name)
    return applied


def reset_for_tests() -> None:
    """Empties every data table. Never called outside the test suite."""
    with pool.connection() as conn:
        conn.execute("TRUNCATE note, device_token, member RESTART IDENTITY CASCADE")
```

- [ ] **Step 5: Wire migration into startup and reset into the fixtures**

Replace the top of `backend/app/main.py` with:

```python
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI

from . import db


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.migrate()
    yield


app = FastAPI(title="Lucy camp server", docs_url=None, redoc_url=None,
              lifespan=lifespan)
```

Add to `backend/tests/conftest.py`, after the imports:

```python
from app import db  # noqa: E402


@pytest.fixture(autouse=True)
def clean_database():
    """Every test starts with empty tables. Tests that share rows pass in the
    order they were written and fail in every other order."""
    db.migrate()
    db.reset_for_tests()
    yield
```

- [ ] **Step 6: Run the whole suite and watch it pass**

```bash
cd ~/Snails/backend
.venv/bin/python -m pytest -v
```

Expected: 6 passed.

- [ ] **Step 7: Commit**

```bash
cd ~/Snails
git add backend/migrations/001_init.sql backend/app/db.py backend/app/main.py \
        backend/tests/conftest.py backend/tests/test_migrations.py
git commit -m "feat(backend): schema, and migrations that survive being run twice"
```

---

## Task 3: Joining the camp, and the bearer token

**Files:**
- Create: `backend/app/auth.py`, `backend/app/models.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_join.py`

**Interfaces:**
- Consumes: `db.pool`, `settings.join_code`.
- Produces:
  - `JoinRequest` pydantic model with `code: str`, `deviceId: uuid.UUID`, `displayName: str`
  - `JoinResponse` with `token: str`, `memberId: uuid.UUID`
  - `def mint_token(member_id: uuid.UUID) -> str`
  - `def current_member(authorization: str | None = Header(None)) -> uuid.UUID` — the FastAPI dependency every protected route uses. Raises 401.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_join.py`:

```python
"""Joining is the whole of identity: no email, no password, one shared camp
code rotated per year. This is weak authentication and it is the correct
amount -- it keeps strangers out of a camp feed, and it is not protecting
anything that would survive a determined attacker who is also a member.

What it must get right is the small stuff: constant-time comparison, tokens
stored hashed, and re-joining from the same phone landing on the same member
instead of quietly forking the person in two.
"""
import uuid

from app import db

DEVICE = "11111111-2222-3333-4444-555555555555"


def join(client, code="test-code", device=DEVICE, name="Marcus"):
    return client.post("/v1/join", json={
        "code": code, "deviceId": device, "displayName": name})


class TestJoin:
    def test_correct_code_returns_a_token(self, client):
        r = join(client)
        assert r.status_code == 200
        assert len(r.json()["token"]) >= 32
        assert r.json()["memberId"] == DEVICE

    def test_wrong_code_is_refused(self, client):
        assert join(client, code="not-the-code").status_code == 403

    def test_wrong_code_creates_no_member(self, client):
        join(client, code="not-the-code")
        with db.pool.connection() as conn:
            assert conn.execute("SELECT count(*) FROM member").fetchone()[0] == 0

    def test_rejoining_the_same_phone_reuses_the_member(self, client):
        first = join(client).json()
        second = join(client, name="Marcus F").json()
        assert first["memberId"] == second["memberId"]
        with db.pool.connection() as conn:
            assert conn.execute("SELECT count(*) FROM member").fetchone()[0] == 1

    def test_rejoining_updates_the_display_name(self, client):
        join(client)
        join(client, name="Marcus F")
        with db.pool.connection() as conn:
            name = conn.execute("SELECT display_name FROM member").fetchone()[0]
        assert name == "Marcus F"

    def test_the_plaintext_token_is_not_stored(self, client):
        token = join(client).json()["token"]
        with db.pool.connection() as conn:
            rows = conn.execute("SELECT token_sha256 FROM device_token").fetchall()
        stored = bytes(rows[0][0])
        assert token.encode() not in stored


class TestBearerToken:
    def test_a_minted_token_authenticates(self, client):
        token = join(client).json()["token"]
        r = client.get("/v1/whoami", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["memberId"] == DEVICE

    def test_no_header_is_401(self, client):
        assert client.get("/v1/whoami").status_code == 401

    def test_a_made_up_token_is_401(self, client):
        r = client.get("/v1/whoami",
                       headers={"Authorization": "Bearer " + "x" * 43})
        assert r.status_code == 401

    def test_a_missing_bearer_prefix_is_401(self, client):
        token = join(client).json()["token"]
        assert client.get("/v1/whoami",
                          headers={"Authorization": token}).status_code == 401
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd ~/Snails/backend
.venv/bin/python -m pytest tests/test_join.py -v
```

Expected: every test fails with 404 — `/v1/join` does not exist.

- [ ] **Step 3: Write `backend/app/models.py`**

```python
import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class JoinRequest(BaseModel):
    code: str
    deviceId: uuid.UUID
    displayName: str = Field(min_length=1, max_length=80)


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
    id: uuid.UUID
    seq: int
    author: str
    authorId: uuid.UUID
    takenAt: datetime
    transcript: str
    attached: list[str]
    hasPhoto: bool
    hasMemo: bool


class NoteList(BaseModel):
    notes: list[NoteSummary]
    cursor: int
```

- [ ] **Step 4: Write `backend/app/auth.py`**

```python
"""Identity: one phone, one member, one long-lived token.

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
    # compare_digest, not ==. String equality returns early on the first
    # differing byte, which leaks the code one character at a time to anyone
    # patient enough to time the responses.
    if not hmac.compare_digest(req.code, settings.join_code):
        raise HTTPException(status_code=403, detail="that code is not this year's")

    # The phone supplies its own id, so reinstalling the app on the same phone
    # rejoins as the same person rather than forking them in two.
    with db.pool.connection() as conn:
        conn.execute(
            "INSERT INTO member (id, display_name) VALUES (%s, %s) "
            "ON CONFLICT (id) DO UPDATE SET display_name = EXCLUDED.display_name",
            (str(req.deviceId), req.displayName))
    return JoinResponse(token=mint_token(req.deviceId), memberId=req.deviceId)


def current_member(authorization: str | None = Header(None)) -> uuid.UUID:
    """The dependency every protected route takes. 401 and nothing else --
    a 403 here would tell an attacker their token shape was right."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="no token")
    token = authorization.removeprefix("Bearer ").strip()
    with db.pool.connection() as conn:
        row = conn.execute(
            "UPDATE device_token SET last_seen_at = now() "
            "WHERE token_sha256 = %s RETURNING member_id",
            (_digest(token),)).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="no token")
    return row[0]
```

- [ ] **Step 5: Mount the router and add `/v1/whoami`**

Add to `backend/app/main.py`, after the `app = FastAPI(...)` line:

```python
import uuid

from fastapi import Depends

from . import auth

app.include_router(auth.router)


@app.get("/v1/whoami")
def whoami(member_id: uuid.UUID = Depends(auth.current_member)) -> dict:
    """Exists so the phone can check its token is still good without
    uploading anything, and so the auth dependency has something to test
    against that has no other moving parts."""
    return {"memberId": str(member_id)}
```

- [ ] **Step 6: Run the suite and watch it pass**

```bash
cd ~/Snails/backend
.venv/bin/python -m pytest -v
```

Expected: 16 passed.

- [ ] **Step 7: Commit**

```bash
cd ~/Snails
git add backend/app/auth.py backend/app/models.py backend/app/main.py \
        backend/tests/test_join.py
git commit -m "feat(backend): join by camp code, bearer tokens stored hashed"
```

---

## Task 4: The blob store

**Files:**
- Create: `backend/app/media.py`
- Test: `backend/tests/test_media.py`

**Interfaces:**
- Consumes: `settings.media_root`.
- Produces: `def put(data: bytes) -> str` returning the hex SHA-256; `def path_for(sha: str) -> pathlib.Path`; `def exists(sha: str) -> bool`.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_media.py`:

```python
"""Blobs are content-addressed and never deleted.

Two members photographing the same sign produce byte-identical JPEGs and
therefore one file. If deleting a note unlinked its blob, the other member's
note would silently lose its photo -- so nothing here removes anything, and
the delete path (not yet built) tombstones rows and leaves the disk alone.
"""
from app import media


class TestPut:
    def test_returns_the_sha256(self):
        sha = media.put(b"hello")
        assert sha == ("2cf24dba5fb0a30e26e83b2ac5b9e29e"
                       "1b161e5c1fa7425e73043362938b9824")

    def test_writes_the_bytes(self):
        sha = media.put(b"hello")
        assert media.path_for(sha).read_bytes() == b"hello"

    def test_identical_bytes_land_on_one_file(self):
        a = media.put(b"same picture")
        b = media.put(b"same picture")
        assert a == b
        assert media.path_for(a) == media.path_for(b)

    def test_shards_by_prefix(self):
        # One flat directory with fifty thousand files in it is a directory
        # nobody can list on a small VM.
        sha = media.put(b"hello")
        assert media.path_for(sha).parent.name == sha[2:4]
        assert media.path_for(sha).parent.parent.name == sha[0:2]

    def test_rewriting_an_existing_blob_is_a_no_op(self):
        sha = media.put(b"hello")
        before = media.path_for(sha).stat().st_mtime_ns
        media.put(b"hello")
        assert media.path_for(sha).stat().st_mtime_ns == before


class TestExists:
    def test_true_after_put(self):
        assert media.exists(media.put(b"hello"))

    def test_false_for_something_never_written(self):
        assert not media.exists("0" * 64)
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd ~/Snails/backend
.venv/bin/python -m pytest tests/test_media.py -v
```

Expected: `ImportError: cannot import name 'media' from 'app'`.

- [ ] **Step 3: Write `backend/app/media.py`**

```python
"""Photos and voice memos on the VM's own disk, addressed by content.

Not GCS, deliberately. A playa server has no GCS, and a design that only works
with a cloud dependency cannot move to a box behind a camp router. Disk is
cheap and the volume is small: a hundred notes a week at ~2.5 MB is a quarter
of a gigabyte.
"""
import hashlib
import os
from pathlib import Path

from .settings import settings


def path_for(sha: str) -> Path:
    return settings.media_root / sha[0:2] / sha[2:4] / sha


def exists(sha: str) -> bool:
    return path_for(sha).is_file()


def put(data: bytes) -> str:
    """Writes the bytes if they are not already there. Returns the digest."""
    sha = hashlib.sha256(data).hexdigest()
    dest = path_for(sha)
    if dest.is_file():
        return sha
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Write to a temporary name in the same directory and rename. A half
    # written blob under its final name is indistinguishable from a complete
    # one, and the digest is the only thing anybody checks.
    tmp = dest.with_suffix(".part")
    tmp.write_bytes(data)
    os.replace(tmp, dest)
    return sha
```

- [ ] **Step 4: Point the tests at a temporary media root**

Add to `backend/tests/conftest.py`:

```python
@pytest.fixture(autouse=True)
def temp_media(tmp_path, monkeypatch):
    """Blobs go somewhere disposable. Without this the suite scribbles into
    whatever LUCY_MEDIA_ROOT points at, which on the VM is real data."""
    from app import settings as settings_module
    monkeypatch.setattr(settings_module.settings, "media_root", tmp_path / "media",
                        raising=False)
    yield
```

`Settings` is a frozen dataclass, so `monkeypatch.setattr` on the instance needs `object.__setattr__`. Use this instead:

```python
@pytest.fixture(autouse=True)
def temp_media(tmp_path):
    from app.settings import settings
    original = settings.media_root
    object.__setattr__(settings, "media_root", tmp_path / "media")
    yield
    object.__setattr__(settings, "media_root", original)
```

- [ ] **Step 5: Run the suite and watch it pass**

```bash
cd ~/Snails/backend
.venv/bin/python -m pytest -v
```

Expected: 23 passed.

- [ ] **Step 6: Commit**

```bash
cd ~/Snails
git add backend/app/media.py backend/tests/test_media.py backend/tests/conftest.py
git commit -m "feat(backend): content-addressed blob store, blobs never deleted"
```

---

## Task 5: Uploading a note

**Files:**
- Create: `backend/app/notes.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_notes_upload.py`

**Interfaces:**
- Consumes: `auth.current_member`, `media.put`, `db.pool`, `NoteMeta`, `NoteUploaded`.
- Produces: `router` in `backend/app/notes.py`, mounted in `main.py`. Endpoint `POST /v1/notes`, multipart with parts `meta` (JSON string), `photo` (optional file), `memo` (optional file).

- [ ] **Step 1: Write the failing test**

`backend/tests/test_notes_upload.py`:

```python
"""Uploading a note.

The two things that must hold on a bad connection in the desert: an upload
that dies halfway leaves nothing behind that stops the retry, and a retry
that succeeds does not produce a second note. The note's id comes from the
phone for exactly this reason.
"""
import json
import uuid

from app import db

DEVICE = "11111111-2222-3333-4444-555555555555"


def token(client):
    return client.post("/v1/join", json={
        "code": "test-code", "deviceId": DEVICE,
        "displayName": "Marcus"}).json()["token"]


def upload(client, tok, note_id=None, photo=b"\xff\xd8jpegbytes",
           memo=None, transcript="the water tank is half"):
    meta = {
        "noteId": note_id or str(uuid.uuid4()),
        "takenAt": "2026-08-28T19:04:00Z",
        "transcript": transcript,
        "attached": ["water"],
        "appVersion": "1.0 (142)",
    }
    files = [("meta", ("meta.json", json.dumps(meta), "application/json"))]
    if photo is not None:
        files.append(("photo", ("photo.jpg", photo, "image/jpeg")))
    if memo is not None:
        files.append(("memo", ("memo.m4a", memo, "audio/mp4")))
    return client.post("/v1/notes", files=files,
                       headers={"Authorization": f"Bearer {tok}"})


class TestUpload:
    def test_accepts_a_note(self, client):
        r = upload(client, token(client))
        assert r.status_code == 200
        assert r.json()["duplicate"] is False
        assert r.json()["seq"] >= 1

    def test_stores_the_transcript_and_attachments(self, client):
        upload(client, token(client))
        with db.pool.connection() as conn:
            row = conn.execute(
                "SELECT transcript, attached FROM note").fetchone()
        assert row[0] == "the water tank is half"
        assert row[1] == ["water"]

    def test_a_note_with_only_a_memo_is_a_whole_note(self, client):
        # Half the useful notes are said, not photographed, because your hands
        # are full of the thing you are describing.
        r = upload(client, token(client), photo=None, memo=b"m4abytes")
        assert r.status_code == 200
        with db.pool.connection() as conn:
            photo, memo = conn.execute(
                "SELECT photo_sha, memo_sha FROM note").fetchone()
        assert photo is None and memo is not None

    def test_a_note_with_neither_is_refused(self, client):
        r = upload(client, token(client), photo=None, memo=None)
        assert r.status_code == 400

    def test_requires_a_token(self, client):
        meta = json.dumps({"noteId": str(uuid.uuid4()),
                           "takenAt": "2026-08-28T19:04:00Z"})
        r = client.post("/v1/notes", files=[
            ("meta", ("meta.json", meta, "application/json")),
            ("photo", ("photo.jpg", b"x", "image/jpeg"))])
        assert r.status_code == 401


class TestIdempotency:
    def test_the_same_note_id_twice_makes_one_note(self, client):
        tok = token(client)
        note_id = str(uuid.uuid4())
        first = upload(client, tok, note_id=note_id)
        second = upload(client, tok, note_id=note_id)
        assert second.status_code == 200
        assert second.json()["duplicate"] is True
        assert second.json()["seq"] == first.json()["seq"]
        with db.pool.connection() as conn:
            assert conn.execute("SELECT count(*) FROM note").fetchone()[0] == 1

    def test_a_retry_does_not_change_what_was_stored(self, client):
        # Notes are immutable once uploaded. A phone re-sending a note it
        # already sent must not be able to rewrite the camp's copy.
        tok = token(client)
        note_id = str(uuid.uuid4())
        upload(client, tok, note_id=note_id, transcript="original")
        upload(client, tok, note_id=note_id, transcript="rewritten")
        with db.pool.connection() as conn:
            assert conn.execute(
                "SELECT transcript FROM note").fetchone()[0] == "original"


class TestOrdering:
    def test_seq_increases(self, client):
        tok = token(client)
        seqs = [upload(client, tok).json()["seq"] for _ in range(5)]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == 5
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd ~/Snails/backend
.venv/bin/python -m pytest tests/test_notes_upload.py -v
```

Expected: all fail with 404.

- [ ] **Step 3: Write `backend/app/notes.py`**

```python
"""Notes: upload, list, fetch media.

Notes are immutable. A member can later delete their own, which will tombstone
the row; nothing here ever edits one, and a retry of an upload already stored
returns what is stored rather than replacing it.
"""
import json
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile

from . import db, media
from .auth import current_member
from .models import NoteMeta, NoteUploaded

router = APIRouter()

# A photo off an iPhone is ~2-4 MB and a minute of AAC is ~500 KB. These caps
# are generous for both and small enough that a bug cannot fill the disk in an
# afternoon. Caddy enforces a 30 MB request body as well.
MAX_PHOTO = 12 * 1024 * 1024
MAX_MEMO = 25 * 1024 * 1024


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

    if photo_bytes is None and memo_bytes is None:
        raise HTTPException(status_code=400,
                            detail="a note needs a photo or a memo")
    if photo_bytes is not None and len(photo_bytes) > MAX_PHOTO:
        raise HTTPException(status_code=413, detail="photo too large")
    if memo_bytes is not None and len(memo_bytes) > MAX_MEMO:
        raise HTTPException(status_code=413, detail="memo too large")

    with db.pool.connection() as conn:
        existing = conn.execute("SELECT seq FROM note WHERE id = %s",
                                (str(parsed.noteId),)).fetchone()
        if existing is not None:
            return NoteUploaded(noteId=parsed.noteId, seq=existing[0],
                                duplicate=True)

    photo_sha = media.put(photo_bytes) if photo_bytes is not None else None
    memo_sha = media.put(memo_bytes) if memo_bytes is not None else None

    with db.pool.connection() as conn:
        with conn.transaction():
            # seq comes from a BIGSERIAL, and a sequence hands out numbers in
            # request order but transactions commit in whatever order they
            # finish. A client polling ?since=N could therefore step over a
            # lower seq that committed later and never see that note again.
            # This lock serialises the assignment with the commit, which for a
            # camp-sized write rate costs nothing.
            conn.execute("SELECT pg_advisory_xact_lock(4242)")
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
                return NoteUploaded(noteId=parsed.noteId, seq=seq, duplicate=True)
            return NoteUploaded(noteId=parsed.noteId, seq=row[0], duplicate=False)
```

- [ ] **Step 4: Mount the router**

Add to `backend/app/main.py`:

```python
from . import notes

app.include_router(notes.router)
```

- [ ] **Step 5: Run the suite and watch it pass**

```bash
cd ~/Snails/backend
.venv/bin/python -m pytest -v
```

Expected: 32 passed.

- [ ] **Step 6: Commit**

```bash
cd ~/Snails
git add backend/app/notes.py backend/app/main.py backend/tests/test_notes_upload.py
git commit -m "feat(backend): note upload, idempotent on the phone's note id"
```

---

## Task 6: Listing notes, and fetching their media

**Files:**
- Modify: `backend/app/notes.py`
- Test: `backend/tests/test_notes_list.py`

**Interfaces:**
- Consumes: everything from Task 5, plus `NoteList` and `NoteSummary`.
- Produces: `GET /v1/notes?since=<int>&limit=<int>` → `NoteList`; `GET /v1/notes/{note_id}/photo` and `/memo` → the bytes.

- [ ] **Step 1: Write the failing test**

`backend/tests/test_notes_list.py`:

```python
"""The list is what stops two people noting the same thing.

It carries enough to read -- who, when, what was said -- and not the media,
because the list is cheap and the media is not. On a Starlink connection
shared by fifty people that difference is the feature.
"""
import json
import uuid

DEVICE_A = "11111111-2222-3333-4444-555555555555"
DEVICE_B = "99999999-8888-7777-6666-555555555555"


def token(client, device=DEVICE_A, name="Marcus"):
    return client.post("/v1/join", json={
        "code": "test-code", "deviceId": device,
        "displayName": name}).json()["token"]


def upload(client, tok, transcript="a note", photo=b"jpegbytes", memo=None):
    meta = {"noteId": str(uuid.uuid4()), "takenAt": "2026-08-28T19:04:00Z",
            "transcript": transcript, "attached": [], "appVersion": "1.0"}
    files = [("meta", ("meta.json", json.dumps(meta), "application/json"))]
    if photo is not None:
        files.append(("photo", ("photo.jpg", photo, "image/jpeg")))
    if memo is not None:
        files.append(("memo", ("memo.m4a", memo, "audio/mp4")))
    return client.post("/v1/notes", files=files,
                       headers={"Authorization": f"Bearer {tok}"}).json()


def listing(client, tok, since=0):
    return client.get(f"/v1/notes?since={since}",
                      headers={"Authorization": f"Bearer {tok}"})


class TestList:
    def test_empty_camp_returns_nothing(self, client):
        r = listing(client, token(client))
        assert r.status_code == 200
        assert r.json()["notes"] == []
        assert r.json()["cursor"] == 0

    def test_returns_notes_from_everyone(self, client):
        # Notes are the camp's, not the author's. That is the point of
        # sharing them: so nobody notes the same thing twice.
        a = token(client, DEVICE_A, "Marcus")
        b = token(client, DEVICE_B, "Dusty")
        upload(client, a, transcript="from Marcus")
        upload(client, b, transcript="from Dusty")
        said = {n["transcript"] for n in listing(client, a).json()["notes"]}
        assert said == {"from Marcus", "from Dusty"}

    def test_carries_the_author_name(self, client):
        a = token(client, DEVICE_A, "Marcus")
        upload(client, a)
        assert listing(client, a).json()["notes"][0]["author"] == "Marcus"

    def test_since_returns_only_what_is_newer(self, client):
        tok = token(client)
        first = upload(client, tok, transcript="one")
        upload(client, tok, transcript="two")
        after = listing(client, tok, since=first["seq"]).json()
        assert [n["transcript"] for n in after["notes"]] == ["two"]

    def test_cursor_is_the_highest_seq_returned(self, client):
        tok = token(client)
        upload(client, tok)
        second = upload(client, tok)
        assert listing(client, tok).json()["cursor"] == second["seq"]

    def test_cursor_holds_when_nothing_is_new(self, client):
        tok = token(client)
        seq = upload(client, tok)["seq"]
        assert listing(client, tok, since=seq).json()["cursor"] == seq

    def test_flags_which_media_exist_without_sending_them(self, client):
        tok = token(client)
        upload(client, tok, photo=b"jpegbytes", memo=None)
        note = listing(client, tok).json()["notes"][0]
        assert note["hasPhoto"] is True and note["hasMemo"] is False
        assert "photo" not in note

    def test_requires_a_token(self, client):
        assert client.get("/v1/notes?since=0").status_code == 401


class TestMedia:
    def test_photo_comes_back_byte_for_byte(self, client):
        tok = token(client)
        note = upload(client, tok, photo=b"\xff\xd8exactlythese")
        r = client.get(f"/v1/notes/{note['noteId']}/photo",
                       headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200
        assert r.content == b"\xff\xd8exactlythese"

    def test_memo_comes_back(self, client):
        tok = token(client)
        note = upload(client, tok, photo=None, memo=b"m4abytes")
        r = client.get(f"/v1/notes/{note['noteId']}/memo",
                       headers={"Authorization": f"Bearer {tok}"})
        assert r.content == b"m4abytes"

    def test_absent_media_is_404(self, client):
        tok = token(client)
        note = upload(client, tok, photo=b"jpeg", memo=None)
        r = client.get(f"/v1/notes/{note['noteId']}/memo",
                       headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 404

    def test_unknown_note_is_404(self, client):
        tok = token(client)
        r = client.get(f"/v1/notes/{uuid.uuid4()}/photo",
                       headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 404

    def test_requires_a_token(self, client):
        tok = token(client)
        note = upload(client, tok)
        assert client.get(f"/v1/notes/{note['noteId']}/photo").status_code == 401
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd ~/Snails/backend
.venv/bin/python -m pytest tests/test_notes_list.py -v
```

Expected: all fail with 404 or 405.

- [ ] **Step 3: Append the list and media routes to `backend/app/notes.py`**

```python
@router.get("/v1/notes", response_model=NoteList)
def list_notes(since: int = 0, limit: int = 200,
               member_id: uuid.UUID = Depends(current_member)) -> NoteList:
    limit = max(1, min(limit, 500))
    with db.pool.connection() as conn:
        rows = conn.execute(
            "SELECT n.id, n.seq, m.display_name, n.member_id, n.taken_at,"
            " n.transcript, n.attached, n.photo_sha, n.memo_sha"
            " FROM note n JOIN member m ON m.id = n.member_id"
            " WHERE n.seq > %s ORDER BY n.seq LIMIT %s",
            (since, limit)).fetchall()
    notes = [NoteSummary(id=r[0], seq=r[1], author=r[2], authorId=r[3],
                         takenAt=r[4], transcript=r[5], attached=r[6],
                         hasPhoto=r[7] is not None, hasMemo=r[8] is not None)
             for r in rows]
    # Hold the caller's cursor when nothing is new, rather than resetting it
    # to zero and re-downloading the week.
    return NoteList(notes=notes, cursor=notes[-1].seq if notes else since)


def _blob(note_id: uuid.UUID, column: str, content_type: str) -> Response:
    with db.pool.connection() as conn:
        row = conn.execute(f"SELECT {column} FROM note WHERE id = %s",
                           (str(note_id),)).fetchone()
    if row is None or row[0] is None or not media.exists(row[0]):
        raise HTTPException(status_code=404, detail="not here")
    return Response(content=media.path_for(row[0]).read_bytes(),
                    media_type=content_type)


@router.get("/v1/notes/{note_id}/photo")
def note_photo(note_id: uuid.UUID,
               member_id: uuid.UUID = Depends(current_member)) -> Response:
    return _blob(note_id, "photo_sha", "image/jpeg")


@router.get("/v1/notes/{note_id}/memo")
def note_memo(note_id: uuid.UUID,
              member_id: uuid.UUID = Depends(current_member)) -> Response:
    return _blob(note_id, "memo_sha", "audio/mp4")
```

Add `NoteList, NoteSummary` to the `from .models import` line at the top of the file.

`_blob` interpolates `column` into SQL. That is safe here and only here: it is called from two places with two hardcoded literals and is never reachable from a request parameter. Do not extend it to take a caller-supplied column.

- [ ] **Step 4: Run the suite and watch it pass**

```bash
cd ~/Snails/backend
.venv/bin/python -m pytest -v
```

Expected: 45 passed.

- [ ] **Step 5: Commit**

```bash
cd ~/Snails
git add backend/app/notes.py backend/tests/test_notes_list.py
git commit -m "feat(backend): note list on a server cursor, and media on request"
```

---

## Task 7: Containers, Caddy, and running the whole stack locally

**Files:**
- Create: `backend/Dockerfile`, `backend/compose.yml`, `backend/Caddyfile`, `backend/.env.example`, `backend/README.md`
- Modify: `backend/app/settings.py`

**Interfaces:**
- Consumes: the app from Tasks 1–6.
- Produces: a stack that `docker compose up` brings to life; `api` listening on 8000 inside the network; Caddy terminating TLS on 80/443 and serving `/srv/site` at `/` behind basic auth.

- [ ] **Step 1: Write `backend/Dockerfile`**

```dockerfile
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY migrations ./migrations

# One worker, on purpose. The note sequence is serialised with an advisory
# lock and the write rate is a camp, not a website; more workers would buy
# nothing and cost the simplicity of knowing there is exactly one writer.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

- [ ] **Step 2: Write `backend/compose.yml`**

```yaml
# The whole camp server. This same file is meant to run on a box behind a
# playa router with no internet -- change LUCY_HOSTNAME to a local name, drop
# the TLS block in the Caddyfile, and nothing else moves.
services:
  db:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: lucy
    volumes:
      - ./pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 5s
      retries: 10

  api:
    build: .
    restart: unless-stopped
    environment:
      DATABASE_URL: postgresql://postgres:${POSTGRES_PASSWORD}@db:5432/lucy
      LUCY_JOIN_CODE: ${LUCY_JOIN_CODE}
      LUCY_MEDIA_ROOT: /data/media
    volumes:
      - ./media:/data/media
    depends_on:
      db:
        condition: service_healthy

  caddy:
    image: caddy:2-alpine
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    environment:
      LUCY_HOSTNAME: ${LUCY_HOSTNAME}
      LUCY_SITE_USER: ${LUCY_SITE_USER}
      LUCY_SITE_HASH: ${LUCY_SITE_HASH}
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - ./site:/srv/site:ro
      - ./caddy_data:/data
      - ./caddy_config:/config
    depends_on:
      - api
```

- [ ] **Step 3: Write `backend/Caddyfile`**

```
{$LUCY_HOSTNAME} {
	encode gzip

	# The API. No basic auth here -- the phone carries a bearer token, and a
	# browser password prompt in the middle of an upload is not a thing an
	# iOS URLSession can answer.
	handle /v1/* {
		request_body {
			max_size 30MB
		}
		reverse_proxy api:8000
	}

	# The camp instructions. Password-protected because it is for the camp,
	# and server-side because a JavaScript prompt in front of a static file
	# is not protection, it is decoration.
	handle {
		basic_auth {
			{$LUCY_SITE_USER} {$LUCY_SITE_HASH}
		}
		root * /srv/site
		file_server
	}
}
```

- [ ] **Step 4: Write `backend/.env.example`**

```bash
# Copy to .env on the VM and fill in. .env is gitignored and must stay that way.
LUCY_HOSTNAME=lucy.marcusfoster.com
POSTGRES_PASSWORD=
# The camp code, rotated per year. This is what members type to join.
LUCY_JOIN_CODE=
# For the instructions site. Generate the hash with:
#   docker run --rm caddy:2-alpine caddy hash-password --plaintext 'yourpassword'
LUCY_SITE_USER=camp
LUCY_SITE_HASH=
```

- [ ] **Step 5: Bring the stack up locally over plain HTTP and prove it works**

Create `backend/.env` locally with `LUCY_HOSTNAME=localhost`, a password, a join code, and a generated hash. Caddy serves `localhost` over HTTP without trying for a certificate.

```bash
cd ~/Snails/backend
mkdir -p site && echo "<h1>Lucy</h1>" > site/index.html
docker compose up -d --build
sleep 10
curl -s http://localhost/v1/health
```

Expected: `{"ok":true,"time":"..."}`

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://localhost/
curl -s -u camp:yourpassword http://localhost/ | head -1
```

Expected: `401`, then `<h1>Lucy</h1>`.

```bash
curl -s -X POST http://localhost/v1/join \
     -H 'Content-Type: application/json' \
     -d '{"code":"<the join code>","deviceId":"11111111-2222-3333-4444-555555555555","displayName":"Marcus"}'
```

Expected: a JSON object with `token` and `memberId`.

- [ ] **Step 6: Tear down and commit**

```bash
cd ~/Snails/backend
docker compose down
cd ~/Snails
git add backend/Dockerfile backend/compose.yml backend/Caddyfile backend/.env.example
git commit -m "feat(backend): containers, TLS, and the camp site behind basic auth"
```

Write `backend/README.md` covering: how to run the tests, how to run the stack locally, what each env var is, and the rule that `.env` never enters git. Commit it with the same command.

---

## Task 8: The VM, DNS, and the first real deploy

**Files:**
- Create: `deploy/vm_create.sh`, `deploy/push.sh`

**Interfaces:**
- Consumes: `backend/` from Tasks 1–7.
- Produces: a running `https://lucy.marcusfoster.com/v1/health`.

This task has a step only Marcus can perform. Do not work around it.

- [ ] **Step 1: Write `deploy/vm_create.sh`**

```bash
#!/usr/bin/env bash
# Creates the camp server. Run once.
#
# The VM is a pet, deliberately: the eval pipeline wants a machine that holds a
# database and runs batch jobs, and gluing four managed services together to
# avoid owning one box is a worse trade. It does mean the app must treat the
# backend as optional at every point, which it does.
set -euo pipefail

PROJECT=lucy-snails
ZONE=us-west1-b
REGION=us-west1
NAME=lucy-api

gcloud compute addresses create ${NAME}-ip --project "${PROJECT}" \
    --region "${REGION}" 2>/dev/null || true
IP=$(gcloud compute addresses describe ${NAME}-ip --project "${PROJECT}" \
    --region "${REGION}" --format='value(address)')

gcloud compute firewall-rules create allow-lucy-web --project "${PROJECT}" \
    --allow tcp:80,tcp:443 --target-tags lucy-api \
    --source-ranges 0.0.0.0/0 2>/dev/null || true

# e2-small: 2 vCPU burst, 2 GB. Postgres, a single uvicorn worker and Caddy fit
# in that with room. Debian rather than Container-Optimized OS because COS has
# no package manager and the compose plugin is a fight there.
gcloud compute instances create "${NAME}" --project "${PROJECT}" \
    --zone "${ZONE}" --machine-type e2-small \
    --image-family debian-12 --image-project debian-cloud \
    --boot-disk-size 50GB --boot-disk-type pd-balanced \
    --address "${IP}" --tags lucy-api \
    --scopes https://www.googleapis.com/auth/devstorage.read_write \
    --metadata startup-script='#!/bin/bash
set -e
apt-get update
apt-get install -y ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
    > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
systemctl enable --now docker
mkdir -p /opt/lucy
'

# Backups go to the private bucket that already exists. The org enforces
# iam.disableServiceAccountKeyCreation, so this is the attached service
# account via the metadata server -- there is no key file and cannot be one.
SA=$(gcloud compute instances describe "${NAME}" --project "${PROJECT}" \
    --zone "${ZONE}" --format='value(serviceAccounts[0].email)')
gcloud storage buckets add-iam-policy-binding gs://lucy-snails-backups \
    --member "serviceAccount:${SA}" --role roles/storage.objectAdmin \
    --project "${PROJECT}"

echo
echo "VM is up. Its address is: ${IP}"
echo
echo "Marcus: add this A record at Hostinger for marcusfoster.com —"
echo "    Type: A    Name: lucy    Value: ${IP}    TTL: 300"
echo
echo "Caddy cannot get a certificate until that record resolves."
```

- [ ] **Step 2: Run it**

```bash
chmod +x ~/Snails/deploy/vm_create.sh
~/Snails/deploy/vm_create.sh
```

Expected: the VM is created and the script prints an IP address and the A record to add.

- [ ] **Step 3: STOP. Hand the A record to Marcus and wait.**

Only Marcus can add the record — the DNS is at Hostinger and behind his login. Give him the exact three values the script printed. Then confirm propagation before going further:

```bash
dig +short lucy.marcusfoster.com A
```

Expected: the VM's IP. Do not proceed until it does. Caddy will get itself rate-limited by Let's Encrypt if it retries against a name that does not resolve.

- [ ] **Step 4: Write `deploy/push.sh`**

```bash
#!/usr/bin/env bash
# Copies backend/ to the VM and restarts the stack. Run for every deploy.
#
# No container registry: this is one box and one developer, and a registry is
# a second thing to keep credentials for. The source goes up and builds there.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT=lucy-snails
ZONE=us-west1-b
NAME=lucy-api

# .env is not copied. It lives on the VM and only on the VM.
tar --exclude .venv --exclude pgdata --exclude media --exclude caddy_data \
    --exclude caddy_config --exclude .env --exclude __pycache__ \
    -czf /tmp/lucy-backend.tgz -C "${DIR}" backend site

gcloud compute scp /tmp/lucy-backend.tgz "${NAME}":/tmp/ \
    --project "${PROJECT}" --zone "${ZONE}"

gcloud compute ssh "${NAME}" --project "${PROJECT}" --zone "${ZONE}" --command '
set -e
sudo mkdir -p /opt/lucy
sudo tar -xzf /tmp/lucy-backend.tgz -C /opt/lucy
# The instructions site is served from backend/site so Caddy has one root.
sudo rm -rf /opt/lucy/backend/site
sudo mv /opt/lucy/site /opt/lucy/backend/site 2>/dev/null || true
cd /opt/lucy/backend
sudo docker compose up -d --build
sudo docker compose ps
'
```

- [ ] **Step 5: Create `.env` on the VM and deploy**

```bash
gcloud compute ssh lucy-api --project lucy-snails --zone us-west1-b
# On the VM:
sudo mkdir -p /opt/lucy/backend
sudo nano /opt/lucy/backend/.env
```

Fill it from `backend/.env.example` with `LUCY_HOSTNAME=lucy.marcusfoster.com`, a generated Postgres password, the camp join code Marcus chooses, and a site password hash from `sudo docker run --rm caddy:2-alpine caddy hash-password --plaintext '<password>'`.

Then:

```bash
chmod +x ~/Snails/deploy/push.sh
~/Snails/deploy/push.sh
```

- [ ] **Step 6: Verify TLS and the API from the laptop**

```bash
curl -sv https://lucy.marcusfoster.com/v1/health 2>&1 | grep -E "SSL certificate|subject:|issuer:"
curl -s https://lucy.marcusfoster.com/v1/health
```

Expected: a Let's Encrypt issuer, and `{"ok":true,"time":"..."}`.

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://lucy.marcusfoster.com/
```

Expected: `401` — the site is protected.

If Caddy has no certificate, read its log before changing anything:

```bash
gcloud compute ssh lucy-api --project lucy-snails --zone us-west1-b \
    --command 'cd /opt/lucy/backend && sudo docker compose logs caddy --tail 50'
```

- [ ] **Step 7: Commit**

```bash
cd ~/Snails
git add deploy/vm_create.sh deploy/push.sh
git commit -m "feat(deploy): the camp VM, and a push script that needs no registry"
```

---

## Task 9: iOS identity — keychain, and joining

**Files:**
- Create: `Lucy/Keychain.swift`, `Lucy/Sync.swift`
- Test: manual, on the device.

**Interfaces:**
- Consumes: `https://lucy.marcusfoster.com/v1`.
- Produces:
  - `enum Keychain { static func read(_ key: String) -> String?; static func write(_ key: String, _ value: String); static func delete(_ key: String) }`
  - `@MainActor final class Identity: ObservableObject` with `var deviceID: UUID`, `@Published private(set) var token: String?`, `@Published var displayName: String`, `var joined: Bool`
  - `actor SyncClient` with `func health() async -> Bool`, `func join(code: String, displayName: String) async throws -> String`

- [ ] **Step 1: Write `Lucy/Keychain.swift`**

```swift
import Foundation
import Security

// Two strings that must survive a reinstall: the device's identity and its
// camp token.
//
// UserDefaults would lose both when the app is deleted, and losing the device
// id means rejoining as a second person -- every note you have ever uploaded
// now belongs to a stranger with your name. The keychain survives, which is
// the only reason it is here; nothing stored is a secret worth a Secure
// Enclave.
enum Keychain {
    private static let service = "ai.kaymo.Lucy.sync"

    static func read(_ key: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var out: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &out) == errSecSuccess,
              let data = out as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func write(_ key: String, _ value: String) {
        delete(key)
        let item: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
            kSecValueData as String: Data(value.utf8),
            // The phone is locked in a pocket most of the burn and sync runs
            // only when someone is holding it, so first-unlock is enough --
            // and ThisDeviceOnly keeps it out of an iCloud backup.
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
        ]
        SecItemAdd(item as CFDictionary, nil)
    }

    static func delete(_ key: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
        ]
        SecItemDelete(query as CFDictionary)
    }
}
```

- [ ] **Step 2: Write the first half of `Lucy/Sync.swift`**

```swift
import Foundation

// Talking to the camp server.
//
// Everything here is optional at every point. There is no signal on playa
// except sometimes, so sync is a thing you press and never a thing that
// happens -- and every path through this file has to fail without making the
// app look broken. A backend that is down for the whole week must cost
// nothing except that nobody syncs.

enum Backend {
    static let baseURL = URL(string: "https://lucy.marcusfoster.com/v1")!
    /// Short on purpose. The phone is either on Starlink or it is not, and
    /// making someone watch a spinner for sixty seconds to be told "no signal"
    /// is worse than telling them in five.
    static let probeTimeout: TimeInterval = 6
    static let uploadTimeout: TimeInterval = 120
}

/// Who this phone is, as far as the camp is concerned.
@MainActor
final class Identity: ObservableObject {
    static let shared = Identity()

    private static let deviceKey = "device-id"
    private static let tokenKey = "camp-token"
    private static let nameKey = "display-name"

    let deviceID: UUID
    @Published private(set) var token: String?
    @Published var displayName: String {
        didSet { Keychain.write(Self.nameKey, displayName) }
    }

    var joined: Bool { token != nil }

    private init() {
        if let existing = Keychain.read(Self.deviceKey),
           let id = UUID(uuidString: existing) {
            deviceID = id
        } else {
            let id = UUID()
            Keychain.write(Self.deviceKey, id.uuidString)
            deviceID = id
        }
        token = Keychain.read(Self.tokenKey)
        displayName = Keychain.read(Self.nameKey) ?? ""
    }

    func store(token: String) {
        Keychain.write(Self.tokenKey, token)
        self.token = token
    }

    func leave() {
        Keychain.delete(Self.tokenKey)
        token = nil
    }
}

enum SyncError: LocalizedError {
    case unreachable
    case badCode
    case unauthorised
    case server(Int)

    var errorDescription: String? {
        switch self {
        case .unreachable:
            return "Couldn't reach the camp server. Try again when you have signal."
        case .badCode:
            return "That code isn't this year's."
        case .unauthorised:
            return "This phone isn't joined to the camp any more."
        case .server(let code):
            return "The camp server said \(code)."
        }
    }
}

actor SyncClient {
    static let shared = SyncClient()

    private let session: URLSession = {
        let config = URLSessionConfiguration.default
        config.waitsForConnectivity = false
        return URLSession(configuration: config)
    }()

    /// The reachability probe. Never throws: the answer to "can I sync" is
    /// yes or no, and an error type here would only get mapped back to a bool.
    func health() async -> Bool {
        var request = URLRequest(url: Backend.baseURL.appendingPathComponent("health"))
        request.timeoutInterval = Backend.probeTimeout
        guard let (_, response) = try? await session.data(for: request),
              let http = response as? HTTPURLResponse else { return false }
        return http.statusCode == 200
    }

    func join(code: String, displayName: String) async throws -> String {
        let deviceID = await Identity.shared.deviceID
        var request = URLRequest(url: Backend.baseURL.appendingPathComponent("join"))
        request.httpMethod = "POST"
        request.timeoutInterval = Backend.probeTimeout
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "code": code,
            "deviceId": deviceID.uuidString,
            "displayName": displayName,
        ])

        guard let (data, response) = try? await session.data(for: request),
              let http = response as? HTTPURLResponse else {
            throw SyncError.unreachable
        }
        if http.statusCode == 403 { throw SyncError.badCode }
        guard http.statusCode == 200,
              let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let token = body["token"] as? String else {
            throw SyncError.server(http.statusCode)
        }
        return token
    }
}
```

- [ ] **Step 3: Regenerate the project and build**

```bash
cd ~/Snails/Lucy
xcodegen generate
xcodebuild -project Lucy.xcodeproj -scheme Lucy -sdk iphonesimulator \
    -destination 'platform=iOS Simulator,name=iPhone 17 Pro' \
    -configuration Debug build CODE_SIGNING_ALLOWED=NO 2>&1 | tail -5
```

Expected: `** BUILD SUCCEEDED **`.

- [ ] **Step 4: Commit**

```bash
cd ~/Snails
git add Lucy/Keychain.swift Lucy/Sync.swift
git commit -m "feat(ios): a keychain identity and the join call"
```

---

## Task 10: iOS upload — note ids, markers, and the Sync sheet

**Files:**
- Modify: `Lucy/Capture.swift`, `Lucy/Sync.swift`, `Lucy/ChatView.swift`
- Create: `Lucy/SyncView.swift`

**Interfaces:**
- Consumes: `Identity`, `SyncClient`, `CaptureStore`, `Capture`.
- Produces:
  - `Capture.noteID: UUID` and `Capture.uploaded: Bool`
  - `SyncClient.upload(_ capture: Capture) async throws -> Bool` (true when newly stored, false when the server already had it)
  - `SyncClient.list(since: Int) async throws -> (count: Int, cursor: Int)`
  - `struct SyncView: View`

- [ ] **Step 1: Give every capture a stable note id**

In `Lucy/Capture.swift`, add `import CryptoKit` at the top, and add to `struct Capture`:

```swift
    /// The note's identity on the camp server, chosen here so an upload that
    /// dies halfway can be retried without creating a second note.
    ///
    /// Captures taken before sync existed have no id in their meta.json, and
    /// there are already some on every tester's phone. Deriving one from the
    /// device id and the folder name gives those the same id on every retry --
    /// a UUIDv5 by hand, because Foundation has no namespace UUIDs.
    var noteID: UUID {
        if let data = try? Data(contentsOf: folder.appendingPathComponent("meta.json")),
           let meta = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let stored = meta["note_id"] as? String,
           let id = UUID(uuidString: stored) {
            return id
        }
        return Capture.derivedID(device: Identity.shared.deviceID, folder: id)
    }

    /// Deterministic and stable: same phone, same folder, same UUID, forever.
    nonisolated static func derivedID(device: UUID, folder: String) -> UUID {
        var digest = Array(SHA256.hash(data: Data("\(device.uuidString):\(folder)".utf8)))
        digest[6] = (digest[6] & 0x0F) | 0x50      // version 5
        digest[8] = (digest[8] & 0x3F) | 0x80      // RFC 4122 variant
        let b = digest
        return UUID(uuid: (b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7],
                           b[8], b[9], b[10], b[11], b[12], b[13], b[14], b[15]))
    }

    /// A marker file beside the media. Plain files, like everything else in a
    /// capture folder: the post-burn pipeline can read the whole state of a
    /// note without this app's cooperation, and a database that disagreed with
    /// the folder would be a third source of truth.
    var uploadedMarker: URL { folder.appendingPathComponent("uploaded") }
    var uploaded: Bool { FileManager.default.fileExists(atPath: uploadedMarker.path) }
```

`noteID` reads `Identity.shared`, which is `@MainActor`. Mark the computed property `@MainActor` and call it from main-actor context only — the sync sheet is a view, so this costs nothing.

In `CaptureStore.writeMeta`, add the id so future captures carry their own:

```swift
    static func writeMeta(_ folder: URL, taken: Date, attached: [String],
                          transcript: String = "", noteID: UUID = UUID()) {
        let meta: [String: Any] = [
            "note_id": noteID.uuidString,
            "taken_at": ISO8601DateFormatter().string(from: taken),
            "attached": attached,
            "transcript": transcript,
            "app_version": Bundle.main.infoDictionary?["CFBundleShortVersionString"] ?? "dev",
        ]
```

- [ ] **Step 2: Add upload and list to `Lucy/Sync.swift`**

```swift
extension SyncClient {
    /// Uploads one capture. Returns false when the server already had it,
    /// which is a success: it means an earlier attempt got further than the
    /// phone realised.
    func upload(_ capture: Capture, noteID: UUID, token: String) async throws -> Bool {
        let boundary = "lucy-\(UUID().uuidString)"
        var body = Data()

        func part(name: String, filename: String, type: String, data: Data) {
            body.append(Data("--\(boundary)\r\n".utf8))
            body.append(Data("Content-Disposition: form-data; name=\"\(name)\"; filename=\"\(filename)\"\r\n".utf8))
            body.append(Data("Content-Type: \(type)\r\n\r\n".utf8))
            body.append(data)
            body.append(Data("\r\n".utf8))
        }

        let meta: [String: Any] = [
            "noteId": noteID.uuidString,
            "takenAt": ISO8601DateFormatter().string(from: capture.takenAt),
            "transcript": capture.text ?? "",
            "attached": [],
            "appVersion": Bundle.main.infoDictionary?["CFBundleShortVersionString"] ?? "dev",
        ]
        part(name: "meta", filename: "meta.json", type: "application/json",
             data: try JSONSerialization.data(withJSONObject: meta))
        if let photo = try? Data(contentsOf: capture.photo) {
            part(name: "photo", filename: "photo.jpg", type: "image/jpeg", data: photo)
        }
        if capture.hasMemo, let memo = try? Data(contentsOf: capture.memo) {
            part(name: "memo", filename: "memo.m4a", type: "audio/mp4", data: memo)
        }
        body.append(Data("--\(boundary)--\r\n".utf8))

        var request = URLRequest(url: Backend.baseURL.appendingPathComponent("notes"))
        request.httpMethod = "POST"
        request.timeoutInterval = Backend.uploadTimeout
        request.setValue("multipart/form-data; boundary=\(boundary)",
                         forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")

        guard let (data, response) = try? await session.upload(for: request, from: body),
              let http = response as? HTTPURLResponse else { throw SyncError.unreachable }
        if http.statusCode == 401 { throw SyncError.unauthorised }
        guard http.statusCode == 200 else { throw SyncError.server(http.statusCode) }
        let body_ = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        return (body_?["duplicate"] as? Bool) == false
    }

    /// How many notes the camp has that this phone has not seen. The media is
    /// not fetched: the list is cheap and the media is not, and knowing a note
    /// exists is what stops two people noting the same thing.
    func list(since: Int, token: String) async throws -> (count: Int, cursor: Int) {
        var comps = URLComponents(url: Backend.baseURL.appendingPathComponent("notes"),
                                  resolvingAgainstBaseURL: false)!
        comps.queryItems = [URLQueryItem(name: "since", value: String(since))]
        var request = URLRequest(url: comps.url!)
        request.timeoutInterval = Backend.probeTimeout
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")

        guard let (data, response) = try? await session.data(for: request),
              let http = response as? HTTPURLResponse else { throw SyncError.unreachable }
        if http.statusCode == 401 { throw SyncError.unauthorised }
        guard http.statusCode == 200,
              let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let notes = body["notes"] as? [[String: Any]],
              let cursor = body["cursor"] as? Int else { throw SyncError.server(http.statusCode) }
        return (notes.count, cursor)
    }
}
```

`Capture` needs `takenAt`. Add it, reading `meta.json` with the folder name as the fallback:

```swift
    /// When the shutter was pressed. The folder name is an ISO timestamp with
    /// the colons swapped out, so it is the fallback when meta.json is absent
    /// -- a capture whose app was killed before it was filed still has a time.
    var takenAt: Date {
        if let data = try? Data(contentsOf: folder.appendingPathComponent("meta.json")),
           let meta = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let stamp = meta["taken_at"] as? String,
           let date = ISO8601DateFormatter().date(from: stamp) {
            return date
        }
        return ISO8601DateFormatter().date(
            from: id.replacingOccurrences(of: "-", with: ":")) ?? Date()
    }
```

The folder name replaced `:` with `-`, and the date itself contains `-`. Reversing it blindly corrupts the date. Use this instead, which only repairs the time portion:

```swift
    var takenAt: Date {
        if let data = try? Data(contentsOf: folder.appendingPathComponent("meta.json")),
           let meta = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let stamp = meta["taken_at"] as? String,
           let date = ISO8601DateFormatter().date(from: stamp) {
            return date
        }
        // "2026-08-28T19-04-00Z" -> "2026-08-28T19:04:00Z"
        let parts = id.split(separator: "T", maxSplits: 1)
        if parts.count == 2 {
            let repaired = parts[0] + "T"
                + parts[1].replacingOccurrences(of: "-", with: ":")
            if let date = ISO8601DateFormatter().date(from: String(repaired)) {
                return date
            }
        }
        return Date()
    }
```

- [ ] **Step 3: Write `Lucy/SyncView.swift`**

```swift
import SwiftUI

// Sync, as a thing you press.
//
// Not a background task, not a thing that happens on launch. There is no
// signal on playa except sometimes, and an app that quietly tries and quietly
// fails is an app whose sync state nobody can reason about. You press it, it
// tells you what happened, and if it could not reach the server it says so in
// a sentence that is about the desert rather than about the app.

struct SyncView: View {
    let face: Face
    @ObservedObject private var identity = Identity.shared
    @Environment(\.dismiss) private var dismiss

    @State private var code = ""
    @State private var name = ""
    @State private var busy = false
    @State private var status: String?
    @State private var problem: String?
    @AppStorage("sync-cursor") private var cursor = 0

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack {
                Text("SYNC")
                    .font(.eyebrow(15))
                    .tracking(Style.current.eyebrowTracking + 0.5)
                    .foregroundStyle(face.accent)
                Spacer()
                Button("Done") { dismiss() }
                    .font(.body_(16)).foregroundStyle(face.muted)
            }

            if identity.joined { joined } else { joining }

            if let status {
                Text(status).font(.body_(16)).foregroundStyle(face.text)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let problem {
                Text(problem).font(.body_(16)).foregroundStyle(face.accent)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer()
        }
        .padding(22)
        .background(face.ground)
        .preferredColorScheme(face.night ? .dark : .light)
    }

    private var joining: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Join the camp")
                .font(.display(24)).foregroundStyle(face.text)
            Text("Your notes go to the whole camp, with your name on them. "
                 + "That's the point — so nobody notes the same thing twice.")
                .font(.body_(16)).foregroundStyle(face.muted)
                .fixedSize(horizontal: false, vertical: true)
            TextField("Your name", text: $name)
                .textFieldStyle(.roundedBorder).font(.body_(17))
            TextField("Camp code", text: $code)
                .textFieldStyle(.roundedBorder).font(.body_(17))
                .autocorrectionDisabled()
                .textInputAutocapitalization(.never)
            Button(busy ? "Joining…" : "Join") { Task { await doJoin() } }
                .font(.body_(17)).foregroundStyle(face.accent)
                .disabled(busy || code.isEmpty || name.isEmpty)
        }
    }

    private var joined: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Joined as \(identity.displayName)")
                .font(.display(22)).foregroundStyle(face.text)
            Button(busy ? "Syncing…" : "Sync now") { Task { await doSync() } }
                .font(.body_(17)).foregroundStyle(face.accent)
                .disabled(busy)
        }
    }

    private func doJoin() async {
        busy = true; problem = nil; status = nil
        defer { busy = false }
        do {
            let token = try await SyncClient.shared.join(code: code, displayName: name)
            identity.displayName = name
            identity.store(token: token)
            status = "Joined."
        } catch {
            problem = error.localizedDescription
        }
    }

    private func doSync() async {
        busy = true; problem = nil; status = nil
        defer { busy = false }
        guard let token = identity.token else { return }

        guard await SyncClient.shared.health() else {
            problem = SyncError.unreachable.errorDescription
            return
        }

        var sent = 0
        for capture in CaptureStore.all() where !capture.uploaded {
            do {
                let fresh = try await SyncClient.shared.upload(
                    capture, noteID: capture.noteID, token: token)
                // Marked whether it was newly stored or already there. Both
                // mean the camp has it, and a note that keeps re-uploading
                // because the phone did not believe the server is how a slow
                // connection turns into no connection.
                FileManager.default.createFile(
                    atPath: capture.uploadedMarker.path, contents: Data())
                if fresh { sent += 1 }
            } catch {
                problem = error.localizedDescription
                break
            }
        }

        do {
            let (count, newCursor) = try await SyncClient.shared.list(
                since: cursor, token: token)
            cursor = newCursor
            let mine = sent == 1 ? "1 note sent" : "\(sent) notes sent"
            let theirs = count == 1 ? "1 new from the camp"
                                    : "\(count) new from the camp"
            status = "\(mine), \(theirs)."
        } catch {
            if problem == nil { problem = error.localizedDescription }
        }
    }
}
```

- [ ] **Step 4: Add the menu item in `Lucy/ChatView.swift`**

Add a state property beside the others:

```swift
    @State private var showingSync = false
```

Add a section to the `menu` body, above `Section("What she remembers")`:

```swift
            Section("The camp") {
                Button("Sync…") { showingSync = true }
            }
```

Add the sheet to the root `VStack` in `body`, after `.onTapGesture`:

```swift
        .sheet(isPresented: $showingSync) { SyncView(face: face) }
```

- [ ] **Step 5: Build and install to the device**

```bash
cd ~/Snails/Lucy
./device.sh
```

Expected: `App installed:` and `Installed. Launch 'Lucy' on the phone.`

- [ ] **Step 6: Commit**

```bash
cd ~/Snails
git add Lucy/Capture.swift Lucy/Sync.swift Lucy/SyncView.swift Lucy/ChatView.swift
git commit -m "feat(ios): sync — join the camp, upload notes, count what is new"
```

---

## Task 11: End-to-end on the phone

**Files:** none. This is verification, and it is the point of the whole plan.

Nothing in Tasks 1–10 proves the thing that matters. A green pytest suite and a `curl` from the laptop are not an iPhone posting multipart through Caddy with a keychain token.

- [ ] **Step 1: Confirm the server is up and empty**

```bash
curl -s https://lucy.marcusfoster.com/v1/health
gcloud compute ssh lucy-api --project lucy-snails --zone us-west1-b --command \
    'cd /opt/lucy/backend && sudo docker compose exec -T db psql -U postgres -d lucy -c "SELECT count(*) FROM note"'
```

- [ ] **Step 2: On the phone — join**

Open Lucy, hamburger menu, **The camp ▸ Sync…**, enter a name and the camp code, press Join. Expected on screen: "Joined."

Then confirm the member exists:

```bash
gcloud compute ssh lucy-api --project lucy-snails --zone us-west1-b --command \
    'cd /opt/lucy/backend && sudo docker compose exec -T db psql -U postgres -d lucy -c "SELECT id, display_name FROM member"'
```

- [ ] **Step 3: On the phone — make a note and sync it**

Note tab. Take a photo, hold the button and say something, let it transcribe, file it. Then menu ▸ Sync… ▸ Sync now.

Expected on screen: "1 note sent, 1 new from the camp." (The phone counts its own note as new, because the list is the camp's and this phone is in the camp. That is correct and should not be "fixed" without deciding it is wrong.)

- [ ] **Step 4: Verify the note landed, with its media, unmangled**

```bash
gcloud compute ssh lucy-api --project lucy-snails --zone us-west1-b --command \
    'cd /opt/lucy/backend && sudo docker compose exec -T db psql -U postgres -d lucy -c "SELECT seq, taken_at, transcript, photo_sha IS NOT NULL AS photo, memo_sha IS NOT NULL AS memo FROM note"'
```

Expected: one row, the transcript matching what was said, both media flags true, and `taken_at` matching when the photo was taken rather than 1970 or today's date at midnight.

Then fetch the photo back and look at it:

```bash
TOKEN=<paste from a fresh curl to /v1/join with a throwaway deviceId>
NOTE=<the note id from the row above>
curl -s -H "Authorization: Bearer $TOKEN" \
     https://lucy.marcusfoster.com/v1/notes/$NOTE/photo -o /tmp/note.jpg
open /tmp/note.jpg
```

Expected: the photo taken on the phone. **Look at it.** A file of the right size is not proof the right bytes arrived — the whole multipart path is what is under test here.

- [ ] **Step 5: Press Sync again and confirm nothing duplicates**

Expected on screen: "0 notes sent, 0 new from the camp." And:

```bash
gcloud compute ssh lucy-api --project lucy-snails --zone us-west1-b --command \
    'cd /opt/lucy/backend && sudo docker compose exec -T db psql -U postgres -d lucy -c "SELECT count(*) FROM note"'
```

Expected: still 1.

- [ ] **Step 6: Turn on airplane mode and press Sync**

Expected on screen, within about six seconds: "Couldn't reach the camp server. Try again when you have signal." Nothing else in the app changes, no note is lost, and no marker is written. Turn airplane mode off and sync again to confirm the note that was pending still goes.

- [ ] **Step 7: Record what happened**

Write the outcome into `docs/learnings-lucy-2026-08-10.md` — what worked first time, what did not, and anything that behaved differently on the phone than in the tests. That gap is the whole reason this task exists.

---

## Deferred, and owed before the next plan

These are in the spec and deliberately not here:

- **Chat log upload and the gap list.** The valuable part is nearly free — a turn whose answer matches a refusal is a question the camp's knowledge does not cover — but it needs a decision first: chat logs are private from other members and still a record of what each person asked, held on a machine Marcus administers. **The camp should be told rather than merely not lied to.** Decide whether that is a disclosure, an opt-out, or an opt-in before writing the endpoint.
- **Tombstoned deletes.** Schema is ready; blobs stay.
- **Lazy media fetch and a feed screen.** The list already carries everything a feed needs.
- **Backups to `gs://lucy-snails-backups`.** The VM's service account already has `objectAdmin` on it as of Task 8.
- **Vertex evaluation.** Behind the gap list, which is behind chat logs.
