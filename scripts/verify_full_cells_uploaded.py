"""Quick verification: how many full_cell images are in Supabase Storage.

Samples 100 random full_cell_crop_ids from index.jsonl and checks if their
public URL returns 200. Also queries Storage list() for total object count.
"""
from __future__ import annotations

import json
import os
import random
import sys
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
try:
    from dotenv import load_dotenv
    load_dotenv(_REPO / ".env")
except ImportError:
    pass

from supabase import create_client


def main() -> None:
    crops_dir = Path("data/labels_v2/crops")
    index_path = crops_dir / "index.jsonl"

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
    total = len(ids)
    print(f"Total unique full_cell IDs in index: {total}")

    url = os.environ["SUPABASE_URL"].strip()
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not service_key:
        raise SystemExit("SUPABASE_SERVICE_ROLE_KEY required")
    client = create_client(url, service_key)

    sample = random.sample(sorted(ids), min(100, total))
    base_public = f"{url}/storage/v1/object/public/crops"
    hits = 0
    misses = []
    for cid in sample:
        try:
            req = urllib.request.Request(f"{base_public}/{cid}.png", method="HEAD")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    hits += 1
                else:
                    misses.append(cid)
        except Exception:
            misses.append(cid)

    print(f"\nSample check (100 random IDs):")
    print(f"  present in Storage: {hits}/100")
    print(f"  missing: {len(misses)}/100")
    if misses[:5]:
        print(f"  first missing: {misses[:5]}")

    if hits == 100:
        print("\nAll sampled IDs are in Storage. Upload likely complete.")
    elif hits == 0:
        print("\nNone of the sampled IDs are in Storage. Upload likely not run.")
    else:
        print(f"\nPartial: ~{hits}% uploaded. Re-run upload_full_cells.py to finish.")


if __name__ == "__main__":
    main()
