"""
backfill_full_cell.py — Populate crops.full_cell_crop_id from index.jsonl.

The portal shows the full 3-box cell image as context for each digit. On a cloud
deploy we can't read the local index.jsonl, so we mirror full_cell_crop_id into
the crops table (source of truth in prod).

Prerequisite (run once in the Supabase SQL Editor):
    ALTER TABLE crops ADD COLUMN IF NOT EXISTS full_cell_crop_id TEXT;

Usage:
    python scripts/backfill_full_cell.py
    python scripts/backfill_full_cell.py --labels-dir data/labels_v2 --dry-run

Idempotent: upserts by crop_id, so re-running just refreshes the values.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
try:
    from dotenv import load_dotenv
    load_dotenv(_REPO / ".env")
except ImportError:
    pass


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--labels-dir", default=os.environ.get("LABELS_DIR", "data/labels_v2"))
    ap.add_argument("--batch-size", type=int, default=500)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from src.modules.labeler.db import _client

    index_path = Path(args.labels_dir) / "crops" / "index.jsonl"
    if not index_path.exists():
        raise SystemExit(f"index.jsonl not found: {index_path}")

    client = _client()

    # Existing crop_ids in the table (paginated) — only update rows that exist.
    existing: set[str] = set()
    start = 0
    while True:
        resp = client.table("crops").select("crop_id").range(start, start + 999).execute()
        rows = resp.data or []
        existing.update(r["crop_id"] for r in rows if r.get("crop_id"))
        if len(rows) < 1000:
            break
        start += 1000
    print(f"crops in table: {len(existing)}")

    batch: list[dict] = []
    updated = 0

    def flush(rows: list[dict]) -> None:
        nonlocal updated
        if not rows:
            return
        client.table("crops").upsert(rows, on_conflict="crop_id").execute()
        updated += len(rows)

    with open(index_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            cid = r.get("crop_id")
            fc = r.get("full_cell_crop_id")
            if not cid or cid not in existing or not fc:
                continue
            # Include NOT-NULL columns so the upsert is valid (values unchanged).
            batch.append({
                "crop_id": cid,
                "pdf_path": r.get("pdf_path", ""),
                "field_name": r.get("field_name", ""),
                "digit_index": r.get("digit_index", -1),
                "full_cell_crop_id": fc,
            })
            if len(batch) >= args.batch_size:
                if not args.dry_run:
                    flush(batch)
                else:
                    updated += len(batch)
                batch = []
    if batch:
        if not args.dry_run:
            flush(batch)
        else:
            updated += len(batch)

    print(f"{'[DRY RUN] would update' if args.dry_run else 'updated'}: {updated} crops with full_cell_crop_id")


if __name__ == "__main__":
    main()
