"""
Seven signup sheets, one roster.

The hard part is not parsing, it is identity. Email only appears from 2019 on,
so the early years must be matched by name, and 2017 abbreviates them. These
tests pin the two failures that matter in opposite directions: splitting one
person into two records (visible, recoverable) and fusing two people into one
(invisible, not).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from build_people import (ATTENDING, LISTED, MAYBE, NOT_ATTENDING, Appearance,
                          Person, confirms, first_names_agree, merge_variants,
                          apply_same_person, norm, person_name,
                          read_status, split_name, surnames_agree)


def appearance(year, first, last, email="", status=ATTENDING, city=""):
    return Appearance(year=year, display=f"{first} {last}".strip(), first=first,
                      last=last, nickname="", email=email, phone="", city=city,
                      status=status, source="t.csv", row=1)


def person(*apps):
    p = Person()
    for a in apps:
        p.add(a, "")
    return p


class TestReadStatus:
    def test_the_many_ways_people_write_yes(self):
        for raw in ["Yes", "yes!", "Yesssss!", "Yes. Obvi", "100%", "LOLYES",
                    "IWBLY", "Confident", "Yep"]:
            assert read_status(raw, LISTED) == ATTENDING, raw

    def test_no_beats_yes_in_the_same_cell(self):
        assert read_status("Not attending", ATTENDING) == NOT_ATTENDING
        assert read_status("No. :(", ATTENDING) == NOT_ATTENDING

    def test_a_hedge_is_read_as_a_hedge(self):
        # "yes/hopefully" contains a yes; the hedge is the honest reading.
        assert read_status("yes/hopefully", LISTED) == MAYBE
        assert read_status("50/50", LISTED) == MAYBE
        assert read_status("Hopefully", LISTED) == MAYBE
        assert read_status(
            "Unlikely because I am at the top of a tall mountain in Tibet",
            LISTED) == MAYBE

    def test_blank_falls_back_to_the_sheets_default(self):
        # The 2022 file is the confirmed roster; being on it is the answer.
        assert read_status("", ATTENDING) == ATTENDING
        assert read_status("   ", LISTED) == LISTED


class TestConfirms:
    """2019 has no yes/no column, so commitment columns stand in for one."""

    def test_an_amount_of_dues_is_a_commitment(self):
        assert confirms("420")
        assert confirms("940")

    def test_a_shift_number_is_a_commitment(self):
        assert confirms("1")
        assert confirms("2")

    def test_words_meaning_paid(self):
        assert confirms("Paid")
        assert confirms("Venmo")

    def test_filled_in_is_not_enough(self):
        # The dues column holds these, and treating any non-empty value as a
        # yes flipped four people who had written "No. :(" into attendees.
        assert not confirms("NO DUES")
        assert not confirms("Refunded")

    def test_zero_and_blank(self):
        assert not confirms("0")
        assert not confirms("")
        assert not confirms("   ")


class TestSplitName:
    def test_inline_nickname_is_kept(self):
        assert split_name("Piotr 'Sunrise' Bartkowski") == (
            "Piotr", "Bartkowski", "Sunrise")

    def test_curly_quotes(self):
        assert split_name('Joseph "ZenMaster" Carlton') == (
            "Joseph", "Carlton", "ZenMaster")

    def test_abbreviated(self):
        assert split_name("P 'Sunrise' B") == ("P", "B", "Sunrise")

    def test_single_word(self):
        assert split_name("Alice") == ("Alice", "", "")

    def test_multi_word_surname(self):
        assert split_name("Ana Maria Bartkowska") == (
            "Ana", "Maria Bartkowska", "")


class TestNamesAgree:
    def test_diminutives(self):
        assert first_names_agree("Don", "Donald")
        assert first_names_agree("Pip", "Philippa")
        assert first_names_agree("Lav", "Lavanya")
        assert first_names_agree("Edd", "Edward")

    def test_slash_alternates(self):
        assert first_names_agree("James/Jimmy", "Jimmy")

    def test_a_shared_first_letter_is_not_enough(self):
        # Aylon and Amir Stennett are two people. So are John and James E.
        assert not first_names_agree("Aylon", "Amir")
        assert not first_names_agree("John", "James")

    def test_surname_initial_stands_in_for_the_full_name(self):
        assert surnames_agree("Z", "Zander")
        assert not surnames_agree("Zander", "Yates")

    def test_initials_stand_in_for_a_multi_word_surname(self):
        # "Marc MC" in the chat is Marc Mercer Cabrera on the roster, and the
        # MC row is the one holding his messages and his portrait.
        assert surnames_agree("MC", "Mercer Cabrera")
        assert surnames_agree("Mercer Cabrera", "MC")

    def test_initials_do_not_match_a_single_word_surname(self):
        # Otherwise every two-letter surname matches anything starting with it.
        assert not surnames_agree("MC", "Mccarthy")
        assert not surnames_agree("MC", "Martin")


class TestMergeVariants:
    def test_two_addresses_for_one_person_become_one_record(self):
        a = person(appearance(2019, "Jess", "Lang", "jessh@example.com"))
        b = person(appearance(2022, "Jess", "Lang", "jess.h@example.com"))
        out = merge_variants([a, b])
        assert len(out) == 1
        assert {x.year for x in out[0].appearances} == {2019, 2022}
        assert "different addresses" in " ".join(out[0].notes)

    def test_abbreviation_joins_its_full_name(self):
        full = person(appearance(2019, "Nicolas", "Zander", "n@z.com"))
        abbr = person(appearance(2017, "Nic", "Z"))
        out = merge_variants([full, abbr])
        assert len(out) == 1
        assert person_name(out[0]) == ("Nicolas", "Zander")

    def test_a_first_name_only_row_joins_the_person_it_belongs_to(self):
        # The sheets record "Giordano" one year and "Gio Salvi" another.
        # Demanding two surnames match meant these never merged.
        bare = person(appearance(2016, "Giordano", ""))
        full = person(appearance(2017, "Gio", "Salvi", "g@x.com"))
        out = merge_variants([bare, full])
        assert len(out) == 1
        assert person_name(out[0]) == ("Giordano", "Salvi")

    def test_a_cluster_of_agreeing_rows_merges_as_a_group(self):
        # The sheets carry "Simo", "Simo" and "Simone" for one person.
        # Requiring exactly one partner meant every row had two and none of
        # them merged.
        rows = [person(appearance(2016, "Simo", "")),
                person(appearance(2017, "Simo", "")),
                person(appearance(2019, "Simone", ""))]
        out = merge_variants(rows)
        assert len(out) == 1
        assert person_name(out[0])[0] == "Simone"
        assert {a.year for a in out[0].appearances} == {2016, 2017, 2019}

    def test_a_cluster_that_is_not_a_clique_stays_apart(self):
        # Anna M fits both, but Morrow and Moss disagree with each other, so
        # the component is incomplete and nothing merges.
        rows = [person(appearance(2019, "Anna", "M", "rose@x.com")),
                person(appearance(2022, "Anna", "Morrow", "am@x.com")),
                person(appearance(2022, "Anna", "Moss", "amok@x.com"))]
        assert len(merge_variants(rows)) == 3

    def test_a_punctuation_only_token_is_not_a_name(self):
        # WhatsApp exports "~ Laszlo" with the tilde split off as its own
        # token; reading it as the given name is why the row holding his 135
        # messages never matched him.
        assert split_name("~ Laszlo") == ("Laszlo", "", "")
        assert split_name("~ Chloe Kassis-Crowe") == ("Chloe", "Kassis-Crowe", "")

    def test_punctuation_attached_to_a_name_is_left_alone(self):
        # "~~G~" is one token with a letter in it. It stays whole, and norm()
        # is what makes it comparable.
        assert split_name("~~G~")[0] == "~~G~"
        assert norm("~~G~") == "g"

    def test_a_first_name_that_fits_two_people_still_refuses(self):
        bare = person(appearance(2016, "Sam", ""))
        a = person(appearance(2017, "Sam", "Bergman", "sb@x.com"))
        b = person(appearance(2017, "Sam", "Whitney", "sw@x.com"))
        assert len(merge_variants([bare, a, b])) == 3

    def test_an_ambiguous_name_is_refused_not_guessed(self):
        # "Anna M" fits both Morrow and Moss. A name that identifies two people
        # identifies neither.
        morrow = person(appearance(2022, "Anna", "Morrow", "am@x.com"))
        moss = person(appearance(2022, "Anna", "Moss", "amok@x.com"))
        ambiguous = person(appearance(2019, "Anna", "M", "rose@x.com"))
        out = merge_variants([morrow, moss, ambiguous])
        assert len(out) == 3
        assert any("ambiguous" in " ".join(p.notes) for p in out)

    def test_result_does_not_depend_on_input_order(self):
        # Merging incrementally let whichever record came first absorb "Joe H".
        hanley = person(appearance(2019, "Jo", "Hanley", "joh@x.com"))
        horvath = person(appearance(2019, "Joseph", "Horvath", "jhor@x.com"))
        joe = person(appearance(2017, "Joe", "H"))
        forward = len(merge_variants([hanley, horvath, joe]))
        backward = len(merge_variants([joe, horvath, hanley]))
        assert forward == backward == 3

    def test_distinct_people_sharing_a_surname_stay_distinct(self):
        aylon = person(appearance(2022, "Aylon", "Stennett", "a@x.com"))
        amir = person(appearance(2023, "Amir", "Stennett", "amir@x.com"))
        assert len(merge_variants([aylon, amir])) == 2


class TestCityBreaksTies:
    def test_a_first_name_in_a_crowd_is_settled_by_where_they_live(self):
        # Two rows reading just "Ana", both from SF, beside Ana Bartkowska of
        # San Francisco, Ana Luna of Zihuatanejo and Ana Stancuic of
        # Barcelona. A name that fits three people, a city that fits one.
        bare = person(appearance(2016, "Ana", "", city="SF"))
        bz = person(appearance(2019, "Ana", "Bartkowska", "b@x.com",
                               city="San Francisco"))
        lo = person(appearance(2023, "Ana", "Luna", "l@x.com",
                               city="Zihuatanejo"))
        out = merge_variants([bare, bz, lo])
        assert len(out) == 2
        merged = [p for p in out if person_name(p)[1] == "Bartkowska"][0]
        assert {a.year for a in merged.appearances} == {2016, 2019}

    def test_it_refuses_when_the_city_fits_more_than_one(self):
        bare = person(appearance(2019, "Anna", "", city="SF"))
        a = person(appearance(2022, "Anna", "Morrow", "m@x.com", city="SF"))
        b = person(appearance(2017, "Anna", "Moss", "k@x.com",
                              city="San Francisco"))
        assert len(merge_variants([bare, a, b])) == 3


class TestSamePersonFile:
    def test_a_hand_recorded_group_merges_what_no_rule_can(self):
        a = person(appearance(2019, "Anna", "M", "rose@x.com", city="SF"))
        b = person(appearance(2022, "Anna", "Morrow", "m@x.com", city="SF"))
        c = person(appearance(2017, "Anna", "Moss", "k@x.com", city="SF"))
        out = apply_same_person([a, b, c], [{"anna m", "anna morrow"}])
        assert len(out) == 2
        merged = [p for p in out if person_name(p)[1] == "Morrow"][0]
        assert {x.year for x in merged.appearances} == {2019, 2022}
        assert any("by hand" in n for n in merged.notes)

    def test_a_group_naming_one_known_person_changes_nothing(self):
        a = person(appearance(2019, "Anna", "Morrow", "m@x.com"))
        assert len(apply_same_person([a], [{"anna morrow", "someone else"}])) == 1


class TestPersonName:
    def test_longest_spelling_wins_not_the_most_recent(self):
        # 2017 abbreviates, so recency would make "David D." canonical.
        p = person(appearance(2016, "David", "DiMarco"),
                   appearance(2017, "David", "D."))
        assert person_name(p) == ("David", "DiMarco")
