## Exploration: Segunda Vuelta Portal Switch

### Scope note

This change is about the **Flask labeling web portal**, not the PDF extraction pipeline. The related prior change `openspec/changes/segunda-vuelta/` covers the `Segunda Vuelta/src/` PDF extractor. The two changes meet at the boundary of *crop generation*: the extractor produces `analisis_resultados.jsonl` / raw PDFs, and the portal needs a matching exporter that turns those PDFs into `data/labels/crops/index.jsonl` + PNGs for the new round.

### Current State

- Production portal is a Flask app in `src/modules/labeler/` served by Gunicorn via `src/modules/labeler/wsgi:app` (`Procfile`).
- Runtime mode is env-driven:
  - `SUPABASE_URL` unset → local dev mode (single-user, file-based queue).
  - `SUPABASE_URL` set → production mode (Supabase Auth + Postgres + optional Storage).
- Existing env toggles already follow this pattern: `LAUNCH_AT`, `LOCAL_DEV_BYPASS`, `USE_SUPABASE_STORAGE`.
- The public landing page (`templates/home.html`) already embeds the Loom tutorial (`https://www.loom.com/embed/40f6d1a46315479f969c1f6296b1624b`) and has a launch gate driven by `LAUNCH_AT`.
- Public routes: `GET /status` (no auth). Auth routes: `/auth/*`. Protected labeling routes: `/`, `/work`, `/label`, `/skip`, `/back`, `/image/<id>`, `/next`, `/pdf`, `/mark-fraud`, `/admin/conflicts`, `/debug/sentry-test`.
- Cloud side: Supabase tables `crops`, `labels`, `assignments`; Storage bucket `crops`; migration script `scripts/migrate_to_supabase.py` uploads local data *to* the cloud. `fetch_confirmed_crops.py` can download confirmed + train-pool crops *from* Storage, but there is no generic "verify all cloud assets against local" tool.
- The current crop exporter (`src/modules/labeler/exporter.py`) is hard-wired to the primera-vuelta layout (`CANDIDATE_ROWS_P0/P1`, `NIVELACION`, `TOTALS`). It cannot be reused for segunda vuelta without a layout abstraction.
- The repository is on branch `develop` with no tags and no `main` branch locally; there is no runtime `primera-vuelta` artifact yet.

### Affected Areas

- `src/modules/labeler/server.py` — add maintenance-mode guard and module switch.
- `src/modules/labeler/wsgi.py` — may need to honor `MODULE` env to pick the correct labels directory.
- `src/modules/labeler/templates/` — new `maintenance.html` landing page; possibly module-specific home/label templates.
- `src/modules/labeler/exporter.py` — likely stays untouched for primera; a new layout-aware exporter is needed for segunda.
- `Segunda Vuelta/src/layout.py` and `Segunda Vuelta/src/extractor.py` — source of layout constants for the new exporter.
- `Procfile` / `.env.example` / Railway env vars — new `MAINTENANCE_MODE` and `MODULE`/`LABELS_DIR` variables.
- `tests/labeler/` — new tests for the maintenance guard and module switch.
- Git history — tag + branch for the primera-vuelta archive.

### Exploration Questions

#### 1. Can maintenance mode short-circuit all routes safely?

Yes. The existing `before_request` hook (`_assign_request_id`) is the natural place. A new `MAINTENANCE_MODE=true` guard can return `render_template("maintenance.html")` for any request whose endpoint is not on a whitelist. The whitelist should keep `GET /status` alive for Railway health checks and the maintenance page itself. Static files (CSS/JS) are served from CDN in templates, so no extra whitelist is needed.

#### 2. Which routes must stay alive during maintenance?

Only `GET /status` is strictly required if Railway is configured to health-check it. Everything else — auth, labeling, admin — should be hidden. The maintenance page itself is served from `/` (and optionally `/maintenance`).

#### 3. Where is the tutorial and how is it referenced?

The current tutorial is a Loom embed hard-coded in `templates/home.html` (line 90). For maintenance mode we should externalize the URL into an env var (`TUTORIAL_URL`) so the same template can be reused and the link can be changed without a deploy.

#### 4. What does "verify cloud vs local" mean concretely?

Supabase has three things the local repo may not fully mirror:

1. **Storage bucket `crops`** — PNG files. The local `data/labels/crops/` dir is the source of truth that was uploaded, but web-only re-downloads (e.g., `fetch_confirmed_crops.py`) may not cover *all* crops.
2. **`crops` table rows** — metadata. Derived from `data/labels/crops/index.jsonl`; should match if `migrate_to_supabase.py` was the source.
3. **`labels` and `assignments` tables** — these exist **only** in Supabase for the web labels. `data/labels/manifest.jsonl` only contains legacy pre-web labels.

So a real audit needs to:
- List all objects in Storage bucket `crops` and verify each `crop_id` has a local PNG or a local index entry.
- Export the `labels` table to a local JSONL backup before any takedown.
- Export `assignments` (less critical, 60-min leases).

There is no existing script that does all three.

#### 5. What is the simplest archive strategy for "primera vuelta"?

A git tag + branch is the cheapest, reversible archive:

```bash
git tag -a primera-vuelta-0.25.2 -m "Archive primera vuelta portal state"
git branch primera-vuelta
```

This preserves the exact code, does not break imports, and lets the team run `python main.py label` from that branch in local dev. Renaming `src/modules/labeler/` to `src/modules/labeler_primera/` would break many absolute imports and test paths; not recommended.

#### 6. Should the segunda-vuelta module be a forked package or a switch inside the existing app?

A switch inside the existing app is preferable. The auth, queue, agreement, and admin logic are identical between rounds; only the crop source and possibly the tutorial/home copy differ. Duplicating `server.py` (~1,600 lines) and templates would create a large maintenance burden and exceed the 400-line review budget quickly. The switch can be env-driven (`MODULE=primera|segunda`) selecting the `labels_dir`, `index_path`, and template subfolder.

#### 7. What new exporter is needed for segunda vuelta?

A segunda-vuelta crop exporter that reads the second-round layout from `Segunda Vuelta/src/layout.py` (or a clean `layout_sv.py`) and writes PNGs + `index.jsonl`. It should mirror the contract of `src/modules/labeler/exporter.py` (same `CropRecord` fields) so the existing `server.py` queue/db code can consume it unchanged.

### Approaches

1. **Single app with env module switch + maintenance guard (recommended)**
   - Description: Add `MAINTENANCE_MODE` and `MODULE` env vars to `server.py`. `MODULE` selects the labels directory / index path / template subfolder. Maintenance guard short-circuits non-whitelisted routes to `maintenance.html`. Build a new segunda-vuelta exporter in `Segunda Vuelta/src/exporter_labeler.py` (or `src/modules/labeler_segunda/exporter.py`) that writes crops compatible with the existing portal.
   - Pros: Minimal code duplication; reuses auth/db/queue/agreement logic; follows existing env-toggle patterns; fits 400-line review budget if sliced.
   - Cons: `server.py` gains a small routing/state branch; segunda layout must still be stable.
   - Effort: **Low–Medium**.

2. **Fork portal into `src/modules/labeler_segunda/` and swap the Procfile**
   - Description: Copy `server.py`, `auth.py`, `db.py`, `export_supabase.py`, `wsgi.py`, and templates into a new package, adjust the exporter for segunda layout, and point `Procfile` to the new WSGI entry for segunda-vuelta deployments.
   - Pros: Complete isolation; primera code stays frozen; easy to reason about per-module state.
   - Cons: Duplicates ~1,600 lines of server logic + templates; maintenance guard must be added to both copies; large diff, hard to keep auth/db fixes in sync.
   - Effort: **High**.

3. **Maintenance-only patch on current app + run segunda as a separate service**
   - Description: Add maintenance guard to the current app and deploy the segunda-vuelta portal as a second Railway service or a separate repo.
   - Pros: Zero coupling; preserves primera app exactly.
   - Cons: Duplicate infrastructure and likely duplicate Supabase project; not aligned with the current single-service setup; more operational complexity.
   - Effort: **Medium–High**.

### Recommendation

Adopt **Approach 1**:

1. Add `MAINTENANCE_MODE=true/false` and `TUTORIAL_URL` env vars.
2. Implement a `before_request` maintenance guard with a whitelist for `/status` and the maintenance page.
3. Create `templates/maintenance.html` (mini landing: platform description + tutorial iframe/link).
4. Add `MODULE=primera|segunda` (or simply `LABELS_DIR`) switch so the app can point at `data/labels_primera/` vs `data/labels_segunda/`.
5. Build a segunda-vuelta exporter that produces crops compatible with `CropRecord` and the existing `server.py` queue.
6. Archive the current state with a git tag `primera-vuelta-0.25.2` and branch `primera-vuelta`.
7. Add unit tests in `tests/labeler/test_routes.py` covering the maintenance guard (HTML → maintenance page, `/status` → 200, JSON → maintenance JSON or 503).

### Risks

- **Health-check blackout**: If the maintenance guard blocks `/status`, Railway will mark the service unhealthy. Mitigation: explicit whitelist.
- **Mid-session users**: Users labeling when maintenance starts will lose in-progress assignments after 60 minutes. They can resume if the same Supabase project is reused; if a fresh project is used, their accounts and progress must be migrated or reset.
- **Cloud audit gaps**: Web labels exist only in Supabase. Any takedown without exporting `labels` and Storage objects risks data loss. Mitigation: run a full backup script before maintenance.
- **Segunda layout churn**: The second-round extractor is still being calibrated. Crop coordinates may change, requiring re-export. Mitigation: keep layout constants isolated and use `--calibrate` before the final export.
- **Review budget**: Approach 2 would blow the 400-line limit; Approach 1 keeps the diff small if sliced by phase (maintenance guard first, module switch second, exporter third).

### Unknowns

- Will segunda vuelta reuse the same Supabase project + tables or require a fresh project?
- What is the definitive second-round field layout and candidate/blank/totals structure?
- Where will segunda-vuelta PDFs live? (`data/pdfs_segunda_vuelta/`?)
- Is the existing Loom tutorial the final one for the maintenance page, or is a new segunda-vuelta tutorial being prepared?
- Does the Railway deployment track `develop` or another branch?

### Ready for Proposal

**Yes.** The next recommended phase is `sdd-propose` to define the env vars, file paths, deploy sequence, and acceptance criteria for the maintenance guard and module switch.
