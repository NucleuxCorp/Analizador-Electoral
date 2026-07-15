"""
audit_transversal_blanks.py

Audit E14C conflictivas index for false-positive blanks vs real blank uncertainty.

Usage:
    python scripts/audit_transversal_blanks.py
    python scripts/audit_transversal_blanks.py --index path/to/index.jsonl --out data/audit_transversal_blanks.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from src.modules.review.blank_audit import (
    FIELD_BUCKET_LABELS,
    MESA_BUCKET_LABELS,
    aggregate_audit,
    classify_mesa,
)
from src.modules.review.datasets import lab_dataset_dir, normalize_dataset
from src.modules.review.field_audit import mesa_key as _mesa_key
from src.modules.review.queue import iter_cross_rows

DATA_DIR = ROOT / "data"


def _load_index(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _load_cross_by_keys(keys: set[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in iter_cross_rows(DATA_DIR):
        mk = _mesa_key(row)
        if mk in keys:
            out[mk] = row
    return out


def _print_summary(summary: dict) -> None:
    n = summary["mesas_total"]
    print(f"\n=== Auditoría transversal blancos ({n:,} mesas) ===\n")

    print("--- Por mesa (filtro cola blancos) ---")
    for key, label in MESA_BUCKET_LABELS.items():
        c = summary["mesa_buckets"].get(key, 0)
        pct = 100.0 * c / n if n else 0
        print(f"  {label}: {c:,} ({pct:.1f}%)")

    print("\n--- Banderas mesa ---")
    mf = summary["mesa_flags"]
    for k, c in mf.items():
        pct = 100.0 * c / n if n else 0
        print(f"  {k}: {c:,} ({pct:.1f}%)")

    print("\n--- Por campo × 3 slots (VOTANTES/URNA/SUMA × mesas) ---")
    for key, label in FIELD_BUCKET_LABELS.items():
        c = summary["field_slot_counts"].get(key, 0)
        slots = n * 3
        pct = 100.0 * c / slots if slots else 0
        print(f"  {label}: {c:,} slots ({pct:.1f}% de {slots:,})")

    print("\n--- AUTO falso por campo ---")
    for field, c in summary["auto_false_positive_by_field"].items():
        print(f"  {field}: {c:,} mesas con al menos un slot AUTO falso en ese campo")

    print("\n--- Partial dígito por campo (E14C class partial) ---")
    for field, c in summary["partial_digit_by_field"].items():
        print(f"  {field}: {c:,}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit transversal blank false positives")
    parser.add_argument("--dataset", default="E14C_conflictivas")
    parser.add_argument("--index", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "audit_transversal_blanks.jsonl")
    parser.add_argument("--summary-json", type=Path, default=ROOT / "data" / "audit_transversal_blanks_summary.json")
    parser.add_argument("--examples", type=int, default=5, help="Print sample mesa_keys per bucket")
    args = parser.parse_args()

    index_path = args.index
    if index_path is None:
        index_path = lab_dataset_dir(normalize_dataset(args.dataset)) / "index.jsonl"
    if not index_path.is_file():
        raise SystemExit(f"Index not found: {index_path}")

    index_rows = _load_index(index_path)
    keys = {r["mesa_key"] for r in index_rows}
    cross_by_key = _load_cross_by_keys(keys)
    missing_cross = len(keys) - len(cross_by_key)

    classified = [
        classify_mesa(row, cross_by_key.get(row["mesa_key"]))
        for row in index_rows
    ]
    summary = aggregate_audit(classified)
    summary["index_path"] = str(index_path)
    summary["cross_rows_missing"] = missing_cross

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        for row in classified:
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")

    args.summary_json.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"index: {index_path} ({len(index_rows):,} mesas)")
    print(f"cross match: {len(cross_by_key):,} (missing {missing_cross})")
    _print_summary(summary)
    print(f"\nWrote: {args.out}")
    print(f"Wrote: {args.summary_json}")

    if args.examples:
        print(f"\n--- Ejemplos (hasta {args.examples} por bucket) ---")
        by_bucket: dict[str, list[str]] = {}
        for row in classified:
            b = row["mesa_bucket"]
            by_bucket.setdefault(b, [])
            if len(by_bucket[b]) < args.examples:
                by_bucket[b].append(row["mesa_key"])
        for key in MESA_BUCKET_LABELS:
            samples = by_bucket.get(key, [])
            if samples:
                print(f"  {key}: {', '.join(samples)}")


if __name__ == "__main__":
    main()