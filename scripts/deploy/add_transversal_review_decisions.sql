-- add_transversal_review_decisions.sql
-- Idempotent DDL for transversal review panel decisions.
-- Run twice safely in Supabase SQL Editor.

CREATE TABLE IF NOT EXISTS transversal_review_decisions (
    id          BIGSERIAL    PRIMARY KEY,
    mesa_key    TEXT         NOT NULL,
    field       TEXT         NOT NULL CHECK (field IN ('VOTANTES', 'URNA', 'SUMA_TOTAL')),
    source      TEXT         NOT NULL CHECK (source IN ('e14c', 'e14d', 'e14t')),
    decision    TEXT         NOT NULL CHECK (decision IN ('accepted', 'rejected')),
    notes       TEXT,
    reviewer_id UUID         NOT NULL,
    created_at  TIMESTAMPTZ  DEFAULT now(),
    updated_at  TIMESTAMPTZ  DEFAULT now(),
    UNIQUE (mesa_key, field, source)
);

CREATE INDEX IF NOT EXISTS idx_transversal_decisions_mesa_key
    ON transversal_review_decisions (mesa_key);

CREATE INDEX IF NOT EXISTS idx_transversal_decisions_reviewer_id
    ON transversal_review_decisions (reviewer_id);

ALTER TABLE transversal_review_decisions ENABLE ROW LEVEL SECURITY;

-- SELECT: authenticated users may read decisions (export / panel reload)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'transversal_review_decisions'
          AND policyname = 'transversal_decisions_select_authenticated'
    ) THEN
        CREATE POLICY transversal_decisions_select_authenticated
            ON transversal_review_decisions
            FOR SELECT
            TO authenticated
            USING (true);
    END IF;
END
$$;

-- INSERT/UPDATE: application server uses service_role (bypasses RLS).
-- No authenticated write policies — matches mesa_results pattern.

GRANT SELECT ON transversal_review_decisions TO authenticated;
GRANT ALL ON transversal_review_decisions TO service_role;