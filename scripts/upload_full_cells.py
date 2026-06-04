"""
upload_full_cells.py — Upload the full-cell (3-box) context images to Storage.

The main crop upload only pushed the digit crops that live in the `crops` table.
The full-cell context images (referenced by full_cell_crop_id in index.jsonl) are
separate PNGs that were never uploaded, so /image/<full_cell_id> 404s in prod.
This uploads them to the same public 'crops' bucket.

Requires SUPABASE_SERVICE_ROLE_KEY (server-side; bypasses Storage RLS).

Usage:
    python scripts/upload_full_cells.py
    python scripts/upload_full_cells.py --labels-dir data/labels_v2 --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
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
    ap.add_argument("--bucket", default="crops")
    ap.add_argument("--rate", type=float, default=10.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    crops_dir = Path(args.labels_dir) / "crops"
    index_path = crops_dir / "index.jsonl"
    if not index_path.exists():
        raise SystemExit(f"index.jsonl not found: {index_path}")

    # Unique full_cell_crop_ids referenced by the index.
    ids: set[str] = set()
    with open(index_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            fc = r.get("full_cell_crop_id")
            if fc:
                ids.add(fc)
    print(f"unique full-cell images referenced: {len(ids)}")

    if args.dry_run:
        on_disk = sum(1 for i in ids if (crops_dir / f"{i}.png").exists())
        print(f"[DRY RUN] {on_disk} present on disk, would upload (skipping existing).")
        return

    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not service_key:
        raise SystemExit("SUPABASE_SERVICE_ROLE_KEY required (service_role bypasses Storage RLS).")
    from supabase import create_client
    client = create_client(os.environ["SUPABASE_URL"].strip(), service_key)

    delay = 1.0 / args.rate
    uploaded = skipped = missing = errors = 0
    for i, cid in enumerate(sorted(ids), 1):
        png = crops_dir / f"{cid}.png"
        if not png.exists():
            missing += 1
            continue
        try:
            with open(png, "rb") as fh:
                data = fh.read()
            client.storage.from_(args.bucket).upload(
                path=f"{cid}.png",
                file=data,
                file_options={"content-type": "image/png", "upsert": "false"},
            )
            uploaded += 1
        except Exception as exc:
            msg = str(exc).lower()
            if "exists" in msg or "duplicate" in msg or "409" in msg:
                skipped += 1
            else:
                errors += 1
                if errors <= 10:
                    print(f"  ERROR {cid}: {exc}")
        if i % 500 == 0:
            print(f"  ... {i}/{len(ids)} (uploaded={uploaded}, skipped={skipped})")
        time.sleep(delay)

    print(f"\nDone. uploaded={uploaded}, skipped_existing={skipped}, missing_on_disk={missing}, errors={errors}")


if __name__ == "__main__":
    main()
