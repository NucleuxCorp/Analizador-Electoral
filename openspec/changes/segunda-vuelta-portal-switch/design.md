# Design: Segunda Vuelta Portal Switch

## Context

The labeling portal (`src/modules/labeler/`) is a Flask app served by Gunicorn on Railway. The current code is hard-wired to primera-vuelta data: the `exporter.py` uses primera-vuelta field constants (`CANDIDATE_ROWS_P0/P1`, `NIVELACION`, `TOTALS`), the Supabase tables have no round discriminator, and there is no maintenance mode or cloud-audit tooling. We must transition the same deployment to segunda vuelta with zero data loss and a reversible archive — all within a 5-PR chain (≤400 lines each), strict TDD.

The change touches 4 layers: Flask routing (maintenance guard), Supabase schema (vuelta column), a cloud-audit CLI, and a segunda-specific crop exporter. The existing ~1,600-line `server.py` is NOT duplicated; instead, an env-driven `MODULE` switch selects data paths and a `before_request` guard gates traffic during cutover.

## Goals & Non-Goals

**Goals**: maintenance guard with whitelist, `vuelta` column on 3 tables, env-driven module switch, cloud-to-local audit script with `--incremental` support, segunda crop exporter with `vuelta=segunda` tagging, git tag + branch archive.

**Non-Goals**: segunda PDF extractor (owned by change `segunda-vuelta`), layout calibration, new Supabase project, `server.py` duplication.

## Architecture Decisions

| Decision | Option A (chosen) | Option B (rejected) | Rationale |
|---|---|---|---|
| Guard hook placement | Second `before_request` after `_assign_request_id` | Expand existing `_assign_request_id` | Separation of concerns; maintenance guard is a distinct cross-cutting concern. Flask executes `before_request` handlers in registration order. |
| MODULE env reading | `create_app()` reads `os.environ["MODULE"]`, validates, stores in `app.config["MODULE"]` | `wsgi.py` resolves MODULE→labels_dir before calling `create_app()` | `create_app()` already reads env vars internally (SUPABASE_URL, SECRET_KEY); consistency wins. `wsgi.py` stays simple. |
| MODULE default | Fallback to `primera` when unset or invalid | Abort startup on unset | Safety: defaulting to primera prevents serving unaudited segunda data. Only `MODULE=segunda` (exact string) activates segunda paths. |
| Segunda exporter location | New `scripts/export_crops_segunda.py` | Extend `src/modules/labeler/export_supabase.py` | Input shape differs (segunda OCR JSONL vs primera scan output). New script reuses `export_supabase.{seed_crops_table, upload_crops_to_storage}` via import — no code duplication. |
| Audit mock strategy | Test imports `scripts.audit_cloud_to_local`, mocks `db._client()` | Integration test against real Supabase | Audit must be importable without network. Mock at `db._client()` boundary is the same pattern used by existing `test_db.py`. |
| Template subdir | No template subdirectories; landing page served from `templates/maintenance.html`; work templates unchanged | `templates/primera/`, `templates/segunda/` subdirs | The work UI (label.html, home.html) is identical across rounds — only the landing page differs. Simpler is better. |
| Supabase migration delivery | New `scripts/supabase_schema_v2.sql` with `ALTER TABLE ... ADD COLUMN vuelta TEXT NOT NULL DEFAULT 'primera'` | Inline DDL in db.py or migration runner | SQL file is the existing convention (`supabase_schema.sql`). Adding a v2 preserves the base schema as reference. |

## Design

### 1. Maintenance guard (PR-A)

**Files**: modify `server.py` (new `before_request`), new `templates/maintenance.html`, new `tests/labeler/test_maintenance.py`.

The guard reads `os.environ.get("MAINTENANCE_MODE", "").lower() == "true"` at request time (thread-safe in gunicorn workers — env is process-level after fork). When active, it intercepts after `_assign_request_id` and checks `request.endpoint` against a whitelist:

```python
_MAINTENANCE_WHITELIST = {
    "status",            # GET /status
    "auth_register_get", # GET /auth/register
    "auth_register_post",# POST /auth/register
}
```

Non-whitelisted routes: if `Accept: application/json` → `jsonify({"error":"maintenance","message":"..."}), 503`; otherwise → `render_template("maintenance.html"), 200`.

**`templates/maintenance.html`**: AIDA copy in Spanish (Colombian product), Loom embed from `home.html` line 90, CSS inline (no external static assets). Contains an `<h1>` for SEO/accessibility per resolved product decision #3. Structure: `<h1>Verificación Ciudadana de Actas</h1>` → Attention (transition notice) → Interest (why it matters) → Desire (what's coming) → Action (register link + Loom tutorial).

**Tests**: 7 scenarios mapped — `test_status_200_under_maintenance`, `test_work_route_returns_landing`, `test_json_client_gets_503`, `test_register_allowed_under_maintenance`, `test_login_blocked_under_maintenance`, `test_guard_off_by_default`, `test_landing_has_h1`.

### 2. Database schema + MODULE switch (PR-B)

**Files**: new `scripts/supabase_schema_v2.sql`, modify `db.py`, modify `server.py` (create_app validation), new `tests/labeler/test_module_switch.py`, new `tests/labeler/test_vuelta_tagging.py`.

**DDL migration** (`supabase_schema_v2.sql`):
```sql
ALTER TABLE crops ADD COLUMN IF NOT EXISTS vuelta TEXT NOT NULL DEFAULT 'primera';
ALTER TABLE labels ADD COLUMN IF NOT EXISTS vuelta TEXT NOT NULL DEFAULT 'primera';
ALTER TABLE assignments ADD COLUMN IF NOT EXISTS vuelta TEXT NOT NULL DEFAULT 'primera';
```

**`db.py` additions**: 
- Module-level `_active_vuelta` resolved at import time from `os.environ.get("MODULE", "primera")`
- New helpers: `list_crops_by_vuelta(vuelta)`, `list_labels_by_vuelta(vuelta)`, `create_label_with_vuelta(crop_id, annotator_id, label_human, amended, is_admin, vuelta)`
- All existing INSERTs add `"vuelta": _active_vuelta` to their row dicts
- `assign_next_crop` RPC update: add `WHERE vuelta = _active_vuelta` filter in the server-side function (requires a v2 RPC definition in `supabase_schema_v2.sql`)

**MODULE switch** (in `create_app()` after env validation, before route registration):
```python
_module = os.environ.get("MODULE", "").strip().lower() or "primera"
if _module not in ("primera", "segunda"):
    raise RuntimeError(f"Invalid MODULE={_module!r}. Must be 'primera' or 'segunda'.")
app.config["MODULE"] = _module
```
When `MODULE=segunda`, default `LABELS_DIR` becomes `data/labels_segunda` (overridable by explicit `LABELS_DIR` env).

**wsgi.py**: No changes needed — `create_app()` reads MODULE internally. Backward-compatible: absent MODULE → primera.

### 3. Cloud audit script (PR-C)

**File**: new `scripts/audit_cloud_to_local.py`, new `tests/labeler/test_audit_cloud_to_local.py`.

CLI: `python scripts/audit_cloud_to_local.py [--incremental] [--output primera_backup.jsonl] [--vuelta primera]`.

Four-layer comparison:
1. **Storage**: list bucket via `_client().storage.from_("crops").list()` → compare against `data/labels/crops/*.png` filenames
2. **crops table**: `SELECT * FROM crops WHERE vuelta=$vuelta` → compare against `index.jsonl`
3. **labels table**: export all rows → write web-only rows (no local counterpart) to `{output}` JSONL
4. **assignments table**: count check (leases are ephemeral — warn on count > 0, don't block)

Exit codes: 0 = clean, 1 = mismatches found, 2 = unrecoverable error.

**Mocking for tests**: the script imports `from src.modules.labeler.db import _client`. Tests patch `db._client` with a MagicMock returning pre-configured data. Same pattern as `test_db.py` line 27.

`--incremental`: writes a `.audit_checkpoint` file with `{layer, last_id}`; skip already-verified items on re-run.

### 4. Segunda crop exporter (PR-D)

**File**: new `scripts/export_crops_segunda.py`, new `tests/labeler/test_exporter_segunda.py`.

Pipeline: read `Segunda Vuelta/src/layout_sv.py` → for each PDF in `data/pdfs_segunda/`, render pages (reuse `exporter.render_pdf_pages`), crop digits using layout coordinates, run OCR (reuse `SegmentedEngine`), write PNGs + `index.jsonl` with `CropRecord` contract → call `export_supabase.seed_crops_table()` and `export_supabase.upload_crops_to_storage()` with `vuelta='segunda'` tagged on every row.

Key reuse: `export_supabase.seed_crops_table(index_path, crops_dir)` and `export_supabase.upload_crops_to_storage(crops_dir)` — zero code duplication of upload logic. The exporter only handles the input→CropRecord transformation.

### 5. Git archive (PR-E)

**File**: new `scripts/archive_primera_vuelta.sh`, new `tests/labeler/test_primera_archive.py` (integration tier).

```bash
git tag -a primera-vuelta-v1 -m "Archive: primera vuelta portal state before segunda switch"
git branch primera-vuelta
```

Does NOT push. Tests: `test_tag_and_branch_exist`, `test_label_runs_from_archive`, `test_no_folder_rename`. Integration tier — these tests run the script and check `git tag -l` / `git branch -l` output.

## Data Model

```
crops                   labels                  assignments
┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│ crop_id      PK   │───<│ crop_id      FK  │    │ crop_id      PK  │
│ pdf_path          │    │ annotator_id     │    │ annotator_id PK  │
│ field_name        │    │ label_human      │    │ assigned_at      │
│ digit_index       │    │ amended          │    │ expires_at       │
│ label_ocr         │    │ is_admin_res.    │    │ vuelta ★         │
│ confidence        │    │ ts               │    └──────────────────┘
│ priority          │    │ vuelta ★         │
│ storage_url       │    └──────────────────┘
│ annotation_count  │
│ confirmed_label   │
│ status            │
│ source_url        │
│ full_cell_crop_id │
│ vuelta ★          │    ★ = new column
└──────────────────┘    TEXT NOT NULL DEFAULT 'primera'
                        CHECK (vuelta IN ('primera', 'segunda'))
```

## Test Strategy

| Tier | New test file | Tests target |
|---|---|---|
| Unit | `tests/labeler/test_maintenance.py` | 7 scenarios: guard whitelist, JSON 503, off-by-default, h1 presence |
| Unit | `tests/labeler/test_module_switch.py` | 3 scenarios: segunda routing, primera fallback, invalid abort |
| Unit | `tests/labeler/test_vuelta_tagging.py` | 3 scenarios: migration DDL, backfill, query filtering |
| Unit | `tests/labeler/test_audit_cloud_to_local.py` | 4 scenarios: clean exit, mismatch exit, backup export, incremental skip |
| Unit | `tests/labeler/test_exporter_segunda.py` | 3 scenarios: segunda tag, portal compatibility, rerun on layout change |
| Integration | `tests/labeler/test_primera_archive.py` | 3 scenarios: tag+branch exist, labeler runs from archive, no folder rename |

No existing test files are modified. Strict TDD: tests merge WITH their implementation in each PR.

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Guard blocks `/status` → Railway kill | Whitelist tested first; `test_status_200_under_maintenance` runs before any other guard test |
| Supabase RLS breaks with new `vuelta` column | `service_role` key bypasses RLS; same pattern used in existing `db.py` line 35 |
| MODULE env not set → segunda code path unreachable until Railway env update | `wsgi.py` reads env at import time (gunicorn preload); Railway env changes trigger restart |
| Existing `assign_next_crop` RPC unaware of `vuelta` | v2 RPC in `supabase_schema_v2.sql` with `WHERE vuelta = p_vuelta`; old RPC stays functional for primera queries |
| Segunda layout churn forces re-export | Exporter is idempotent (skips existing PNGs); rerun is cheap |

## Open Questions for Human

None — all 8 product decisions are resolved.
