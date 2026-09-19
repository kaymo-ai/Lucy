"""Notes: upload, list, media.

The two things that must hold on a bad connection in the desert: an upload that
dies halfway leaves nothing behind that stops the retry, and a retry that
succeeds does not produce a second note. The note's id comes from the phone for
exactly that reason.
"""
import json
import uuid

from app import db

DEVICE_A = "11111111-2222-3333-4444-555555555555"
DEVICE_B = "99999999-8888-7777-6666-555555555555"


def token(client, device=DEVICE_A, name="Marcus"):
    return client.post("/v1/join", json={
        "code": "test-code", "deviceId": device,
        "displayName": name}).json()["token"]


def upload(client, tok, note_id=None, photo=b"\xff\xd8jpegbytes", memo=None,
           transcript="the water tank is half", attached=("water",)):
    meta = {
        "noteId": note_id or str(uuid.uuid4()),
        "takenAt": "2026-08-28T19:04:00Z",
        "transcript": transcript,
        "attached": list(attached),
        "appVersion": "1.0 (142)",
    }
    files = [("meta", ("meta.json", json.dumps(meta), "application/json"))]
    if photo is not None:
        files.append(("photo", ("photo.jpg", photo, "image/jpeg")))
    if memo is not None:
        files.append(("memo", ("memo.m4a", memo, "audio/mp4")))
    return client.post("/v1/notes", files=files,
                       headers={"Authorization": f"Bearer {tok}"})


def listing(client, tok, since=0):
    return client.get(f"/v1/notes?since={since}",
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
            row = conn.execute("SELECT transcript, attached FROM note").fetchone()
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
        assert upload(client, token(client),
                      photo=None, memo=None).status_code == 400

    def test_unreadable_meta_is_refused(self, client):
        tok = token(client)
        r = client.post("/v1/notes", files=[
            ("meta", ("meta.json", "not json at all", "application/json")),
            ("photo", ("photo.jpg", b"x", "image/jpeg"))],
            headers={"Authorization": f"Bearer {tok}"})
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
        # Notes are immutable. A phone re-sending a note it already sent must
        # not be able to rewrite the camp's copy.
        tok = token(client)
        note_id = str(uuid.uuid4())
        upload(client, tok, note_id=note_id, transcript="original")
        upload(client, tok, note_id=note_id, transcript="rewritten")
        with db.pool.connection() as conn:
            assert conn.execute(
                "SELECT transcript FROM note").fetchone()[0] == "original"


class TestOrdering:
    def test_seq_increases_and_does_not_repeat(self, client):
        tok = token(client)
        seqs = [upload(client, tok).json()["seq"] for _ in range(5)]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == 5


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

    def test_carries_no_author(self, client):
        # Notes are anonymous to the camp. Nothing in the list identifies who
        # sent one -- not a name, not a member id, nothing to join against.
        # The server still knows, and the app says so rather than implying
        # more privacy than exists.
        a = token(client, DEVICE_A, "Marcus")
        upload(client, a)
        note = listing(client, a).json()["notes"][0]
        assert "author" not in note and "authorId" not in note
        assert "Marcus" not in listing(client, a).text

    def test_two_peoples_notes_are_indistinguishable(self, client):
        a = token(client, DEVICE_A, "Marcus")
        b = token(client, DEVICE_B, "Dusty")
        upload(client, a, transcript="one")
        upload(client, b, transcript="two")
        notes = listing(client, a).json()["notes"]
        assert {n["transcript"] for n in notes} == {"one", "two"}
        # Same shape for both: nothing distinguishes whose is whose.
        assert notes[0].keys() == notes[1].keys()

    def test_since_returns_only_what_is_newer(self, client):
        tok = token(client)
        first = upload(client, tok, transcript="one").json()
        upload(client, tok, transcript="two")
        after = listing(client, tok, since=first["seq"]).json()
        assert [n["transcript"] for n in after["notes"]] == ["two"]

    def test_cursor_is_the_highest_seq_returned(self, client):
        tok = token(client)
        upload(client, tok)
        second = upload(client, tok).json()
        assert listing(client, tok).json()["cursor"] == second["seq"]

    def test_cursor_holds_when_nothing_is_new(self, client):
        # Resetting to zero would re-report the whole week as unseen.
        tok = token(client)
        seq = upload(client, tok).json()["seq"]
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
        note = upload(client, tok, photo=b"\xff\xd8exactlythese").json()
        r = client.get(f"/v1/notes/{note['noteId']}/photo",
                       headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200
        assert r.content == b"\xff\xd8exactlythese"

    def test_memo_comes_back(self, client):
        tok = token(client)
        note = upload(client, tok, photo=None, memo=b"m4abytes").json()
        r = client.get(f"/v1/notes/{note['noteId']}/memo",
                       headers={"Authorization": f"Bearer {tok}"})
        assert r.content == b"m4abytes"

    def test_absent_media_is_404(self, client):
        tok = token(client)
        note = upload(client, tok, photo=b"jpeg", memo=None).json()
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
        note = upload(client, tok).json()
        assert client.get(
            f"/v1/notes/{note['noteId']}/photo").status_code == 401
