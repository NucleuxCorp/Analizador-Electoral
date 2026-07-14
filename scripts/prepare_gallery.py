"""
scripts/prepare_gallery.py

Pipeline to prepare gallery for any E14 analysis set:
1. Find target mesas from cross_mesa_validation files
2. Map mesa keys to PDF files in e14_segunda/{E14T,E14D,E14C}
3. Convert PDFs to JPEGs in gallery mesas/ directory
4. Write manifest.json

Usage:
    python scripts/prepare_gallery.py --set E14T_3_totales_blancos
    python scripts/prepare_gallery.py --set E14T_3_totales_blancos --dry-run
    python scripts/prepare_gallery.py --set E14T_3_totales_blancos --workers 4
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import fitz  # PyMuPDF

# E14C-only helper: is_confirmed_blank checks whether a 3-element array is all-None.
# SCHEMA ASYMMETRY NOTE: E14C fields are 3-element arrays; E14T fields are scalars.
# Applying is_confirmed_blank or derive_scalar to E14T fields would be a defect.
from debug_sv.field_array import is_confirmed_blank

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
E14_BASE = Path(r"E:\Nucleux\tools\Analizador de Elecciones\Data\e14_segunda")
LAB_BASE = ROOT / "Laboratorio/analisis_transversal/analisis_visual"
SOURCES = ("e14t", "e14d", "e14c")

JPEG_DPI = 150  # balance quality vs file size for gallery images


# ---------------------------------------------------------------------------
# Set definitions — add new sets here
# ---------------------------------------------------------------------------
SET_CONFIGS = {
    "E14T_3_totales_blancos": {
        "title": "E14T — 3 totales en blanco (segunda vuelta)",
        "filter": "e14t_3_blanks",
        "sources": ("e14t", "e14d", "e14c"),
    },
    "E14C_3_totales_blancos": {
        "title": "E14C — 3 totales en blanco (segunda vuelta)",
        "filter": "e14c_3_blanks",
        "sources": ("e14t", "e14d", "e14c"),
    },
}

FILTER_FUNCS = {}

# Canonical file pattern — excludes .bak_* backups
_CROSS_FILE_RE = re.compile(r"^cross_mesa_validation_\d{2}\.jsonl$")


def filter_e14t_3_blanks(row: dict) -> bool:
    """E14T status=ok, VOTANTES=URNA=SUMA_TOTAL=0 (strict), and C1+C2>0.

    SCHEMA NOTE: E14T fields are scalars (integers). Do NOT apply is_confirmed_blank
    or derive_scalar here — those helpers are for the E14C array schema only.
    This function is intentionally left untouched by the E14C ADR-2 migration.
    """
    e14t = row.get("sources", {}).get("e14t", {})
    if e14t.get("status") != "ok":
        return False
    fields = e14t.get("fields", {})
    if fields.get("VOTANTES") != 0 or fields.get("URNA") != 0 or fields.get("SUMA_TOTAL") != 0:
        return False
    c1 = fields.get("C1_CEPEDA") or 0
    c2 = fields.get("C2_ABELARDO") or 0
    return (c1 + c2) > 0


def filter_e14c_3_blanks(row: dict) -> bool:
    """E14C status=ok, VOTANTES=URNA=SUMA_TOTAL all confirmed blank (array schema).

    SCHEMA NOTE: E14C fields are 3-element digit arrays (post ADR-2 migration).
    is_confirmed_blank([None,None,None]) returns True only when cell_has_ink()
    returned False for all subcells — the jury wrote nothing in that cell.
    This is semantically equivalent to the old scalar==0 check, but correct:
    the old scalar=0 was a fabricated value from the contamination bug.

    Do NOT apply is_confirmed_blank to E14T fields — those remain scalars.
    """
    e14c = row.get("sources", {}).get("e14c", {})
    if e14c.get("status") != "ok":
        return False
    f = e14c.get("fields", {})
    return (
        is_confirmed_blank(f.get("VOTANTES"))
        and is_confirmed_blank(f.get("URNA"))
        and is_confirmed_blank(f.get("SUMA_TOTAL"))
    )


FILTER_FUNCS["e14t_3_blanks"] = filter_e14t_3_blanks
FILTER_FUNCS["e14c_3_blanks"] = filter_e14c_3_blanks


# ---------------------------------------------------------------------------
# Reference data loaders
# ---------------------------------------------------------------------------

def load_dept_map() -> dict[str, str]:
    """dept_code -> folder_name (from departamentos.json)."""
    with open(DATA_DIR / "departamentos.json", encoding="utf-8") as f:
        depts = json.load(f)
    mapping = {}
    for d in depts:
        name = d["nombre"].rstrip(".")  # BOGOTA D.C. -> BOGOTA D.C
        mapping[d["id"]] = name
    return mapping


def load_mpio_map() -> dict[tuple[str, str], str]:
    """(dept_code, mpio_code) -> folder_name (from divipole.json)."""
    with open(DATA_DIR / "divipole.json", encoding="utf-8") as f:
        div = json.load(f)
    mapping = {}
    for dept_code, dept_data in div.get("departamentos", {}).items():
        for mpio_code, mpio_data in dept_data.get("municipios", {}).items():
            mapping[(dept_code, mpio_code)] = mpio_data["nombre"]
    return mapping


def load_e14t_url_map() -> dict[tuple[str, str, str, str, str], str]:
    """(dept, mpio, zona, puesto, mesa) -> hash."""
    url_map = {}
    hash_re = re.compile(r"/([0-9a-f]{64})\.pdf")
    with open(DATA_DIR / "e14t_sv_urls.jsonl", encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            m = hash_re.search(row.get("pdf_url", ""))
            if m:
                key = (
                    row["cod_dept"],
                    row["cod_mpio"],
                    row["zona"],
                    row["puesto"],
                    row["mesa"],
                )
                url_map[key] = m.group(1)
    return url_map


# ---------------------------------------------------------------------------
# Target mesa discovery
# ---------------------------------------------------------------------------

def find_target_mesas(filter_fn) -> list[dict]:
    """Read all cross_mesa_validation_*.jsonl and return matching rows (deduped by mesa_key)."""
    seen: set[str] = set()
    results = []
    for path in sorted(p for p in DATA_DIR.iterdir() if _CROSS_FILE_RE.match(p.name)):
        with open(path, encoding="utf-8") as f:
            for line in f:
                row = json.loads(line)
                key = f"{row['dept']}_{row['mpio']}_{row['zona']}_{row['puesto']}_{row['mesa']}"
                if key in seen:
                    continue
                if filter_fn(row):
                    seen.add(key)
                    results.append(row)
    return results


# ---------------------------------------------------------------------------
# PDF path resolvers
# ---------------------------------------------------------------------------

def _dept_folder(dept_code: str, dept_map: dict) -> str | None:
    return dept_map.get(dept_code)


def _mpio_folder(dept_code: str, mpio_code: str, mpio_map: dict) -> str | None:
    return mpio_map.get((dept_code, mpio_code))


def find_e14t_pdf(
    row: dict, dept_map: dict, mpio_map: dict, url_map: dict
) -> Path | None:
    dept = row["dept"]
    mpio = row["mpio"]
    zona = row["zona"]
    puesto = row["puesto"]
    mesa = row["mesa"]

    dept_name = _dept_folder(dept, dept_map)
    mpio_name = _mpio_folder(dept, mpio, mpio_map)
    if not dept_name or not mpio_name:
        return None

    pdf_hash = url_map.get((dept, mpio, zona, puesto, mesa))
    if not pdf_hash:
        return None

    path = E14_BASE / "E14T" / dept_name / mpio_name / f"zona_{zona}" / f"puesto_{puesto}" / f"{pdf_hash}.pdf"
    return path if path.exists() else None


def find_e14d_pdf(
    row: dict, dept_map: dict, mpio_map: dict, url_map: dict
) -> Path | None:
    # E14D uses same hash and same path structure as E14T
    dept = row["dept"]
    mpio = row["mpio"]
    zona = row["zona"]
    puesto = row["puesto"]
    mesa = row["mesa"]

    dept_name = _dept_folder(dept, dept_map)
    mpio_name = _mpio_folder(dept, mpio, mpio_map)
    if not dept_name or not mpio_name:
        return None

    pdf_hash = url_map.get((dept, mpio, zona, puesto, mesa))
    if not pdf_hash:
        return None

    path = E14_BASE / "E14D" / dept_name / mpio_name / f"zona_{zona}" / f"puesto_{puesto}" / f"{pdf_hash}.pdf"
    return path if path.exists() else None


def find_e14c_pdf(
    row: dict, dept_map: dict, mpio_map: dict
) -> Path | None:
    dept = row["dept"]
    mpio = row["mpio"]
    zona = row["zona"]
    puesto = row["puesto"]
    mesa = row["mesa"]

    dept_name = _dept_folder(dept, dept_map)
    mpio_name = _mpio_folder(dept, mpio, mpio_map)
    if not dept_name or not mpio_name:
        return None

    zona_2 = f"{int(zona):02d}"
    folder = E14_BASE / "E14C" / dept_name / mpio_name / f"zona_{zona_2}" / f"puesto_{puesto}"
    if not folder.exists():
        return None

    # Find file matching this mesa number
    target_mesa_padded = mesa  # already zero-padded from cross_mesa_validation
    for fname in folder.iterdir():
        if fname.suffix.lower() != ".pdf":
            continue
        parts = fname.stem.split("_")
        if len(parts) >= 2 and parts[-2] == target_mesa_padded:
            return fname
    return None


# ---------------------------------------------------------------------------
# PDF → JPEG conversion
# ---------------------------------------------------------------------------

def pdf_to_jpegs(pdf_path: Path, out_dir: Path, prefix: str) -> list[Path]:
    """Convert each page of pdf_path to {prefix}_p{N:02d}.jpg in out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    doc = fitz.open(str(pdf_path))
    n = len(doc)
    mat = fitz.Matrix(JPEG_DPI / 72, JPEG_DPI / 72)
    for i in range(n):
        page = doc[i]
        pix = page.get_pixmap(matrix=mat, alpha=False)
        out_path = out_dir / f"{prefix}_p{i+1:02d}.jpg"
        pix.save(str(out_path))
        outputs.append(out_path)
    doc.close()
    return outputs


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def prepare_mesa(
    row: dict,
    mesas_dir: Path,
    dept_map: dict,
    mpio_map: dict,
    url_map: dict,
    dry_run: bool,
) -> dict:
    dept, mpio, zona, puesto, mesa = row["dept"], row["mpio"], row["zona"], row["puesto"], row["mesa"]
    mesa_key = f"{dept}_{mpio}_{zona}_{puesto}_{mesa}"
    mesa_dir = mesas_dir / mesa_key

    result = {"key": mesa_key, "sources": {}}

    pdf_finders = {
        "e14t": lambda: find_e14t_pdf(row, dept_map, mpio_map, url_map),
        "e14d": lambda: find_e14d_pdf(row, dept_map, mpio_map, url_map),
        "e14c": lambda: find_e14c_pdf(row, dept_map, mpio_map),
    }

    for src, finder in pdf_finders.items():
        pdf_path = finder()
        if pdf_path is None:
            result["sources"][src] = "missing"
            continue

        if dry_run:
            result["sources"][src] = str(pdf_path)
            continue

        # Check if already converted
        existing = sorted(mesa_dir.glob(f"{src}_p*.jpg"))
        if existing:
            result["sources"][src] = f"already_done ({len(existing)} pages)"
            continue

        pages = pdf_to_jpegs(pdf_path, mesa_dir, src)
        result["sources"][src] = f"converted ({len(pages)} pages)"

    return result


def run(set_name: str, dry_run: bool, workers: int) -> None:
    if set_name not in SET_CONFIGS:
        print(f"Unknown set: {set_name}. Available: {list(SET_CONFIGS.keys())}")
        sys.exit(1)

    cfg = SET_CONFIGS[set_name]
    filter_fn = FILTER_FUNCS[cfg["filter"]]

    out_dir = LAB_BASE / set_name
    mesas_dir = out_dir / "mesas"
    manifest_path = out_dir / "manifest.json"

    print(f"[prepare_gallery] Set: {set_name}")
    print(f"[prepare_gallery] Output: {out_dir}")

    # Load reference data
    print("[prepare_gallery] Loading reference data...")
    dept_map = load_dept_map()
    mpio_map = load_mpio_map()
    url_map = load_e14t_url_map()
    print(f"  depts: {len(dept_map)}, mpios: {len(mpio_map)}, e14t urls: {len(url_map)}")

    # Find target mesas
    print("[prepare_gallery] Scanning cross_mesa_validation files...")
    rows = find_target_mesas(filter_fn)
    print(f"  Found {len(rows)} target mesas")

    if dry_run:
        print("[prepare_gallery] DRY RUN — no files will be written")

    # Process mesas
    if not dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)
        mesas_dir.mkdir(parents=True, exist_ok=True)

    results = []
    missing_count = {"e14t": 0, "e14d": 0, "e14c": 0}

    def do_mesa(row):
        return prepare_mesa(row, mesas_dir, dept_map, mpio_map, url_map, dry_run)

    if workers > 1 and not dry_run:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(do_mesa, row): row for row in rows}
            for i, fut in enumerate(as_completed(futures), 1):
                r = fut.result()
                results.append(r)
                for src in SOURCES:
                    if r["sources"].get(src) == "missing":
                        missing_count[src] += 1
                if i % 10 == 0:
                    print(f"  [{i}/{len(rows)}] processed...")
    else:
        for i, row in enumerate(rows, 1):
            r = do_mesa(row)
            results.append(r)
            for src in SOURCES:
                if r["sources"].get(src) == "missing":
                    missing_count[src] += 1
            if i % 10 == 0 or dry_run:
                status = " | ".join(f"{s}: {r['sources'].get(s, '?')}" for s in SOURCES)
                print(f"  [{i:3d}/{len(rows)}] {r['key']} — {status}")

    print(f"\n[prepare_gallery] Done. Missing PDFs: {missing_count}")

    # Write manifest
    manifest = []
    for row in rows:
        dept, mpio, zona, puesto, mesa = row["dept"], row["mpio"], row["zona"], row["puesto"], row["mesa"]
        manifest.append({"dept": dept, "mpio": mpio, "zona": zona, "puesto": puesto, "mesa": mesa})

    if not dry_run:
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
        print(f"[prepare_gallery] Wrote manifest: {manifest_path} ({len(manifest)} entries)")
    else:
        print(f"\n[prepare_gallery] Would write manifest with {len(manifest)} entries")
        print("Sample entries:")
        for entry in manifest[:5]:
            print(f"  {entry}")


def main():
    parser = argparse.ArgumentParser(description="Prepare gallery mesas from cross_mesa_validation")
    parser.add_argument("--set", default="E14T_3_totales_blancos", help="Gallery set name")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without writing files")
    parser.add_argument("--workers", type=int, default=1, help="Parallel workers for PDF conversion")
    args = parser.parse_args()
    run(args.set, args.dry_run, args.workers)


if __name__ == "__main__":
    main()
