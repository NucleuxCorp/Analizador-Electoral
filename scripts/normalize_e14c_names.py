"""
normalize_e14c_names.py — Rename E14_PRE_*.pdf files to add dept_mpio_zona_puesto_ prefix.

Only renames files that start with E14_PRE (no prefix yet).
Skips dif_evidence folders.
Skips if target name already exists (conflict — logs it).
"""
from __future__ import annotations

import os
from pathlib import Path

E14C_ROOT = Path("E:/Nucleux/tools/Analizador de Elecciones/data/e14_segunda/E14C")


def derive_prefix(filename: str) -> str:
    # E14_PRE_{dept}_{mpio3}_{zona3}_{zona2}_{puesto}_{mesa}_{ts}.pdf
    stem = filename.replace(".pdf", "")
    parts = stem.split("_")
    dept   = parts[2]
    mpio   = parts[3]
    zona2  = parts[5]
    puesto = parts[6]
    return f"{dept}_{mpio}_{zona2}_{puesto}_"


def main():
    renamed = 0
    skipped_conflict = 0
    errors = 0

    for root, dirs, files in os.walk(E14C_ROOT):
        if os.path.basename(root) == "dif_evidence":
            continue

        for f in files:
            if not (f.startswith("E14_PRE") and f.endswith(".pdf")):
                continue

            try:
                prefix = derive_prefix(f)
                new_name = prefix + f
                src = os.path.join(root, f)
                dst = os.path.join(root, new_name)

                if os.path.exists(dst):
                    skipped_conflict += 1
                    print(f"CONFLICT: {dst}")
                    continue

                os.rename(src, dst)
                renamed += 1

                if renamed % 5000 == 0:
                    print(f"  Renombrados: {renamed} | conflictos: {skipped_conflict}", flush=True)

            except Exception as e:
                errors += 1
                print(f"ERROR {f}: {e}")

    print(f"\n=== LISTO ===")
    print(f"  Renombrados:  {renamed:,}")
    print(f"  Conflictos:   {skipped_conflict}")
    print(f"  Errores:      {errors}")


if __name__ == "__main__":
    main()
