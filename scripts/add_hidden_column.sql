-- add_hidden_column.sql
-- Idempotent: adds soft-delete flag to fraud_marks, feedback_marks, and reports.
-- Run once in Supabase SQL Editor.

ALTER TABLE fraud_marks    ADD COLUMN IF NOT EXISTS hidden BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE feedback_marks ADD COLUMN IF NOT EXISTS hidden BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE reports        ADD COLUMN IF NOT EXISTS hidden BOOLEAN NOT NULL DEFAULT FALSE;

-- Indexes to keep admin queries fast after filtering hidden=false
CREATE INDEX IF NOT EXISTS fraud_marks_hidden_idx    ON fraud_marks    (hidden) WHERE NOT hidden;
CREATE INDEX IF NOT EXISTS feedback_marks_hidden_idx ON feedback_marks (hidden) WHERE NOT hidden;
CREATE INDEX IF NOT EXISTS reports_hidden_idx        ON reports        (hidden) WHERE NOT hidden;
