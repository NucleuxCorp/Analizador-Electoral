#!/usr/bin/env python3
"""Download all production labels + crops index from Supabase to local.

Idempotent: safe to re-run.  Writes JSONL lines, overwrites per-row by dedup key.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.modules.labeler.db import _client

PAGE = 1000
OUTPUT_DIR = Path("data/labels")


def _write_jsonl(path: Path, rows: list[dict], key_field: str = "crop_id") -> int:
    """Idempotent: if file exists, load existing, merge new, deduplicate by key, rewrite."""
    if not rows:
        return 0
    existing: dict[str, dict] = {}
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    row = json.loads(line)
                    key = row.get(key_field)
                    if key:
                        existing[key] = row
    for row in rows:
        key = row.get(key_field)
        if key:
            existing[key] = row
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for k in sorted(existing.keys()):
            fh.write(json.dumps(existing[k], ensure_ascii=False) + "\n")
    return len(rows)


def download_labels(client, output_dir: Path) -> tuple[int, int]:
    """Download all rows from labels table to labels/manifest.jsonl.  Paginates."""
    rows: list[dict] = []
    for offset in range(0, 99999, PAGE):
        resp = (
            client.table("labels")
            .select("*")
            .range(offset, offset + PAGE - 1)
            .limit(PAGE)
            .execute()
        )
        if not resp.data:
            break
        rows.extend(resp.data)
        if len(resp.data) < PAGE:
            break
    path = output_dir / "labels" / "manifest.jsonl"
    written = _write_jsonl(path, rows, key_field="id")
    return len(rows), written


def download_crops_index(client, output_dir: Path) -> tuple[int, int]:
    """Download all rows from crops table to crops/index.jsonl.  Idempotent dedup."""
    rows: list[dict] = []
    for offset in range(0, 99999, PAGE):
        resp = (
            client.table("crops")
            .select("*")
            .range(offset, offset + PAGE - 1)
            .limit(PAGE)
            .execute()
        )
        if not resp.data:
            break
        rows.extend(resp.data)
        if len(resp.data) < PAGE:
            break
    path = output_dir / "crops" / "index.jsonl"
    written = _write_jsonl(path, rows, key_field="crop_id")
    return len(rows), written


def main() -> int:
    labels_dir = OUTPUT_DIR
    client = _client()

    label_count, label_written = download_labels(client, labels_dir)
    print(f"Labels downloaded: {label_count} rows, {label_written} written to manifest.jsonl")

    crop_count, crop_written = download_crops_index(client, labels_dir)
    print(f"Crops downloaded: {crop_count} rows, {crop_written} written to index.jsonl")

    return 0


if __name__ == "__main__":
    sys.exit(main())
