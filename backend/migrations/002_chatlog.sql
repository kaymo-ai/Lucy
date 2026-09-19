-- Chat logs: what people asked Lucy and what she said back.
--
-- They go up private and are mined in aggregate for the questions Lucy could
-- not answer. "Nobody reads anybody's questions" is the promise, and it is
-- strongest as a column that does not exist: there is deliberately no
-- member_id here, so a copy of this database cannot say who asked what. Auth
-- gates the door -- the upload route still requires a member token -- but the
-- member id is used to open the door and then dropped on the floor. Adding
-- attribution later should mean meeting this comment and a failing test, not
-- quietly extending an INSERT.
--
-- id is chosen by the phone, same reasoning as note.id: a retry on a bad
-- connection must not duplicate the turn, and the phone knowing the id is the
-- whole idempotency mechanism.
--
-- No seq, in contrast to note.seq: nothing polls chatlog. It is write-only
-- from phones; the read side is a person running an aggregate query, and
-- aggregates do not need a sync cursor.
CREATE TABLE IF NOT EXISTS chatlog (
    id          UUID        PRIMARY KEY,
    question    TEXT        NOT NULL,
    answer      TEXT        NOT NULL,
    model       TEXT,
    asked_at    TIMESTAMPTZ NOT NULL,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
