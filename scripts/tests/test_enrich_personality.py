"""gendered_fields() decides who gets the they/them retry, and the same
regex verifies the retry worked — so a pronoun form it cannot see is a
violation that ships. Reflexives were exactly that: \\b keeps "him" from
reaching into "himself", so "built it himself" passed here while the
pronoun pass (enrich_pronouns.GENDERED) caught it. The two patterns are
kept aligned by hand; these tests are what notices if they drift again.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from enrich_personality import gendered_fields


def test_reflexives_are_caught():
    assert gendered_fields({"summary": "Built the shade structure himself."})
    assert gendered_fields({"voice": "fixes the generator herself, quietly"})


def test_plain_pronouns_still_caught():
    assert gendered_fields({"summary": "He runs the kitchen."})
    assert gendered_fields({"cares_about": "the truck is hers"})


def test_caught_in_any_text_field_not_just_summary():
    assert gendered_fields({"shows_up_as": "does it all himself at 6am"})


def test_they_them_and_lookalike_words_pass():
    # "themselves", "the shed", "history", "there": the words the word
    # boundary exists to protect.
    assert not gendered_fields({
        "summary": "They built the shed themselves.",
        "voice": "brisk",
        "cares_about": "this camp values its history",
        "shows_up_as": "there before anyone else",
    })
