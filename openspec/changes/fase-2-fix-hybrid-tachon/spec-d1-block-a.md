# Spec D1: Hybrid Block A — `anchor_block_a()`

**Change:** `fase-2-fix-hybrid-tachon`  
**Deliverable:** D1 only (Block A anchoring fix)  
**Date:** 2026-06-22  
**Phase:** spec  
**Status:** Ready for design-d1

---

## Overview

Fase 2 introduced `label_rows_by_structure()` to replace `guess_label()`. Block A assignment (lines L501–509 of `debug_sv/grid_detector_v2.py`) takes the **first three compact rows before C1** without a Y-position floor. In **43/55** URNA=0 cases, spurious header rows at **Y≈833–964** consume those three slots; the real nivelación band (**Y≈1023–1270**) remains `UNK@{y_mid}` with the URNA digit value.

**D1 goal:** Extract Block A into `anchor_block_a()`, apply a **ratio-scaled Y floor** so Block A anchors in the real nivelación band, and refactor `label_rows_by_structure()` to delegate Block A only. **C1/C2 mega-gap logic and Block C last-4 logic MUST remain unchanged.**

**Out of scope for D1:** `field_groups`, tachon (D2), edits under `src/modules/analyzer/`, batch 90K, mega-gap-not-detected cases (10 PDFs), Block C redesign, rollback of structural labeling.

### References

| Artifact | Role |
|----------|------|
| `openspec/changes/fase-2-fix-hybrid-tachon/proposal.md` | Business rules, gates, deliverable boundaries |
| `openspec/changes/fase-2-fix-hybrid-tachon/exploration.md` | Insertion points, regression cases |
| `debug_sv/Reporte explore — 55 PDFs con URNA=0 (Fase 2).md` | Offset diagnosis (category a: 43/55) |
| `debug_sv/grid_detector_v2.py` L414–516 | Current constants and `label_rows_by_structure()` |
| `debug_sv/test_row_labeling.py` | Existing 9 tests + extension target |
| `openspec/config.yaml` | `strict_tdd: true` — tests BEFORE implementation |

---

## Problem statement

| Symptom | Root cause |
|---------|------------|
| OK% 24% → 14% | URNA mislabeled → arithmetic checks fail |
| 55/100 `fields["URNA"] == 0` | Block A slots consumed by Y<980 spurious rows |
| Value present in OCR | Digit correct at Y≈1150 but row labeled `UNK@1152` or `INCINER` |

**Dominant fixable pattern (category a, 78% of URNA=0):** anchor Block A below Y≈980, not at first compact pre-C1 rows globally.

---

## Requirements

### Functional

#### REQ-D1-001 — New `anchor_block_a()` function
The system **MUST** add a dedicated function `anchor_block_a()` in `debug_sv/grid_detector_v2.py` in the "Row labelling" section (before `label_rows_by_structure()`), implementing Block A index selection only.

#### REQ-D1-002 — Y floor filter
`anchor_block_a()` **MUST** exclude candidate rows whose `y_mid < y_floor`, where `y_floor` is ratio-scaled from a reference constant (see API contract). Rows at Y≈833–964 **MUST NOT** be eligible for Block A slots after D1.

#### REQ-D1-003 — Compact row eligibility
Among rows with index `i < c1_idx`, only rows with compact height **`ROW_MIN_H ≤ h ≤ ROW_TYPICAL_MAX_H`** (80–120 px) **MUST** be Block A candidates. Existing constants `ROW_MIN_H` (80) and `ROW_TYPICAL_MAX_H` (120) **MUST** be reused; no change to height bounds in D1.

#### REQ-D1-004 — Slot count and order
`anchor_block_a()` **MUST** return up to **3** row indices in **ascending index order** (top → bottom), corresponding to labels `VOTANTES`, `URNA`, `INCINER`. It **MUST** take the first N eligible compact rows (post Y-floor filter), where N = `min(3, len(eligible))`. Fewer than 3 indices **MAY** be returned when fewer eligible rows exist above the floor.

#### REQ-D1-005 — Refactor Block A delegation only
`label_rows_by_structure()` **MUST** replace lines L501–509 (`pre_c1[: len(LABELS_BLOCK_A)]`) with a call to `anchor_block_a()`. Block B (C1/C2) and Block C assignment **MUST NOT** be modified.

#### REQ-D1-006 — C1/C2 mega-gap preserved
Mega-gap detection (`gaps[i] > SECTION_GAP_THRESHOLD`, threshold 400 px), largest-internal-gap C1/C2 selection, and mid-third fallback when `best_gap >= ROW_MAX_H` **MUST** remain byte-for-byte equivalent in behavior to pre-D1 code paths.

#### REQ-D1-007 — Block C last-4 preserved
Block C **MUST** continue assigning `BLANCO`, `NULOS`, `NO_MARCADOS`, `SUMA_TOTAL` to `range(n - 4, n)` when `n >= 4`. No redesign in D1.

#### REQ-D1-008 — UNK@ spacer behavior
Rows not assigned known labels **MUST** remain `UNK@{y_mid}`. Spacer rows between INCINER and C1 (e.g. synthetic y≈1470) **MUST NOT** receive Block A labels. UNK@ rows **MUST NOT** consume Block A slots when they appear **after** the Y floor among compact pre-C1 rows unless they are among the first 3 eligible compact rows (i.e. UNK@ is a label applied *after* selection, not a selection filter).

#### REQ-D1-009 — `label_source` for Block A
Rows labeled via `anchor_block_a()` **SHOULD** set `label_source` to `"anchor_block_a"`. All other structural labels **MAY** retain `"structure"`.

#### REQ-D1-010 — Public API stability
`label_rows_by_structure(rows: list[dict]) -> list[dict]` signature and return shape **MUST** remain unchanged. Downstream callers (`e14_worker.py`, `candidate_subcells.py`, `grid_detector_v2.main`) **MUST NOT** require modification for D1.

### Non-functional

#### REQ-D1-011 — Strict TDD (mandatory)
Per `openspec/config.yaml` (`strict_tdd: true`), **every behavior in this spec MUST be covered by a failing test written BEFORE the corresponding implementation.** Workflow:

1. **RED:** Add/extend tests in `debug_sv/test_row_labeling.py` (and optional `debug_sv/test_anchor_block_a.py` if logic grows); confirm failure against current `pre_c1[:3]` behavior.
2. **GREEN:** Implement `anchor_block_a()` and refactor Block A delegation until tests pass.
3. **REFACTOR:** Clean up without changing observable labeling behavior covered by tests.

No production code for `anchor_block_a()` **SHALL** be merged before its test matrix entries exist and have been observed failing.

#### REQ-D1-012 — Scope boundary
D1 **MUST NOT** edit files under `src/modules/analyzer/`, `debug_sv/e14_worker.py`, `debug_sv/candidate_subcells.py`, or introduce `field_groups` / tachon wiring.

#### REQ-D1-013 — Page-height scaling
Y floor **MUST** scale with rendered page height so Antioquia validation PDFs (page heights ~3860–3900) and reference layouts remain consistent. Fixed pixel floor without scaling **MUST NOT** be used when `page_h` is available.

---

## API contract

### Constants (new)

```python
BLOCK_A_Y_FLOOR = 980          # px at reference page height
BLOCK_A_REF_PAGE_H = 3500      # reference height for ratio scaling
# BLOCK_A_Y_CEILING = 1300     # optional; NOT required in D1 (document only)
```

| Constant | Value | Semantics |
|----------|-------|-----------|
| `BLOCK_A_Y_FLOOR` | `980` | Minimum `y_mid` for Block A candidacy at `BLOCK_A_REF_PAGE_H` |
| `BLOCK_A_REF_PAGE_H` | `3500` | Reference render height (300 DPI segunda-vuelta baseline) |

**Ratio scaling formula (MUST):**

```
y_floor_effective = int(page_h * BLOCK_A_Y_FLOOR / BLOCK_A_REF_PAGE_H)
```

**`page_h` derivation (MUST):** When called from `label_rows_by_structure()`, `page_h` **MUST** be `max(row["bot"] for row in labeled)` (or equivalent maximum grid extent). When `anchor_block_a()` is called directly in unit tests, callers **MAY** pass explicit `y_floor` to bypass scaling.

### Function signature

```python
def anchor_block_a(
    rows: list[dict],
    c1_idx: int,
    *,
    y_floor: int | None = None,
    page_h: int | None = None,
) -> list[int]:
    """Return up to 3 row indices for VOTANTES/URNA/INCINER before c1_idx."""
```

### Inputs

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `rows` | `list[dict]` | Yes | Grid rows with `top`, `bot`, optional `height`, `row_idx` |
| `c1_idx` | `int` | Yes | Index of C1 row (from mega-gap or fallback); Block A candidates satisfy `i < c1_idx` |
| `y_floor` | `int \| None` | No | Explicit floor; if `None`, computed from `page_h` via ratio formula |
| `page_h` | `int \| None` | No | Page/render height for ratio scaling; required when `y_floor` is `None` |

**Precondition:** `c1_idx` is not `None` and `c1_idx > 0`. If `c1_idx` is `None` or `0`, `anchor_block_a()` **MUST** return `[]` (caller skips Block A assignment).

### Output

| Return | Type | Description |
|--------|------|-------------|
| `list[int]` | Row indices | 0–3 indices in ascending order; empty when no eligible rows |

**Postcondition:** Every returned index `i` satisfies:

- `0 <= i < c1_idx`
- `ROW_MIN_H <= height(i) <= ROW_TYPICAL_MAX_H`
- `y_mid(i) >= y_floor_effective`

### Integration in `label_rows_by_structure()`

```python
if c1_idx is not None and c1_idx > 0:
    page_h = max(r["bot"] for r in labeled)
    block_a_indices = anchor_block_a(labeled, c1_idx, page_h=page_h)
    _assign_labels(labeled, block_a_indices, LABELS_BLOCK_A)
    # Optionally set label_source = "anchor_block_a" on those rows
```

---

## Algorithm

### Step 1 — Compute effective Y floor
1. If `y_floor` argument is provided, use it as `y_floor_effective`.
2. Else if `page_h` is provided, `y_floor_effective = int(page_h * BLOCK_A_Y_FLOOR / BLOCK_A_REF_PAGE_H)`.
3. Else raise `ValueError` (tests may pass explicit `y_floor`).

**Example:** `page_h = 3890` → `y_floor_effective = int(3890 * 980 / 3500) = 1089`.

### Step 2 — Build eligible candidate list
For each `i` in `range(c1_idx)`:

1. Compute `h = rows[i].get("height", rows[i]["bot"] - rows[i]["top"])`.
2. Compute `y_mid = (rows[i]["top"] + rows[i]["bot"]) // 2`.
3. Include `i` in `eligible` if **all** hold:
   - `ROW_MIN_H <= h <= ROW_TYPICAL_MAX_H`
   - `y_mid >= y_floor_effective`

Preserve natural ascending `i` order (equivalent to ascending Y).

### Step 3 — Select Block A indices
Return `eligible[:3]` (first three eligible compact rows at or below the spurious-header zone).

### Step 4 — Label assignment (in caller)
`_assign_labels(labeled, block_a_indices, LABELS_BLOCK_A)` maps indices to `VOTANTES`, `URNA`, `INCINER` in order. Truncation follows existing `zip` behavior when fewer than 3 indices.

### Decision log (spec phase resolutions)

| Open question (proposal) | D1 decision |
|--------------------------|-------------|
| Fixed 980 vs ratio scaling | **Ratio scaling MUST** use `BLOCK_A_REF_PAGE_H = 3500` |
| Block A row count | **Up to 3** slots; dynamic 1–3 when fewer eligible rows |
| `BLOCK_A_Y_CEILING` (~1300) | **Out of D1 scope** — monitor via 029/010 tests; add only if batch regression |
| Post-OCR value-in-wrong-slot heuristic | **Out of D1 scope** (worker validation later) |

---

## Invariants preserved

| Invariant | Owner | D1 status |
|-----------|-------|-----------|
| Mega-gap C1/C2 (`SECTION_GAP_THRESHOLD = 400`) | `label_rows_by_structure` | **UNCHANGED** |
| Mid-third C1/C2 fallback (`best_gap >= ROW_MAX_H`) | `label_rows_by_structure` | **UNCHANGED** |
| Block C last 4 rows | `label_rows_by_structure` | **UNCHANGED** |
| Initial UNK@ on all rows | `label_rows_by_structure` | **UNCHANGED** |
| `validate_labeled_rows` arithmetic rule `C1+C2+BLANCO+NULOS+NO_MARCADOS ≤ URNA` | `e14_worker` | **UNCHANGED** (benefits from correct URNA) |
| `KNOWN_ROW_LABELS` vocabulary | `grid_detector_v2` | **UNCHANGED** |
| Row order in output list | `label_rows_by_structure` | **UNCHANGED** |

---

## Test matrix

All tests **MUST** be authored in the RED phase before `anchor_block_a()` implementation. Baseline: **16/16** pytest green (`test_row_labeling` 9 + `test_h_line_filter` 7). D1 target: **16+** PASS with new tests.

### Unit tests (synthetic / isolated)

| Test ID | Test name (proposed) | Scenario | Expected |
|---------|---------------------|----------|----------|
| UT-D1-01 | `test_anchor_block_a_empty_when_c1_zero` | `c1_idx=0` | Returns `[]` |
| UT-D1-02 | `test_anchor_block_a_y_floor_excludes_spurious_rows` | Synthetic pre-C1 rows at y_mid 846, 946, 1035, 1152 (compact); `y_floor=980` | Returns indices for 1035, 1152 only (≤3) — **not** 846/946 |
| UT-D1-03 | `test_anchor_block_a_ratio_scaling` | `page_h=3500` → floor 980; `page_h=3890` → floor 1089 | Formula verified |
| UT-D1-04 | `test_anchor_block_a_skips_non_compact_rows` | Row with `height=200` before C1 | Excluded from candidates |
| UT-D1-05 | `test_synthetic_spacer_gets_unk_at_1470` | **Existing** — must still pass | Block A at rows 0–2; spacer `UNK@1465` |
| UT-D1-06 | `test_bad_case_preserves_row_order` | **Existing** — must still pass | Spacer `UNK@1499`; order preserved |

### Integration tests (PDF / JSON grid)

| Test ID | Test name (proposed) | Fixture | Expected |
|---------|---------------------|---------|----------|
| IT-D1-01 | `test_offset_pdf_003_urna_at_nivelacion_band` | `01_001_01_01_E14_PRE_01_001_001_01_01_003_5002.pdf` | `URNA` label at `y_mid` ≈ 1146–1158 (±80 px); **not** at y≈946 |
| IT-D1-02 | `test_good_pdf_nine_known_labels` | **Existing** `01_01_001_5002` | All 9 labels unchanged; `URNA` y_mid ≈ 1149 |
| IT-D1-03 | `test_json_grid_structural_labels` | **Existing** JSON grid | All `EXPECTED_GOOD` y_mid within ±80 px |
| IT-D1-04 | `test_healthy_pdf_010_votantes_840_urna_1147` | `01_001_01_01_E14_PRE_01_001_001_01_01_010_5002.pdf` | `URNA` y_mid ≈ 1147 (±80); VOTANTES@840 **MAY** stay unlabeled or below floor — **URNA MUST NOT regress** |
| IT-D1-05 | `test_low_urna_pdf_029_urna_at_1025` | `01_001_01_01_E14_PRE_01_001_001_01_01_029_5002.pdf` | `URNA` y_mid ≈ 1025 (±80) — proves floor not over-aggressive |
| IT-D1-06 | `test_bad_pdf_finds_candidates_and_spacers` | **Existing** `01_03_001_5003` | C1/C2 found; UNK spacers present |

### Optional worker-level check (manual / batch, not unit test)

| Check | Command | Note |
|-------|---------|------|
| Offset fields recovery | Re-run OCR pipeline on `003_5002` | `fields["URNA"] > 0` after full worker path (validated in batch gate) |

### TDD sequencing (MUST)

```
Phase RED (tests first):
  UT-D1-02, UT-D1-03, IT-D1-01, IT-D1-04, IT-D1-05 → FAIL on current code

Phase GREEN:
  Implement anchor_block_a + refactor L501–509 → all above PASS
  Regression: UT-D1-05, UT-D1-06, IT-D1-02, IT-D1-03, IT-D1-06 → still PASS

Phase GATE:
  pytest + analyze_e14_batch.py --validate
```

---

## Acceptance criteria and gates

### Blocking gates (PR-1 / D1 merge)

| Gate ID | Metric | Baseline (Fase 2 broken) | D1 target | Verification |
|---------|--------|--------------------------|-----------|--------------|
| GATE-D1-01 | OK% (`--validate` 100 PDFs) | 14% | **≥ 24%** (Fase 1 baseline) | `python debug_sv/analyze_e14_batch.py --validate` |
| GATE-D1-02 | `URNA=0` count | 55/100 | **≪ 55** (expect ~12 residual: 10 mega-gap + 2 other) | `e14c_validate_complete.json` / JSONL stats |
| GATE-D1-03 | `arith_ok` | 16 | **≥ 24** | Same validation run |
| GATE-D1-04 | pytest | 16/16 | **≥ 16 PASS** (new offset tests included) | `python -m pytest debug_sv/test_row_labeling.py debug_sv/test_h_line_filter.py -v` |
| GATE-D1-05 | Gold PDF labels | 9/9 | **9/9 unchanged** (`001_5002`) | `test_good_pdf_nine_known_labels` |
| GATE-D1-06 | Offset PDF | URNA@946 (wrong) | **URNA@~1150** | `test_offset_pdf_003_urna_at_nivelacion_band` |
| GATE-D1-07 | Scope | — | **No edits** under `src/modules/analyzer/` | PR diff review |

### Validation commands

```powershell
Set-Location "D:\Nucleux\tools\Analizador de Elecciones"
python -m pytest debug_sv/test_row_labeling.py debug_sv/test_h_line_filter.py -v
python debug_sv/analyze_e14_batch.py --validate
```

**Metrics source:** `data/analysis_segunda_vuelta/e14c_validate_complete.json`

### Definition of done (D1)

- [ ] REQ-D1-001 through REQ-D1-013 satisfied
- [ ] Strict TDD evidenced: failing tests merged or committed before implementation
- [ ] All blocking gates PASS
- [ ] Residual URNA=0 cases documented as non-goals (see below)

---

## Non-goals and residual cases

| Category | Count | D1 behavior | Follow-up |
|----------|-------|-------------|-----------|
| **(a) Offset systematic** | 43 | **Fixed** by Y floor | — |
| **(b) Extra Block A row** | 1 (`01_03_009_5003`) | May remain `URNA=0` | OCR/grid |
| **(c) Mega-gap not detected** | 10 | **Out of scope** — C1/grid failure | Separate grid/C1 work |
| **(d) Other / fragmented** | 1 (`01_03_015_5003`) | **Out of scope** | Grid fragmentation |
| **Mega-gap representative** | e.g. `01_01_022_5002` | No change expected | Not a D1 regression if still URNA=0 |

**Explicit non-goals:**

- Rollback of `label_rows_by_structure()` for C1/C2/Block C
- `field_groups`, `tachon_summary`, `analyze_cell` integration (D2)
- Fase 1b (`process_h_lines` in `candidate_subcells`) — recommended parallel, not D1 blocker
- National X/Y calibration beyond ratio-scaled floor (Fase 3)
- Batch 90K execution
- `BLOCK_A_Y_CEILING` enforcement unless 029/010 regression observed in gates

---

## Files to modify

| File | Action | Est. LOC |
|------|--------|----------|
| `debug_sv/grid_detector_v2.py` | Add `BLOCK_A_Y_FLOOR`, `BLOCK_A_REF_PAGE_H`, `anchor_block_a()`; refactor Block A in `label_rows_by_structure()` (L501–509) | +80–120 |
| `debug_sv/test_row_labeling.py` | Add RED-first unit + integration tests (UT-D1-*, IT-D1-*) | +60–100 |

### Files that MUST NOT change (D1)

| File | Reason |
|------|--------|
| `src/modules/analyzer/*` | Import-only restriction; tachon is D2 |
| `debug_sv/e14_worker.py` | API of `label_rows_by_structure` unchanged |
| `debug_sv/candidate_subcells.py` | Indirect benefit only |
| `debug_sv/analyze_e14_batch.py` | Re-run for gate; no code change required |
| `debug_sv/e14_worker.py`, D2 test files | D2 scope |

### Optional (if `anchor_block_a` logic exceeds ~60 LOC)

| File | Action |
|------|--------|
| `debug_sv/test_anchor_block_a.py` | Isolated unit tests (still RED-first) |

---

## Risks

| Risk | Severity | Mitigation in D1 |
|------|----------|------------------|
| Y floor too rigid nationally | High | Ratio scaling via `page_h`; gate on 100-PDF validate |
| Y floor drops real URNA@1025 | Medium | **IT-D1-05** (`029_5002`) blocking |
| Healthy `VOTANTES@840` broken | Medium | **IT-D1-04** (`010_5002`) — URNA regression guard |
| Unit tests pass, batch fails | High | **GATE-D1-01** mandatory `--validate` |
| 10 mega-gap cases still URNA=0 | Medium | Documented non-goal; do not block D1 |
| `label_source` mismatch downstream | Low | Additive `"anchor_block_a"`; no consumer break expected |

---

## Next step

**design-d1:** Implementation plan with exact diff hunks for `grid_detector_v2.py`, test-first task ordering, and rollback note (revert Block A delegation to `pre_c1[:3]` if OK% regresses).

**Then:** D2 spec (`field_groups` + tachon) — blocked on D1 merge.