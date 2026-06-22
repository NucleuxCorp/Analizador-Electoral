# Proposal: Segunda Vuelta Portal Switch

## Intent

The labeler portal must transition from primera to segunda vuelta with zero downtime on `https://analizadore14.porciudad.com/`. We need a maintenance landing page, a safe cloud-to-local audit before takedown, an env-driven module switch to avoid duplicating ~1,600 lines of `server.py`, and a reversible archive of the primera-vuelta state so the round can be re-run locally.

## Scope

### In Scope
- `MAINTENANCE_MODE` + `before_request` guard with `/status` whitelist and new `maintenance.html` landing page
- Cloud-to-local audit script covering Storage bucket + `crops` + `labels` + `assignments`
- `MODULE=primera|segunda` env switch selecting `labels_dir` / `index_path` / template subdir
- Segunda-vuelta crop exporter producing portal-compatible `CropRecord` output (layout from `Segunda Vuelta/src/layout.py`)
- Git tag `primera-vuelta-0.25.2` + branch `primera-vuelta` (no folder rename)
- Deploy sequence: maintenance ON → audit/backup → module swap → maintenance OFF
- pytest coverage for guard, switch, and exporter contract

### Out of Scope
- Segunda-vuelta PDF extractor itself (owned by change `segunda-vuelta`)
- Layout/calibration of segunda-vuelta crop coordinates (still churning)
- New Supabase project provisioning (assumes same project)
- Wholesale rewrite of `server.py` or `exporter.py`

## Capabilities

### New Capabilities
- `portal-maintenance-mode`: env-driven `before_request` guard that short-circuits non-whitelisted routes to a landing page while keeping `/status` and static assets alive
- `portal-module-switch`: env-driven selection of `primera` vs `segunda` data dirs, index paths, and template subfolder inside the same Flask app
- `cloud-to-local-audit`: one-shot verification of Supabase Storage bucket + `crops`/`labels`/`assignments` tables against local copies, with JSONL backup of web-only `labels`
- `segunda-vuelta-crop-exporter`: layout-aware exporter emitting `CropRecord`-compatible crops from `Segunda Vuelta/src/layout.py` for portal ingestion

### Modified Capabilities
- None (no specs exist in `openspec/specs/`)

## Approach

Follow exploration **Approach 1** (single app with env switch). Strict TDD with pytest. Force-chained PRs sliced by phase to keep each review under 400 lines:

1. **PR-A — Maintenance guard + landing page**: `server.py` `before_request` guard, `templates/maintenance.html`, `TUTORIAL_URL` env, `tests/labeler/test_maintenance.py`. Deployable independently (maintenance OFF by default).
2. **PR-B — Module switch**: `MODULE` env selects `LABELS_DIR`/`INDEX_PATH`/template subdir; `wsgi.py` honors it. Tests for prima↔segunda routing.
3. **PR-C — Cloud audit**: new `scripts/audit_cloud_to_local.py` comparing Storage objects + DB rows vs local, exporting `labels` to `data/labels/primera_backup.jsonl`.
4. **PR-D — Segunda exporter**: new `Segunda Vuelta/src/exporter_labeler.py` (or `src/modules/labeler/exporter_segunda.py`) emitting `CropRecord`-compatible crops from `Segunda Vuelta/src/layout.py`.
5. **PR-E — Archive**: git tag `primera-vuelta-0.25.2` + branch `primera-vuelta`; no code diff beyond `CHANGELOG`-style entry.

Deploy sequence: enable maintenance (PR-A land + `MAINTENANCE_MODE=true`) → run PR-C audit → flip `MODULE=segunda` + prime labels dir → disable maintenance.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/modules/labeler/server.py` | Modified | Maintenance guard + module switch in `before_request`/startup |
| `src/modules/labeler/wsgi.py` | Modified | Honor `MODULE` env |
| `src/modules/labeler/templates/maintenance.html` | New | Landing page with platform description + tutorial |
| `src/modules/labeler/templates/home.html` | Modified | Externalize `TUTORIAL_URL` if reused |
| `scripts/audit_cloud_to_local.py` | New | Storage + DB vs local audit + labels backup |
| `Segunda Vuelta/src/exporter_labeler.py` | New | Portal-compatible crop exporter |
| `tests/labeler/test_maintenance.py` | New | Guard whitelist + content negotiation |
| `tests/labeler/test_module_switch.py` | New | MODULE routing |
| `Procfile` / `.env.example` | Modified | `MAINTENANCE_MODE`, `TUTORIAL_URL`, `MODULE`, `LABELS_DIR` |
| Git history | New | Tag `primera-vuelta-0.25.2` + branch `primera-vuelta` |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Guard blocks `/status` → Railway kills pod | High | Explicit whitelist + unit test asserting `GET /status` 200 under maintenance |
| Web `labels`/Storage loss before audit | High | PR-C MUST run before `MAINTENANCE_MODE=true`; script aborts takedown if mismatch |
| Segunda crop coords churn | High | Exporter reads live `Segunda Vuelta/src/layout.py`; rerun with `--calibrate` until stable |
| Mid-session users lose assignments during cutover | Medium | Same Supabase project; 60-min leases expire; announce via landing page |
| Module switch picks wrong labels_dir in prod | Medium | Env validation at startup; defaults to `primera`; test both branches |

## Rollback Plan

1. Set `MAINTENANCE_MODE=true` (re-enable landing page) — instant via Railway env.
2. Set `MODULE=primera` and point `LABELS_DIR` back to `data/labels/`.
3. If exporter misbehaves: drop `Segunda Vuelta/src/exporter_labeler.py` (no first-round import depends on it).
4. Worst case: `git checkout primera-vuelta-0.25.2` tag and redeploy from the archived code.
5. Restore `labels` from `data/labels/primera_backup.jsonl` via `migrate_to_supabase.py` if any post-takedown writes occurred.

## Dependencies

- Same Supabase project reachable during audit (TLS / corporate cert bypass unchanged)
- `Segunda Vuelta/src/layout.py` available at exporter build time (owned by change `segunda-vuelta`)
- Railway healthcheck still hits `GET /status` (confirm in service config)
- Unknown: final tutorial URL for landing page (existing Loom embed is placeholder)

## Success Criteria

- [ ] `GET /status` returns 200 when `MAINTENANCE_MODE=true`
- [ ] All non-whitelisted routes return `maintenance.html` (or 503 JSON for `Accept: application/json`) under maintenance
- [ ] `audit_cloud_to_local.py` reports zero mismatched rows/objects and writes `primera_backup.jsonl` before takedown
- [ ] `MODULE=primera` and `MODULE=segunda` route to distinct labels dirs with matching tests
- [ ] Segunda exporter produces `CropRecord`-compatible `index.jsonl` consumable by current `server.py` queue
- [ ] Git tag `primera-vuelta-0.25.2` + branch `primera-vuelta` exist and `python main.py label` runs from that branch in local dev
- [ ] `https://analizadore14.porciudad.com/` serves the landing page continuously during the cutover window
- [ ] Each chained PR ≤ 400 changed lines; all red on `pytest -xvs tests/`