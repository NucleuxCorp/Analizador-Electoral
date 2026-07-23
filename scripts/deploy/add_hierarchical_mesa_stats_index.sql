-- add_hierarchical_mesa_stats_index.sql
-- Companion to add_hierarchical_mesa_stats_rpc.sql: the only existing index on
-- mesa_results is (dept, overall_status) — nothing covers mpio/zona/puesto, so
-- get_hierarchical_mesa_stats_grouped()'s GROUP BY dept, mpio, zona, puesto,
-- overall_status forces a full sequential scan + hash aggregate. On this table's
-- row count that exceeds Postgres's own statement_timeout (error 57014,
-- "canceling statement due to statement timeout") well before gunicorn's
-- 60s request timeout ever kicks in.
--
-- Run this as ITS OWN statement (not pasted together with other SQL) —
-- CONCURRENTLY cannot run inside a transaction block, and it must not be
-- batched with statements that open one.

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_mesa_results_hierarchical
    ON mesa_results (dept, mpio, zona, puesto, overall_status);
