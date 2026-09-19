#!/usr/bin/env python3
"""The cache must never hand back another job's answer.

Every check here is offline. The danger this guards is not a slow build, it
is a WRONG one: ingest_all recreates ps_knowledge.db and reassigns every
autoincrement, so anything keyed on a row id would, after a rebuild, return
the answer belonging to whatever message inherited that id. It would look
perfectly healthy.

    python test_enrich_cache.py
"""
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from enrich_cache import Cache, fingerprint

SCHEMA = {"type": "object", "properties": {"facts": {"type": "array"}}}
OTHER_SCHEMA = {"type": "object", "properties": {"stories": {"type": "array"}}}
SYS = "You extract claims about a camp."
MODEL = "gemini-3.1-pro-preview"


def test_same_input_same_key():
    a = fingerprint(MODEL, SYS, SCHEMA, "Doris holds the ladders")
    b = fingerprint(MODEL, SYS, SCHEMA, "Doris holds the ladders")
    assert a == b
    print("   identical input -> identical key: PASS")


def test_every_component_changes_the_key():
    base = fingerprint(MODEL, SYS, SCHEMA, "prompt")
    variants = {
        "model": fingerprint("gemini-3.6-flash", SYS, SCHEMA, "prompt"),
        "system": fingerprint(MODEL, SYS + " Be terse.", SCHEMA, "prompt"),
        "schema": fingerprint(MODEL, SYS, OTHER_SCHEMA, "prompt"),
        "prompt": fingerprint(MODEL, SYS, SCHEMA, "prompt "),
    }
    for label, fp in variants.items():
        assert fp != base, f"changing the {label} did not change the key"
    assert len(set(variants.values())) == len(variants), "two variants collided"
    print("   model/system/schema/prompt each change the key: PASS")


def test_a_prompt_edit_invalidates_everything():
    """The failure this project has already had twice: a prompt changed and
    something stale kept being served."""
    old = [fingerprint(MODEL, SYS, SCHEMA, f"job {i}") for i in range(20)]
    new = [fingerprint(MODEL, SYS + "\nRule 6: no lists.", SCHEMA, f"job {i}")
           for i in range(20)]
    assert not (set(old) & set(new)), "a prompt edit left cached answers valid"
    print("   a system-prompt edit invalidates every entry: PASS")


def test_roundtrip_and_isolation():
    with tempfile.TemporaryDirectory() as tmp:
        c = Cache(Path(tmp) / "c.db")
        fp_a = fingerprint(MODEL, SYS, SCHEMA, "message A")
        fp_b = fingerprint(MODEL, SYS, SCHEMA, "message B")
        c.put(fp_a, {"facts": ["a"]}, pass_name="entities", model=MODEL)
        c.commit()
        assert c.get(fp_a) == {"facts": ["a"]}
        assert c.get(fp_b) is None, "B got A's answer"
        print("   stores and retrieves, and B never gets A's answer: PASS")


def test_survives_a_reopen():
    """The whole point: the corpus database is destroyed between runs and
    this file is not."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "c.db"
        fp = fingerprint(MODEL, SYS, SCHEMA, "durable")
        first = Cache(path)
        first.put(fp, {"facts": ["kept"]})
        first.commit()
        del first
        assert Cache(path).get(fp) == {"facts": ["kept"]}
        print("   survives being closed and reopened: PASS")


def test_hit_and_miss_are_counted():
    with tempfile.TemporaryDirectory() as tmp:
        c = Cache(Path(tmp) / "c.db")
        fp = fingerprint(MODEL, SYS, SCHEMA, "counted")
        c.get(fp)                    # miss
        c.put(fp, {"ok": True}); c.commit()
        c.get(fp)                    # hit
        assert (c.hits, c.misses) == (1, 1), (c.hits, c.misses)
        assert "1 hit" in c.report()
        print(f"   counts hits and misses ({c.report()}): PASS")


def test_disable_switch():
    import os
    os.environ["LUCY_NO_CACHE"] = "1"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            c = Cache(Path(tmp) / "c.db")
            fp = fingerprint(MODEL, SYS, SCHEMA, "x")
            c.put(fp, {"ok": True}); c.commit()
            assert c.get(fp) is None, "disabled cache still served an answer"
            assert "disabled" in c.report()
            print("   LUCY_NO_CACHE=1 turns it off completely: PASS")
    finally:
        del os.environ["LUCY_NO_CACHE"]


def main() -> int:
    print("enrichment cache\n")
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\n{len(tests)} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
