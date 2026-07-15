"""
scripts/diag_missing_pdfs.py

Diagnostic: classify WHY prepare_gallery finds PDFs "missing" for the
E14D_conflictivas_pendientes and E14T_conflictivas_pendientes datasets.

For each mesa in each dataset's index.jsonl we run the SAME lookup logic
as find_e14t_pdf / find_e14d_pdf in scripts/prepare_gallery.py and classify
the failure reason when the resolved path is None:

    no_dept_name      -> dept_code not in dept_map (departamentos.json)
    no_mpio_name      -> (dept_code, mpio_code) not in mpio_map (divipole.json)
    no_pdf_hash       -> (dept, mpio, zona, puesto, mesa) not in url_map
                         (e14t_sv_urls.jsonl)
    file_not_found    -> path resolved but file does not exist on disk

ASCII-only output (no Unicode arrows / em-dashes) to avoid Windows cp1252
console crashes.

Usage:
    python scripts/diag_missing_pdfs.py
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(r"D:\Nucleux\tools\Analizador de Elecciones")
DATA_DIR = ROOT / "data"
E14_BASE = Path(r"E:\Nucleux\tools\Analizador de Elecciones\Data\e14_segunda")
LAB_BASE = ROOT / "Laboratorio" / "analisis_transversal"

DATASETS = {
    "E14T_conflictivas": LAB_BASE / "E14T_conflictivas_pendientes" / "index.jsonl",
    "E14D_conflictivas": LAB_BASE / "E14D_conflictivas_pendientes" / "index.jsonl",
}

# The reprocess jsonl is checked separately for the presence of *_path fields
# (item 6 of the task).
REPROCESS_FILES = {
    "E14T_conflictivas": LAB_BASE / "E14T_conflictivas_pendientes" / "revalidation_reprocess.jsonl",
    "E14D_conflictivas": LAB_BASE / "E14D_conflictivas_pendientes" / "revalidation_reprocess.jsonl",
}

# Cross-mesa validation files are where prepare_gallery actually sources rows.
CROSS_RE = re.compile(r"^cross_mesa_validation_\d{2}\.jsonl$")

# Expected path fields per source (used to answer item 6).
PATH_FIELD_BY_SOURCE = {
    "e14t": "e14t_path",
    "e14d": "e14d_path",
    "e14c": "e14c_path",
}


# ---------------------------------------------------------------------------
# Reference data loaders (mirror of prepare_gallery.py)
# ---------------------------------------------------------------------------
def load_dept_map() -> dict[str, str]:
    with open(DATA_DIR / "departamentos.json", encoding="utf-8") as f:
        depts = json.load(f)
    out = {}
    for d in depts:
        out[d["id"]] = d["nombre"].rstrip(".")
    return out


def load_mpio_map() -> dict[tuple[str, str], str]:
    with open(DATA_DIR / "divipole.json", encoding="utf-8") as f:
        div = json.load(f)
    out = {}
    for dept_code, dept_data in div.get("departamentos", {}).items():
        for mpio_code, mpio_data in dept_data.get("municipios", {}).items():
            out[(dept_code, mpio_code)] = mpio_data["nombre"]
    return out


def load_e14t_url_map() -> dict[tuple[str, str, str, str, str], str]:
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
# Classification (mirror of find_e14t_pdf / find_e14d_pdf, but with reason)
# ---------------------------------------------------------------------------
def classify(
    row: dict,
    dept_map: dict,
    mpio_map: dict,
    url_map: dict,
    kind: str,  # "E14T" or "E14D"
) -> tuple[str | None, str | None]:
    """Return (resolved_path_or_None, reason_or_None).

    reason is None when the file is found (resolved_path is not None).
    """
    dept = row["dept"]
    mpio = row["mpio"]
    zona = row["zona"]
    puesto = row["puesto"]
    mesa = row["mesa"]

    if dept not in dept_map:
        return None, "no_dept_name"
    if (dept, mpio) not in mpio_map:
        return None, "no_mpio_name"

    pdf_hash = url_map.get((dept, mpio, zona, puesto, mesa))
    if not pdf_hash:
        return None, "no_pdf_hash"

    dept_name = dept_map[dept]
    mpio_name = mpio_map[(dept, mpio)]
    path = (
        E14_BASE / kind / dept_name / mpio_name
        / f"zona_{zona}" / f"puesto_{puesto}" / f"{pdf_hash}.pdf"
    )
    if not path.exists():
        return None, "file_not_found"
    return path, None


# ---------------------------------------------------------------------------
# Loaders for index.jsonl
# ---------------------------------------------------------------------------
def load_index(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def cross_mesa_files() -> list[Path]:
    return sorted(p for p in DATA_DIR.iterdir() if CROSS_RE.match(p.name))


def load_cross_rows() -> list[dict]:
    rows = []
    for p in cross_mesa_files():
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rows.append(json.loads(line))
    return rows


# ---------------------------------------------------------------------------
# Item 6: are e14t_path / e14d_path / e14c_path present anywhere the
# reprocess step might store them?
# ---------------------------------------------------------------------------
def check_path_fields(dataset_label: str) -> dict:
    """Inspect revalidation_reprocess.jsonl and cross_mesa_validation rows for
    e14t_path / e14d_path / e14c_path fields at any nesting depth.
    """
    report = {"reprocess_present": False, "cross_present": False}

    # reprocess file
    rp = REPROCESS_FILES.get(dataset_label)
    if rp and rp.exists():
        with open(rp, encoding="utf-8") as f:
            first = f.readline().strip()
        if first:
            obj = json.loads(first)
            top_keys = set(obj.keys())
            if any(pf in top_keys for pf in PATH_FIELD_BY_SOURCE.values()):
                report["reprocess_present"] = True
            # nested check inside "sources"
            for k, v in obj.items():
                if isinstance(v, dict) and any(pf in v for pf in PATH_FIELD_BY_SOURCE.values()):
                    report["reprocess_present"] = True

    # cross_mesa_validation
    cross_rows = load_cross_rows()
    for row in cross_rows[:200]:
        srcs = row.get("sources", {})
        for src_data in srcs.values():
            if isinstance(src_data, dict) and any(
                pf in src_data for pf in PATH_FIELD_BY_SOURCE.values()
            ):
                report["cross_present"] = True
                break
        if report["cross_present"]:
            break
    return report


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    print("[diag] Loading reference data...")
    dept_map = load_dept_map()
    mpio_map = load_mpio_map()
    url_map = load_e14t_url_map()
    print(f"[diag]   depts: {len(dept_map)}")
    print(f"[diag]   mpios: {len(mpio_map)}")
    print(f"[diag]   e14t url_map entries: {len(url_map)}")
    print()

    # URL map key padding sanity check -- compare against index padding.
    # Peek one key from each dataset vs one from url_map.
    if url_map:
        sample_k = next(iter(url_map.keys()))
        print(f"[diag] url_map sample key (dept,mpio,zona,puesto,mesa): {sample_k}")

    grand_reason_counts = defaultdict(lambda: defaultdict(int))  # dataset -> reason -> count
    grand_total = defaultdict(int)
    grand_missing = defaultdict(int)
    grand_found = defaultdict(int)
    examples = defaultdict(lambda: defaultdict(list))  # dataset -> reason -> [mesa_key,...]

    for label, idx_path in DATASETS.items():
        if not idx_path.exists():
            print(f"[diag] WARNING index not found: {idx_path}")
            continue
        rows = load_index(idx_path)
        print(f"[diag] Dataset: {label}  ({len(rows)} mesas in index)")

        # Determine which finder(s) to apply.
        # The conflictivas pendientes datasets are primary_source driven:
        #   E14T_conflictivas -> primary_source e14t -> find_e14t_pdf
        #   E14D_conflictivas -> primary_source e14d -> find_e14d_pdf
        # But to be safe we ALSO run the other finder so the report covers
        # both possible "missing" buckets (the user mentioned E14T 126 + E14D
        # 216 = 342, which matches one finder per dataset).
        if label.startswith("E14T"):
            kind = "E14T"
        elif label.startswith("E14D"):
            kind = "E14D"
        else:
            kind = "E14T"

        for row in rows:
            grand_total[label] += 1
            path, reason = classify(row, dept_map, mpio_map, url_map, kind)
            if reason is None:
                grand_found[label] += 1
                continue
            grand_missing[label] += 1
            grand_reason_counts[label][reason] += 1
            mk = row.get("mesa_key") or f"{row['dept']}_{row['mpio']}_{row['zona']}_{row['puesto']}_{row['mesa']}"
            if len(examples[label][reason]) < 10:
                examples[label][reason].append(mk)

        print(f"[diag]   applied finder: find_{kind.lower()}_pdf")
        print(f"[diag]   found:   {grand_found[label]}")
        print(f"[diag]   missing: {grand_missing[label]}")
        print(f"[diag]   reasons:")
        for reason in ("no_dept_name", "no_mpio_name", "no_pdf_hash", "file_not_found"):
            c = grand_reason_counts[label].get(reason, 0)
            print(f"[diag]     {reason:18s} = {c}")
        print()

    # ------------------------------------------------------------------
    # Summary table
    # ------------------------------------------------------------------
    print("=" * 78)
    print("SUMMARY: missing PDFs per dataset per reason")
    print("=" * 78)
    header = "{:<24s} {:>12s} {:>14s} {:>12s} {:>16s} {:>10s}".format(
        "dataset", "no_dept_name", "no_mpio_name", "no_pdf_hash",
        "file_not_found", "TOTAL"
    )
    print(header)
    print("-" * 78)
    total_all = 0
    for label in DATASETS:
        rc = grand_reason_counts[label]
        tot = grand_missing[label]
        total_all += tot
        print("{:<24s} {:>12d} {:>14d} {:>12d} {:>16d} {:>10d}".format(
            label,
            rc.get("no_dept_name", 0),
            rc.get("no_mpio_name", 0),
            rc.get("no_pdf_hash", 0),
            rc.get("file_not_found", 0),
            tot,
        ))
    print("-" * 78)
    print("{:<24s} {:>12s} {:>14s} {:>12s} {:>16s} {:>10d}".format(
        "TOTAL", "-", "-", "-", "-", total_all))
    print()

    # ------------------------------------------------------------------
    # Examples per failure category
    # ------------------------------------------------------------------
    print("=" * 78)
    print("EXAMPLES (first 10 mesa_keys per dataset / reason)")
    print("=" * 78)
    for label in DATASETS:
        for reason in ("no_dept_name", "no_mpio_name", "no_pdf_hash", "file_not_found"):
            exs = examples[label].get(reason, [])
            if not exs:
                continue
            print(f"\n[{label}] {reason} ({len(exs)} shown):")
            for mk in exs:
                print(f"  {mk}")

    # ------------------------------------------------------------------
    # Item 6: do reprocess / cross_mesa rows carry *_path fields?
    # ------------------------------------------------------------------
    print()
    print("=" * 78)
    print("ITEM 6: presence of e14t_path / e14d_path / e14c_path fields")
    print("=" * 78)
    for label in DATASETS:
        rep = check_path_fields(label)
        print(f"[{label}]")
        print(f"  revalidation_reprocess.jsonl has any *_path field: {rep['reprocess_present']}")
        print(f"  cross_mesa_validation rows have any *_path field:   {rep['cross_present']}")
    print()


if __name__ == "__main__":
    main()