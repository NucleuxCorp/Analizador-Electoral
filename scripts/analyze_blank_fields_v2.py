"""
analyze_blank_fields_v2.py

Version 2 of analyze_blank_fields.py.

Changes from v1:
  - Reads ONLY cross_mesa_validation_*.jsonl (segunda vuelta, all 34 depts).
    The _cnn.jsonl complement is removed — those are primera vuelta data and
    all depts now have cross_mesa_validation.
  - Blank criterion for all sources: val == 0 or val is None
    (cross_mesa_validation stores 0, not None, when no ink is detected —
    the underlying extractor is grid_detector_v3 via e14_worker.py, which
    returns 0 for empty cells, not None)
  - Adds per-dept breakdown for E14C.

Usage:
    python scripts/analyze_blank_fields_v2.py
    python scripts/analyze_blank_fields_v2.py --source e14c
    python scripts/analyze_blank_fields_v2.py --csv
    python scripts/analyze_blank_fields_v2.py --by-dept
"""

import argparse
import glob
import json
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

TOTAL_FIELDS = ["VOTANTES", "URNA", "SUMA_TOTAL"]


def _is_blank(val) -> bool:
    return val is None or val == 0


def analyze_cross_val(by_dept: bool = False):
    """Read E14C, E14D, E14T from cross_mesa_validation_*.jsonl (segunda vuelta only)."""
    stats = {
        src: {"has_data": 0, "field_zero": defaultdict(int),
              "at_least_one": 0, "all_three": 0, "combos": defaultdict(int),
              "by_dept": defaultdict(lambda: {"has_data": 0, "field_zero": defaultdict(int), "at_least_one": 0})}
        for src in ["e14c", "e14d", "e14t"]
    }

    for f in sorted(glob.glob(str(DATA_DIR / "cross_mesa_validation_*.jsonl"))):
        # Extract dept code from filename
        stem = Path(f).stem  # cross_mesa_validation_05
        dept = stem.replace("cross_mesa_validation_", "")

        with open(f, encoding="utf-8") as fp:
            for line in fp:
                row = json.loads(line)
                sources = row.get("sources", {})
                for src in ["e14c", "e14d", "e14t"]:
                    src_data = sources.get(src, {})
                    if src_data.get("status") != "ok":
                        continue
                    fields = src_data.get("fields", {})
                    c1 = fields.get("C1_CEPEDA") or 0
                    c2 = fields.get("C2_ABELARDO") or 0
                    if (c1 + c2) == 0:
                        continue
                    s = stats[src]
                    s["has_data"] += 1
                    zeros = [field for field in TOTAL_FIELDS if _is_blank(fields.get(field))]
                    if zeros:
                        s["at_least_one"] += 1
                        for field in zeros:
                            s["field_zero"][field] += 1
                        if len(zeros) == 3:
                            s["all_three"] += 1
                        s["combos"][tuple(sorted(zeros))] += 1
                    if by_dept and src == "e14c":
                        d = s["by_dept"][dept]
                        d["has_data"] += 1
                        if zeros:
                            d["at_least_one"] += 1
                            for field in zeros:
                                d["field_zero"][field] += 1
    return stats


def print_stats(label: str, s: dict, n: int) -> None:
    if n == 0:
        print(f"{label}: sin datos")
        return
    at1 = s["at_least_one"]
    print(f"\n=== {label} — {n:,} mesas ===")
    print(f"  Al menos 1 campo total en 0/null : {at1:,}  ({at1/n*100:.2f}%)")
    print(f"  Los 3 campos en 0/null           : {s['all_three']:,}  ({s['all_three']/n*100:.2f}%)")
    for field in TOTAL_FIELDS:
        v = s["field_zero"][field]
        print(f"    {field:<12}: {v:,}  ({v/n*100:.2f}%)")
    print("  Combinaciones:")
    for combo, cnt in sorted(s["combos"].items(), key=lambda x: -x[1]):
        label_c = ", ".join(combo)
        print(f"    {label_c:<40}: {cnt:,}  ({cnt/n*100:.2f}%)")


def print_by_dept(by_dept: dict) -> None:
    print("\n=== E14C por departamento ===")
    print(f"  {'dept':<6} {'mesas':>7} {'al_menos_1':>10} {'%':>7} {'VOTANTES':>9} {'URNA':>7} {'SUMA_TOTAL':>10}")
    for dept in sorted(by_dept.keys()):
        d = by_dept[dept]
        n = d["has_data"]
        if n == 0:
            continue
        at1 = d["at_least_one"]
        v = d["field_zero"]["VOTANTES"]
        u = d["field_zero"]["URNA"]
        st = d["field_zero"]["SUMA_TOTAL"]
        print(f"  {dept:<6} {n:>7,} {at1:>10,} {at1/n*100:>6.1f}% {v:>9,} {u:>7,} {st:>10,}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["e14c", "e14d", "e14t", "all"], default="all")
    parser.add_argument("--csv", action="store_true", help="Print CSV summary")
    parser.add_argument("--by-dept", action="store_true", help="Print E14C breakdown by dept")
    args = parser.parse_args()

    print("Criterio: val == 0 or val is None (cross_mesa_validation, segunda vuelta)")
    print("Fuente: SOLO cross_mesa_validation_*.jsonl — sin _cnn.jsonl (primera vuelta)")
    print("Leyendo cross_mesa_validation files...")
    cross = analyze_cross_val(by_dept=args.by_dept)

    sources_to_print = {
        "E14C": (cross["e14c"], cross["e14c"]["has_data"]),
        "E14D": (cross["e14d"], cross["e14d"]["has_data"]),
        "E14T": (cross["e14t"], cross["e14t"]["has_data"]),
    }

    if args.source != "all":
        sources_to_print = {k: v for k, v in sources_to_print.items()
                            if args.source.upper() == k.upper()}

    for label, (s, n) in sources_to_print.items():
        print_stats(label, s, n)

    if args.by_dept:
        print_by_dept(cross["e14c"]["by_dept"])

    if args.csv:
        print("\n\nCSV:")
        print("fuente,criterio,mesas,al_menos_1,al_menos_1_pct,VOTANTES,VOTANTES_pct,URNA,URNA_pct,SUMA_TOTAL,SUMA_TOTAL_pct,todos_3,todos_3_pct")
        for label, (s, n) in sources_to_print.items():
            if n == 0:
                continue
            row_data = [
                label, "val_eq_0_or_None", n,
                s["at_least_one"], f"{s['at_least_one']/n*100:.2f}",
                s["field_zero"]["VOTANTES"], f"{s['field_zero']['VOTANTES']/n*100:.2f}",
                s["field_zero"]["URNA"], f"{s['field_zero']['URNA']/n*100:.2f}",
                s["field_zero"]["SUMA_TOTAL"], f"{s['field_zero']['SUMA_TOTAL']/n*100:.2f}",
                s["all_three"], f"{s['all_three']/n*100:.2f}",
            ]
            print(",".join(str(x) for x in row_data))


if __name__ == "__main__":
    main()
