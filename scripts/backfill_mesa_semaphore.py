"""
backfill_mesa_semaphore.py — One-shot backfill for crops.mesa_key_mr.

Reads data/crop_manifest.jsonl, derives mesa_key_mr from the 5 coord fields
(dept, mpio, zona, puesto, mesa) for each row, and also normalizes field_name
using the same mapping as export_supabase._normalize_field_name().

Rows missing any of the 5 coord fields are skipped and logged to
data/backfill_mesa_key_mr_skipped.jsonl.

The script is idempotent: re-running produces no duplicates or data loss
(same input → same output; Supabase upsert overwrites with identical values).

Usage:
    python scripts/backfill_mesa_semaphore.py [--dry-run] [--batch-size 500]

Environment variables (same as the labeler portal):
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY  (preferred — bypasses RLS)
    SUPABASE_ANON_KEY          (fallback)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Resolve project root (two levels up from this script)
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
_MANIFEST_PATH = _PROJECT_ROOT / "data" / "crop_manifest.jsonl"
_SKIPPED_LOG = _PROJECT_ROOT / "data" / "backfill_mesa_key_mr_skipped.jsonl"


# ---------------------------------------------------------------------------
# Field-name normalisation (mirrors export_supabase._normalize_field_name)
# ---------------------------------------------------------------------------

def _normalize_field_name(name: str) -> str:
    """Normalize raw field_name to canonical DB values."""
    if not name:
        return name
    m = re.match(r"^C(\d+)_", name)
    if m:
        return f"candidato_{m.group(1)}"
    _MAP: dict[str, str] = {
        "URNA": "total_urna",
        "SUMA_TOTAL": "suma_total",
        "VOTANTES": "total_votantes",
        "BLANCO": "blanco",
        "NULOS": "nulos",
        "NO_MARCADOS": "no_marcados",
        "INCINER": "incineradas",
    }
    return _MAP.get(name, name)


# ---------------------------------------------------------------------------
# Supabase client
# ---------------------------------------------------------------------------

def _build_client():
    """Build a Supabase service-role client from environment variables."""
    url = os.environ.get("SUPABASE_URL", "").strip()
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    anon_key = os.environ.get("SUPABASE_ANON_KEY", "").strip()
    key = service_key or anon_key

    if not url or not key:
        print(
            "ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (or SUPABASE_ANON_KEY) "
            "must be set.",
            file=sys.stderr,
        )
        sys.exit(1)

    from supabase import create_client  # type: ignore[import]
    return create_client(url, key)


# ---------------------------------------------------------------------------
# Main backfill logic
# ---------------------------------------------------------------------------

def _iter_manifest(path: Path):
    """Yield (crop_id, mesa_key_mr, normalized_field_name) tuples from the manifest."""
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"  WARN: skipping malformed JSON at line {lineno}: {exc}")
                continue
            yield row


def _build_key(row: dict) -> str | None:
    """Return mesa_key_mr string or None if any coord field is missing."""
    dept = str(row.get("dept") or "").strip()
    mpio = str(row.get("mpio") or "").strip()
    zona = str(row.get("zona") or "").strip()
    puesto = str(row.get("puesto") or "").strip()
    mesa = str(row.get("mesa") or "").strip()
    if not (dept and mpio and zona and puesto and mesa):
        return None
    return f"{dept}_{mpio}_{zona}_{puesto}_{mesa}"


def run_backfill(
    manifest_path: Path,
    client,
    batch_size: int = 500,
    dry_run: bool = False,
) -> None:
    """
    Read manifest, build update payloads, and batch-upsert into Supabase.

    Each row update sets:
      - mesa_key_mr: derived from coord fields
      - field_name: normalized via _normalize_field_name

    Rows missing coord fields are skipped and logged.
    """
    skipped_path = _SKIPPED_LOG
    skipped_path.parent.mkdir(parents=True, exist_ok=True)

    n_total = 0
    n_skipped = 0
    n_updated = 0
    n_errors = 0

    # Deduplicate by crop_id (manifest may have multiple rows per crop for
    # different e14_type values — we only need to set mesa_key_mr once).
    seen_crop_ids: set[str] = set()

    batch: list[dict] = []
    skipped_lines: list[dict] = []

    def _flush_batch(rows: list[dict]) -> int:
        if not rows or dry_run:
            return len(rows)
        count = 0
        for payload in rows:
            try:
                client.table("crops").update(
                    {
                        "mesa_key_mr": payload["mesa_key_mr"],
                        "field_name": payload["field_name"],
                    }
                ).eq("crop_id", payload["crop_id"]).execute()
                count += 1
            except Exception as exc:
                print(f"  ERROR updating crop_id={payload['crop_id']}: {exc}")
        return count

    for row in _iter_manifest(manifest_path):
        n_total += 1
        crop_id = str(row.get("crop_id") or "").strip()
        if not crop_id:
            continue

        if crop_id in seen_crop_ids:
            continue  # already queued for this crop

        mesa_key_mr = _build_key(row)
        if mesa_key_mr is None:
            n_skipped += 1
            skipped_lines.append({"crop_id": crop_id, "reason": "missing_coord_fields", "row": row})
            continue

        raw_field_name = str(row.get("field_name") or "")
        normalized_field_name = _normalize_field_name(raw_field_name)

        seen_crop_ids.add(crop_id)
        batch.append({
            "crop_id": crop_id,
            "mesa_key_mr": mesa_key_mr,
            "field_name": normalized_field_name,
        })

        if len(batch) >= batch_size:
            updated_in_batch = _flush_batch(batch)
            n_updated += updated_in_batch
            print(f"  flushed batch: {updated_in_batch} rows (total so far: {n_updated})")
            batch = []

    # Final partial batch
    if batch:
        updated_in_batch = _flush_batch(batch)
        n_updated += updated_in_batch
        print(f"  flushed final batch: {updated_in_batch} rows")

    # Write skipped log
    if skipped_lines:
        with open(skipped_path, "w", encoding="utf-8") as fh:
            for entry in skipped_lines:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(f"  Skipped rows logged to: {skipped_path}")

    print()
    print("=" * 50)
    print(f"Backfill complete {'(DRY RUN — no writes)' if dry_run else ''}")
    print(f"  Manifest rows read:  {n_total}")
    print(f"  Unique crops queued: {len(seen_crop_ids) + n_skipped}")
    print(f"  Updated:             {n_updated}")
    print(f"  Skipped (no coords): {n_skipped}")
    if n_errors:
        print(f"  Errors:              {n_errors}")
    print("=" * 50)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill crops.mesa_key_mr from crop_manifest.jsonl")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=_MANIFEST_PATH,
        help=f"Path to crop_manifest.jsonl (default: {_MANIFEST_PATH})",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Number of rows per Supabase update batch (default: 500)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse manifest and report counts without writing to Supabase",
    )
    args = parser.parse_args()

    if not args.manifest.exists():
        print(f"ERROR: manifest not found: {args.manifest}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print("DRY RUN — connecting to Supabase for validation only (no writes).")
        client = _build_client()
    else:
        client = _build_client()

    print(f"Reading manifest: {args.manifest}")
    print(f"Batch size: {args.batch_size}")
    print()

    run_backfill(
        manifest_path=args.manifest,
        client=client,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
