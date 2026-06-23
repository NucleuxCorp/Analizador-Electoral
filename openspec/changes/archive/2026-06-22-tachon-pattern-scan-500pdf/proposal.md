# Proposal: Tachon Pattern Scan — 500 PDFs × Per-Method Isolation

**Change:** `tachon-pattern-scan-500pdf`  
**Date:** 2026-06-22  
**Status:** Proposal — ready for `sdd-spec`

---

## Intent

Run a **controlled 500-PDF experiment** that executes each visual alteration detector **in isolation** (not via the combined `analyze_cell()` gate) to mine patterns of enmiendas, tachones, and related cell-level fraud signals. The experiment must produce **score distributions, co-occurrence matrices, and per-department breakdowns** that inform threshold calibration before any production tuning.

**Why now:** The D2 validate run (100 PDFs, dept `01` only) proved that per-method behavior is masked by combined weighting — isolated TACHON flags **~93% of ink subcells** (1,783 / 1,918) while the combined score flags only **144**. Without isolated runs on a nationally representative sample, threshold tuning risks catastrophic false-positive rates.

---

## Product Assumptions

| Assumption | User answer |
|------------|-------------|
| 500-PDF sample must be **stratified by department** (representative national sample across 33 depts) | **YES** (explicit user decision) |
| Sample must NOT be dept-01-only like the current validate set | **YES** (implicit from stratification requirement) |
| Per-method isolation required before production threshold changes | **YES** (validated by explore findings) |
| `notes_extractor` (handwritten enmiendas) is a separate signal | **YES** — out of scope |
| 90K full-corpus batch, CNN retrain, portal UI | **NO** — out of scope |

---

## Scope

### In Scope

| Item | Description |
|------|-------------|
| **Stratified 500-PDF manifest** | Reproducible sample from `data/pdfs_e14c_segunda/` (90,838 PDFs, 33 depts) |
| **`debug_sv/tachon_method_scan.py`** | Self-contained scan script calling individual detector functions |
| **Per-method isolated runs** | TACHON, DOBLE_ESCRITURA, DENSIDAD_ALTA, ZONA_SUCIA separately + optional combined baseline |
| **Flat subcell JSONL** | One record per subcell with all 4 raw scores including `double_score` |
| **Aggregate reports** | Summary JSON, co-occurrence matrix, score histograms, completion metadata |
| **Human-readable summary** | Markdown report with histograms and co-occurrence tables |
| **D2 pipeline restore (prerequisite)** | Minimal grid + subcell crop worker deps from PR #1 or equivalent |

### Out of Scope

| Item | Reason |
|------|--------|
| 90K full-corpus batch | Explicitly deferred; validate OK% still ~24% |
| CNN / digit classifier retrain | Unrelated to heuristic pattern mining |
| Portal / triage UI | Downstream consumer; not needed to discover patterns |
| `notes_extractor` integration | Different fraud modality (textual vs. digit-cell visual) |
| Edits to `src/modules/analyzer/tachon_detector.py` | D2 constraint; use direct function calls from `debug_sv/` |
| Production threshold changes | Experiment output informs calibration; no auto-tuning |
| Crop gallery beyond optional top-N triage | Optional deliverable; cap disk usage |

---

## Sampling Specification

### Corpus

- **Source:** `data/pdfs_e14c_segunda/`
- **Total:** 90,838 PDFs across **33 departments** (filename prefix `NN_`)
- **Target sample size:** 500 PDFs

### Strategy: Stratified proportional with minimum floor

**Rejected alternatives:** first-500 alphabetically (biased to dept `01`), uniform random (under-represents small depts), tachon_suspicious-only (no negative class).

**Algorithm** (`random.seed(42)`):

1. Parse department from filename: `^(\d+)_`.
2. Group all PDFs by department; count `dept_count` per dept.
3. **Initial allocation:** `alloc[d] = max(1, round(500 × dept_count / 90838))` for each dept with ≥1 file.
4. **Adjust to exactly 500:**
   - If `sum(alloc) > 500`: decrement from largest depts (by `alloc`, then by `dept_count`) until sum = 500.
   - If `sum(alloc) < 500`: increment largest depts until sum = 500.
5. Within each dept: `random.shuffle(pdfs_in_dept)`; take first `alloc[d]` files.
6. Write manifest with metadata per row.

### Manifest file

**Path:** `data/analysis_segunda_vuelta/tachon_scan_500_manifest.json`

```json
{
  "seed": 42,
  "total_pdfs": 500,
  "total_corpus": 90838,
  "departments": 33,
  "allocation_formula": "proportional_with_min_1",
  "created_at": "2026-06-22T...",
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

**Expected allocation (approximate):** dept `01` ≈ 66, `16` ≈ 58, `31` ≈ 42, … tiny depts (`68`, `50`, `60`, `72`) ≈ 1 each → all 33 depts represented.

### Optional enrichment (non-blocking)

- Tag entries overlapping the 100-PDF validate set as `validate_holdout: true` for baseline comparison.
- Do **not** force-include all `tachon_suspicious` PDFs — stratification takes precedence.

---

## Per-Method Experiment Design

### Detectors (from `src/modules/analyzer/tachon_detector.py`)

| Method | Function | Flag threshold | Combined weight |
|--------|----------|----------------|-----------------|
| TACHON | `detect_tachon()` | `>= 0.45` | 0.40 |
| DOBLE_ESCRITURA | `detect_double_writing()` | `>= 0.50` | 0.30 |
| DENSIDAD_ALTA | `detect_density()` | `>= 0.60` | 0.20 |
| ZONA_SUCIA | `detect_noise()` | `>= 0.50` | 0.10 |

Combined baseline: `combined_score = tachon×0.40 + double×0.30 + density×0.20 + noise×0.10`; `is_suspicious_combined = combined_score >= 0.45`.

### Isolation approach (Approach A — recommended)

`tachon_method_scan.py` calls detector functions **directly**, not `analyze_cell()`:

```python
METHODS = {
    "TACHON":          (detect_tachon,          0.45),
    "DOBLE_ESCRITURA": (detect_double_writing,  0.50),
    "DENSIDAD_ALTA":   (detect_density,         0.60),
    "ZONA_SUCIA":      (detect_noise,           0.50),
}
```

**Single PDF pass** computes all 4 scores (~4× detector cost; still cheap vs. OCR). CLI:

```
python debug_sv/tachon_method_scan.py \
  --manifest data/analysis_segunda_vuelta/tachon_scan_500_manifest.json \
  --method all \
  --workers 8 \
  --skip-ocr
```

**Per-method mode:** `--method TACHON` (or `DOBLE_ESCRITURA`, `DENSIDAD_ALTA`, `ZONA_SUCIA`) sets `scan_mode` in output; all 4 scores still stored for cross-analysis.

### Pipeline per PDF

```
PDF → grid_detector_v2 → label_rows → subcell crops
    → per-subcell: detect_tachon, detect_double_writing, detect_density, detect_noise
    → flags_by_method + combined_score
    → JSONL line per subcell
```

### Subcell coverage

- Analyze all subcells with `has_ink` metadata (matches validate: ~19 ink subcells/PDF).
- Empty cells: record with zero scores and `has_ink: false` (NEUTRAL pattern from D2).
- Include `block`, `label`, `subcell_idx`, `digit`, `confidence` when OCR available (`--skip-ocr` omits digit/confidence).

### Downstream pattern mining (from aggregates)

- P(TACHON | ZONA_SUCIA) and full 4×4 co-occurrence matrix
- Per-label hit rates (C1/C2 vs. NIVELACIÓN rows)
- Dept-specific TACHON rate anomalies vs. national baseline

---

## Prerequisites

| Prerequisite | Status | Action |
|--------------|--------|--------|
| D2 worker files (`e14_worker.py`, `subcell_tachon.py`, `analyze_e14_batch.py`, `grid_detector_v2.py`) | **Missing** from workspace | **Merge PR #1** (`feat/d2-field-groups-tachon`) or vendor minimal grid+worker deps into `debug_sv/` |
| `tachon_detector.py` (4 detectors) | Present | Import only; no edits |
| PDF corpus `data/pdfs_e14c_segunda/` | Present (90,838 files) | Read-only |
| Validate baseline `e14c_validate_results.jsonl` | Present (100 PDFs, dept 01) | Comparison only; lacks `double_score` |

**Blocker resolution:** Scan script cannot run without subcell extraction. First apply step must restore D2 pipeline files from PR #1 branch before implementing `tachon_method_scan.py`.

---

## Deliverables

| # | Artifact | Path | Description |
|---|----------|------|-------------|
| 1 | Manifest generator | `debug_sv/build_tachon_scan_manifest.py` | Builds stratified 500-PDF manifest (seed=42) |
| 2 | Manifest | `data/analysis_segunda_vuelta/tachon_scan_500_manifest.json` | Locked reproducible sample |
| 3 | Scan script | `debug_sv/tachon_method_scan.py` | Batch runner with `--method`, `--manifest`, `--workers`, `--skip-ocr` |
| 4 | Subcell JSONL | `data/analysis_segunda_vuelta/tachon_method_scan_500.jsonl` | Flat per-subcell records |
| 5 | Summary JSON | `data/analysis_segunda_vuelta/tachon_method_scan_500_summary.json` | Per-method flag rates, per-dept/per-label breakdown |
| 6 | Co-occurrence | `data/analysis_segunda_vuelta/tachon_method_cooccurrence.json` | 4×4 flag co-occurrence matrix |
| 7 | Histograms | `data/analysis_segunda_vuelta/tachon_method_histograms.json` | Score bins per method (p50/p90/p99) |
| 8 | Completion metadata | `data/analysis_segunda_vuelta/tachon_method_scan_500_complete.json` | Elapsed, workers, errors, manifest hash |
| 9 | Markdown report | `data/analysis_segunda_vuelta/tachon_method_scan_500_report.md` | Human-readable summary with tables |
| 10 | Optional crops | `data/analysis_segunda_vuelta/tachon_scan_crops/{method}/` | Top-N highest scores per method (cap ~50/method) |

### JSONL record schema

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

---

## Success Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| PDF processing error rate | **< 1%** | `errors / 500` in completion metadata |
| Manifest reproducibility | **100%** | Re-run manifest builder with seed=42 → identical `manifest_hash` |
| Department coverage | **33 / 33** | All depts with ≥1 PDF in corpus represented |
| Per-method hit rates documented | **Required** | Summary JSON + markdown report per method on ink subcells |
| `double_score` present | **100%** of ink subcell records | JSONL schema validation |
| Score distributions | **Required** | Histogram JSON with p50/p90/p99 per method |
| Co-occurrence matrix | **Required** | 4×4 `flags_by_method` co-occurrence |
| Wall time | **< 10 min** | 500 PDFs, 8 workers (validate baseline: 0.28 s/PDF) |
| No production file changes | **Required** | Zero edits under `src/modules/analyzer/` |

---

## Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| D2 worker files missing | **High** | Merge PR #1 first; scan script blocked until restore |
| TACHON over-fires (~93% ink cells) | **High** | Primary deliverable = score distributions + optional crop gallery, not boolean precision |
| Subcell crop misalignment | Medium | Spot-check top-N crops per dept in optional gallery |
| `double_score` absent from legacy validate JSONL | Medium | New scan mandatory; validate used for TACHON/density/noise baseline only |
| Single-dept validate misleading | Medium | **Resolved** by stratified 500-PDF manifest |
| Multiprocess JSON numpy types | Low | Reuse `_json_safe_tachon()` pattern from D2 report |
| Premature threshold tuning | Medium | Proposal explicitly defers production changes |

---

## Rollback Plan

All outputs are additive under `debug_sv/` and `data/analysis_segunda_vuelta/`:

1. Delete `debug_sv/tachon_method_scan.py`, `debug_sv/build_tachon_scan_manifest.py`.
2. Delete `data/analysis_segunda_vuelta/tachon_scan_500_manifest.json`, `tachon_method_scan_500*.jsonl/json/md`, co-occurrence, histograms, optional crops.
3. If D2 files were vendored only for this change, revert those additions separately.
4. `src/modules/analyzer/tachon_detector.py` and production inference paths remain untouched.

No database, portal, or model changes to revert.

---

## PR Plan

| Item | Value |
|------|-------|
| **PR count** | **1** (single PR) |
| **Branch** | `feat/tachon-pattern-scan-500pdf` (from `develop` after PR #1 merge) |
| **File scope** | `debug_sv/` only (+ generated artifacts committed or gitignored per project convention) |
| **Blocked by** | PR #1 (`feat/d2-field-groups-tachon`) merge or equivalent worker restore |
| **Review focus** | Manifest reproducibility, JSONL schema, per-method isolation correctness, no `src/` edits |

### PR contents

```
debug_sv/build_tachon_scan_manifest.py   # NEW
debug_sv/tachon_method_scan.py           # NEW
data/analysis_segunda_vuelta/tachon_scan_500_manifest.json          # NEW (committed)
data/analysis_segunda_vuelta/tachon_method_scan_500.jsonl           # NEW (or gitignored if large)
data/analysis_segunda_vuelta/tachon_method_scan_500_summary.json  # NEW
data/analysis_segunda_vuelta/tachon_method_cooccurrence.json        # NEW
data/analysis_segunda_vuelta/tachon_method_histograms.json        # NEW
data/analysis_segunda_vuelta/tachon_method_scan_500_complete.json  # NEW
data/analysis_segunda_vuelta/tachon_method_scan_500_report.md       # NEW
```

### Post-merge follow-up (separate change)

- Optional `analyze_cell_extended()` in `tachon_detector.py` to permanently expose `double_score` in production JSONL (Approach B from exploration).

---

## Dependencies

| Dependency | Source |
|------------|--------|
| `tachon_detector.py` detectors | `src/modules/analyzer/tachon_detector.py` |
| Grid + subcell pipeline | PR #1: `e14_worker.py`, `subcell_tachon.py`, `grid_detector_v2.py` |
| PDF corpus | `data/pdfs_e14c_segunda/` (90,838 files) |
| Validate baseline | `data/analysis_segunda_vuelta/e14c_validate_results.jsonl` |
| OpenCV, numpy | Existing project deps |

---

## Performance Estimate

| Scenario | Estimate |
|----------|----------|
| Full pipeline (OCR + grid + all methods), 8 workers | ~2–3 min |
| Grid + subcells only (`--skip-ocr`), 8 workers | ~1–2 min |
| Pure detector CPU (38K–54K subcell analyses) | 38–270 s |

Bottleneck is PDF I/O and grid detection, not the 4 OpenCV detectors.

---

## References

| Resource | Path |
|----------|------|
| Exploration artifact | `openspec/changes/tachon-pattern-scan-500pdf/exploration.md` |
| Detectors | `src/modules/analyzer/tachon_detector.py` |
| D2 completion report | `debug_sv/reporte-d2-apply-completado-2026-06-22.md` |
| Validate results | `data/analysis_segunda_vuelta/e14c_validate_results.jsonl` |
| PDF corpus | `data/pdfs_e14c_segunda/` |