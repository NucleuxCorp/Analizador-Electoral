# Spec D2: field_groups + per-subcell tachon

**Change:** `fase-2-fix-hybrid-tachon`  
**Deliverable:** D2 (field_groups + tachon enrichment)  
**Date:** 2026-06-22  
**Phase:** spec  
**Depends on:** D1 (`anchor_block_a` — correct row→label map)  
**Strict TDD:** Tests in `debug_sv/test_tachon_field_groups.py` MUST be written and MUST fail before implementation.

---

## 1. Overview

Deliverable 2 enriches the E-14C segunda-vuelta JSONL worker output with hierarchical field metadata (`field_groups`) and per-digit visual fraud signals (`tachon` on each subcell crop). Candidate vote rows (C1/C2) are first-class: each of three digit subcells carries OCR confidence plus `analyze_cell()` scores imported from `src/modules/analyzer/tachon_detector.py` (import only — no edits under `src/modules/analyzer/`).

**Product outcome:** Fraud analysts and ML pipelines can triage actas by `tachon_summary` and drill into `field_groups.block_b` without breaking existing consumers of flat `fields` and `rows`.

### 1.1 Dependency on D1

| Aspect | Why D1 blocks D2 |
|--------|------------------|
| Block assignment | `field_groups` nests fields under `block_a` / `block_b` / `block_c` by semantic label |
| Tachon attribution | Wrong URNA/C1 row labels attach tachon scores to the wrong Y band |
| `label_source` | Block A entries expect `anchor_block_a` after D1; Block B/C remain `structure` |

D2 MAY be specified and tested independently (mock labels), but MUST NOT merge until D1 gates pass. Post-D1 baseline metrics must not regress.

### 1.2 Scope boundary

| In scope | Out of scope |
|----------|--------------|
| Additive `field_groups`, `tachon_summary` on worker records | Breaking changes to `fields` / `rows` |
| `_build_field_groups()` helper in `debug_sv/e14_worker.py` | Editing `tachon_detector.py` thresholds |
| Tachon on primary-path subcells (accepted rows) | Portal / UI integration |
| Tachon on `candidate_subcells` crops (C1/C2 priority) | National X/Y calibration (Fase 3) |
| `test_tachon_field_groups.py` (new) | Batch 90K run |
| Extend `_assess()` with tachon-derived `suspicious_reasons` | D1 `anchor_block_a` implementation |
| Import: `from src.modules.analyzer.tachon_detector import analyze_cell` | Retraining CNN |

---

## 2. Requirements

### 2.1 Schema requirements

| ID | Requirement |
|----|-------------|
| **REQ-D2-001** | Every successful worker record (`method` ∈ `primary`, `primary+fallback`) MUST include top-level `field_groups` with keys `block_a`, `block_b`, `block_c`. |
| **REQ-D2-002** | Each block MUST include `labels` (ordered tuple as string list) and `fields` (object keyed by label). |
| **REQ-D2-003** | Each field entry under `fields[label]` MUST include: `value`, `y_mid`, `y_top`, `y_bot`, `label_source`, `subcells` (array length 0–3). |
| **REQ-D2-004** | Each subcell MUST include: `idx`, `x1`, `y1`, `x2`, `y2`, `digit`, `confidence`, `has_ink`, `tachon`. |
| **REQ-D2-005** | `tachon` on each subcell MUST be the dict returned by `CellAnalysis.to_dict()` (fields: `score`, `tachon_score`, `density_score`, `noise_score`, `flags`, `is_suspicious`). |
| **REQ-D2-006** | When at least one `analyze_cell()` call executes, record MUST include `tachon_summary` with `suspicious_subcells` (int) and `flags_by_label` (object). |
| **REQ-D2-007** | Flat `fields` and `rows` builders MUST remain unchanged in semantics; `field_groups` is enrichment only. |

### 2.2 Tachon execution requirements

| ID | Requirement |
|----|-------------|
| **REQ-D2-010** | Run `analyze_cell(crop)` on BGR crops using the same inner padding as OCR: `CELL_PAD` (8 px), i.e. `img[y1+pad:y2-pad, x1+pad:x2-pad]`. |
| **REQ-D2-011** | **Priority path:** `candidate_subcells.read_candidate_row` MUST attach tachon to each subcell before returning the candidate dict. |
| **REQ-D2-012** | **Primary path:** `_analyze_primary` loop MUST run tachon on each of up to 3 cells per accepted row with a known label. |
| **REQ-D2-013** | **Empty-cell policy:** When `has_ink` is `false`, SKIP `analyze_cell()`; emit neutral tachon `{score:0, tachon_score:0, density_score:0, noise_score:0, flags:[], is_suspicious:false}`. |
| **REQ-D2-014** | Block B fields sourced from fallback MUST include `v_lines_row` (list of 4 ints) on the field entry. |
| **REQ-D2-015** | `UNK@*` rows MUST NOT appear in `field_groups`. |

### 2.3 Assessment requirements

| ID | Requirement |
|----|-------------|
| **REQ-D2-020** | If any subcell has `tachon.is_suspicious === true`, append `tachon_suspicious` to `suspicious_reasons` (once per record). |
| **REQ-D2-021** | Tachon MUST NOT alone set `is_suspicious=true`; existing arithmetic / missing-field rules in `_assess()` remain authoritative for `is_suspicious`. |
| **REQ-D2-022** | Per-label flag detail MUST be aggregated in `tachon_summary.flags_by_label` (e.g. `{"C1_CEPEDA": ["TACHON"]}`). |

### 2.4 Block label constants

Reuse from `grid_detector_v2.py`:

```python
LABELS_BLOCK_A = ("VOTANTES", "URNA", "INCINER")
LABELS_BLOCK_B = ("C1_CEPEDA", "C2_ABELARDO")
LABELS_BLOCK_C = ("BLANCO", "NULOS", "NO_MARCADOS", "SUMA_TOTAL")
```

`_build_field_groups()` routes each known label to the corresponding block. Missing labels produce empty `fields` for that block but `labels` array is always present.

---

## 3. JSON schema

### 3.1 Top-level record (additive keys only)

Existing keys (`pdf`, `dept`, `fields`, `rows`, `structure_warnings`, `rows_accepted`, `v_lines`, `h_lines_*`, `grid_rows_count`, `method`, `fallback_triggered`, `is_suspicious`, `suspicious_reasons`, `fallback_candidates`) are unchanged.

**New keys:**

| Key | Type | When present |
|-----|------|--------------|
| `field_groups` | object | All successful primary / primary+fallback records |
| `tachon_summary` | object | When ≥1 `analyze_cell()` executed (inked subcells only) |

Error records (`error` key) MAY omit both.

### 3.2 `field_groups` shape

```json
{
  "field_groups": {
    "block_a": {
      "labels": ["VOTANTES", "URNA", "INCINER"],
      "fields": {
        "URNA": {
          "value": 141,
          "y_mid": 1149,
          "y_top": 1105,
          "y_bot": 1193,
          "label_source": "anchor_block_a",
          "subcells": [ /* Subcell, length 3 */ ]
        }
      }
    },
    "block_b": {
      "labels": ["C1_CEPEDA", "C2_ABELARDO"],
      "fields": {
        "C1_CEPEDA": {
          "value": 44,
          "y_mid": 1796,
          "y_top": 1750,
          "y_bot": 1842,
          "label_source": "structure",
          "v_lines_row": [905, 998, 1091, 1187],
          "subcells": [ /* Subcell, length 3 */ ]
        }
      }
    },
    "block_c": {
      "labels": ["BLANCO", "NULOS", "NO_MARCADOS", "SUMA_TOTAL"],
      "fields": {}
    }
  }
}
```

**Field entry (`fields[label]`):**

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `value` | int \| null | yes | Same as `fields[label]` when present |
| `y_mid` | int | yes | Row center Y |
| `y_top` | int | yes | Row top bound |
| `y_bot` | int | yes | Row bottom bound |
| `label_source` | string | yes | `"anchor_block_a"` (post-D1 Block A) or `"structure"` |
| `v_lines_row` | int[4] | block_b fallback only | Per-row refined V-lines from `read_candidate_row` |
| `subcells` | Subcell[] | yes | May be `[]` if row skipped (not accepted) |

### 3.3 `Subcell` object

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `idx` | int | yes | 0-based digit position (hundreds, tens, units) |
| `x1`, `y1`, `x2`, `y2` | int | yes | Outer cell bounds (unpadded grid coordinates) |
| `digit` | string | yes | `"0"`–`"9"` |
| `confidence` | float | yes | OCR confidence, 3 decimal places |
| `has_ink` | bool | yes | From `cell_has_ink()` |
| `tachon` | Tachon | yes | See §3.4 |

Coordinates MUST match the cell dict used for OCR (primary: `row["cells"][j]`; fallback: `build_subcells(v_row, y_top, y_bot)`).

### 3.4 `Tachon` object (`CellAnalysis.to_dict()`)

```json
{
  "score": 0.12,
  "tachon_score": 0.05,
  "density_score": 0.0,
  "noise_score": 0.0,
  "flags": [],
  "is_suspicious": false
}
```

- `is_suspicious` uses `TACHON_THRESHOLD = 0.45` inside `tachon_detector` (not configurable in D2).
- `flags` values: `TACHON`, `DOBLE_ESCRITURA`, `DENSIDAD_ALTA`, `ZONA_SUCIA` (as emitted by detector).

### 3.5 `tachon_summary` shape

```json
{
  "tachon_summary": {
    "analyzed_subcells": 18,
    "suspicious_subcells": 2,
    "flags_by_label": {
      "C1_CEPEDA": ["TACHON"],
      "URNA": ["ZONA_SUCIA", "TACHON"]
    }
  }
}
```

| Field | Type | Rule |
|-------|------|------|
| `analyzed_subcells` | int | Count of subcells where `analyze_cell()` ran (`has_ink=true`) |
| `suspicious_subcells` | int | Count where `tachon.is_suspicious === true` |
| `flags_by_label` | object | Keys = known labels; values = sorted unique flags from subcells |

When no inked subcells analyzed, omit `tachon_summary` OR emit zeros with empty `flags_by_label` (implementation MUST pick one; tests enforce consistency — **recommended: omit when `analyzed_subcells === 0`**).

### 3.6 Backward compatibility contract

| Consumer | Contract |
|----------|----------|
| Batch stats (`analyze_e14_batch.py`) | Continue reading `fields`, `is_suspicious`, `suspicious_reasons` only |
| Arithmetic assess | `fields["URNA"]`, vote sum unchanged |
| Debug tooling | `rows[]` shape unchanged |
| New consumers | MAY read `field_groups`, `tachon_summary`; MUST ignore unknown top-level keys |

**Invariant:** For every label `L` in `fields` where value is non-null, `field_groups.*.fields[L].value` MUST equal `fields[L]`.

---

## 4. Suspicious acta rules (D2)

| Rule | Description | Implementation |
|------|-------------|----------------|
| **R-SUS-1** | Run `analyze_cell(crop)` on each digit subcell crop (same BGR padding as OCR) | REQ-D2-010, REQ-D2-012, REQ-D2-011 |
| **R-SUS-2** | Priority: C1/C2 via `read_candidate_row`; primary path covers all accepted known rows | Fallback merges into `block_b` with `v_lines_row` |
| **R-SUS-3** | `tachon.is_suspicious` (threshold 0.45) contributes to `suspicious_reasons` | REQ-D2-020 → `"tachon_suspicious"` |
| **R-SUS-4** | Tachon is additive — does not override OK/SUS from arithmetic | REQ-D2-021 |
| **R-SUS-5** | Flat `fields` / `rows` remain source of truth for counts | REQ-D2-007 |

---

## 5. Integration points

### 5.1 Data flow (post-D2)

```
process_pdf_task(pdf)
  → _analyze_primary(pdf)
       → label_rows_by_structure (D1-corrected Block A)
       → per-row: ink → ocr_cell → analyze_cell (primary tachon)
       → fields{}, rows[], primary_tachon_meta{}
  → [if C1 or C2 missing] extract_candidates()
       → read_candidate_row: crop_cell → ocr → analyze_cell (priority tachon)
  → _merge_fallback() — fields merge unchanged
  → _build_field_groups(labeled_rows, rows, fallback_candidates, tachon_meta)
  → _assess() — append tachon_suspicious if needed
  → record JSONL
```

### 5.2 `e14_worker._analyze_primary`

**Location:** `debug_sv/e14_worker.py` L61–129.

**Changes:**

1. During the per-cell OCR loop (L93–102), after computing `ch, conf`:
   - Build BGR crop with `CELL_PAD` (mirror `ocr_cell` geometry).
   - If `inks[j]`: `tachon = analyze_cell(crop).to_dict()`; else neutral tachon (REQ-D2-013).
   - Collect per-row subcell payloads for `_build_field_groups`.

2. Retain `labeled_rows` (full grid labels) in the return dict as `labeled_rows` (internal builder input; MAY be stripped before JSONL emit if size concern — **recommended: omit from JSONL**, pass only to `_build_field_groups` in `process_pdf_task`).

3. Return signature extension (internal):

```python
return {
    "fields": fields,
    "rows": rows,
    # ... existing keys ...
    "_builder_inputs": {
        "labeled_rows": labeled_rows,
        "row_subcells": {label: [subcell_dict, ...]},  # known labels only
    },
}
```

`_builder_inputs` MUST NOT be written to JSONL.

### 5.3 `candidate_subcells.read_candidate_row`

**Location:** `debug_sv/candidate_subcells.py` L184–263.

**Changes:**

1. Import `analyze_cell` from `tachon_detector`.
2. In the per-cell loop (L199–220):
   - Always compute `crop = crop_cell(img, cell, pad)` when evaluating ink (reuse for tachon).
   - After OCR, attach `subcells` array to return dict with full Subcell schema.
3. Extend return dict:

```python
{
    "label": label,
    "y_top": ..., "y_bot": ...,
    "v_lines_row": v_row,
    "digits": ..., "confidences": ..., "value": ..., "ink": ...,
    "subcells": [ { idx, x1..y2, digit, confidence, has_ink, tachon }, ... ],
    "crops": crop_paths,  # unchanged
}
```

**C1/C2 priority:** When fallback runs, `field_groups.block_b.fields[label]` MUST be built from `fallback_candidates[].subcells` (not re-OCR'd in worker). Primary-path subcells for C1/C2 used only when both candidates present in primary `fields`.

### 5.4 `_build_field_groups`

**Location:** New function in `debug_sv/e14_worker.py` (or pure helper module if >80 LOC).

**Signature:**

```python
def _build_field_groups(
    *,
    labeled_rows: list[dict],
    primary_row_subcells: dict[str, list[dict]],
    fallback_candidates: list[dict] | None,
    rows_accepted: list[dict],  # flat rows from primary
) -> tuple[dict, dict | None]:
    """Return (field_groups, tachon_summary)."""
```

**Logic:**

1. Initialize three blocks with static `labels` arrays.
2. For each known label, locate row metadata from `labeled_rows` (y_top, y_bot, y_mid, label_source).
3. **Block B fallback override:** If `fallback_candidates` contains label, use its `subcells` and `v_lines_row`; set value from candidate.
4. **Else:** Use `primary_row_subcells[label]` if row was accepted in primary loop.
5. Aggregate `tachon_summary` from all subcells in final `field_groups`.
6. Skip labels with no row and no fallback entry.

### 5.5 `process_pdf_task` orchestration

**Location:** `debug_sv/e14_worker.py` L188–218.

After fallback merge:

```python
fg, ts = _build_field_groups(...)
record["field_groups"] = fg
if ts is not None:
    record["tachon_summary"] = ts
```

Strip `_builder_inputs` from `primary` before spreading into `record`.

### 5.6 `_assess` extension

After existing reason collection, scan `record.get("tachon_summary", {})`:

```python
if record.get("tachon_summary", {}).get("suspicious_subcells", 0) > 0:
    reasons.append("tachon_suspicious")
```

Do not change `is_critical` predicate for tachon alone.

---

## 6. Test matrix (strict TDD)

**File:** `debug_sv/test_tachon_field_groups.py` (new).  
**Order:** Write tests first; all MUST fail on pre-D2 codebase; all MUST pass before merge.

### 6.1 Schema validation tests

| Test ID | Name | Assertion |
|---------|------|-----------|
| T-D2-S01 | `test_field_groups_has_three_blocks` | Mock worker output includes `block_a`, `block_b`, `block_c` each with `labels` + `fields` |
| T-D2-S02 | `test_subcell_required_keys` | Each subcell has idx, coords, digit, confidence, has_ink, tachon with 6 tachon keys |
| T-D2-S03 | `test_fields_value_invariant` | `field_groups.*.fields[L].value == fields[L]` for all populated labels |
| T-D2-S04 | `test_rows_unchanged_shape` | Flat `rows[]` entries still only `label`, `value`, `digits`, `confidences`, `y_mid` |
| T-D2-S05 | `test_unk_rows_excluded` | No `UNK@*` keys in any `field_groups` block |

### 6.2 Mock tachon tests

| Test ID | Name | Assertion |
|---------|------|-----------|
| T-D2-M01 | `test_analyze_cell_mocked_on_inked_crop` | Patch `analyze_cell`; verify called once per inked subcell with BGR ndarray |
| T-D2-M02 | `test_crop_padding_matches_ocr` | Crop slice uses `CELL_PAD` offset on cell bounds |
| T-D2-M03 | `test_neutral_tachon_when_no_ink` | `has_ink=false` → `analyze_cell` not called; `is_suspicious=false` |
| T-D2-M04 | `test_tachon_summary_aggregation` | Two mocked suspicious subcells → `suspicious_subcells==2`, `flags_by_label` populated |
| T-D2-M05 | `test_assess_appends_tachon_suspicious` | Suspicious tachon adds `tachon_suspicious` to reasons without forcing `is_suspicious` if arithmetic OK |

### 6.3 C1/C2 three-subcell tests

| Test ID | Name | Assertion |
|---------|------|-----------|
| T-D2-C01 | `test_read_candidate_row_returns_three_subcells` | Fallback row → `len(subcells)==3`, `idx` 0..2 |
| T-D2-C02 | `test_block_b_includes_v_lines_row_on_fallback` | `field_groups.block_b.fields.C1_CEPEDA.v_lines_row` length 4 |
| T-D2-C03 | `test_fallback_subcells_prefer_over_primary` | When `primary+fallback`, block_b subcells match candidate dict |
| T-D2-C04 | `test_primary_only_block_b_no_v_lines_row` | Primary-only C1/C2 entries omit `v_lines_row` |

### 6.4 Empty-cell policy tests

| Test ID | Name | Assertion |
|---------|------|-----------|
| T-D2-E01 | `test_empty_cell_digit_zero_confidence_zero` | No ink → digit `"0"`, confidence `0.0` |
| T-D2-E02 | `test_healthy_votantes_zero_no_false_tachon` | All-neutral tachon on empty cells; `suspicious_subcells==0` for empty row |
| T-D2-E03 | `test_tachon_summary_omitted_when_no_analysis` | All cells empty → no `tachon_summary` key (recommended policy) |

### 6.5 Regression guards

| Test ID | Name | Assertion |
|---------|------|-----------|
| T-D2-R01 | `test_gold_pdf_field_groups_mirror_fields` | `01_01_001_5002` — 9 known labels reflected in field_groups |
| T-D2-R02 | `test_d1_labels_required_for_block_a_source` | Post-D1: Block A `label_source == "anchor_block_a"` |

### 6.6 Test fixtures

- Mock `numpy` crops (minimal 32×32 BGR) for unit tests.
- Optional integration: `01_001_01_01_E14_PRE_01_001_001_01_01_001_5002.pdf` (gold), `01_01_003_5002` (offset).
- Patch target: `src.modules.analyzer.tachon_detector.analyze_cell`.

---

## 7. Acceptance criteria and gates

### 7.1 Merge gates (blocking)

| Gate | Target |
|------|--------|
| `test_tachon_field_groups.py` | All tests PASS |
| `field_groups` present | 100% of successful worker records |
| C1/C2 `subcells[].tachon` | Populated when fallback or primary reads 3 digits |
| `tachon_summary` | Present when ≥1 inked subcell analyzed |
| D1 metrics | No regression vs post-D1 `--validate` baseline |
| `src/modules/analyzer/` | No file edits (import only) |
| Existing pytest | `test_row_labeling.py` + `test_h_line_filter.py` still green |

### 7.2 Validation commands

```powershell
Set-Location "D:\Nucleux\tools\Analizador de Elecciones"

# D2 unit tests (write first)
python -m pytest debug_sv/test_tachon_field_groups.py -v

# Regression
python -m pytest debug_sv/test_row_labeling.py debug_sv/test_h_line_filter.py -v

# Batch validation (D1 metrics must hold)
python debug_sv/analyze_e14_batch.py --validate
```

### 7.3 Non-functional gate

| Metric | Target |
|--------|--------|
| JSONL size growth | Monitor; expect &lt;2× per record vs pre-D2 |
| Single-PDF latency | Acceptable on 100-PDF validate set (no hard fail) |

---

## 8. Performance note

**Cost model:** Up to **3 × N** `analyze_cell()` calls per PDF, where N = accepted known rows (typically ≤9) plus up to 2 fallback candidate rows.

| Factor | Estimate |
|--------|----------|
| Per call | OpenCV morphology on ~60×80 px crop — sub-ms to low-ms per cell |
| 100-PDF validate | ~2,700–5,400 extra analyses — acceptable for debug_sv batch |
| 90K batch | **Out of scope** — would add significant CPU; defer optimization (skip Block A/C tachon, parallel pool tuning) to future change |

**Mitigations in D2:**

1. Skip `analyze_cell` when `has_ink=false` (REQ-D2-013).
2. Do not run tachon twice on C1/C2 when fallback subcells already populated.
3. Do not persist `_builder_inputs` or raw crops in JSONL.

---

## 9. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| `candidate_subcells` raw `h_lines` desync (Fase 1b) | Document; recommend Fase 1b before relying on fallback+tachon for production triage |
| High-recall tachon → noisy `suspicious_subcells` | `tachon_summary` for triage; R-SUS-4 — no auto-reject |
| Crop/OCR misalignment | Single `crop_cell` / pad helper; T-D2-M02 |
| JSONL bloat at scale | Omit neutral subcells policy not adopted — full subcells required for ML bootstrap |
| Wrong Block A labels pre-D1 | Hard dependency; T-D2-R02 |

---

## 10. Non-goals

- Editing `tachon_detector.py` or `TACHON_THRESHOLD`
- Portal / review UI consuming `field_groups`
- National layout calibration (Fase 3)
- Batch 90K execution
- Fase 1b (`process_h_lines` in `candidate_subcells`) — recommended parallel, not D2 blocker
- Persisting cell PNG paths in JSONL (debug `crops` remain on disk only when `save_suspicious_crops`)
- Auto-rejecting actas on tachon alone
- Training or retraining CNN models

---

## 11. Implementation checklist

- [ ] Create `test_tachon_field_groups.py` with T-D2-* cases (red phase)
- [ ] Add `analyze_cell` import to `candidate_subcells.py` and `e14_worker.py`
- [ ] Extend `read_candidate_row` with `subcells[]` + tachon
- [ ] Extend `_analyze_primary` loop with tachon + builder inputs
- [ ] Implement `_build_field_groups()` + `_summarize_tachon()`
- [ ] Wire `process_pdf_task` to attach `field_groups` / `tachon_summary`
- [ ] Extend `_assess()` with `tachon_suspicious`
- [ ] Green phase: all tests pass
- [ ] Run `--validate`; confirm D1 metrics unchanged

---

## 12. References

| Artifact | Role |
|----------|------|
| `openspec/changes/fase-2-fix-hybrid-tachon/proposal.md` | Business rules R-SUS-1..5, schema sketch |
| `openspec/changes/fase-2-fix-hybrid-tachon/exploration.md` | Insertion points §3 |
| `debug_sv/e14_worker.py` | `_analyze_primary`, `_assess`, `process_pdf_task` |
| `debug_sv/candidate_subcells.py` | `read_candidate_row`, `crop_cell` |
| `src/modules/analyzer/tachon_detector.py` | `analyze_cell`, `CellAnalysis.to_dict()` (read-only) |
| `data/analysis_segunda_vuelta/e14c_validate_results.jsonl` | Pre-D2 schema baseline |

---

## Next step

**Design D2:** API signatures for `_build_field_groups`, mock fixtures, and PR-2 file diff plan.