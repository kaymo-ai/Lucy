"""Connection pool and migration runner.

Migrations are plain numbered .sql files applied in filename order and recorded
in schema_migration. No Alembic: there is one developer, the schema is three
tables, and a migration tool is a second thing that has to be understood by
whoever picks this up on playa with no internet to search from.
"""
from pathlib import Path

from psycopg_pool import ConnectionPool

from .settings import settings

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"

# `open=False` so importing this module does not require a database. The tests
# import the app to collect, and a pool that dials out at import time turns a
# missing Postgres into a collection error instead of a test failure.
pool = ConnectionPool(settings.database_url, min_size=1, max_size=8,
                      open=False, kwargs={"autocommit": True})


def connect() -> None:
    """Opens the pool if it is not already open. Safe to call repeatedly."""
    if pool.closed:
        pool.open()
    pool.wait(timeout=30)


def migrate() -> list[str]:
    """Applies every migration not yet recorded. Returns the ones applied.

    Runs at startup, in the container, with no operator present -- so running
    it twice has to be harmless. The api container restarts on deploy and on
    OOM, and a migration that failed the second time would take the service
    down at the worst possible moment.
    """
    connect()
    applied: list[str] = []
    with pool.connection() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migration ("
            " name TEXT PRIMARY KEY,"
            " applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        done = {r[0] for r in
                conn.execute("SELECT name FROM schema_migration").fetchall()}
        # Dotfiles are skipped. macOS tar writes an AppleDouble sidecar named
        # `._001_init.sql` beside every file it archives, it matches *.sql, it
        # is binary, and feeding it to read_text() kills the container at
        # startup with a UnicodeDecodeError about byte 0xa3 -- which says
        # nothing at all about the actual cause.
        for path in sorted(p for p in MIGRATIONS.glob("*.sql")
                           if not p.name.startswith(".")):
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
        conn.execute(
            "TRUNCATE answer, chatlog, note, device_token, member"
            " RESTART IDENTITY CASCADE")
