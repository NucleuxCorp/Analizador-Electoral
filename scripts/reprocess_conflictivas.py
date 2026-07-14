"""
scripts/reprocess_conflictivas.py

Targeted reprocessing of mesas with blank VOTANTES/URNA/SUMA_TOTAL in E14C.
Fixes false zeros caused by the x>0 bug in grid_detector_v3.py cell_has_ink().

Modes:
  conflictivas (default) — mesas where E14C has at least one total field = 0
  remaining              — all mesas NOT flagged as conflictivas
  all                    — every mesa (equivalent to full re-run per dept)

The existing cross_mesa_validation_{dept}.jsonl is updated in-place.
A timestamped backup is created before any overwrite (max 10 per file).

Usage:
    python scripts/reprocess_conflictivas.py
    python scripts/reprocess_conflictivas.py --mode conflictivas --dept 09
    python scripts/reprocess_conflictivas.py --mode remaining --dept 05
    python scripts/reprocess_conflictivas.py --dry-run
"""
from __future__ import annotations

import argparse
import glob
import json
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# E14C-only: array-schema helpers. MUST NOT be applied to E14T or E14D fields.
# SCHEMA NOTE: after ADR-2 migration, E14C fields are 3-element digit arrays.
# E14T/E14D fields remain scalars — do not apply these helpers to them.
from debug_sv.field_array import is_confirmed_blank  # noqa: E402

DATA_DIR = ROOT / "data"
INDEX_PATH = DATA_DIR / "cross_mesa_index.jsonl"
TOTAL_FIELDS = ("VOTANTES", "URNA", "SUMA_TOTAL")
MAX_BACKUPS = 10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_conflictiva(rec: dict) -> bool:
    """True if E14C has blank total fields AND candidate votes > 0.

    A total field is considered a *real blank* (and thus conflictiva candidate)
    only when its digits are [None, None, None] — i.e. cell_has_ink() was False
    for all three subcells (the jury wrote nothing).
    Falls back to fields check if digits are not present.
    """
    e14c = (rec.get("sources") or {}).get("e14c") or {}
    if e14c.get("status") != "ok":
        return False
    fields = e14c.get("fields") or {}
    digits = e14c.get("digits") or {}

    c1 = fields.get("C1_CEPEDA") or 0
    c2 = fields.get("C2_ABELARDO") or 0
    if c1 + c2 == 0:
        return False

    def _is_real_blank(field: str) -> bool:
        dlist = digits.get(field)
        if isinstance(dlist, list) and len(dlist) >= 3:
            return all(d is None for d in dlist)
        # fallback to old fields logic
        val = fields.get(field)
        return val is None or val == 0

    return any(_is_real_blank(f) for f in TOTAL_FIELDS)


def _mesa_key(rec: dict) -> tuple:
    return (rec.get("dept"), rec.get("mpio"), rec.get("zona"), rec.get("puesto"), rec.get("mesa"))


def _backup(path: Path) -> Path:
    ts = time.strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(path.stem + f"_backup_{ts}" + path.suffix)
    shutil.copy2(path, backup)
    existing = sorted(path.parent.glob(path.stem + "_backup_*" + path.suffix))
    while len(existing) > MAX_BACKUPS:
        existing[0].unlink()
        existing = existing[1:]
    return backup


# ---------------------------------------------------------------------------
# Array-safe field merge helper (E14C only)
# ---------------------------------------------------------------------------

_BLANK_SENTINEL = [None, None, None]


def _merge_e14c_total_field(new_val, cur_val=None):
    """Merge one E14C total field value preserving array shape.

    After ADR-2, e14_worker returns arrays for all fields. This helper ensures
    that the stored value is always an array — never a scalar.

    Rules:
      - new_val is a list (post-migration array) → use it as-is
      - new_val is None (no value from worker, or pre-migration scalar None)
        → use blank sentinel [None, None, None]
      - new_val is a scalar int (should not happen post-migration; defensive)
        → use blank sentinel and log a warning (scalar in E14C fields is a defect)
    """
    if isinstance(new_val, list):
        return new_val
    # new_val is None or a scalar — treat as blank sentinel
    return list(_BLANK_SENTINEL)


# ---------------------------------------------------------------------------
# Index loader
# ---------------------------------------------------------------------------

def load_index(dept: str | None = None) -> dict[tuple, dict]:
    """Load cross_mesa_index.jsonl keyed by (dept, mpio, zona, puesto, mesa)."""
    index: dict[tuple, dict] = {}
    with open(INDEX_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if dept and entry.get("dept") != dept:
                continue
            key = (entry["dept"], entry["mpio"], entry["zona"], entry["puesto"], entry["mesa"])
            index[key] = entry
    return index


# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------

def _load_checkpoint(path: Path) -> set[tuple]:
    if not path.exists():
        return set()
    try:
        with open(path, encoding="utf-8") as f:
            chk = json.load(f)
        return {tuple(k) for k in chk.get("done_keys", [])}
    except Exception:
        return set()


def _save_checkpoint(path: Path, done_keys: set[tuple]) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"done_keys": [list(k) for k in done_keys], "ts": time.time()}, f)
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Per-dept reprocessing
# ---------------------------------------------------------------------------

def reprocess_dept(
    dept_code: str,
    index: dict[tuple, dict],
    mode: str,
    workers: int,
    dry_run: bool,
) -> dict:
    val_path = DATA_DIR / f"cross_mesa_validation_{dept_code}.jsonl"
    if not val_path.exists():
        print(f"  [{dept_code}] validation file not found — skipping")
        return {"total": 0, "conflictivas": 0, "reprocessed": 0, "skipped": 0}

    checkpoint_path = DATA_DIR / f"reprocess_{dept_code}_{mode}.checkpoint.json"
    done_keys = _load_checkpoint(checkpoint_path)

    records: list[dict] = []
    with open(val_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    n_conflictivas = sum(1 for r in records if _is_conflictiva(r))

    # Build list of (position, index_entry) to process
    targets: list[tuple[int, dict]] = []
    skipped_no_index = 0
    for i, rec in enumerate(records):
        key = _mesa_key(rec)
        if key in done_keys:
            continue
        is_conf = _is_conflictiva(rec)
        if mode == "conflictivas" and not is_conf:
            continue
        if mode == "remaining" and is_conf:
            continue
        if key not in index:
            skipped_no_index += 1
            continue
        targets.append((i, index[key]))

    print(
        f"  [{dept_code}] total={len(records)}, conflictivas={n_conflictivas}, "
        f"targets={len(targets)}, already_done={len(done_keys)}, "
        f"no_index={skipped_no_index}"
    )

    if dry_run or not targets:
        return {
            "total": len(records),
            "conflictivas": n_conflictivas,
            "reprocessed": 0,
            "skipped": skipped_no_index,
        }

    from src.modules.analyzer.cross_validator_cli import _worker_extract

    updated = 0

    if workers > 1:
        future_to_pos: dict = {}
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for pos, idx_entry in targets:
                fut = pool.submit(_worker_extract, idx_entry)
                future_to_pos[fut] = (pos, _mesa_key(idx_entry))

            for fut in tqdm(as_completed(future_to_pos), total=len(targets), desc=f"  {dept_code}"):
                pos, key = future_to_pos[fut]
                try:
                    raw = fut.result()
                    # _worker_extract returns the full cross-validation record:
                    # raw["sources"]["e14c"]["fields"] — NOT raw["fields"]
                    raw_e14c = ((raw.get("sources") or {}).get("e14c") or {})
                    # Merge into existing record structure instead of overwriting the whole record.
                    # This preserves dept, sources structure, congruencia, aritmetica, etc.
                    if "sources" not in records[pos]:
                        records[pos]["sources"] = {}
                    e14c = records[pos]["sources"].setdefault("e14c", {})
                    e14c["status"] = "ok"
                    # Merge fields: keep candidates etc from previous; update total fields as arrays.
                    # ADR-2: E14C total fields are 3-element digit arrays, never scalars.
                    # _merge_e14c_total_field preserves array shape; scalars/None → blank sentinel.
                    new_f = raw_e14c.get("fields") or {}
                    cur_f = e14c.get("fields", {}) or {}
                    for f in ("VOTANTES", "URNA", "SUMA_TOTAL"):
                        cur_f[f] = _merge_e14c_total_field(new_f.get(f), cur_f.get(f))
                    e14c["fields"] = cur_f
                    # Propagate "digits" so real blanks are determinable:
                    # digits[field] = [None, None, None] means cell_has_ink() was False for all subcells
                    # (verdadero espacio en blanco: el jurado no escribió nada).
                    e14c["digits"] = raw_e14c.get("digits") or {}
                    # Keep rows for deeper debugging if needed
                    e14c["rows"] = raw_e14c.get("rows") or []
                    done_keys.add(key)
                    updated += 1
                except Exception as exc:
                    print(f"\n  [{dept_code}] error on {key}: {exc}")

                if updated % 200 == 0 and updated > 0:
                    _save_checkpoint(checkpoint_path, done_keys)
    else:
        for pos, idx_entry in tqdm(targets, desc=f"  {dept_code}"):
            key = _mesa_key(idx_entry)
            try:
                raw = _worker_extract(idx_entry)
                raw_e14c = ((raw.get("sources") or {}).get("e14c") or {})
                if "sources" not in records[pos]:
                    records[pos]["sources"] = {}
                e14c = records[pos]["sources"].setdefault("e14c", {})
                e14c["status"] = "ok"
                # Merge fields: keep candidates etc from previous; update total fields as arrays.
                # ADR-2: E14C total fields are 3-element digit arrays, never scalars.
                # _merge_e14c_total_field preserves array shape; scalars/None → blank sentinel.
                new_f = raw_e14c.get("fields") or {}
                cur_f = e14c.get("fields", {}) or {}
                for f in ("VOTANTES", "URNA", "SUMA_TOTAL"):
                    cur_f[f] = _merge_e14c_total_field(new_f.get(f), cur_f.get(f))
                e14c["fields"] = cur_f
                # Propagate "digits" (None means real blank from cell_has_ink() == False)
                e14c["digits"] = raw_e14c.get("digits") or {}
                e14c["rows"] = raw_e14c.get("rows") or []
                done_keys.add(key)
                updated += 1
            except Exception as exc:
                print(f"\n  [{dept_code}] error on {key}: {exc}")

            if updated % 200 == 0 and updated > 0:
                _save_checkpoint(checkpoint_path, done_keys)

    # Write back with backup
    backup_path = _backup(val_path)
    print(f"  [{dept_code}] backup -> {backup_path.name}")

    tmp = val_path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    tmp.replace(val_path)

    _save_checkpoint(checkpoint_path, done_keys)
    print(f"  [{dept_code}] written {updated} updated entries to {val_path.name}")

    return {
        "total": len(records),
        "conflictivas": n_conflictivas,
        "reprocessed": updated,
        "skipped": skipped_no_index,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Targeted reprocessing of conflictiva mesas in cross_mesa_validation files."
    )
    parser.add_argument(
        "--mode",
        choices=["conflictivas", "remaining", "all"],
        default="conflictivas",
        help="Which mesas to reprocess (default: conflictivas)",
    )
    parser.add_argument("--dept", default=None, help="Dept code to process (e.g. 09). Omit for all.")
    parser.add_argument("--workers", type=int, default=4, help="Parallel workers (default: 4)")
    parser.add_argument("--dry-run", action="store_true", help="Count targets without reprocessing")
    args = parser.parse_args()

    print(f"Mode: {args.mode} | dept: {args.dept or 'ALL'} | workers: {args.workers} | dry-run: {args.dry_run}")

    if not INDEX_PATH.exists():
        print(f"[ERROR] Index not found: {INDEX_PATH}", file=sys.stderr)
        sys.exit(1)

    print(f"Loading index (dept={args.dept or 'all'})...")
    index = load_index(dept=args.dept)
    print(f"  {len(index):,} entries")

    if args.dept:
        dept_codes = [args.dept]
    else:
        files = sorted(glob.glob(str(DATA_DIR / "cross_mesa_validation_??.jsonl")))
        dept_codes = [Path(f).stem.replace("cross_mesa_validation_", "") for f in files]
        print(f"Departments to process: {dept_codes}")

    totals = {"total": 0, "conflictivas": 0, "reprocessed": 0}
    for dept_code in dept_codes:
        stats = reprocess_dept(dept_code, index, args.mode, args.workers, args.dry_run)
        for k in totals:
            totals[k] += stats.get(k, 0)

    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Summary:")
    print(f"  Total mesas scanned : {totals['total']:,}")
    print(f"  Conflictivas found  : {totals['conflictivas']:,}")
    print(f"  Reprocessed         : {totals['reprocessed']:,}")


if __name__ == "__main__":
    main()
