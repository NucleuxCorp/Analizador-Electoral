-- add_hierarchical_mesa_stats_rpc.sql
-- RPC to fix the /mesas timeout: get_hierarchical_mesa_stats() in db.py used to
-- page through mesa_results 1000 rows at a time and aggregate in Python. On a
-- cold cache (5-min TTL) that national scan is dozens of sequential round trips
-- to Supabase, which can exceed the gunicorn worker timeout and 500 the request.
--
-- This function does the GROUP BY in Postgres and returns one row per
-- (dept, mpio, zona, puesto, overall_status) combination — a few thousand rows
-- instead of one row per mesa, in a single round trip.
--
-- SECURITY DEFINER so it bypasses RLS; call only from the service-role Flask path.

CREATE OR REPLACE FUNCTION get_hierarchical_mesa_stats_grouped()
RETURNS TABLE (
    dept            TEXT,
    mpio            TEXT,
    zona            TEXT,
    puesto          TEXT,
    overall_status  TEXT,
    n               BIGINT
)
LANGUAGE sql
STABLE
SECURITY DEFINER
AS $$
  SELECT dept, mpio, zona, puesto, overall_status, COUNT(*) AS n
  FROM mesa_results
  GROUP BY dept, mpio, zona, puesto, overall_status;
$$;
