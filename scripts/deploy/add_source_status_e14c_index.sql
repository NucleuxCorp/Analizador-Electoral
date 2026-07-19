-- Functional index for the "mesas sin E14C" public home counter.
-- Idempotent — safe to re-run.
--
-- Without this, `neq('source_status->>e14c', 'ok')` combined with
-- count=exact forces Postgres to evaluate the JSON arrow expression per
-- row with no usable index, causing a full scan that times out on
-- mesa_results (122k rows). The existing GIN index on the whole
-- source_status column (add_mesa_source_status.sql) does not help here —
-- GIN supports containment/existence operators (@>, ?, ?&, ?|), not text
-- equality/inequality on an extracted ->> value.

CREATE INDEX IF NOT EXISTS idx_mesa_results_source_status_e14c
    ON mesa_results ((source_status ->> 'e14c'));
