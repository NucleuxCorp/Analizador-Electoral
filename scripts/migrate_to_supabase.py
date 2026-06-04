"""
migrate_to_supabase — One-time migration script for the labeler web portal.

Seeds the Supabase database from local export pipeline output:
  1. Inserts crops from data/labels/crops/index.jsonl → `crops` table
     (batch_size=500, auto_skip crops excluded per spec invariant I2)
  2. Inserts historical labels from data/labels/manifest.jsonl → `labels` table
     (annotator_id='legacy-user' UUID placeholder for pre-web labels)
  3. Uploads PNGs from data/labels/crops/ → Supabase Storage bucket 'crops'

Idempotent: re-running skips already-inserted/uploaded rows.

Usage:
    python scripts/migrate_to_supabase.py [--labels-dir PATH] [--dry-run]

Environment variables required:
    SUPABASE_URL        — Your Supabase project URL
    SUPABASE_ANON_KEY   — Your Supabase anon/public key

Optional:
    LABELS_DIR          — Override default data/labels (default: relative to repo root)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup — ensure repo root is importable
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Load .env from repo root if present
try:
    from dotenv import load_dotenv
    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LEGACY_USER_ID = "00000000-0000-0000-0000-000000000001"  # stable UUID for legacy labels


def _require_env() -> None:
    """Raise early with a clear message if required env vars are missing."""
    missing = [v for v in ("SUPABASE_URL", "SUPABASE_ANON_KEY") if not os.environ.get(v, "").strip()]
    if missing:
        raise SystemExit(
            f"ERROR: Missing required environment variables: {', '.join(missing)}\n"
            "Set them in your shell or .env file before running this script."
        )


def _iter_jsonl(path: Path):
    """Yield parsed dicts from a JSONL file; skip blank lines and bad JSON."""
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


# ---------------------------------------------------------------------------
# Step 1: Seed crops table
# ---------------------------------------------------------------------------

def migrate_crops(
    index_path: Path,
    crops_dir: Path,
    dry_run: bool = False,
    batch_size: int = 500,
) -> dict:
    """
    Seed the Supabase `crops` table from index.jsonl.

    Args:
        index_path: Path to data/labels/crops/index.jsonl.
        crops_dir:  Path to data/labels/crops/ (PNGs).
        dry_run:    When True, print rows but do not insert.
        batch_size: Insert batch size (default 500).

    Returns:
        Stats dict.
    """
    from src.modules.labeler.export_supabase import seed_crops_table

    if not index_path.exists():
        print(f"  SKIP crops migration — index file not found: {index_path}")
        return {"inserted": 0, "skipped_auto_skip": 0, "skipped_existing": 0, "errors": 0}

    if dry_run:
        count = sum(1 for _ in _iter_jsonl(index_path))
        print(f"  [DRY RUN] Would process {count} rows from {index_path}")
        return {"inserted": 0, "skipped_auto_skip": 0, "skipped_existing": 0, "errors": 0}

    print(f"  Seeding crops table from {index_path} ...")
    stats = seed_crops_table(
        index_path=index_path,
        crops_dir=crops_dir,
        batch_size=batch_size,
        skip_auto_skip=True,
    )
    print(
        f"  Crops: inserted={stats['inserted']}, "
        f"skipped_auto_skip={stats['skipped_auto_skip']}, "
        f"skipped_existing={stats['skipped_existing']}, "
        f"errors={stats['errors']}"
    )
    return stats


# ---------------------------------------------------------------------------
# Step 2: Seed labels table from manifest.jsonl
# ---------------------------------------------------------------------------

def migrate_labels(
    manifest_path: Path,
    dry_run: bool = False,
    batch_size: int = 500,
) -> dict:
    """
    Insert historical labels from manifest.jsonl into the Supabase `labels` table.

    Each row is attributed to annotator_id='legacy-user' (a stable UUID).
    Only inserts crops that already exist in the `crops` table (foreign key constraint).

    Args:
        manifest_path: Path to data/labels/manifest.jsonl.
        dry_run:       When True, print count but do not insert.
        batch_size:    Insert batch size (default 500).

    Returns:
        Stats dict.
    """
    if not manifest_path.exists():
        print(f"  SKIP labels migration — manifest not found: {manifest_path}")
        return {"inserted": 0, "skipped_missing_crop": 0, "errors": 0}

    from src.modules.labeler.db import _client

    client = _client()

    if dry_run:
        count = sum(1 for _ in _iter_jsonl(manifest_path))
        print(f"  [DRY RUN] Would process {count} rows from {manifest_path}")
        return {"inserted": 0, "skipped_missing_crop": 0, "errors": 0}

    # Fetch valid crop_ids from crops table (to enforce FK — only insert if crop exists)
    try:
        resp = client.table("crops").select("crop_id").execute()
        valid_crop_ids: set[str] = {r["crop_id"] for r in (resp.data or [])}
    except Exception as exc:
        print(f"  ERROR fetching crops for FK validation: {exc}")
        return {"inserted": 0, "skipped_missing_crop": 0, "errors": 0}

    stats = {"inserted": 0, "skipped_missing_crop": 0, "errors": 0}
    batch: list[dict] = []

    def _flush(rows: list[dict]) -> None:
        if not rows:
            return
        try:
            client.table("labels").insert(rows).execute()
            stats["inserted"] += len(rows)
        except Exception as exc:
            print(f"  ERROR inserting labels batch of {len(rows)}: {exc}")
            stats["errors"] += len(rows)

    print(f"  Seeding labels table from {manifest_path} ...")
    for record in _iter_jsonl(manifest_path):
        crop_id = record.get("crop_id", "")
        if not crop_id or crop_id not in valid_crop_ids:
            stats["skipped_missing_crop"] += 1
            continue

        label_human = record.get("label_human", "")
        amended = bool(record.get("amended", False))

        # Parse timestamp from manifest row; fall back to epoch
        ts_str = record.get("ts") or record.get("timestamp", "")
        ts: str
        if ts_str:
            ts = ts_str
        else:
            ts = datetime.now(tz=timezone.utc).isoformat()

        row = {
            "crop_id": crop_id,
            "annotator_id": _LEGACY_USER_ID,
            "label_human": label_human,
            "amended": amended,
            "is_admin_resolution": False,
            "ts": ts,
        }
        batch.append(row)

        if len(batch) >= batch_size:
            _flush(batch)
            batch = []

    _flush(batch)
    print(
        f"  Labels: inserted={stats['inserted']}, "
        f"skipped_missing_crop={stats['skipped_missing_crop']}, "
        f"errors={stats['errors']}"
    )
    return stats


# ---------------------------------------------------------------------------
# Step 3: Upload PNGs to Supabase Storage
# ---------------------------------------------------------------------------

def migrate_storage(crops_dir: Path, dry_run: bool = False) -> dict:
    """
    Upload PNG files from crops_dir to the Supabase Storage bucket 'crops'.

    Args:
        crops_dir: Directory containing {crop_id}.png files.
        dry_run:   When True, count PNGs but do not upload.

    Returns:
        Stats dict.
    """
    from src.modules.labeler.export_supabase import upload_crops_to_storage

    if not crops_dir.exists():
        print(f"  SKIP storage upload — crops dir not found: {crops_dir}")
        return {"uploaded": 0, "skipped_already_uploaded": 0, "errors": 0}

    if dry_run:
        count = sum(1 for p in crops_dir.glob("*.png"))
        print(f"  [DRY RUN] Would upload up to {count} PNGs from {crops_dir}")
        return {"uploaded": 0, "skipped_already_uploaded": 0, "errors": 0}

    # Storage RLS blocks anon writes. If a service_role key is available, build a
    # privileged client used ONLY for this bulk upload (never the running portal).
    upload_client = None
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if service_key:
        from supabase import create_client
        upload_client = create_client(os.environ["SUPABASE_URL"].strip(), service_key)
        print("  Using SUPABASE_SERVICE_ROLE_KEY for Storage upload (bypasses RLS).")
    else:
        print("  WARNING: SUPABASE_SERVICE_ROLE_KEY not set — uploading with anon key.")
        print("           This fails unless the 'crops' bucket has a public INSERT policy.")

    print(f"  Uploading PNGs from {crops_dir} to Storage bucket 'crops' ...")
    stats = upload_crops_to_storage(crops_dir=crops_dir, rate_limit_rps=10.0, client=upload_client)
    print(
        f"  Storage: uploaded={stats['uploaded']}, "
        f"skipped_already_uploaded={stats['skipped_already_uploaded']}, "
        f"errors={stats['errors']}"
    )
    return stats


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="One-time migration: seed Supabase from local labeler export output."
    )
    parser.add_argument(
        "--labels-dir",
        default=os.environ.get("LABELS_DIR", str(_REPO_ROOT / "data" / "labels")),
        help="Root of the local labels directory (default: data/labels)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Rows per Supabase batch insert (default: 500)",
    )
    parser.add_argument(
        "--skip-crops",
        action="store_true",
        help="Skip crops table migration",
    )
    parser.add_argument(
        "--skip-labels",
        action="store_true",
        help="Skip labels table migration",
    )
    parser.add_argument(
        "--skip-storage",
        action="store_true",
        help="Skip Supabase Storage upload",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without making any Supabase calls",
    )
    args = parser.parse_args()

    if not args.dry_run:
        _require_env()

    labels_dir = Path(args.labels_dir).resolve()
    crops_dir = labels_dir / "crops"
    index_path = crops_dir / "index.jsonl"
    manifest_path = labels_dir / "manifest.jsonl"

    print(f"Migration — labels_dir: {labels_dir}")
    print(f"  index.jsonl  : {index_path}  (exists={index_path.exists()})")
    print(f"  manifest.jsonl: {manifest_path}  (exists={manifest_path.exists()})")
    print(f"  crops_dir    : {crops_dir}  (exists={crops_dir.exists()})")
    print()

    all_stats: dict[str, dict] = {}

    # --- Step 1: crops table ---
    if not args.skip_crops:
        print("=== Step 1/3: Seeding crops table ===")
        all_stats["crops"] = migrate_crops(
            index_path=index_path,
            crops_dir=crops_dir,
            dry_run=args.dry_run,
            batch_size=args.batch_size,
        )
        print()

    # --- Step 2: labels table ---
    if not args.skip_labels:
        print("=== Step 2/3: Seeding labels table ===")
        all_stats["labels"] = migrate_labels(
            manifest_path=manifest_path,
            dry_run=args.dry_run,
            batch_size=args.batch_size,
        )
        print()

    # --- Step 3: Storage upload ---
    if not args.skip_storage:
        print("=== Step 3/3: Uploading PNGs to Supabase Storage ===")
        all_stats["storage"] = migrate_storage(
            crops_dir=crops_dir,
            dry_run=args.dry_run,
        )
        print()

    print("=== Migration complete ===")
    for step, stats in all_stats.items():
        print(f"  {step}: {stats}")


if __name__ == "__main__":
    main()
