# Fase 2-fix Hybrid Block A + field_groups / tachon — Exploration

**Change:** `fase-2-fix-hybrid-tachon`  
**Date:** 2026-06-22  
**Phase:** explore (read-only)  
**Decision:** Option C (hybrid) — `anchor_block_a()` + keep `label_rows_by_structure` for C1/C2 and Block C

---

## Quick path

1. **Ship Deliverable 1 first:** add `anchor_block_a()` in `grid_detector_v2.py`, refactor `label_rows_by_structure()` to delegate Block A only.
2. **Validate D1:** extend `test_row_labeling.py` with offset regression cases; re-run `analyze_e14_batch.py --validate` (target OK% ≥ 24%, ideally recover ~43/55 URNA=0).
3. **Then Deliverable 2:** enrich JSONL with `field_groups` + per-subcell `tachon` via `tachon_detector.analyze_cell()` on crops (priority C1/C2).

---

## Executive context

Fase 2 replaced `guess_label()` with `label_rows_by_structure()`, causing a batch regression: **OK% 24% → 14%**, **55/100 URNA=0**. Root cause (43/55): Block A anchors to spurious header rows **Y≈833–964** instead of real nivelación **Y≈1023–1270** because the rule takes the *first 3 compact rows before C1* without a Y floor.

User chose **Option C (hybrid)**: dedicated Block A anchoring (`anchor_block_a()`) while preserving structural mega-gap labeling for C1/C2 and bottom Block C.

Deliverable 2 adds **`field_groups`** to JSONL and **tachon detection per subcell** using existing `src/modules/analyzer/tachon_detector.py` — import only, no `src/modules/analyzer/` edits unless later required.

---

## 1. Current pipeline

### Data flow (per PDF)

```
render_page(pdf)
  → detect_gray_v_lines / detect_gray_h_lines
  → process_h_lines (merge + filter)          # e14_worker, grid_detector_v2 main
  → build_grid(v_lines, h_lines)
  → label_rows_by_structure(grid_rows)        # ALL blocks in one function today
  → per-row: cell_has_ink → ocr_cell (SegmentedEngine)
  → fields{} + rows[] + structure_warnings
  → [if C1 or C2 missing] candidate_subcells.extract_candidates()
  → assess → JSONL record
```

### File roles

| File | Role |
|------|------|
| `debug_sv/grid_detector_v2.py` | Line detection, grid build, **row labeling**, ink/OCR helpers |
| `debug_sv/e14_worker.py` | Primary analysis (`_analyze_primary`), fallback merge, suspicious assessment |
| `debug_sv/analyze_e14_batch.py` | Parallel batch orchestration, checkpoint, `--validate` (100 PDFs) |
| `debug_sv/candidate_subcells.py` | C1/C2 fallback: per-row V-line refinement, subcell crops + OCR |
| `src/modules/analyzer/tachon_detector.py` | `analyze_cell(crop)` → `CellAnalysis` (unused in debug_sv today) |
| `src/modules/analyzer/ocr_engines.py` | `SegmentedEngine._read_single_digit` |

### Call sites for labeling

| Caller | Function | Notes |
|--------|----------|-------|
| `e14_worker._analyze_primary` L76 | `label_rows_by_structure` | **Primary path** — builds `fields` |
| `candidate_subcells.find_candidate_rows` L159 | `label_rows_by_structure` | Locates C1/C2 rows |
| `grid_detector_v2.main` L612 | `label_rows_by_structure` | Debug CLI |

### Known asymmetry (risk)

`candidate_subcells.extract_candidates` L280–281 uses **raw** `detect_gray_h_lines` **without** `process_h_lines`:

```python
h_lines = grid.detect_gray_h_lines(gray)
grid_rows = grid.build_grid(v_global, h_lines)
```

Worker and `grid_detector_v2.main` use `process_h_lines`. This is tracked as pending **Fase 1b** and can desync fallback C1/C2 row bounds from primary labeling.

---

## 2. Insertion points — Deliverable 1 (hybrid Block A)

### 2.1 New function: `anchor_block_a()`

**Location:** `debug_sv/grid_detector_v2.py`, in the "Row labelling" section (~L414), **before** `label_rows_by_structure()`.

**Suggested signature:**

```python
BLOCK_A_Y_FLOOR = 980  # or ratio-scaled from page height

def anchor_block_a(
    rows: list[dict],
    c1_idx: int,
    *,
    y_floor: int = BLOCK_A_Y_FLOOR,
) -> list[int]:
    """Return up to 3 row indices for VOTANTES/URNA/INCINER before c1_idx."""
```

**Proposed logic (from explore report + Option C):**

1. Restrict candidates to `i < c1_idx` with compact height (`ROW_MIN_H ≤ h ≤ ROW_TYPICAL_MAX_H`).
2. **Y floor:** require `y_mid >= y_floor` (≈980 px @ 300 DPI ref) — excludes spurious header rows Y≈833–964.
3. Take up to 3 consecutive compact rows in ascending Y order (not "first 3 globally").
4. Optional tie-break: prefer rows where ink/value will land (post-OCR validation can live in worker later; explore suggests value-in-INCINER pattern as diagnostic).

**Constants to add:** `BLOCK_A_Y_FLOOR` (980 or `int(page_h * 980/3500)`), possibly `BLOCK_A_Y_CEILING` (~1300).

### 2.2 Refactor `label_rows_by_structure()`

**Location:** `grid_detector_v2.py` L446–516.

**Split responsibilities:**

| Block | Owner | Current lines | Change |
|-------|-------|---------------|--------|
| **A** (VOTANTES, URNA, INCINER) | `anchor_block_a()` | L501–509 | **Replace** `pre_c1[:3]` with `anchor_block_a(labeled, c1_idx)` |
| **B** (C1, C2) | `label_rows_by_structure` | L474–499 | **Keep** mega-gap + mid-third fallback |
| **C** (BLANCO…SUMA_TOTAL) | `label_rows_by_structure` | L511–514 | **Keep** last 4 rows |
| UNK@ spacers | `label_rows_by_structure` | L468–472 | **Keep** |

**Pseudocode after refactor:**

```python
def label_rows_by_structure(rows):
    # ... init UNK@, compute gaps, find c1_idx/c2_idx (unchanged)
    _assign_labels(labeled, [c1_idx, c2_idx], LABELS_BLOCK_B)
    block_a_indices = anchor_block_a(labeled, c1_idx)
    _assign_labels(labeled, block_a_indices, LABELS_BLOCK_A)
    # Block C unchanged
```

### 2.3 Downstream callers — no API change expected

| File | Change |
|------|--------|
| `e14_worker.py` | **None** if `label_rows_by_structure` signature unchanged |
| `candidate_subcells.py` | **None** for D1; benefits from better C1 anchor indirectly |
| `test_row_labeling.py` | **Extend** with offset regression tests |

### 2.4 Regression cases to encode in tests

From `debug_sv/Reporte explore — 55 PDFs con URNA=0 (Fase 2).md`:

| Case | PDF / pattern | Expected after fix |
|------|---------------|-------------------|
| Offset (a) | `01_01_003_5002` — VOTANTES@846, URNA@946, value in INCINER@1035 | URNA@~1150, value in `fields["URNA"]` |
| Healthy | `01_01_001_5002` — URNA@1149 | Unchanged |
| Mega-gap (c) | `01_01_022_5002` | **Out of D1 scope** — C1/grid issue |
| Synthetic | `test_synthetic_spacer_gets_unk_at_1470` | Block A still at rows 0–2, spacer UNK |

---

## 3. Insertion points — Deliverable 2 (field_groups + tachon)

### 3.1 Tachon integration points

`tachon_detector.analyze_cell(cell_bgr)` expects a BGR crop — same as `crop_cell()` in `candidate_subcells.py` and padded OCR crops in `grid_detector_v2.ocr_cell`.

| Integration site | Priority | Mechanism |
|------------------|----------|-----------|
| `candidate_subcells.read_candidate_row` L198–220 | **High** | After `crop_cell`, call `analyze_cell(crop)` per digit subcell |
| `e14_worker._analyze_primary` L93–102 | Medium | Tachon on each of 3 cells per accepted row |
| `analyze_e14_batch.py` | None | Schema passthrough only |

**Import pattern (worker / candidate_subcells):**

```python
from src.modules.analyzer.tachon_detector import analyze_cell
# ...
tachon = analyze_cell(crop).to_dict()
```

### 3.2 Proposed `field_groups` JSONL schema

**Today** (`e14c_validate_results.jsonl`):

```json
{
  "pdf": "...",
  "dept": "01",
  "fields": {"URNA": 141, "C1_CEPEDA": 44, ...},
  "rows": [
    {"label": "URNA", "value": 141, "digits": ["1","4","1"],
     "confidences": [1.0, 0.97, 0.99], "y_mid": 1149}
  ],
  "structure_warnings": [],
  "rows_accepted": 9,
  "v_lines": [...],
  "h_lines_raw_count": 49,
  "h_lines_filtered_count": 12,
  "grid_rows_count": 11,
  "method": "primary",
  "fallback_triggered": false,
  "is_suspicious": false,
  "suspicious_reasons": []
}
```

**Proposed addition** (backward-compatible — new top-level key):

```json
{
  "field_groups": {
    "block_a": {
      "labels": ["VOTANTES", "URNA", "INCINER"],
      "fields": {
        "VOTANTES": {
          "value": 141,
          "y_mid": 1023,
          "y_top": 978,
          "y_bot": 1068,
          "label_source": "anchor_block_a",
          "subcells": [
            {
              "idx": 0,
              "x1": 905, "y1": 978, "x2": 998, "y2": 1068,
              "digit": "1",
              "confidence": 0.994,
              "has_ink": true,
              "tachon": {
                "score": 0.12,
                "tachon_score": 0.05,
                "density_score": 0.0,
                "noise_score": 0.0,
                "flags": [],
                "is_suspicious": false
              }
            }
          ]
        }
      }
    },
    "block_b": {
      "labels": ["C1_CEPEDA", "C2_ABELARDO"],
      "fields": { "...": "same subcell shape; v_lines_row when fallback" }
    },
    "block_c": {
      "labels": ["BLANCO", "NULOS", "NO_MARCADOS", "SUMA_TOTAL"],
      "fields": { "...": {} }
    }
  },
  "tachon_summary": {
    "suspicious_subcells": 2,
    "flags_by_label": {"C1_CEPEDA": ["TACHON"]}
  }
}
```

**Builder location:** new helper `_build_field_groups(labeled_rows, ocr_results, tachon_results)` in `e14_worker.py` (or `grid_detector_v2.py` if kept pure).

**Flat `rows[]` and `fields{}`:** keep for backward compatibility; `field_groups` is additive enrichment.

### 3.3 C1/C2 special handling

`candidate_subcells` already produces per-subcell crops with row-scoped V-lines. For D2:

1. Run tachon on each `crop_cell` output in `read_candidate_row`.
2. When fallback merges into record, nest under `field_groups.block_b.fields[label].subcells` with `v_lines_row` metadata.
3. Optionally flag `is_suspicious` at record level if any candidate subcell has `tachon.is_suspicious`.

---

## 4. Test files to extend

| File | Deliverable | New tests |
|------|-------------|-----------|
| `debug_sv/test_row_labeling.py` | **D1** | `test_anchor_block_a_skips_spurious_header_rows`, `test_offset_pdf_01_01_003_urna_not_zero`, `test_anchor_block_a_y_floor_constant` |
| `debug_sv/test_h_line_filter.py` | D1 | No change expected |
| `debug_sv/test_anchor_block_a.py` | D1 (optional) | Unit tests isolated from full PDF if logic grows |
| `debug_sv/test_tachon_field_groups.py` | **D2** (new) | Mock crop → `analyze_cell`; worker record shape; C1/C2 3-subcell tachon array length |
| `debug_sv/test_e14_worker.py` | D2 (new, optional) | End-to-end single PDF with `field_groups` key present |

**Existing baseline:** 16/16 PASS (`test_row_labeling` 9 + `test_h_line_filter` 7).

**PDF fixtures:**

- Good: `01_001_01_01_E14_PRE_01_001_001_01_01_001_5002.pdf`
- Offset bad: `01_001_01_01_E14_PRE_01_001_001_01_01_003_5002.pdf`
- Noisy grid: `01_001_01_03_E14_PRE_01_001_001_01_03_001_5003.pdf`

---

## 5. Deliverable dependencies

```
┌─────────────────────────────────────┐
│  D1: anchor_block_a + hybrid split  │  MUST ship first
│  (fixes fields{} / rows[] labels)   │
└──────────────┬──────────────────────┘
               │ correct row→label map
               ▼
┌─────────────────────────────────────┐
│  D2: field_groups + tachon/subcell  │  Depends on D1
│  (enrichment + visual flags)        │
└─────────────────────────────────────┘
```

| Dependency | Reason |
|------------|--------|
| D2 → D1 | `field_groups` groups by semantic blocks; wrong Block A labels produce misleading tachon attribution |
| D2 ⊥ Fase 1b | Tachon can run without `process_h_lines` in candidate_subcells, but row bounds may be wrong — recommend Fase 1b before or with D2 |
| Batch 90K | Blocked until D1 restores OK% ≥ Fase 1 baseline (24%) |

**Parallelizable after D1:** Fase 1b (`process_h_lines` in `candidate_subcells`) is independent but should land before relying on fallback+tachon for C1/C2.

---

## 6. Risks and unknowns

| Risk | Severity | Notes |
|------|----------|-------|
| Y floor 980 too rigid for national layout drift | **High** | Scale by `page_height` ratio; validate on non-Antioquia depts |
| Block C "last 4 rows" mislabel | **Medium** | Unchanged in hybrid; may cause BLANCO/NULOS swaps on short grids |
| 10 mega-gap cases (c) not fixed by D1 | **Medium** | ~18% of URNA=0 subset; separate grid/C1 work |
| `candidate_subcells` raw h_lines | **Medium** | Fallback row Y may disagree with primary |
| Tachon false positives (high recall design) | **Medium** | `TACHON_THRESHOLD=0.45`; may inflate `suspicious_subcells` |
| JSONL size + batch CPU | **Low** | +3 tachon analyses × ~9 rows × 100K PDFs |
| `e14c_validate_results.jsonl` accumulates runs | **Low** | Use `--validate` with `--reset` (built-in) for clean metrics |
| Modifying `src/modules/analyzer/` | **Low** | Restriction: import-only for tachon |

---

## 7. Files to touch (scope estimate)

### Deliverable 1 (~150–250 LOC)

| File | Est. LOC | Action |
|------|----------|--------|
| `debug_sv/grid_detector_v2.py` | +80–120 | `anchor_block_a()`, refactor `label_rows_by_structure` |
| `debug_sv/test_row_labeling.py` | +60–100 | Offset regression + unit tests |

### Deliverable 2 (~200–350 LOC)

| File | Est. LOC | Action |
|------|----------|--------|
| `debug_sv/e14_worker.py` | +80–150 | `_build_field_groups`, tachon in primary loop |
| `debug_sv/candidate_subcells.py` | +40–80 | Tachon per subcell; optional `process_h_lines` (Fase 1b) |
| `debug_sv/test_tachon_field_groups.py` | +80–120 | **New** |
| `debug_sv/analyze_e14_batch.py` | +0–20 | Docstring / optional `tachon_summary` in stats |

**Out of scope (unless explicitly requested):** `src/modules/analyzer/*`, batch 90K, portal integration.

---

## 8. Validation commands

```powershell
Set-Location "D:\Nucleux\tools\Analizador de Elecciones"

# Unit tests (baseline 16 + new)
python -m pytest debug_sv/test_row_labeling.py debug_sv/test_h_line_filter.py -v

# After D2
python -m pytest debug_sv/test_tachon_field_groups.py -v

# Batch validation (100 PDFs, isolated outputs, resets checkpoint)
python debug_sv/analyze_e14_batch.py --validate

# Single-PDF debug
python debug_sv/grid_detector_v2.py
python debug_sv/candidate_subcells.py --pdf data/pdfs_e14c_segunda/01_001_01_01_E14_PRE_01_001_001_01_01_003_5002.pdf

# Acceptance gates
# D1: OK% >= 24% (Fase 1 baseline); URNA=0 count << 55; pytest green
# D2: field_groups present in JSONL; C1/C2 subcells[].tachon populated
```

**Metrics source:** `data/analysis_segunda_vuelta/e14c_validate_complete.json`

| Metric | Fase 1 | Fase 2 (broken) | D1 target |
|--------|--------|-----------------|-----------|
| OK% | 24% | 14% | ≥ 24% |
| URNA=0 | — | 55/100 | ≪ 55 |
| arith_ok | 24 | 16 | ≥ 24 |

---

## 9. Checklist (for propose / design phase)

- [ ] `anchor_block_a()` uses Y floor (ratio-scaled), not first-3-compact globally
- [ ] C1/C2 mega-gap logic untouched
- [ ] Block C last-4 logic untouched
- [ ] Offset PDF `01_01_003_5002` assigns URNA to Y≈1150 in tests
- [ ] Gold PDF `01_01_001_5002` still passes 9 known labels
- [ ] `field_groups` additive to JSONL; flat `fields` retained
- [ ] Tachon runs on BGR crops matching OCR padding
- [ ] `candidate_subcells` uses `process_h_lines` (Fase 1b) before D2 fallback reliance
- [ ] No edits under `src/modules/analyzer/` without explicit approval

---

## 10. References

- `debug_sv/Reporte explore — 55 PDFs con URNA=0 (Fase 2).md` — offset diagnosis
- `debug_sv/fase-2-labeling-pausa-regresion-2026-06-22.md` — regression metrics, Option C
- `debug_sv/fase-1-h-line-filter-2026-06-22.md` — h-line pipeline
- `openspec/changes/segunda-vuelta/exploration.md` — tachon reusability note
- `data/analysis_segunda_vuelta/e14c_validate_results.jsonl` — current schema samples

---

## Next step

**Propose** change `fase-2-fix-hybrid-tachon`: spec D1 (`anchor_block_a` + tests + validate gate), then spec D2 (`field_groups` schema + tachon wiring).