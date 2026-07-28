-- add_mesa_stats_rpc.sql
-- RPC for get_mesa_stats() / get_public_stats() in db.py: both currently reach
-- _fetch_mesa_results_batched(), which pages through the entire mesa_results
-- table (~123k rows, ~123 OFFSET pages) and aggregates in Python. This is the
-- largest disk-I/O consumer on the project per pg_stat_statements (2026-07-28,
-- ~24.5 GB shared-block reads) and exhausted the Supabase Disk IO burst
-- budget, throttling the instance and crash-looping gunicorn.
--
-- This function does the GROUP BY in Postgres and returns one row per
-- (dept, overall_status) combination instead of one row per mesa — at most
-- 34 depts x 6 statuses = 204 rows, in a single round trip.
--
-- p_dept mirrors get_review_semaphore_by_dept(p_dept TEXT DEFAULT NULL):
-- NULL returns all departments, a non-NULL value filters server-side.
--
-- No new index required: idx_mesa_results_dept_status (dept, overall_status)
-- already covers this GROUP BY (confirmed via live EXPLAIN (ANALYZE, BUFFERS)
-- against the production Supabase project, sdd/mesa-stats-server-aggregation
-- design-verification, 2026-07-28).
--
-- SECURITY DEFINER so it bypasses RLS; call only from the service-role Flask path.

CREATE OR REPLACE FUNCTION get_mesa_stats_grouped(p_dept TEXT DEFAULT NULL)
RETURNS TABLE (
    dept            TEXT,
    overall_status  TEXT,
    n               BIGINT
)
LANGUAGE sql
STABLE
SECURITY DEFINER
AS $$
  SELECT dept, overall_status, COUNT(*) AS n
  FROM mesa_results
  WHERE p_dept IS NULL OR dept = p_dept
  GROUP BY dept, overall_status;
$$;
