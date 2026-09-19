-- The cursor a phone polls is the moment of APPROVAL, not of upload.
--
-- seq is assigned when the answer arrives, but an answer becomes servable only
-- when a human approves it, which can be days later -- by then every phone has
-- synced past that seq and would never see it. approved_seq is assigned under
-- the same advisory lock at the moment of approval, so the down-sync orders by
-- the thing that actually changed.
--
-- A separate file, not an amendment to 003_help_lucy.sql: migrations are
-- tracked by filename in schema_migration (see db.py's migrate()), with no
-- checksum and no re-diff, so appending to an already-applied file is a
-- silent no-op on any database -- including the camp VM -- that already has
-- 003 recorded. 001, 002 and 003 are each their own file for the same reason;
-- this keeps that convention.
ALTER TABLE answer ADD COLUMN IF NOT EXISTS approved_seq BIGINT;
CREATE SEQUENCE IF NOT EXISTS answer_approved_seq_counter;
CREATE INDEX IF NOT EXISTS answer_approved_cursor_idx
    ON answer (approved_seq) WHERE status = 'approved';
