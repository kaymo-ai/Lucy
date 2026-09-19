-- Members, their devices, and their notes.
--
-- member.id IS the device UUID the phone generated and keeps in its keychain.
-- There is no separate account: one phone is one member. Re-joining from the
-- same phone must land on the same row, which is why the phone supplies the id
-- rather than the server assigning one -- otherwise reinstalling the app forks
-- someone in two and every note they ever uploaded belongs to a stranger with
-- their name.
CREATE TABLE IF NOT EXISTS member (
    id           UUID PRIMARY KEY,
    display_name TEXT        NOT NULL,
    joined_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Only the hash is kept. The plaintext token exists in the join response and
-- in the phone's keychain and nowhere else, so a copy of this database is not
-- a set of working credentials.
CREATE TABLE IF NOT EXISTS device_token (
    token_sha256 BYTEA       PRIMARY KEY,
    member_id    UUID        NOT NULL REFERENCES member(id) ON DELETE CASCADE,
    issued_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS device_token_member_idx ON device_token (member_id);

-- id is chosen by the phone so an upload interrupted halfway can be retried
-- without creating a second note. seq is the sync cursor: a server-assigned
-- monotonic integer, not a timestamp, because phone clocks in the desert are
-- not to be trusted for ordering.
--
-- photo_sha / memo_sha are hex SHA-256 digests naming a file in the blob
-- store. They are deliberately not foreign keys, and blobs are never deleted:
-- two people can photograph the same sign and produce identical bytes, so one
-- note being deleted must never blind the other.
CREATE TABLE IF NOT EXISTS note (
    id          UUID PRIMARY KEY,
    seq         BIGSERIAL   NOT NULL UNIQUE,
    member_id   UUID        NOT NULL REFERENCES member(id),
    taken_at    TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    transcript  TEXT        NOT NULL DEFAULT '',
    attached    TEXT[]      NOT NULL DEFAULT '{}',
    photo_sha   TEXT,
    memo_sha    TEXT,
    app_version TEXT        NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS note_seq_idx ON note (seq);
