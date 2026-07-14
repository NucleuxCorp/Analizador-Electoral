#!/usr/bin/env python3
"""
Detect re-uploaded actas within each class (E14T, E14D, E14C).

Methodology:
  E14T/E14D: Compare transmission code snapshots (2026-06-21 vs 2026-06-30).
             Same geographic key but different SHA256 = re-uploaded content.
  E14C:      Compare suffix in local PDFs vs current e14c_sv_urls.jsonl index.
             Different suffix for same mesa = re-uploaded acta.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"

E14_BASE = Path("E:/Nucleux/tools/Analizador de Elecciones/Data/e14_segunda")
# Note: E14C/e14c_segunda/ is a secondary copy of some departments — excluded from analysis.

DEPT_NAMES = None  # lazy-loaded


def load_dept_names():
    global DEPT_NAMES
    if DEPT_NAMES is not None:
        return DEPT_NAMES
    with open(DATA / "departamentos.json", encoding="utf-8") as f:
        raw = json.load(f)
    DEPT_NAMES = {item["nombre"]: str(item["id"]) for item in raw}
    return DEPT_NAMES


# ── E14T/E14D: transmission snapshot comparison ──────────────────────────────

def _build_transmission_index(filepath: Path) -> dict:
    """Return {(dept, mpio, zona, puesto, mesa): sha256} from allTransmissionCodes file."""
    with open(filepath, encoding="utf-8") as f:
        d = json.load(f)
    idx = {}
    for sv in d["data"].values():
        for node in sv.get("nodes", []):
            key = (
                node["idDepartmentCode"],
                node["municipalityCode"],
                node["idZoneCode"],
                node["standCode"],
                node["numberStand"],
            )
            idx[key] = node["expectedName"].replace(".pdf", "")
    return idx


def detect_transmission_reuploads() -> dict:
    """Compare 2026-06-21 vs 2026-06-30 transmission snapshots."""
    snap_21 = DATA / "allTransmissionCodes_segunda.json"
    snap_30 = DATA / "allTransmissionCodes_segunda_2026-06-30.json"

    idx_21 = _build_transmission_index(snap_21)
    idx_30 = _build_transmission_index(snap_30)

    common = set(idx_21.keys()) & set(idx_30.keys())
    reuploads = []
    for key in common:
        sha21 = idx_21[key]
        sha30 = idx_30[key]
        if sha21 != sha30:
            dept, mpio, zona, puesto, mesa = key
            reuploads.append({
                "dept": dept, "mpio": mpio, "zona": zona,
                "puesto": puesto, "mesa": mesa,
                "sha256_2026-06-21": sha21,
                "sha256_2026-06-30": sha30,
            })

    only_in_30 = set(idx_30.keys()) - set(idx_21.keys())

    return {
        "snapshot_1": str(snap_21),
        "snapshot_2": str(snap_30),
        "mesas_in_snapshot_1": len(idx_21),
        "mesas_in_snapshot_2": len(idx_30),
        "mesas_in_common": len(common),
        "mesas_only_in_snapshot_2": len(only_in_30),
        "reuploads_count": len(reuploads),
        "reuploads": reuploads,
    }


# ── E14C: local files vs current index ───────────────────────────────────────

def _parse_e14c_filename(fname: str):
    """
    Parse E14C filename: {dept}_{mpio}_{zona}_{puesto}_E14_PRE_..._{mesa}_{suffix}.pdf
    Returns ((dept, mpio, zona, puesto, mesa_int), suffix) or None.
    """
    name = fname.replace(".pdf", "")
    parts = name.split("_")
    if len(parts) < 13:
        return None
    try:
        dept = parts[0]
        mpio = parts[1]
        zona = parts[2]
        puesto = parts[3]
        mesa = int(parts[11])
        suffix = parts[12]
        return (dept, mpio, zona, puesto, mesa), suffix
    except (IndexError, ValueError):
        return None


def _build_local_e14c_index() -> dict:
    """Return {(dept, mpio, zona, puesto, mesa): suffix} from disk.
    Excludes the e14c_segunda subdirectory (secondary copy, not a different download).
    """
    base = E14_BASE / "E14C"
    if not base.exists():
        print(f"  WARNING: {base} not found", file=sys.stderr)
        return {}, {}
    idx = {}
    dupes = defaultdict(list)
    for pdf in base.rglob("*.pdf"):
        # Skip the nested secondary copy
        if "e14c_segunda" in str(pdf):
            continue
        parsed = _parse_e14c_filename(pdf.name)
        if parsed is None:
            continue
        key, suffix = parsed
        if key in idx:
            dupes[key].append(suffix)
        else:
            idx[key] = suffix
    return idx, dict(dupes)


def _build_index_e14c() -> dict:
    """Return {(dept, mpio, zona, puesto, mesa): suffix} from e14c_sv_urls.jsonl."""
    idx = {}
    with open(DATA / "e14c_sv_urls.jsonl", encoding="utf-8") as f:
        for line in f:
            e = json.loads(line)
            fname = e["pdf_url"].split("/")[-1]
            parts = fname.replace(".pdf", "").split("_")
            # URL format: E14_PRE_{dept}_{mpio}_{corp}_{zona}_{puesto}_{mesa}_{suffix}
            # We use the JSONL fields directly — they're authoritative
            key = (
                str(e["cod_dept"]),
                str(e["cod_mpio"]),
                str(e["zona"]),
                str(e["cod_puesto"]),
                int(e["mesa"]),
            )
            # Suffix is last part of filename
            suffix = parts[-1]
            idx[key] = suffix
    return idx


def detect_e14c_reuploads() -> dict:
    local_idx, local_dupes = _build_local_e14c_index()
    server_idx = _build_index_e14c()

    common = set(local_idx.keys()) & set(server_idx.keys())
    reuploads = []
    for key in common:
        local_suffix = local_idx[key]
        server_suffix = server_idx[key]
        if local_suffix != server_suffix:
            dept, mpio, zona, puesto, mesa = key
            reuploads.append({
                "dept": dept, "mpio": mpio, "zona": zona,
                "puesto": puesto, "mesa": mesa,
                "suffix_local": local_suffix,
                "suffix_current_index": server_suffix,
                "version_changed": int(server_suffix) > int(local_suffix),
            })

    only_local = set(local_idx.keys()) - set(server_idx.keys())
    only_server = set(server_idx.keys()) - set(local_idx.keys())

    return {
        "mesas_local": len(local_idx),
        "mesas_server_index": len(server_idx),
        "mesas_in_common": len(common),
        "mesas_only_local": len(only_local),
        "mesas_only_server": len(only_server),
        "local_duplicate_filenames": len(local_dupes),
        "reuploads_count": len(reuploads),
        "reuploads": reuploads,
    }


# ── E14T/E14D identity check ─────────────────────────────────────────────────

def check_e14t_e14d_identity() -> dict:
    """Verify whether E14T and E14D serve identical PDFs."""
    idx_t, idx_d = {}, {}
    with open(DATA / "e14t_sv_urls.jsonl", encoding="utf-8") as f:
        for line in f:
            e = json.loads(line)
            key = (str(e["cod_dept"]), str(e["cod_mpio"]), str(e["zona"]),
                   str(e["puesto"]), str(e["mesa"]))
            sha = e["pdf_url"].split("/")[-1].split("?")[0].replace(".pdf", "")
            idx_t[key] = sha
    with open(DATA / "e14d_sv_urls.jsonl", encoding="utf-8") as f:
        for line in f:
            e = json.loads(line)
            key = (str(e["cod_dept"]), str(e["cod_mpio"]), str(e["zona"]),
                   str(e["puesto"]), str(e["mesa"]))
            sha = e["pdf_url"].split("/")[-1].split("?")[0].replace(".pdf", "")
            idx_d[key] = sha

    overlap = set(idx_t.keys()) & set(idx_d.keys())
    same_sha = sum(1 for k in overlap if idx_t[k] == idx_d[k])
    diff_sha = [(k, idx_t[k], idx_d[k]) for k in overlap if idx_t[k] != idx_d[k]]

    return {
        "e14t_entries": len(idx_t),
        "e14d_entries": len(idx_d),
        "overlap": len(overlap),
        "identical_sha256": same_sha,
        "different_sha256": len(diff_sha),
        "conclusion": "E14T and E14D are identical mirrors" if len(diff_sha) == 0 else "DIFFER",
        "diff_samples": diff_sha[:5],
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 65)
    print("Re-upload Detection Report — Actas E-14 Segunda Vuelta 2026")
    print("=" * 65)

    # 1. E14T / E14D identity
    print("\n[1] E14T vs E14D — Content identity check")
    identity = check_e14t_e14d_identity()
    print(f"    E14T: {identity['e14t_entries']:,} entries")
    print(f"    E14D: {identity['e14d_entries']:,} entries")
    print(f"    Overlap: {identity['overlap']:,}")
    print(f"    Same SHA256: {identity['identical_sha256']:,}")
    print(f"    Different SHA256: {identity['different_sha256']:,}")
    print(f"    -> {identity['conclusion']}")

    # 2. Transmission re-uploads (E14T/E14D via snapshots)
    print("\n[2] Transmission (E14T/E14D) — Cross-snapshot SHA256 comparison")
    print("    Snapshots: 2026-06-21 vs 2026-06-30")
    t_result = detect_transmission_reuploads()
    print(f"    Mesas in 2026-06-21 snapshot: {t_result['mesas_in_snapshot_1']:,}")
    print(f"    Mesas in 2026-06-30 snapshot: {t_result['mesas_in_snapshot_2']:,}")
    print(f"    Common mesas (comparable): {t_result['mesas_in_common']:,}")
    print(f"    New mesas in 2026-06-30 only: {t_result['mesas_only_in_snapshot_2']:,}")
    print(f"    Re-uploads detected (SHA256 changed): {t_result['reuploads_count']:,}")
    if t_result["reuploads"]:
        print("    Samples:")
        for r in t_result["reuploads"][:5]:
            print(f"      dept={r['dept']} mpio={r['mpio']} zona={r['zona']} "
                  f"puesto={r['puesto']} mesa={r['mesa']}")
            print(f"        21-jun: {r['sha256_2026-06-21'][:32]}...")
            print(f"        30-jun: {r['sha256_2026-06-30'][:32]}...")

    # 3. E14C re-uploads
    print("\n[3] E14C — Local files vs current server index")
    e14c_result = detect_e14c_reuploads()
    print(f"    Local PDFs mapped: {e14c_result['mesas_local']:,}")
    print(f"    Server index entries: {e14c_result['mesas_server_index']:,}")
    print(f"    Common mesas (comparable): {e14c_result['mesas_in_common']:,}")
    print(f"    Local-only (not in current index): {e14c_result['mesas_only_local']:,}")
    print(f"    Server-only (not downloaded): {e14c_result['mesas_only_server']:,}")
    print(f"    Local duplicate filenames: {e14c_result['local_duplicate_filenames']:,}")
    print(f"    Re-uploads detected (suffix changed): {e14c_result['reuploads_count']:,}")

    if e14c_result["reuploads"]:
        newer = sum(1 for r in e14c_result["reuploads"] if r["version_changed"])
        older = e14c_result["reuploads_count"] - newer
        print(f"      -> Server is newer: {newer:,}  |  Server is older: {older:,}")
        print("    Samples (server newer):")
        samples = [r for r in e14c_result["reuploads"] if r["version_changed"]][:10]
        for r in samples:
            print(f"      dept={r['dept']} mpio={r['mpio']} zona={r['zona']} "
                  f"puesto={r['puesto']} mesa={r['mesa']}: "
                  f"local={r['suffix_local']} → server={r['suffix_current_index']}")

    # Save full results
    out = REPO / "data" / "analysis_segunda_vuelta" / "reupload_detection.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            "e14t_e14d_identity": identity,
            "transmission_reuploads": t_result,
            "e14c_reuploads": e14c_result,
        }, f, ensure_ascii=False, indent=2)
    print(f"\nFull results saved to: {out}")


if __name__ == "__main__":
    main()
