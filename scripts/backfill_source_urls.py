"""
backfill_source_urls — Populate crops.source_url with public Registraduria links.

For every acta (pdf_path) present in the Supabase `crops` table, look up its
public PDF URL in data/e14c_urls.jsonl (matched by the stable filename prefix,
ignoring the rotating timestamp token) and write it to crops.source_url.

This lets the deployed portal serve "Ver acta" by redirecting to the public
Registraduria PDF instead of hosting ~30 GB of PDFs ourselves.

Prerequisite (run once in the Supabase SQL Editor):
    ALTER TABLE crops ADD COLUMN IF NOT EXISTS source_url TEXT;

Usage:
    python scripts/backfill_source_urls.py
    python scripts/backfill_source_urls.py --urls data/e14c_urls.jsonl --dry-run

Idempotent: updates by pdf_path, so re-running just refreshes the URLs.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(_REPO_ROOT / ".env")
except ImportError:
    pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--urls", default=str(_REPO_ROOT / "data" / "e14c_urls.jsonl"),
                        help="Path to e14c_urls.jsonl (default: data/e14c_urls.jsonl)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report matches without writing to Supabase")
    args = parser.parse_args()

    from src.modules.labeler.db import _client
    from src.modules.labeler.export_supabase import build_source_url_index, source_url_for

    urls_path = Path(args.urls)
    print(f"Building URL index from {urls_path} ...")
    url_index = build_source_url_index(urls_path)
    print(f"  Indexed {len(url_index)} Registraduria URLs")
    if not url_index:
        raise SystemExit("ERROR: no URLs indexed — check the --urls path.")

    client = _client()

    # Fetch all distinct pdf_paths currently in the crops table (paginated).
    print("Fetching distinct actas from crops table ...")
    pdf_paths: set[str] = set()
    start, page = 0, 1000
    while True:
        resp = client.table("crops").select("pdf_path").range(start, start + page - 1).execute()
        rows = resp.data or []
        for r in rows:
            pp = r.get("pdf_path")
            if pp:
                pdf_paths.add(pp)
        if len(rows) < page:
            break
        start += page
    print(f"  {len(pdf_paths)} unique actas in crops")

    matched, missing, updated = 0, 0, 0
    for pp in sorted(pdf_paths):
        url = source_url_for(pp, url_index)
        if not url:
            missing += 1
            if missing <= 10:
                print(f"  MISS: {Path(pp).name}")
            continue
        matched += 1
        if args.dry_run:
            continue
        try:
            client.table("crops").update({"source_url": url}).eq("pdf_path", pp).execute()
            updated += 1
        except Exception as exc:
            print(f"  ERROR updating {Path(pp).name}: {exc}")

    print("\n=== Backfill complete ===")
    print(f"  Actas matched : {matched}")
    print(f"  Actas missing : {missing}")
    if not args.dry_run:
        print(f"  Rows updated  : {updated} (by pdf_path)")
    else:
        print("  [DRY RUN] no writes performed")


if __name__ == "__main__":
    main()
