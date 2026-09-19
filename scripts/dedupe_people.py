#!/usr/bin/env python3
"""
Deduplicate and clean up the person table, and record what people are called.

- Remove non-person entries (ingredients, etc.)
- Merge duplicate names, keeping every discarded spelling as an alias
- Split first and last names off full ones, as aliases
- Tidy display names: WhatsApp's "~ " prefix, trailing (email@address)

RUN THIS BEFORE ENRICHMENT. It is not optional ordering.

Removing a non-person deletes their person_content, and enrichment's
`evidence` rows cite person_content by id. Run afterwards and 247 evidence
rows are left pointing at deleted messages; drop those and 49 claims are left
with no evidence at all, at which point verify_evidence_complete refuses the
artifact -- correctly, because an unevidenced claim is exactly what this
project does not ship. Measured, in that order, on 2026-08-20.

The orphan cleanup below exists so a late run fails at the gate rather than
producing a corpus that silently cannot be packaged. It is not a licence to
run late.

ALIASES ARE THE POINT, not a side effect. entity_alias has done this for
THINGS since enrich_ask_vocab existed, which is why "Oz Hole" and "OzHole"
both resolve. People had nothing, and people are what questions are about: of
105 distinct spoken questions on the owner's phone, 16 arrived mistranscribed
and the cluster was names. The variants were already in the database -- "CeCe",
"CeCe Garlan", "Saami" -- and this script was deleting them.
"""

import json
import re
import sqlite3
from pathlib import Path

# Obvious non-person patterns
NOT_PEOPLE = [
    # Food items
    r'^(olive oil|avocado|tomato|sugar|salt|pepper|cheese|milk|cream|wine|beer)s?$',
    r'^(rice|pasta|bread|butter|egg|onion|garlic|potato|carrot|lettuce)s?$',
    r'^(bacon|chicken|beef|pork|fish|salmon|tuna|shrimp)s?$',
    r'^(flour|oil|vinegar|sauce|syrup|honey|mustard|ketchup)s?$',
    r'^(apple|banana|orange|lemon|lime|mango|berry|fruit)s?$',
    r'^(cookie|cake|chocolate|candy|chip|cracker|pretzel)s?$',
    r'^(trail mix|granola|cereal|oat|nut)s?$',
    r'^(coffee|tea|juice|soda|water|sparkling)s?$',
    r'^(vodka|rum|whiskey|gin|tequila|bourbon)$',
    r'^(tortilla|wrap|bun|roll|bagel|croissant)s?$',
    r'^(cilantro|basil|parsley|mint|oregano|thyme|rosemary)$',
    r'^(sour cream|shredded cheese|tomato paste|soy milk)$',
    r'^(scallion|jalapeño|pepper|chili|cumin|paprika)s?$',
    r'^(watermelon|cantaloupe|honeydew|pineapple|strawberr)y?i?e?s?$',
    
    # Non-name patterns
    r'^\d+$',  # Just numbers
    # Abbreviations, and ONLY in upper case.
    #
    # This was matched with re.IGNORECASE, so it caught every two- and
    # three-letter NAME as well: Oz, Edd, Ana, Kat, Max, Jay, Jo, Pip, BJ.
    # Twenty-five campmates on the 2026-08-20 rebuild, and the deletion takes
    # their person_content with it -- Oz alone was 1,513 messages, and he has
    # a camp tradition named after him.
    #
    # Same family as the `count > 2` filter in Retrieval.terms and again in
    # CampVocabulary, both fixed for the same reason: a length or shape rule
    # cannot tell a short name from noise. This one was worse, because those
    # made him unreachable and this one deleted him.
    r'^[A-Z]{2,3}$',  # matched case-sensitively; see is_likely_not_person
    r'^(yes|no|maybe|tbd|n/a|none|other)$',
    r'^\s*$',  # Empty
]

def is_likely_not_person(name: str) -> bool:
    """Check if name is probably not a person."""
    name_lower = name.lower().strip()
    
    # Too short
    if len(name_lower) < 2:
        return True
    
    # Case-insensitively against the lowered name for the food and filler
    # lists, which are written in lower case -- but the abbreviation pattern
    # is upper case ON PURPOSE and is matched against the name as written, so
    # "PS" is an abbreviation and "Oz" is a person.
    for pattern in NOT_PEOPLE:
        if pattern == r'^[A-Z]{2,3}$':
            if re.match(pattern, name.strip()):
                return True
            continue
        if re.match(pattern, name_lower, re.IGNORECASE):
            return True

    return False


def has_history(cursor, person_id: int) -> bool:
    """Whether this row has ever said anything.

    The guard that matters, and the one that would have saved Oz on its own.
    Every pattern above is a guess from a NAME, and a name is thin evidence;
    a thousand messages is not. Somebody who has written in the camp chat is
    a person whatever their name looks like, and deleting them takes their
    messages with them.
    """
    row = cursor.execute(
        "SELECT COUNT(*) FROM person_content WHERE person_id = ?",
        (person_id,)).fetchone()
    return bool(row and row[0])


def normalize_name(name: str) -> str:
    """Normalize name for comparison."""
    # Remove ~ prefix (WhatsApp)
    name = re.sub(r'^~\s*', '', name)
    # Strip whitespace
    name = name.strip()
    # Remove extra spaces
    name = re.sub(r'\s+', ' ', name)
    return name


# Everything that points at a person. Kept in one place because the merge
# and the absorb both have to move all of them, and the failure of missing
# one is a broken artifact rather than a wrong answer.
_PERSON_TABLES = ("person_content", "roster", "shifts", "person_profile",
                  "expertise", "camp_member", "personality")


def person_tables(cursor) -> tuple:
    """The ones that exist right now.

    This script runs BEFORE enrichment, when person_profile, expertise,
    camp_member and personality do not exist yet -- so the list cannot be a
    constant. It also has to survive being run after, when they do, because
    that is when a missed table leaves a row pointing at a deleted person and
    build_preview_db refuses the artifact.
    """
    have = {r[0] for r in cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    return tuple(t for t in _PERSON_TABLES if t in have)

ALIAS_SCHEMA = """
CREATE TABLE IF NOT EXISTS person_alias (
    person_id INTEGER NOT NULL,
    alias     TEXT    NOT NULL,
    source    TEXT    NOT NULL,
    PRIMARY KEY (person_id, alias)
);
"""

# An alias has to be worth priming a recogniser with and worth matching in a
# question. These are not.
BAD_ALIAS = re.compile(r"@|^\W|^\d|^(the|and|for|with|name|location|notes)$",
                       re.I)


def record_alias(cursor, person_id: int, alias: str, source: str) -> bool:
    """One way somebody refers to this person. Returns whether it was kept.

    Deliberately permissive about case and punctuation and strict about
    shape: "CeCe" and "Cece" differ only in case and both are real, but an
    email address is not a name and a single letter matches everyone.
    """
    alias = (alias or "").strip().strip(",;:")
    if len(alias) < 2 or BAD_ALIAS.search(alias):
        return False
    cursor.execute("SELECT name FROM person WHERE id = ?", (person_id,))
    row = cursor.fetchone()
    if row and (row[0] or "").strip().lower() == alias.lower():
        return False          # the canonical name is not an alias of itself
    cursor.execute(
        "INSERT OR IGNORE INTO person_alias (person_id, alias, source) "
        "VALUES (?, ?, ?)", (person_id, alias, source))
    return cursor.rowcount > 0


def record_name_parts(cursor, person_id: int, full_name: str) -> int:
    """"Saami Khoury" also answers to "Saami", and to "Khoury".

    This is what makes a question that uses only a first name reach the
    person at all -- "where is Opal" is asked far more often than the full
    name, and the corpus stores the full one.
    """
    kept = 0
    parts = [x for x in re.split(r"[\s]+", (full_name or "").strip()) if x]
    if len(parts) < 2:
        return 0
    for part in parts:
        # Initials ("T.") identify nobody.
        if len(part.strip(".")) < 2:
            continue
        if record_alias(cursor, person_id, part.strip("."), "part"):
            kept += 1
    return kept


MANUAL_ALIASES = Path(__file__).resolve().parent / "manual_aliases.json"


def apply_manual_aliases(cursor) -> int:
    """Aliases a human curated, from manual_aliases.json.

    The derived passes can only record variants that exist somewhere in the
    corpus. Piotr is spelled correctly every time the camp writes it and
    mangled every time it is spoken, so he gets nothing from them -- there is
    no misspelling to harvest and no second name part to split.

    Applied LAST so a curated alias is never lost to a derived one, and
    reported loudly on a name that does not match: a typo here fails silently
    otherwise, which is the whole class of bug this file exists inside.
    """
    if not MANUAL_ALIASES.exists():
        return 0
    data = json.loads(MANUAL_ALIASES.read_text(encoding="utf-8"))
    added, missing = 0, []
    for name, aliases in data.items():
        if name.startswith("_"):
            continue
        cursor.execute("SELECT id FROM person WHERE name = ?", (name,))
        row = cursor.fetchone()
        if not row:
            missing.append(name)
            continue
        for alias in aliases:
            if record_alias(cursor, row[0], alias, "manual"):
                added += 1
    for name in missing:
        print(f"   ⚠ manual_aliases.json names '{name}', who is not in person")
    return added


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", (name or "").lower()).strip()


def _distance(a: str, b: str) -> int:
    """Levenshtein, small and local rather than a dependency."""
    if a == b:
        return 0
    if abs(len(a) - len(b)) > 3:
        return 99
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def absorb_orphan_spellings(cursor) -> int:
    """Fold the zero-message rows that are another spelling of a real person.

    find_duplicates() groups on the normalised name, which is exact, so it
    never joined "CeCe Garlan" to "Cece Garland" or "Saami" to "Saami
    Khoury" -- one surname differs by a letter, the other is a first name
    on its own. Those rows sat in `person` carrying no messages and were the
    exact spellings people actually use.

    Two rules, both deliberately narrow:

      · every token of the orphan appears in the real name ("Saami" inside
        "Saami Khoury", "CeCe" inside "Cece Garland")
      · or the whole normalised name is within one edit for names long
        enough that one edit cannot change who is meant ("cecegarlan" ->
        "cecegehring")

    An orphan matching more than one person is skipped entirely: guessing
    which "David" was meant is worse than not linking.
    """
    cursor.execute("""
        SELECT p.id, p.name, COUNT(c.id)
        FROM person p LEFT JOIN person_content c ON c.person_id = p.id
        WHERE p.name IS NOT NULL GROUP BY p.id
    """)
    rows = cursor.fetchall()
    real = [(pid, name, n) for pid, name, n in rows if n > 0]
    orphans = [(pid, name) for pid, name, n in rows if n == 0]

    kept = 0
    for oid, oname in orphans:
        on = _norm(oname)
        if len(on) < 3:
            continue
        matches = []
        for pid, name, _ in real:
            rn = _norm(name)
            if not rn or rn == on:
                continue
            otoks, rtoks = set(on.split()), set(rn.split())
            subset = otoks and otoks.issubset(rtoks)
            close = (len(on) >= 8 and len(rn) >= 8
                     and _distance(on.replace(" ", ""), rn.replace(" ", "")) <= 1)
            if subset or close:
                matches.append((pid, name))
        if len(matches) != 1:
            continue                      # ambiguous, or nothing
        pid, _ = matches[0]
        if record_alias(cursor, pid, oname, "merged"):
            kept += 1
        # EVERY table that references a person, not just the three the
        # original merge knew about. Missing one leaves a row pointing at a
        # person that no longer exists, and build_preview_db's integrity
        # check refuses the artifact -- "evidence.source_id does not exist in
        # the named source table". That is the right place for it to fail,
        # and it is why this list is derived from the schema rather than
        # remembered.
        for table in person_tables(cursor):
            cursor.execute(f"UPDATE {table} SET person_id=? WHERE person_id=?",
                           (pid, oid))
        cursor.execute("DELETE FROM person WHERE id=?", (oid,))
    return kept


EMAIL_SUFFIX = re.compile(r"\s*[(<][^)>]*@[^)>]*[)>]\s*$")


def tidy_display_names(cursor) -> int:
    """Make the canonical name the one a person would recognise.

    Two shapes, both of which were surviving as the name the People tab shows
    because dedupe keeps whichever row carries the most messages, and that row
    is often the one with the ugliest label.

    "~ " is WhatsApp's marker for a sender who is not in the exporter's
    contacts. It is a fact about one phone, not about the camp, and it was
    winning: "~ Adina" was canonical with "Adina" recorded as its alias, which
    is backwards.

    A trailing "(someone@example.com)" comes from roster and signup sheets
    that pair a name with an address. It made Oz canonical as
    "Oz (oz@example.com)". The full string is kept as an alias, because
    somebody searching the roster may well type it.

    It marks a sender who is not in your contacts, which is a fact about the
    exporter's phone and not about the camp. It was surviving as the CANONICAL
    name -- "~ Adina" with "Adina" recorded as the alias, which is backwards --
    because the tilde row carried the messages and so won the merge.
    """
    fixed = 0
    cursor.execute("SELECT id, name FROM person WHERE name IS NOT NULL")
    for pid, name in cursor.fetchall():
        clean = EMAIL_SUFFIX.sub("", name.lstrip("~")).strip()
        if clean == name or len(clean) < 2:
            continue
        # A name is only worth rewriting if what is left is still a name.
        if BAD_ALIAS.search(clean) or "@" in clean:
            continue
        cursor.execute("SELECT id FROM person WHERE lower(name)=? AND id<>?",
                       (clean.lower(), pid))
        if cursor.fetchone():
            continue                      # a clean row already exists; leave it
        record_alias(cursor, pid, name, "merged")
        cursor.execute("UPDATE person SET name=? WHERE id=?", (clean, pid))
        fixed += 1
    return fixed


def find_duplicates(cursor):
    """Find duplicate names."""
    cursor.execute('SELECT id, name FROM person')
    all_people = cursor.fetchall()
    
    # Group by normalized name
    by_normalized = {}
    for pid, name in all_people:
        norm = normalize_name(name).lower()
        if norm not in by_normalized:
            by_normalized[norm] = []
        by_normalized[norm].append((pid, name))
    
    # Find groups with multiple entries
    duplicates = {k: v for k, v in by_normalized.items() if len(v) > 1}
    return duplicates


def main():
    db_path = Path('~/Snails/scripts/output/ps_knowledge.db')
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("🧹 Cleaning person table...")
    
    # Step 1: Remove non-person entries
    cursor.execute('SELECT id, name FROM person')
    all_people = cursor.fetchall()
    
    non_people = []
    spared = []
    for pid, name in all_people:
        if not is_likely_not_person(name):
            continue
        # A name that looks like noise but has history is a person.
        if has_history(cursor, pid):
            spared.append(name)
            continue
        non_people.append((pid, name))
    if spared:
        print(f"   kept {len(spared)} whose names look like noise but who "
              f"have written in the chat: {', '.join(sorted(spared)[:8])}"
              + (" ..." if len(spared) > 8 else ""))
    
    print(f"\n📋 Found {len(non_people)} likely non-person entries:")
    for pid, name in non_people[:20]:
        print(f"   - {name}")
    if len(non_people) > 20:
        print(f"   ... and {len(non_people) - 20} more")
    
    # Delete non-people
    for pid, name in non_people:
        # First update foreign keys
        cursor.execute('DELETE FROM person_content WHERE person_id = ?', (pid,))
        cursor.execute('DELETE FROM roster WHERE person_id = ?', (pid,))
        cursor.execute('DELETE FROM shifts WHERE person_id = ?', (pid,))
        cursor.execute('DELETE FROM person WHERE id = ?', (pid,))
    
    print(f"\n✓ Removed {len(non_people)} non-person entries")
    
    # Step 2: Find and merge duplicates
    cursor.executescript(ALIAS_SCHEMA)
    alias_count = 0
    duplicates = find_duplicates(cursor)
    print(f"\n👥 Found {len(duplicates)} duplicate name groups:")
    
    merged_count = 0
    for norm_name, entries in duplicates.items():
        if len(entries) <= 1:
            continue
        
        # Keep the one with most content
        cursor.execute('''
            SELECT person_id, COUNT(*) as cnt 
            FROM person_content 
            WHERE person_id IN ({})
            GROUP BY person_id
            ORDER BY cnt DESC
        '''.format(','.join(str(e[0]) for e in entries)))
        
        content_counts = cursor.fetchall()
        
        # Pick the primary (most content, or first if none)
        if content_counts:
            primary_id = content_counts[0][0]
        else:
            primary_id = entries[0][0]
        
        primary_name = next(name for pid, name in entries if pid == primary_id)
        
        # Merge others into primary
        other_ids = [pid for pid, name in entries if pid != primary_id]
        
        if other_ids:
            print(f"   {norm_name}: keeping '{primary_name}' (id {primary_id}), merging {len(other_ids)} others")
            
            for oid in other_ids:
                # Record the spelling BEFORE deleting the row that carries it.
                # This is the whole point: these are names the corpus itself
                # used for this person, and they were being thrown away.
                cursor.execute('SELECT name FROM person WHERE id = ?', (oid,))
                dup = cursor.fetchone()
                if dup:
                    if record_alias(cursor, primary_id, dup[0], 'merged'):
                        alias_count += 1
                # Update foreign keys
                for table in person_tables(cursor):
                    cursor.execute(f'UPDATE {table} SET person_id = ? WHERE person_id = ?', (primary_id, oid))
                # Delete duplicate person
                cursor.execute('DELETE FROM person WHERE id = ?', (oid,))
                merged_count += 1
    
    print(f"\n✓ Merged {merged_count} duplicate entries")

    # Step 3: first and last names, for everyone who survived the merge.
    # Done after merging so parts attach to the primary row rather than to a
    # duplicate that is about to disappear.
    cursor.execute('SELECT id, name FROM person WHERE name IS NOT NULL')
    for pid, name in cursor.fetchall():
        alias_count += record_name_parts(cursor, pid, name)

    alias_count += absorb_orphan_spellings(cursor)
    tidied = tidy_display_names(cursor)
    # Tidying renames a person AFTER their aliases were recorded, so an
    # alias can end up identical to the name it belongs to -- Oz was
    # "Oz (oz@example.com)" with the alias "Oz", and became "Oz". Harmless
    # to match on, but it wastes one of the hundred phrases the recogniser is
    # primed with.
    alias_count += apply_manual_aliases(cursor)

    cursor.execute("""
        DELETE FROM person_alias
        WHERE alias IN (SELECT name FROM person WHERE person.id = person_alias.person_id)
    """)
    # Evidence that now cites nothing.
    #
    # Removing a non-person deletes their person_content, and any evidence row
    # quoting one of those messages is left pointing at an id that is gone.
    # build_preview_db refuses the artifact for exactly this -- "evidence
    # .source_id does not exist in the named source table" -- which is the
    # right place to fail, but it means this script must clean up after
    # itself rather than leave a corpus that cannot be packaged.
    #
    # This script is meant to run BEFORE enrichment, when no evidence exists
    # and this is a no-op. It survives being run after, because someone will.
    # Only when there is evidence to clean. This script runs BEFORE
    # enrichment, when the table does not exist and there is nothing to
    # orphan -- and it must survive being run after, when there is.
    have = {r[0] for r in cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    dead = 0
    if "evidence" in have:
        cursor.execute("""
        DELETE FROM evidence WHERE
          (source_table='person_content'
           AND NOT EXISTS (SELECT 1 FROM person_content x WHERE x.id = evidence.source_id))
       OR (source_table='camp_member'
           AND NOT EXISTS (SELECT 1 FROM camp_member x WHERE x.id = evidence.source_id))
       OR (source_table='camp_knowledge'
           AND NOT EXISTS (SELECT 1 FROM camp_knowledge x WHERE x.id = evidence.source_id))
        """)
        dead = cursor.rowcount
    if dead:
        print(f"✓ Dropped {dead} evidence rows citing a deleted source")
    print(f"✓ Recorded {alias_count} person aliases")
    print(f"✓ Tidied {tidied} display names")
    
    conn.commit()
    
    # Final stats
    cursor.execute('SELECT COUNT(*) FROM person')
    person_count = cursor.fetchone()[0]
    print(f"\n📊 Final person count: {person_count}")
    
    # Show top people by content
    print("\n🏆 Top 10 people by content:")
    cursor.execute('''
        SELECT p.name, COUNT(pc.id) as cnt
        FROM person p
        LEFT JOIN person_content pc ON p.id = pc.person_id
        GROUP BY p.id
        ORDER BY cnt DESC
        LIMIT 10
    ''')
    for name, cnt in cursor.fetchall():
        print(f"   {name}: {cnt} messages")
    
    conn.close()
    print("\n✅ Done!")


if __name__ == '__main__':
    main()
