#!/usr/bin/env python3
"""upload_mesa_results — Upload cross-validation results to Supabase mesa_results table.

Reads data/cross_mesa_validation_{DD}.jsonl files (where DD is a 2-digit dept code),
classifies each row with a 5-level overall_status, builds a flat column dict, and
upserts batches of 500 into the Supabase mesa_results table.

Usage:
    python scripts/upload_mesa_results.py [--dry-run] [--dept DEPT_CODE] [--data-dir PATH]

Environment variables required:
    SUPABASE_URL              — Supabase project URL
    SUPABASE_SERVICE_ROLE_KEY — Service-role key (needs write access; bypasses RLS)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

# ---------------------------------------------------------------------------
# Path setup — ensure repo root is importable
# ---------------------------------------------------------------------------

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# Load .env from repo root if present
try:
    from dotenv import load_dotenv  # type: ignore[import]
    load_dotenv(REPO / ".env")
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FRAUD_MAX_DIFF: int = 30  # same threshold as form_extractor.py — defined locally
BATCH_SIZE: int = 500
TABLE_NAME: str = "mesa_results"
_JSONL_PATTERN: re.Pattern = re.compile(r"^cross_mesa_validation_(\d{2})\.jsonl$")


# ---------------------------------------------------------------------------
# Supabase client factory (injectable for testing)
# ---------------------------------------------------------------------------

def _get_supabase_client():
    """Return a Supabase client using SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY."""
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        raise SystemExit(
            "ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set.\n"
            "Set them in your shell or .env file before running this script."
        )
    from supabase import create_client  # type: ignore[import]
    return create_client(url, key)


# ---------------------------------------------------------------------------
# Internal helpers (shared by compute_overall_status and build_row)
# ---------------------------------------------------------------------------

_SOURCES: tuple[str, ...] = ("e14c", "e14t", "e14d")


def _derive_has_missing_fields(row: dict) -> bool:
    """Return True when a mesa has incomplete data.

    Conditions:
      - sources_ok_count < 2 (fewer than 2 sources available), OR
      - Any present source has URNA=0 while sum_votes > 0 (URNA_NO_DILIGENCIADA pattern).
    """
    sources_ok_count: int = (row.get("congruencia") or {}).get("sources_ok", 0)
    if sources_ok_count < 2:
        return True

    sources = row.get("sources") or {}
    aritmetica = row.get("aritmetica") or {}
    for src_key in _SOURCES:
        src = sources.get(src_key)
        if src is None:
            continue
        arith = aritmetica.get(src_key)
        if arith is None:
            continue
        urna = arith.get("urna", 0) or 0
        sum_votes = arith.get("sum_votes", 0) or 0
        if urna == 0 and sum_votes > 0:
            return True
    return False


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def compute_overall_status(row: dict, *, fraud_max_diff: int = FRAUD_MAX_DIFF) -> str:
    """Classify a cross-validation row into one of 5 priority levels.

    Priority order (first match wins):
      1. needs_review_large_delta — (cross_discrepancy AND tachon_suspicious) OR any |arith_delta| > fraud_max_diff
      2. discrepancy              — cross_discrepancy OR any arith_delta != 0
      3. warning                  — tachon_suspicious (no arithmetic issue)
      4. known_anomaly            — sources_ok_count < 2 OR any source URNA=0 while votes>0
      5. clean                    — none of the above

    Args:
        row:            A parsed JSONL row dict from cross_mesa_validation_{DD}.jsonl.
        fraud_max_diff: Threshold above which an arithmetic delta is classified as needs_review_large_delta.

    Returns:
        One of: 'needs_review_large_delta', 'discrepancy', 'warning', 'known_anomaly', 'clean'.
    """
    cong_summary = (row.get("congruencia") or {}).get("summary") or {}
    aritmetica = row.get("aritmetica") or {}
    tachones = row.get("tachones") or {}

    # --- Derived flags ---

    # cross_discrepancy: true when the 3 sources disagree on the same digit cell
    cross_discrepancy: bool = bool(cong_summary.get("cross_discrepancy"))

    # tachon_suspicious: true when ANY source has suspicious=True
    tachon_suspicious: bool = any(
        bool((t or {}).get("suspicious"))
        for t in tachones.values()
    )

    # Arithmetic deltas from all three sources (None when source absent)
    arith_deltas: list[int] = []
    for src_key in _SOURCES:
        arith = aritmetica.get(src_key)
        if arith is not None:
            delta = arith.get("delta")
            if delta is not None:
                arith_deltas.append(delta)

    any_delta_critical: bool = any(abs(d) > fraud_max_diff for d in arith_deltas)
    any_delta_nonzero: bool = any(d != 0 for d in arith_deltas)

    # --- Priority classification ---

    # 1. critical
    if (cross_discrepancy and tachon_suspicious) or any_delta_critical:
        return "needs_review_large_delta"

    # 2. discrepancy
    if cross_discrepancy or any_delta_nonzero:
        return "discrepancy"

    # 3. warning
    if tachon_suspicious:
        return "warning"

    # 4. known_anomaly
    if _derive_has_missing_fields(row):
        return "known_anomaly"

    # 5. clean
    return "clean"


# ---------------------------------------------------------------------------
# Row builder and file discovery
# ---------------------------------------------------------------------------

def build_row(jsonl_row: dict) -> dict:
    """Build a flat dict ready for Supabase upsert.

    Extracts all mesa_results columns from a cross_mesa_validation JSONL row,
    preserves the full raw row in raw_data, and computes overall_status.

    Args:
        jsonl_row: A parsed JSONL row from cross_mesa_validation_{DD}.jsonl.

    Returns:
        Flat dict matching the mesa_results table column contract.
    """
    dept = jsonl_row.get("dept", "")
    mpio = jsonl_row.get("mpio", "")
    zona = jsonl_row.get("zona", "")
    puesto = jsonl_row.get("puesto", "")
    mesa = jsonl_row.get("mesa", "")

    mesa_key = f"{dept}_{mpio}_{zona}_{puesto}_{mesa}"

    aritmetica = jsonl_row.get("aritmetica") or {}
    tachones = jsonl_row.get("tachones") or {}
    cong_summary = (jsonl_row.get("congruencia") or {}).get("summary") or {}
    sources = jsonl_row.get("sources") or {}

    def _src_status(src_key: str) -> str | None:
        src = sources.get(src_key)
        return src.get("status") if src else None

    def _arith_ok(src_key: str) -> bool | None:
        a = aritmetica.get(src_key)
        return a.get("ok") if a else None

    def _arith_delta(src_key: str) -> int | None:
        a = aritmetica.get(src_key)
        return a.get("delta") if a else None

    def _tachon_suspicious(src_key: str) -> bool:
        t = tachones.get(src_key)
        return bool(t.get("suspicious")) if t else False

    def _tachon_max_score(src_key: str) -> float | None:
        t = tachones.get(src_key)
        return t.get("max_score") if t else None

    # Aggregate tachon flags across all sources
    tachon_suspicious: bool = any(_tachon_suspicious(k) for k in _SOURCES)
    tachon_scores = [_tachon_max_score(k) for k in _SOURCES if _tachon_max_score(k) is not None]
    tachon_max_score: float | None = max(tachon_scores) if tachon_scores else None

    # sources_ok_count from congruencia
    sources_ok_count: int = (jsonl_row.get("congruencia") or {}).get("sources_ok", 0)

    # has_missing_fields — delegated to shared helper
    has_missing_fields: bool = _derive_has_missing_fields(jsonl_row)

    overall_status = compute_overall_status(jsonl_row)

    return {
        "mesa_key": mesa_key,
        "dept": dept,
        "mpio": mpio,
        "zona": zona,
        "puesto": puesto,
        "mesa": mesa,
        "sources_ok_count": sources_ok_count,
        "e14c_arith_ok": _arith_ok("e14c"),
        "e14t_arith_ok": _arith_ok("e14t"),
        "e14d_arith_ok": _arith_ok("e14d"),
        "e14c_arith_delta": _arith_delta("e14c"),
        "e14t_arith_delta": _arith_delta("e14t"),
        "e14d_arith_delta": _arith_delta("e14d"),
        "cross_discrepancy": bool(cong_summary.get("cross_discrepancy")),
        "tachon_suspicious": tachon_suspicious,
        "has_missing_fields": has_missing_fields,
        "overall_status": overall_status,
        "raw_data": jsonl_row,
    }


def discover_files(data_dir: Path) -> list[Path]:
    """Return sorted list of cross_mesa_validation_{DD}.jsonl files.

    Matches only filenames with a 2-digit dept code. Skips any file
    containing '_test' in the name (e.g. cross_mesa_validation_antioquia_test.jsonl).

    Args:
        data_dir: Directory to search.

    Returns:
        Sorted list of matching Path objects.
    """
    matches: list[Path] = []
    for f in sorted(data_dir.iterdir()):
        if not f.is_file():
            continue
        if not _JSONL_PATTERN.match(f.name):
            continue
        matches.append(f)
    return matches


def _iter_jsonl(path: Path) -> Iterator[dict]:
    """Yield parsed dicts from a JSONL file; skip blank lines and bad JSON."""
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


# ---------------------------------------------------------------------------
# Upload loop
# ---------------------------------------------------------------------------

@dataclass
class UploadSummary:
    """Summary of a completed upload run."""
    processed: int = 0
    upserted: int = 0
    skipped: int = 0
    errors: int = 0
    dry_run: bool = False
    dept_counts: dict = field(default_factory=dict)


def upload(
    data_dir: Path | None = None,
    dry_run: bool = False,
    dept: str | None = None,
) -> UploadSummary:
    """Discover JSONL files, classify rows, and upsert to Supabase mesa_results.

    Args:
        data_dir: Directory containing cross_mesa_validation files.
                  Defaults to REPO/data.
        dry_run:  If True, parse and classify rows but do NOT write to Supabase.
        dept:     Optional 2-digit dept code to restrict processing to one dept.

    Returns:
        UploadSummary with counts of processed / upserted / skipped / errors.
    """
    if data_dir is None:
        data_dir = REPO / "data"

    summary = UploadSummary(dry_run=dry_run)

    files = discover_files(data_dir)
    if dept:
        files = [f for f in files if _JSONL_PATTERN.match(f.name) and _JSONL_PATTERN.match(f.name).group(1) == dept]

    client = None
    if not dry_run:
        client = _get_supabase_client()

    for jsonl_path in files:
        match = _JSONL_PATTERN.match(jsonl_path.name)
        dept_code = match.group(1) if match else "??"
        print(f"Processing dept {dept_code}: {jsonl_path.name}")

        batch: list[dict] = []
        file_processed = 0
        file_upserted = 0
        file_errors = 0

        def _flush_batch():
            nonlocal file_upserted, file_errors
            if not batch:
                return
            rows_to_send = list(batch)
            batch.clear()
            try:
                client.table(TABLE_NAME).upsert(
                    rows_to_send,
                    on_conflict="mesa_key",
                ).execute()
                file_upserted += len(rows_to_send)
            except Exception as exc:
                print(f"  ERROR upserting batch: {exc}")
                file_errors += len(rows_to_send)

        for raw in _iter_jsonl(jsonl_path):
            try:
                row = build_row(raw)
            except Exception as exc:
                print(f"  ERROR building row: {exc}")
                file_errors += 1
                summary.errors += 1
                continue

            file_processed += 1
            summary.processed += 1

            if dry_run:
                summary.skipped += 1
                continue

            batch.append(row)
            if len(batch) >= BATCH_SIZE:
                _flush_batch()

        if not dry_run:
            _flush_batch()

        summary.upserted += file_upserted
        summary.errors += file_errors
        summary.dept_counts[dept_code] = {
            "processed": file_processed,
            "upserted": file_upserted,
            "errors": file_errors,
        }

        if dry_run:
            print(f"  [dry-run] would upsert {file_processed} rows")
        else:
            print(f"  upserted={file_upserted} errors={file_errors}")

    print(
        f"\nDone. processed={summary.processed} upserted={summary.upserted} "
        f"skipped={summary.skipped} errors={summary.errors}"
    )
    return summary


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Upload cross-validation results to Supabase mesa_results table."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and classify rows without writing to Supabase.",
    )
    parser.add_argument(
        "--dept",
        metavar="CODE",
        help="Restrict to a specific 2-digit dept code (e.g. 01).",
    )
    parser.add_argument(
        "--data-dir",
        metavar="PATH",
        default=str(REPO / "data"),
        help="Directory containing cross_mesa_validation_*.jsonl files.",
    )
    args = parser.parse_args()

    upload(
        data_dir=Path(args.data_dir),
        dry_run=args.dry_run,
        dept=args.dept,
    )
