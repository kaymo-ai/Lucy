"""Photos and voice memos on the VM's own disk, addressed by content.

Not GCS, deliberately. A playa server has no GCS, and a design that only works
with a cloud dependency cannot move to a box behind a camp router. Disk is
cheap and the volume is small: a hundred notes a week at ~2.5 MB is a quarter
of a gigabyte.

Blobs are never deleted. Two members photographing the same sign produce
byte-identical JPEGs and therefore one file; if deleting a note unlinked its
blob, the other member's note would silently lose its photo. The delete path
(not yet built) tombstones rows and leaves the disk alone.

The interface is deliberately three functions that say nothing about where the
bytes are: put, get, exists. Callers never see a path. That is the whole
difference between swapping in GCS later -- if this ever moves to Cloud Run,
where the local disk is ephemeral -- and rewriting the note routes. The disk
implementation below stays the one the playa box uses either way.
"""
import hashlib
import os
from pathlib import Path

from .settings import settings


def _path(sha: str) -> Path:
    # Sharded two levels. One flat directory with fifty thousand files in it is
    # a directory nobody can list on a small VM.
    return settings.media_root / sha[0:2] / sha[2:4] / sha


def exists(sha: str) -> bool:
    return _path(sha).is_file()


def get(sha: str) -> bytes | None:
    """The bytes, or None if this store has never seen them."""
    p = _path(sha)
    return p.read_bytes() if p.is_file() else None


def put(data: bytes) -> str:
    """Writes the bytes if they are not already there. Returns the digest."""
    sha = hashlib.sha256(data).hexdigest()
    dest = _path(sha)
    if dest.is_file():
        return sha
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Written under a temporary name in the same directory and renamed. A
    # half-written blob under its final name is indistinguishable from a
    # complete one, and the digest is the only thing anybody checks.
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, dest)
    return sha
