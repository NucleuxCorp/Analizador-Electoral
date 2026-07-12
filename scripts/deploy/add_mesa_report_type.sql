-- add_mesa_report_type.sql
-- Extend reports CHECK constraint to include 'mesa' report type.
-- Add partial unique index to prevent duplicate mesa reports per annotator.
--
-- Run in Supabase SQL Editor.

ALTER TABLE reports DROP CONSTRAINT IF EXISTS reports_report_type_check;

ALTER TABLE reports ADD CONSTRAINT reports_report_type_check
    CHECK (report_type IN ('enmienda', 'otro', 'mesa'));

CREATE UNIQUE INDEX IF NOT EXISTS reports_mesa_dedup_idx
    ON reports (pdf_path, annotator)
    WHERE report_type = 'mesa';
