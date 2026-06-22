# Tasks: Segunda Vuelta Portal Switch

## PR-A: Maintenance Guard + Landing Page (target: main)

### Task A1: [x] [Test] Maintenance guard — whitelist and content negotiation
- Files: `tests/labeler/test_maintenance_mode.py`
- Strict TDD: write test FIRST
- Description: Create test file with 8 scenarios: `test_status_200_under_maintenance`, `test_work_route_returns_landing`, `test_json_client_gets_503`, `test_register_allowed_under_maintenance`, `test_login_blocked_under_maintenance`, `test_guard_off_by_default`, `test_landing_has_h1`, `test_landing_reuses_existing_tutorial_embed`. Use `prod_app` fixture pattern from `test_routes.py`; set `MAINTENANCE_MODE=true` via `monkeypatch.setenv`. Assert HTML response renders `maintenance.html` (503), JSON response returns 503 with `{"ok":false,"error":"maintenance_mode"}`, whitelisted routes pass through, response carries `X-Request-ID`.
- Test: `pytest tests/labeler/test_maintenance_mode.py -xvs` (RED → 5 failed, 3 passed; all new guard scenarios failed)
- Estimated lines: +130

### Task A2: [x] [Test+Impl] Maintenance guard implementation + landing page
- Files: `src/modules/labeler/server.py`, `src/modules/labeler/templates/maintenance.html`
- Strict TDD: implement to make A1 GREEN
- Description: In `server.py`, after `_assign_request_id` (line ~501), register a second `@app.before_request` guard. Define `_MAINTENANCE_WHITELIST = {"static", "status_view", "auth_register_get", "auth_register_post"}`. When `os.environ.get("MAINTENANCE_MODE","").lower() == "true"` and `request.endpoint not in _MAINTENANCE_WHITELIST`: if `request.is_json or request.path.startswith("/api/") or request.accept_mimetypes.best == "application/json"` → `jsonify({"ok":False,"error":"maintenance_mode"}), 503`; else → `render_template("maintenance.html"), 503`. Create `templates/maintenance.html` — AIDA structure with `<h1>Verificación Ciudadana de Actas</h1>`, inline CSS, Loom iframe embed reused from `home.html` line 90 (`https://www.loom.com/embed/40f6d1a46315479f969c1f6296b1624b`), register CTA link.
- Test: `pytest tests/labeler/test_maintenance_mode.py -xvs` (all 8 GREEN)
- Estimated lines: +160 (server.py ~40, maintenance.html ~120)

**PR-A total: ~290 lines** ✅

---

## PR-B: Schema Vuelta + Module Switch (target: main)

### Task B1: [x] [Test] Vuelta tagging — migration DDL and query filtering
- Files: `tests/labeler/test_vuelta_tagging.py`
- Strict TDD: write test FIRST
- Description: 3 scenarios: `test_migration_adds_vuelta_column` (verify DDL file contains ALTER TABLE for crops/labels/assignments with CHECK constraint), `test_existing_rows_backfilled_primera` (mock `db._client()`, verify DEFAULT 'primera' in DDL), `test_queries_filter_by_active_vuelta` (mock supabase client, call new `list_crops_by_vuelta("segunda")`, verify `.eq("vuelta","segunda")` in query chain). Use `_chain()` helper pattern from `test_db.py`.
- Test: `pytest tests/labeler/test_vuelta_tagging.py -xvs` (3 RED)
- Estimated lines: +90

### Task B2: [x] [Test+Impl] Schema v2 migration SQL
- Files: `scripts/supabase_schema_v2.sql`
- Strict TDD: implement to make B1 migration tests GREEN
- Description: Create DDL file with: `ALTER TABLE crops ADD COLUMN IF NOT EXISTS vuelta TEXT NOT NULL DEFAULT 'primera'` (same for labels, assignments), `ALTER TABLE ... ADD CONSTRAINT chk_vuelta CHECK (vuelta IN ('primera','segunda'))` for each table. Include `CREATE OR REPLACE FUNCTION assign_next_crop_v2(p_annotator_id UUID, p_vuelta TEXT)` — copy of existing `assign_next_crop` with added `WHERE c.vuelta = p_vuelta` filter in all 3 SELECT steps. Grant execute to anon/authenticated/service_role.
- Test: `pytest tests/labeler/test_vuelta_tagging.py::test_migration_adds_vuelta_column` GREEN
- Estimated lines: +85

### Task B3: [x] [Test+Impl] db.py vuelta-aware helpers
- Files: `src/modules/labeler/db.py`
- Strict TDD: implement to make B1 query tests GREEN
- Description: Add module-level `_active_vuelta = os.environ.get("MODULE","primera").strip().lower() or "primera"`. Add helpers: `list_crops_by_vuelta(vuelta)`, `list_labels_by_vuelta(vuelta)`, `list_assignments_by_vuelta(vuelta)`, `create_label_with_vuelta(crop_id, annotator_id, label_human, amended, is_admin, vuelta)`. Modify existing `write_label` INSERT to include `"vuelta": _active_vuelta` in the row dict. Modify `assign_next_crop` to call `assign_next_crop_v2` RPC with `p_vuelta=_active_vuelta`.
- Test: `pytest tests/labeler/test_vuelta_tagging.py -xvs` (all 3 GREEN)
- Estimated lines: +65

### Task B4: [x] [Test] Module switch — env validation and path resolution
- Files: `tests/labeler/test_module_switch.py`
- Strict TDD: write test FIRST
- Description: 3 scenarios: `test_segunda_module_resolves_segunda_paths` (set `MODULE=segunda`, create `data/labels_segunda/crops/index.jsonl`, verify `app.config["MODULE"] == "segunda"` and labels_dir resolves correctly), `test_unset_module_defaults_primera` (unset MODULE, verify `app.config["MODULE"] == "primera"`), `test_invalid_module_aborts_startup` (set `MODULE=tercera`, verify `create_app()` raises `RuntimeError`).
- Test: `pytest tests/labeler/test_module_switch.py -xvs` (3 RED)
- Estimated lines: +75

### Task B5: [x] [Test+Impl] Module switch in create_app()
- Files: `src/modules/labeler/server.py`
- Strict TDD: implement to make B4 GREEN
- Description: In `create_app()`, after mode detection (line ~490), add: `_module = os.environ.get("MODULE","").strip().lower() or "primera"`; `if _module not in ("primera","segunda"): raise RuntimeError(...)`. Store `app.config["MODULE"] = _module`. When `_module == "segunda"` and `LABELS_DIR` env is unset, default `_labels_dir` to `data/labels_segunda`. Pass through to existing `index_path`/`labels_dir` resolution.
- Test: `pytest tests/labeler/test_module_switch.py -xvs` (all 3 GREEN)
- Estimated lines: +30

**PR-B total: ~345 lines** ✅

---

## PR-C: Cloud-to-Local Audit Script (target: main)

### Task C1: [x] [Test] Audit script — clean, mismatch, backup, incremental
- Files: `tests/labeler/test_audit_cloud_to_local.py`
- Strict TDD: write test FIRST
- Description: 4 scenarios: `test_audit_exits_zero_on_match`, `test_audit_exits_non_zero_on_mismatch`, `test_audit_backup_jsonl_format`, `test_audit_incremental_flag`. Mock `db.supabase` at `src.modules.labeler.db.supabase` boundary; use `tmp_path` for local labels dir and checkpoint/output files.
- Test: `pytest tests/labeler/test_audit_cloud_to_local.py -xvs` (4 RED → GREEN)
- Estimated lines: +160

### Task C2: [x] [Test+Impl] Audit script implementation
- Files: `scripts/audit_cloud_to_local.py`, `scripts/__init__.py`
- Strict TDD: implement to make C1 GREEN
- Description: argparse CLI with `--incremental`, `--output` (default `primera_backup.jsonl`), `--bucket` (default `crops`), `--labels-dir` (default `data/labels`), `--vuelta` (default `primera`). 4 layers: Storage vs local PNGs; crops table vs `crops/index.jsonl`; labels table vs `labels/manifest.jsonl`; assignments count check. Mismatches written as JSONL `{layer, kind, id, reason}`. `--incremental` reads/writes `.audit_checkpoint` JSON with `{verified: [...]}`. Exit 0/1/2.
- Test: `pytest tests/labeler/test_audit_cloud_to_local.py -xvs` (all 4 GREEN)
- Estimated lines: +190

**PR-C total: ~350 lines** ✅

---

## PR-D: Cloud-to-Local Label Backup (target: main)

### Task D1: [x] [Test] Download labels + crops index from Supabase
- Files: `tests/labeler/test_download_labels.py`
- Strict TDD: write test FIRST
- Description: 3 scenarios: `test_download_labels_writes_manifest` (mock `db.supabase`, call `download_labels`, assert `labels/manifest.jsonl` written with expected `crop_id` and `annotator_id` fields), `test_download_labels_paginates` (mock two pages, assert all rows written), `test_download_crops_index` (mock crops table, assert `crops/index.jsonl` written and deduplicated by `crop_id`).
- Test: `pytest tests/labeler/test_download_labels.py -xvs` (3 RED → GREEN)
- Estimated lines: +120

### Task D2: [x] [Test+Impl] Download labels + crops index implementation
- Files: `scripts/download_labels_to_local.py`
- Strict TDD: implement to make D1 GREEN
- Description: `download_labels(client, output_dir)` paginates through Supabase `labels` table and writes `labels/manifest.jsonl` keyed by `id`. `download_crops_index(client, output_dir)` paginates through `crops` table and writes `crops/index.jsonl` keyed by `crop_id`. Both use `_write_jsonl` to merge with existing files and deduplicate. `main()` calls both via `db._client()` and prints counts.
- Test: `pytest tests/labeler/test_download_labels.py -xvs` (all 3 GREEN)
- Estimated lines: +100

**PR-D total: ~220 lines** ✅

---

## PR-E: Git Archive — Primera Vuelta (target: main)

### Task E1: [Test] Archive script — tag, branch, no folder rename
- Files: `tests/labeler/test_archive_primera_vuelta.py`
- Strict TDD: write test FIRST
- Description: Integration tier. 3 scenarios: `test_tag_and_branch_exist` (run script in temp git repo, verify `git tag -l primera-vuelta-v1` and `git branch -l primera-vuelta` return non-empty), `test_label_runs_from_archive` (checkout `primera-vuelta` branch in temp repo, run `python main.py label --help`, verify exit 0), `test_no_folder_rename` (verify script does NOT contain `git mv` or `os.rename` for `src/modules/labeler`). Use `subprocess.run` with `tmp_path` as working directory; init a fresh git repo with a dummy commit.
- Test: `pytest tests/labeler/test_archive_primera_vuelta.py -xvs` (3 RED)
- Estimated lines: +90

### Task E2: [Test+Impl] Archive script implementation
- Files: `scripts/archive_primera_vuelta.sh`
- Strict TDD: implement to make E1 GREEN
- Description: Bash script. `git tag -a primera-vuelta-v1 -m "Archive: primera vuelta portal state before segunda switch"`. `git branch primera-vuelta`. Print instructions: "To push: `git push origin primera-vuelta-v1 && git push origin primera-vuelta`". Exit 0 on success, non-zero if tag/branch already exists (with helpful message). No `git push`, no folder rename.
- Test: `pytest tests/labeler/test_archive_primera_vuelta.py -xvs` (all 3 GREEN)
- Estimated lines: +25

**PR-E total: ~115 lines** ✅

---

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines per PR | PR-A: 290, PR-B: 345, PR-C: 350, PR-D: 220, PR-E: 115 |
| Total estimated changed lines | 1320 |
| Chained PRs recommended | Yes (force-chained) |
| 400-line budget risk | Low (all PRs under 400) |
| Decision needed before apply | No (all 8 product decisions resolved) |
| Delivery strategy | force-chained (PR-A → PR-E, all merge to main) |
| Chain strategy | stacked-to-main (each PR merges to main in order) |

### Suggested Work Units

| Unit | Goal | Likely PR | Notes |
|------|------|-----------|-------|
| 1 | Maintenance guard + landing page | PR-A | Base: main; tests + impl; deployable independently (guard OFF by default) |
| 2 | Schema vuelta + module switch | PR-B | Base: main (after PR-A merge); depends on PR-A for server.py context |
| 3 | Cloud-to-local audit script | PR-C | Base: main (after PR-B); standalone script, no server.py changes |
| 4 | Cloud-to-local label backup | PR-D | Base: main (after PR-C); standalone script, download labels + crops index before maintenance takedown |
| 5 | Git archive script + tag | PR-E | Base: main (after PR-D); smallest PR, pure ops |

## Implementation Order

1. **PR-A first** — maintenance guard is the safety net for cutover; deployable with guard OFF, so zero risk.
2. **PR-B second** — schema migration + module switch must land before audit (audit needs `vuelta` column to exist).
3. **PR-C third** — audit script validates data integrity before segunda exporter runs.
4. **PR-D fourth** — segunda exporter populates the queue for MODULE=segunda.
5. **PR-E last** — git archive is the final snapshot after all code changes are stable.

Each PR is a self-contained work unit with tests + implementation. Strict TDD: tests merge RED, then implementation makes them GREEN within the same PR.
