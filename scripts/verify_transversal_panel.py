"""SDD verify — transversal-review-panel (T12)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.modules.review.export import build_export_envelope
from src.modules.review.exclusions import load_excluded_keys
from src.modules.review.queue import build_queue_page, iter_conflictivas_jsonl

LAB = ROOT / "Laboratorio/analisis_transversal/E14C_conflictivas_pendientes"


def main() -> int:
    meta = json.loads((LAB / "index_meta.json").read_text(encoding="utf-8"))
    excluded = load_excluded_keys("confirmed")
    rows = list(iter_conflictivas_jsonl(excluded=excluded))
    page = build_queue_page(rows, set(), source_available=lambda _mk, _s: True)
    alert_index = json.loads((LAB / "alert_index.json").read_text(encoding="utf-8"))

    checks: list[tuple[str, bool, str]] = []

    ok_count = page["total"] == meta["count"] == 4117
    checks.append((
        "queue_count_vs_lab",
        ok_count,
        f"got={page['total']} lab_meta={meta['count']}",
    ))

    from src.modules.review.alerts import build_mesa_alert_package

    blank_mk = "01_001_001_01_001"
    blank_row = next(r for r in rows if r["mesa_key"] == blank_mk)
    blank_pkg = build_mesa_alert_package(blank_row, source_available=lambda _mk, _s: True)
    checks.append((
        "real_blank_mesa_needs_confirm",
        blank_pkg["pending_human_count"] > 0,
        f"mesa={blank_mk} pending={blank_pkg['pending_human_count']}",
    ))

    partial_mk = "01_001_005_08_005"
    partial_row = next((r for r in rows if r["mesa_key"] == partial_mk), None)
    if partial_row:
        partial_pkg = build_mesa_alert_package(partial_row, source_available=lambda _mk, _s: True)
        checks.append((
            "partial_votantes_suppressed",
            len(partial_pkg["human"]) == 1 and partial_pkg["human"][0]["field"] == "SUMA_TOTAL",
            f"human_fields={[h['field'] for h in partial_pkg['human']]}",
        ))

    export = build_export_envelope({
        "13_001_001_03_011": {"SUMA_TOTAL": {"e14c": "accepted", "e14d": "rejected"}},
    })
    ok_export = all(k in export for k in ("generated", "project", "decisions"))
    checks.append(("export_json_shape", ok_export, f"keys={list(export.keys())}"))

    print("=== SDD verify: transversal-review-panel (T12) ===")
    all_ok = True
    for name, ok, detail in checks:
        tag = "PASS" if ok else "FAIL"
        if not ok:
            all_ok = False
        print(f"  [{tag}] {name} — {detail}")

    print(f"\n  jsonl_conflictivas={len(rows)} excluded_confirmed={len(excluded)}")
    print(f"  pending_human_in_queue={page['stats']['pending_human']}")
    print(f"\nOVERALL: {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())