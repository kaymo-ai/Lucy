#!/usr/bin/env python3
"""Check the tuning-schema path in build_voice_dataset.py without spending.

Everything here runs offline. The expensive half of that script -- generating
pairs through the model -- is untouched; what is checked is the part that
decides what reaches a training file, which is the part that cannot be undone
later. A database gets redact() and a gate before it ships. A LoRA gets
neither.

    python test_voice_dataset.py
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import build_voice_dataset as B


def test_generate_content_schema():
    """The shape the tuning job accepts, field for field."""
    row = {"instruction": "How does the camp handle grey water?",
           "response": "Grey water goes to the evap pond behind the kitchen.",
           "source": "corpus"}
    gc = B.as_generate_content(row)
    assert set(gc) == {"systemInstruction", "contents"}, gc.keys()
    assert gc["systemInstruction"]["parts"][0]["text"] == B.VOICE_SYSTEM
    # The service's vocabulary, not OpenAI's and not the docs' example:
    # "Supported roles are: user, model, function."
    assert [c["role"] for c in gc["contents"]] == ["user", "model"]
    assert gc["contents"][0]["parts"][0]["text"] == row["instruction"]
    assert gc["contents"][1]["parts"][0]["text"] == row["response"]
    # No stray keys: the auditable file carries `source`, the uploaded one
    # must not, and json.dumps would happily ship it.
    assert "source" not in json.dumps(gc)
    print("   generate_content schema: PASS")


def test_prompt_completion_schema():
    row = {"instruction": "q", "response": "a", "source": "culture"}
    assert B.as_prompt_completion(row) == {"prompt": "q", "completion": "a"}
    print("   prompt_completion schema: PASS")


def test_system_instruction_is_not_the_grounding_prompt():
    """The one that matters.

    LucyBrain.prompt's Rule 1 is "anything particular to this camp comes only
    from the facts below". These rows carry no facts. Training an answer
    under that instruction teaches the model to break the rule while being
    told it, and there is no un-training a LoRA.
    """
    text = B.VOICE_SYSTEM.lower()
    for forbidden in ("only from the facts", "rules", "facts below",
                      "you do not know it"):
        assert forbidden not in text, f"grounding language leaked: {forbidden}"
    assert "lucy" in text, "the identity is the part voice hangs off"
    print("   system instruction is identity, not grounding: PASS")


def test_length_cap_matches_the_documented_limit():
    """8192 is the documented maximum sequence length for every Gemma the
    platform will tune -- E2B IT and 3-1B IT alike."""
    assert B.MAX_SEQUENCE_TOKENS == 8192
    short = B.estimated_tokens(B.VOICE_SYSTEM, "a" * 300, "b" * 300)
    huge = B.estimated_tokens(B.VOICE_SYSTEM, "a" * 40000, "b" * 40000)
    assert short < B.MAX_SEQUENCE_TOKENS < huge, (short, huge)
    # The estimate must OVERCOUNT against a real tokeniser, so the cap errs
    # toward dropping a row rather than letting the job truncate one.
    assert B.CHARS_PER_TOKEN < 4.0, "Gemma averages ~4 chars/token on prose"
    print("   length cap: PASS")


def _rows(n_corpus=90, n_culture=10):
    return ([{"instruction": f"q{i}", "response": f"a{i}", "source": "corpus"}
             for i in range(n_corpus)]
            + [{"instruction": f"c{i}", "response": f"a{i}", "source": "culture"}
               for i in range(n_culture)])


def test_split_holds_back_every_source():
    """Stratified, or validation measures half the work.

    The dataset teaches two things -- the camp's own threads and Burning Man
    culture. An unstratified 10% split over 90/10 rows can hand every culture
    pair to training, and the run then reports a validation number that says
    nothing about half of what was taught.
    """
    rows = _rows()
    train, val = B.stratified_split(rows, seed=42, fraction=0.1)
    assert len(train) + len(val) == len(rows)
    assert sorted({r["source"] for r in val}) == ["corpus", "culture"]
    print(f"   split holds back every source (train={len(train)}, "
          f"val={len(val)}): PASS")


def test_split_does_not_leak():
    rows = _rows()
    train, val = B.stratified_split(rows, seed=42, fraction=0.1)
    key = lambda rs: {(r["instruction"], r["source"]) for r in rs}
    assert not (key(train) & key(val)), "a row is in both halves"
    print("   no row in both halves: PASS")


def test_split_is_seed_deterministic():
    """A rebuild must reproduce the same split, or two runs are not
    comparable and the validation number drifts for no reason."""
    rows = _rows()
    assert B.stratified_split(rows, 42, 0.1) == B.stratified_split(rows, 42, 0.1)
    assert B.stratified_split(rows, 7, 0.1) != B.stratified_split(rows, 42, 0.1)
    print("   split is seed-deterministic: PASS")


def main() -> int:
    print("voice dataset, tuning-schema checks\n")
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\n{len(tests)} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
