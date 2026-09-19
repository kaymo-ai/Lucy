import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db  # noqa: E402
from app.main import app  # noqa: E402
from app.settings import settings  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def database():
    """Requires the throwaway Postgres from compose.test.yml:

        docker compose -f compose.test.yml up -d

    A real database rather than a fake one, because every interesting thing in
    this service is a Postgres behaviour: ON CONFLICT, advisory locks, arrays,
    and a BIGSERIAL whose ordering is the sync cursor. A stub would test the
    stub.
    """
    db.migrate()
    yield
    # Without this the pool's worker threads outlive the interpreter and every
    # run ends in "couldn't stop thread ... within 5.0 seconds", which is noise
    # that trains you to ignore the end of the test output.
    db.pool.close()


@pytest.fixture(autouse=True)
def clean_database(database):
    """Every test starts with empty tables. Tests that share rows pass in the
    order they were written and fail in every other order."""
    db.reset_for_tests()
    yield


@pytest.fixture(autouse=True)
def temp_media(tmp_path):
    """Blobs go somewhere disposable. Without this the suite scribbles into
    whatever LUCY_MEDIA_ROOT points at, which on the VM is real data.

    Settings is a frozen dataclass, hence object.__setattr__.
    """
    original = settings.media_root
    object.__setattr__(settings, "media_root", tmp_path / "media")
    yield
    object.__setattr__(settings, "media_root", original)


@pytest.fixture
def client():
    # https, because the site cookie is set Secure and a client on http never
    # sends it back -- which looks exactly like a broken signature check.
    # Caddy terminates TLS in front of this in production, so https is also
    # the only scheme that ever reaches it.
    with TestClient(app, base_url="https://testserver") as c:
        yield c


@pytest.fixture
def site_dir(tmp_path):
    """A miniature copy of the camp site, so the serving tests do not depend
    on the real one being present or on what it happens to say today."""
    root = tmp_path / "site"
    (root / "screens").mkdir(parents=True)
    (root / "gate.html").write_text(
        "<html><body><h1>Camp only</h1></body></html>")
    (root / "index.html").write_text(
        "<html><body><h2>GETTING HER</h2></body></html>")
    (root / "screens" / "people.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    # The public case study lives beside the gated site, outside the gate.
    (root / "tech").mkdir()
    (root / "tech.html").write_text(
        "<html><body><h2>CASE STUDY</h2></body></html>")
    (root / "tech" / "chat.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    original = settings.site_root
    object.__setattr__(settings, "site_root", root)
    yield root
    object.__setattr__(settings, "site_root", original)
