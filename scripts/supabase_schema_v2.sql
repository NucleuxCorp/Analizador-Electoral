-- supabase_schema_v2.sql
-- Migration for the segunda-vuelta portal switch (PR-B).
-- Adds vuelta tagging to crops, labels, and assignments, and introduces
-- assign_next_crop_v2(p_vuelta) so each voting round has its own queue.
--
-- Run after scripts/supabase_schema.sql has been applied.

-- ---------------------------------------------------------------------------
-- 1. Add vuelta column to existing tables
-- ---------------------------------------------------------------------------
ALTER TABLE crops
    ADD COLUMN vuelta TEXT NOT NULL DEFAULT 'primera'
    CHECK (vuelta IN ('primera', 'segunda'));

ALTER TABLE labels
    ADD COLUMN vuelta TEXT NOT NULL DEFAULT 'primera'
    CHECK (vuelta IN ('primera', 'segunda'));

ALTER TABLE assignments
    ADD COLUMN vuelta TEXT NOT NULL DEFAULT 'primera'
    CHECK (vuelta IN ('primera', 'segunda'));

-- ---------------------------------------------------------------------------
-- 2. Indexes for vuelta-scoped lookups
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_crops_vuelta_status
    ON crops (vuelta, status, annotation_count, priority, crop_id);

CREATE INDEX IF NOT EXISTS idx_labels_vuelta_crop_id
    ON labels (vuelta, crop_id);

CREATE INDEX IF NOT EXISTS idx_assignments_vuelta_annotator_expires
    ON assignments (vuelta, annotator_id, expires_at);

-- ---------------------------------------------------------------------------
-- 3. Replace the assignment RPC with a vuelta-aware version
-- ---------------------------------------------------------------------------
DROP FUNCTION IF EXISTS assign_next_crop(UUID);

CREATE OR REPLACE FUNCTION assign_next_crop_v2(p_annotator_id UUID, p_vuelta TEXT)
RETURNS TEXT
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_crop_id TEXT;
BEGIN
    -- Pick the highest-priority crop for this round that is not already
    -- assigned to another annotator and has fewer than 2 labels.
    SELECT c.crop_id INTO v_crop_id
    FROM crops c
    WHERE c.vuelta = p_vuelta
      AND c.status IN ('pending', 'needs_third')
      AND c.annotation_count < 2
      AND NOT EXISTS (
          SELECT 1
          FROM assignments a
          WHERE a.crop_id = c.crop_id
            AND a.vuelta = p_vuelta
            AND a.expires_at > now()
      )
    ORDER BY c.priority ASC, c.crop_id ASC
    FOR UPDATE OF c SKIP LOCKED
    LIMIT 1;

    IF v_crop_id IS NULL THEN
        RETURN NULL;
    END IF;

    -- Create or refresh the lease for this annotator/crop.
    INSERT INTO assignments (crop_id, annotator_id, vuelta, assigned_at, expires_at)
    VALUES (v_crop_id, p_annotator_id, p_vuelta, now(), now() + interval '30 minutes')
    ON CONFLICT (crop_id, annotator_id) DO UPDATE
        SET assigned_at = now(),
            expires_at  = now() + interval '30 minutes',
            vuelta      = EXCLUDED.vuelta;

    RETURN v_crop_id;
END;
$$;
