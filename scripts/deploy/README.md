# Supabase deployment SQL

**Production Supabase only.** Run files from this folder in the Supabase SQL Editor.

Lab scripts, collectors, and diagnostics live on branch `develop` under `scripts/` (not here).

## Branch layout

| Branch | `scripts/` |
|--------|------------|
| `develop` | Full lab + this `deploy/` folder |
| `production` | **Only** `deploy/` SQL + `upload_mesa_results.py` + `verify_transversal_panel.py` |

Never copy loose `*.sql` into `scripts/` root on `production`.

## Execution order

All files are idempotent unless noted.

### 1. Baseline (new environment, once)

| File | Type | Notes |
|------|------|-------|
| `supabase_schema.sql` | **Baseline** | Run first. Core tables. |
| `supabase_schema_v2.sql` | **Baseline** | After `supabase_schema.sql`. |

### 2. Tables / RPC

| File | Type | Notes |
|------|------|-------|
| `supabase_schema_mesa_results.sql` | Installation | `mesa_results` table |
| `supabase_schema_mesa_semaphore_rpc.sql` | Installation | Semaphore RPC |
| `assign_next_crop.sql` | Installation | Crop assignment function |
| `fix_assign_next_crop_v2.sql` | **Replacement** | After `assign_next_crop.sql` |

### 3. Additive migrations

Safe to re-run:

| File | Type | Notes |
|------|------|-------|
| `add_reports_table.sql` | Migration | `reports` table |
| `add_reports_tables.sql` | Migration | `fraud_marks` + `feedback_marks` |
| `add_hidden_column.sql` | Migration | Hidden column |
| `add_mesa_key_to_reports.sql` | Migration | `mesa_key` on reports |
| `add_mesa_report_type.sql` | Migration | `report_type` column |
| `fix_mesa_reports_view.sql` | Migration | `mesa_reports_view` + backfill |
| `add_mesa_source_status.sql` | Migration | `source_status` JSONB + GIN index on `mesa_results` |
| `add_mesa_stats_rpc.sql` | Installation | `get_mesa_stats_grouped(p_dept)` RPC — server-side GROUP BY for `get_mesa_stats()`/`get_public_stats()`, replaces the full-table Python scan (sdd/mesa-stats-server-aggregation). No new index needed — covered by existing `idx_mesa_results_dept_status` |

### 4. Panel transversal (`/admin/transversal`)

After `mesa_results` is populated:

| File | Type | Notes |
|------|------|-------|
| `add_transversal_review_decisions.sql` | Migration | ✓/✗ decisions |
| `add_transversal_review_reports.sql` | Migration | 📝 structured reports |

### 5. Security

| File | Type | Notes |
|------|------|-------|
| `rls_hardening.sql` | Security | Run last |