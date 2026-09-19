"""
The roster in the knowledge database.

Two invariants matter here. Every fact Lucy can retrieve must cite a row and a
quote lifted verbatim from it — that is the spine, and a roster fact is not
exempt just because it was generated from a table rather than extracted from
prose. And nothing that reaches the device may carry an email or a phone
number, which is the whole reason the roster keeps them in a separate column
from `record`.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from build_people import COLUMNS, install, render_record, roster_facts
from enrich_schema import create_enrichment_tables

PEOPLE = [
    {"name": "Piotr Bartkowski", "first_name": "Piotr",
     "last_name": "Bartkowski", "nickname": "Sunrise",
     "email": "piotr@example.com", "phone": "+15555550100",
     "home_city": "London", "years_attended": "2016,2019",
     "years_listed": "2016,2017,2019", "year_count": 2, "first_year": 2016,
     "last_year": 2019, "statuses": "2016=attending", "match_notes": ""},
    {"name": "Anna Morrow", "first_name": "Anna", "last_name": "Morrow",
     "nickname": "", "email": "anna@x.com", "phone": "", "home_city": "SF",
     "years_attended": "2019", "years_listed": "2019", "year_count": 1,
     "first_year": 2019, "last_year": 2019, "statuses": "2019=attending",
     "match_notes": ""},
    {"name": "Never Came", "first_name": "Never", "last_name": "Came",
     "nickname": "", "email": "", "phone": "", "home_city": "",
     "years_attended": "", "years_listed": "2017", "year_count": 0,
     "first_year": 2017, "last_year": 2017, "statuses": "2017=maybe",
     "match_notes": ""},
]


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "k.db"
    conn = sqlite3.connect(path)
    # The corpus tables the evidence trigger checks against, plus `person`:
    # the roster links itself to the chat corpus on install, and personality
    # rows reference it.
    conn.executescript("""
        CREATE TABLE person_content (id INTEGER PRIMARY KEY, content TEXT);
        CREATE TABLE camp_knowledge (id INTEGER PRIMARY KEY, content TEXT);
        CREATE TABLE person (id INTEGER PRIMARY KEY, name TEXT,
                             message_count INTEGER);
        INSERT INTO person (id, name, message_count)
        VALUES (1, 'Piotr', 40), (2, 'Piotr ''Sunrise''', 3),
               (3, 'Anna Morrow', 25);
    """)
    create_enrichment_tables(conn)
    conn.commit()
    conn.close()
    return path


class TestRenderRecord:
    def test_contact_details_are_in_the_record(self):
        # They have to be: evidence quotes must be verbatim substrings of the
        # record, so a contact fact can only cite one if the record carries it.
        r = render_record(PEOPLE[0])
        assert "Email: piotr@example.com." in r
        assert "Phone: +15555550100." in r

    def test_record_carries_what_the_facts_will_quote(self):
        r = render_record(PEOPLE[0])
        assert "Known as Sunrise." in r
        assert "Said yes in 2016, 2019." in r
        assert "Signed up in 2016, 2017, 2019." in r


class TestRosterFacts:
    def test_every_quote_is_verbatim_in_its_record(self):
        members = [(i, p, render_record(p)) for i, p in enumerate(PEOPLE, 1)]
        records = {i: rec for i, _, rec in members}
        for f in roster_facts(members):
            for source_id, quote in f["cites"]:
                assert quote in records[source_id], (f["fact"], quote)

    def test_someone_who_never_said_yes_gets_no_attendance_fact(self):
        members = [(i, p, render_record(p)) for i, p in enumerate(PEOPLE, 1)]
        facts = [f["fact"] for f in roster_facts(members)]
        assert not any(f.startswith("Never Came signed up") for f in facts)

    def test_the_fact_claims_no_more_than_its_evidence_says(self):
        # A signup sheet records an answer, not an arrival. "has camped with
        # us" was asserted from a quote reading "Said yes in 2016, 2017" — a
        # claim its own receipt does not support.
        members = [(i, p, render_record(p)) for i, p in enumerate(PEOPLE, 1)]
        attendance = [f for f in roster_facts(members)
                      if f["fact"].startswith("Piotr")
                      and "2016" in f["fact"]][0]
        assert "signed up and said yes" in attendance["fact"]
        assert "has camped with us" not in attendance["fact"]

    def test_the_count_cites_every_row_it_counted(self):
        members = [(i, p, render_record(p)) for i, p in enumerate(PEOPLE, 1)]
        counts = [f for f in roster_facts(members)
                  if "people said yes to coming in 2019" in f["fact"]]
        assert len(counts) == 1
        # Piotr and Anna, not one convenient row standing in for both.
        assert len(counts[0]["cites"]) == 2
        assert counts[0]["fact"].startswith("2 people")

    def test_the_count_says_what_it_is_and_is_not(self):
        members = [(i, p, render_record(p)) for i, p in enumerate(PEOPLE, 1)]
        count = next(f for f in roster_facts(members) if "people said yes" in f["fact"])
        # Without "people" and "coming" the count ties with any one camper's
        # attendance fact and loses the tie arbitrarily.
        assert "people" in count["fact"] and "coming" in count["fact"]
        assert "not a headcount" in count["fact"]


class TestInstall:
    def test_writes_members_facts_and_evidence(self, db):
        n_facts, n_evidence = install(db, PEOPLE)
        conn = sqlite3.connect(db)
        assert conn.execute("SELECT COUNT(*) FROM camp_member").fetchone()[0] == 3
        assert n_facts > 0 and n_evidence > 0
        assert conn.execute(
            "SELECT COUNT(*) FROM camp_fact WHERE topic='roster'"
        ).fetchone()[0] == n_facts

    def test_rerunning_replaces_rather_than_accumulates(self, db):
        install(db, PEOPLE)
        first = sqlite3.connect(db).execute(
            "SELECT COUNT(*) FROM camp_fact").fetchone()[0]
        install(db, PEOPLE)
        second = sqlite3.connect(db).execute(
            "SELECT COUNT(*) FROM camp_fact").fetchone()[0]
        assert first == second

    def test_every_stored_quote_is_verbatim_in_its_stored_record(self, db):
        install(db, PEOPLE)
        conn = sqlite3.connect(db)
        assert conn.execute("""
            SELECT COUNT(*) FROM evidence e JOIN camp_member m ON m.id = e.source_id
            WHERE e.source_table='camp_member' AND instr(m.record, e.quote) = 0
        """).fetchone()[0] == 0

    def test_no_roster_fact_lacks_evidence(self, db):
        install(db, PEOPLE)
        conn = sqlite3.connect(db)
        assert conn.execute("""
            SELECT COUNT(*) FROM camp_fact f WHERE NOT EXISTS (
                SELECT 1 FROM evidence e
                WHERE e.claim_table='camp_fact' AND e.claim_id = f.id)
        """).fetchone()[0] == 0

    def test_a_fact_citing_a_missing_member_is_refused(self, db):
        install(db, PEOPLE)
        conn = sqlite3.connect(db)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO evidence (claim_table, claim_id, source_table, "
                "source_id, quote) VALUES ('camp_fact', 1, 'camp_member', "
                "999999, 'nope')")

    def test_contact_details_are_retrievable(self, db):
        # A column nothing queries is not an answer: retrieval only searches
        # camp_fact, so the roster's contact details have to become a fact.
        install(db, PEOPLE)
        conn = sqlite3.connect(db)
        fact, = conn.execute(
            "SELECT fact FROM camp_fact WHERE fact LIKE '%Piotr%reach%' "
            "OR fact LIKE 'We can reach Piotr%'").fetchone()
        assert "piotr@example.com" in fact
        assert "+15555550100" in fact
        # The words the question arrives in.
        assert "reach" in fact and "email" in fact and "phone" in fact

    def test_duplicate_chat_rows_for_one_person_do_not_block_the_link(self, db):
        # "Piotr" and "Piotr 'Sunrise'" are one human exported twice. Treating
        # that as ambiguity left the camp's most prolific member unlinked.
        install(db, PEOPLE)
        conn = sqlite3.connect(db)
        person_id, how = conn.execute(
            "SELECT person_id, person_match FROM camp_member "
            "WHERE last_name = 'Bartkowski'").fetchone()
        assert person_id == 1          # the row carrying the messages
        assert "2 rows" in (how or "")

    def test_links_to_the_row_carrying_the_messages_not_the_exact_name(self, db):
        # Two chat rows for one human: the exact name with nothing in it, and
        # a short form with all the writing. Preferring the exact name gave
        # Marcus Foster an empty person and no portrait.
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO person (id, name, message_count) "
                     "VALUES (5, 'Anna Morrow Smith', 0), (6, 'Anna', 900)")
        conn.commit()
        install(db, [dict(PEOPLE[1], name="Anna Morrow Smith",
                          last_name="Morrow Smith")])
        person_id = sqlite3.connect(db).execute(
            "SELECT person_id FROM camp_member").fetchone()[0]
        assert person_id == 6

    def test_an_empty_stub_never_beats_a_row_with_writing(self, db):
        # The corpus is full of names someone was addressed by once. An empty
        # "Jess Sheldon" won on its exact name over the "Jessica Sheldon" row
        # holding 212 messages and her portrait.
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO person (id, name, message_count) VALUES "
                     "(8, 'Jess Sheldon', 0), (9, 'Jessica Sheldon', 212), "
                     "(10, 'Jessie', 0)")
        conn.commit()
        install(db, [dict(PEOPLE[1], name="Jess Sheldon",
                          first_name="Jess", last_name="Sheldon")])
        assert sqlite3.connect(db).execute(
            "SELECT person_id FROM camp_member").fetchone()[0] == 9

    def test_a_stub_still_wins_when_nothing_else_is_available(self, db):
        # Otherwise people with no chat presence stop linking at all.
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO person (id, name, message_count) "
                     "VALUES (11, 'Quiet Person', 0)")
        conn.commit()
        install(db, [dict(PEOPLE[1], name="Quiet Person",
                          first_name="Quiet", last_name="Person")])
        assert sqlite3.connect(db).execute(
            "SELECT person_id FROM camp_member").fetchone()[0] == 11

    def test_a_taken_row_does_not_end_the_search(self, db):
        # Collapsing onto an already-linked row and giving up left Marc Mercer
        # Cabrera unlinked because Marcus Foster had claimed "Marcus" first.
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO person (id, name, message_count) "
                     "VALUES (7, 'Marc MC', 53)")
        conn.commit()
        people = [dict(PEOPLE[0], name="Marcus Foster", first_name="Marcus",
                       last_name="Foster", nickname=""),
                  dict(PEOPLE[1], name="Marc Mercer Cabrera", first_name="Marc",
                       last_name="Mercer Cabrera")]
        install(db, people)
        rows = dict(sqlite3.connect(db).execute(
            "SELECT last_name, person_id FROM camp_member"))
        assert rows["Mercer Cabrera"] == 7      # not None
        assert rows["Foster"] != rows["Mercer Cabrera"]

    def test_a_surname_that_disagrees_still_refuses_to_link(self, db):
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO person (id, name, message_count) "
                     "VALUES (4, 'Anna Moss', 30)")
        conn.commit()
        install(db, PEOPLE)
        linked = sqlite3.connect(db).execute(
            "SELECT person_id FROM camp_member WHERE last_name = 'Morrow'"
        ).fetchone()[0]
        # Anna Morrow matches "Anna Morrow" exactly; "Anna Moss" disagrees on
        # surname, so the two never collapse into one candidate.
        assert linked == 3

    def test_someone_with_no_contact_details_gets_no_contact_fact(self, db):
        install(db, PEOPLE)
        conn = sqlite3.connect(db)
        assert conn.execute(
            "SELECT COUNT(*) FROM camp_fact WHERE fact LIKE 'We can reach "
            "Never Came%'").fetchone()[0] == 0
