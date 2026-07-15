"""
analyze_blank_fields.py

Analiza qué campos de totales (VOTANTES, URNA, SUMA_TOTAL) quedan en blanco
en las actas E14, por fuente (E14C, E14D, E14T).

Fuentes de datos:
  - cross_mesa_validation_*.jsonl  -> E14C + E14D + E14T (cuando el OCR fue corrido)
  - data/*_cnn.jsonl               -> E14C complementario para deptos sin cross-val E14C

Los campos en blanco se identifican como null o 0 cuando C1+C2 > 0
(un acta con votos pero sin total es una omisión real del jurado).

Usage:
    python scripts/analyze_blank_fields.py
    python scripts/analyze_blank_fields.py --source e14c   # solo una fuente
    python scripts/analyze_blank_fields.py --csv           # exportar CSV
"""

import argparse
import glob
import json
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

TOTAL_FIELDS = ["VOTANTES", "URNA", "SUMA_TOTAL"]

# Mapping from _cnn.jsonl flat keys -> cross-val field names
CNN_FIELD_MAP = {
    "VOTANTES":   "total_votantes",
    "URNA":       "total_urna",
    "SUMA_TOTAL": "suma_total",
    "C1_CEPEDA":  None,   # votos_candidatos[0] — handled separately
    "C2_ABELARDO": None,  # votos_candidatos[varies] — handled separately
}

# Departments already covered by cross-val E14C (>5% coverage)
# These are read from cross_mesa_validation directly.
# _cnn.jsonl is used only for the rest.
CROSS_VAL_E14C_COVERED = {
    "01", "03", "07", "16", "17", "19", "21", "23", "24",
    "25", "26", "27", "28", "29", "40", "44", "46", "48",
    "50", "52", "54", "56", "60", "64",
}

CNN_DEPT_MAP = {
    "bolivar":      "05",
    "caldas":       "09",
    "caqueta":      "11",
    "cauca":        "12",
    "cesar":        "13",
    "cundinamarca": "15",
    "valle":        "31",
    "vichada":      "68",
    "vaupes":       "72",
    "amazonas":     "88",
}


def _is_blank(val) -> bool:
    return val is None or val == 0


def analyze_cross_val():
    """Read E14C, E14D, E14T from cross_mesa_validation files."""
    stats = {
        src: {"has_data": 0, "field_zero": defaultdict(int),
              "at_least_one": 0, "all_three": 0, "combos": defaultdict(int)}
        for src in ["e14c", "e14d", "e14t"]
    }

    for f in sorted(glob.glob(str(DATA_DIR / "cross_mesa_validation_*.jsonl"))):
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
                    zeros = [f for f in TOTAL_FIELDS if _is_blank(fields.get(f))]
                    if zeros:
                        s["at_least_one"] += 1
                        for field in zeros:
                            s["field_zero"][field] += 1
                        if len(zeros) == 3:
                            s["all_three"] += 1
                        s["combos"][tuple(sorted(zeros))] += 1
    return stats


def analyze_cnn_files():
    """Read E14C from *_cnn.jsonl for departments missing from cross-val."""
    s = {"has_data": 0, "field_zero": defaultdict(int),
         "at_least_one": 0, "all_three": 0, "combos": defaultdict(int),
         "by_dept": {}}

    for slug, code in CNN_DEPT_MAP.items():
        path = DATA_DIR / f"{slug}_cnn.jsonl"
        if not path.exists():
            continue
        dept_stats = {"has_data": 0, "field_zero": defaultdict(int), "at_least_one": 0}
        with open(path, encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue  # skip broken/partial lines
                # Detect C1+C2: votos_candidatos list, first two non-null
                vc = row.get("votos_candidatos") or []
                non_null = [v for v in vc if v is not None]
                if len(non_null) < 2:
                    continue
                # Use flat field names
                fields = {
                    "VOTANTES":   row.get("total_votantes"),
                    "URNA":       row.get("total_urna"),
                    "SUMA_TOTAL": row.get("suma_total"),
                }
                s["has_data"] += 1
                dept_stats["has_data"] += 1
                zeros = [f for f in TOTAL_FIELDS if _is_blank(fields.get(f))]
                if zeros:
                    s["at_least_one"] += 1
                    dept_stats["at_least_one"] += 1
                    for field in zeros:
                        s["field_zero"][field] += 1
                        dept_stats["field_zero"][field] += 1
                    if len(zeros) == 3:
                        s["all_three"] += 1
                    s["combos"][tuple(sorted(zeros))] += 1
        s["by_dept"][code] = dept_stats
    return s


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["e14c", "e14d", "e14t", "all"], default="all")
    parser.add_argument("--csv", action="store_true", help="Print CSV summary")
    args = parser.parse_args()

    print("Leyendo cross_mesa_validation files...")
    cross = analyze_cross_val()

    print("Leyendo *_cnn.jsonl para deptos complementarios...")
    cnn = analyze_cnn_files()

    # Merge E14C: cross-val + cnn
    e14c_merged = {
        "has_data":    cross["e14c"]["has_data"] + cnn["has_data"],
        "at_least_one": cross["e14c"]["at_least_one"] + cnn["at_least_one"],
        "all_three":   cross["e14c"]["all_three"] + cnn["all_three"],
        "field_zero":  defaultdict(int),
        "combos":      defaultdict(int),
    }
    for field in TOTAL_FIELDS:
        e14c_merged["field_zero"][field] = (
            cross["e14c"]["field_zero"][field] + cnn["field_zero"][field]
        )
    for combo, cnt in cross["e14c"]["combos"].items():
        e14c_merged["combos"][combo] += cnt
    for combo, cnt in cnn["combos"].items():
        e14c_merged["combos"][combo] += cnt

    sources_to_print = {
        "E14C (cross-val only)": (cross["e14c"], cross["e14c"]["has_data"]),
        "E14C (_cnn.jsonl complement)": (cnn, cnn["has_data"]),
        "E14C TOTAL (merged)": (e14c_merged, e14c_merged["has_data"]),
        "E14D": (cross["e14d"], cross["e14d"]["has_data"]),
        "E14T": (cross["e14t"], cross["e14t"]["has_data"]),
    }

    if args.source != "all":
        key = f"E14C TOTAL (merged)" if args.source == "e14c" else args.source.upper()
        sources_to_print = {k: v for k, v in sources_to_print.items() if args.source.upper() in k.upper()}

    for label, (s, n) in sources_to_print.items():
        print_stats(label, s, n)

    if args.csv:
        print("\n\nCSV:")
        print("fuente,mesas,al_menos_1,al_menos_1_pct,VOTANTES,VOTANTES_pct,URNA,URNA_pct,SUMA_TOTAL,SUMA_TOTAL_pct,todos_3,todos_3_pct")
        for label, (s, n) in sources_to_print.items():
            if n == 0:
                continue
            row = [
                label, n,
                s["at_least_one"], f"{s['at_least_one']/n*100:.2f}",
                s["field_zero"]["VOTANTES"], f"{s['field_zero']['VOTANTES']/n*100:.2f}",
                s["field_zero"]["URNA"], f"{s['field_zero']['URNA']/n*100:.2f}",
                s["field_zero"]["SUMA_TOTAL"], f"{s['field_zero']['SUMA_TOTAL']/n*100:.2f}",
                s["all_three"], f"{s['all_three']/n*100:.2f}",
            ]
            print(",".join(str(x) for x in row))


if __name__ == "__main__":
    main()
