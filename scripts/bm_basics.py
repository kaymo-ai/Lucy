#!/usr/bin/env python3
"""
General Burning Man knowledge, written down rather than recalled.

The camp's own corpus knows what the camp said to itself. It does not know that
nothing is sold on playa except ice and coffee, or how much water a person needs
per day — because nobody in the group chat ever had to explain it.

That knowledge has to come from somewhere, and the tempting answer is "the
language model already knows". It does, approximately, and approximately is how
someone ends up on playa with half the water they need. So these are written
here, by hand, from the Survival Guide and long-standing event practice, and
they enter the database as rows like any other claim.

They are marked as a distinct source. Lucy says "that's general playa practice"
rather than "our camp says", because the difference matters: one is something
the camp decided and can change, the other is how the event works.

Usage:
    python bm_basics.py output/enriched_preview.db
"""
import sqlite3
import sys
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS general_knowledge (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    topic   TEXT NOT NULL,
    fact    TEXT NOT NULL,
    source  TEXT NOT NULL
);
"""

SURVIVAL_GUIDE = "Burning Man Survival Guide"
PRACTICE = "Long-standing event practice"

# Deliberately conservative. Everything here is stable year to year and load
# bearing for someone in the desert. Anything that changes annually — ticket
# prices, gate hours, exact burn nights — is left out on purpose: a confidently
# wrong date is worse than no date.
FACTS = [
    ("water",
     "Nothing is sold on playa except ice and coffee at Center Camp. You must "
     "bring all the water you will drink, cook with and wash in.",
     SURVIVAL_GUIDE),
    ("water",
     "Plan for at least 1.5 gallons of water per person per day, and more if "
     "you are working through the heat or staying longer than a week.",
     SURVIVAL_GUIDE),
    ("water",
     "Ice is sold at Arctica, at Center Camp and at 3:00 and 9:00 plazas. It is "
     "usually the only thing you can buy in Black Rock City.",
     PRACTICE),
    ("greywater",
     "Greywater may not be discharged onto the playa. Dishwater and shower "
     "runoff must be evaporated or carried out with you.",
     SURVIVAL_GUIDE),
    ("greywater",
     "The playa surface is a dry lake bed and an environmentally protected "
     "area. Anything that soaks in is a violation, including a bucket tipped "
     "out behind camp.",
     SURVIVAL_GUIDE),
    ("moop",
     "MOOP means Matter Out Of Place: anything that was not part of the desert "
     "when you arrived. Leave No Trace means all of it comes home with you.",
     SURVIVAL_GUIDE),
    ("moop",
     "Camps are scored on their MOOP line after the event, and a bad score "
     "affects placement the following year.",
     PRACTICE),
    ("address",
     "Black Rock City is laid out as a clock face. Addresses are given as a "
     "time and a lettered street — 7:59 & C is between 7:00 and 8:00, on the "
     "third ring out from the Man.",
     PRACTICE),
    ("address",
     "Streets run alphabetically outward from the Esplanade, which is the "
     "innermost ring facing open playa.",
     PRACTICE),
    ("dust",
     "Dust storms arrive with little warning and can reduce visibility to "
     "nothing. Carry goggles and a dust mask at all times, day or night.",
     SURVIVAL_GUIDE),
    ("dust",
     "Playa dust is strongly alkaline. It dries and cracks skin, and vinegar "
     "diluted with water neutralises it.",
     PRACTICE),
    ("heat",
     "Daytime temperatures regularly exceed 100°F and drop sharply overnight. "
     "Pack for both, not for the average.",
     SURVIVAL_GUIDE),
    ("gifting",
     "Black Rock City runs on gifting, not barter. A gift is given without "
     "expecting anything in return, and trade is not the point.",
     SURVIVAL_GUIDE),
    ("lights",
     "Anyone moving around after dark needs to be lit — EL wire, lights, "
     "anything visible. Unlit people and bikes are called darkwads and are the "
     "main cause of night collisions.",
     PRACTICE),
    ("bikes",
     "A bike is close to essential; the city is roughly 1.5 miles across. "
     "Label it, lock it, and fit lights you can see from a distance.",
     PRACTICE),
    ("exodus",
     "Leaving at the end can take many hours in queue. Carry enough water, "
     "fuel and food to sit in exodus without needing anything.",
     PRACTICE),
]


def write_general_knowledge(conn: sqlite3.Connection) -> int:
    """Write the background layer into an open database, returning the count.

    Callable, not just runnable: this used to live only inside main(), which
    made it a manual step in the shipping path — nothing invoked it, so every
    preview rebuild dropped general_knowledge and searchGeneral quietly
    returned [] on device. build_preview_db.py now calls this against the
    artifact it just built; the CLI below remains for running it by hand.

    Wipe-and-rewrite, so editing FACTS and re-running never leaves a stale
    row behind.
    """
    conn.executescript(SCHEMA)
    conn.execute("DELETE FROM general_knowledge")
    conn.executemany(
        "INSERT INTO general_knowledge (topic, fact, source) VALUES (?,?,?)",
        FACTS)
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM general_knowledge").fetchone()[0]


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    db = Path(sys.argv[1])
    if not db.exists():
        raise SystemExit(f"no database at {db}")

    conn = sqlite3.connect(db)
    n = write_general_knowledge(conn)
    topics = [r[0] for r in conn.execute(
        "SELECT DISTINCT topic FROM general_knowledge ORDER BY topic")]
    print(f"wrote {n} general facts to {db}")
    print(f"  topics: {', '.join(topics)}")


if __name__ == "__main__":
    main()
