-- add_mesa_report_type.sql
-- Idempotent migration: extend reports CHECK constraint to include 'mesa'
-- and add a partial unique index for per-annotator mesa dedup.
--
-- Safe to run twice: the DO $$ block queries pg_constraint before acting,
-- and CREATE UNIQUE INDEX uses IF NOT EXISTS.
--
-- Apply order: run AFTER add_reports_table.sql (table must exist).

DO $$
DECLARE v_conname text;
BEGIN
    -- Locate the existing CHECK constraint on report_type (name may vary)
    SELECT conname INTO v_conname
    FROM pg_constraint
    WHERE conrelid = 'reports'::regclass
      AND contype = 'c'
      AND pg_get_constraintdef(oid) ILIKE '%report_type%';

    IF v_conname IS NOT NULL THEN
        EXECUTE format('ALTER TABLE reports DROP CONSTRAINT %I', v_conname);
    END IF;

    -- Add new CHECK that includes 'mesa'
    ALTER TABLE reports ADD CONSTRAINT reports_report_type_check
        CHECK (report_type IN ('enmienda', 'otro', 'mesa'));
END $$;

-- Partial unique index: one mesa report per (pdf_path, annotator).
-- Enmienda and otro are not affected (partial WHERE clause).
CREATE UNIQUE INDEX IF NOT EXISTS reports_mesa_dedup_idx
    ON reports (pdf_path, annotator)
    WHERE report_type = 'mesa';
