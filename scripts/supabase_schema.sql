-- supabase_schema.sql
-- Authoritative DDL for the web-portal labeling system.
-- Run once against your Supabase project (SQL Editor or psql).
--
-- Tables:
--   crops        — items to be annotated
--   labels       — annotation records (one per annotator per crop)
--   assignments  — active lease: annotator → crop (expires in 30 min)
--
-- Design reference: sdd/web-portal/spec (capability: shared-labeling-queue)

-- ---------------------------------------------------------------------------
-- crops
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS crops (
    crop_id          TEXT        PRIMARY KEY,
    pdf_path         TEXT        NOT NULL,
    field_name       TEXT        NOT NULL,
    digit_index      INTEGER     NOT NULL,
    label_ocr        TEXT,
    confidence       FLOAT,
    priority         INTEGER     DEFAULT 2,
    storage_url      TEXT,
    annotation_count INTEGER     DEFAULT 0,
    confirmed_label  TEXT,
    status           TEXT        DEFAULT 'pending',  -- pending | needs_third | confirmed | conflict
    source_url       TEXT,       -- public Registraduria PDF URL ("Ver acta" online)
    full_cell_crop_id TEXT,      -- crop_id of the full 3-digit cell image (context)
    created_at       TIMESTAMPTZ DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- labels
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS labels (
    id                   BIGSERIAL   PRIMARY KEY,
    crop_id              TEXT        REFERENCES crops(crop_id),
    annotator_id         UUID        NOT NULL,
    label_human          TEXT        NOT NULL,
    amended              BOOLEAN     DEFAULT FALSE,
    is_admin_resolution  BOOLEAN     DEFAULT FALSE,
    ts                   TIMESTAMPTZ DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- assignments
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS assignments (
    crop_id       TEXT        REFERENCES crops(crop_id),
    annotator_id  UUID        NOT NULL,
    assigned_at   TIMESTAMPTZ DEFAULT now(),
    expires_at    TIMESTAMPTZ,
    PRIMARY KEY (crop_id, annotator_id)
);

-- ---------------------------------------------------------------------------
-- Indexes (performance)
-- ---------------------------------------------------------------------------
-- Fast lookup of crops eligible for assignment
CREATE INDEX IF NOT EXISTS idx_crops_status_count
    ON crops (status, annotation_count, priority, crop_id);

-- Fast lookup of labels for a given crop
CREATE INDEX IF NOT EXISTS idx_labels_crop_id
    ON labels (crop_id);

-- Fast lookup of active assignments per annotator
CREATE INDEX IF NOT EXISTS idx_assignments_annotator_expires
    ON assignments (annotator_id, expires_at);

-- Fast lookup for expiry cleanup
CREATE INDEX IF NOT EXISTS idx_assignments_expires_at
    ON assignments (expires_at);
