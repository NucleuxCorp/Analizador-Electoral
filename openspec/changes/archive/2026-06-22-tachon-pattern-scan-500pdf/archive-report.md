# Archive Report — tachon-pattern-scan-500pdf

**Change:** `tachon-pattern-scan-500pdf`  
**Archived:** 2026-06-22  
**Status:** COMPLETE  
**Artifact store:** openspec

---

## Executive summary

SDD change `tachon-pattern-scan-500pdf` fully planned, implemented, verified, and archived. Delivers stratified 500-PDF manifest and per-method isolated tachon detector scan (Approach A: direct detector imports, zero `src/modules/analyzer/` edits). PR-1 (`feat/tachon-scan-manifest`) and PR-2 (`feat/tachon-method-scan`) merged to `develop`.

---

## Deliverables completed

| Deliverable | PR | Gates |
|-------------|-----|-------|
| Stratified 500-PDF manifest | PR-1 | GATE-SCAN-02, GATE-SCAN-08 |
| Per-method scan + aggregates | PR-2 | GATE-SCAN-01–09 |

**Full run:** 500/500 PDFs OK, 11,855 subcell records, 7,064 ink subcells, 46.6 s wall time.

---

## Verification

- **verify-report:** `verify-report.md` — PASS, 9/9 blocking gates, 0 CRITICAL, 0 WARNING
- **pytest:** 37/37 green (6 manifest + 12 scan + 19 D2 regression)

---

## Specs synced

| Domain | Action | Path |
|--------|--------|------|
| `debug-sv-segunda-vuelta` | Updated | `openspec/specs/debug-sv-segunda-vuelta/spec.md` |

Delta spec preserved in archive folder (`spec.md`).

---

## Archive location

```
openspec/changes/archive/2026-06-22-tachon-pattern-scan-500pdf/
  exploration.md, proposal.md, spec.md, design.md, tasks.md
  verify-report.md, archive-report.md
```

---

## Implementation files

| File | Role |
|------|------|
| `debug_sv/build_tachon_scan_manifest.py` | Stratified manifest builder |
| `debug_sv/tachon_method_scan.py` | Scan runner + aggregates + report |
| `debug_sv/test_tachon_scan_manifest.py` | Manifest tests (T-M01–T-M05) |
| `debug_sv/test_tachon_method_scan.py` | Scan tests (T-S*, T-J*, T-A*, T-R01, T-P01, T-D01) |
| `data/analysis_segunda_vuelta/tachon_scan_500_manifest.json` | Locked 500-PDF sample |
| `data/analysis_segunda_vuelta/tachon_method_scan_500*.json*` | Scan artifacts |

---

## Residual / follow-up (out of scope)

| Item | Priority |
|------|----------|
| Production threshold tuning from scan distributions | Medium — human review required |
| Optional crop gallery (`--save-crops`) | Low — non-blocking |
| Batch 90K scaling | Low — blocked on OK% ≥ 70% |

---

## SDD cycle

```
explore → propose → spec → design → tasks → apply (PR-1 + PR-2) → verify → archive ✅
```

**next_recommended:** `/sdd-new` for threshold calibration or Fase 1b follow-up.