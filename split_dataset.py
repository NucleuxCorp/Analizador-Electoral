"""Data split logic: test = confirmed, train/val = pool, 90/10 stratified."""
from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path
from typing import Iterable

# Replicate label vocabulary from src/modules/labeler/db.py so the split module
# can be imported without depending on Supabase client initialisation.
ZERO_VARIANTS = {"*", "-", ".", "+", "o", "O"}
SKIP_LABEL = "_skip"
VALID_LABELS = {str(i) for i in range(10)}


def load_manifest(path: str | Path) -> list[dict]:
    """Load JSONL, dedupe by crop_id keeping the most recent label.

    The manifest is expected to be ordered by `ts` DESC (most recent first),
    which matches the Supabase query in fetch_confirmed_crops.py. The first
    occurrence of a crop_id wins.
    """
    path = Path(path)
    if not path.exists():
        return []

    records: list[dict] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            crop_id = record.get("crop_id")
            if not crop_id or crop_id in seen:
                continue
            seen.add(crop_id)
            records.append(record)
    return records


def normalize_label(label: str | None) -> str | None:
    """Map zero-variant glyphs to '0' and filter invalid labels.

    Valid output classes are '0'..'9'. Anything else (including _skip) is None.
    """
    if label is None:
        return None
    value = str(label).strip()
    if value == SKIP_LABEL:
        return None
    value = "0" if value in ZERO_VARIANTS else value
    return value if value in VALID_LABELS else None


def _canonical_label(record: dict) -> str | None:
    """Return the canonical label for a record, preferring confirmed_label."""
    label = record.get("confirmed_label")
    if label is None:
        label = record.get("label_human")
    return normalize_label(label)


def stratified_split(records: list[dict], val_ratio: float = 0.1, seed: int = 42) -> tuple[list[dict], list[dict]]:
    """Split records into train/val preserving per-class proportions.

    Records must already be deduped and canonicalised. The split is
    deterministic given the same seed and input order.
    """
    rng = random.Random(seed)

    # Group by canonical label.
    by_class: dict[str, list[dict]] = {}
    for rec in records:
        label = _canonical_label(rec)
        if label is None:
            continue
        by_class.setdefault(label, []).append(rec)

    train: list[dict] = []
    val: list[dict] = []
    for label, items in by_class.items():
        # Shuffle a copy so input order is not mutated.
        shuffled = items[:]
        rng.shuffle(shuffled)
        n_val = max(1, int(len(shuffled) * val_ratio)) if len(shuffled) >= 10 else 0
        if n_val >= len(shuffled):
            n_val = len(shuffled) // 10 or 0
        val.extend(shuffled[:n_val])
        train.extend(shuffled[n_val:])

    # Final global shuffle for training efficiency (still deterministic).
    rng.shuffle(train)
    rng.shuffle(val)
    return train, val


def check_min_per_class(records: list[dict], min_per_class: int = 50) -> None:
    """Abort with RuntimeError if any class has fewer than min_per_class examples."""
    counts = Counter()
    for rec in records:
        label = _canonical_label(rec)
        if label is not None:
            counts[label] += 1

    for cls in sorted(VALID_LABELS):
        count = counts.get(cls, 0)
        if count < min_per_class:
            raise RuntimeError(f"Class {cls} has {count} samples (< {min_per_class} threshold)")


def cache_split(
    test_crop_ids: Iterable[str],
    train_records: Iterable[dict],
    val_records: Iterable[dict],
    output_dir: str | Path,
) -> None:
    """Cache test ids and train/val assignments to disk for reproducibility."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    test_ids_path = output_dir / "test_crop_ids.json"
    with test_ids_path.open("w", encoding="utf-8") as f:
        json.dump(sorted(test_crop_ids), f, indent=2, ensure_ascii=False)

    split_map: dict[str, str] = {}
    for rec in train_records:
        crop_id = rec.get("crop_id")
        if crop_id:
            split_map[crop_id] = "train"
    for rec in val_records:
        crop_id = rec.get("crop_id")
        if crop_id:
            split_map[crop_id] = "val"

    split_path = output_dir / "train_val_split.json"
    with split_path.open("w", encoding="utf-8") as f:
        json.dump(split_map, f, indent=2, ensure_ascii=False)


def load_or_create_split(
    confirmed_manifest_path: str | Path,
    train_pool_manifest_path: str | Path,
    cache_dir: str | Path,
) -> tuple[list[str], list[dict], list[dict]]:
    """Main entry point: load manifests, create split, cache it.

    Returns:
        (test_crop_ids, train_records, val_records)
    """
    cache_dir = Path(cache_dir)
    test_ids_path = cache_dir / "test_crop_ids.json"
    split_path = cache_dir / "train_val_split.json"

    if test_ids_path.exists() and split_path.exists():
        with test_ids_path.open("r", encoding="utf-8") as f:
            test_crop_ids = json.load(f)
        with split_path.open("r", encoding="utf-8") as f:
            split_map = json.load(f)

        pool = load_manifest(train_pool_manifest_path)
        train = [r for r in pool if split_map.get(r.get("crop_id")) == "train"]
        val = [r for r in pool if split_map.get(r.get("crop_id")) == "val"]
        return test_crop_ids, train, val

    confirmed = load_manifest(confirmed_manifest_path)
    test_crop_ids = [r["crop_id"] for r in confirmed]

    pool = load_manifest(train_pool_manifest_path)
    confirmed_ids = set(test_crop_ids)
    pool = [r for r in pool if r.get("crop_id") not in confirmed_ids]

    # Normalize labels and drop records that do not map to a valid class.
    pool = [
        {**r, "label_human": normalize_label(r.get("label_human"))}
        for r in pool
        if normalize_label(r.get("label_human")) is not None
    ]

    check_min_per_class(pool, min_per_class=50)
    train, val = stratified_split(pool, val_ratio=0.1, seed=42)

    cache_split(test_crop_ids, train, val, cache_dir)
    return test_crop_ids, train, val


if __name__ == "__main__":
    print("Use fetch_confirmed_crops.py to generate manifests, then import load_or_create_split.")
