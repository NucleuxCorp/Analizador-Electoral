-- fix_assign_next_crop_v2.sql
-- Two fixes:
--   1. Exclude crops the annotator has already labeled (any status)
--   2. Remove the FOR UPDATE OF c alias syntax that breaks on some supabase versions

CREATE OR REPLACE FUNCTION assign_next_crop_v2(p_annotator_id UUID, p_vuelta TEXT)
RETURNS TEXT
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_crop_id TEXT;
BEGIN
    SELECT c.crop_id INTO v_crop_id
    FROM crops c
    WHERE c.vuelta = p_vuelta
      AND c.status IN ('pending', 'needs_third')
      AND c.annotation_count < 2
      AND c.digit_index >= 0
      AND NOT EXISTS (
          SELECT 1
          FROM assignments a
          WHERE a.crop_id = c.crop_id
            AND a.annotator_id = p_annotator_id
            AND a.vuelta = p_vuelta
            AND a.expires_at > now()
      )
      AND NOT EXISTS (
          SELECT 1
          FROM labels l
          WHERE l.crop_id = c.crop_id
            AND l.annotator_id = p_annotator_id
            AND l.vuelta = p_vuelta
      )
    ORDER BY c.priority ASC, c.crop_id ASC
    LIMIT 1
    FOR UPDATE SKIP LOCKED;

    IF v_crop_id IS NULL THEN
        RETURN NULL;
    END IF;

    INSERT INTO assignments (crop_id, annotator_id, vuelta, assigned_at, expires_at)
    VALUES (v_crop_id, p_annotator_id, p_vuelta, now(), now() + interval '30 minutes')
    ON CONFLICT (crop_id, annotator_id) DO UPDATE
        SET assigned_at = now(),
            expires_at  = now() + interval '30 minutes',
            vuelta      = EXCLUDED.vuelta;

    RETURN v_crop_id;
END;
$$;

GRANT EXECUTE ON FUNCTION assign_next_crop_v2(UUID, TEXT) TO anon;
GRANT EXECUTE ON FUNCTION assign_next_crop_v2(UUID, TEXT) TO authenticated;
GRANT EXECUTE ON FUNCTION assign_next_crop_v2(UUID, TEXT) TO service_role;
