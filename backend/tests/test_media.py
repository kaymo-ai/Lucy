"""Blobs are content-addressed and never deleted.

Two members photographing the same sign produce byte-identical JPEGs and
therefore one file. If deleting a note unlinked its blob, the other member's
note would silently lose its photo -- so nothing here removes anything, and the
delete path (not yet built) tombstones rows and leaves the disk alone.

These tests go through put/get/exists and never touch a path, on purpose: that
is the interface the note routes are allowed to use, and keeping the tests
honest about it is what stops a filesystem assumption leaking back in.
"""
from app import media
from app.settings import settings

HELLO_SHA = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"


class TestPut:
    def test_returns_the_sha256(self):
        assert media.put(b"hello") == HELLO_SHA

    def test_the_bytes_come_back(self):
        assert media.get(media.put(b"hello")) == b"hello"

    def test_identical_bytes_land_on_one_blob(self):
        a = media.put(b"same picture")
        b = media.put(b"same picture")
        assert a == b
        assert media.get(a) == b"same picture"

    def test_stores_only_one_copy_of_identical_bytes(self):
        media.put(b"same picture")
        media.put(b"same picture")
        files = [p for p in settings.media_root.rglob("*") if p.is_file()]
        assert len(files) == 1

    def test_leaves_no_partial_file_behind(self):
        media.put(b"hello")
        assert list(settings.media_root.rglob("*.part")) == []


class TestGet:
    def test_none_for_something_never_written(self):
        assert media.get("0" * 64) is None


class TestExists:
    def test_true_after_put(self):
        assert media.exists(media.put(b"hello"))

    def test_false_for_something_never_written(self):
        assert not media.exists("0" * 64)
