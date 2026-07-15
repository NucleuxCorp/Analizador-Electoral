"""
lab_e14_conflictivas.py

Laboratorio — conflictivas pendientes de revisión humana (E14C/D/T via --dataset).

Subcommands:
  build-index       Create index.jsonl for pending conflictivas
  revalidate        Audit stored fields (or --reprocess PDFs) for the 3 total fields
  prepare-gallery   Convert PDFs to JPEGs + manifest.json (after reprocess)
  build-html        Generate galeria.html from manifest + mesas/
  build-prototype   SDD prototype: alert_index + galeria_prototype.html (index only, no reprocess)
  run-pipeline      revalidate --reprocess then prepare-gallery

Output directory (gitignored Laboratorio):
  Laboratorio/analisis_transversal/{E14C|E14D|E14T}_conflictivas_pendientes/

Usage:
    python scripts/lab_e14_conflictivas.py build-index
    python scripts/lab_e14_conflictivas.py build-index --dataset E14D_conflictivas
    python scripts/lab_e14_conflictivas.py revalidate --reprocess --workers 4
    python scripts/lab_e14_conflictivas.py prepare-gallery --workers 4
    python scripts/lab_e14_conflictivas.py run-pipeline --workers 4 --dataset E14T_conflictivas
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.modules.review.datasets import dataset_config, normalize_dataset
from src.modules.review.exclusions import load_excluded_keys
from src.modules.review.field_audit import (
    TOTAL_FIELDS,
    candidate_votes,
    field_audit,
    is_conflictiva,
    mesa_key,
)

DATA_DIR = ROOT / "data"
INDEX_PATH = DATA_DIR / "cross_mesa_index.jsonl"
DECISIONS_DIR = Path(r"E:\Nucleux\tools\Analizador de Elecciones\Data")
DECISIONS_FILES = (
    "decisiones_E14C_3_totales_blancos.json",
    "decisiones_E14D_3_totales_blancos.json",
    "decisiones_E14T_3_totales_blancos.json",
)
_LAB_ROOT = ROOT / "Laboratorio/analisis_transversal"
_DATASET_KEY = "E14C_conflictivas"
_PRIMARY_SOURCE = "e14c"
LAB_DIR = _LAB_ROOT / "E14C_conflictivas_pendientes"

_CROSS_FILE_RE = re.compile(r"^cross_mesa_validation_\d{2}\.jsonl$")
MAX_BACKUPS = 25


def _configure_dataset(name: str | None) -> None:
    global _DATASET_KEY, _PRIMARY_SOURCE, LAB_DIR
    cfg = dataset_config(normalize_dataset(name))
    _DATASET_KEY = cfg["key"]
    _PRIMARY_SOURCE = cfg["primary_source"]
    LAB_DIR = _LAB_ROOT / cfg["lab_subdir"]


def _backup_if_exists(path: Path) -> None:
    if not path.exists() or path.stat().st_size == 0:
        return
    ts = time.strftime("%Y-%m-%d_%H-%M-%S")
    backup = path.with_name(f"{path.stem}.bak_{ts}{path.suffix}")
    shutil.copy2(path, backup)
    existing = sorted(path.parent.glob(f"{path.stem}.bak_*{path.suffix}"))
    while len(existing) > MAX_BACKUPS:
        existing[0].unlink()
        existing = existing[1:]


def mesa_tuple(row: dict) -> tuple[str, str, str, str, str]:
    return row["dept"], row["mpio"], row["zona"], row["puesto"], row["mesa"]


def load_excluded_mesa_keys(mode: str) -> set[str]:
    return load_excluded_keys(mode, decisions_dir=DECISIONS_DIR, source=_PRIMARY_SOURCE)


def iter_cross_rows():
    for path in sorted(p for p in DATA_DIR.iterdir() if _CROSS_FILE_RE.match(p.name)):
        with open(path, encoding="utf-8") as fp:
            for line in fp:
                if line.strip():
                    yield json.loads(line)


def cmd_build_index(args: argparse.Namespace) -> None:
    excluded = load_excluded_mesa_keys(args.exclude_mode)
    LAB_DIR.mkdir(parents=True, exist_ok=True)

    index_path = LAB_DIR / "index.jsonl"
    meta_path = LAB_DIR / "index_meta.json"
    _backup_if_exists(index_path)
    _backup_if_exists(meta_path)

    entries: list[dict] = []
    stats = {
        "all_three_confirmed_blank": 0,
        "partial_blank_only": 0,
        "has_unreadable": 0,
        "has_partial": 0,
        "has_digits_in_totals": 0,
    }

    for row in iter_cross_rows():
        if not is_conflictiva(row, _PRIMARY_SOURCE):
            continue
        mk = mesa_key(row)
        if mk in excluded:
            continue

        fields = row["sources"][_PRIMARY_SOURCE]["fields"]
        audit = field_audit(fields, _PRIMARY_SOURCE)
        if audit["all_three_confirmed_blank"]:
            stats["all_three_confirmed_blank"] += 1
        else:
            stats["partial_blank_only"] += 1
        if audit["has_unreadable"]:
            stats["has_unreadable"] += 1
        if audit["has_partial"]:
            stats["has_partial"] += 1
        if audit["has_digits"]:
            stats["has_digits_in_totals"] += 1

        entries.append({
            "mesa_key": mk,
            "dept": row["dept"],
            "mpio": row["mpio"],
            "zona": row["zona"],
            "puesto": row["puesto"],
            "mesa": row["mesa"],
            "primary_source": _PRIMARY_SOURCE,
            "candidate_votes": candidate_votes(fields, _PRIMARY_SOURCE),
            "blank_fields": audit["blank_fields"],
            "all_three_confirmed_blank": audit["all_three_confirmed_blank"],
            "field_class": {n: audit["fields"][n]["class"] for n in TOTAL_FIELDS},
            "stored_arrays": {n: audit["fields"][n]["stored"] for n in TOTAL_FIELDS},
            "revalidation_status": "pending",
        })

    entries.sort(key=lambda e: e["mesa_key"])

    with open(index_path, "w", encoding="utf-8") as fp:
        for entry in entries:
            fp.write(json.dumps(entry, ensure_ascii=False) + "\n")

    meta = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dataset": _DATASET_KEY,
        "primary_source": _PRIMARY_SOURCE,
        "criterion": f"{_PRIMARY_SOURCE}_conflictiva: status=ok, candidate_votes>0, >=1 blank total",
        "excluded_decisions_files": [str(DECISIONS_DIR / f) for f in DECISIONS_FILES],
        "exclude_mode": args.exclude_mode,
        "excluded_mesa_keys": len(excluded),
        "count": len(entries),
        "stats": stats,
        "output": str(index_path),
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(f"[build-index] Wrote {len(entries):,} entries → {index_path}")
    print(f"[build-index] Meta → {meta_path}")
    print(f"  all_three_confirmed_blank: {stats['all_three_confirmed_blank']:,}")
    print(f"  partial (1-2 blank):       {stats['partial_blank_only']:,}")
    print(f"  has_unreadable field:      {stats['has_unreadable']:,}")
    print(f"  has_partial field:         {stats['has_partial']:,}")
    print(f"  has_digits in totals:      {stats['has_digits_in_totals']:,}")


def load_lab_index() -> list[dict]:
    index_path = LAB_DIR / "index.jsonl"
    if not index_path.exists():
        print(f"Missing index: {index_path}. Run build-index first.")
        sys.exit(1)
    rows = []
    with open(index_path, encoding="utf-8") as fp:
        for line in fp:
            if line.strip():
                rows.append(json.loads(line))
    return rows


CHECKPOINT_PATH = LAB_DIR / "reprocess.checkpoint.json"


def _load_checkpoint() -> set[str]:
    if not CHECKPOINT_PATH.exists():
        return set()
    try:
        data = json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
        return set(data.get("done_keys", []))
    except Exception:
        return set()


def _save_checkpoint(done_keys: set[str]) -> None:
    CHECKPOINT_PATH.write_text(
        json.dumps({"done_keys": sorted(done_keys), "ts": time.time()}, indent=2),
        encoding="utf-8",
    )


def _load_reprocess_results() -> dict[str, dict]:
    path = LAB_DIR / "revalidation_reprocess.jsonl"
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            if line.strip():
                rec = json.loads(line)
                out[rec["mesa_key"]] = rec
    return out


def load_cross_rows_by_keys(keys: set[str]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for row in iter_cross_rows():
        mk = mesa_key(row)
        if mk in keys:
            found[mk] = row
    return found


def _reprocess_one(idx_row: dict, idx_entry: dict) -> dict:
    """Re-extract primary source from PDF (E14C arrays or E14D/E14T scalars)."""
    from pathlib import Path as _Path

    from src.modules.analyzer.cross_validator import _extract_source

    mk = idx_row["mesa_key"]
    src = _PRIMARY_SOURCE
    try:
        pdf_path = idx_entry.get(f"{src}_path")
        pdf = _Path(pdf_path) if pdf_path else None
        extracted = _extract_source(pdf, src)
        fields = extracted.get("fields") or {}
        audit = field_audit(fields, src)
        return {
            "mesa_key": mk,
            "mode": "reprocess",
            "primary_source": src,
            f"{src}_status": extracted.get("status"),
            f"{src}_error": extracted.get("error"),
            **audit,
            "field_class": {n: audit["fields"][n]["class"] for n in TOTAL_FIELDS},
            "stored_arrays": {n: audit["fields"][n]["stored"] for n in TOTAL_FIELDS},
            "index_all_three": idx_row.get("all_three_confirmed_blank"),
            "changed_all_three": audit["all_three_confirmed_blank"] != idx_row.get("all_three_confirmed_blank"),
            "still_conflictiva": bool(audit["blank_fields"]),
        }
    except Exception as exc:
        return {"mesa_key": mk, "mode": "reprocess", "error": str(exc)}


def load_cross_index(dept: str | None = None) -> dict[tuple, dict]:
    index: dict[tuple, dict] = {}
    with open(INDEX_PATH, encoding="utf-8") as fp:
        for line in fp:
            if not line.strip():
                continue
            entry = json.loads(line)
            if dept and entry.get("dept") != dept:
                continue
            key = (entry["dept"], entry["mpio"], entry["zona"], entry["puesto"], entry["mesa"])
            index[key] = entry
    return index


def cmd_revalidate(args: argparse.Namespace) -> None:
    index_rows = load_lab_index()
    if args.dept:
        index_rows = [r for r in index_rows if r["dept"] == args.dept]

    out_path = LAB_DIR / ("revalidation_reprocess.jsonl" if args.reprocess else "revalidation_audit.jsonl")
    _backup_if_exists(out_path)

    summary = {
        "all_three_confirmed_blank": 0,
        "not_all_three": 0,
        "changed_from_index": 0,
        "reprocess_errors": 0,
    }

    if args.reprocess:
        cross_index = load_cross_index(args.dept)

        done_keys = _load_checkpoint() if not args.reset_checkpoint else set()
        prior = _load_reprocess_results() if not args.reset_checkpoint else {}
        results_map: dict[str, dict] = dict(prior)

        targets: list[tuple[dict, dict]] = []
        for row in index_rows:
            mk = row["mesa_key"]
            if mk in done_keys:
                continue
            key = mesa_tuple(row)
            if key not in cross_index:
                results_map[mk] = {"mesa_key": mk, "mode": "reprocess", "error": "missing_cross_index"}
                done_keys.add(mk)
                summary["reprocess_errors"] += 1
                continue
            targets.append((row, cross_index[key]))

        if targets:
            if args.workers > 1:
                with ProcessPoolExecutor(max_workers=args.workers) as pool:
                    futs = {pool.submit(_reprocess_one, t[0], t[1]): t[0]["mesa_key"] for t in targets}
                    for fut in tqdm(as_completed(futs), total=len(futs), desc="reprocess"):
                        rec = fut.result()
                        results_map[rec["mesa_key"]] = rec
                        done_keys.add(rec["mesa_key"])
                        if len(done_keys) % 25 == 0:
                            _save_checkpoint(done_keys)
            else:
                for idx_row, idx_entry in tqdm(targets, desc="reprocess"):
                    rec = _reprocess_one(idx_row, idx_entry)
                    results_map[rec["mesa_key"]] = rec
                    done_keys.add(rec["mesa_key"])

        _save_checkpoint(done_keys)
        results = list(results_map.values())
    else:
        results = []
        for row in index_rows:
            audit = field_audit({
                "VOTANTES": row["stored_arrays"]["VOTANTES"],
                "URNA": row["stored_arrays"]["URNA"],
                "SUMA_TOTAL": row["stored_arrays"]["SUMA_TOTAL"],
            }, _PRIMARY_SOURCE)
            rec = {
                "mesa_key": row["mesa_key"],
                "mode": "audit",
                **audit,
                "index_all_three": row.get("all_three_confirmed_blank"),
            }
            results.append(rec)

    with open(out_path, "w", encoding="utf-8") as fp:
        for rec in sorted(results, key=lambda r: r["mesa_key"]):
            if rec.get("all_three_confirmed_blank"):
                summary["all_three_confirmed_blank"] += 1
            elif "all_three_confirmed_blank" in rec:
                summary["not_all_three"] += 1
            if rec.get("changed_all_three"):
                summary["changed_from_index"] += 1
            if rec.get("error"):
                summary["reprocess_errors"] += 1
            fp.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"[revalidate] mode={'reprocess' if args.reprocess else 'audit'}")
    print(f"[revalidate] mesas: {len(results):,} → {out_path}")
    print(f"  all_three_confirmed_blank: {summary['all_three_confirmed_blank']:,}")
    print(f"  not all three blank:       {summary['not_all_three']:,}")
    if args.reprocess:
        print(f"  changed vs index:          {summary['changed_from_index']:,}")
        print(f"  errors:                    {summary['reprocess_errors']:,}")
        still = sum(1 for r in results if r.get("still_conflictiva"))
        print(f"  still_conflictiva:         {still:,}")


def cmd_prepare_gallery(args: argparse.Namespace) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from scripts.prepare_gallery import (
        load_dept_map,
        load_e14t_url_map,
        load_mpio_map,
        prepare_mesa,
    )

    if not (LAB_DIR / "revalidation_reprocess.jsonl").exists():
        print("Missing revalidation_reprocess.jsonl — run revalidate --reprocess first.")
        sys.exit(1)

    index_rows = load_lab_index()
    reprocess = _load_reprocess_results()

    if args.all_three_only:
        keys = {mk for mk, rec in reprocess.items() if rec.get("all_three_confirmed_blank")}
        index_rows = [r for r in index_rows if r["mesa_key"] in keys]
        print(f"[prepare-gallery] Filter all-three-only: {len(index_rows):,} mesas")
    elif args.still_conflictiva_only:
        keys = {mk for mk, rec in reprocess.items() if rec.get("still_conflictiva")}
        index_rows = [r for r in index_rows if r["mesa_key"] in keys]
        print(f"[prepare-gallery] Filter still-conflictiva: {len(index_rows):,} mesas")

    cross_by_key = load_cross_rows_by_keys({r["mesa_key"] for r in index_rows})
    rows = []
    missing_cross = 0
    for entry in index_rows:
        row = cross_by_key.get(entry["mesa_key"])
        if row:
            rows.append(row)
        else:
            missing_cross += 1

    mesas_dir = LAB_DIR / "mesas"
    manifest_path = LAB_DIR / "manifest.json"
    _backup_if_exists(manifest_path)
    mesas_dir.mkdir(parents=True, exist_ok=True)

    dept_map = load_dept_map()
    mpio_map = load_mpio_map()
    url_map = load_e14t_url_map()

    missing_count = {"e14t": 0, "e14d": 0, "e14c": 0}

    def do_mesa(row: dict):
        return prepare_mesa(row, mesas_dir, dept_map, mpio_map, url_map, dry_run=False)

    if args.workers > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(do_mesa, row) for row in rows]
            for i, fut in enumerate(tqdm(as_completed(futs), total=len(futs), desc="gallery"), 1):
                r = fut.result()
                for src in ("e14t", "e14d", "e14c"):
                    if r["sources"].get(src) == "missing":
                        missing_count[src] += 1
    else:
        for row in tqdm(rows, desc="gallery"):
            r = do_mesa(row)
            for src in ("e14t", "e14d", "e14c"):
                if r["sources"].get(src) == "missing":
                    missing_count[src] += 1

    manifest = [
        {"dept": r["dept"], "mpio": r["mpio"], "zona": r["zona"], "puesto": r["puesto"], "mesa": r["mesa"]}
        for r in rows
    ]
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"[prepare-gallery] manifest: {len(manifest):,} mesas → {manifest_path}")
    print(f"[prepare-gallery] mesas dir: {mesas_dir}")
    print(f"[prepare-gallery] missing PDFs: {missing_count}")
    if missing_cross:
        print(f"[prepare-gallery] missing cross rows: {missing_cross}")


def cmd_build_prototype(_args: argparse.Namespace) -> None:
    from scripts.build_gallery_compatible import main as build_compatible_main
    import sys as _sys

    _sys.argv = [
        "build_gallery_compatible.py",
        "--lab-dir",
        str(LAB_DIR.relative_to(ROOT)),
    ]
    build_compatible_main()


def cmd_build_html(_args: argparse.Namespace) -> None:
    from scripts.build_gallery import main as build_gallery_main
    import sys as _sys

    _sys.argv = [
        "build_gallery.py",
        "--lab-dir",
        str(LAB_DIR.relative_to(ROOT)),
    ]
    build_gallery_main()


def cmd_run_pipeline(args: argparse.Namespace) -> None:
    reval = argparse.Namespace(
        reprocess=True,
        dept=getattr(args, "dept", None),
        workers=args.workers,
        reset_checkpoint=getattr(args, "reset_checkpoint", False),
    )
    cmd_revalidate(reval)
    cmd_prepare_gallery(args)
    cmd_build_html(args)


def main() -> None:
    parser = argparse.ArgumentParser(description="Laboratorio conflictivas pendientes (E14C/D/T)")
    parser.add_argument(
        "--dataset",
        default=None,
        help="E14C_conflictivas | E14D_conflictivas | E14T_conflictivas",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_index = sub.add_parser("build-index", help="Build index.jsonl for pending conflictivas")
    p_index.add_argument(
        "--exclude-mode",
        choices=["confirmed", "all"],
        default="confirmed",
        help="Exclude human-confirmed suspicious (default, 4117) or any reviewed mesa",
    )
    p_index.set_defaults(func=cmd_build_index)

    p_rev = sub.add_parser("revalidate", help="Re-audit or reprocess the 3 total fields")
    p_rev.add_argument("--reprocess", action="store_true", help="Re-extract primary source from PDFs")
    p_rev.add_argument("--dept", help="Limit to one department code (e.g. 05)")
    p_rev.add_argument("--workers", type=int, default=1, help="Parallel workers for --reprocess")
    p_rev.add_argument("--reset-checkpoint", action="store_true", help="Ignore reprocess checkpoint")
    p_rev.set_defaults(func=cmd_revalidate)

    p_gal = sub.add_parser("prepare-gallery", help="Build manifest + JPEGs from index")
    p_gal.add_argument("--workers", type=int, default=4, help="Parallel PDF converters")
    p_gal.add_argument("--all-three-only", action="store_true", help="Only mesas with 3 blanks post-reprocess")
    p_gal.add_argument(
        "--still-conflictiva-only",
        action="store_true",
        help="Only mesas still conflictiva after reprocess (default: all index mesas)",
    )
    p_gal.set_defaults(func=cmd_prepare_gallery)

    p_html = sub.add_parser("build-html", help="Generate galeria.html (requires manifest.json)")
    p_html.set_defaults(func=cmd_build_html)

    p_proto = sub.add_parser(
        "build-prototype",
        help="SDD prototype gallery from index.jsonl (compatible alerts, no reprocess)",
    )
    p_proto.set_defaults(func=cmd_build_prototype)

    p_pipe = sub.add_parser("run-pipeline", help="reprocess then prepare-gallery")
    p_pipe.add_argument("--workers", type=int, default=4)
    p_pipe.add_argument("--reset-checkpoint", action="store_true")
    p_pipe.add_argument("--all-three-only", action="store_true")
    p_pipe.add_argument("--still-conflictiva-only", action="store_true")
    p_pipe.set_defaults(func=cmd_run_pipeline)

    args = parser.parse_args()
    _configure_dataset(getattr(args, "dataset", None))
    print(f"[lab] dataset={_DATASET_KEY} primary_source={_PRIMARY_SOURCE} lab_dir={LAB_DIR}")
    args.func(args)


if __name__ == "__main__":
    main()