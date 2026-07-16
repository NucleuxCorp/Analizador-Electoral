-- add_reports_tables.sql
-- Run once in Supabase SQL Editor.
-- Adds fraud_marks and feedback_marks tables for persistent reporting.

-- ---------------------------------------------------------------------------
-- fraud_marks
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fraud_marks (
    id          BIGSERIAL    PRIMARY KEY,
    crop_id     TEXT,
    pdf_path    TEXT,
    reason      TEXT         NOT NULL,
    annotator   TEXT         NOT NULL,
    marked_at   TIMESTAMPTZ  DEFAULT now()
);

CREATE INDEX IF NOT EXISTS fraud_marks_pdf_path_idx  ON fraud_marks (pdf_path);
CREATE INDEX IF NOT EXISTS fraud_marks_marked_at_idx ON fraud_marks (marked_at DESC);

-- ---------------------------------------------------------------------------
-- feedback_marks
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS feedback_marks (
    id          BIGSERIAL    PRIMARY KEY,
    crop_id     TEXT,
    pdf_path    TEXT,
    message     TEXT         NOT NULL,
    annotator   TEXT         NOT NULL,
    reported_at TIMESTAMPTZ  DEFAULT now()
);

CREATE INDEX IF NOT EXISTS feedback_marks_reported_at_idx ON feedback_marks (reported_at DESC);

-- ---------------------------------------------------------------------------
-- RLS
-- ---------------------------------------------------------------------------
ALTER TABLE fraud_marks    ENABLE ROW LEVEL SECURITY;
ALTER TABLE feedback_marks ENABLE ROW LEVEL SECURITY;

-- authenticated users can insert
CREATE POLICY "auth insert fraud"    ON fraud_marks    FOR INSERT TO authenticated WITH CHECK (true);
CREATE POLICY "auth insert feedback" ON feedback_marks FOR INSERT TO authenticated WITH CHECK (true);

-- authenticated users (backend service role) can read all rows
-- service_role bypasses RLS automatically; this policy covers anon-key fallback
CREATE POLICY "auth select fraud"    ON fraud_marks    FOR SELECT TO authenticated USING (true);
CREATE POLICY "auth select feedback" ON feedback_marks FOR SELECT TO authenticated USING (true);
