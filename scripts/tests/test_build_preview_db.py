"""
The device database carries a third of its documents as order confirmations,
and those arrive with the buyer's home address and card attached.

Deleting them is the obvious move and the wrong one — "Med kit supplies" is
the only document in the corpus containing the word "tourniquet", so dropping
it takes a medical question's answer with it.

Matching addresses anywhere is the other obvious move and is also wrong. The
first version of this did that and ate the camp's own addresses: the storage
unit on Greg St, the hardware store on S Rock Blvd. Those are the addresses
someone driving to Gerlach needs.

What separates the two is not the address, it is where it sits. These tests
pin that line.
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from build_preview_db import main, redact, set_roster
from enrich_schema import create_enrichment_tables

RECEIPT = ("Condition: New\nShipping Address:\nMadeline Gale\n"
           "1272 RHODE ISLAND ST UNIT 19\nSAN FRANCISCO, CA 94107-4405\n"
           "United States\nShipping Speed:\nStandard Shipping\n")


class TestRemovesPersonalData:
    def test_labelled_street_address(self):
        out = redact(RECEIPT)
        assert "RHODE ISLAND" not in out
        assert "1272" not in out
        # The name is roster data the camp already holds; only the address goes.
        assert "Madeline Gale" in out

    def test_labelled_city_state_zip(self):
        assert "94107" not in redact(RECEIPT)

    def test_address_repeated_at_a_page_break_without_its_label(self):
        # The PDF extraction drops "Shipping Address:" at page breaks but never
        # the "Shipping Speed:" that follows, so that closes the block instead.
        text = ("orderID=111-2222222-3333333 4/4\n| Madeline Gale "
                "1272 RHODE ISLAND ST UNIT 19 SAN FRANCISCO, CA 94107-4405 "
                "United States Shipping Speed: Standard")
        out = redact(text)
        assert "RHODE ISLAND" not in out
        assert "94107" not in out

    def test_card_last_four(self):
        out = redact("Visa | Last digits: 2775")
        assert "2775" not in out and "****" in out

    def test_order_number(self):
        assert "111-7249140-5066635" not in redact(
            "Amazon.com order number: 111-7249140-5066635")

    def test_bank_details_anywhere(self):
        # Pasted into the group chat once. Worse on a lost phone than an address.
        out = redact("Routing Number: 123456789 Account Number: 987654321")
        assert "123456789" not in out
        assert "987654321" not in out

    def test_full_card_number(self):
        assert "4111" not in redact("Card 4111 1111 1111 1111 charged")


class TestKeepsWhatAnswersQuestions:
    def test_camp_addresses_in_prose_survive(self):
        # The manual. Someone is driving to Gerlach and needs these.
        text = ("Storage unit is at Northwest Self Storage, 1056 Greg St, "
                "Sparks, NV 89431. Hardware is 470 S Rock Blvd, Reno, NV 89523.")
        assert redact(text) == text

    def test_item_list_survives(self):
        text = ("1 of: Military Tourniquet Combat Outdoors Emergency $11.96 "
                "Tourniquet Military First Aid Equipment")
        assert redact(text) == text

    def test_recipe_step_survives(self):
        # "Place" is a street suffix; a numbered step is not an address.
        text = "1\nHeat a skillet to medium-high heat. Place the bread down."
        assert redact(text) == text

    def test_street_word_inside_another_word_is_left_alone(self):
        # "LIST" ends in "ST"; 6 recipe documents depend on the word boundary.
        text = "Shipping Address: 65 TOTAL INGREDIENT LIST"
        assert "INGREDIENT LIST" in redact(text)

    def test_playa_address_survives(self):
        # Camp addresses are clock positions, and are exactly what is needed.
        text = "We are at 7:59 & C this year, third ring from the Man."
        assert redact(text) == text

    def test_gate_code_survives(self):
        # Operational secrets are the point of the app; personal data is not.
        text = "We use the code 8765 to open the padlock on Doris."
        assert redact(text) == text


class TestEdges:
    def test_none_passes_through(self):
        assert redact(None) is None

    def test_empty_passes_through(self):
        assert redact("") == ""

    def test_no_label_means_no_address_pass(self):
        text = "Meet at 202 Example Drive, the house with the hot tub."
        assert redact(text) == text


class TestRemovesContactDetails:
    """A contact goes when something says it belongs to a person.

    Not when it merely looks like a phone number. The corpus has the camp's
    suppliers in it — the Honda dealer, 4imprint's toll-free line — and those
    are the numbers somebody in the desert actually needs.
    """

    def setup_method(self):
        set_roster(["Juli Pennock", "Piotr Bartkowski", "Martina"])

    def test_consumer_email_anywhere(self):
        assert "@" not in redact("mail piotr@example.com whenever")

    def test_email_beside_a_roster_name(self):
        out = redact("Contact details for Piotr Bartkowski: chandra@example.org")
        assert "example.org" not in out

    def test_phone_beside_a_roster_name(self):
        assert "555" not in redact("We contact Juli at 415-555-1234")

    def test_phone_under_a_personal_label(self):
        assert "555" not in redact("Zip: 94608 Primary Phone#: 415-555-1234")

    def test_bare_ten_digits_in_a_roster_line(self):
        out = redact("contact numbers for our campmates: Martina (6265550183)")
        assert "6265550183" not in out

    def test_a_bare_phone_label(self):
        # The evidence quotes built from the sign-up roster read
        # "Phone: <number>." with no name anywhere near — the name sits in a
        # different column. The label alone says whose shape this is.
        assert "5555550100" not in redact("Phone: +15555550100.")

    def test_numbers_not_shaped_like_san_francisco(self):
        # The sign-up roster is international. The old shape demanded US
        # 3-3-4 grouping with a [2-9] area code and called all of these clean.
        for number in ("1-443-555-0161", "+52 5555 019876", "07400123456",
                       "+31612345678", "13235550147", "001.650.555.0192"):
            out = redact(f"Phone: {number}.")
            assert not any(c.isdigit() for c in out), (number, out)

    def test_a_phone_beside_a_roster_name_in_any_shape(self):
        assert "314" not in redact("Juli Pennock [email removed] 1.314.555.0174")

    def test_a_name_dash_number(self):
        # A driver sheet lists people the roster has never heard of.
        # "Melissa" is not in the roster set up above, and the dash between a
        # name and a number is how a person writes their own.
        out = redact("Melissa - 415-555-0137")
        assert "290-7422" not in out

    def test_the_prose_survives(self):
        out = redact("We contact Juli at 415-555-1234 with any questions.")
        assert out.startswith("We contact Juli at")
        assert out.endswith("with any questions.")


class TestKeepsTheCampsOwnContacts:
    """The mistake the address rule already made once, in a second domain."""

    def setup_method(self):
        set_roster(["Juli Pennock", "Piotr Bartkowski"])

    def test_a_suppliers_toll_free_number(self):
        text = "4imprint, Oshkosh WI 54901 Toll Free: 877-446-7746"
        assert "877-446-7746" in redact(text)

    def test_a_dealers_line_in_the_manual(self):
        assert "888-888-3139" in redact("through Helm Inc. Telephone: 888-888-3139")

    def test_a_vendor_email(self):
        assert "service@bannersonthecheap.com" in redact(
            "Banners On The Cheap <service@bannersonthecheap.com>")

    def test_a_business_name_before_its_number_without_a_dash(self):
        # A store recommendation pasted into the chat. The name-dash rule must
        # not widen into "any capitalized word near a number", which is the
        # shape-only matching that ate the suppliers once already.
        text = "Occasionally has treasure\nBedford Galleries Inc\n(718) 230-1298"
        assert "230-1298" in redact(text)

    def test_telephone_is_not_a_bare_phone_label(self):
        # "Telephone:" is how a business prints its own; the bare-label rule
        # must not match the tail of the longer word.
        assert "497-6400" in redact("Or telephone: (770) 497-6400")

    def test_a_bare_phone_label_inside_a_business_block_ships(self):
        # The landlord of the lot the camp rents, in the manual. "Phone:" on
        # its own used to be enough to call this personal.
        set_roster(["Juli Pennock"])
        text = ("Black Rock Rentals (fka Burn Pit Storage)\nContact details:\n"
                "HWY 447 MM70 Empire, NV 89405\nPhone: Kieth: +1-775-555-0142")
        assert "775-555-0142" in redact(text)

    def test_a_bare_phone_label_with_nothing_around_it_is_still_personal(self):
        # The other half of the same label: the sign-up roster writes exactly
        # this, with the member's name in a column the quote does not carry.
        # Ten of these are in evidence.quote. Removing the bare label outright
        # exposed all ten, and the shipped-db gate caught it.
        set_roster(["Juli Pennock"])
        assert "15555550100" not in redact("Phone: +15555550100.")

    def test_a_one_letter_roster_name_does_not_make_prose_personal(self):
        # The roster has a member whose last_name is the single letter "S",
        # and two more at "T" and "C". They went into the roster whole, and
        # `\bs\b` matches the apostrophe-s in "Keith's", "It's", "Curtis'" --
        # so almost every number in every document sat next to a "roster name"
        # and was redacted. Keith runs the storage lot the camp rents, and the
        # shipped manual read "Keith's number is [phone removed]".
        set_roster(["Juli Pennock", "S", "T", "C"])
        text = ("Tammy sold the lot to her brother, Keith. Sunny is Curtis' "
                "stepmom. It's a family affair.\n  * Keith's number is "
                "775-555-0142.")
        assert "775-555-0142" in redact(text), \
            "a one-character roster entry redacted the camp's own landlord"

    def test_two_letter_names_still_redact(self):
        # The fix must not go the other way. Oz, BJ, Jo, MC, KC and Lu are
        # real campmates whose names are two characters, and they reach the
        # roster as whole entries -- `person.name` is "Oz" on its own -- so a
        # contact sitting beside one is still personal. One character is junk;
        # two is a name.
        set_roster(["Oz", "BJ", "Jo"])
        assert "555-0100" not in redact("Oz 415-555-0100")
        assert "555-0101" not in redact("BJ 415-555-0101")

    def test_a_two_letter_first_name_inside_a_full_name_is_a_known_gap(self):
        # Documents the limit rather than asserting a fix. The word-parts loop
        # requires len > 2, so "Oz Sezer" contributes "oz sezer" and "sezer"
        # but never "oz" -- a number after a bare "Oz" is caught only because
        # `person.name` carries "Oz" whole, which it does. If the person table
        # ever stops doing that, this test starts passing and the protection
        # is gone silently.
        set_roster(["Oz Sezer"])
        assert "555-0100" in redact("Oz 415-555-0100"), \
            "if this fails, the parts loop began covering 2-char names and " \
            "this test can be deleted"

class TestKeepsThingsShapedLikeContacts:
    """The first version of the address rule ate a recipe. These pin the
    equivalent line for phone numbers."""

    def setup_method(self):
        set_roster(["Juli Pennock"])

    def test_a_date_is_not_a_phone_number(self):
        assert "2026-08-28" in redact("gates open 2026-08-28 at dawn")

    def test_a_zip_plus_four_is_not_a_phone_number(self):
        # 5-4 digits, not 3-3-4. It is handled by the address rule, in a
        # labelled window, and must not be eaten out of prose by this one.
        assert "94107-4405" in redact("the depot ZIP is 94107-4405")

    def test_a_whatsapp_mention_is_not_an_email(self):
        assert "@marcus" in redact("ask @marcus about the generator")

    def test_a_time_is_left_alone(self):
        assert "19:04" in redact("dinner at 19:04 sharp")

    def test_bare_ten_digits_with_nobody_attached(self):
        # Ten digits and no name near them is a part number as often as a
        # phone. Redacting on shape alone is what ate the camp's suppliers.
        assert "6265550183" in redact("item 6265550183 shipped separately")

    def test_an_epoch_timestamp_is_not_a_phone_number(self):
        # Ten digits and phone-shaped now that the shape is international.
        # What keeps it is that nobody is standing next to it.
        assert "1786415000" in redact("stamped at 1786415000 in the log")

    def test_a_ticket_link_survives_a_mention(self):
        # A digit run inside a URL is an identifier, not a number someone can
        # be reached on — even with a campmate mentioned just before it.
        text = ("ask Juli about it https://www.eventbrite.com/e/"
                "demon-princess-tickets-912345678901 and the app "
                "https://apps.apple.com/us/app/id1554374905")
        assert redact(text) == text

    def test_a_number_that_ends_a_sentence(self):
        # The trailing guard used to reject the full stop, so every
        # "...by phone on +15555550100." survived untouched.
        out = redact("Contact details for Piotr Bartkowski: reach them on "
                     "+15555550100.")
        assert "5555550100" not in out
        assert out.endswith(".")

    def test_a_consumer_provider_on_any_tld(self):
        # The roster has hotmail.co.uk, yahoo.gr and a typo'd gmail.con.
        for address in ("someone@hotmail.co.uk", "d@yahoo.gr", "n@gmail.con"):
            assert "@" not in redact(f"write to {address} about it")

    def test_an_address_broken_by_pdf_extraction(self):
        # "PARKER.CHENOWETH@GMAIL. COM" is one address; the plain pattern sees
        # no TLD and left it standing in a permit application.
        out = redact("Primary Email: PARKER.CHENOWETH@GMAIL. COM Application")
        assert "CHENOWETH" not in out

    def test_a_campmate_at_their_own_domain(self):
        set_roster(["Chandra Khoury"])
        assert "example.org" not in redact("write to chandra@example.org")

    def test_the_next_sentence_is_not_eaten(self):
        # The broken-TLD branch must not swallow the word after a full stop.
        out = redact("mail ops@vendor.co. Company policy applies.")
        assert "Company policy applies." in out


# --------------------------------------------------------------------------
# main() — the whole build, against a tiny source. Until 2026-08-11 only
# redact() had tests, and that gap is precisely how a preview with no
# general_knowledge table shipped: the function was correct, the build that
# was supposed to call a second script never did, and nothing here noticed.
# --------------------------------------------------------------------------

# A story that retells a receipt closely enough to reproduce its shipping
# block. Lore is model prose derived from chat; the block must not survive
# the trip into the preview.
PLANTED_STORY = ("The generator order went to the wrong coast. The label "
                 "read Shipping Address: Madeline Gale 1272 RHODE ISLAND ST "
                 "UNIT 19 SAN FRANCISCO, CA 94107-4405 United States, and "
                 "the driver would not take it back.")


def make_source(tmp_path) -> Path:
    """A miniature ps_knowledge.db: two chat messages, one document, and one
    row in each of the layers this build must carry — lore (with a planted
    address), camp_fact, its ask_word, and an entity whose alias table is
    regressed to the shape it had before the `source` column existed, which
    is the shape that made the positional copy die with a column-count
    error until main() learned to migrate the source first."""
    src = tmp_path / "src.db"
    conn = sqlite3.connect(src)
    conn.executescript("""
        CREATE TABLE person (
            id INTEGER PRIMARY KEY, name TEXT, email TEXT, phone TEXT,
            message_count INTEGER, first_seen TEXT, last_seen TEXT);
        CREATE TABLE person_content (
            id INTEGER PRIMARY KEY, person_id INTEGER, content TEXT, source TEXT,
            content_type TEXT, timestamp TEXT, media_ref TEXT);
        CREATE TABLE camp_knowledge (
            id INTEGER PRIMARY KEY, title TEXT, content TEXT, source_file TEXT,
            category TEXT, year INTEGER);
        INSERT INTO person VALUES (1, 'Piotr', NULL, NULL, 2, NULL, NULL);
        INSERT INTO person_content VALUES
            (10, 1, 'the generator went to the wrong coast!!', 'chat',
             'sent', '2023-08-30', NULL),
            (11, 1, 'still cannot believe the generator story', 'chat',
             'sent', '2023-09-02', NULL);
        INSERT INTO camp_knowledge VALUES
            (1, 'Med kit supplies', 'The tourniquet lives in Doris.', NULL,
             'manual', 2023);
    """)
    create_enrichment_tables(conn)
    conn.execute("INSERT INTO lore (id, title, story, year, people) "
                 "VALUES (1, 'The wrong-coast generator', ?, 2023, 'Piotr')",
                 (PLANTED_STORY,))
    conn.execute("INSERT INTO camp_fact (id, topic, fact, category, year) "
                 "VALUES (1, 'safety', 'The tourniquet lives in Doris.', "
                 "'where', 2023)")
    conn.execute("INSERT INTO ask_word (claim_table, claim_id, word) "
                 "VALUES ('camp_fact', 1, 'medkit')")
    conn.execute("INSERT INTO entity (id, name, kind, summary) "
                 "VALUES (1, 'Doris', 'vehicle', 'The box truck.')")
    conn.executemany(
        "INSERT INTO evidence (claim_table, claim_id, source_table, "
        "source_id, quote) VALUES (?,?,?,?,?)",
        [("lore", 1, "person_content", 10,
          "the generator went to the wrong coast!!"),
         ("lore", 1, "person_content", 11,
          "still cannot believe the generator story"),
         ("camp_fact", 1, "camp_knowledge", 1,
          "The tourniquet lives in Doris."),
         ("entity", 1, "person_content", 10,
          "the generator went to the wrong coast!!")])
    # Regress entity_alias to its pre-`source` shape, one row and all — the
    # state every real source built before 2026-08-11 is in.
    conn.execute("DROP TABLE entity_alias")
    conn.execute("CREATE TABLE entity_alias ("
                 "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                 "entity_id INTEGER NOT NULL, alias TEXT NOT NULL, "
                 "UNIQUE (entity_id, alias))")
    conn.execute("INSERT INTO entity_alias (entity_id, alias) "
                 "VALUES (1, 'the truck')")
    conn.commit()
    conn.close()
    return src


class TestMain:
    def build(self, tmp_path) -> sqlite3.Connection:
        out = tmp_path / "preview.db"
        main(["--src", str(make_source(tmp_path)), "--out", str(out)])
        return sqlite3.connect(out)

    def test_general_knowledge_is_written_by_the_build_itself(self, tmp_path):
        # The shipped bug: bm_basics was a manual step, every rebuild dropped
        # the background layer, and searchGeneral returned [] on device.
        conn = self.build(tmp_path)
        assert conn.execute(
            "SELECT COUNT(*) FROM general_knowledge").fetchone()[0] > 0

    def test_lore_and_ask_word_ship(self, tmp_path):
        conn = self.build(tmp_path)
        assert conn.execute("SELECT COUNT(*) FROM lore").fetchone()[0] == 1
        assert conn.execute(
            "SELECT claim_table, claim_id, word FROM ask_word").fetchall() \
            == [("camp_fact", 1, "medkit")]

    def test_a_planted_address_in_a_lore_story_is_redacted(self, tmp_path):
        conn = self.build(tmp_path)
        story = conn.execute("SELECT story FROM lore").fetchone()[0]
        assert "RHODE ISLAND" not in story
        assert "94107" not in story
        # The story survives as a story; only the label block goes.
        assert "wrong coast" in story

    def test_an_old_source_without_the_alias_column_still_copies(self, tmp_path):
        # Positional INSERT: three source columns into a four-column preview
        # table was a column-count error until the build migrated the source.
        conn = self.build(tmp_path)
        rows = conn.execute(
            "SELECT alias, source FROM entity_alias").fetchall()
        assert rows == [("the truck", None)]
