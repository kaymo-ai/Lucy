#!/usr/bin/env python3
"""
Stamp a build manifest into the knowledge database and install it into the app.

Why this exists
---------------
A previous build shipped vectors of one dimension into a runtime expecting
another. Nothing failed loudly: cosine similarity between mismatched vectors is
just a number, so every query returned confident nonsense. The `meta` table is
the guard. The app reads `embedding_model` and `embedding_dim` at launch and
refuses to start on a mismatch, which turns a silent wrong answer into an
obvious startup failure.

That guard only works if the manifest describes what is *actually in the file*.
So the values here are measured from the embeddings themselves and then checked
against what this build expects — writing the constants in blind would produce
a manifest that always agrees with itself and catches nothing.

`package_db.py --install` is the only supported way to update the shipped
database. The manual `cp` it replaces is how the dimension mismatch shipped.

Usage:
    python package_db.py ./output/ps_knowledge.db
    python package_db.py ./output/ps_knowledge.db --install
    python package_db.py ./output/ps_knowledge.db --install --app-db /path/to/ps_knowledge.db
"""
import argparse
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

EMBED_MODEL = "embeddinggemma-300M-Q8_0"
EMBED_DIM = 768
SCHEMA_VERSION = "2"

BYTES_PER_FLOAT = 4  # embeddings are stored as Float32 BLOBs

REQUIRED_KEYS = (
    "embedding_model", "embedding_dim", "schema_version",
    "built_at", "source_commit", "message_count", "knowledge_count",
    "embedded_message_count",
)

SCRIPTS_DIR = Path(__file__).resolve().parent
APP_DB = SCRIPTS_DIR.parent / "LucyPT" / "LucyPT" / "ps_knowledge.db"


def measure_embeddings(conn) -> tuple[str, int]:
    """Read the model and dimension out of the stored vectors themselves.

    Returns (model_name, dim). Raises SystemExit if the table is empty, holds
    more than one model, or holds vectors of more than one length — each of
    which means the file cannot be described by a single manifest, and is
    exactly the state that produced the shipped defect.
    """
    models = [r[0] for r in conn.execute(
        "SELECT DISTINCT model_name FROM knowledge_embeddings")]
    if not models:
        raise SystemExit("refusing to package: knowledge_embeddings is empty")
    if len(models) > 1:
        raise SystemExit(
            f"refusing to package: embeddings came from {len(models)} different "
            f"models ({', '.join(sorted(map(str, models)))}); the manifest can "
            "only describe one")

    sizes = [r[0] for r in conn.execute(
        "SELECT DISTINCT length(embedding) FROM knowledge_embeddings")]
    if len(sizes) > 1:
        raise SystemExit(
            f"refusing to package: embeddings have {len(sizes)} different byte "
            f"lengths ({sorted(sizes)}); vectors of mixed dimension cannot be "
            "compared")
    if sizes[0] % BYTES_PER_FLOAT:
        raise SystemExit(
            f"refusing to package: embedding blobs are {sizes[0]} bytes, which "
            f"is not a whole number of {BYTES_PER_FLOAT}-byte floats")

    return models[0], sizes[0] // BYTES_PER_FLOAT


def write_manifest(conn, source_commit: str) -> None:
    """Verify the database, then stamp the meta table describing it."""
    # Every knowledge row must have a vector. Comparing counts is not enough —
    # 616 embeddings against 616 rows still passes if one row has two vectors
    # and another has none.
    orphans = conn.execute("""
        SELECT COUNT(*) FROM camp_knowledge k
        WHERE NOT EXISTS (SELECT 1 FROM knowledge_embeddings e
                          WHERE e.knowledge_id = k.id)
    """).fetchone()[0]
    if orphans:
        raise SystemExit(
            f"refusing to package: {orphans} camp_knowledge row(s) have no "
            "embedding; run embed_corpus.py first")

    model, dim = measure_embeddings(conn)
    if model != EMBED_MODEL:
        raise SystemExit(
            f"refusing to package: embeddings were made by {model!r} but this "
            f"build expects {EMBED_MODEL!r}")
    if dim != EMBED_DIM:
        raise SystemExit(
            f"refusing to package: embeddings are {dim}-dimensional but this "
            f"build expects {EMBED_DIM}. This is the mismatch the manifest "
            "exists to catch — do not edit EMBED_DIM to make it pass")

    knowledge = conn.execute("SELECT COUNT(*) FROM camp_knowledge").fetchone()[0]
    messages = conn.execute("SELECT COUNT(*) FROM person_content").fetchone()[0]
    embedded_messages = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]

    conn.executescript("""
        DROP TABLE IF EXISTS meta;
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)
    conn.executemany(
        "INSERT INTO meta (key, value) VALUES (?, ?)",
        [
            ("embedding_model", model),
            ("embedding_dim", str(dim)),
            ("schema_version", SCHEMA_VERSION),
            ("built_at", datetime.now(timezone.utc).isoformat()),
            ("source_commit", source_commit),
            ("message_count", str(messages)),
            ("knowledge_count", str(knowledge)),
            ("embedded_message_count", str(embedded_messages)),
        ],
    )
    conn.commit()


def source_commit() -> str:
    """The commit this build came from.

    Resolved from the scripts directory, not from the database's directory.
    `scripts/output` may be a symlink into a different worktree, and asking git
    there would stamp an unrelated branch's commit into the manifest.
    """
    r = subprocess.run(["git", "rev-parse", "HEAD"],
                       capture_output=True, text=True, cwd=SCRIPTS_DIR)
    return r.stdout.strip() or "unknown"


def main() -> None:
    ap = argparse.ArgumentParser(description="Stamp and install the knowledge DB.")
    ap.add_argument("db", type=Path, help="path to ps_knowledge.db")
    ap.add_argument("--install", action="store_true",
                    help="copy into the app bundle after stamping")
    ap.add_argument("--app-db", type=Path, default=APP_DB,
                    help=f"install target (default: {APP_DB})")
    args = ap.parse_args()

    if not args.db.exists():
        raise SystemExit(f"no database at {args.db}")

    conn = sqlite3.connect(args.db)
    write_manifest(conn, source_commit())

    print("manifest:")
    for key, value in conn.execute("SELECT key, value FROM meta ORDER BY key"):
        print(f"  {key:<24} {value}")

    conn.execute("VACUUM")
    conn.close()

    print(f"\n{args.db} — {args.db.stat().st_size / 1_048_576:.0f} MB")

    if args.install:
        target = args.app_db
        if not target.parent.exists():
            raise SystemExit(f"install target directory does not exist: {target.parent}")
        if target.exists():
            print(f"replacing {target} "
                  f"({target.stat().st_size / 1_048_576:.0f} MB)")
        shutil.copy2(args.db, target)
        print(f"installed to {target} "
              f"({target.stat().st_size / 1_048_576:.0f} MB)")


if __name__ == "__main__":
    main()
