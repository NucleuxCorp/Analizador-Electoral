"""
export_supabase — Supabase export pipeline for the labeler web portal.

This module bridges the local export pipeline (exporter.py output files) and
Supabase: it reads exporter.py's index.jsonl output, filters auto_skip crops,
and seeds the Supabase `crops` table + Storage bucket.

Design references:
  ADR-5 (new module; exporter.py UNCHANGED — spec invariant I4)
  ADR-3 (Supabase Storage bucket 'crops' for image URLs)
  spec capability: web-deployment (export pipeline)

Usage (from scripts/migrate_to_supabase.py or CLI):
    from src.modules.labeler.export_supabase import seed_crops_table, upload_crops_to_storage
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Iterator

from src.modules.labeler.db import _client, _supabase_url


# ---------------------------------------------------------------------------
# Source URL mapping — link each acta to its public Registraduría PDF
# ---------------------------------------------------------------------------

def _strip_timestamp(filename: str) -> str:
    """
    Reduce an E-14C PDF filename to its stable prefix (drops the trailing
    _<timestamp> token the Registraduría rotates on re-upload).

    E14_PRE_01_001_001_01_05_001_5002.pdf -> E14_PRE_01_001_001_01_05_001
    """
    return re.sub(r"_[0-9]+$", "", Path(filename).stem)


def build_source_url_index(urls_path: Path) -> dict[str, str]:
    """Build {stable_prefix: pdf_url} from data/e14c_urls.jsonl."""
    index: dict[str, str] = {}
    if not urls_path.exists():
        return index
    with open(urls_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            url = row.get("pdf_url", "")
            if url:
                index[_strip_timestamp(url.split("/")[-1])] = url
    return index


def source_url_for(pdf_path: str, url_index: dict[str, str]) -> str:
    """Return the public Registraduría URL for a local pdf_path, or '' if absent."""
    return url_index.get(_strip_timestamp(Path(pdf_path).name), "")


# ---------------------------------------------------------------------------
# Auto-skip detection (inline — mirrors server._should_auto_skip without import)
# We re-implement rather than import from server.py to avoid pulling in Flask.
# ---------------------------------------------------------------------------

def _should_auto_skip(png_path: Path) -> bool:
    """
    Return True if the crop PNG should be excluded from Supabase (auto-skip).

    Criteria (mirrors server._should_auto_skip):
      'blank'  — ink ratio < 2%  (empty cell)
      'border' — aspect ratio w/h < 0.15 (vertical line artefact)

    Returns False if opencv is unavailable or the file cannot be read
    (conservative: include the crop rather than silently skip it).
    """
    try:
        import cv2
        import numpy as np  # noqa: F401 — needed by cv2

        img = cv2.imread(str(png_path), cv2.IMREAD_GRAYSCALE)
        if img is None:
            return False
        h, w = img.shape
        if w == 0 or h == 0:
            return False

        _, bw = cv2.threshold(img, 120, 255, cv2.THRESH_BINARY_INV)
        ink_ratio = float(bw.sum() / 255) / (h * w)
        if ink_ratio < 0.02:
            return True  # blank

        if (w / h) < 0.15:
            return True  # vertical border line

    except Exception:
        pass

    return False


# ---------------------------------------------------------------------------
# Index reader — yields raw dicts from index.jsonl
# ---------------------------------------------------------------------------

def _iter_index(index_path: Path) -> Iterator[dict]:
    """Yield one dict per non-empty line in index.jsonl."""
    with open(index_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


# ---------------------------------------------------------------------------
# 5.1  seed_crops_table
# ---------------------------------------------------------------------------

def seed_crops_table(
    index_path: Path,
    crops_dir: Path,
    batch_size: int = 500,
    skip_auto_skip: bool = True,
) -> dict:
    """
    Read exporter.py's index.jsonl and insert rows into the Supabase `crops` table.

    Auto-skip crops (blank/border) are excluded when skip_auto_skip=True (spec I2).
    Already-existing rows are skipped via upsert on conflict (idempotent).

    Args:
        index_path:    Path to data/labels/crops/index.jsonl (exporter output).
        crops_dir:     Path to data/labels/crops/ (PNG files).
        batch_size:    Number of rows per Supabase batch insert (default 500).
        skip_auto_skip: When True, run _should_auto_skip() and exclude flagged crops.

    Returns:
        Dict with keys: inserted, skipped_auto_skip, skipped_existing, errors.
    """
    client = _client()

    # Fetch already-present crop_ids to avoid duplicate work
    existing_ids: set[str] = set()
    try:
        resp = client.table("crops").select("crop_id").execute()
        for row in resp.data or []:
            cid = row.get("crop_id")
            if cid:
                existing_ids.add(cid)
    except Exception:
        pass  # proceed without dedup — upsert will handle conflicts

    stats = {"inserted": 0, "skipped_auto_skip": 0, "skipped_existing": 0, "errors": 0}
    batch: list[dict] = []

    def _flush(rows: list[dict]) -> None:
        if not rows:
            return
        try:
            client.table("crops").upsert(rows, on_conflict="crop_id").execute()
            stats["inserted"] += len(rows)
        except Exception as exc:
            print(f"  ERROR inserting batch of {len(rows)}: {exc}")
            stats["errors"] += len(rows)

    for record in _iter_index(index_path):
        crop_id = record.get("crop_id", "")
        if not crop_id:
            continue

        if crop_id in existing_ids:
            stats["skipped_existing"] += 1
            continue

        if skip_auto_skip:
            png_path = crops_dir / f"{crop_id}.png"
            if png_path.exists() and _should_auto_skip(png_path):
                stats["skipped_auto_skip"] += 1
                continue

        row = {
            "crop_id": crop_id,
            "pdf_path": record.get("pdf_path", ""),
            "field_name": record.get("field_name", ""),
            "digit_index": record.get("digit_index", -1),
            "label_ocr": record.get("label_ocr"),
            "confidence": record.get("confidence"),
            "priority": record.get("priority", 2),
            # storage_url populated after upload_crops_to_storage()
            "annotation_count": 0,
            "status": "pending",
        }
        batch.append(row)
        existing_ids.add(crop_id)  # prevent re-adding in same run

        if len(batch) >= batch_size:
            _flush(batch)
            batch = []

    _flush(batch)
    return stats


# ---------------------------------------------------------------------------
# 5.2  upload_crops_to_storage
# ---------------------------------------------------------------------------

def upload_crops_to_storage(
    crops_dir: Path,
    bucket: str = "crops",
    rate_limit_rps: float = 10.0,
    update_storage_url: bool = True,
    client=None,
) -> dict:
    """
    Upload PNG files from crops_dir to the Supabase Storage bucket.

    Only uploads crops that exist in the `crops` table.  Skips files already
    present in Storage (checked via file existence in the bucket listing or
    storage_url already populated on the crops row).

    Args:
        crops_dir:         Directory containing {crop_id}.png files.
        bucket:            Supabase Storage bucket name (default 'crops').
        rate_limit_rps:    Max upload requests per second (default 10).
        update_storage_url: When True, update crops.storage_url after upload.
        client:            Optional Supabase client. Defaults to the module-level
                           anon client; pass a service_role client for bulk
                           uploads that must bypass Storage RLS.

    Returns:
        Dict with keys: uploaded, skipped_already_uploaded, errors.
    """
    client = client or _client()
    delay = 1.0 / rate_limit_rps

    stats = {"uploaded": 0, "skipped_already_uploaded": 0, "errors": 0}

    # Fetch crop_ids that need uploading (storage_url is NULL or empty).
    # PostgREST caps a single response at 1000 rows, so paginate explicitly.
    crops: list[dict] = []
    try:
        start, page = 0, 1000
        while True:
            resp = (
                client.table("crops")
                .select("crop_id, storage_url")
                .range(start, start + page - 1)
                .execute()
            )
            rows = resp.data or []
            crops.extend(rows)
            if len(rows) < page:
                break
            start += page
    except Exception as exc:
        print(f"  ERROR fetching crops list: {exc}")
        return stats

    for crop_row in crops:
        crop_id = crop_row.get("crop_id", "")
        if not crop_id:
            continue

        storage_url = crop_row.get("storage_url") or ""
        if storage_url:
            stats["skipped_already_uploaded"] += 1
            continue

        png_path = crops_dir / f"{crop_id}.png"
        if not png_path.exists():
            # PNG not on disk — skip silently
            continue

        object_key = f"{crop_id}.png"
        try:
            with open(png_path, "rb") as f:
                png_bytes = f.read()

            client.storage.from_(bucket).upload(
                path=object_key,
                file=png_bytes,
                file_options={"content-type": "image/png", "upsert": "false"},
            )
            stats["uploaded"] += 1

            if update_storage_url:
                base_url = _supabase_url.rstrip("/")
                public_url = f"{base_url}/storage/v1/object/public/{bucket}/{object_key}"
                client.table("crops").update(
                    {"storage_url": public_url}
                ).eq("crop_id", crop_id).execute()

        except Exception as exc:
            err_msg = str(exc).lower()
            if "already exists" in err_msg or "duplicate" in err_msg or "409" in err_msg:
                # File already in Storage — just update the URL
                stats["skipped_already_uploaded"] += 1
                if update_storage_url:
                    try:
                        base_url = _supabase_url.rstrip("/")
                        public_url = (
                            f"{base_url}/storage/v1/object/public/{bucket}/{object_key}"
                        )
                        client.table("crops").update(
                            {"storage_url": public_url}
                        ).eq("crop_id", crop_id).execute()
                    except Exception:
                        pass
            else:
                print(f"  ERROR uploading {crop_id}: {exc}")
                stats["errors"] += 1

        time.sleep(delay)

    return stats
