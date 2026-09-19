"""
The gate checks the artifact, not the function — but the gate itself has to be
able to see what it is refusing. It called the preview database clean while 64
evidence quotes carried campmates' phone numbers, because its phone pattern
demanded an explicit personal label ("cell phone", "phone #") and the quotes
read "Phone: +15555550100." and "Melissa - 415-555-0137".

These tests hand the gate a database containing exactly those two forms and
require it to refuse — and hand it the camp's own vendor lines and secrets and
require it to pass. If redaction's notion of "personal" widens again, widen
the gate first and watch it fail here.

The gate is also structural since 2026-08-11: general_knowledge shipped absent
for weeks because writing it was a manual step nothing invoked, and the app
degraded silently. So make_db builds a structurally complete artifact by
default, and the structural tests remove pieces one at a time.
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from check_shipped_db import main


def make_db(tmp_path, quotes=(), lore_rows=(),
            with_general=True, with_lore=True, with_ask_word=True):
    """A minimal artifact the structural gate accepts, unless told otherwise.

    lore_rows is [(title, story), ...]; quotes go to evidence as before.
    """
    path = tmp_path / "shipped.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE evidence (id INTEGER PRIMARY KEY, quote TEXT)")
    conn.executemany("INSERT INTO evidence (quote) VALUES (?)",
                     [(q,) for q in quotes])
    if with_general:
        conn.execute("CREATE TABLE general_knowledge ("
                     "id INTEGER PRIMARY KEY, topic TEXT, fact TEXT, source TEXT)")
        conn.execute("INSERT INTO general_knowledge (topic, fact, source) "
                     "VALUES ('water', 'Bring all the water you will drink.', "
                     "'Burning Man Survival Guide')")
    if with_lore:
        conn.execute("CREATE TABLE lore (id INTEGER PRIMARY KEY, title TEXT, "
                     "story TEXT, year INTEGER, people TEXT)")
        conn.executemany(
            "INSERT INTO lore (title, story) VALUES (?, ?)", list(lore_rows))
    if with_ask_word:
        conn.execute("CREATE TABLE ask_word ("
                     "claim_table TEXT, claim_id INTEGER, word TEXT)")
    conn.commit()
    conn.close()
    return str(path)


class TestRefusesPersonalPhones:
    def test_a_bare_phone_label(self, tmp_path):
        # The form the sign-up roster quotes take: label, number, full stop.
        assert main(make_db(tmp_path, ["Phone: +15555550100."])) == 1

    def test_a_name_dash_number(self, tmp_path):
        # The form a driver sheet takes for someone the roster does not know.
        assert main(make_db(tmp_path, ["Melissa - 415-555-0137"])) == 1

    def test_a_labelled_number_still_refused(self, tmp_path):
        # The case the gate always caught must keep failing.
        assert main(make_db(tmp_path, ["cell phone: 415-555-1234"])) == 1


class TestShipsTheCampsOwn:
    def test_vendor_lines_and_secrets_pass(self, tmp_path):
        assert main(make_db(tmp_path, [
            # A business printing its own numbers.
            "Or telephone: (770) 497-6400",
            "4imprint, Oshkosh WI 54901 Toll Free: 877-446-7746",
            "Fax: (916) 446-5280",
            # A store recommendation: name before number, no dash.
            "Occasionally has treasure\nBedford Galleries Inc\n(718) 230-1298",
            # Camp operational secrets deliberately stay.
            "the gate code is 4242 and the padlock combo is 30-16-2",
            # The camp's own places, in prose, outside any shipping label.
            "the Monument warehouse at 140 9th St, then the Fernley dump",
        ])) == 0


class TestScansTheEnrichmentLayer:
    """lore stories are model prose retold from chat — text like any other,
    which is exactly how 299 contact details once reached camp_fact while
    camp_knowledge was carefully cleaned. The gate's whole-database scan must
    cover the new tables the same as the old."""

    def test_a_card_number_in_a_lore_story_is_refused(self, tmp_path):
        assert main(make_db(tmp_path, lore_rows=[
            ("The year of the barrel invoice",
             "Someone pasted the receipt whole, card 4111 1111 1111 1111 "
             "and all, and the chat never let them forget it."),
        ])) == 1

    def test_a_label_block_reproduced_in_a_story_is_refused(self, tmp_path):
        # A story that retells a receipt closely enough to reproduce its
        # shipping block. Addresses in lore are checked inside label windows
        # now that lore is in ADDRESS_TABLES.
        assert main(make_db(tmp_path, lore_rows=[
            ("The misdelivered generator",
             "The label read Shipping Address: Madeline Gale 1272 RHODE "
             "ISLAND ST UNIT 19 SAN FRANCISCO, CA 94107 and the generator "
             "still went to Gerlach."),
        ])) == 1

    def test_camp_addresses_in_a_story_pass(self, tmp_path):
        # The deliberate scope: the camp's own places in plain prose ship,
        # in a story exactly as in the manual.
        assert main(make_db(tmp_path, lore_rows=[
            ("The great decom shuffle",
             "Everything went from the Monument warehouse at 140 9th St to "
             "the Fernley dump, and the rest to the Tahoe decom house."),
        ])) == 0


class TestStructuralGate:
    """A missing layer is silent on the phone — the Swift consumers degrade
    to the old behaviour by design — so the artifact is where it has to be
    loud."""

    def test_absent_general_knowledge_fails(self, tmp_path):
        assert main(make_db(tmp_path, with_general=False)) == 1

    def test_empty_general_knowledge_fails(self, tmp_path):
        path = make_db(tmp_path)
        conn = sqlite3.connect(path)
        conn.execute("DELETE FROM general_knowledge")
        conn.commit()
        conn.close()
        assert main(path) == 1

    def test_absent_lore_fails(self, tmp_path):
        assert main(make_db(tmp_path, with_lore=False)) == 1

    def test_absent_ask_word_fails(self, tmp_path):
        assert main(make_db(tmp_path, with_ask_word=False)) == 1

    def test_empty_lore_and_ask_word_pass(self, tmp_path):
        # The stages may simply not have run yet. Present-but-empty is a
        # state the app handles honestly; absent is the schema never arriving.
        assert main(make_db(tmp_path)) == 0
