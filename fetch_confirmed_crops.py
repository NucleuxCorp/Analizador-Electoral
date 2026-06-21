#!/usr/bin/env python3
"""Fetch confirmed crops (test set) + train pool from Supabase to local disk."""
from __future__ import annotations

import json
import os
import shutil
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

# Replicate label vocabulary from src/modules/labeler/db.py so this script can
# run standalone without importing the whole labeler module.
ZERO_VARIANTS = {"*", "-", ".", "+", "o", "O"}
SKIP_LABEL = "_skip"
VALID_LABELS = {str(i) for i in range(10)}

ROOT = Path(__file__).resolve().parent
LABELS_DIR = ROOT / "data" / "labels"
DIGITS_DIR = LABELS_DIR / "digits"
CONFIRMED_DIR = LABELS_DIR / "confirmed"
TRAIN_POOL_DIR = LABELS_DIR / "train_pool"
CONFIRMED_MANIFEST = LABELS_DIR / "confirmed_manifest.jsonl"
TRAIN_POOL_MANIFEST = LABELS_DIR / "train_pool_manifest.jsonl"

PAGE_SIZE = 1000
MAX_RETRIES = 3


def _load_env() -> None:
    """Load environment variables from a .env file if present."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    with env_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _create_supabase_client() -> Any:
    """Create a Supabase client using service_role key, falling back to anon key."""
    url = os.environ.get("SUPABASE_URL", "").strip()
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    anon_key = os.environ.get("SUPABASE_ANON_KEY", "").strip()
    key = service_key or anon_key

    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_ANON_KEY) must be set."
        )

    if not service_key and anon_key:
        print("WARNING: SUPABASE_SERVICE_ROLE_KEY not set; falling back to SUPABASE_ANON_KEY.")

    from supabase import create_client  # type: ignore[import]

    return create_client(url, key)


def _normalize_label(label: str | None) -> str | None:
    """Collapse zero-variant glyphs to '0' and drop invalid labels."""
    if label is None:
        return None
    value = str(label).strip()
    if value == SKIP_LABEL:
        return None
    value = "0" if value in ZERO_VARIANTS else value
    return value if value in VALID_LABELS else None


def _storage_url_for(crop_id: str) -> str:
    """Return the public Supabase Storage URL for a crop PNG."""
    base_url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    if not base_url:
        raise RuntimeError("SUPABASE_URL is not set")
    return f"{base_url}/storage/v1/object/public/crops/{crop_id}.png"


def _dedupe_labels_by_crop_id(rows: list[dict]) -> dict[str, dict]:
    """Keep the first (most recent) label per crop_id.

    Rows are expected to be ordered by `ts` DESC.
    """
    deduped: dict[str, dict] = {}
    for row in rows:
        crop_id = row.get("crop_id")
        if crop_id and crop_id not in deduped:
            deduped[crop_id] = row
    return deduped


def fetch_confirmed_crops(client: Any) -> list[dict]:
    """Fetch all crops with status='confirmed' from Supabase."""
    records: list[dict] = []
    start = 0
    while True:
        response = (
            client.table("crops")
            .select("crop_id, confirmed_label, field_name, pdf_path")
            .eq("status", "confirmed")
            .not_.is_("confirmed_label", "null")
            .order("crop_id", desc=False)
            .range(start, start + PAGE_SIZE - 1)
            .execute()
        )
        page = response.data or []
        if not page:
            break
        records.extend(page)
        if len(page) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return records


def fetch_train_pool(client: Any, confirmed_ids: set[str]) -> list[dict]:
    """Fetch crops for train/val — exclude confirmed, needs_third, disputed, _skip."""
    # Phase 1: fetch all non-skip labels ordered by timestamp DESC (most recent first).
    all_labels: list[dict] = []
    start = 0
    while True:
        response = (
            client.table("labels")
            .select("crop_id, label_human, ts")
            .neq("label_human", SKIP_LABEL)
            .order("ts", desc=True)
            .range(start, start + PAGE_SIZE - 1)
            .execute()
        )
        page = response.data or []
        if not page:
            break
        all_labels.extend(page)
        if len(page) < PAGE_SIZE:
            break
        start += PAGE_SIZE

    # Phase 2: dedupe by crop_id keeping most recent label.
    deduped = _dedupe_labels_by_crop_id(all_labels)

    # Phase 3: drop confirmed crop_ids and apply status filter.
    candidate_ids = [cid for cid in deduped if cid not in confirmed_ids]
    if not candidate_ids:
        return []

    # Batch status lookup in chunks to avoid huge IN clauses.
    excluded_statuses = {"needs_third", "disputed"}
    valid: dict[str, dict] = {}
    chunk_size = 500
    for i in range(0, len(candidate_ids), chunk_size):
        chunk = candidate_ids[i : i + chunk_size]
        response = (
            client.table("crops")
            .select("crop_id, status")
            .in_("crop_id", chunk)
            .execute()
        )
        for row in response.data or []:
            if row.get("status") not in excluded_statuses:
                crop_id = row["crop_id"]
                label_row = deduped[crop_id]
                valid[crop_id] = {
                    "crop_id": crop_id,
                    "label_human": label_row["label_human"],
                    "ts": label_row.get("ts"),
                }

    return list(valid.values())


def _download_with_retry(url: str, max_retries: int = MAX_RETRIES) -> bytes | None:
    """Download bytes from url with exponential backoff."""
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                return resp.read()
        except urllib.error.URLError as exc:
            if attempt == max_retries - 1:
                return None
            wait = 2 ** attempt
            print(f"  download attempt {attempt + 1} failed ({exc}); retrying in {wait}s...")
            time.sleep(wait)
    return None


def copy_crop_local(
    crop_id: str,
    label: str,
    dest_dir: Path,
    client: Any | None = None,
) -> Path | None:
    """Copy crop from local digits/ or download from Supabase Storage."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"img_{crop_id}.png"

    if dest_path.exists():
        return dest_path

    # Local-first: try the existing digits layout.
    local_src = DIGITS_DIR / label / f"img_{crop_id}.png"
    if local_src.exists():
        shutil.copy2(local_src, dest_path)
        return dest_path

    # Fallback: public Supabase Storage URL.
    url = _storage_url_for(crop_id)
    data = _download_with_retry(url)
    if data is None:
        return None

    dest_path.write_bytes(data)
    return dest_path


def write_manifest(records: list[dict], path: Path) -> None:
    """Write JSONL manifest, one JSON object per line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _log_distribution(records: list[dict], label_key: str) -> None:
    """Print per-class distribution for a manifest."""
    counts: Counter = Counter()
    for rec in records:
        label = _normalize_label(rec.get(label_key))
        if label is not None:
            counts[label] += 1
        else:
            counts["<invalid>"] += 1
    print(f"  Total: {len(records)}")
    for cls in sorted(VALID_LABELS):
        print(f"    class {cls}: {counts.get(cls, 0)}")


def _materialize_crops(
    records: list[dict],
    label_key: str,
    dest_root: Path,
    client: Any,
) -> tuple[list[dict], list[str]]:
    """Copy/download crops and build manifest records.

    Returns (manifest_records, missing_crop_ids).
    """
    manifest_records: list[dict] = []
    missing: list[str] = []

    for idx, rec in enumerate(records, start=1):
        crop_id = rec["crop_id"]
        raw_label = rec.get(label_key)
        label = _normalize_label(raw_label)
        if label is None:
            missing.append(crop_id)
            continue

        dest_dir = dest_root / label
        local_path = copy_crop_local(crop_id, label, dest_dir, client)
        if local_path is None:
            missing.append(crop_id)
            continue

        manifest_record = dict(rec)
        manifest_record["local_path"] = str(local_path.relative_to(ROOT))
        manifest_records.append(manifest_record)

        if idx % 500 == 0:
            print(f"  processed {idx}/{len(records)}")

    return manifest_records, missing


def main() -> None:
    _load_env()
    client = _create_supabase_client()

    CONFIRMED_DIR.mkdir(parents=True, exist_ok=True)
    TRAIN_POOL_DIR.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Phase A: confirmed crops (test set)
    # ------------------------------------------------------------------
    print("Fetching confirmed crops (test set)...")
    confirmed = fetch_confirmed_crops(client)
    print(f"  fetched {len(confirmed)} confirmed rows")

    confirmed_manifest, confirmed_missing = _materialize_crops(
        confirmed, "confirmed_label", CONFIRMED_DIR, client
    )
    write_manifest(confirmed_manifest, CONFIRMED_MANIFEST)
    print(f"Confirmed manifest written: {CONFIRMED_MANIFEST} ({len(confirmed_manifest)} crops)")
    _log_distribution(confirmed_manifest, "confirmed_label")
    if confirmed_missing:
        print(f"  missing: {len(confirmed_missing)} crops")
        for cid in confirmed_missing[:20]:
            print(f"    - {cid}")
        if len(confirmed_missing) > 20:
            print(f"    ... and {len(confirmed_missing) - 20} more")

    # ------------------------------------------------------------------
    # Phase B: train pool
    # ------------------------------------------------------------------
    print("\nFetching train pool...")
    confirmed_ids = {r["crop_id"] for r in confirmed}
    train_pool = fetch_train_pool(client, confirmed_ids)
    print(f"  fetched {len(train_pool)} train-pool rows")

    train_manifest, train_missing = _materialize_crops(
        train_pool, "label_human", TRAIN_POOL_DIR, client
    )
    write_manifest(train_manifest, TRAIN_POOL_MANIFEST)
    print(f"Train pool manifest written: {TRAIN_POOL_MANIFEST} ({len(train_manifest)} crops)")
    _log_distribution(train_manifest, "label_human")
    if train_missing:
        print(f"  missing: {len(train_missing)} crops")
        for cid in train_missing[:20]:
            print(f"    - {cid}")
        if len(train_missing) > 20:
            print(f"    ... and {len(train_missing) - 20} more")

    # ------------------------------------------------------------------
    # Phase C: split cache (delegated to split_dataset for reuse)
    # ------------------------------------------------------------------
    print("\nCaching train/val split...")
    from split_dataset import load_or_create_split

    test_ids, train_records, val_records = load_or_create_split(
        CONFIRMED_MANIFEST, TRAIN_POOL_MANIFEST, LABELS_DIR
    )
    print(f"  test: {len(test_ids)}  train: {len(train_records)}  val: {len(val_records)}")
    print("Done.")


if __name__ == "__main__":
    main()
