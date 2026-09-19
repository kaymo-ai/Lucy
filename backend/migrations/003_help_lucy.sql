-- Help Lucy: the answer half.
--
-- A gap is NOT a table. It is a query over chatlog rows whose answer is
-- refusal-shaped, grouped by a normalised question. Storing gaps would mean a
-- second copy of chatlog that drifts from it, and a nightly job to keep them
-- in step that nobody on playa is going to notice has stopped.
--
-- Two rules get a function each, because both already exist in more than one
-- place and the drift is silent rather than loud.

-- What counts as Lucy failing to answer.
--
-- These three prefixes ARE deadEnd() in Lucy/ChatView.swift. The normalisation
-- is that function's, character for character: lowercase, curly apostrophe to
-- straight, "do not" collapsed to "don't" -- which is how "i do not know" and
-- "i don't know" become one prefix. If deadEnd() grows a fourth phrase, it
-- grows here too, or the new refusal shape silently stops counting as a gap
-- and the camp never sees the question.
CREATE OR REPLACE FUNCTION lucy_is_refusal(a TEXT) RETURNS BOOLEAN AS $$
    SELECT replace(replace(lower(a), '’', ''''), 'do not', 'don''t')
           LIKE ANY (ARRAY['i don''t know%',
                           'nothing in what i''ve got%',
                           'that''s not something i''ve been told%']);
$$ LANGUAGE SQL IMMUTABLE;

-- How two phrasings of one question become one gap.
--
-- Server-side only, and deliberately NOT mirrored on the phone. The phone's
-- equivalent is contentWords() in ChatView.swift, which folds case through
-- Swift's Unicode rules, splits on CharacterSet.alphanumerics and returns an
-- unordered Set -- none of which this reproduces byte-for-byte, and all of
-- which the camp's own names and apostrophes will find. So the phone uploads
-- the verbatim question and this assigns the key. A hash that disagreed on one
-- codepoint would not throw; it would file an answer against a gap that does
-- not exist.
-- Mirrors contentWords() in Lucy/ChatView.swift in SPIRIT, not in bytes --
-- the phone never runs this and never has to agree with it. Same three moves:
-- split on non-alphanumerics, drop single characters, drop the same stopwords.
--
-- Dropping single characters is what makes "Where's the medkit?" and "where is
-- the medkit" one gap: the apostrophe splits "where's" into "where" and "s",
-- the stopword list takes "where", and the length test takes "s". Stripping
-- punctuation alone leaves "where s the medkit", which is its own gap forever.
--
-- Sorted, so word order does not turn one question into two.
CREATE OR REPLACE FUNCTION lucy_normalise_question(q TEXT) RETURNS TEXT AS $$
    SELECT coalesce(nullif(string_agg(w, ' ' ORDER BY w), ''),
                    -- A question with no content words at all ("what is the?").
                    -- Without this fallback every one of them hashes to the
                    -- empty string and the camp sees them as a single gap.
                    btrim(regexp_replace(lower(replace(q, '’', '''')),
                                         '[^a-z0-9]+', ' ', 'g')))
    FROM unnest(regexp_split_to_array(
             lower(replace(q, '’', '''')), '[^a-z0-9]+')) AS w
    WHERE length(w) > 1
      AND w <> ALL (ARRAY['what','whats','is','an','the','who','how','do',
                          'does','we','where','when','why','are','our','my']);
$$ LANGUAGE SQL IMMUTABLE;

CREATE OR REPLACE FUNCTION lucy_gap_key(q TEXT) RETURNS TEXT AS $$
    SELECT encode(sha256(lucy_normalise_question(q)::bytea), 'hex');
$$ LANGUAGE SQL IMMUTABLE;

-- Answers. Shaped like note, deliberately.
--
-- id comes from the phone: a retry on a bad connection must not double-store,
-- and the phone knowing the id is the whole idempotency mechanism.
--
-- seq is a cursor, assigned under an advisory lock in answers.py for the same
-- reason note.seq is: without it a phone polling ?since=N steps over a row
-- that committed late and never sees that answer again.
--
-- No member_id and no device_id. Same promise chatlog makes and the same way
-- of keeping it: a copy of this database cannot say who answered, any more
-- than it can say who asked. Adding attribution later should mean meeting this
-- comment and a failing test, not quietly extending an INSERT.
--
-- question is stored verbatim alongside gap_key, because gap_key is a hash and
-- a reviewer looking at a pending answer needs to see what was asked.
CREATE TABLE IF NOT EXISTS answer (
    id          UUID        PRIMARY KEY,
    seq         BIGSERIAL   NOT NULL UNIQUE,
    gap_key     TEXT        NOT NULL,
    question    TEXT        NOT NULL,
    body        TEXT        NOT NULL,
    status      TEXT        NOT NULL DEFAULT 'pending'
                CONSTRAINT answer_status_check
                CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS answer_gap_idx ON answer (gap_key);
-- Partial, because the down-sync only ever asks for approved rows in seq
-- order, and pending ones are the majority right after the burn.
CREATE INDEX IF NOT EXISTS answer_approved_seq_idx
    ON answer (seq) WHERE status = 'approved';
-- The gap query filters chatlog by refusal shape before it groups. Without
-- this it is a sequential scan over every turn the camp ever took.
CREATE INDEX IF NOT EXISTS chatlog_refusal_idx
    ON chatlog (lucy_gap_key(question)) WHERE lucy_is_refusal(answer);
