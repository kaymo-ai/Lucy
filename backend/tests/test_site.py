"""The codeword gate on the instructions page.

The security property that matters is not the strength of the codeword -- it
is one word shared in a camp thread and it will leak. It is that the page is
never served to a browser that has not passed. A JavaScript prompt in front of
a static file fails that test and this must not.
"""
from app.site import COOKIE


class TestUnlock:
    def test_the_right_codeword_sets_a_cookie(self, client):
        r = client.post("/v1/site-unlock", data={"code": "test-site-code"},
                        follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/"
        assert COOKIE in r.cookies

    def test_the_codeword_is_not_case_sensitive(self, client):
        # It is typed on a phone, where the keyboard capitalises the first
        # letter of everything by default.
        r = client.post("/v1/site-unlock", data={"code": "TEST-SITE-CODE"},
                        follow_redirects=False)
        assert COOKIE in r.cookies

    def test_surrounding_whitespace_is_forgiven(self, client):
        r = client.post("/v1/site-unlock", data={"code": "  test-site-code "},
                        follow_redirects=False)
        assert COOKIE in r.cookies

    def test_the_wrong_codeword_sets_nothing(self, client):
        r = client.post("/v1/site-unlock", data={"code": "guess"},
                        follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/?wrong=1"
        assert COOKIE not in r.cookies

    def test_the_cookie_is_not_readable_by_script(self, client):
        r = client.post("/v1/site-unlock", data={"code": "test-site-code"},
                        follow_redirects=False)
        assert "httponly" in r.headers["set-cookie"].lower()


class TestAuth:
    def test_no_cookie_is_401(self, client):
        assert client.get("/v1/site-auth").status_code == 401

    def test_a_valid_cookie_is_200(self, client):
        client.post("/v1/site-unlock", data={"code": "test-site-code"},
                    follow_redirects=False)
        # TestClient keeps the cookie jar between calls, as a browser would.
        assert client.get("/v1/site-auth").status_code == 200

    def test_a_forged_cookie_is_401(self, client):
        # An expiry far in the future with a signature that was never ours.
        client.cookies.set(COOKIE, "99999999999.notarealsignature")
        assert client.get("/v1/site-auth").status_code == 401

    def test_an_expired_cookie_is_401(self, client):
        from app.site import _sign
        client.cookies.set(COOKIE, _sign(1))   # signed by us, from 1970
        assert client.get("/v1/site-auth").status_code == 401

    def test_a_junk_cookie_is_401(self, client):
        client.cookies.set(COOKIE, "nonsense")
        assert client.get("/v1/site-auth").status_code == 401

    def test_no_www_authenticate_header(self, client):
        # That header is exactly what makes a browser open the username and
        # password dialog this whole thing exists to avoid.
        assert "www-authenticate" not in client.get("/v1/site-auth").headers


class TestServing:
    """The page itself, served by the API rather than by Caddy.

    The property under test is the one that matters: a browser that has not
    passed the codeword never receives the instructions, the screenshots, or
    anything else in the site directory.
    """

    def test_without_the_codeword_you_get_the_gate(self, client, site_dir):
        r = client.get("/")
        assert r.status_code == 200
        assert "Camp only" in r.text
        assert "GETTING HER" not in r.text

    def test_without_the_codeword_screenshots_are_not_served(self, client, site_dir):
        r = client.get("/screens/people.png")
        # The gate, not the image. Nothing in the directory leaks.
        assert r.headers["content-type"].startswith("text/html")
        assert b"\x89PNG" not in r.content

    def test_with_the_codeword_you_get_the_page(self, client, site_dir):
        client.post("/v1/site-unlock", data={"code": "test-site-code"},
                    follow_redirects=False)
        r = client.get("/")
        assert r.status_code == 200
        assert "GETTING HER" in r.text

    def test_with_the_codeword_screenshots_are_served(self, client, site_dir):
        client.post("/v1/site-unlock", data={"code": "test-site-code"},
                    follow_redirects=False)
        r = client.get("/screens/people.png")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"

    def test_the_api_is_not_swallowed_by_the_catch_all(self, client, site_dir):
        # The catch-all is GET /{path:path}. Registered too early it eats
        # every other route in the service.
        assert client.get("/v1/health").json()["ok"] is True

    def test_path_traversal_finds_nothing(self, client, site_dir):
        client.post("/v1/site-unlock", data={"code": "test-site-code"},
                    follow_redirects=False)
        assert client.get("/../../etc/passwd").status_code == 404

    def test_an_unknown_page_is_404_not_the_gate(self, client, site_dir):
        client.post("/v1/site-unlock", data={"code": "test-site-code"},
                    follow_redirects=False)
        assert client.get("/nothing-here.html").status_code == 404


class TestPublicTechPage:
    """/tech is the portfolio case study, deliberately outside the gate.

    Two properties, and the second matters more than the first: the page and
    its assets are served to a cookieless browser, and being under the /tech
    prefix does NOT open a way past the gate for anything else.
    """

    def test_the_page_is_served_without_a_cookie(self, client, site_dir):
        r = client.get("/tech")
        assert r.status_code == 200
        assert "CASE STUDY" in r.text
        assert "Camp only" not in r.text

    def test_its_screenshots_are_served_without_a_cookie(self, client, site_dir):
        r = client.get("/tech/chat.png")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"

    def test_the_camp_site_is_still_gated_beside_it(self, client, site_dir):
        # The whole risk of an ungated prefix is that it widens. The gate
        # page coming back here is the property the camp is relying on.
        assert "Camp only" in client.get("/").text
        assert b"\x89PNG" not in client.get("/screens/people.png").content

    def test_the_prefix_does_not_escape_its_directory(self, client, site_dir):
        # Encoded dots, because the HTTP client normalises literal ".." out
        # of the URL before the server ever sees it -- a request written
        # plainly here would test the client, not the route. Starlette
        # decodes %2e back into a dot on the way in.
        for sneak in ("/tech/%2e%2e/index.html",
                      "/tech/%2e%2e/screens/people.png",
                      "/tech/%2e%2e/%2e%2e/etc/passwd"):
            r = client.get(sneak)
            assert r.status_code == 404, sneak
            assert "GETTING HER" not in r.text

    def test_even_a_normalised_escape_never_shows_gated_content(self, client,
                                                                site_dir):
        # And the plain spelling too: whatever the client turns it into, the
        # response must never be the gated site.
        r = client.get("/tech/../index.html")
        assert "GETTING HER" not in r.text
        assert b"\x89PNG" not in client.get("/tech/../screens/people.png").content

    def test_a_missing_tech_asset_is_404_not_the_gate(self, client, site_dir):
        assert client.get("/tech/nothing.png").status_code == 404


class TestAdmin:
    """The QA page. Its own code, and invisible without it.

    404 rather than 401 for everything but the gate: there is no reason for
    anyone who does not already know this page exists to find out.
    """

    def unlock(self, client, code="test-admin-code"):
        return client.post("/v1/admin-unlock", data={"code": code},
                           follow_redirects=False)

    def test_without_the_code_you_get_a_form_and_no_data(self, client):
        r = client.get("/admin")
        assert r.status_code == 200
        assert "What has synced" not in r.text
        assert "admin-unlock" in r.text

    def test_the_camp_codeword_does_not_open_it(self, client):
        # The camp code is shared with forty people and unlocks install
        # instructions. It must not also unlock every note in the camp.
        client.post("/v1/site-unlock", data={"code": "test-site-code"},
                    follow_redirects=False)
        assert "What has synced" not in client.get("/admin").text

    def test_the_wrong_code_sets_nothing(self, client):
        r = self.unlock(client, code="guess")
        assert r.headers["location"] == "/admin?wrong=1"
        from app.admin import COOKIE
        assert COOKIE not in r.cookies

    def test_the_right_code_opens_it(self, client):
        self.unlock(client)
        r = client.get("/admin")
        assert r.status_code == 200
        assert "What has synced" in r.text

    def test_media_is_404_without_the_code(self, client):
        import uuid
        assert client.get(
            f"/admin/notes/{uuid.uuid4()}/photo").status_code == 404

    def test_it_shows_the_install_a_note_came_from(self, client):
        import json, uuid
        tok = client.post("/v1/join", json={
            "code": "test-code",
            "deviceId": "77777777-8888-9999-aaaa-bbbbbbbbbbbb"}).json()["token"]
        meta = json.dumps({"noteId": str(uuid.uuid4()),
                           "takenAt": "2026-08-28T19:04:00Z",
                           "transcript": "the tank is half", "attached": [],
                           "appVersion": "1.0 (150)"})
        client.post("/v1/notes", files=[
            ("meta", ("meta.json", meta, "application/json")),
            ("photo", ("photo.jpg", b"\xff\xd8jpeg", "image/jpeg"))],
            headers={"Authorization": f"Bearer {tok}"})
        self.unlock(client)
        page = client.get("/admin").text
        assert "the tank is half" in page
        # The install, truncated. This is the thing the app deliberately does
        # not show, and the reason this page has its own code.
        assert "77777777" in page
        assert "1.0 (150)" in page


class TestAdminBlankTranscripts:
    """A photo with no words is ordinary. A memo with no transcript is not.

    Showing both as "No transcript" made a real failure -- on-device
    recognition producing nothing -- look like the normal case.
    """

    def _note(self, client, tok, photo=b"\xff\xd8jpeg", memo=None, said=""):
        import json, uuid
        meta = json.dumps({"noteId": str(uuid.uuid4()),
                           "takenAt": "2026-08-28T19:04:00Z",
                           "transcript": said, "attached": [],
                           "appVersion": "1.0"})
        files = [("meta", ("meta.json", meta, "application/json"))]
        if photo:
            files.append(("photo", ("photo.jpg", photo, "image/jpeg")))
        if memo:
            files.append(("memo", ("memo.m4a", memo, "audio/mp4")))
        return client.post("/v1/notes", files=files,
                           headers={"Authorization": f"Bearer {tok}"})

    def _open(self, client):
        tok = client.post("/v1/join", json={
            "code": "test-code",
            "deviceId": "55555555-6666-7777-8888-999999999999"}).json()["token"]
        return tok

    def test_a_photo_with_no_words_is_not_flagged(self, client):
        tok = self._open(client)
        self._note(client, tok, said="")
        client.post("/v1/admin-unlock", data={"code": "test-admin-code"},
                    follow_redirects=False)
        page = client.get("/admin").text
        assert "Photo only" in page
        assert "not transcribed" not in page

    def test_a_memo_with_no_transcript_is_flagged(self, client):
        tok = self._open(client)
        self._note(client, tok, photo=None, memo=b"m4a", said="")
        client.post("/v1/admin-unlock", data={"code": "test-admin-code"},
                    follow_redirects=False)
        assert "Voice memo, not transcribed" in client.get("/admin").text


class TestLinkPreview:
    """og.png is served without the codeword, and nothing else is.

    A crawler has no cookie. WhatsApp fetches the URL the moment somebody
    pastes it into the camp thread; if og:image needs the gate, the preview is
    a grey box.
    """

    def test_the_card_needs_no_codeword(self, client, site_dir):
        (site_dir / "og.png").write_bytes(b"\x89PNG\r\n\x1a\ncard")
        r = client.get("/og.png")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"

    def test_nothing_else_is_ungated(self, client, site_dir):
        # The exemption is one filename, not a directory.
        r = client.get("/screens/people.png")
        assert r.headers["content-type"].startswith("text/html")

    def test_a_missing_card_is_404_not_the_gate(self, client, site_dir):
        assert client.get("/og.png").status_code == 404

    def test_the_gate_carries_the_preview_tags(self, client, site_dir):
        # The tags have to be on the page a crawler actually receives.
        (site_dir / "gate.html").write_text(
            '<html><head><meta property="og:image" content="x">'
            '</head><body><h1>Camp only</h1></body></html>')
        assert 'og:image' in client.get("/").text
