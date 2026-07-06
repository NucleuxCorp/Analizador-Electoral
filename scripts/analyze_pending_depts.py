"""
analyze_pending_depts.py

Run analyze-e14c for all departments where E14C OCR is missing or partial.
PDFs live on the external drive E:/. Results go to data/analysis_e14c_{code}.jsonl.

Usage:
    python scripts/analyze_pending_depts.py
    python scripts/analyze_pending_depts.py --dry-run       # show commands only
    python scripts/analyze_pending_depts.py --dept CALDAS   # single department
    python scripts/analyze_pending_depts.py --engine segmented  # OCR engine (default)
"""

import argparse
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
E14C_BASE = Path("E:/Nucleux/tools/Analizador de Elecciones/Data/e14_segunda/E14C")
BACKUP_DIR = PROJECT_ROOT / "data" / "backups" / "analysis_e14c"

# Departments with missing or partial E14C OCR (as of 2026-07-06)
PENDING = [
    ("BOLIVAR",        "05", "bolivar"),
    ("CALDAS",         "09", "caldas"),
    ("CAQUETA",        "11", "caqueta"),
    ("CAUCA",          "12", "cauca"),
    ("CESAR",          "13", "cesar"),
    ("CUNDINAMARCA",   "15", "cundinamarca"),
    ("VALLE",          "31", "valle"),
    ("VICHADA",        "68", "vichada"),
    ("VAUPES",         "72", "vaupes"),
    ("AMAZONAS",       "88", "amazonas"),
]


def find_existing(dept_slug: str, dept_code: str) -> Path | None:
    """Return path to existing results file, checking both naming conventions."""
    candidates = [
        PROJECT_ROOT / "data" / f"{dept_slug}_cnn.jsonl",
        PROJECT_ROOT / "data" / f"analysis_e14c_{dept_code}.jsonl",
    ]
    for p in candidates:
        if p.exists() and p.stat().st_size > 0:
            return p
    return None


def backup(path: Path) -> Path:
    """Copy file to backup dir with timestamp suffix. Returns backup path."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"{path.stem}_{ts}{path.suffix}"
    shutil.copy2(path, dest)
    return dest


def run_dept(dept_name: str, dept_code: str, dept_slug: str, engine: str, dry_run: bool, force: bool) -> bool:
    pdf_dir = E14C_BASE / dept_name
    if not pdf_dir.exists():
        print(f"  [SKIP] {dept_name}: directorio no encontrado en {pdf_dir}")
        return False

    pdf_count = len(list(pdf_dir.rglob("*.pdf")))
    if pdf_count == 0:
        print(f"  [SKIP] {dept_name}: sin PDFs en {pdf_dir}")
        return False

    existing = find_existing(dept_slug, dept_code)
    output = PROJECT_ROOT / "data" / f"analysis_e14c_{dept_code}.jsonl"

    print(f"\n{'='*60}")
    print(f"  Dept     : {dept_name} ({dept_code})")
    print(f"  PDFs     : {pdf_count:,}")

    if existing:
        lines = sum(1 for _ in open(existing))
        size = existing.stat().st_size / 1024 / 1024
        print(f"  Existente: {existing.name}  ({lines:,} líneas, {size:.1f} MB)")
        if not force:
            print(f"  [SKIP] Ya procesado — usar --force para re-analizar")
            return True
        # Backup before overwriting
        bak = backup(existing)
        print(f"  [BACKUP] -> {bak.name}")

    print(f"  Out      : {output.name}")
    cmd = [
        sys.executable, "main.py", "analyze-e14c",
        "--dir", str(pdf_dir),
        "--output", str(output),
        "--engine", engine,
    ]
    print(f"  Cmd      : {' '.join(cmd)}")

    if dry_run:
        print("  [DRY RUN] saltando ejecucion")
        return True

    start = time.time()
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    elapsed = time.time() - start
    mins, secs = int(elapsed // 60), int(elapsed % 60)

    if result.returncode == 0:
        size = output.stat().st_size / 1024 / 1024 if output.exists() else 0
        print(f"  [OK] completado en {mins}m {secs}s — {size:.1f} MB")
        return True
    else:
        print(f"  [ERROR] exit code {result.returncode} — {mins}m {secs}s")
        return False


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Re-analizar aunque ya existan resultados (crea backup)")
    parser.add_argument("--dept", metavar="NAME", help="Procesar solo este departamento (ej: CALDAS)")
    parser.add_argument("--engine", default="segmented",
                        choices=["segmented", "easyocr", "trocr", "vlm"])
    args = parser.parse_args()

    targets = PENDING
    if args.dept:
        targets = [(d, c, s) for d, c, s in PENDING if d.upper() == args.dept.upper()]
        if not targets:
            print(f"Departamento '{args.dept}' no está en la lista de pendientes.")
            print("Pendientes:", [d for d, _, _ in PENDING])
            sys.exit(1)

    print(f"Departamentos a procesar: {len(targets)}")
    if args.dry_run:
        print("Modo DRY RUN — no se ejecuta nada\n")

    ok = failed = skipped = 0
    total_start = time.time()

    for dept_name, dept_code, dept_slug in targets:
        result = run_dept(dept_name, dept_code, dept_slug, args.engine, args.dry_run, args.force)
        if result is True:
            ok += 1
        elif result is False:
            skipped += 1

    total_mins = int((time.time() - total_start) // 60)
    print(f"\n{'='*60}")
    print(f"Completado: {ok} OK | {skipped} saltados | tiempo total: {total_mins}m")


if __name__ == "__main__":
    main()
