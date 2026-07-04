-- supabase_schema_mesa_semaphore_rpc.sql
-- RPC for Slice 2 of the mesa-semaphore feature.
-- Run once in the Supabase SQL Editor after applying the mesa_key_mr column
-- migration in supabase_schema.sql.
--
-- Returns per-mesa review state so the Flask app can classify each mesa as
-- 🟡 EN REVISIÓN (annotation_count >= 1, not all priority crops confirmed) or
-- 🟢 REVISADA (all priority <= 1 crops have status = 'confirmed').
--
-- Only ALERTA mesas (mesa_results.overall_status != 'clean') are included.
-- The function is SECURITY DEFINER so it bypasses RLS; call it only from
-- the service-role Flask path, never from the browser.

CREATE OR REPLACE FUNCTION get_review_semaphore_by_dept(p_dept TEXT DEFAULT NULL)
RETURNS TABLE (
    mesa_key_mr           TEXT,
    dept                  TEXT,
    annotation_count_sum  BIGINT,
    priority_total        BIGINT,
    priority_confirmed    BIGINT,
    candidato_sum         BIGINT,
    total_urna_val        INTEGER
)
LANGUAGE sql
STABLE
SECURITY DEFINER
AS $$
  SELECT
    c.mesa_key_mr,
    LEFT(c.mesa_key_mr, 2)                          AS dept,
    SUM(c.annotation_count)                          AS annotation_count_sum,
    COUNT(*) FILTER (WHERE c.priority <= 1)          AS priority_total,
    COUNT(*) FILTER (
        WHERE c.priority <= 1
          AND c.status = 'confirmed'
    )                                                AS priority_confirmed,
    SUM(
      CASE WHEN c.field_name LIKE 'candidato_%'
            AND c.status = 'confirmed'
            AND c.confirmed_label ~ '^\d+$'
           THEN c.confirmed_label::INTEGER ELSE 0 END
    )                                                AS candidato_sum,
    MAX(
      CASE WHEN c.field_name = 'total_urna'
            AND c.status = 'confirmed'
            AND c.confirmed_label ~ '^\d+$'
           THEN c.confirmed_label::INTEGER ELSE NULL END
    )                                                AS total_urna_val
  FROM crops c
  INNER JOIN mesa_results mr ON mr.mesa_key = c.mesa_key_mr
  WHERE c.mesa_key_mr IS NOT NULL
    AND mr.overall_status != 'clean'
    AND (p_dept IS NULL OR LEFT(c.mesa_key_mr, 2) = p_dept)
  GROUP BY c.mesa_key_mr
  ORDER BY c.mesa_key_mr;
$$;
