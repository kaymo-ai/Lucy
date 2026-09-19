"""Health is the reachability probe the phone hits before it does anything
else. It must answer without a token and without touching the database: a
probe that failed because Postgres was slow would tell someone "no signal"
while they are standing under Starlink.
"""
from app import db


class TestHealth:
    def test_returns_ok(self, client):
        r = client.get("/v1/health")
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_carries_server_time(self, client):
        # Phone clocks in the desert are not to be trusted. The server's own
        # time being visible is how a confused client can notice.
        assert "time" in client.get("/v1/health").json()

    def test_needs_no_token(self, client):
        # No Authorization header is sent here at all.
        assert client.get("/v1/health").status_code == 200


class TestMigrate:
    def test_creates_the_tables(self):
        db.migrate()
        with db.pool.connection() as conn:
            rows = conn.execute(
                "SELECT table_name FROM information_schema.tables"
                " WHERE table_schema = 'public'").fetchall()
        assert {"member", "device_token", "note"} <= {r[0] for r in rows}

    def test_running_twice_applies_nothing_the_second_time(self):
        # The api container restarts on deploy and on OOM. A migration that
        # failed the second time would take the service down at the worst
        # possible moment.
        db.migrate()
        assert db.migrate() == []

    def test_records_what_it_applied(self):
        db.migrate()
        with db.pool.connection() as conn:
            rows = conn.execute("SELECT name FROM schema_migration").fetchall()
        assert "001_init.sql" in {r[0] for r in rows}
