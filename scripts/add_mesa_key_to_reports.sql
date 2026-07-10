-- add_mesa_key_to_reports.sql
-- Adds mesa_key to reports for fast grouping, and creates mesa_reports_view
-- for consolidated per-mesa access with nested individual reports.
--
-- Idempotent: safe to run twice.
-- Does NOT modify existing report rows or their content.

-- ---------------------------------------------------------------------------
-- 1. Add mesa_key column
-- ---------------------------------------------------------------------------
ALTER TABLE reports ADD COLUMN IF NOT EXISTS mesa_key TEXT;

-- ---------------------------------------------------------------------------
-- 2. Backfill mesa_key from pdf_path for existing rows
--    Pattern: E14_PRE_{dept}_{mpio}_{zona}_{puesto}_{mesa}_{ts}.pdf
-- ---------------------------------------------------------------------------
UPDATE reports
SET mesa_key = substring(pdf_path FROM 'E14_PRE_(\d+_\d+_\d+_\d+_\d+_\d+)_\d+')
WHERE mesa_key IS NULL
  AND pdf_path IS NOT NULL;

-- ---------------------------------------------------------------------------
-- 3. Index for grouping queries
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS reports_mesa_key_idx ON reports (mesa_key);

-- ---------------------------------------------------------------------------
-- 4. Consolidated view: one row per mesa, individual reports as JSON array
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW mesa_reports_view AS
SELECT
    mesa_key,
    COUNT(*)                                        AS total_reports,
    COUNT(*) FILTER (WHERE report_type = 'enmienda') AS enmiendas,
    COUNT(*) FILTER (WHERE report_type = 'otro')     AS otros,
    COUNT(*) FILTER (WHERE report_type = 'mesa')     AS mesa_reports,
    COUNT(DISTINCT annotator)                        AS annotators,
    MAX(created_at)                                  AS last_report_at,
    JSON_AGG(
        JSON_BUILD_OBJECT(
            'id',               id,
            'crop_id',          crop_id,
            'report_type',      report_type,
            'digit_original',   digit_original,
            'digit_corrected',  digit_corrected,
            'digit',            digit,
            'notes',            notes,
            'annotator',        annotator,
            'created_at',       created_at,
            'hidden',           hidden
        )
        ORDER BY created_at DESC
    )                                                AS reports
FROM reports
WHERE mesa_key IS NOT NULL
GROUP BY mesa_key
ORDER BY total_reports DESC, last_report_at DESC;

-- Grant read access consistent with RLS on reports table
GRANT SELECT ON mesa_reports_view TO authenticated;
GRANT SELECT ON mesa_reports_view TO service_role;
