-- add_reports_table.sql
-- Idempotent DDL for the reports table.
-- Run twice safely: CREATE TABLE IF NOT EXISTS + IF NOT EXISTS indexes.

CREATE TABLE IF NOT EXISTS reports (
    id              BIGSERIAL    PRIMARY KEY,
    crop_id         TEXT         NOT NULL,
    pdf_path        TEXT,
    report_type     TEXT         NOT NULL CHECK (report_type IN ('enmienda', 'otro')),
    digit_original  TEXT,
    digit_corrected TEXT,
    digit           TEXT,
    notes           TEXT,
    annotator       UUID         NOT NULL,
    created_at      TIMESTAMPTZ  DEFAULT now()
);

-- Composite index for retraction queries (annotator + crop + recency window)
CREATE INDEX IF NOT EXISTS reports_crop_annotator_ts
    ON reports (crop_id, annotator, created_at);

-- Descending index for admin ordering (most-recent-first)
CREATE INDEX IF NOT EXISTS reports_created_at_desc
    ON reports (created_at DESC);

-- Row-level security
ALTER TABLE reports ENABLE ROW LEVEL SECURITY;

-- INSERT: any authenticated user may insert
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'reports' AND policyname = 'reports_insert_authenticated'
    ) THEN
        CREATE POLICY reports_insert_authenticated
            ON reports
            FOR INSERT
            TO authenticated
            WITH CHECK (true);
    END IF;
END
$$;

-- SELECT: any authenticated user may select
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'reports' AND policyname = 'reports_select_authenticated'
    ) THEN
        CREATE POLICY reports_select_authenticated
            ON reports
            FOR SELECT
            TO authenticated
            USING (true);
    END IF;
END
$$;
