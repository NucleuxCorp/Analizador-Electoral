"""
analyze_all_depts.py — Run CNN analysis on all downloaded departments.

Skips departments already analyzed (output file exists and has data).
Processes smallest to largest to get results quickly.

Usage:
    python analyze_all_depts.py
    python analyze_all_depts.py --max-pdfs 3000   # skip departments over N PDFs
    python analyze_all_depts.py --dept ARAUCA      # single department
"""
import argparse
import subprocess
import sys
from pathlib import Path

PDFS_ROOT = Path("data/pdfs")
OUTPUT_DIR = Path("data")


def count_pdfs(dept_dir: Path) -> int:
    return sum(1 for _ in dept_dir.rglob("*.pdf"))


def output_path(dept_name: str) -> Path:
    safe = dept_name.lower().replace(" ", "_").replace(".", "")
    return OUTPUT_DIR / f"{safe}_cnn.jsonl"


def already_done(dept_name: str) -> bool:
    out = output_path(dept_name)
    return out.exists() and out.stat().st_size > 100


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-pdfs", type=int, default=0, help="Skip depts with more than N PDFs (0 = no limit)")
    parser.add_argument("--dept", help="Process only this department")
    args = parser.parse_args()

    depts = sorted(
        [(count_pdfs(d), d.name, d) for d in PDFS_ROOT.iterdir() if d.is_dir()],
        key=lambda x: x[0],
    )

    if args.dept:
        depts = [(c, n, p) for c, n, p in depts if n.lower() == args.dept.lower()]
        if not depts:
            print(f"Department not found: {args.dept}")
            sys.exit(1)

    print(f"{'Department':<25} {'PDFs':>6}  {'Status'}")
    print("-" * 50)
    to_run = []
    for count, name, path in depts:
        if already_done(name):
            print(f"{name:<25} {count:>6}  DONE")
            continue
        if args.max_pdfs and count > args.max_pdfs:
            print(f"{name:<25} {count:>6}  SKIPPED (>{args.max_pdfs})")
            continue
        print(f"{name:<25} {count:>6}  PENDING")
        to_run.append((count, name, path))

    if not to_run:
        print("\nAll departments already analyzed.")
        return

    print(f"\nWill analyze {len(to_run)} departments.\n")

    for i, (count, name, dept_path) in enumerate(to_run, 1):
        out = output_path(name)
        print(f"[{i}/{len(to_run)}] {name} ({count} PDFs) -> {out}")
        result = subprocess.run(
            [sys.executable, "main.py", "analyze-e14c",
             "--dir", str(dept_path),
             "--output", str(out)],
            capture_output=False,
        )
        if result.returncode != 0:
            print(f"  ERROR: exit code {result.returncode}")
        else:
            lines = sum(1 for _ in open(out, encoding="utf-8")) if out.exists() else 0
            suspicious = sum(
                1 for line in open(out, encoding="utf-8")
                if '"needs_review": true' in line or '"is_suspicious": true' in line
            ) if out.exists() else 0
            print(f"  Done: {lines} actas, {suspicious} suspicious\n")


if __name__ == "__main__":
    main()
