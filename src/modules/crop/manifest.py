"""Crop manifest I/O — append-only JSONL with last-wins merge semantics.

The manifest is an append-only log where each line is a JSON object
keyed by crop_id. Readers merge on load: later records overwrite earlier
records for the same crop_id. This makes each subcommand O(1) per update
(append a delta) instead of O(N) (rewrite the file).

Public API:
    make_crop_id(mesa_key, field_name, subcell_pos, e14_type, crop_type) -> str
    load_manifest(path) -> dict[str, dict]
    append_records(path, records) -> None
    update_fields(path, crop_id, **fields) -> None
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Callable, Iterator


# ---------------------------------------------------------------------------
# Deterministic crop_id
# ---------------------------------------------------------------------------


def make_crop_id(
    mesa_key: str,
    field_name: str,
    subcell_pos: int | None,
    e14_type: str,
    crop_type: str,
) -> str:
    """Derive a deterministic 32-hex-char crop_id from the record's natural key.

    Using SHA-256 truncated to 32 chars (128 bits) makes classify idempotent:
    re-running produces the same crop_id, so load_manifest's last-wins merge
    deduplicates for free without an in-memory seen-set.

    Args:
        mesa_key: The mesa identifier string.
        field_name: Canonical field label (e.g. "C1_CEPEDA").
        subcell_pos: Subcell position 0-2, or None for fullcell records.
        e14_type: One of "e14c", "e14t", "e14d".
        crop_type: "subcell" or "fullcell".

    Returns:
        A 32-character lowercase hex string.
    """
    raw = f"{mesa_key}|{field_name}|{subcell_pos}|{e14_type}|{crop_type}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Manifest I/O
# ---------------------------------------------------------------------------


def load_manifest(path: Path) -> dict[str, dict]:
    """Read the manifest JSONL and merge records by crop_id (last wins).

    If the file does not exist or is empty, returns an empty dict.
    Logs manifest size and unique crop count on each call for monitoring.

    Args:
        path: Path to crop_manifest.jsonl.

    Returns:
        Dict of crop_id → merged record dict.
    """
    merged: dict[str, dict] = {}

    if not path.exists():
        return merged

    line_count = 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            line_count += 1
            try:
                record = json.loads(line)
                crop_id = record.get("crop_id")
                if not crop_id:
                    continue
                if crop_id in merged:
                    merged[crop_id].update(record)
                else:
                    merged[crop_id] = dict(record)
            except json.JSONDecodeError as exc:
                print(
                    f"manifest: warning — skipping malformed line: {exc}",
                    file=sys.stderr,
                )

    print(
        f"manifest: loaded {line_count} lines, {len(merged)} unique crop_ids from {path.name}",
        file=sys.stderr,
    )
    return merged


def append_records(path: Path, records: list[dict]) -> None:
    """Append new records to the manifest, skipping crop_ids already present.

    Reads existing crop_ids from the file first, then writes only records
    whose crop_id is not yet in the file.

    Args:
        path: Path to crop_manifest.jsonl (created if absent).
        records: List of record dicts; each must have a "crop_id" key.
    """
    if not records:
        return

    # Collect existing crop_ids to avoid duplicates
    existing_ids: set[str] = set()
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    cid = rec.get("crop_id")
                    if cid:
                        existing_ids.add(cid)
                except json.JSONDecodeError:
                    pass

    new_records = [r for r in records if r.get("crop_id") not in existing_ids]
    if not new_records:
        return

    with path.open("a", encoding="utf-8") as fh:
        for record in new_records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def update_fields(path: Path, crop_id: str, **fields) -> None:
    """Append a delta record containing crop_id and the given fields.

    The delta is merged on the next load_manifest() call via last-wins
    semantics. This keeps update cost O(1) regardless of manifest size.

    Args:
        path: Path to crop_manifest.jsonl.
        crop_id: The crop to update.
        **fields: Field name → new value pairs to write.
    """
    delta = {"crop_id": crop_id, **fields}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(delta, ensure_ascii=False) + "\n")


def iter_manifest(
    path: Path,
    filter_fn: Callable[[dict], bool] | None = None,
) -> Iterator[dict]:
    """Yield merged manifest records, optionally filtered.

    Loads the full manifest via load_manifest() and yields records.
    If filter_fn is provided, only records where filter_fn(record) is
    True are yielded.

    Args:
        path: Path to crop_manifest.jsonl.
        filter_fn: Optional predicate; records failing it are skipped.

    Yields:
        Merged record dicts.
    """
    manifest = load_manifest(path)
    for record in manifest.values():
        if filter_fn is None or filter_fn(record):
            yield record
