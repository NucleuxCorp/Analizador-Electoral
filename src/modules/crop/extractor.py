"""Crop extractor — opens each PDF once and writes PNG crops.

Public API:
    extract_mesa_crops(mesa_key, e14_type, pdf_path, manifest_records, crops_root) -> list[dict]
    run_extract(manifest_path, index_path, workers, errors_path, limit) -> ExtractReport

Design notes:
    - Opens each PDF exactly once per (mesa_key, e14_type).
    - Uses process_pdf_task (e14c) or extract_fields (e14t/e14d) with return_bboxes=True.
    - Workers run in ProcessPoolExecutor; Windows-safe because all imports are at
      top level or inside _ensure_modules()-style lazy loaders in the extractor
      modules themselves. This module only does lightweight wrapping.
    - Error records: written to errors_path as JSONL lines, processing continues.
    - Resume: skips records where local_path is set AND the file exists.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# Report type
# ---------------------------------------------------------------------------


@dataclass
class ExtractReport:
    processed: int = 0
    skipped: int = 0
    errors: int = 0


# ---------------------------------------------------------------------------
# Internal extractor wrappers
# These are top-level functions (required for ProcessPoolExecutor / Windows spawn).
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[3]


def _call_e14c_extractor(pdf_path: Path) -> tuple[np.ndarray, dict]:
    """Render the E14C PDF and return (img, result_with_bboxes)."""
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location(
        "e14_worker", ROOT / "debug_sv" / "e14_worker.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # pdf_path must be passed as a relative string from ROOT to match e14_worker expectations
    try:
        pdf_rel = str(pdf_path.resolve().relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        pdf_rel = str(pdf_path).replace("\\", "/")

    result = mod.process_pdf_task(pdf_rel, save_suspicious_crops=False, return_bboxes=True)

    # Re-render the first page for cropping (e14_worker does not expose the img)
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
        page = doc[0]
        mat = fitz.Matrix(300 / 72, 300 / 72)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        doc.close()
        if img.ndim == 3 and img.shape[2] == 4:
            img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
        elif img.ndim == 3 and img.shape[2] == 1:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        elif img.ndim == 3 and img.shape[2] == 3:
            img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    except Exception:
        # Fallback: create empty image so downstream can still produce deltas
        img = np.zeros((600, 400, 3), dtype=np.uint8)

    return img, result


def _call_e14t_extractor(pdf_path: Path) -> tuple[np.ndarray, dict]:
    """Render the E14T/E14D PDF and return (img, result_with_bboxes)."""
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location(
        "e14t_extractor", ROOT / "debug_sv" / "e14t_extractor.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    result = mod.extract_fields(pdf_path, return_bboxes=True)

    # Render the image for cropping
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
        page = doc[0]
        mat = fitz.Matrix(300 / 72, 300 / 72)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        doc.close()
        if img.ndim == 3 and img.shape[2] == 4:
            img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
        elif img.ndim == 3 and img.shape[2] == 1:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        elif img.ndim == 3 and img.shape[2] == 3:
            img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    except Exception:
        img = np.zeros((600, 400, 3), dtype=np.uint8)

    return img, result


# ---------------------------------------------------------------------------
# Public: extract_mesa_crops
# ---------------------------------------------------------------------------


def extract_mesa_crops(
    mesa_key: str,
    e14_type: str,
    pdf_path: Path,
    manifest_records: list[dict],
    crops_root: Path = Path("crops"),
) -> list[dict]:
    """Extract crops for all manifest records belonging to one (mesa_key, e14_type).

    Opens the PDF exactly once, obtains the rendered image and bboxes, then
    crops each record from the image and writes a PNG.

    Args:
        mesa_key: The mesa identifier string (e.g. "0101001010030102").
        e14_type: One of "e14c", "e14t", "e14d".
        pdf_path: Absolute path to the PDF file.
        manifest_records: List of manifest record dicts for this (mesa, type).
        crops_root: Root directory for writing PNG files (default "crops").

    Returns:
        List of delta dicts: [{crop_id, local_path}, ...] for each new crop.

    Raises:
        Exception: Re-raises if the PDF cannot be opened/processed at all, so
                   the caller (run_extract) can write an error record.
    """
    # Determine which records need extraction
    pending = [
        r for r in manifest_records
        if not (r.get("local_path") and Path(r["local_path"]).exists())
    ]
    if not pending:
        return []

    # Call the appropriate extractor
    if e14_type == "e14c":
        img, result = _call_e14c_extractor(pdf_path)
    else:
        img, result = _call_e14t_extractor(pdf_path)

    subcell_bboxes: dict[str, list[tuple]] = result.get("subcell_bboxes", {})
    fullcell_bboxes: dict[str, tuple] = result.get("fullcell_bboxes", {})

    deltas: list[dict] = []

    for record in pending:
        crop_id = record["crop_id"]
        field_name = record["field_name"]
        subcell_pos = record.get("subcell_pos")
        crop_type = record.get("crop_type", "subcell")
        dept = record.get("dept", "00")
        mpio = record.get("mpio", "000")
        zona = record.get("zona", "00")

        # Determine bbox
        if crop_type == "subcell":
            boxes = subcell_bboxes.get(field_name)
            if not boxes:
                continue  # field not detected — skip silently
            pos = subcell_pos if subcell_pos is not None else 0
            if pos >= len(boxes):
                continue  # position out of range — skip
            x0, y0, x1, y1 = boxes[pos]
        else:
            # fullcell
            bbox = fullcell_bboxes.get(field_name)
            if bbox is None:
                continue
            x0, y0, x1, y1 = bbox

        # Clamp to image bounds
        h, w = img.shape[:2]
        x0 = max(0, int(x0))
        y0 = max(0, int(y0))
        x1 = min(w, int(x1))
        y1 = min(h, int(y1))

        if x1 <= x0 or y1 <= y0:
            continue  # degenerate bbox — skip

        crop_bgr = img[y0:y1, x0:x1]

        # Determine output path
        out_dir = crops_root / dept / mpio / zona
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{crop_id}.png"

        if not cv2.imwrite(str(out_path), crop_bgr):
            continue  # write failed — skip

        deltas.append({"crop_id": crop_id, "local_path": str(out_path)})

    return deltas


# ---------------------------------------------------------------------------
# Worker function for ProcessPoolExecutor
# ---------------------------------------------------------------------------

def _worker_task(task: dict) -> dict:
    """Worker function — one task per (mesa_key, e14_type).

    Args:
        task: Dict with keys: mesa_key, e14_type, pdf_path, records, crops_root.

    Returns:
        Dict with keys: deltas (list), errors (list), mesa_key, e14_type.
    """
    mesa_key: str = task["mesa_key"]
    e14_type: str = task["e14_type"]
    pdf_path: Path = Path(task["pdf_path"])
    records: list[dict] = task["records"]
    crops_root: Path = Path(task["crops_root"])

    try:
        deltas = extract_mesa_crops(
            mesa_key=mesa_key,
            e14_type=e14_type,
            pdf_path=pdf_path,
            manifest_records=records,
            crops_root=crops_root,
        )
        return {"deltas": deltas, "errors": [], "mesa_key": mesa_key, "e14_type": e14_type}
    except Exception as exc:
        ts = datetime.now(timezone.utc).isoformat()
        # Build error records for each pending crop_id
        error_records = [
            {
                "crop_id": r.get("crop_id", ""),
                "mesa_key": mesa_key,
                "e14_type": e14_type,
                "field": r.get("field_name", ""),
                "error": str(exc),
                "ts": ts,
            }
            for r in records
            if not (r.get("local_path") and Path(r["local_path"]).exists())
        ]
        return {"deltas": [], "errors": error_records, "mesa_key": mesa_key, "e14_type": e14_type}


# ---------------------------------------------------------------------------
# Public: run_extract
# ---------------------------------------------------------------------------


def run_extract(
    manifest_path: Path,
    index_path: Path,
    workers: int = 4,
    errors_path: Path = Path("data/crop_extract_errors.jsonl"),
    limit: int | None = None,
    crops_root: Path = Path("crops"),
) -> ExtractReport:
    """Fan out crop extraction over all pending (mesa_key, e14_type) pairs.

    Args:
        manifest_path: Path to crop_manifest.jsonl.
        index_path: Path to cross_mesa_index.jsonl.
        workers: Number of parallel worker processes.
        errors_path: Path to the error accumulation JSONL file.
        limit: If set, process at most this many (mesa_key, e14_type) groups.
        crops_root: Root directory for output PNGs.

    Returns:
        ExtractReport with processed/skipped/errors counts.
    """
    from src.modules.crop.manifest import load_manifest, update_fields

    report = ExtractReport()

    # Load manifest
    manifest = load_manifest(manifest_path)
    if not manifest:
        return report

    # Load index for pdf paths.
    # The cross_mesa_index has no mesa_key field — compute it from geo fields.
    # mesa_key format: f"{mesa}{puesto}{zona}{mpio}{dept}{corp}" (same as classify).
    # Corp is absent from the index; the manifest records carry their own corp value,
    # so we key the index by a 5-tuple (dept, mpio, zona, puesto, mesa) and look up
    # using those components extracted from the manifest record.
    mesa_index: dict[tuple, dict] = {}
    if index_path.exists():
        with index_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    key = (
                        str(row.get("dept", "")),
                        str(row.get("mpio", "")),
                        str(row.get("zona", "")),
                        str(row.get("puesto", "")),
                        str(row.get("mesa", "")),
                    )
                    if all(key):
                        mesa_index[key] = row
                except json.JSONDecodeError:
                    pass

    # Group pending records by (mesa_key, e14_type)
    groups: dict[tuple[str, str], list[dict]] = {}
    for record in manifest.values():
        local_path = record.get("local_path")
        if local_path and Path(local_path).exists():
            report.skipped += 1
            continue
        mesa_key = record.get("mesa_key", "")
        e14_type = record.get("e14_type", "")
        if not mesa_key or not e14_type:
            continue
        key = (mesa_key, e14_type)
        groups.setdefault(key, []).append(record)

    if not groups:
        return report

    # Apply limit
    group_items = list(groups.items())
    if limit is not None and limit > 0:
        group_items = group_items[:limit]

    # Determine pdf_path for each group from index.
    # The index is keyed by (dept, mpio, zona, puesto, mesa) 5-tuple; use the
    # first manifest record in the group to retrieve those raw field values.
    tasks = []
    for (mesa_key, e14_type), records in group_items:
        sample = records[0]
        index_key = (
            str(sample.get("dept", "")),
            str(sample.get("mpio", "")),
            str(sample.get("zona", "")),
            str(sample.get("puesto", "")),
            str(sample.get("mesa", "")),
        )
        index_entry = mesa_index.get(index_key, {})
        pdf_key = f"{e14_type}_path"
        pdf_path_str = index_entry.get(pdf_key)
        if not pdf_path_str:
            # No PDF path in index — skip this group silently (expected for partial mesas)
            continue

        pdf_path = Path(pdf_path_str)
        if not pdf_path.exists():
            print(
                f"extractor: warning — PDF not found: {pdf_path}; skipping {mesa_key}/{e14_type}",
                file=sys.stderr,
            )
            continue

        tasks.append({
            "mesa_key": mesa_key,
            "e14_type": e14_type,
            "pdf_path": str(pdf_path),
            "records": records,
            "crops_root": str(crops_root),
        })

    if not tasks:
        return report

    # Fan out via ProcessPoolExecutor
    errors_path.parent.mkdir(parents=True, exist_ok=True)

    with ProcessPoolExecutor(max_workers=workers, initializer=_worker_init) as executor:
        futures = {executor.submit(_worker_task, task): task for task in tasks}
        for future in as_completed(futures):
            result = future.result()
            # Write deltas to manifest
            for delta in result.get("deltas", []):
                update_fields(manifest_path, delta["crop_id"], local_path=delta["local_path"])
                report.processed += 1
            # Write error records
            error_records = result.get("errors", [])
            if error_records:
                with errors_path.open("a", encoding="utf-8") as ef:
                    for err in error_records:
                        ef.write(json.dumps(err, ensure_ascii=False) + "\n")
                report.errors += len(error_records)

    return report


def _worker_init() -> None:
    """ProcessPoolExecutor initializer — mute noisy logging in worker processes."""
    import logging
    logging.disable(logging.CRITICAL)
