# Supabase Deployment SQL — Execution Order

All files are idempotent-safe unless noted.

## Order

### 1. Baseline (new environment, once)
| File | Type | Notes |
|------|------|-------|
| `supabase_schema.sql` | **Baseline** | Run first. Creates core tables. |
| `supabase_schema_v2.sql` | **Baseline** | DEPENDS ON `supabase_schema.sql` — must run after. Hard ordering required. |

### 2. Tables / RPC installs
| File | Type | Notes |
|------|------|-------|
| `supabase_schema_mesa_results.sql` | Installation | Creates mesa_results table + indexes |
| `supabase_schema_mesa_semaphore_rpc.sql` | Installation | Creates semaphore RPC functions |
| `assign_next_crop.sql` | Installation | Creates crop assignment function |
| `fix_assign_next_crop_v2.sql` | **Replacement** | Replaces `assign_next_crop` function. Run AFTER `assign_next_crop.sql`. |

### 3. Idempotent additive migrations
Safe to re-run in any order:

| File | Type | Notes |
|------|------|-------|
| `add_reports_table.sql` | Migration | Creates `reports` table |
| `add_reports_tables.sql` | Migration | Creates `fraud_marks` + `feedback_marks` tables |
| `add_hidden_column.sql` | Migration | Adds hidden column to existing table |
| `add_mesa_key_to_reports.sql` | Migration | Adds mesa_key column to reports |
| `add_mesa_report_type.sql` | Migration | Adds report_type column |

### 4. Security
| File | Type | Notes |
|------|------|-------|
| `rls_hardening.sql` | Security | Row-Level Security policies. Run LAST. |
