-- add_transversal_review_reports.sql
-- Structured mesa reports for /admin/transversal (separate from accept/reject decisions).
-- Idempotent — safe to run twice in Supabase SQL Editor.

CREATE TABLE IF NOT EXISTS transversal_review_reports (
    id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    mesa_key    TEXT         NOT NULL,
    source      TEXT         NOT NULL CHECK (source IN ('e14c', 'e14d', 'e14t')),
    report_type TEXT         NOT NULL CHECK (report_type IN ('campos_vacios', 'enmienda', 'otro')),
    notes       TEXT         NOT NULL,
    fields      JSONB,
    annotator   UUID         NOT NULL,
    created_at  TIMESTAMPTZ  DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_transversal_reports_mesa_key
    ON transversal_review_reports (mesa_key);

CREATE INDEX IF NOT EXISTS idx_transversal_reports_annotator
    ON transversal_review_reports (annotator);

ALTER TABLE transversal_review_reports ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'transversal_review_reports'
          AND policyname = 'transversal_reports_select_authenticated'
    ) THEN
        CREATE POLICY transversal_reports_select_authenticated
            ON transversal_review_reports
            FOR SELECT
            TO authenticated
            USING (true);
    END IF;
END
$$;

GRANT SELECT ON transversal_review_reports TO authenticated;
GRANT ALL ON transversal_review_reports TO service_role;