#!/usr/bin/env python3
"""Organize flat E14T/E14D PDFs into dept/mpio/zona/puesto hierarchy."""

import json
import os
import sys
from pathlib import Path
from datetime import datetime


DATA_DIR = Path("D:/Nucleux/tools/Analizador de Elecciones/data")
BASE_DIR = Path("E:/e14_segunda")


def load_dept_names():
    with open(DATA_DIR / "departamentos.json", "r", encoding="utf-8") as f:
        raw = json.load(f)
    return {str(item["id"]): item["nombre"] for item in raw}


def load_mpio_names():
    with open(DATA_DIR / "divipole.json", "r", encoding="utf-8") as f:
        raw = json.load(f)
    mpio_map = {}
    for dept_code, dept in raw["departamentos"].items():
        for mpio_code, mpio in dept["municipios"].items():
            key = (str(dept_code), str(mpio_code))
            mpio_map[key] = mpio["nombre"]
    return mpio_map


def safe_name(name):
    """Replace filesystem-unfriendly characters."""
    unsafe = '<>:"/\\|?*'
    for ch in unsafe:
        name = name.replace(ch, "_")
    return name.strip()


def organize(source_name, jsonl_name, dept_names, mpio_names):
    source_root = BASE_DIR / source_name
    jsonl_path = DATA_DIR / jsonl_name

    moved = 0
    missing = 0
    errors = 0
    last_log = datetime.now()
    report_interval = 10_000

    # Pre-count for progress
    total = 0
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for _ in f:
            total += 1

    print(f"[{source_name}] {total:,} entries to process from {jsonl_path}")
    print(f"[{source_name}] Source dir: {source_root}")

    missing_samples = []
    error_samples = []

    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                entry = json.loads(line)
                cod_dept = str(entry["cod_dept"])
                cod_mpio = str(entry["cod_mpio"])
                zona = str(entry["zona"])
                puesto = str(entry["puesto"])
                pdf_url = entry["pdf_url"]
                hash_name = pdf_url.split("/")[-1].split("?")[0]

                dept_name = safe_name(dept_names.get(cod_dept, f"DEPT_{cod_dept}"))
                mpio_name = safe_name(mpio_names.get((cod_dept, cod_mpio), f"MPIO_{cod_mpio}"))

                dest_dir = (
                    source_root
                    / dept_name
                    / mpio_name
                    / f"zona_{zona}"
                    / f"puesto_{puesto}"
                )
                dest_dir.mkdir(parents=True, exist_ok=True)

                src = source_root / hash_name
                dst = dest_dir / hash_name

                if not src.exists():
                    missing += 1
                    if len(missing_samples) < 5:
                        missing_samples.append(str(src))
                    continue

                os.rename(str(src), str(dst))
                moved += 1

                if moved % report_interval == 0:
                    now = datetime.now()
                    elapsed = (now - last_log).total_seconds()
                    print(
                        f"[{source_name}] Moved {moved:,}/{total:,} | "
                        f"missing {missing:,} | errors {errors:,} | "
                        f"last batch {elapsed:.1f}s"
                    )
                    last_log = now

            except Exception as exc:
                errors += 1
                if len(error_samples) < 5:
                    error_samples.append(f"{exc} | line: {line[:120].strip()}")

    print(f"[{source_name}] DONE: moved {moved:,}, missing {missing:,}, errors {errors:,}")
    if missing_samples:
        print(f"[{source_name}] Missing samples:")
        for s in missing_samples:
            print(f"  - {s}")
    if error_samples:
        print(f"[{source_name}] Error samples:")
        for s in error_samples:
            print(f"  - {s}")

    return {"moved": moved, "missing": missing, "errors": errors, "total": total}


def show_tree(root, max_depth=4, max_files=3):
    print(f"\nSample structure from {root}:")
    for dirpath, dirnames, filenames in os.walk(root):
        depth = dirpath.replace(str(root), "").count(os.sep)
        if depth > max_depth:
            del dirnames[:]
            continue
        indent = "  " * depth
        print(f"{indent}{Path(dirpath).name}/")
        for f in sorted(filenames)[:max_files]:
            print(f"{indent}  {f}")
        if len(filenames) > max_files:
            print(f"{indent}  ... ({len(filenames) - max_files} more files)")


def main():
    dept_names = load_dept_names()
    mpio_names = load_mpio_names()
    print(f"Loaded {len(dept_names)} departments, {len(mpio_names)} municipalities")

    results = {}
    results["E14T"] = organize("E14T", "e14t_sv_urls.jsonl", dept_names, mpio_names)
    results["E14D"] = organize("E14D", "e14d_sv_urls.jsonl", dept_names, mpio_names)

    print("\n=== FINAL SUMMARY ===")
    for label, r in results.items():
        print(
            f"{label}: total={r['total']:,}, moved={r['moved']:,}, "
            f"missing={r['missing']:,}, errors={r['errors']:,}"
        )

    show_tree(BASE_DIR / "E14T", max_depth=4, max_files=3)


if __name__ == "__main__":
    main()
