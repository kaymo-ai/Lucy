-- Refusal shapes: hasPrefix -> contains, plus three phrases the rule never
-- had.
--
-- Measured 2026-08-19 against 99 real answered turns from the device's own
-- journal (Documents/lucy-journal.txt). The rule in 003_help_lucy.sql caught
-- 33 of 99. Two defects, both fixed here, mirroring the fix to deadEnd() in
-- Lucy/ChatView.swift:
--
--   1. The match was a prefix match. "OK ho. I don't know what a K is." was
--      missed only because two words of filler sat in front of a phrase the
--      rule already knew -- leading filler is common in her output. This
--      widens `LIKE ANY (ARRAY['pattern%'])` to
--      `LIKE ANY (ARRAY['%pattern%'])`.
--   2. Three refusal shapes were missing outright: "I don't have any
--      facts...", "I don't have a fact...", and "I can only say what the
--      facts state...".
--
-- The widened rule catches 38 of 99, loses none of the original 33, and
-- produces no false positive across all 99. The normalisation (lowercase,
-- curly apostrophe to straight, "do not" collapsed to "don't") is unchanged.
--
-- A new file, not an edit to 003_help_lucy.sql: migrations are tracked by
-- filename in schema_migration, with no checksum and no re-diff (see
-- db.py's migrate()), so editing an already-applied file is a silent no-op
-- on any database that has already recorded it -- including the camp VM,
-- which has 003 and 004 applied. This exact mistake is called out in
-- 004_approved_seq.sql's own comment; this file follows the same
-- convention. CREATE OR REPLACE FUNCTION below supersedes 003's
-- three-phrase, prefix-matching body with the seven-phrase, contains-
-- matching one.
--
-- Replacing the function is not the whole fix. chatlog_refusal_idx (defined
-- in 003) is a partial index whose predicate is this same function call.
-- CREATE OR REPLACE FUNCTION keeps the function's pg_proc identity, so
-- Postgres does not retroactively re-evaluate which existing chatlog rows
-- satisfy the replaced body -- and answers.py's
-- `WHERE lucy_is_refusal(c.answer)` matches the index's predicate
-- syntactically, so the planner trusts the index instead of rechecking each
-- row. A chatlog row written before this migration, whose answer only
-- qualifies under the new phrase list, would have no index entry and would
-- silently stay off the gap list forever -- the exact failure this task
-- exists to close, just relocated from the function into the index. The
-- REINDEX below rebuilds the index against the function now in place, so
-- rows already in chatlog on the camp VM are picked up, not just new ones.
CREATE OR REPLACE FUNCTION lucy_is_refusal(a TEXT) RETURNS BOOLEAN AS $$
    SELECT replace(replace(lower(a), '’', ''''), 'do not', 'don''t')
           LIKE ANY (ARRAY['%i don''t know%',
                           '%nothing in what i''ve got%',
                           '%that''s not something i''ve been told%',
                           '%i don''t have any facts%',
                           '%i don''t have a fact%',
                           '%the facts provided don''t contain%',
                           '%i can only say what the facts state%']);
$$ LANGUAGE SQL IMMUTABLE;

REINDEX INDEX chatlog_refusal_idx;
