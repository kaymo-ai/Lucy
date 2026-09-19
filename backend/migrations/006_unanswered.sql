-- The gap signal stops being a guess about wording.
--
-- Until now the server decided whether a turn was a gap by matching the answer
-- against lucy_is_refusal() -- a list of seven phrases, three of which the app
-- had written itself in LucyVoice.blank(). The phone generated a refusal so
-- that the server could detect one, and every new way of saying "I have
-- nothing" silently stopped counting as a gap until both lists were updated
-- together.
--
-- The phone knows. Retrieval either returned rows or it did not, and that is
-- settled before a word is generated. It now records that on the turn and
-- uploads it.
--
-- NULL is a third state and it matters. Turns uploaded before this column
-- existed carry no answer to the question, and their wording is the only
-- evidence there is about them. COALESCE below keeps every gap the camp has
-- already collected while new rows use the real signal.
--
-- This is why it is a new file rather than an edit to 003. db.migrate()
-- records applied migrations BY FILENAME and skips any it has seen, so DDL
-- appended to an applied file never runs on the camp VM no matter how
-- re-runnable it is. That mistake was caught once already on this branch.

ALTER TABLE chatlog ADD COLUMN IF NOT EXISTS unanswered BOOLEAN;

-- The old partial index encodes the old predicate, and Postgres evaluates a
-- partial index's predicate at index time and stores the result -- which is
-- why 005 needed a REINDEX after merely replacing the function. A changed
-- predicate cannot be reindexed into place; the index has to be rebuilt.
DROP INDEX IF EXISTS chatlog_refusal_idx;

CREATE INDEX IF NOT EXISTS chatlog_gap_idx
    ON chatlog (lucy_gap_key(question))
    WHERE COALESCE(unanswered, lucy_is_refusal(answer));

-- lucy_is_refusal() is deliberately kept. It is no longer the mechanism, only
-- the decoder for rows that predate the column, and dropping it would silently
-- discard every gap collected before today.
