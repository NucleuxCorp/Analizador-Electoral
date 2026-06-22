# Spec: Segunda Vuelta Portal Switch

## Requirements

### Requirement: Maintenance Mode

The system SHALL gate all non-whitelisted routes behind a maintenance landing page when `MAINTENANCE_MODE=true`, while keeping the healthcheck, static landing assets, and account registration functional. The guard SHALL be off by default. Whitelisted routes are exactly: static assets required by the landing page, `GET /status`, `GET /auth/register`, and `POST /auth/register`. The login route is NOT whitelisted.
Delivered by: PR-A

- Scenario: Healthcheck stays alive under maintenance
  - GIVEN `MAINTENANCE_MODE=true`
  - WHEN a client requests `GET /status`
  - THEN the response is HTTP 200 with the normal status payload
  - Test: tests/labeler/test_maintenance.py::test_status_200_under_maintenance

- Scenario: Work routes serve the landing page
  - GIVEN `MAINTENANCE_MODE=true`
  - WHEN a browser requests `GET /` (or any non-whitelisted work route) with `Accept: text/html`
  - THEN the response is HTTP 200 rendering `maintenance.html`, not the work SPA
  - Test: tests/labeler/test_maintenance.py::test_work_route_returns_landing

- Scenario: JSON clients get 503
  - GIVEN `MAINTENANCE_MODE=true`
  - WHEN a client requests a non-whitelisted route with `Accept: application/json`
  - THEN the response is HTTP 503 with a JSON maintenance body
  - Test: tests/labeler/test_maintenance.py::test_json_client_gets_503

- Scenario: Registration works during maintenance
  - GIVEN `MAINTENANCE_MODE=true`
  - WHEN a new user submits `POST /auth/register` with valid credentials
  - THEN the account is created and a success response is returned, not the landing page
  - Test: tests/labeler/test_maintenance.py::test_register_allowed_under_maintenance

- Scenario: Login is blocked during maintenance
  - GIVEN `MAINTENANCE_MODE=true`
  - WHEN a user submits `POST /auth/login`
  - THEN the guard returns the maintenance response, because login is intentionally not whitelisted
  - Test: tests/labeler/test_maintenance.py::test_login_blocked_under_maintenance

- Scenario: Guard is off by default
  - GIVEN `MAINTENANCE_MODE` is unset or `false`
  - WHEN any route is requested
  - THEN normal routing applies with no maintenance response
  - Test: tests/labeler/test_maintenance.py::test_guard_off_by_default

- Scenario: Landing page reuses the existing tutorial embed
  - GIVEN the landing page template
  - WHEN it renders
  - THEN it embeds the same Loom tutorial URL already present in `home.html`, and no new tutorial asset is introduced
  - Test: tests/labeler/test_maintenance.py::test_landing_reuses_existing_tutorial_embed

- Scenario: Landing page copy includes an `<h1>` and follows AIDA
  - GIVEN the landing page template
  - WHEN it renders
  - THEN it contains an `<h1>` heading for accessibility/SEO and the body copy follows AIDA structure (Attention, Interest, Desire, Action)
  - Test: tests/labeler/test_maintenance.py::test_landing_has_h1

### Requirement: Vuelta Tagging

The system SHALL add a non-null `vuelta` column holding `primera` or `segunda` to the Supabase `crops`, `labels`, and `assignments` tables, backfilling existing rows as `primera`, and all portal queries against those tables SHALL filter by the active vuelta.
Delivered by: PR-B

- Scenario: Migration adds the column to all three tables
  - GIVEN the Supabase project has the existing schema from `scripts/supabase_schema.sql`
  - WHEN the migration for this change runs
  - THEN `crops`, `labels`, and `assignments` each gain a non-null `vuelta` column
  - Test: tests/labeler/test_vuelta_tagging.py::test_migration_adds_vuelta_column

- Scenario: Existing rows backfilled as primera
  - GIVEN the migration has run on a project with pre-existing rows
  - WHEN all rows are selected without a `vuelta` filter
  - THEN every pre-existing row has `vuelta=primera`
  - Test: tests/labeler/test_vuelta_tagging.py::test_existing_rows_backfilled_primera

- Scenario: Portal queries filter by active vuelta
  - GIVEN the portal is serving `MODULE=segunda` and both primera and segunda rows exist
  - WHEN the portal fetches the next crop, assignment, or labels
  - THEN only rows with `vuelta=segunda` are returned
  - Test: tests/labeler/test_vuelta_tagging.py::test_queries_filter_by_active_vuelta

### Requirement: Module Switch

The system SHALL select the active vuelta backend (`LABELS_DIR`, `INDEX_PATH`, template subdir) from the `MODULE` env var. When `MODULE` is unset the system SHALL fall back to `primera` to avoid serving unaudited segunda data. The system SHALL validate the value at startup.
Delivered by: PR-B

- Scenario: Segunda module routes to segunda dirs
  - GIVEN `MODULE=segunda` and the segunda labels dir and index exist
  - WHEN the app starts
  - THEN crop queue, assignment, and templates resolve to the segunda paths
  - Test: tests/labeler/test_module_switch.py::test_segunda_module_resolves_segunda_paths

- Scenario: Unset MODULE falls back to primera
  - GIVEN `MODULE` is unset
  - WHEN the app starts
  - THEN the portal serves the primera labels dir and primera index as a safe default
  - Test: tests/labeler/test_module_switch.py::test_unset_module_defaults_primera

- Scenario: Invalid MODULE value aborts startup
  - GIVEN `MODULE` is set to an unsupported value (e.g. `tercera`)
  - WHEN the app starts
  - THEN startup raises a validation error and the process exits non-zero
  - Test: tests/labeler/test_module_switch.py::test_invalid_module_aborts_startup

### Requirement: Cloud-to-Local Audit Script

The system SHALL provide `scripts/audit_cloud_to_local.py` that verifies the Supabase Storage bucket and the `crops`, `labels`, and `assignments` tables against local copies, exports web-only `labels` to `primera_backup.jsonl`, supports `--incremental` re-runs, and exits non-zero on any mismatch so cutover is blocked.
Delivered by: PR-C

- Scenario: Clean audit passes
  - GIVEN Storage objects and all three tables exactly match local copies
  - WHEN the script runs without flags
  - THEN it reports zero mismatches, writes no new backup rows, and exits 0
  - Test: tests/labeler/test_audit_cloud_to_local.py::test_clean_audit_exits_zero

- Scenario: Mismatch aborts with non-zero exit
  - GIVEN a Storage object exists in the cloud with no local counterpart
  - WHEN the script runs
  - THEN it reports the mismatch and exits non-zero, blocking the cutover
  - Test: tests/labeler/test_audit_cloud_to_local.py::test_mismatch_exits_nonzero

- Scenario: Web-only labels are backed up
  - GIVEN `labels` rows exist in Supabase that are not present in the local JSONL
  - WHEN the script runs
  - THEN those rows are appended to `data/labels/primera_backup.jsonl` before exit
  - Test: tests/labeler/test_audit_cloud_to_local.py::test_web_only_labels_backed_up

- Scenario: Incremental flag skips verified rows
  - GIVEN a previous full run recorded a checkpoint
  - WHEN the script runs with `--incremental`
  - THEN it only re-checks rows and objects not yet verified and exits 0 if all new checks pass
  - Test: tests/labeler/test_audit_cloud_to_local.py::test_incremental_skips_verified

### Requirement: Segunda Vuelta Crop Exporter

The system SHALL provide a segunda-vuelta exporter that emits `CropRecord`-compatible crops from `Segunda Vuelta/src/layout.py` and pushes them to Supabase Storage and the `crops` table tagged `vuelta=segunda`, consumable by the existing portal queue with no `server.py` changes beyond the module switch.
Delivered by: PR-D

- Scenario: Exporter tags every row as segunda
  - GIVEN the exporter is run over segunda-vuelta OCR output
  - WHEN it writes a crop to Supabase
  - THEN the `crops` row and the Storage object carry `vuelta=segunda`
  - Test: tests/labeler/test_exporter_segunda.py::test_exporter_tags_segunda

- Scenario: Output is consumable by the existing portal queue
  - GIVEN the exporter has produced `index.jsonl`
  - WHEN `server.py` loads the queue under `MODULE=segunda`
  - THEN the crops are assignable and labelable with no `server.py` changes beyond the module switch
  - Test: tests/labeler/test_exporter_segunda.py::test_output_consumable_by_portal

- Scenario: Layout churn is handled by rerun
  - GIVEN `Segunda Vuelta/src/layout.py` coordinates change
  - WHEN the exporter is rerun over the same source
  - THEN it emits a fresh `index.jsonl` without requiring guard or switch changes
  - Test: tests/labeler/test_exporter_segunda.py::test_exporter_rerun_on_layout_change

### Requirement: Primera Vuelta Archive

The system SHALL mark the primera-vuelta state with a long-lived git branch `primera-vuelta` and a git tag `primera-vuelta-v1`, without renaming any project folders, so the primera round can be re-run locally.
Delivered by: PR-E

- Scenario: Tag and branch exist
  - GIVEN PR-E has landed
  - WHEN a developer lists refs with `git tag -l primera-vuelta-v1` and `git branch -l primera-vuelta`
  - THEN both refs exist and point at the primera-vuelta final commit
  - Test: tests/labeler/test_primera_archive.py::test_tag_and_branch_exist

- Scenario: Labeler runs from the archived branch
  - GIVEN a developer has run `git checkout primera-vuelta`
  - WHEN they run `python main.py label`
  - THEN the portal starts and serves primera crops without error
  - Test: tests/labeler/test_primera_archive.py::test_label_runs_from_archive

- Scenario: No folders are renamed
  - GIVEN PR-E has landed
  - WHEN the change is reviewed
  - THEN no project directory has been renamed or moved, because the archive is git-history only
  - Test: tests/labeler/test_primera_archive.py::test_no_folder_rename

## Non-Goals

- The segunda-vuelta PDF extractor and layout calibration (owned by change `segunda-vuelta`).
- Provisioning a new Supabase project (this change reuses the existing project and tables).
- A wholesale rewrite of `server.py` or `exporter.py`.
- A new tutorial asset (the landing page reuses the existing Loom embed from `home.html`).
- The human-chosen SLA for the cutover window (out of scope; the audit script supports both full and incremental re-run regardless).
