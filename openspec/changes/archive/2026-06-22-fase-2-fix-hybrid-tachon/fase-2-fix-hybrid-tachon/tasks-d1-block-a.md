# Tasks D1: Hybrid Block A — `anchor_block_a()`

**Change:** `fase-2-fix-hybrid-tachon`  
**Deliverable:** D1 only (PR-1)  
**Date:** 2026-06-22  
**Phase:** tasks-d1  
**Status:** Ready for apply-d1  
**Authoritative spec:** `openspec/changes/fase-2-fix-hybrid-tachon/spec-d1-block-a.md`  
**Authoritative design:** `openspec/changes/fase-2-fix-hybrid-tachon/design-d1-block-a.md`

---

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | **~150–220** (prod ~45–55, tests ~90–120, openspec artifact optional) |
| 400-line budget risk | **Low** — well within single-PR budget |
| Chained PRs recommended | **No split needed for D1** — single PR-1-D1 |
| Chain strategy | **stacked-to-main** — merge D1 before D2 branch |
| Delivery | PR-1 only; D2 blocked until D1 gates pass |
| Files touched | `debug_sv/grid_detector_v2.py`, `debug_sv/test_row_labeling.py` |
| Files forbidden | `src/modules/analyzer/*`, `e14_worker.py`, `candidate_subcells.py`, D2 test files |

### Suggested work units (commit hints)

| Unit | Commit message (conventional) | Scope | Est. LOC |
|------|-------------------------------|-------|----------|
| **WU-1** | `test(d1): add anchor_block_a unit tests (RED)` | Imports, helpers, UT-D1-01..04 | ~45–55 |
| **WU-2** | `test(d1): add offset/healthy/low-urna PDF tests (RED)` | IT-D1-01, IT-D1-04, IT-D1-05 | ~45–55 |
| **WU-3** | `feat(d1): add anchor_block_a with ratio-scaled Y floor` | H1 + H2 in `grid_detector_v2.py` | ~40–50 |
| **WU-4** | `refactor(d1): delegate Block A in label_rows_by_structure` | H3 + H4 in `grid_detector_v2.py` | ~10–15 |
| **WU-5** | `test(d1): assert anchor_block_a label_source on Block A rows` | `test_good_pdf_nine_known_labels` update | ~8–12 |
| **WU-6** | `chore(d1): validate batch metrics post-D1` | PR description / metrics snapshot only | 0 code |

**PR title:** `fix(debug_sv): anchor Block A below Y floor (D1 hybrid tachon)`

**Review order:** `anchor_block_a()` logic → Block A hunk only (L501–509) → new tests → `--validate` metrics.

---

## Dependency

| Dependency | Rule |
|------------|------|
| **D2 (`field_groups` + tachon)** | **BLOCKED** until all D1 gates (GATE-D1-01..07) pass and PR-1 merges |
| **Baseline tests** | 16/16 pytest green before RED phase (`test_row_labeling` 9 + `test_h_line_filter` 7) |
| **Strict TDD** | No WU-3/WU-4 production code until WU-1 + WU-2 committed and new tests observed **failing** |

---

## Phase 1 — RED: Unit tests (strict TDD)

> **Done-when (phase):** `pytest debug_sv/test_row_labeling.py -v` — existing 9 tests PASS; UT-D1-02..04 **FAIL**; UT-D1-01 PASS (contract on missing function edge).

- [x] **1.1** Add test imports and helpers — **Files:** `debug_sv/test_row_labeling.py` — **~15 loc**  
  - Import `anchor_block_a`, `BLOCK_A_Y_FLOOR`, `BLOCK_A_REF_PAGE_H` from `grid_detector_v2` (will fail until WU-3; use forward-compatible imports).  
  - Add PDF constants: `PDF_OFFSET` (`003_5002`), `PDF_HEALTHY_010` (`010_5002`), `PDF_LOW_URNA_029` (`029_5002`).  
  - Add helpers `_y_mid(row)` and `_synthetic_compact_row(idx, y_mid, height=90)`.  
  - **Done-when:** helpers usable by unit tests below.

- [x] **1.2** UT-D1-01 — `test_anchor_block_a_empty_when_c1_zero` — **Files:** `debug_sv/test_row_labeling.py` — **~8 loc**  
  - Call `anchor_block_a(rows, c1_idx=0, y_floor=980)` → `[]`.  
  - **Done-when:** test exists; passes once `anchor_block_a` stub exists, or fails import until WU-3.

- [x] **1.3** UT-D1-02 — `test_anchor_block_a_y_floor_excludes_spurious_rows` — **Files:** `debug_sv/test_row_labeling.py` — **~20 loc**  
  - Synthetic rows at y_mid 846, 946, 1035, 1152 + C1 at index 4; `y_floor=980`.  
  - **Expected:** `[2, 3]` — **not** `[0, 1, 2]`.  
  - **Done-when:** test **FAILS** on current `pre_c1[:3]` code (returns first 3 compact rows).

- [x] **1.4** UT-D1-03 — `test_anchor_block_a_ratio_scaling` — **Files:** `debug_sv/test_row_labeling.py` — **~12 loc**  
  - Assert `int(3500 * BLOCK_A_Y_FLOOR / BLOCK_A_REF_PAGE_H) == 980`.  
  - Assert `int(3890 * BLOCK_A_Y_FLOOR / BLOCK_A_REF_PAGE_H) == 1089`.  
  - Optionally call `anchor_block_a(..., page_h=3890)` and verify floor excludes y_mid=946.  
  - **Done-when:** test **FAILS** (function/constants missing).

- [x] **1.5** UT-D1-04 — `test_anchor_block_a_skips_non_compact_rows` — **Files:** `debug_sv/test_row_labeling.py` — **~15 loc**  
  - Pre-C1 row with `height=200` at y_mid=1100 plus compact rows at 1035, 1152.  
  - **Expected:** tall row excluded; returns compact indices only.  
  - **Done-when:** test **FAILS** on current code.

**Commit hint:** WU-1 — `test(d1): add anchor_block_a unit tests (RED)`

---

## Phase 2 — RED: Integration tests (PDF fixtures)

> **Done-when (phase):** IT-D1-01, IT-D1-04, IT-D1-05 **FAIL** on current code; all pre-existing integration tests still PASS.

- [x] **2.1** IT-D1-01 — `test_offset_pdf_003_urna_at_nivelacion_band` — **Files:** `debug_sv/test_row_labeling.py` — **~25 loc**  
  - Fixture: `01_001_01_01_E14_PRE_01_001_001_01_01_003_5002.pdf`.  
  - **Expected:** `URNA` y_mid in [1066, 1238]; no known label at y_mid in [866, 1026].  
  - **Done-when:** test **FAILS** (URNA currently at y≈946).

- [x] **2.2** IT-D1-04 — `test_healthy_pdf_010_votantes_840_urna_1147` — **Files:** `debug_sv/test_row_labeling.py` — **~18 loc**  
  - Fixture: `01_001_01_01_E14_PRE_01_001_001_01_01_010_5002.pdf`.  
  - **Expected:** `URNA` y_mid ≈ 1147 (±80); VOTANTES@840 may stay unlabeled — **no VOTANTES assertion required**.  
  - **Done-when:** test **FAILS** or URNA misplaced on current code.

- [x] **2.3** IT-D1-05 — `test_low_urna_pdf_029_urna_at_1025` — **Files:** `debug_sv/test_row_labeling.py` — **~15 loc**  
  - Fixture: `01_001_01_01_E14_PRE_01_001_001_01_01_029_5002.pdf`.  
  - **Expected:** `URNA` y_mid ≈ 1025 (±80) — proves floor not over-aggressive.  
  - **Done-when:** test **FAILS** on current code.

- [x] **2.4** RED gate checkpoint — **Files:** none (verify only) — **~0 loc**  
  - Run: `python -m pytest debug_sv/test_row_labeling.py -v`  
  - **Done-when:** 9 existing tests PASS; 3+ new tests FAIL; document failure output in PR or session notes.  
  - **Rule:** Do **not** proceed to Phase 3 until this checkpoint passes.

**Commit hint:** WU-2 — `test(d1): add offset/healthy/low-urna PDF tests (RED)`

---

## Phase 3 — Implementation: `anchor_block_a()` + Block A delegation

> **Done-when (phase):** `anchor_block_a()` implemented; Block A in `label_rows_by_structure()` delegates; C1/C2 (L474–499) and Block C (L511–514) **unchanged**.

- [x] **3.1** H1 — Add Block A constants — **Files:** `debug_sv/grid_detector_v2.py` (after L418 `ROW_TYPICAL_MAX_H`) — **~2 loc**  
  - `BLOCK_A_Y_FLOOR = 980`  
  - `BLOCK_A_REF_PAGE_H = 3500`  
  - **Done-when:** constants importable from tests.

- [x] **3.2** H2 — Add `_effective_block_a_y_floor()` and `anchor_block_a()` — **Files:** `debug_sv/grid_detector_v2.py` (before `label_rows_by_structure`) — **~40 loc**  
  - `_effective_block_a_y_floor(*, y_floor, page_h)` — raise `ValueError` if both None.  
  - `anchor_block_a(rows, c1_idx, *, y_floor=None, page_h=None) -> list[int]` per spec pseudocode.  
  - Return `[]` when `c1_idx is None` or `c1_idx <= 0`.  
  - Filter: `ROW_MIN_H <= h <= ROW_TYPICAL_MAX_H`, `y_mid >= y_floor_effective`, `i < c1_idx`.  
  - Return `eligible[:3]` in ascending index order.  
  - **Done-when:** UT-D1-01..04 PASS.

- [x] **3.3** H3 — Extend `_assign_labels()` with `label_source` kwarg — **Files:** `debug_sv/grid_detector_v2.py` (L437–444) — **~3 loc**  
  - Signature: `label_source: str = "structure"`.  
  - **Done-when:** kwarg used by Block A delegation; Block B/C calls unchanged (default `"structure"`).

- [x] **3.4** H4 — Replace Block A block (L501–509) — **Files:** `debug_sv/grid_detector_v2.py` — **~10 loc**  
  - Remove `pre_c1` loop; call:
    ```python
    page_h = max(r["bot"] for r in labeled)
    block_a_indices = anchor_block_a(labeled, c1_idx, page_h=page_h)
    _assign_labels(labeled, block_a_indices, LABELS_BLOCK_A, label_source="anchor_block_a")
    ```
  - **Done-when:** IT-D1-01, IT-D1-04, IT-D1-05 PASS; L474–499 and L511–514 diff shows **no logic change**.

**Commit hints:** WU-3 — `feat(d1): add anchor_block_a with ratio-scaled Y floor`; WU-4 — `refactor(d1): delegate Block A in label_rows_by_structure`

---

## Phase 4 — GREEN: Regression + label_source update

> **Done-when (phase):** `python -m pytest debug_sv/test_row_labeling.py debug_sv/test_h_line_filter.py -v` — **≥16 PASS** (9 existing + 7 new D1 tests minimum).

- [x] **4.1** Update `test_good_pdf_nine_known_labels` label_source assertions — **Files:** `debug_sv/test_row_labeling.py` — **~10 loc**  
  - Block A labels (`VOTANTES`, `URNA`, `INCINER`) → `label_source == "anchor_block_a"`.  
  - Other known labels → `label_source == "structure"`.  
  - **Done-when:** IT-D1-02 (gold PDF 9/9 labels ±80 px) still PASS with new assertions.

- [x] **4.2** Regression sweep — existing tests must stay green — **Files:** verify only — **~0 loc**  
  - `test_synthetic_spacer_gets_unk_at_1470` (UT-D1-05)  
  - `test_bad_case_preserves_row_order` (UT-D1-06)  
  - `test_json_grid_structural_labels` (IT-D1-03)  
  - `test_bad_pdf_finds_candidates_and_spacers` (IT-D1-06)  
  - `debug_sv/test_h_line_filter.py` (7 tests)  
  - **Done-when:** all above PASS unchanged.

- [x] **4.3** GREEN gate checkpoint — **Files:** none (verify only) — **~0 loc**  
  - Run: `python -m pytest debug_sv/test_row_labeling.py debug_sv/test_h_line_filter.py -v`  
  - **Done-when:** GATE-D1-04 satisfied (≥16 PASS).

**Commit hint:** WU-5 — `test(d1): assert anchor_block_a label_source on Block A rows`

---

## Phase 5 — Batch gate + PR closure

> **Done-when (phase):** All blocking gates GATE-D1-01..07 PASS; PR-1 ready to merge; D2 unblocked for spec/tasks.

- [x] **5.1** Run batch validation — **Files:** none (command only) — **~0 loc**  
  - Run: `python debug_sv/analyze_e14_batch.py --validate`  
  - Metrics source: `data/analysis_segunda_vuelta/e14c_validate_complete.json`  
  - **Done-when:** GATE-D1-01 OK% **≥ 24%**; GATE-D1-02 URNA=0 **≪ 55** (~12 residual expected); GATE-D1-03 arith_ok **≥ 24**.

- [x] **5.2** Scope verification — **Files:** PR diff review — **~0 loc**  
  - Confirm **no edits** under `src/modules/analyzer/`, `e14_worker.py`, `candidate_subcells.py`.  
  - Confirm only `grid_detector_v2.py` + `test_row_labeling.py` (+ openspec artifacts) in PR.  
  - **Done-when:** GATE-D1-07 satisfied.

- [x] **5.3** Document residual URNA=0 cases in PR notes — **Files:** PR description — **~0 loc**  
  - Category (c) mega-gap not detected: 10 PDFs — non-goal.  
  - Category (b) extra Block A row: 1 PDF — may remain URNA=0.  
  - Category (d) fragmented grid: 1 PDF — non-goal.  
  - **Done-when:** operator knows expected ~12 residual failures post-D1.

- [x] **5.4** Attach metrics snapshot to PR — **Files:** PR description — **~0 loc**  
  - Record: OK%, URNA=0 count, arith_ok, pytest count, gold PDF status.  
  - **Done-when:** WU-6 complete; merge unblocks D2 planning.

**Commit hint:** WU-6 — `chore(d1): validate batch metrics post-D1`

---

## Rollback trigger (apply agent reference)

Revert WU-3..WU-5 if **any** blocking gate fails after implementation:

| Failure | Action |
|---------|--------|
| OK% < 24% | Full revert Block A delegation to `pre_c1[:3]` |
| `test_low_urna_pdf_029` fails | Floor too aggressive — revert; spec amendment required for constant tweak |
| Gold PDF 9/9 labels shift | Revert H4; investigate page_h scaling |
| pytest regression | Revert implementation commits; keep RED tests for diagnosis |

---

## Acceptance checklist (D1 definition of done)

- [x] REQ-D1-001 through REQ-D1-013 satisfied
- [x] Strict TDD evidenced: WU-1 + WU-2 merged/committed before WU-3
- [x] GATE-D1-01..07 PASS
- [x] Residual URNA=0 documented (mega-gap 10 + other 2)
- [x] **D2 tasks may begin** only after this checklist is complete

---

## Validation commands

```powershell
Set-Location "D:\Nucleux\tools\Analizador de Elecciones"
python -m pytest debug_sv/test_row_labeling.py debug_sv/test_h_line_filter.py -v
python debug_sv/analyze_e14_batch.py --validate
```

---

## Next step

**apply-d1** — Execute tasks 1.1 → 5.4 in order; open PR-1-D1 stacked to `main`.

**Blocked until D1 merges:** `spec-d2`, `tasks-d2`, `field_groups` + tachon implementation.