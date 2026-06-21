-- assign_next_crop.sql
-- PL/pgSQL function: collision-free crop assignment using SELECT FOR UPDATE SKIP LOCKED.
--
-- MODE: complete-acta-first + adaptive redundancy (2 -> 3, only on disagreement).
-- The function keeps an annotator on the SAME acta (pdf_path) until every digit
-- of that acta is labeled, then moves to the next acta. This lets us revalidate
-- a mesa's arithmetic as soon as it is fully labeled.
--
-- A crop is eligible in THREE cases:
--   1. annotation_count < 2  →  normal assignment (first or second annotator).
--   2. annotation_count = 2 AND status = 'needs_third'  →  tiebreaker for labels.
--   3. annotation_count = 0 AND status = 'needs_third'  →  tiebreaker for skips
--      (crop skipped by 2 users, needs a 3rd to validate or skip).
--
-- Two agreeing annotators → confirmed (stops at 2, never re-assigned).
-- Two disagreeing → status 'needs_third', re-opens for a third annotator.
-- Three all distinct → status 'disputed' (admin review needed).
--
-- Lease timeout: 60 minutes (was 30). After expiry the crop can be re-assigned.
--
-- Called via supabase.rpc("assign_next_crop", {"p_annotator_id": "<uuid>"})
-- Run after supabase_schema.sql. Safe to re-run (CREATE OR REPLACE).

CREATE OR REPLACE FUNCTION assign_next_crop(p_annotator_id UUID)
RETURNS TEXT
LANGUAGE plpgsql
AS $$
DECLARE
    v_crop_id  TEXT;
    v_pdf_path TEXT;
BEGIN
    -- ── Step 1: is there an acta this annotator already started but not finished? ──
    SELECT c.pdf_path
    INTO   v_pdf_path
    FROM   crops c
    WHERE  ((c.annotation_count < 2 AND c.status NOT IN ('confirmed', 'conflict', 'disputed'))
            OR (c.annotation_count = 2 AND c.status = 'needs_third')
            OR (c.annotation_count = 0 AND c.status = 'needs_third'))
      AND  c.crop_id NOT IN (
               SELECT a.crop_id FROM assignments a
               WHERE  a.annotator_id = p_annotator_id AND a.expires_at > now()
           )
      AND  c.crop_id NOT IN (
               SELECT l.crop_id FROM labels l WHERE l.annotator_id = p_annotator_id
           )
      AND  c.pdf_path IN (
               SELECT DISTINCT c2.pdf_path
               FROM   labels l2
               JOIN   crops  c2 ON l2.crop_id = c2.crop_id
               WHERE  l2.annotator_id = p_annotator_id
           )
    ORDER BY c.field_name ASC, c.digit_index ASC, c.crop_id ASC
    LIMIT 1;

    -- ── Step 2: continue that in-progress acta (locked) ──
    IF v_pdf_path IS NOT NULL THEN
        SELECT c.crop_id
        INTO   v_crop_id
        FROM   crops c
        WHERE  c.pdf_path = v_pdf_path
          AND  ((c.annotation_count < 2 AND c.status NOT IN ('confirmed', 'conflict', 'disputed'))
                OR (c.annotation_count = 2 AND c.status = 'needs_third')
                OR (c.annotation_count = 0 AND c.status = 'needs_third'))
          AND  c.crop_id NOT IN (
                   SELECT a.crop_id FROM assignments a
                   WHERE  a.annotator_id = p_annotator_id AND a.expires_at > now()
               )
          AND  c.crop_id NOT IN (
                   SELECT l.crop_id FROM labels l WHERE l.annotator_id = p_annotator_id
               )
        ORDER BY c.field_name ASC, c.digit_index ASC, c.crop_id ASC
        LIMIT 1
        FOR UPDATE SKIP LOCKED;
    END IF;

    -- ── Step 3: no acta in progress → start a fresh one ──
    IF v_crop_id IS NULL THEN
        SELECT c.crop_id
        INTO   v_crop_id
        FROM   crops c
        WHERE  ((c.annotation_count < 2 AND c.status NOT IN ('confirmed', 'conflict', 'disputed'))
                OR (c.annotation_count = 2 AND c.status = 'needs_third')
                OR (c.annotation_count = 0 AND c.status = 'needs_third'))
          AND  c.crop_id NOT IN (
                   SELECT a.crop_id FROM assignments a
                   WHERE  a.annotator_id = p_annotator_id AND a.expires_at > now()
               )
          AND  c.crop_id NOT IN (
                   SELECT l.crop_id FROM labels l WHERE l.annotator_id = p_annotator_id
               )
        ORDER BY c.priority ASC, c.pdf_path ASC, c.field_name ASC, c.digit_index ASC, c.crop_id ASC
        LIMIT 1
        FOR UPDATE SKIP LOCKED;
    END IF;

    -- No eligible crop found
    IF v_crop_id IS NULL THEN
        RETURN NULL;
    END IF;

    -- Lease the crop to this annotator for 60 minutes.
    INSERT INTO assignments (crop_id, annotator_id, assigned_at, expires_at)
    VALUES (v_crop_id, p_annotator_id, now(), now() + INTERVAL '60 minutes')
    ON CONFLICT DO NOTHING;

    RETURN v_crop_id;
END;
$$;

-- Grant execute to the roles used by supabase-py.
GRANT EXECUTE ON FUNCTION assign_next_crop(UUID) TO anon;
GRANT EXECUTE ON FUNCTION assign_next_crop(UUID) TO authenticated;
GRANT EXECUTE ON FUNCTION assign_next_crop(UUID) TO service_role;
