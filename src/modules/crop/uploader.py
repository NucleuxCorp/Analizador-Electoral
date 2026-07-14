"""Crop uploader — filter manifest by concordance state and upload to Supabase.

Public API:
    upload_state(manifest_path, state, max_upload, concurrent, bucket, supabase_client) -> UploadReport

Sort order: max(jsd_e14c_e14t, jsd_e14c_e14d, jsd_e14t_e14d) descending; nulls last.
When all JSD values are null for a record, it falls to the end in insertion order.

Warning: if > 50% of eligible records have all-null JSD, a warning is printed to stderr.
"""
from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Report type
# ---------------------------------------------------------------------------


@dataclass
class UploadReport:
    uploaded: int = 0
    skipped: int = 0
    errors: int = 0


# ---------------------------------------------------------------------------
# Internal helpers (public for testing)
# ---------------------------------------------------------------------------


def _max_jsd(record: dict) -> float | None:
    """Return the maximum non-null JSD value for a record, or None if all null."""
    vals = [
        record.get("jsd_e14c_e14t"),
        record.get("jsd_e14c_e14d"),
        record.get("jsd_e14t_e14d"),
    ]
    non_null = [v for v in vals if v is not None]
    return max(non_null) if non_null else None


def _sort_eligible(records: list[dict]) -> list[dict]:
    """Sort records by max JSD descending, nulls last, preserving insertion order within ties.

    Records with any non-null JSD come before records with all-null JSD.
    Within the non-null group, sorted by max-JSD descending.
    Within the all-null group, insertion order is preserved (stable sort guarantee).
    """
    def sort_key(record: dict) -> tuple:
        m = _max_jsd(record)
        if m is None:
            return (1, 0.0)  # nulls last
        return (0, -m)       # non-null: higher JSD first

    return sorted(records, key=sort_key)


def _filter_eligible(records: list[dict], state: str) -> list[dict]:
    """Return records matching state, not yet uploaded, with a local_path."""
    return [
        r for r in records
        if r.get("concordance_state") == state
        and not r.get("supabase_uploaded", False)
        and r.get("local_path") is not None
    ]


def _check_null_jsd_ratio(records: list[dict]) -> None:
    """Print a warning to stderr if > 50% of records have all-null JSD."""
    if not records:
        return
    null_count = sum(1 for r in records if _max_jsd(r) is None)
    ratio = null_count / len(records)
    if ratio > 0.50:
        print(
            f"uploader: warning — {null_count}/{len(records)} eligible records "
            f"({ratio:.0%}) have all-null JSD; upload ordering is effectively FIFO "
            f"within state. Consider enriching JSD scores before uploading.",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# Public: upload_state
# ---------------------------------------------------------------------------


async def upload_state(
    manifest_path: Path,
    state: str,
    max_upload: int | None = None,
    concurrent: int = 5,
    bucket: str = "crops",
    supabase_client: Any = None,
) -> UploadReport:
    """Upload manifest records for a given concordance state to Supabase.

    Args:
        manifest_path: Path to crop_manifest.jsonl.
        state: Concordance state to filter by (e.g. "discrepante", "divergente").
        max_upload: Maximum number of records to upload (None = unlimited).
        concurrent: Maximum number of concurrent upload coroutines.
        bucket: Supabase Storage bucket name.
        supabase_client: Optional Supabase client. If None, uses the module-level client.

    Returns:
        UploadReport with uploaded/skipped/errors counts.
    """
    from src.modules.crop.manifest import load_manifest, update_fields
    from src.modules.labeler.export_supabase import upload_crop_with_metadata

    report = UploadReport()

    if supabase_client is None:
        try:
            from src.modules.labeler.db import _client
            supabase_client = _client()
        except Exception as exc:
            print(f"uploader: error — cannot get Supabase client: {exc}", file=sys.stderr)
            report.errors += 1
            return report

    manifest = load_manifest(manifest_path)
    all_records = list(manifest.values())

    # Filter to eligible records
    eligible = _filter_eligible(all_records, state)

    if not eligible:
        return report

    # Warn if JSD data is mostly absent
    _check_null_jsd_ratio(eligible)

    # Sort by JSD descending, nulls last
    eligible = _sort_eligible(eligible)

    # Apply max_upload cap
    if max_upload is not None:
        eligible = eligible[:max_upload]

    semaphore = asyncio.Semaphore(concurrent)

    async def _upload_one(record: dict) -> None:
        crop_id = record["crop_id"]
        local_path_str = record["local_path"]
        local_path = Path(local_path_str)

        if not local_path.exists():
            report.skipped += 1
            return

        metadata = {
            "mesa_key": record.get("mesa_key"),
            "field_name": record.get("field_name"),
            "e14_type": record.get("e14_type"),
            "crop_type": record.get("crop_type"),
            "concordance_state": record.get("concordance_state"),
            "jsd_e14c_e14t": record.get("jsd_e14c_e14t"),
            "jsd_e14c_e14d": record.get("jsd_e14c_e14d"),
            "jsd_e14t_e14d": record.get("jsd_e14t_e14d"),
            "dept": record.get("dept"),
            "mpio": record.get("mpio"),
            "zona": record.get("zona"),
        }

        async with semaphore:
            try:
                url = await asyncio.to_thread(
                    upload_crop_with_metadata,
                    supabase_client,
                    bucket,
                    crop_id,
                    local_path,
                    metadata,
                    "crops/sv",
                )
                if url is not None:
                    update_fields(manifest_path, crop_id, supabase_uploaded=True)
                    report.uploaded += 1
                else:
                    report.errors += 1
            except Exception as exc:
                print(f"uploader: error uploading {crop_id}: {exc}", file=sys.stderr)
                report.errors += 1

    await asyncio.gather(*[_upload_one(r) for r in eligible])

    return report
