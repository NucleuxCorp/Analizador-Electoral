# Exploration: Tachon Pattern Scan — 500 PDFs × Per-Method Isolation

**Change:** `tachon-pattern-scan-500pdf`  
**Date:** 2026-06-22  
**Status:** Exploration complete — ready for `sdd-propose`

---

## Executive Summary

The project needs a **controlled 500-PDF experiment** that runs each visual alteration detector **separately** (not via the combined `analyze_cell()` gate) to mine patterns of enmiendas, tachones, and similar cell-level fraud signals.

**Key finding:** The existing D2 validate run (`e14c_validate_results.jsonl`, 100 PDFs) already stores per-subcell `tachon_score`, `density_score`, and `noise_score`, but **not** `double_score` (DOBLE_ESCRITURA). More critically, **TACHON in isolation flags ~93% of ink subcells** (1,783 / 1,918) while the combined score flags only 144 — proving that per-method isolation is necessary before any production threshold tuning.

**Blocker:** The D2 integrated pipeline files (`debug_sv/e14_worker.py`, `subcell_tachon.py`, `analyze_e14_batch.py`) are **referenced in completion reports but absent from the workspace**. Only output JSONL and spike utilities remain. The 500-PDF experiment must either restore those files or ship a self-contained scan script.

---

## Current State

### 1. Core detectors — `src/modules/analyzer/tachon_detector.py`

Four independent OpenCV heuristics, combined in `analyze_cell()`:

| Method | Function | Flag threshold | Weight in combined |
|--------|----------|----------------|-------------------|
| TACHON | `detect_tachon()` — morphological H/diag kernels | `tachon_score >= 0.45` | 0.40 |
| DOBLE_ESCRITURA | `detect_double_writing()` — excess connected components | `double >= 0.5` | 0.30 |
| DENSIDAD_ALTA | `detect_density()` — ink area ratio | `density >= 0.6` | 0.20 |
| ZONA_SUCIA | `detect_noise()` — gray smudge band 70–200 | `noise >= 0.5` | 0.10 |

Combined suspicion: `score >= 0.45` → `is_suspicious=True`.

**Gaps in `CellAnalysis`:**

- `double_score` is computed but **not stored** on the dataclass or in `to_dict()`.
- `flags` only reflect per-method thresholds; `is_suspicious` uses the **weighted sum**, so a cell can have `TACHON` flag yet `is_suspicious=false` (common in validate data).
- `analyze_form_cells()` only returns suspicious cells — not suitable for pattern mining (need all cells).

### 2. D2 integrated pipeline (documented, files missing)

Per `debug_sv/reporte-d2-apply-completado-2026-06-22.md`:

```
PDF → grid_detector_v2 → label_rows → subcell crops
     → enrich_subcell_tachon() → analyze_cell()
     → field_groups + tachon_summary → JSONL
```

| File (expected) | Role |
|-----------------|------|
| `debug_sv/e14_worker.py` | Primary worker, `_build_field_groups`, `_build_tachon_summary` |
| `debug_sv/subcell_tachon.py` | `enrich_subcell_tachon()`, `NEUTRAL_TACHON` for empty cells |
| `debug_sv/analyze_e14_batch.py` | Batch runner, `--validate` mode (100 PDFs) |
| `debug_sv/candidate_subcells.py` | Fallback Block B subcells |
| `debug_sv/grid_detector_v2.py` | H-line filter, grid rows (**also missing**) |

**Present in workspace:** `debug_sv/grid_detector.py`, spike scripts, markdown reports, validate JSONL outputs.

### 3. Validate output — `data/analysis_segunda_vuelta/e14c_validate_results.jsonl`

| Metric | Value |
|--------|-------|
| Records | 100 |
| Department | 100% `01` (Amazonas) |
| `tachon_suspicious` in `suspicious_reasons` | 56 PDFs |
| `suspicious_subcells` (combined threshold) | 144 total |
| Analyzed subcells (ink + empty with neutral) | 1,918 total (~19.2/PDF) |
| Ink subcells (`has_ink=true`) | 1,918 |
| Batch elapsed | 27.8 s / 100 PDFs (8 workers) |

**Subcell `tachon` payload (from D2):**

```json
{
  "score": 0.431,
  "tachon_score": 1.0,
  "density_score": 0.0,
  "noise_score": 0.308,
  "flags": ["TACHON"],
  "is_suspicious": false
}
```

Note: no `double_score`, no per-method isolation metadata.

**Simulated per-method hits on validate ink subcells (current thresholds):**

| Method (isolated) | Subcells flagged |
|-------------------|------------------|
| TACHON (`>= 0.45`) | 1,783 (92.9%) |
| ZONA_SUCIA (`>= 0.5`) | 108 (5.6%) |
| DENSIDAD_ALTA (`>= 0.6`) | 19 (1.0%) |
| COMBINED (`score >= 0.45`) | 145 (7.6%) |
| `is_suspicious` flag | 144 |

**Score percentiles (ink subcells, n=1,918):**

| Score | p50 | p90 | p99 | max |
|-------|-----|-----|-----|-----|
| `tachon_score` | 1.000 | 1.000 | 1.000 | 1.000 |
| `noise_score` | 0.133 | 0.427 | 0.684 | 1.000 |
| `density_score` | 0.000 | 0.000 | 0.562 | 1.000 |
| `combined score` | 0.405 | 0.446 | 0.565 | 0.672 |

**Top `flags_by_label` in `tachon_summary`:** TACHON dominates every field label (49–90 PDFs each); ZONA_SUCIA is secondary (12–14 on candidate/total rows).

### 4. Handwritten notes — `src/modules/analyzer/notes_extractor.py`

Separate signal: EasyOCR on freeform “Notas o Constancias” regions (pages 1–2). **Out of scope** for this experiment — different fraud modality (textual enmiendas vs. digit-cell alterations).

### 5. PDF corpus — `data/pdfs_e14c_segunda/`

| Metric | Value |
|--------|-------|
| Total PDFs | **90,838** |
| Departments | **33** (prefix `NN_` in filename) |
| Largest depts | 01 (12,076), 16 (10,511), 31 (7,571), 15 (5,620), 03 (4,781) |
| Smallest depts | 68 (56), 50 (72), 60 (107), 72 (112) |

---

## Gap Analysis

| Gap | Impact |
|-----|--------|
| `analyze_cell()` runs all 4 detectors but gates on **combined** score | Per-method patterns masked; TACHON noise hidden behind weighting |
| `double_score` not exposed in JSONL | Cannot mine DOBLE_ESCRITURA patterns from existing validate data |
| TACHON detector fires on ~93% of ink subcells at current threshold | Isolated TACHON run will produce huge hit rate — experiment must output **score distributions**, not just boolean flags |
| D2 pipeline source missing | Cannot re-run even 100-PDF validate without restore |
| Validate sample is single-dept | Patterns may not generalize; 500-PDF sample must be stratified |
| Empty cells get `NEUTRAL_TACHON` (zeros) | Correct for precision; experiment should record `has_ink` and optionally analyze all 27 subcells for baseline |

---

## Technical Approaches

### Approach A — Dedicated scan script calling individual detectors (RECOMMENDED)

**Idea:** New `debug_sv/tachon_method_scan.py` that reuses grid/subcell extraction from D2 worker but calls `detect_tachon`, `detect_double_writing`, `detect_density`, `detect_noise` **directly**, emitting per-method flags and raw scores without combined weighting.

**Implementation sketch:**

```python
METHODS = {
    "TACHON":           (detect_tachon,           0.45),
    "DOBLE_ESCRITURA":  (detect_double_writing,   0.50),
    "DENSIDAD_ALTA":    (detect_density,          0.60),
    "ZONA_SUCIA":       (detect_noise,            0.50),
}

def analyze_subcell_isolated(crop_bgr, method: str | None = None) -> dict:
    scores = {
        "tachon_score": detect_tachon(crop),
        "double_score": detect_double_writing(crop),
        "density_score": detect_density(crop),
        "noise_score": detect_noise(crop),
    }
    flags = {name: scores[key] >= thr for name, (_, thr) in METHODS.items()}
    # Optional: --method TACHON runs only one detector + emits flag
    return {"scores": scores, "flags_by_method": flags, ...}
```

**Pros:**

- No changes to `src/modules/analyzer/tachon_detector.py` (respects D2 constraint)
- Full `double_score` visibility
- Single PDF pass computes all 4 scores (~4× detector cost, still cheap vs. OCR)
- CLI flags: `--method TACHON`, `--method all`, `--limit 500`, `--manifest path`

**Cons:**

- Must restore or reimplement subcell extraction (grid + v_lines + labeling)
- Duplicates scoring logic already in `analyze_cell()` (mitigate with thin wrapper)

**Effort:** Medium (1–2 days if D2 worker restored from git/PR; longer if rewritten from reports).

---

### Approach B — Extend `tachon_detector.py` with `analyze_cell_extended()` + batch flag

**Idea:** Add a non-breaking function in `tachon_detector.py`:

```python
def analyze_cell_extended(cell) -> dict:
    # All 4 raw scores + per-method flags + combined (existing logic)
    # Includes double_score in output
```

Batch runner calls `analyze_cell_extended` instead of `analyze_cell`. Optional `--active-method TACHON` sets only that method's flag as the record's `primary_flag`.

**Pros:**

- Single canonical scoring location; fixes `double_score` omission permanently
- Easier for future D3/D4 iterations

**Cons:**

- Violates prior constraint “no edits to `src/modules/analyzer/`” from D2
- Requires proposal approval before apply

**Effort:** Low code change; same pipeline restore dependency as A.

---

### Approach C — Post-process existing validate JSONL (NOT SUFFICIENT ALONE)

**Idea:** Derive per-method flags from stored `tachon_score`, `density_score`, `noise_score` in `e14c_validate_results.jsonl`.

**Pros:** Zero new PDF processing for 100 PDFs.

**Cons:**

- **No `double_score`** → DOBLE_ESCRITURA cannot be analyzed
- Only 100 single-dept PDFs
- TACHON distributions already show detector is miscalibrated — post-hoc confirms problem but cannot fix crop pipeline issues

**Verdict:** Useful as **baseline comparison** only; not a substitute for the 500-PDF run.

---

### Approach D — Four separate batch runs with patched thresholds

**Idea:** Temporarily set impossible thresholds on 3 methods inside a forked `analyze_cell()`, leaving one active per run. E.g. run 1: only TACHON threshold real, others set to `2.0`.

**Pros:** Reuses `analyze_cell()` unchanged.

**Cons:** Hacky, error-prone, 4× full batch wall time if run sequentially, still omits `double_score` in output unless `to_dict()` patched.

**Verdict:** Avoid.

---

### Recommended path

**Approach A** for the experiment, with optional **Approach B** as a follow-up change if `double_score` should live permanently in production JSONL.

---

## Sampling Strategy for 500 PDFs

| Strategy | Verdict |
|----------|---------|
| First 500 alphabetically | **Reject** — heavily biased to dept `01` |
| Random 500 (uniform) | Acceptable; under-represents small depts |
| **Stratified proportional by dept** | **Recommended** |
| Validate 100 + 400 new | Good for continuity with known baseline |
| 500 from `tachon_suspicious` only | **Reject** — no negative class for pattern contrast |

### Recommended: Stratified proportional sample (seed=42)

1. Parse dept prefix from filename (`^(\d+)_`).
2. Allocate `round(500 × dept_count / 90838)` per dept; minimum 1 PDF for depts with ≥1 file.
3. Redistribute remainder to largest depts after min floor.
4. `random.seed(42)`; shuffle selection within each dept.
5. Write manifest: `data/analysis_segunda_vuelta/tachon_scan_500_manifest.json`.

**Expected allocation (approx.):** dept 01 ≈ 66, 16 ≈ 58, 31 ≈ 42, …, tiny depts 1 each → ~33 depts represented.

### Optional enrichment

- Force-include all 56 `tachon_suspicious` PDFs from validate set (if not already in sample).
- Tag manifest rows: `validate_holdout`, `stratum_dept`, `arith_suspicious`.

---

## Output Artifacts

### Primary: flat subcell JSONL

**Path:** `data/analysis_segunda_vuelta/tachon_method_scan_500.jsonl`  
**Granularity:** one line per subcell (enables SQL/pandas pattern mining)

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

### Aggregates

| Artifact | Contents |
|----------|----------|
| `tachon_method_scan_500_summary.json` | Per-method flag rates, per-dept/per-label breakdown |
| `tachon_method_cooccurrence.json` | 4×4 flag co-occurrence matrix |
| `tachon_method_histograms.json` | Score bins per method (p50/p90/p99) |
| `tachon_method_scan_500_complete.json` | Run metadata: elapsed, workers, errors, manifest hash |

### Optional (human triage)

- `data/analysis_segunda_vuelta/tachon_scan_crops/{method}/{pdf_stem}_{label}_{idx}.png` — top-N highest scores per method (cap ~50/method to limit disk).

### Flag co-occurrence analysis (downstream)

Compute from `flags_by_method`:

- P(TACHON | ZONA_SUCIA)
- Methods that fire together on C1/C2 vs. NIVELACIÓN rows
- Dept-specific TACHON rate anomalies

---

## Performance Estimate

### Work units

| Scenario | Subcell analyses |
|----------|------------------|
| 9 fields × 3 digits = 27 subcells/PDF, all analyzed | 500 × 27 × 4 = **54,000** |
| Ink-only (~19/PDF, matches validate) | 500 × 19 × 4 ≈ **38,000** |

OpenCV per crop: ~1–5 ms → **38–270 s** of pure detector CPU.

### End-to-end (including PDF render + grid + OCR)

From validate: 100 PDFs in 27.8 s (8 process workers) ≈ **0.28 s/PDF** full pipeline.

| Mode | 500 PDFs estimate |
|------|-------------------|
| Full pipeline (OCR + grid + all methods) | **~2–3 min** (8 workers), dominated by OCR |
| Grid + subcells only, `--skip-ocr` | **~1–2 min** |
| Re-crop from cached `field_groups` (100 PDFs only) | **< 10 s** |

**Conclusion:** 500 PDFs is feasible on a single workstation. Bottleneck is PDF I/O and grid detection, not the 4 detectors.

---

## What NOT To Do

| Anti-pattern | Reason |
|--------------|--------|
| Batch 90K PDFs | Explicitly out of scope; OK% still ~24% on validate |
| Retrain CNN / digit classifier | Unrelated to heuristic pattern mining |
| Portal / triage UI | Downstream consumer; not needed to discover patterns |
| Combine with `notes_extractor` | Different signal; confounds cell-level visual analysis |
| Tune production `TACHON_THRESHOLD` before reviewing score histograms | 93% ink-cell hit rate proves premature tuning risk |
| Block experiment on missing D2 files without recovery plan | Restore from git/PR-2 or reimplement minimal worker first |

---

## Dependencies & Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| `debug_sv` worker files missing | **High** | Restore from `fase-2-fix-hybrid-tachon` branch/PR; or extract minimal crop logic from validate JSONL coords |
| TACHON detector over-fires on grid/digit strokes | **High** | Experiment primary deliverable = score distributions + crop gallery, not boolean precision |
| `double_score` absent from legacy JSONL | Medium | Approach A/B mandatory |
| Single-dept validate misleading | Medium | Stratified 500-PDF manifest |
| Subcell crop misalignment | Medium | Spot-check crops from top flags per dept |
| Multiprocess JSON numpy types | Low | Reuse `_json_safe_tachon()` from D2 report |

---

## Recommended Next Steps (`sdd-propose`)

1. **Restore** `debug_sv/e14_worker.py`, `subcell_tachon.py`, `analyze_e14_batch.py`, `grid_detector_v2.py` (prerequisite).
2. **Propose** `tachon_method_scan.py` with `--manifest`, `--method {TACHON,DOBLE_ESCRITURA,DENSIDAD_ALTA,ZONA_SUCIA,all}`, `--skip-ocr`.
3. **Lock** stratified 500-PDF manifest with seed 42.
4. **Define** acceptance: 4 summary histograms, co-occurrence matrix, <1% PDF processing errors.
5. **Defer** `tachon_detector.py` changes to optional follow-up (`analyze_cell_extended`).

---

## References

| Resource | Path |
|----------|------|
| Detectors | `src/modules/analyzer/tachon_detector.py` |
| D2 completion report | `debug_sv/reporte-d2-apply-completado-2026-06-22.md` |
| Validate results | `data/analysis_segunda_vuelta/e14c_validate_results.jsonl` |
| Validate stats | `data/analysis_segunda_vuelta/e14c_validate_complete.json` |
| PDF corpus | `data/pdfs_e14c_segunda/` (90,838 files, 33 depts) |
| Notes (excluded) | `src/modules/analyzer/notes_extractor.py` |