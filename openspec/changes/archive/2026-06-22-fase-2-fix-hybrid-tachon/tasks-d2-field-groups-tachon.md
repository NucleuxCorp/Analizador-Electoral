# Tasks D2: field_groups + per-subcell tachon

**Change:** `fase-2-fix-hybrid-tachon`  
**Deliverable:** D2 (PR-2)  
**Date:** 2026-06-22  
**Phase:** tasks  
**Depends on:** D1 merged (`anchor_block_a` on `main`)  
**Authoritative spec:** `spec-d2-field-groups-tachon.md`  
**Authoritative design:** `design-d2-field-groups-tachon.md`  
**Strict TDD:** `debug_sv/test_tachon_field_groups.py` RED before any production code

---

## ⛔ Blocker — do not start apply until cleared

- [x] **GATE-D2-00:** D1 PR-1 is merged to `main` (`anchor_block_a`, `test_row_labeling` extensions).
- [x] **GATE-D2-01:** Post-D1 `--validate` baseline is frozen and passing:
  - OK% ≥ 24%
  - URNA=0 ≪ 55
  - `python -m pytest debug_sv/test_row_labeling.py debug_sv/test_h_line_filter.py -v` — all green

**Do not start until D1 PR merged and `--validate` gate passed.**

---

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~350–500 (tests ~280, impl ~170) |
| 400-line budget risk | Medium (single PR near budget ceiling) |
| Chained PRs recommended | Yes (PR-2 after PR-1) |
| Suggested split | Single PR-2 (D2 only); no D1 work in this PR |
| Delivery strategy | stacked-to-main |
| Chain strategy | stacked-to-main (PR-2 targets `main` after PR-1 merged) |
| Decision needed before apply | No (spec + design resolved; blocked only on D1 gate) |

### Suggested Work Units

| Unit | Goal | Commit | Est. LOC | Notes |
|------|------|--------|----------|-------|
| WU-1 | RED test suite (all T-D2-*) | `test: add test_tachon_field_groups.py (RED)` | ~280 | 19 tests across 5 classes; must fail on pre-D2 codebase |
| WU-2 | Shared tachon helper | `feat: add subcell_tachon shared helper` | ~55 | `NEUTRAL_TACHON`, `enrich_subcell_tachon`, `build_subcell_payload` |
| WU-3 | Fallback path tachon | `feat: tachon in read_candidate_row + subcells schema` | ~40 | C1/C2 priority; `subcells[]` length 3 |
| WU-4 | Primary path tachon | `feat: primary loop tachon + _builder_inputs` | ~50 | Per accepted known row; strip internals before JSONL |
| WU-5 | Field groups assembly | `feat: _build_field_groups + _build_tachon_summary` | ~70 | Block B fallback override; omit `tachon_summary` when zero analysis |
| WU-6 | Worker wire + assess | `feat: process_pdf_task wire + _assess tachon_suspicious` | ~30 | REQ-D2-020/021; flat `fields`/`rows` unchanged |
| WU-7 | Gates | `chore: validate green + batch --validate` | ~5 | Optional docstring in `analyze_e14_batch.py` |

**PR-2 total:** ~350–500 LOC in `debug_sv/` only. **No edits** under `src/modules/analyzer/` (import `analyze_cell` only).

---

## Implementation order (apply sequence)

### Phase 0: Preconditions

- [x] 0.1 Confirm D1 merged; branch PR-2 from current `main`.
- [x] 0.2 Snapshot post-D1 `--validate` metrics (`e14c_validate_complete.json`) for regression compare.

---

### Phase 1: RED — `test_tachon_field_groups.py` (strict TDD)

Create `debug_sv/test_tachon_field_groups.py` **before** touching production modules. All tests MUST fail on pre-D2 codebase.

- [x] 1.1 **Fixtures & helpers** (~60 LOC)
  - `MOCK_IMG` (32×32 BGR), `synthetic_labeled_rows`, `make_cell_analysis()` factory
  - `mock_analyze_cell` fixture (tracks call count + crop shapes)
  - `assert_subcell_schema()`, `assert_field_groups_invariant()`, `run_worker_mocked()`
  - Patch target: `src.modules.analyzer.tachon_detector.analyze_cell`
  - PDF refs: gold `01_01_001_5002`, offset `01_01_003_5002`

- [x] 1.2 **`TestFieldGroupsSchema`** — T-D2-S01..S05
  - [x] S01 `test_field_groups_has_three_blocks` — `block_a`, `block_b`, `block_c` each with `labels` + `fields`
  - [x] S02 `test_subcell_required_keys` — idx, coords, digit, confidence, has_ink, tachon (6 tachon keys)
  - [x] S03 `test_fields_value_invariant` — `field_groups.*.fields[L].value == fields[L]`
  - [x] S04 `test_rows_unchanged_shape` — flat `rows[]` keys unchanged
  - [x] S05 `test_unk_rows_excluded` — no `UNK@*` in `field_groups`

- [x] 1.3 **`TestTachonMocked`** — T-D2-M01..M05
  - [x] M01 `test_analyze_cell_mocked_on_inked_crop` — one call per inked subcell, BGR ndarray
  - [x] M02 `test_crop_padding_matches_ocr` — slice uses `CELL_PAD` (8 px) inner crop
  - [x] M03 `test_neutral_tachon_when_no_ink` — `analyze_cell` not called; neutral tachon dict
  - [x] M04 `test_tachon_summary_aggregation` — suspicious count + `flags_by_label` sorted unique
  - [x] M05 `test_assess_appends_tachon_suspicious` — reason added; `is_suspicious` stays false when arithmetic OK

- [x] 1.4 **`TestCandidateSubcells`** — T-D2-C01..C04
  - [x] C01 `test_read_candidate_row_returns_three_subcells` — `len(subcells)==3`, idx 0..2
  - [x] C02 `test_block_b_includes_v_lines_row_on_fallback` — `v_lines_row` length 4 on Block B fallback
  - [x] C03 `test_fallback_subcells_prefer_over_primary` — `primary+fallback` uses candidate subcells
  - [x] C04 `test_primary_only_block_b_no_v_lines_row` — primary-only omits `v_lines_row`

- [x] 1.5 **`TestEmptyCellPolicy`** — T-D2-E01..E03
  - [x] E01 `test_empty_cell_digit_zero_confidence_zero` — no ink → digit `"0"`, conf `0.0`
  - [x] E02 `test_healthy_votantes_zero_no_false_tachon` — all-neutral; `suspicious_subcells==0`
  - [x] E03 `test_tachon_summary_omitted_when_no_analysis` — all empty → no `tachon_summary` key

- [x] 1.6 **`TestRegressionGuards`** — T-D2-R01..R02
  - [x] R01 `test_gold_pdf_field_groups_mirror_fields` — gold PDF 9 labels in `field_groups`
  - [x] R02 `test_d1_labels_required_for_block_a_source` — Block A `label_source == "anchor_block_a"` (post-D1)

- [x] 1.7 **RED gate:** `python -m pytest debug_sv/test_tachon_field_groups.py -v` — failures expected (missing `field_groups`, `subcells`, etc.)

**Commit:** `test: add test_tachon_field_groups.py (RED)`

---

### Phase 2: `subcell_tachon.py` (shared helper)

- [x] 2.1 Create `debug_sv/subcell_tachon.py`
  - [x] `NEUTRAL_TACHON` constant (REQ-D2-013)
  - [x] `enrich_subcell_tachon(img, cell, *, has_ink, pad=CELL_PAD)` — skip `analyze_cell` when no ink
  - [x] `build_subcell_payload(cell, *, digit, confidence, has_ink, img, pad)` — full Subcell schema
  - Import `crop_cell` from `candidate_subcells`; `analyze_cell` from `tachon_detector` (no `src/` edits)

- [x] 2.2 **Partial GREEN:** M02, M03, E01 pass when wired through direct helper tests (if isolated); full suite still RED.

**Commit:** `feat: add subcell_tachon shared helper`

---

### Phase 3: `candidate_subcells.py` — fallback tachon

- [x] 3.1 Extend `read_candidate_row()` return dict with `subcells: list[dict]` (length 3)
  - Per-cell loop: reuse `crop_cell` for OCR and tachon (same geometry)
  - Call `build_subcell_payload` for each of 3 cells (REQ-D2-011)
  - Preserve existing keys: `label`, `y_top`, `y_bot`, `v_lines_row`, `digits`, `confidences`, `value`, `ink`, `crops`

- [x] 3.2 **GREEN partial:** C01, C02 pass; M01 for fallback path when integrated.

**Commit:** `feat: tachon in read_candidate_row + subcells schema`

---

### Phase 4: `e14_worker.py` — primary path + builder inputs

- [x] 4.1 Extend `_analyze_primary()` OCR loop (L93–102 area)
  - For each accepted row with `is_known_label(label)`: build up to 3 subcells via `build_subcell_payload`
  - Collect `primary_row_subcells[label]`; exclude `UNK@*` rows (REQ-D2-015)
  - Return internal `_builder_inputs: { labeled_rows, primary_row_subcells }` — **not** written to JSONL

- [x] 4.2 **GREEN partial:** M01, M02, M03 for primary path.

**Commit:** `feat: primary loop tachon + _builder_inputs`

---

### Phase 5: `e14_worker.py` — `_build_field_groups` + `_build_tachon_summary`

- [x] 5.1 Implement `_build_field_groups(*, labeled_rows, primary_row_subcells, fallback_candidates, flat_fields)`
  - Initialize `block_a` / `block_b` / `block_c` with static `labels` from `grid_detector_v2`
  - Route known labels to blocks; normalize `y_top`/`y_bot` vs `top`/`bot`
  - **Block B override:** fallback `subcells` + `v_lines_row` win over primary (REQ-D2-012, REQ-D2-014)
  - Skip labels with no row and no fallback entry
  - Invariant: populated `fields[L].value` matches `field_groups.*.fields[L].value`

- [x] 5.2 Implement `_build_tachon_summary(field_groups)`
  - Walk all `subcells[*].tachon`; count `analyzed_subcells`, `suspicious_subcells`
  - `flags_by_label`: sorted unique flags per label
  - Return `None` when `analyzed_subcells == 0` (omit key — REQ-D2-006, E03)

- [x] 5.3 **GREEN partial:** S01–S05, M04, C03, C04, E02, E03, R01 pass.

**Commit:** `feat: _build_field_groups + _build_tachon_summary`

---

### Phase 6: `e14_worker.py` — wire + `_assess`

- [x] 6.1 `process_pdf_task()` orchestration
  - After fallback merge: call `_build_field_groups`; attach `field_groups` to every successful record
  - Attach `tachon_summary` only when not `None`
  - Strip `_builder_inputs` before JSONL emit

- [x] 6.2 Extend `_assess(record)`
  - When `tachon_summary.suspicious_subcells > 0`: append `"tachon_suspicious"` once (REQ-D2-020)
  - Do **not** set `is_suspicious` from tachon alone (REQ-D2-021)

- [x] 6.3 Optional: `analyze_e14_batch.py` docstring — stats ignore `field_groups` / `tachon_summary`

- [x] 6.4 **GREEN full:** `python -m pytest debug_sv/test_tachon_field_groups.py -v` — all 19 tests PASS

**Commit:** `feat: process_pdf_task wire + _assess tachon_suspicious`

---

### Phase 7: Regression gates (blocking merge)

- [x] 7.1 D2 unit tests green:
  ```powershell
  python -m pytest debug_sv/test_tachon_field_groups.py -v
  ```

- [x] 7.2 D1 regression tests still green:
  ```powershell
  python -m pytest debug_sv/test_row_labeling.py debug_sv/test_h_line_filter.py -v
  ```

- [x] 7.3 Batch validation — D1 metrics unchanged:
  ```powershell
  python debug_sv/analyze_e14_batch.py --validate
  ```
  - OK% ≥ post-D1 baseline (≥ 24%)
  - `field_groups` present on 100% of successful worker records in output JSONL
  - C1/C2 subcells carry `tachon` when digits read (primary or fallback)

- [x] 7.4 Confirm no files changed under `src/modules/analyzer/`

**Commit:** `chore: validate green + batch --validate`

---

## Acceptance checklist (PR-2 merge)

- [x] `field_groups` on all `primary` / `primary+fallback` records (REQ-D2-001)
- [x] Each subcell has full schema + `CellAnalysis.to_dict()` tachon (REQ-D2-004/005)
- [x] Flat `fields` / `rows` semantics unchanged (REQ-D2-007)
- [x] Empty cells skip `analyze_cell`; neutral tachon emitted (REQ-D2-013)
- [x] Fallback Block B includes `v_lines_row`; subcells preferred over primary (REQ-D2-011/014)
- [x] `tachon_suspicious` in `suspicious_reasons` when flagged; arithmetic authority preserved (REQ-D2-020/021)
- [x] `_builder_inputs` never in JSONL output
- [x] Post-D1 `--validate` metrics not regressed

---

## Out of scope (explicit — do not implement in D2 apply)

- D1: `anchor_block_a()`, Block A refactor, `test_row_labeling` D1 tests
- Edits to `src/modules/analyzer/tachon_detector.py` or threshold changes
- Portal / UI consumption of `field_groups`
- Fase 1b (`process_h_lines` in `candidate_subcells`) — recommended parallel, not blocker
- Batch 90K run
- National X/Y calibration (Fase 3)

---

## Review order (for PR-2 reviewer)

1. Schema contract + JSON examples (`spec-d2` §3, `design-d2` §5)
2. `subcell_tachon` crop/pad alignment (T-D2-M02)
3. Fallback Block B override (T-D2-C03)
4. `_assess` additive behavior (T-D2-M05)
5. Flat `fields` / `rows` unchanged (T-D2-S03, S04)
6. `--validate` snapshot vs post-D1 baseline

---

## References

| Artifact | Role |
|----------|------|
| `spec-d2-field-groups-tachon.md` | REQ-D2-* requirements, test matrix |
| `design-d2-field-groups-tachon.md` | Signatures, sequence diagrams, commit plan |
| `proposal.md` | R-SUS-1..5, PR-2 delivery plan |
| `debug_sv/e14_worker.py` | `_analyze_primary`, `_assess`, `process_pdf_task` |
| `debug_sv/candidate_subcells.py` | `read_candidate_row`, `crop_cell` |

---

## Next step

**apply-d2:** Execute Phase 0 gate check, then Phase 1 RED commit; proceed WU-1 → WU-7 in order.