#!/usr/bin/env python3
"""export_clean_mesas — Export mesas that passed all cross-validation filters to CSV.

A mesa is 'clean' when compute_overall_status(raw_row) == 'clean':
  - sources_ok_count >= 2
  - no cross_discrepancy
  - all arith_deltas == 0
  - no tachon_suspicious
  - no URNA=0 with votes>0

Usage:
    python scripts/export_clean_mesas.py [--data-dir PATH] [--output PATH]
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.upload.upload_mesa_results import compute_overall_status, discover_files, _SOURCES

DATA_DIR = REPO / "data"
DEFAULT_OUTPUT = DATA_DIR / "clean_mesas.csv"

CSV_COLUMNS = [
    "mesa_key",
    "dept",
    "mpio",
    "zona",
    "puesto",
    "mesa",
    "sources_ok_count",
    "e14c_arith_delta",
    "e14t_arith_delta",
    "e14d_arith_delta",
    "cross_discrepancy",
    "tachon_suspicious",
]


def _row_to_csv(raw: dict) -> dict:
    dept = raw.get("dept", "")
    mpio = raw.get("mpio", "")
    zona = raw.get("zona", "")
    puesto = raw.get("puesto", "")
    mesa = raw.get("mesa", "")

    aritmetica = raw.get("aritmetica") or {}
    tachones = raw.get("tachones") or {}
    cong_summary = (raw.get("congruencia") or {}).get("summary") or {}
    sources_ok = (raw.get("congruencia") or {}).get("sources_ok", 0)

    def _delta(src: str):
        a = aritmetica.get(src)
        return a.get("delta") if a else None

    tachon_suspicious = any(
        bool((tachones.get(k) or {}).get("suspicious")) for k in _SOURCES
    )

    return {
        "mesa_key": f"{dept}_{mpio}_{zona}_{puesto}_{mesa}",
        "dept": dept,
        "mpio": mpio,
        "zona": zona,
        "puesto": puesto,
        "mesa": mesa,
        "sources_ok_count": sources_ok,
        "e14c_arith_delta": _delta("e14c"),
        "e14t_arith_delta": _delta("e14t"),
        "e14d_arith_delta": _delta("e14d"),
        "cross_discrepancy": bool(cong_summary.get("cross_discrepancy")),
        "tachon_suspicious": tachon_suspicious,
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Export clean mesas to CSV")
    parser.add_argument("--data-dir", default=str(DATA_DIR))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    output = Path(args.output)
    files = discover_files(data_dir)

    if not files:
        print(f"No cross_mesa_validation_DD.jsonl files found in {data_dir}")
        sys.exit(1)

    print(f"Found {len(files)} department files — scanning for clean mesas...")

    total = 0
    clean = 0
    by_status: dict[str, int] = {}

    with output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()

        for f in files:
            with f.open(encoding="utf-8") as jf:
                for line in jf:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    total += 1
                    status = compute_overall_status(raw)
                    by_status[status] = by_status.get(status, 0) + 1

                    if status == "clean":
                        writer.writerow(_row_to_csv(raw))
                        clean += 1

    print(f"\nTotal mesas processed : {total:,}")
    print(f"Clean mesas exported  : {clean:,}")
    print(f"\nBreakdown by status:")
    for s, n in sorted(by_status.items(), key=lambda x: -x[1]):
        print(f"  {s:<30} {n:>8,}")
    print(f"\nOutput: {output}")


if __name__ == "__main__":
    main()
