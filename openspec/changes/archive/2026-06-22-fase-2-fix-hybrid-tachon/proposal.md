# Proposal: Fase 2-fix — Hybrid Block A + field_groups / tachon

**Change:** `fase-2-fix-hybrid-tachon`  
**Date:** 2026-06-22  
**Phase:** propose  
**Decision:** Option C (hybrid) — ship `anchor_block_a()` first, then `field_groups` + per-subcell tachon

---

## Quick path

1. **Deliverable 1 (D1):** Add `anchor_block_a()` in `debug_sv/grid_detector_v2.py`; refactor Block A assignment only; extend `test_row_labeling.py`; re-run `analyze_e14_batch.py --validate`.
2. **Gate D1:** OK% ≥ 24% (Fase 1 baseline); URNA=0 ≪ 55; pytest 16+ green; gold PDF `01_01_001_5002` unchanged.
3. **Deliverable 2 (D2):** Add additive `field_groups` + `tachon_summary` to JSONL; wire `tachon_detector.analyze_cell()` on subcell crops (priority C1/C2 via `candidate_subcells`).
4. **Gate D2:** `field_groups` present on all primary/fallback records; C1/C2 subcells carry tachon payloads; no regression on D1 metrics.

---

## Executive summary

Fase 2 structural labeling (`label_rows_by_structure`) regressed batch validation from **24% OK → 14% OK**, with **55/100 PDFs** showing `URNA=0` despite correct OCR digits in the wrong rows. Root cause: Block A takes the *first three compact rows before C1*, which in 43/55 cases are spurious header rows at Y≈833–964 instead of real nivelación rows at Y≈1023–1270.

**Product outcome:** Restore trustworthy URNA anchoring so segunda-vuelta acta extraction can resume toward the **≥70% OK** gate required before the 90K batch. Then enrich JSONL with per-subcell visual fraud signals (tachon) — especially on candidate vote cells where arithmetic alone cannot detect tampering.

**Approach:** Two chained deliverables in `debug_sv/` only. D1 fixes labeling without touching C1/C2 mega-gap logic or Block C. D2 adds schema enrichment and imports existing `tachon_detector` — no edits under `src/modules/analyzer/`.

---

## Business problem

| Problem | Impact |
|---------|--------|
| URNA systematically mislabeled | 55/100 validation PDFs report `URNA=0`; arithmetic checks fail (`arithmetic_mismatch` ≈84 cases) even when digit OCR is correct |
| False suspicious rate inflated | OK% dropped 10 points; operators cannot trust batch triage |
| 90K batch blocked | Project paused until OK% recovers to ≥24% immediately, ≥70% for full run |
| Visual fraud invisible in JSONL | Candidate cells (C1/C2) lack per-digit tachon scores; tampering may pass arithmetic while digits look altered |

**Desired product outcome:**

- Correct `fields["URNA"]` and row→label mapping for the dominant offset pattern (~78% of URNA=0 cases).
- Preserve structural labeling benefits for C1/C2 and Block C (mega-gap approach works there).
- Emit rich, backward-compatible JSONL so downstream review can flag visually suspicious subcells without breaking existing consumers of flat `fields` / `rows`.

---

## Target users and situations

| User / role | Situation | What they need |
|-------------|-----------|----------------|
| **Batch operator** | Running `analyze_e14_batch.py --validate` before 90K | OK% ≥ Fase 1 baseline; URNA populated on Antioquia sample |
| **Fraud analyst** | Reviewing suspicious actas flagged by worker | Per-subcell tachon on C1/C2; `tachon_summary` for quick triage |
| **ML / labeling pipeline** | Building ground-truth from flagged cells | High-recall tachon scores per digit crop (bootstrap labels) |
| **Maintainer (debug_sv)** | Extending segunda-vuelta extractor without touching `src/` | Clear D1/D2 boundaries; tests that catch batch regressions unit tests missed |

**Primary deployment context:** `debug_sv/` segunda-vuelta pipeline on ~100-PDF validation set (`e14c_validate_*`), Antioquia-heavy, 300 DPI renders.

---

## Business rules

### URNA anchoring (D1)

| Rule | Description |
|------|-------------|
| **R-URNA-1** | URNA must map to a compact nivelación row in Y range ≈1020–1270 (ratio-scaled), not spurious header rows Y<980 |
| **R-URNA-2** | Block A labels: `VOTANTES`, `URNA`, `INCINER` — up to 3 consecutive compact rows (`ROW_MIN_H ≤ h ≤ ROW_TYPICAL_MAX_H`) **after** Y floor filter |
| **R-URNA-3** | C1/C2 assignment unchanged: mega-gap (>400 px) between candidate rows; mid-third fallback when no mega-gap |
| **R-URNA-4** | Block C unchanged: last 4 grid rows → `BLANCO`, `NULOS`, `NO_MARCADOS`, `SUMA_TOTAL` |
| **R-URNA-5** | Spacer rows remain `UNK@{y_mid}`; UNK@ must not consume Block A slots after D1 fix |
| **R-ARITH** | Existing rule preserved: `C1 + C2 + BLANCO + NULOS + NO_MARCADOS ≤ URNA` (worker `validate_labeled_rows`) |

### Suspicious acta signals (D2)

| Rule | Description |
|------|-------------|
| **R-SUS-1** | Run `analyze_cell(crop)` on each digit subcell crop (same BGR padding as OCR) |
| **R-SUS-2** | Priority: C1/C2 candidate rows (`candidate_subcells.read_candidate_row`) — visual fraud matters more than arithmetic for tampered votes |
| **R-SUS-3** | `tachon.is_suspicious` (threshold 0.45) contributes to record-level `suspicious_reasons` when any subcell flags |
| **R-SUS-4** | Tachon is **additive** — does not override OK/SUS from arithmetic; enriches review queue |
| **R-SUS-5** | Flat `fields` / `rows` remain source of truth for counts; `field_groups` is enrichment |

---

## Scope: two deliverables

### Deliverable 1 — Hybrid Block A fix

**Goal:** Fix Block A anchoring via dedicated `anchor_block_a()` while keeping `label_rows_by_structure()` for C1/C2 and Block C.

| In scope | Out of scope (non-goals) |
|----------|--------------------------|
| New `anchor_block_a(rows, c1_idx, y_floor=…)` in `grid_detector_v2.py` | Mega-gap / C1-not-found cases (10 PDFs) |
| Refactor L501–509: replace `pre_c1[:3]` with `anchor_block_a()` | Block C "last 4 rows" redesign |
| `BLOCK_A_Y_FLOOR` constant (980 px @ ref height, ratio-scaled) | Rollback of Fase 2 structural labeling |
| Tests: offset PDF `01_01_003_5002`, gold `01_01_001_5002`, synthetic spacer | `src/modules/analyzer/` changes |
| Batch re-validation `--validate` | Batch 90K run |
| | `field_groups` / tachon (D2) |
| | Fase 1b (`process_h_lines` in `candidate_subcells`) — recommended parallel, not D1 blocker |

**Estimated LOC:** 150–250 (implementation + tests)

### Deliverable 2 — field_groups + tachon per subcell

**Goal:** Enrich JSONL with hierarchical field metadata and per-subcell visual analysis; make C1/C2 candidate rows first-class in schema.

| In scope | Out of scope (non-goals) |
|----------|--------------------------|
| Additive top-level keys: `field_groups`, `tachon_summary` | Breaking change to flat `fields` / `rows` |
| `_build_field_groups()` in `e14_worker.py` (or pure helper) | Editing `tachon_detector.py` thresholds |
| Tachon on primary path subcells (3 cells × accepted rows) | Portal / UI integration |
| Tachon on `candidate_subcells` crops (high priority) | National X/Y calibration (Fase 3) |
| Import: `from src.modules.analyzer.tachon_detector import analyze_cell` | Retraining CNN |
| New `test_tachon_field_groups.py` | |

**Estimated LOC:** 200–350

**Total estimate:** ~350–600 LOC across both deliverables.

### Dependency graph

```
D1 (anchor_block_a) ──MUST FIRST──► D2 (field_groups + tachon)
         │
         └── correct row→label map required for meaningful tachon attribution

Fase 1b (process_h_lines in candidate_subcells) ──recommended before D2 fallback reliance──► D2 C1/C2 bounds
```

---

## Edge cases

| Case | Category | D1 behavior | D2 behavior |
|------|----------|-------------|-------------|
| **Offset systematic** — `01_01_003_5002` | (a) 43/55 | URNA@~1150, value in `fields["URNA"]` | Tachon on correct URNA subcells |
| **Healthy with VOTANTES@840=0** — `01_01_010_5002` | Sano | URNA@1147=228 unchanged; VOTANTES@840 may stay 0 | No false tachon inflation on empty cells |
| **Gold mesa** — `01_01_001_5002` | Regression guard | 9 known labels unchanged | `field_groups` mirrors flat fields |
| **Mega-gap not detected** — `01_01_022_5002` | (c) 10/55 | **Out of D1 scope** — URNA may remain 0 | Tachon still runs if C1/C2 found via fallback |
| **Extra Block A row** — `01_03_009_5003` | (b) 1/55 | May not recover URNA (all empty) | Record flagged suspicious via arithmetic |
| **Fragmented grid** — `01_03_015_5003` | (d) 1/55 | Out of D1 scope | Partial `field_groups` with warnings |
| **C1/C2 via fallback** | Pre-existing | Benefits indirectly from better C1 anchor | `field_groups.block_b` includes `v_lines_row`, per-subcell tachon |
| **Short page / layout drift** | Risk | Y floor must scale: `int(page_h * 980/3500)` | Same crops as OCR — padding alignment critical |

**Key insight from healthy PDFs:** `VOTANTES@840=0` is acceptable when the real URNA row (Y≈1147) still lands in the three Block A slots. D1 must not break this by over-filtering — Y floor excludes spurious rows but must include real nivelación rows down to ~1025 (see `01_01_029_5002`).

---

## JSONL schema implications (D2)

**Backward compatibility contract:**

| Key | Status | Consumers |
|-----|--------|-----------|
| `fields` | **Retained** — unchanged builder | Batch stats, arithmetic assess |
| `rows` | **Retained** — flat list with `label`, `value`, `digits` | Existing debug tooling |
| `field_groups` | **Additive** — nested by `block_a` / `block_b` / `block_c` | New review UI, ML pipeline |
| `tachon_summary` | **Additive** — aggregate counts / flags by label | Triage dashboards |

**Proposed `field_groups` shape (illustrative):**

```json
{
  "field_groups": {
    "block_a": {
      "labels": ["VOTANTES", "URNA", "INCINER"],
      "fields": {
        "URNA": {
          "value": 141,
          "y_mid": 1149,
          "label_source": "anchor_block_a",
          "subcells": [
            {
              "idx": 0,
              "x1": 905, "y1": 978, "x2": 998, "y2": 1068,
              "digit": "1",
              "confidence": 0.994,
              "tachon": { "score": 0.12, "is_suspicious": false, "flags": [] }
            }
          ]
        }
      }
    },
    "block_b": { "labels": ["C1_CEPEDA", "C2_ABELARDO"], "fields": {} },
    "block_c": { "labels": ["BLANCO", "NULOS", "NO_MARCADOS", "SUMA_TOTAL"], "fields": {} }
  },
  "tachon_summary": {
    "suspicious_subcells": 2,
    "flags_by_label": { "C1_CEPEDA": ["TACHON"] }
  }
}
```

**D2 vision:** Candidate rows become first-class — each of 3 digit subcells carries OCR + confidence + tachon. Visual fraud detection complements arithmetic: a tachoned C1 digit may still sum correctly but must surface for human review.

---

## Success metrics and gates

### D1 gates (blocking)

| Metric | Fase 1 baseline | Fase 2 broken | D1 target |
|--------|-----------------|---------------|-----------|
| OK% (`--validate` 100 PDFs) | 24% | 14% | **≥ 24%** |
| `URNA=0` count | — | 55/100 | **≪ 55** (expect ~12 residual: 10 mega-gap + 2 other) |
| `arith_ok` | 24 | 16 | **≥ 24** |
| pytest | 16/16 | 16/16 | **16+ PASS** (new offset tests) |
| Gold PDF labels | 9/9 | 9/9 | **9/9 unchanged** |

**Validation commands:**

```powershell
python -m pytest debug_sv/test_row_labeling.py debug_sv/test_h_line_filter.py -v
python debug_sv/analyze_e14_batch.py --validate
```

Metrics source: `data/analysis_segunda_vuelta/e14c_validate_complete.json`

### D2 gates (non-blocking on batch OK%, but blocking on merge)

| Criterion | Target |
|-----------|--------|
| `field_groups` key present | 100% of records from worker |
| C1/C2 `subcells[].tachon` populated | When fallback or primary reads 3 digits |
| `tachon_summary` present | When any tachon run executed |
| D1 metrics | No regression vs post-D1 baseline |
| pytest | `test_tachon_field_groups.py` green |
| JSONL size growth | Acceptable (&lt;2× per record — monitor) |

### Long-term gate (out of this change)

| Gate | Threshold | Blocked by |
|------|-----------|------------|
| Batch 90K | OK% ≥ 70% | D1 + future grid/C1 work for residual 12 cases |

---

## Delivery plan

**Recommendation: two chained PRs** (not one monolith)

| PR | Content | Rationale |
|----|---------|-----------|
| **PR-1 (D1)** | `anchor_block_a()`, `label_rows_by_structure` refactor, `test_row_labeling` extensions | Unblocks batch metrics; smallest blast radius; review focuses on Y-floor logic |
| **PR-2 (D2)** | `field_groups` builder, tachon wiring, new tests | Depends on correct labels; larger schema surface; can parallelize Fase 1b |

**Review order for PR-1:** `anchor_block_a()` logic → diff in `label_rows_by_structure` (Block A only) → new tests → `--validate` metrics snapshot.

**Review order for PR-2:** schema contract → tachon call sites (crops match OCR) → backward compat of flat fields → aggregate `tachon_summary`.

---

## Files to touch

| File | D1 | D2 |
|------|----|----|
| `debug_sv/grid_detector_v2.py` | ✓ `anchor_block_a()`, refactor | Optional pure helper |
| `debug_sv/test_row_labeling.py` | ✓ offset + regression tests | — |
| `debug_sv/e14_worker.py` | — | ✓ `_build_field_groups`, tachon in primary loop |
| `debug_sv/candidate_subcells.py` | — | ✓ tachon per subcell |
| `debug_sv/test_tachon_field_groups.py` | — | ✓ new |
| `debug_sv/analyze_e14_batch.py` | — | Optional stats docstring |
| `src/modules/analyzer/*` | **No edits** | Import only |

---

## Risks and open questions

| Risk | Severity | Mitigation |
|------|----------|------------|
| Y floor 980 too rigid for national layouts | **High** | Ratio-scale from page height; validate on non-Antioquia depts in spec phase |
| Y floor too aggressive — drops real URNA@1025 | **Medium** | Test `01_01_029_5002`; consider ceiling ~1300 |
| 10 mega-gap cases remain URNA=0 after D1 | **Medium** | Document as separate work item; do not block D1 merge |
| `candidate_subcells` raw h_lines desync | **Medium** | Fase 1b before relying on fallback+tachon for C1/C2 |
| Tachon high recall → noisy `suspicious_subcells` | **Medium** | `tachon_summary` for triage; do not auto-reject actas |
| Unit tests pass but batch fails (Fase 2 lesson) | **High** | Mandatory `--validate` gate on PR-1; encode offset PDF in tests |
| JSONL size at 90K scale | **Low** | Subcell coords + tachon dicts; monitor; optional compression later |
| Block C last-4 mislabel on 12–14 row grids | **Medium** | Unchanged in D1; track separately |

### Open questions (for spec phase)

1. **Y floor calibration:** Fixed 980 vs `page_h * ratio` — which reference height (3500?) and tolerance band?
2. **Block A row count:** Always 3 slots vs dynamic 1–3 based on compact rows found above floor?
3. **Post-OCR validation:** Should worker reject Block A if INCINER holds URNA value (value-in-wrong-slot heuristic) — D1 or later?
4. **Fase 1b timing:** Same PR as D2 or separate PR between D1 and D2?
5. **Tachon on empty cells:** Skip when `has_ink=false` or always run (affects CPU)?
6. **Record-level SUS:** Does any `tachon.is_suspicious` flip `is_suspicious` or only add `suspicious_reasons[]` entry?

---

## Checklist (acceptance)

### D1
- [ ] `anchor_block_a()` applies Y floor (ratio-scaled), not global first-3-compact
- [ ] C1/C2 mega-gap logic untouched
- [ ] Block C last-4 logic untouched
- [ ] `01_01_003_5002` assigns URNA to Y≈1150 in tests
- [ ] `01_01_001_5002` still passes 9 known labels
- [ ] `--validate` OK% ≥ 24%
- [ ] No edits under `src/modules/analyzer/`

### D2
- [ ] `field_groups` additive; flat `fields` / `rows` retained
- [ ] Tachon runs on BGR crops matching OCR padding
- [ ] C1/C2 candidate subcells populated in `field_groups.block_b`
- [ ] `test_tachon_field_groups.py` green
- [ ] D1 metrics not regressed

---

## References

| Artifact | Role |
|----------|------|
| `openspec/changes/fase-2-fix-hybrid-tachon/exploration.md` | Technical insertion points |
| `debug_sv/Reporte explore — 55 PDFs con URNA=0 (Fase 2).md` | Offset diagnosis (43/55) |
| `debug_sv/fase-2-labeling-pausa-regresion-2026-06-22.md` | Regression metrics, Option C |
| `data/analysis_segunda_vuelta/e14c_validate_results.jsonl` | Current schema samples |
| Engram `#542` topic `sdd/fase-2-fix-hybrid-tachon/explore` | Exploration memory |

---

## Next step

**Spec** phase: formalize D1 (`anchor_block_a` API, Y-floor constants, test matrix) then D2 (`field_groups` JSON schema, tachon integration contract, Fase 1b decision).