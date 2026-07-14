"""
recycle_identical_e14c.py — Move identical (safe) duplicate E14C PDFs to recycle bin.

Only moves E14_PRE_*.pdf files that:
  1. Have a prefixed counterpart in the same folder
  2. Have identical SHA-256 hash to that counterpart

Skips:
  - Unpaired files (no prefixed counterpart → only copy, keep it)
  - Hash-different pairs (content differs → investigate manually)
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

E14C_ROOT = Path("E:/Nucleux/tools/Analizador de Elecciones/data/e14_segunda/E14C")
BATCH_SIZE = 200  # PowerShell recycle bin in batches


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def recycle_batch(paths: list[str]):
    list_file = "D:/tmp_recycle_batch.txt"
    with open(list_file, "w", encoding="utf-8") as fh:
        fh.write("\n".join(p.replace("/", "\\") for p in paths))
    ps = (
        "Add-Type -AssemblyName Microsoft.VisualBasic; "
        "Get-Content 'D:\\tmp_recycle_batch.txt' | ForEach-Object { "
        "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($_, 'OnlyErrorDialogs', 'SendToRecycleBin') }"
    )
    subprocess.run(["powershell", "-Command", ps], timeout=120)
    os.remove(list_file)


def main():
    to_recycle: list[str] = []
    total_identical = 0
    total_skipped_unpaired = 0
    total_skipped_diff = 0
    puestos = 0

    for dept_dir in sorted(E14C_ROOT.iterdir()):
        if not dept_dir.is_dir():
            continue
        for mpio_dir in sorted(dept_dir.iterdir()):
            if not mpio_dir.is_dir():
                continue
            for zona_dir in sorted(mpio_dir.iterdir()):
                if not zona_dir.is_dir():
                    continue
                for puesto_dir in sorted(zona_dir.iterdir()):
                    if not puesto_dir.is_dir():
                        continue

                    pdfs = [f.name for f in os.scandir(puesto_dir) if f.name.endswith(".pdf")]
                    with_prefix = {f for f in pdfs if not f.startswith("E14_PRE")}
                    without_prefix = [f for f in pdfs if f.startswith("E14_PRE")]

                    prefixed_by_key: dict[str, str] = {}
                    for f in with_prefix:
                        idx = f.find("E14_PRE")
                        if idx != -1:
                            prefixed_by_key[f[idx:]] = f

                    for wp_file in without_prefix:
                        prefixed = prefixed_by_key.get(wp_file)
                        if prefixed is None:
                            total_skipped_unpaired += 1
                            continue

                        h1 = sha256(str(puesto_dir / prefixed))
                        h2 = sha256(str(puesto_dir / wp_file))

                        if h1 == h2:
                            to_recycle.append(str(puesto_dir / wp_file))
                            total_identical += 1
                        else:
                            total_skipped_diff += 1
                            print(f"DIFF HASH — {puesto_dir}/{wp_file}")

                        if len(to_recycle) >= BATCH_SIZE:
                            recycle_batch(to_recycle)
                            print(f"  Reciclados: {total_identical} | diff: {total_skipped_diff} | unpaired: {total_skipped_unpaired}", flush=True)
                            to_recycle.clear()

                    puestos += 1
                    if puestos % 1000 == 0:
                        print(f"Puestos: {puestos} | reciclados: {total_identical} | diff: {total_skipped_diff} | unpaired: {total_skipped_unpaired}", flush=True)

    if to_recycle:
        recycle_batch(to_recycle)

    print(f"\n=== LISTO ===")
    print(f"  Mandados a papelera (idénticos): {total_identical}")
    print(f"  Saltados — hash diferente:        {total_skipped_diff}")
    print(f"  Saltados — sin par (única copia): {total_skipped_unpaired}")


if __name__ == "__main__":
    main()
