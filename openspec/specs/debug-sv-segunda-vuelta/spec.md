# Spec: debug_sv Segunda Vuelta Worker

**Domain:** `debug-sv-segunda-vuelta`  
**Status:** Active (synced from `fase-2-fix-hybrid-tachon`, 2026-06-22)  
**Source change:** `openspec/changes/archive/2026-06-22-fase-2-fix-hybrid-tachon/`

---

## Overview

E-14C segunda vuelta batch worker (`debug_sv/`) extracts vote counts from PDF actas, detects arithmetic anomalies, and enriches output with hierarchical field metadata and per-digit visual fraud signals.

Flat `fields` and `rows` remain the source of truth for counts. `field_groups` and `tachon_summary` are additive JSONL keys.

---

## D1 — Block A anchoring (`anchor_block_a`)

### Requirements

| ID | Requirement |
|----|-------------|
| REQ-D1-001 | Block A labels (VOTANTES, URNA, INCINER) MUST be assigned via `anchor_block_a()`, not `pre_c1[:3]`. |
| REQ-D1-002 | Y-floor MUST scale with page height: `min(int(page_h * 980/3500), 1020)`. |
| REQ-D1-003 | `page_h` MUST be full render height (`gray.shape[0]`), not `max(row["bot"])`. |
| REQ-D1-004 | Block A rows MUST emit `label_source: "anchor_block_a"`. |
| REQ-D1-005 | C1/C2 mega-gap and Block C labeling MUST remain unchanged. |

### Gates

- OK% ≥ 24% on 100-PDF validate set
- URNA=0 ≪ 55 (achieved: 20/100)
- `test_row_labeling.py` + `test_h_line_filter.py` green

Full spec: archived `spec-d1-block-a.md`

---

## D2 — field_groups + per-subcell tachon

### Requirements

| ID | Requirement |
|----|-------------|
| REQ-D2-001 | Successful records MUST include `field_groups` with `block_a`, `block_b`, `block_c`. |
| REQ-D2-004 | Each subcell MUST include `idx`, coords, `digit`, `confidence`, `has_ink`, `tachon`. |
| REQ-D2-007 | Flat `fields` / `rows` semantics MUST NOT change. |
| REQ-D2-011 | `read_candidate_row` MUST attach tachon to each of 3 subcells. |
| REQ-D2-013 | Empty cells (`has_ink=false`) MUST skip `analyze_cell()` and emit neutral tachon. |
| REQ-D2-014 | Fallback Block B MUST include `v_lines_row`; fallback subcells override primary. |
| REQ-D2-020 | `tachon_suspicious` MUST appear in `suspicious_reasons` when any subcell flagged. |
| REQ-D2-021 | Tachon MUST NOT alone set `is_suspicious=true`. |

### Tachon flags

Imported from `src/modules/analyzer/tachon_detector.py` (read-only):

- `TACHON` — crossing stroke over digit
- `DOBLE_ESCRITURA` — overlapping digits
- `DENSIDAD_ALTA` — abnormal ink density
- `ZONA_SUCIA` — smudges / erasure marks

Full spec: archived `spec-d2-field-groups-tachon.md`

---

## Validation

```powershell
python -m pytest debug_sv/test_tachon_field_groups.py debug_sv/test_row_labeling.py debug_sv/test_h_line_filter.py -v
python debug_sv/analyze_e14_batch.py --validate
```

---

## Out of scope

- Batch 90K (blocked until OK% ≥ 70%)
- Portal UI consumption of `field_groups`
- National layout calibration (Fase 3)
- Edits to `tachon_detector.py` thresholds