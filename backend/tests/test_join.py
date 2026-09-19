"""Joining is the whole of identity: no email, no password, one shared camp
code rotated per year.

This is weak authentication and it is the correct amount. What it must still
get right is the small stuff -- constant-time comparison, tokens stored hashed,
and re-joining from the same phone landing on the same member instead of
quietly forking the person in two.
"""
from app import db

DEVICE = "11111111-2222-3333-4444-555555555555"


def join(client, code="test-code", device=DEVICE, name="Marcus"):
    return client.post("/v1/join", json={
        "code": code, "deviceId": device, "displayName": name})


def join_without_a_name(client, code="test-code", device=DEVICE):
    """What the app actually sends. Notes are anonymous, so there is no name
    to collect and the field is never populated."""
    return client.post("/v1/join", json={"code": code, "deviceId": device})


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
            assert conn.execute(
                "SELECT count(*) FROM member").fetchone()[0] == 0

    def test_rejoining_the_same_phone_reuses_the_member(self, client):
        # Reinstalling the app must not fork someone in two and hand every
        # note they ever uploaded to a stranger with their name.
        first = join(client).json()
        second = join(client, name="Marcus F").json()
        assert first["memberId"] == second["memberId"]
        with db.pool.connection() as conn:
            assert conn.execute(
                "SELECT count(*) FROM member").fetchone()[0] == 1

    def test_rejoining_updates_the_display_name(self, client):
        join(client)
        join(client, name="Marcus F")
        with db.pool.connection() as conn:
            name = conn.execute("SELECT display_name FROM member").fetchone()[0]
        assert name == "Marcus F"

    def test_the_plaintext_token_is_not_stored(self, client):
        # A copy of this database must not be a set of working credentials.
        token = join(client).json()["token"]
        with db.pool.connection() as conn:
            rows = conn.execute(
                "SELECT token_sha256 FROM device_token").fetchall()
        assert token.encode() not in bytes(rows[0][0])


class TestBearerToken:
    def test_a_minted_token_authenticates(self, client):
        token = join(client).json()["token"]
        r = client.get("/v1/whoami",
                       headers={"Authorization": f"Bearer {token}"})
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
        assert client.get(
            "/v1/whoami", headers={"Authorization": token}).status_code == 401


class TestAnonymousJoin:
    def test_a_join_with_no_name_is_accepted(self, client):
        r = join_without_a_name(client)
        assert r.status_code == 200
        assert len(r.json()["token"]) >= 32

    def test_an_older_build_sending_a_name_still_joins(self, client):
        # Build 143 and earlier send one. A phone that cannot join is a phone
        # whose notes are stuck on it.
        assert join(client, name="Marcus").status_code == 200


class TestRotation:
    """LUCY_JOIN_CODE is a list because the secret lives in the app binary.

    Changing it needs a new build, and builds reach phones over days. Without
    a window where both work, rotating the code means every phone that has not
    updated yet stops being able to join.
    """

    def test_either_code_in_the_list_is_accepted(self, client, monkeypatch):
        from app.settings import settings
        object.__setattr__(settings, "join_code", "old-code, new-code")
        try:
            assert join(client, code="old-code").status_code == 200
            assert join(client, code="new-code").status_code == 200
        finally:
            object.__setattr__(settings, "join_code", "test-code")

    def test_a_code_not_in_the_list_is_still_refused(self, client):
        from app.settings import settings
        object.__setattr__(settings, "join_code", "old-code, new-code")
        try:
            assert join(client, code="test-code").status_code == 403
        finally:
            object.__setattr__(settings, "join_code", "test-code")
