# Tasks: Tachon Pattern Scan — 500 PDFs × Per-Method Isolation

**Change:** `tachon-pattern-scan-500pdf`  
**Branch:** `feat/tachon-pattern-scan-500pdf` (from `develop` after PR #1 merge)  
**Delivery strategy:** ask-on-risk → **chained PRs (user confirmed)**  
**Chain strategy:** stacked-to-main  
**Strict TDD:** RED tests before implementation (`openspec/config.yaml`)

---

## Review Workload Forecast

| Field | Value |
|-------|-------|
| **Estimated changed LOC** | **~950–1,050** total (~550 impl + ~400 tests) |
| **Implementation breakdown** | `build_tachon_scan_manifest.py` ~180; `tachon_method_scan.py` ~370 (scan + aggregates + report + CLI); committed manifest JSON ~500 lines (data, not review LOC) |
| **Test breakdown** | `test_tachon_scan_manifest.py` ~140 (5 tests); `test_tachon_method_scan.py` ~320 (12 tests incl. T-D01) |
| **400-line budget risk** | **Medium** — impl alone fits ~550; full PR with tests exceeds 400 |
| **Chained PR recommendation** | **Yes** — user confirmed 2 PRs (2026-06-22) |
| **PR split (locked)** | See **Chained PR Plan** below |
| **Decision needed before apply** | **Resolved** — 2 PRs, stacked-to-main |

**Forecast summary:** Total diff ~1K LOC split into two reviewable PRs under the 400-line budget per slice.

---

## Chained PR Plan (stacked-to-main)

### PR-1 — Manifest (`feat/tachon-scan-manifest`)

| Field | Value |
|-------|-------|
| **Branch** | `feat/tachon-scan-manifest` → `develop` |
| **Phases** | 1–2 (optional Phase 0 only if base lacks corpus paths) |
| **Files** | `debug_sv/build_tachon_scan_manifest.py`, `debug_sv/test_tachon_scan_manifest.py`, `data/analysis_segunda_vuelta/tachon_scan_500_manifest.json` |
| **Est. LOC** | ~320 |
| **D2 worker required?** | **No** — corpus scan only |
| **Gates** | GATE-SCAN-02, GATE-SCAN-08; T-M01–T-M05 green |
| **Merge before** | PR-2 |

### PR-2 — Scan + aggregates (`feat/tachon-method-scan`)

| Field | Value |
|-------|-------|
| **Branch** | `feat/tachon-method-scan` → `develop` (after PR-1 merged) |
| **Phases** | 0 (D2 prereq), 3–7 |
| **Files** | `debug_sv/tachon_method_scan.py`, `debug_sv/test_tachon_method_scan.py`, aggregate JSON/MD outputs |
| **Est. LOC** | ~630 |
| **D2 worker required?** | **Yes** — merge PR #1 (`feat/d2-field-groups-tachon`) first |
| **Gates** | GATE-SCAN-01..07, GATE-SCAN-09; full 500-PDF run |

---

## Work Units & Commit Sequence

| # | Phase | Commit message | Files (primary) | Est. LOC |
|---|-------|----------------|-----------------|----------|
| 0 | Prerequisite | `chore(debug_sv): restore D2 worker deps from PR #1` *(only if missing on base)* | `e14_worker.py`, `subcell_tachon.py`, `grid_detector_v2.py`, `candidate_subcells.py` | vendored |
| 1 | RED manifest | `test(debug_sv): add tachon scan manifest RED tests (T-M01–T-M05)` | `debug_sv/test_tachon_scan_manifest.py` | ~140 |
| 2 | GREEN manifest | `feat(debug_sv): add stratified manifest builder` | `debug_sv/build_tachon_scan_manifest.py`, `data/.../tachon_scan_500_manifest.json` | ~180 |
| 3 | RED scan | `test(debug_sv): add tachon method scan RED tests (T-S*, T-J*, T-P01, T-D01)` | `debug_sv/test_tachon_method_scan.py` (partial) | ~220 |
| 4 | GREEN scan core | `feat(debug_sv): add per-method isolated scan runner` | `debug_sv/tachon_method_scan.py` (core) | ~280 |
| 5 | RED aggregates | `test(debug_sv): add aggregate/report RED tests (T-A*, T-R01)` | `debug_sv/test_tachon_method_scan.py` (append) | ~100 |
| 6 | GREEN aggregates | `feat(debug_sv): add aggregate reporter and markdown report` | `debug_sv/tachon_method_scan.py` (aggregates) | ~90 |
| 7 | Data / gates | `data(analysis): add tachon 500 scan outputs` | summary, cooccurrence, histograms, complete, report; JSONL per git policy | artifacts |

---

## Implementation Phases

### Phase 0: Prerequisite Check (blocking)

**Goal:** Confirm D2 pipeline files exist before any scan work. **Current workspace status:** D2 worker files **present** — PR #1 merged.

- [x] **0.1** Verify PR #1 (`feat/d2-field-groups-tachon`) merged into `develop` — **GATE-SCAN-01**
- [x] **0.2** Confirm required files present:
  - [x] `debug_sv/e14_worker.py` — `process_pdf_task` or `_extract_subcells_for_scan`
  - [x] `debug_sv/grid_detector_v2.py` — H-line filter, grid rows
  - [x] `debug_sv/subcell_tachon.py` — `has_ink`, `_json_safe_tachon`, `NEUTRAL_TACHON`
  - [x] `debug_sv/candidate_subcells.py` — optional Block B fallback
- [x] **0.3** Run D2 regression baseline: `python -m pytest debug_sv/test_tachon_field_groups.py -v` → **19/19 pass** — **GATE-SCAN-09**
- [x] **0.4** If files missing: cherry-pick/vend from PR #1 **or** stop apply and document Option B minimal crop path (design fallback) *(skipped — files present)*
- [x] **0.5** Confirm **zero planned edits** under `src/modules/analyzer/` — **GATE-SCAN-06**

**Exit:** All 0.1–0.3 green; else abort with prerequisite message (matches **T-P01** / exit code 1).

---

### Phase 1: RED — Manifest Tests (`test_tachon_scan_manifest.py`)

**Strict TDD:** Write all manifest tests **before** `build_tachon_scan_manifest.py`. Expect **RED** on first run.

- [x] **1.1** Create `debug_sv/test_tachon_scan_manifest.py` with fixtures: synthetic corpus dir (multi-dept counts), temp output paths, mock validate JSONL
- [x] **1.2** Implement RED tests mapped to spec matrix (see Test Matrix below)
- [x] **1.3** Run: `python -m pytest debug_sv/test_tachon_scan_manifest.py -v` → **5 failed** (module not found / NotImplemented)

**Covers:** REQ-MANIFEST, **T-M01–T-M05**

---

### Phase 2: GREEN — `build_tachon_scan_manifest.py`

**Goal:** Stratified 500-PDF manifest, seed=42, SHA-256 hash, CLI.

- [x] **2.1** `parse_dept(filename)` — regex `^(\d+)_`, raise `ValueError` on no match
- [x] **2.2** `allocate_stratified(dept_counts, *, target=500, total_corpus, min_per_dept=1)` — proportional floor, adjust to exactly 500 (tie-break: larger alloc → larger dept_count → lexicographic dept id)
- [x] **2.3** `build_manifest(...)` — glob corpus, shuffle per dept with `random.seed(seed)`, tag `validate_holdout` from `e14c_validate_results.jsonl`, compute `manifest_hash` (SHA-256 canonical JSON of sorted entries, pdf paths only)
- [x] **2.4** CLI: `--corpus-dir`, `--output`, `--target`, `--seed`, `--validate-jsonl`, `--dry-run`; exit codes 0/1/2 per spec
- [x] **2.5** Write `data/analysis_segunda_vuelta/tachon_scan_500_manifest.json` with `total_pdfs=500`, `departments=33`, `seed=42`
- [x] **2.6** Run manifest tests → **5/5 GREEN** — **GATE-SCAN-02**, **GATE-SCAN-08**
- [x] **2.7** Re-run `build_manifest(seed=42)` twice → identical `manifest_hash` and entry list

**Commit:** `feat(debug_sv): add stratified manifest builder`

---

### Phase 3: RED — Scan Tests (`test_tachon_method_scan.py`)

**Strict TDD:** Write scan/isolation/JSONL/prereq tests **before** `tachon_method_scan.py` core.

- [x] **3.1** Create `debug_sv/test_tachon_method_scan.py` with fixtures: synthetic BGR crops, mocked detector returns, mini manifest (2 PDFs), JSONL temp paths
- [x] **3.2** Implement RED tests for **T-S01–T-S05**, **T-J01–T-J02**, **T-P01**, **T-D01** (see Test Matrix)
- [x] **3.3** Run: `python -m pytest debug_sv/test_tachon_method_scan.py -v -k "not aggregates and not report"` → **RED**

**Covers:** REQ-SCAN-CLI, REQ-JSONL, REQ-PREREQ, REQ-NO-SRC-EDITS (T-D01)

---

### Phase 4: GREEN — `tachon_method_scan.py` Core + Isolated Detectors

**Goal:** Per-subcell isolated scoring via direct detector imports (Approach A); D2 extraction adapter; multiprocess runner.

- [x] **4.1** Prerequisite guard at import/CLI start — missing `e14_worker.py` → exit 1 + PR #1 message (**T-P01**)
- [x] **4.2** `METHODS` dict + thresholds (0.45 / 0.50 / 0.60 / 0.50); `COMBINED_WEIGHTS` + `COMBINED_THRESHOLD=0.45`
- [x] **4.3** `analyze_subcell_isolated(crop_bgr, *, scan_mode, has_ink)` — call `detect_tachon`, `detect_double_writing`, `detect_density`, `detect_noise` from `tachon_detector.py`; **never** call `analyze_cell()` (**T-D01**)
- [x] **4.4** Empty cell policy (`has_ink=False`): zero scores, all flags false (**T-S04**)
- [x] **4.5** `_extract_subcells_from_pdf` — thin adapter over D2 (`e14_worker` / `grid_detector_v2`); Option B fallback only if 0.2 fails
- [x] **4.6** `process_pdf_for_scan(pdf_path, *, scan_mode, skip_ocr)` — flat JSONL records per subcell schema; apply `_json_safe_tachon()` before serialize (**T-S05**)
- [x] **4.7** `run_scan(manifest_path, *, scan_mode, workers=8, skip_ocr, out_jsonl, limit, ...)` — `ProcessPoolExecutor`, append-safe JSONL, error collection (continue on PDF failure)
- [x] **4.8** CLI flags per spec: `--manifest` (required), `--method`, `--workers`, `--skip-ocr`, `--output-jsonl`, `--limit`, `--save-crops`, `--crops-per-method`, `--run-id`
- [x] **4.9** Run Phase 3 tests → **GREEN** for T-S*, T-J*, T-P01, T-D01

**Commit:** `feat(debug_sv): add per-method isolated scan runner`

---

### Phase 5: Aggregates + Markdown Report

**Strict TDD:** RED aggregate/report tests (Phase 5.1) before implementation (5.2).

- [x] **5.1** Add RED tests **T-A01**, **T-A02**, **T-R01** to `test_tachon_method_scan.py`
- [x] **5.2** `build_aggregates(jsonl_path, *, manifest, scan_mode, elapsed_s, workers, errors)`:
  - [x] Per-method flag rates (ink subcells only)
  - [x] Per-dept and per-label (`block`+`label`) breakdowns
  - [x] 4×4 co-occurrence matrix + conditional probabilities
  - [x] Score histograms: bins [0,0.1,…,1.0], p50/p90/p99, max
- [x] **5.3** `write_aggregate_artifacts(aggregates, base_dir)` — summary, cooccurrence, histograms, complete JSON
- [x] **5.4** Render `tachon_method_scan_500_report.md` — 7 sections per REQ-REPORTS (**T-R01**)
- [x] **5.5** Wire `run_scan` post-pass → aggregates + artifacts; completion includes `manifest_hash` (**T-A02**)
- [x] **5.6** Run T-A*, T-R01 → **GREEN** — **GATE-SCAN-07**

**Commit:** `feat(debug_sv): add aggregate reporter and markdown report`

---

### Phase 6: Smoke (5 PDFs) + Full 500 Run Gates

- [x] **6.1** Build/verify manifest: `python debug_sv/build_tachon_scan_manifest.py --seed 42`
- [x] **6.2** **Smoke run** (5 PDFs):  
  `python debug_sv/tachon_method_scan.py --manifest data/analysis_segunda_vuelta/tachon_scan_500_manifest.json --method all --workers 2 --skip-ocr --limit 5`  
  - [x] JSONL lines emitted per subcell  
  - [x] All four scores + `flags_by_method` on every line  
  - [x] `double_score` present on ink subcells
- [x] **6.3** **Full 500-PDF run**:  
  `python debug_sv/tachon_method_scan.py --manifest data/analysis_segunda_vuelta/tachon_scan_500_manifest.json --method all --workers 8 --skip-ocr`  
  - [x] `pdfs_error / 500 < 0.01` in `tachon_method_scan_500_complete.json` — **GATE-SCAN-04**  
  - [x] 100% ink subcells have `scores.double_score` — **GATE-SCAN-05**  
  - [x] All aggregate artifacts exist — **GATE-SCAN-07**  
  - [x] Wall time logged (informational **GATE-SCAN-10**, target &lt; 10 min)
- [ ] **6.4** Optional: `--save-crops --crops-per-method 50` — non-blocking **GATE-SCAN-11**
- [x] **6.5** JSONL git policy: commit if &lt;50MB; else gitignore JSONL, keep summary/cooccurrence/histograms/complete/report
- [x] **6.6** Final pytest suite: **37/37 GREEN** (6 manifest + 12 scan + 19 D2 regression) — **GATE-SCAN-03**

**Commit:** `data(analysis): add tachon 500 scan outputs`

---

## Test Matrix (strict TDD mapping)

### Manifest tests — `debug_sv/test_tachon_scan_manifest.py`

| ID | Test name | Phase | Status |
|----|-----------|-------|--------|
| T-M01 | `test_manifest_exactly_500_all_depts` | 1 RED → 2 GREEN | [x] |
| T-M02 | `test_manifest_reproducible_seed_42` | 1 RED → 2 GREEN | [x] |
| T-M03 | `test_allocation_proportional_min_one` | 1 RED → 2 GREEN | [x] |
| T-M04 | `test_validate_holdout_tagging` | 1 RED → 2 GREEN | [x] |
| T-M05 | `test_manifest_hash_canonical` | 1 RED → 2 GREEN | [x] |

### Scan / JSONL / prereq tests — `debug_sv/test_tachon_method_scan.py`

| ID | Test name | Phase | Status |
|----|-----------|-------|--------|
| T-S01 | `test_scan_emits_all_methods` | 3 RED → 4 GREEN | [x] |
| T-S02 | `test_per_method_flags_isolated` | 3 RED → 4 GREEN | [x] |
| T-S03 | `test_skip_ocr_omits_digit` | 3 RED → 4 GREEN | [x] |
| T-S04 | `test_empty_cell_neutral_scores` | 3 RED → 4 GREEN | [x] |
| T-S05 | `test_jsonl_native_types` | 3 RED → 4 GREEN | [x] |
| T-J01 | `test_jsonl_schema_double_score_present` | 3 RED → 4 GREEN | [x] |
| T-J02 | `test_jsonl_schema_validation` | 3 RED → 4 GREEN | [x] |
| T-P01 | `test_missing_prereq_exit` | 3 RED → 4 GREEN | [x] |
| T-D01 | `test_direct_detector_import_not_analyze_cell` | 3 RED → 4 GREEN | [x] |

### Aggregate / report tests — `debug_sv/test_tachon_method_scan.py`

| ID | Test name | Phase | Status |
|----|-----------|-------|--------|
| T-A01 | `test_aggregates_from_fixture` | 5 RED → 5 GREEN | [x] |
| T-A02 | `test_completion_manifest_hash` | 5 RED → 5 GREEN | [x] |
| T-R01 | `test_report_sections_present` | 5 RED → 5 GREEN | [x] |

**Total:** 17 pytest cases + 1 manual gate (GATE-SCAN-06 src diff check)

---

## Acceptance Checklist (blocking merge)

| Gate | Criterion | Task ref | Done |
|------|-----------|----------|------|
| GATE-SCAN-01 | PR #1 merged; D2 worker files present | Phase 0 | [x] |
| GATE-SCAN-02 | Manifest tests pass; `manifest_hash` stable on re-run | Phase 2 | [x] |
| GATE-SCAN-03 | All `test_tachon_method_scan.py` pass | Phases 4–5 | [x] |
| GATE-SCAN-04 | Full scan: `pdfs_error / 500 < 0.01` | Phase 6.3 | [x] |
| GATE-SCAN-05 | 100% ink subcells have `double_score` | Phase 6.3 | [x] |
| GATE-SCAN-06 | Zero edits under `src/modules/analyzer/` | Phase 0.5 + review | [x] |
| GATE-SCAN-07 | Summary, cooccurrence, histogram, complete, report exist | Phase 5–6 | [x] |
| GATE-SCAN-08 | All 33 departments in manifest | Phase 2 | [x] |
| GATE-SCAN-09 | D2 regression `test_tachon_field_groups.py` 19/19 | Phase 0.3 | [x] |
| GATE-SCAN-10 | Wall time &lt; 10 min (500 PDFs, 8 workers, `--skip-ocr`) | Phase 6.3 | [x] info |
| GATE-SCAN-11 | Optional crop gallery | Phase 6.4 | [ ] optional |

### Deliverables checklist

| # | Artifact | Path | Done |
|---|----------|------|------|
| 1 | Manifest generator | `debug_sv/build_tachon_scan_manifest.py` | [x] |
| 2 | Manifest | `data/analysis_segunda_vuelta/tachon_scan_500_manifest.json` | [x] |
| 3 | Scan script | `debug_sv/tachon_method_scan.py` | [x] |
| 4 | Tests | `debug_sv/test_tachon_scan_manifest.py`, `debug_sv/test_tachon_method_scan.py` | [x] |
| 5 | Subcell JSONL | `data/analysis_segunda_vuelta/tachon_method_scan_500.jsonl` | [x] |
| 6 | Summary | `data/analysis_segunda_vuelta/tachon_method_scan_500_summary.json` | [x] |
| 7 | Co-occurrence | `data/analysis_segunda_vuelta/tachon_method_cooccurrence.json` | [x] |
| 8 | Histograms | `data/analysis_segunda_vuelta/tachon_method_histograms.json` | [x] |
| 9 | Completion | `data/analysis_segunda_vuelta/tachon_method_scan_500_complete.json` | [x] |
| 10 | Report | `data/analysis_segunda_vuelta/tachon_method_scan_500_report.md` | [x] |
| 11 | Optional crops | `data/analysis_segunda_vuelta/tachon_scan_crops/{method}/` | [ ] |

---

## Validation Commands

```powershell
# Phase 0 — prerequisite + D2 regression
Test-Path debug_sv/e14_worker.py, debug_sv/grid_detector_v2.py, debug_sv/subcell_tachon.py
python -m pytest debug_sv/test_tachon_field_groups.py -v

# Phase 1–2 — manifest (RED then GREEN)
python -m pytest debug_sv/test_tachon_scan_manifest.py -v
python debug_sv/build_tachon_scan_manifest.py --seed 42 --dry-run
python debug_sv/build_tachon_scan_manifest.py --seed 42

# Phase 3–5 — scan + aggregates (RED then GREEN)
python -m pytest debug_sv/test_tachon_method_scan.py -v

# Full test suite (17 cases)
python -m pytest debug_sv/test_tachon_scan_manifest.py debug_sv/test_tachon_method_scan.py -v

# Phase 6 — smoke + full run
python debug_sv/tachon_method_scan.py --manifest data/analysis_segunda_vuelta/tachon_scan_500_manifest.json --method all --workers 2 --skip-ocr --limit 5
python debug_sv/tachon_method_scan.py --manifest data/analysis_segunda_vuelta/tachon_scan_500_manifest.json --method all --workers 8 --skip-ocr

# GATE-SCAN-06 — no src edits
git diff develop --name-only | Select-String "src/modules/analyzer/"
# Expect: no output
```

---

## Out of Scope (explicit)

| Item | Reason |
|------|--------|
| 90K full-corpus batch | Deferred; validate OK% ~24% |
| CNN / digit classifier retrain | Unrelated to heuristic pattern mining |
| Portal / triage UI | Downstream consumer |
| `notes_extractor` integration | Textual enmiendas — different fraud modality |
| Edits to `src/modules/analyzer/tachon_detector.py` | D2 constraint; Approach B deferred |
| Production threshold / weight changes | Experiment informs calibration only |
| Force-include all `tachon_suspicious` PDFs | Stratification takes precedence |
| `analyze_cell()` / `analyze_cell_extended()` in scan path | Direct detector calls only (Approach A) |
| Committing multi-GB JSONL | Gitignore if &gt;50MB; summary artifacts required |
| Vendoring D2 into this PR if PR #1 already on `develop` | Phase 0 commit only when files missing |
| Auto-tuning thresholds from scan results | Human review of distributions required |
| Crop gallery beyond optional top-N | Non-blocking; cap disk per `--crops-per-method` |

---

## Rollback

1. Delete `debug_sv/build_tachon_scan_manifest.py`, `debug_sv/tachon_method_scan.py`, test files.
2. Delete artifacts under `data/analysis_segunda_vuelta/tachon_*` and optional `tachon_scan_crops/`.
3. Revert D2 vendoring commit only if added solely for this change.
4. No production paths (`src/modules/analyzer/`) to revert.

---

## References

| Resource | Path |
|----------|------|
| Spec | `openspec/changes/tachon-pattern-scan-500pdf/spec.md` |
| Design | `openspec/changes/tachon-pattern-scan-500pdf/design.md` |
| Proposal | `openspec/changes/tachon-pattern-scan-500pdf/proposal.md` |
| Detectors (read-only) | `src/modules/analyzer/tachon_detector.py` |
| Validate baseline | `data/analysis_segunda_vuelta/e14c_validate_results.jsonl` |
| PDF corpus | `data/pdfs_e14c_segunda/` |