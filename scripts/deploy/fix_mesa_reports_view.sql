-- fix_mesa_reports_view.sql
-- Fixes admin "Ver acta" for mesa reports:
--   1. Backfill reports.mesa_key from crops.mesa_key_mr when missing
--   2. Include pdf_path in mesa_reports_view JSON (E14 type grouping + debugging)
--
-- Idempotent — safe to run twice.

-- 1. Backfill mesa_key from crops (E14T/E14D hash paths lack E14_PRE in pdf_path)
UPDATE reports r
SET mesa_key = c.mesa_key_mr
FROM crops c
WHERE r.mesa_key IS NULL
  AND r.crop_id = c.crop_id
  AND c.mesa_key_mr IS NOT NULL;

UPDATE reports r
SET mesa_key = c.mesa_key
FROM crops c
WHERE r.mesa_key IS NULL
  AND r.crop_id = c.crop_id
  AND c.mesa_key IS NOT NULL;

-- 2. Recreate view with pdf_path in nested JSON
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
            'pdf_path',         pdf_path,
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
  AND COALESCE(hidden, FALSE) = FALSE
GROUP BY mesa_key
ORDER BY total_reports DESC, last_report_at DESC;

GRANT SELECT ON mesa_reports_view TO authenticated;
GRANT SELECT ON mesa_reports_view TO service_role;