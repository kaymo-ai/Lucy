import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from package_db import EMBED_DIM, EMBED_MODEL, REQUIRED_KEYS, write_manifest

FLOATS = 4  # bytes per float32
GOOD_BLOB = EMBED_DIM * FLOATS


@pytest.fixture
def conn():
    """A minimal, valid database: one knowledge row with one good embedding."""
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE person_content (id INTEGER PRIMARY KEY, content TEXT);
        CREATE TABLE camp_knowledge (id INTEGER PRIMARY KEY, title TEXT, content TEXT);
        CREATE TABLE knowledge_embeddings (
            id INTEGER PRIMARY KEY, knowledge_id INTEGER, embedding BLOB, model_name TEXT);
        CREATE TABLE embeddings (
            id INTEGER PRIMARY KEY, content_id INTEGER, embedding BLOB);
        INSERT INTO person_content (id, content) VALUES (1, 'hello');
        INSERT INTO embeddings (content_id, embedding) VALUES (1, zeroblob(3072));
        INSERT INTO camp_knowledge (id, title, content) VALUES (1, 't', 'c');
    """)
    c.execute("INSERT INTO knowledge_embeddings (knowledge_id, embedding, model_name) "
              "VALUES (1, zeroblob(?), ?)", (GOOD_BLOB, EMBED_MODEL))
    c.commit()
    return c


def manifest(c) -> dict:
    return dict(c.execute("SELECT key, value FROM meta").fetchall())


def test_manifest_contains_every_required_key(conn):
    write_manifest(conn, source_commit="abc123")
    rows = manifest(conn)
    for key in REQUIRED_KEYS:
        assert key in rows, f"missing manifest key: {key}"


def test_manifest_records_counts_and_commit(conn):
    write_manifest(conn, source_commit="abc123")
    rows = manifest(conn)
    assert rows["source_commit"] == "abc123"
    assert rows["message_count"] == "1"
    assert rows["knowledge_count"] == "1"
    assert rows["embedded_message_count"] == "1"


def test_dimension_is_measured_from_the_data_not_asserted(conn):
    """The manifest must describe the file, not repeat the build's constants.

    A manifest that writes EMBED_DIM blind always agrees with itself and would
    have caught nothing when 384-dim vectors shipped into a 512-dim runtime.
    """
    write_manifest(conn, source_commit="abc123")
    rows = manifest(conn)
    assert rows["embedding_dim"] == str(EMBED_DIM)
    assert rows["embedding_model"] == EMBED_MODEL


def test_refuses_when_a_knowledge_row_has_no_embedding(conn):
    conn.execute("INSERT INTO camp_knowledge (id, title, content) VALUES (2, 't2', 'c2')")
    conn.commit()
    with pytest.raises(SystemExit, match="no embedding"):
        write_manifest(conn, source_commit="abc123")


def test_counting_embeddings_is_not_enough_to_prove_coverage(conn):
    """Two vectors on one row and none on another still counts 2 against 2."""
    conn.execute("INSERT INTO camp_knowledge (id, title, content) VALUES (2, 't2', 'c2')")
    conn.execute("INSERT INTO knowledge_embeddings (knowledge_id, embedding, model_name) "
                 "VALUES (1, zeroblob(?), ?)", (GOOD_BLOB, EMBED_MODEL))
    conn.commit()
    counts = conn.execute("SELECT (SELECT COUNT(*) FROM camp_knowledge), "
                          "(SELECT COUNT(*) FROM knowledge_embeddings)").fetchone()
    assert counts == (2, 2), "precondition: a naive count check would pass here"
    with pytest.raises(SystemExit, match="no embedding"):
        write_manifest(conn, source_commit="abc123")


def test_refuses_on_a_wrong_dimension(conn):
    conn.execute("UPDATE knowledge_embeddings SET embedding = zeroblob(?)", (384 * FLOATS,))
    conn.commit()
    with pytest.raises(SystemExit, match="384-dimensional"):
        write_manifest(conn, source_commit="abc123")


def test_refuses_on_mixed_dimensions(conn):
    conn.execute("INSERT INTO camp_knowledge (id, title, content) VALUES (2, 't2', 'c2')")
    conn.execute("INSERT INTO knowledge_embeddings (knowledge_id, embedding, model_name) "
                 "VALUES (2, zeroblob(?), ?)", (384 * FLOATS, EMBED_MODEL))
    conn.commit()
    with pytest.raises(SystemExit, match="different byte"):
        write_manifest(conn, source_commit="abc123")


def test_refuses_on_a_wrong_model(conn):
    conn.execute("UPDATE knowledge_embeddings SET model_name = 'all-MiniLM-L6-v2'")
    conn.commit()
    with pytest.raises(SystemExit, match="all-MiniLM-L6-v2"):
        write_manifest(conn, source_commit="abc123")


def test_refuses_on_mixed_models(conn):
    conn.execute("INSERT INTO camp_knowledge (id, title, content) VALUES (2, 't2', 'c2')")
    conn.execute("INSERT INTO knowledge_embeddings (knowledge_id, embedding, model_name) "
                 "VALUES (2, zeroblob(?), 'all-MiniLM-L6-v2')", (GOOD_BLOB,))
    conn.commit()
    with pytest.raises(SystemExit, match="different\n?\\s*models|2 different"):
        write_manifest(conn, source_commit="abc123")


def test_refuses_on_an_empty_embeddings_table(conn):
    conn.execute("DELETE FROM knowledge_embeddings")
    conn.execute("DELETE FROM camp_knowledge")
    conn.commit()
    with pytest.raises(SystemExit, match="empty"):
        write_manifest(conn, source_commit="abc123")


def test_refuses_on_a_blob_that_is_not_whole_floats(conn):
    conn.execute("UPDATE knowledge_embeddings SET embedding = zeroblob(3070)")
    conn.commit()
    with pytest.raises(SystemExit, match="whole number"):
        write_manifest(conn, source_commit="abc123")
