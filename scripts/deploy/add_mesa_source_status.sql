-- Additive migration: per-source status visibility on mesa_results.
-- Idempotent — safe to re-run.

ALTER TABLE mesa_results ADD COLUMN IF NOT EXISTS source_status JSONB;
CREATE INDEX IF NOT EXISTS idx_mesa_results_source_status
    ON mesa_results USING GIN (source_status);
