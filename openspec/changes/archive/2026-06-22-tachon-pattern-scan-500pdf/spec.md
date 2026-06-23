# Spec: Tachon Pattern Scan — 500 PDFs × Per-Method Isolation

**Change:** `tachon-pattern-scan-500pdf`  
**Date:** 2026-06-22  
**Status:** Spec — ready for `sdd-design`  
**Prerequisite:** Merge PR #1 (`feat/d2-field-groups-tachon`) so `debug_sv/e14_worker.py`, `subcell_tachon.py`, `grid_detector_v2.py`, and `analyze_e14_batch.py` are present before apply.

---

## Purpose

Run a **controlled 500-PDF experiment** that executes each visual alteration detector **in isolation** (not via the combined `analyze_cell()` gate) to mine patterns of enmiendas, tachones, and related cell-level fraud signals. Outputs are score distributions, co-occurrence matrices, and per-department breakdowns that inform threshold calibration **before** any production tuning.

**Approach A (locked):** `debug_sv/tachon_method_scan.py` calls `detect_tachon`, `detect_double_writing`, `detect_density`, and `detect_noise` from `src/modules/analyzer/tachon_detector.py` directly. **Zero edits** under `src/modules/analyzer/`.

---

## Detector Reference (read-only import)

| Method | Function | Flag threshold | Combined weight |
|--------|----------|----------------|-----------------|
| TACHON | `detect_tachon()` | `>= 0.45` | 0.40 |
| DOBLE_ESCRITURA | `detect_double_writing()` | `>= 0.50` | 0.30 |
| DENSIDAD_ALTA | `detect_density()` | `>= 0.60` | 0.20 |
| ZONA_SUCIA | `detect_noise()` | `>= 0.50` | 0.10 |

Combined baseline (informational only):  
`combined_score = tachon×0.40 + double×0.30 + density×0.20 + noise×0.10`  
`is_suspicious_combined = combined_score >= 0.45`

---

## Requirements

### REQ-MANIFEST: Stratified 500-PDF Manifest

The system SHALL build a reproducible 500-PDF manifest from `data/pdfs_e14c_segunda/` (90,838 PDFs, 33 departments) using **stratified proportional allocation with minimum floor 1 per department**, `random.seed(42)`, and write `data/analysis_segunda_vuelta/tachon_scan_500_manifest.json`.

**Allocation algorithm (normative):**

1. Parse department from filename: `^(\d+)_`.
2. Group all `*.pdf` files by department; let `dept_count[d]` be the count per dept and `total_corpus = 90838`.
3. **Initial allocation:** `alloc[d] = max(1, round(500 × dept_count[d] / total_corpus))` for each dept with ≥1 file.
4. **Adjust to exactly 500:**
   - If `sum(alloc) > 500`: decrement from departments with largest `alloc` (tie-break: larger `dept_count`, then lexicographic dept id) until sum = 500.
   - If `sum(alloc) < 500`: increment largest departments (same tie-break) until sum = 500.
5. Within each dept: `random.shuffle(pdfs_in_dept)`; take first `alloc[d]` files (stable order after shuffle with seed=42).
6. Compute `manifest_hash` as SHA-256 over canonical JSON of sorted `entries` (pdf path only, UTF-8).

**Manifest schema:**

```json
{
  "seed": 42,
  "total_pdfs": 500,
  "total_corpus": 90838,
  "departments": 33,
  "allocation_formula": "proportional_with_min_1",
  "created_at": "ISO-8601",
  "manifest_hash": "sha256:...",
  "entries": [
    {
      "pdf": "data/pdfs_e14c_segunda/16_..._5002.pdf",
      "dept": "16",
      "stratum": "dept_16",
      "validate_holdout": false
    }
  ]
}
```

**Manifest acceptance criteria:**

| Criterion | Expected |
|-----------|----------|
| `total_pdfs` | Exactly **500** |
| `seed` | **42** |
| `departments` | **33** |
| Dept coverage | Every dept present in corpus has **≥1** entry |
| `entries[].dept` | Matches filename prefix `^(\d+)_` |
| `entries[].stratum` | `dept_{NN}` where NN is zero-padded dept id from filename |
| `validate_holdout` | `true` when PDF overlaps `e14c_validate_results.jsonl` (optional enrichment) |
| Reproducibility | Two runs with default args → identical `manifest_hash` and entry list |

- Scenario: Manifest builder produces exactly 500 stratified PDFs
  - GIVEN `data/pdfs_e14c_segunda/` contains 90,838 PDFs across 33 departments
  - WHEN `python debug_sv/build_tachon_scan_manifest.py` runs with default `--seed 42`
  - THEN `tachon_scan_500_manifest.json` is written with `total_pdfs=500` and `departments=33`
  - AND every department in the corpus has at least one entry
  - Test: `debug_sv/test_tachon_scan_manifest.py::test_manifest_exactly_500_all_depts`

- Scenario: Manifest is reproducible with seed 42
  - GIVEN the corpus directory is unchanged
  - WHEN the manifest builder runs twice with `--seed 42`
  - THEN both runs produce identical `manifest_hash` and identical sorted `entries[].pdf` lists
  - Test: `debug_sv/test_tachon_scan_manifest.py::test_manifest_reproducible_seed_42`

- Scenario: Allocation respects proportional formula with min floor
  - GIVEN a synthetic corpus fixture with known dept counts (large + tiny depts)
  - WHEN allocation runs with `target=500`, `seed=42`
  - THEN tiny depts receive `alloc=1`, large depts receive `round(500 × count / total)`, and sum adjusts to exactly 500
  - Test: `debug_sv/test_tachon_scan_manifest.py::test_allocation_proportional_min_one`

- Scenario: Validate holdout tagging
  - GIVEN a PDF path present in `e14c_validate_results.jsonl`
  - WHEN that PDF is selected into the manifest
  - THEN its entry has `validate_holdout: true`
  - Test: `debug_sv/test_tachon_scan_manifest.py::test_validate_holdout_tagging`

---

### REQ-SCAN-CLI: Scan Runner CLI

The system SHALL provide `debug_sv/tachon_method_scan.py` that processes manifest PDFs through the D2 grid/subcell pipeline and emits per-subcell isolated detector scores.

**Pipeline per PDF:**

```
PDF → grid_detector_v2 → label_rows → subcell crops
    → per-subcell: detect_tachon, detect_double_writing, detect_density, detect_noise
    → flags_by_method + combined_score
    → JSONL line per subcell
    → post-run aggregates
```

**`build_tachon_scan_manifest.py` CLI contract:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--corpus-dir` | path | `data/pdfs_e14c_segunda` | Source PDF directory |
| `--output` | path | `data/analysis_segunda_vuelta/tachon_scan_500_manifest.json` | Manifest output path |
| `--target` | int | `500` | Sample size |
| `--seed` | int | `42` | RNG seed for within-dept shuffle |
| `--validate-jsonl` | path | `data/analysis_segunda_vuelta/e14c_validate_results.jsonl` | Optional holdout tagging source |
| `--dry-run` | flag | off | Print allocation table; do not write file |

Exit codes: `0` success; `1` corpus missing or allocation failure; `2` invalid arguments.

**`tachon_method_scan.py` CLI contract:**

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--manifest` | path | **required** | Path to `tachon_scan_500_manifest.json` |
| `--method` | choice | `all` | `TACHON`, `DOBLE_ESCRITURA`, `DENSIDAD_ALTA`, `ZONA_SUCIA`, or `all` |
| `--workers` | int | `8` | Process pool size |
| `--skip-ocr` | flag | off | Skip digit OCR; omit `digit`/`confidence` or set null |
| `--output-jsonl` | path | `data/analysis_segunda_vuelta/tachon_method_scan_500.jsonl` | Subcell JSONL output |
| `--limit` | int | none | Process first N manifest entries (for dev) |
| `--save-crops` | flag | off | Write optional top-N crop gallery |
| `--crops-per-method` | int | `50` | Cap crops per method when `--save-crops` |
| `--run-id` | str | `tachon-pattern-scan-500pdf` | Recorded on every JSONL line |

**`--method` semantics:**

- All four detector functions run on every subcell regardless of `--method` value (single PDF pass).
- `scan_mode` in output reflects CLI choice: `all_methods` when `--method all`, else the method name (e.g. `TACHON`).
- Per-method isolation is expressed via `flags_by_method` using each method's threshold independently; `--method` does **not** suppress score computation for other methods.

Exit codes: `0` completed with error rate &lt; 1%; `1` prerequisite files missing or manifest invalid; `2` error rate ≥ 1% or fatal worker crash.

- Scenario: Scan runner processes manifest with all methods
  - GIVEN a valid manifest with 2 PDFs (test fixture) and D2 worker files present
  - WHEN `python debug_sv/tachon_method_scan.py --manifest <fixture> --method all --workers 2 --skip-ocr --limit 2` runs
  - THEN `tachon_method_scan_500.jsonl` contains one line per analyzed subcell
  - AND every line includes all four scores and `flags_by_method` for all four methods
  - Test: `debug_sv/test_tachon_method_scan.py::test_scan_emits_all_methods`

- Scenario: Scan runner respects per-method flag thresholds
  - GIVEN a mocked subcell crop with known detector return values
  - WHEN isolated analysis runs
  - THEN `flags_by_method.TACHON` is true iff `tachon_score >= 0.45` (and analogously for other methods)
  - AND `flags_by_method` is independent of `is_suspicious_combined`
  - Test: `debug_sv/test_tachon_method_scan.py::test_per_method_flags_isolated`

- Scenario: Skip-OCR mode omits digit fields
  - GIVEN `--skip-ocr` is set
  - WHEN a PDF is processed
  - THEN JSONL records have `digit: null` and `confidence: null` (or keys omitted per schema policy — must be consistent and documented in tests)
  - Test: `debug_sv/test_tachon_method_scan.py::test_skip_ocr_omits_digit`

- Scenario: Empty cells recorded with zero scores
  - GIVEN a subcell with `has_ink: false`
  - WHEN analysis runs
  - THEN all scores are `0.0`, all `flags_by_method` are false, `is_suspicious_combined` is false
  - Test: `debug_sv/test_tachon_method_scan.py::test_empty_cell_neutral_scores`

- Scenario: Multiprocess JSON serialization is safe
  - GIVEN detector outputs that may include numpy scalar types
  - WHEN records are written to JSONL
  - THEN all values are native Python `float`/`bool`/`str` (reuse `_json_safe_tachon()` pattern from D2)
  - Test: `debug_sv/test_tachon_method_scan.py::test_jsonl_native_types`

---

### REQ-JSONL: Subcell JSONL Schema

The system SHALL emit one JSON object per line to `data/analysis_segunda_vuelta/tachon_method_scan_500.jsonl`.

**Required fields per record:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `run_id` | string | yes | e.g. `tachon-pattern-scan-500pdf` |
| `pdf` | string | yes | Relative path from repo root |
| `dept` | string | yes | Two-digit dept prefix from filename |
| `block` | string | yes | `block_a`, `block_b`, or `block_c` |
| `label` | string | yes | Field label (e.g. `C1_CEPEDA`, `NIVELACION`) |
| `subcell_idx` | int | yes | 0-based index within row |
| `has_ink` | bool | yes | From D2 subcell metadata |
| `scores.tachon_score` | float | yes | `[0.0, 1.0]`, 3 decimal places |
| `scores.double_score` | float | yes | `[0.0, 1.0]` — **mandatory** (gap in validate JSONL) |
| `scores.density_score` | float | yes | `[0.0, 1.0]` |
| `scores.noise_score` | float | yes | `[0.0, 1.0]` |
| `scores.combined_score` | float | yes | Weighted sum per detector reference |
| `flags_by_method.TACHON` | bool | yes | `tachon_score >= 0.45` |
| `flags_by_method.DOBLE_ESCRITURA` | bool | yes | `double_score >= 0.50` |
| `flags_by_method.DENSIDAD_ALTA` | bool | yes | `density_score >= 0.60` |
| `flags_by_method.ZONA_SUCIA` | bool | yes | `noise_score >= 0.50` |
| `is_suspicious_combined` | bool | yes | `combined_score >= 0.45` |
| `scan_mode` | string | yes | `all_methods` or single method name |
| `digit` | string\|null | conditional | Present when OCR runs; null/omitted with `--skip-ocr` |
| `confidence` | float\|null | conditional | OCR confidence when OCR runs |

**Example record:**

```json
{
  "run_id": "tachon-pattern-scan-500pdf",
  "pdf": "data/pdfs_e14c_segunda/16_..._5002.pdf",
  "dept": "16",
  "block": "block_b",
  "label": "C1_CEPEDA",
  "subcell_idx": 1,
  "digit": "2",
  "confidence": 1.0,
  "has_ink": true,
  "scores": {
    "tachon_score": 1.0,
    "double_score": 0.0,
    "density_score": 0.0,
    "noise_score": 0.308,
    "combined_score": 0.431
  },
  "flags_by_method": {
    "TACHON": true,
    "DOBLE_ESCRITURA": false,
    "DENSIDAD_ALTA": false,
    "ZONA_SUCIA": false
  },
  "is_suspicious_combined": false,
  "scan_mode": "all_methods"
}
```

- Scenario: Ink subcells always include double_score
  - GIVEN a completed 500-PDF scan
  - WHEN JSONL is validated
  - THEN 100% of records with `has_ink: true` have `scores.double_score` as a finite float
  - Test: `debug_sv/test_tachon_method_scan.py::test_jsonl_schema_double_score_present`

- Scenario: JSONL schema validation rejects malformed records
  - GIVEN a record missing `flags_by_method.DOBLE_ESCRITURA`
  - WHEN schema validator runs
  - THEN validation fails with explicit field error
  - Test: `debug_sv/test_tachon_method_scan.py::test_jsonl_schema_validation`

---

### REQ-AGGREGATES: Summary, Co-occurrence, Histograms, Completion

After the scan completes, the system SHALL write aggregate artifacts derived from the JSONL (or in-memory aggregation during scan).

| Artifact | Path | Contents |
|----------|------|----------|
| Summary | `data/analysis_segunda_vuelta/tachon_method_scan_500_summary.json` | Per-method flag rates (ink subcells only), per-dept breakdown, per-label breakdown (`block`+`label`), PDF/subcell counts |
| Co-occurrence | `data/analysis_segunda_vuelta/tachon_method_cooccurrence.json` | 4×4 matrix of `flags_by_method` joint counts on ink subcells; conditional probabilities e.g. P(TACHON \| ZONA_SUCIA) |
| Histograms | `data/analysis_segunda_vuelta/tachon_method_histograms.json` | Score bins per method; percentiles p50, p90, p99, max (ink subcells) |
| Completion | `data/analysis_segunda_vuelta/tachon_method_scan_500_complete.json` | `elapsed_sec`, `workers`, `pdfs_ok`, `pdfs_error`, `error_rate`, `manifest_hash`, `run_id`, `scan_mode`, timestamps |
| Report | `data/analysis_segunda_vuelta/tachon_method_scan_500_report.md` | Human-readable tables: flag rates, co-occurrence, histogram percentiles, dept anomalies vs national baseline |

**Co-occurrence matrix shape:**

```json
{
  "methods": ["TACHON", "DOBLE_ESCRITURA", "DENSIDAD_ALTA", "ZONA_SUCIA"],
  "ink_subcells": 9500,
  "joint_counts": { "TACHON+ZONA_SUCIA": 120, "...": 0 },
  "matrix": [[n11, n12, n13, n14], [...], [...], [...]],
  "conditional": { "P_TACHON_given_ZONA_SUCIA": 0.85 }
}
```

**Histogram shape (per method):**

```json
{
  "tachon_score": { "bins": [...], "p50": 1.0, "p90": 1.0, "p99": 1.0, "max": 1.0, "n": 9500 }
}
```

- Scenario: Aggregates computed after full scan
  - GIVEN a JSONL fixture with known flag combinations across depts and labels
  - WHEN aggregate builder runs
  - THEN summary, co-occurrence, and histogram files match expected counts and percentiles
  - Test: `debug_sv/test_tachon_method_scan.py::test_aggregates_from_fixture`

- Scenario: Completion metadata records manifest hash
  - GIVEN a scan against a locked manifest
  - WHEN scan completes
  - THEN `tachon_method_scan_500_complete.json` includes `manifest_hash` matching the manifest file
  - Test: `debug_sv/test_tachon_method_scan.py::test_completion_manifest_hash`

---

### REQ-REPORTS: Markdown Report

The system SHALL generate `data/analysis_segunda_vuelta/tachon_method_scan_500_report.md` containing:

1. Run metadata (date, workers, elapsed, manifest hash, error rate)
2. Per-method flag rates on ink subcells (table)
3. Top departments by TACHON rate vs national baseline
4. Per-label hit rates (C1/C2 candidate rows vs NIVELACIÓN / total rows)
5. Co-occurrence table (4×4)
6. Score percentile table (p50/p90/p99 per method)
7. Note comparing validate baseline (dept 01 only, no `double_score`) when holdout PDFs overlap

- Scenario: Report renders required sections
  - GIVEN aggregate JSON fixtures
  - WHEN report generator runs
  - THEN markdown contains all seven sections with non-empty tables
  - Test: `debug_sv/test_tachon_method_scan.py::test_report_sections_present`

---

### REQ-OPTIONAL-CROPS: Top-N Crop Gallery

When `--save-crops` is set, the system MAY write up to `--crops-per-method` (default 50) highest-scoring crops per method to `data/analysis_segunda_vuelta/tachon_scan_crops/{method}/`. This is **optional** and non-blocking for merge.

---

### REQ-PREREQ: D2 Pipeline Dependency

The scan runner MUST import grid/subcell extraction from restored D2 files (`e14_worker.py`, `subcell_tachon.py`, `grid_detector_v2.py`). If any prerequisite file is missing, the scan CLI SHALL exit code `1` with an explicit message to merge PR #1.

- Scenario: Scan aborts without D2 worker files
  - GIVEN `debug_sv/e14_worker.py` is absent
  - WHEN `tachon_method_scan.py` starts
  - THEN it exits code `1` and prints prerequisite instructions
  - Test: `debug_sv/test_tachon_method_scan.py::test_missing_prereq_exit`

---

### REQ-NO-SRC-EDITS: Production Isolation

The change MUST NOT modify any file under `src/modules/analyzer/`. Detector thresholds and weights are read from existing `tachon_detector.py` behavior; per-method flag thresholds in the scan script MUST match `analyze_cell()` flag constants (0.45 / 0.50 / 0.60 / 0.50).

- Scenario: No src edits in PR diff
  - GIVEN the PR branch for this change
  - WHEN diff is inspected
  - THEN zero files under `src/modules/analyzer/` are modified
  - Test: manual gate `GATE-SCAN-06` (CI or review checklist)

---

## Test Matrix (pytest)

All tests live under `debug_sv/` and run without the full 500-PDF corpus (fixtures + mocks).

| ID | Test module | Test name | Covers |
|----|-------------|-----------|--------|
| T-M01 | `test_tachon_scan_manifest.py` | `test_manifest_exactly_500_all_depts` | REQ-MANIFEST allocation |
| T-M02 | `test_tachon_scan_manifest.py` | `test_manifest_reproducible_seed_42` | REQ-MANIFEST reproducibility |
| T-M03 | `test_tachon_scan_manifest.py` | `test_allocation_proportional_min_one` | REQ-MANIFEST algorithm |
| T-M04 | `test_tachon_scan_manifest.py` | `test_validate_holdout_tagging` | REQ-MANIFEST enrichment |
| T-M05 | `test_tachon_scan_manifest.py` | `test_manifest_hash_canonical` | REQ-MANIFEST hash stability |
| T-S01 | `test_tachon_method_scan.py` | `test_scan_emits_all_methods` | REQ-SCAN-CLI pipeline |
| T-S02 | `test_tachon_method_scan.py` | `test_per_method_flags_isolated` | REQ-SCAN-CLI isolation |
| T-S03 | `test_tachon_method_scan.py` | `test_skip_ocr_omits_digit` | REQ-SCAN-CLI flags |
| T-S04 | `test_tachon_method_scan.py` | `test_empty_cell_neutral_scores` | REQ-SCAN-CLI empty cells |
| T-S05 | `test_tachon_method_scan.py` | `test_jsonl_native_types` | REQ-SCAN-CLI serialization |
| T-J01 | `test_tachon_method_scan.py` | `test_jsonl_schema_double_score_present` | REQ-JSONL |
| T-J02 | `test_tachon_method_scan.py` | `test_jsonl_schema_validation` | REQ-JSONL |
| T-A01 | `test_tachon_method_scan.py` | `test_aggregates_from_fixture` | REQ-AGGREGATES |
| T-A02 | `test_tachon_method_scan.py` | `test_completion_manifest_hash` | REQ-AGGREGATES |
| T-R01 | `test_tachon_method_scan.py` | `test_report_sections_present` | REQ-REPORTS |
| T-P01 | `test_tachon_method_scan.py` | `test_missing_prereq_exit` | REQ-PREREQ |
| T-D01 | `test_tachon_method_scan.py` | `test_direct_detector_import_not_analyze_cell` | REQ-NO-SRC-EDITS / Approach A |

**Regression:** Existing D2 tests (`debug_sv/test_tachon_field_groups.py` 19/19) MUST pass after PR #1 merge; no regression required from this change if `src/` untouched.

**Run command:**

```powershell
python -m pytest debug_sv/test_tachon_scan_manifest.py debug_sv/test_tachon_method_scan.py -v
```

---

## Acceptance Gates (blocking merge)

| Gate | Criterion | Blocking |
|------|-----------|----------|
| GATE-SCAN-01 | PR #1 merged; D2 worker files present | **Yes** |
| GATE-SCAN-02 | `test_tachon_scan_manifest.py` all pass; `manifest_hash` stable on re-run | **Yes** |
| GATE-SCAN-03 | `test_tachon_method_scan.py` all pass | **Yes** |
| GATE-SCAN-04 | Full 500-PDF scan: `pdfs_error / 500 < 0.01` in completion JSON | **Yes** |
| GATE-SCAN-05 | 100% ink subcells in JSONL have `double_score` | **Yes** |
| GATE-SCAN-06 | Zero edits under `src/modules/analyzer/` | **Yes** |
| GATE-SCAN-07 | Summary, co-occurrence, histogram, completion, and report artifacts exist post-run | **Yes** |
| GATE-SCAN-08 | All 33 departments represented in manifest | **Yes** |
| GATE-SCAN-09 | D2 regression `test_tachon_field_groups.py` 19/19 | **Yes** |
| GATE-SCAN-10 | Wall time &lt; 10 min for 500 PDFs, 8 workers, `--skip-ocr` | No (informational) |
| GATE-SCAN-11 | Optional crop gallery | No |

---

## Non-Goals

| Item | Reason |
|------|--------|
| 90K full-corpus batch | Deferred; validate OK% ~24% |
| CNN / digit classifier retrain | Unrelated to heuristic pattern mining |
| Portal / triage UI | Downstream consumer |
| `notes_extractor` integration | Different fraud modality (textual enmiendas) |
| Edits to `tachon_detector.py` | D2 constraint; Approach B deferred to follow-up |
| Production threshold changes | Experiment informs calibration only |
| Force-include all `tachon_suspicious` PDFs | Stratification takes precedence over suspicious-only sampling |
| `analyze_cell()` / `analyze_cell_extended()` in production path | Experiment uses direct detector calls only |
| Committing multi-GB JSONL | May gitignore per project convention; summary artifacts required |

---

## Deliverables

| # | Artifact | Path |
|---|----------|------|
| 1 | Manifest generator | `debug_sv/build_tachon_scan_manifest.py` |
| 2 | Manifest | `data/analysis_segunda_vuelta/tachon_scan_500_manifest.json` |
| 3 | Scan script | `debug_sv/tachon_method_scan.py` |
| 4 | Tests | `debug_sv/test_tachon_scan_manifest.py`, `debug_sv/test_tachon_method_scan.py` |
| 5 | Subcell JSONL | `data/analysis_segunda_vuelta/tachon_method_scan_500.jsonl` |
| 6 | Summary JSON | `data/analysis_segunda_vuelta/tachon_method_scan_500_summary.json` |
| 7 | Co-occurrence | `data/analysis_segunda_vuelta/tachon_method_cooccurrence.json` |
| 8 | Histograms | `data/analysis_segunda_vuelta/tachon_method_histograms.json` |
| 9 | Completion | `data/analysis_segunda_vuelta/tachon_method_scan_500_complete.json` |
| 10 | Report | `data/analysis_segunda_vuelta/tachon_method_scan_500_report.md` |
| 11 | Optional crops | `data/analysis_segunda_vuelta/tachon_scan_crops/{method}/` |

---

## References

| Resource | Path |
|----------|------|
| Proposal | `openspec/changes/tachon-pattern-scan-500pdf/proposal.md` |
| Exploration | `openspec/changes/tachon-pattern-scan-500pdf/exploration.md` |
| Detectors | `src/modules/analyzer/tachon_detector.py` |
| D2 report | `debug_sv/reporte-d2-apply-completado-2026-06-22.md` |
| Validate baseline | `data/analysis_segunda_vuelta/e14c_validate_results.jsonl` |
| PDF corpus | `data/pdfs_e14c_segunda/` |