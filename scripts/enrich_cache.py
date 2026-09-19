#!/usr/bin/env python3
"""Remember what the model already answered, across rebuilds.

WHY THIS EXISTS. `ingest_all.py` recreates ps_knowledge.db from scratch on
every run, so every enrichment pass re-sends every job — 650-odd requests
against a corpus that is 94% unchanged. Measured 2026-08-20: 1,830 of 27,163
messages arrived since 23 January, six per cent, and a full rebuild spent
hours re-deriving the other ninety-four.

Worse than the cost, it set the iteration loop at several hours. Every
question asked that day — is Flash good enough, did the trim break anything,
which entities went missing — took a whole run to answer, and three runs were
abandoned part-way. A slow loop is why the day produced two measurements
instead of a corpus.

KEYED BY CONTENT, NOT BY ROW ID. person_content.id is an autoincrement and is
reassigned on every rebuild; a cache keyed on it would return another
message's answer, confidently. The key here is a hash of exactly what
determines the response — the model, the system prompt, the schema and the
job's own prompt text. Change any of them and the entry misses, which is the
correct behaviour: a prompt edit SHOULD invalidate every cached answer, and
that is the failure this project has been bitten by twice, where a prompt
changed and something stale kept being served.

WHAT IT DOES NOT DO. It does not make the model deterministic and does not
pretend to. Two runs over identical input now return the identical cached
answer rather than two samples — which is a change in behaviour, and mostly a
welcome one: entity discovery varied 149/138 between two Pro runs on the same
corpus, and that variance was noise nobody wanted.

Storage is a sidecar database, deliberately NOT ps_knowledge.db, because that
file is destroyed and recreated by the thing this exists to speed up.

    from enrich_cache import Cache
    cache = Cache()
    hit = cache.get(fingerprint)
    cache.put(fingerprint, payload)
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_PATH = HERE / "output" / "enrich_cache.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS answer (
    fingerprint TEXT PRIMARY KEY,
    pass        TEXT,
    payload     TEXT NOT NULL,
    model       TEXT,
    written_at  TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS answer_pass ON answer(pass);
"""


def fingerprint(model: str, system_text: str, schema: dict, prompt: str) -> str:
    """Everything that determines the answer, and nothing that does not.

    The schema is included because a changed response_schema changes the
    shape of what comes back even when the prompt is identical, and a cache
    that ignored it would hand a caller the old shape and let it fail
    somewhere far away from the cause.
    """
    h = hashlib.sha256()
    for part in (model, system_text, json.dumps(schema, sort_keys=True), prompt):
        h.update(part.encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()


class Cache:
    """A persistent answer cache. Disabled entirely by LUCY_NO_CACHE=1."""

    def __init__(self, path: Path | str = DEFAULT_PATH):
        self.enabled = os.environ.get("LUCY_NO_CACHE") != "1"
        self.hits = 0
        self.misses = 0
        self.conn: sqlite3.Connection | None = None
        if not self.enabled:
            return
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)

    def get(self, fp: str):
        """The cached payload, or None. A miss and a cached null are
        different things, so a stored null comes back as the string 'null'
        parsed to None -- callers treat both as 'no usable answer' anyway."""
        if not self.conn:
            self.misses += 1
            return None
        row = self.conn.execute(
            "SELECT payload FROM answer WHERE fingerprint = ?", (fp,)).fetchone()
        if row is None:
            self.misses += 1
            return None
        self.hits += 1
        try:
            return json.loads(row[0])
        except Exception:
            return None

    def put(self, fp: str, payload, pass_name: str = "", model: str = "") -> None:
        if not self.conn:
            return
        self.conn.execute(
            "INSERT OR REPLACE INTO answer (fingerprint, pass, payload, model)"
            " VALUES (?, ?, ?, ?)",
            (fp, pass_name, json.dumps(payload, ensure_ascii=False), model))

    def commit(self) -> None:
        if self.conn:
            self.conn.commit()

    def report(self) -> str:
        total = self.hits + self.misses
        if not self.enabled:
            return "cache disabled (LUCY_NO_CACHE=1)"
        if not total:
            return "cache: nothing asked"
        return (f"cache: {self.hits} hit, {self.misses} to send "
                f"({self.hits * 100 // total}% reused)")


def stats(path: Path | str = DEFAULT_PATH) -> None:
    path = Path(path)
    if not path.exists():
        print(f"no cache at {path}")
        return
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    total = conn.execute("SELECT COUNT(*) FROM answer").fetchone()[0]
    print(f"{total} cached answers in {path.name} "
          f"({path.stat().st_size // 1024} KB)")
    for pass_name, n in conn.execute(
            "SELECT COALESCE(pass,'?'), COUNT(*) FROM answer "
            "GROUP BY 1 ORDER BY 2 DESC"):
        print(f"   {n:>5}  {pass_name}")


if __name__ == "__main__":
    stats()
