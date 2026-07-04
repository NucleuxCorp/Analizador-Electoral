-- Mesa audit results table — schema migration
-- Safe to run multiple times (idempotent: CREATE TABLE IF NOT EXISTS).
-- Deploy via Supabase SQL editor before running upload_mesa_results.py.
--
-- Design reference: sdd/mesa-results-upload/design (D1–D4, D7)
-- RLS Option A: enabled, no policies — service-role key bypasses; anon blocked.

-- ---------------------------------------------------------------------------
-- Table
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS mesa_results (
    -- Deterministic composite key — enables ON CONFLICT upsert (D2)
    mesa_key                TEXT        PRIMARY KEY,

    -- Structural coordinates
    dept                    TEXT        NOT NULL,
    mpio                    TEXT        NOT NULL,
    zona                    TEXT        NOT NULL,
    puesto                  TEXT        NOT NULL,
    mesa                    TEXT        NOT NULL,

    -- Aggregate verdict (5-level taxonomy, D3)
    overall_status          TEXT        NOT NULL
        CHECK (overall_status IN ('clean', 'known_anomaly', 'warning', 'discrepancy', 'critical')),

    -- Independent boolean flags — kept flat so composite indexes work cleanly (D1)
    cross_discrepancy       BOOLEAN     NOT NULL DEFAULT FALSE,
    tachon_suspicious       BOOLEAN     NOT NULL DEFAULT FALSE,
    has_missing_fields      BOOLEAN     NOT NULL DEFAULT FALSE,

    -- Per-source arithmetic checks (nullable: source may be absent)
    e14c_arith_ok           BOOLEAN,
    e14t_arith_ok           BOOLEAN,
    e14d_arith_ok           BOOLEAN,

    -- Per-source arithmetic deltas (nullable: source may be absent)
    e14c_arith_delta        INT,
    e14t_arith_delta        INT,
    e14d_arith_delta        INT,

    -- Number of sources that passed all checks
    sources_ok_count        INT         NOT NULL DEFAULT 0,

    -- Full audit payload preserved for downstream analysis (D1)
    raw_data                JSONB       NOT NULL,

    -- Timestamp of last upsert
    uploaded_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Uniqueness guard on the natural composite key (redundant with PK but
    -- explicit for clarity and PostgREST FK introspection)
    UNIQUE (dept, mpio, zona, puesto, mesa)
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------

-- Filter by dept + status (primary admin panel query pattern)
CREATE INDEX IF NOT EXISTS idx_mesa_results_dept_status
    ON mesa_results (dept, overall_status);

-- Partial index: find all rows flagged as cross-discrepant (sparse set)
CREATE INDEX IF NOT EXISTS idx_mesa_results_cross_discrepancy
    ON mesa_results (cross_discrepancy)
    WHERE cross_discrepancy = TRUE;

-- Partial index: find all rows flagged as tachon-suspicious (sparse set)
CREATE INDEX IF NOT EXISTS idx_mesa_results_tachon_suspicious
    ON mesa_results (tachon_suspicious)
    WHERE tachon_suspicious = TRUE;

-- Partial index: find all rows with missing fields (sparse set)
CREATE INDEX IF NOT EXISTS idx_mesa_results_has_missing_fields
    ON mesa_results (has_missing_fields)
    WHERE has_missing_fields = TRUE;

-- ---------------------------------------------------------------------------
-- Row Level Security (Option A — service-role bypass, no anon access)
-- ---------------------------------------------------------------------------

ALTER TABLE mesa_results ENABLE ROW LEVEL SECURITY;

-- No policies are created.
-- The application server holds the service_role key, which bypasses RLS.
-- Anon / authenticated JWT requests return 0 rows (implicit deny).
