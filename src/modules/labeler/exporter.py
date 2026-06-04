"""
exporter — Batch PDF-to-crop pipeline for the labeler portal.

Run once (or incrementally with --limit) to populate data/labels/crops/ with
individual digit PNGs and an index.jsonl that drives the validation queue.

Key design decisions (see design doc ADR-1):
  - Bounded pre-export: walks analisis_resultados.jsonl files to build a
    priority-ordered PDF list, then exports only the first --limit PDFs.
  - Idempotent/resumable: skips crops whose PNG already exists.
  - 0-segment fallback: when segment_digits() returns nothing, writes the
    full-cell crop with digit_index=-1 so the human can type the whole number.

Imports analyzer modules (form_extractor, ocr_engines) — these require
pymupdf, opencv, and easyocr. This module must ONLY be imported when running
`export-crops`, never at Flask serve time.
"""
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Optional

import cv2

from src.modules.analyzer.form_extractor import (
    CANDIDATE_ROWS_P0,
    CANDIDATE_ROWS_P1,
    NIVELACION,
    TOTALS,
    _crop,
    render_pdf_pages,
)
from src.modules.analyzer.ocr_engines import SegmentedEngine
from src.modules.labeler.models import CropRecord

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

_INDEX_FILENAME = "index.jsonl"


def _make_crop_id(pdf_path: str, field_name: str, digit_index: int) -> str:
    """Deterministic, idempotent crop identifier: sha1(pdf|field|idx)[:16]."""
    key = f"{pdf_path}|{field_name}|{digit_index}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def _field_regions() -> list[tuple[str, tuple]]:
    """Return (field_name, region_tuple) for every field on both pages."""
    specs: list[tuple[str, tuple]] = []
    for name, region in NIVELACION.items():
        specs.append((name, region))
    for i, region in enumerate(CANDIDATE_ROWS_P0):
        specs.append((f"candidato_{i + 1}", region))
    for i, region in enumerate(CANDIDATE_ROWS_P1):
        specs.append((f"candidato_{i + 1 + len(CANDIDATE_ROWS_P0)}", region))
    for name, region in TOTALS.items():
        specs.append((name, region))
    return specs


def _write_png_atomic(img, dest: Path) -> None:
    """Write a numpy BGR image as PNG to dest, atomically via temp + replace."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, suffix=".png")
    os.close(fd)
    try:
        ok, buf = cv2.imencode(".png", img)
        if not ok:
            raise RuntimeError(f"cv2.imencode failed for {dest}")
        with open(tmp, "wb") as f:
            f.write(buf.tobytes())
        os.replace(tmp, dest)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _append_to_index(index_path: Path, record: CropRecord) -> None:
    """Append one CropRecord line to index.jsonl (non-atomic; caller ensures idempotency)."""
    row = {
        "crop_id": record.crop_id,
        "pdf_path": record.pdf_path,
        "field_name": record.field_name,
        "digit_index": record.digit_index,
        "label_ocr": record.label_ocr,
        "confidence": record.confidence,
        "priority": record.priority,
        "full_cell_crop_id": record.full_cell_crop_id,
        "page_index": record.page_index,
    }
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with open(index_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


# ------------------------------------------------------------------
# Priority builder
# ------------------------------------------------------------------

def _build_priority_list(
    pdfs_root: Path,
    dept_filter: Optional[str] = None,
) -> list[tuple[int, Path]]:
    """
    Walk analisis_resultados.jsonl files to build a (priority, pdf_path) list.

    Priority buckets:
      0 = needs_review (OCR produced implausible values)
      1 = is_suspicious (genuine fraud signals)
      2 = normal / no analysis result found

    When no analisis_resultados.jsonl exists (PDFs not yet analyzed), all
    PDFs fall to priority 2.
    """
    priority_map: dict[str, int] = {}

    for jsonl_file in pdfs_root.rglob("analisis_resultados.jsonl"):
        with open(jsonl_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                pdf_path = row.get("pdf_path", "")
                if row.get("needs_review"):
                    priority = 0
                elif row.get("is_suspicious"):
                    priority = 1
                else:
                    priority = 2
                # Lower priority wins (needs_review overrides is_suspicious)
                existing = priority_map.get(pdf_path, 999)
                if priority < existing:
                    priority_map[pdf_path] = priority

    # Collect all PDFs (filtered by dept if specified)
    all_pdfs: list[Path] = []
    if dept_filter:
        search_root = pdfs_root / dept_filter.upper()
        if not search_root.exists():
            search_root = pdfs_root
    else:
        search_root = pdfs_root

    for pdf in search_root.rglob("*.pdf"):
        all_pdfs.append(pdf)

    # Build sorted list: (priority, pdf_path)
    result: list[tuple[int, Path]] = []
    for pdf in all_pdfs:
        p = priority_map.get(str(pdf), 2)
        result.append((p, pdf))

    result.sort(key=lambda x: (x[0], str(x[1])))
    return result


# ------------------------------------------------------------------
# Main export function
# ------------------------------------------------------------------

def run_export(
    pdfs_root: Path,
    labels_dir: Path,
    limit: int = 2000,
    dept_filter: Optional[str] = None,
) -> None:
    """
    Batch-export digit crops from PDFs to data/labels/crops/.

    Args:
        pdfs_root: Root directory containing PDF files (e.g. data/pdfs/).
        labels_dir: Root of label output tree (e.g. data/labels/).
        limit: Maximum number of PDFs to process.
        dept_filter: When provided, restrict to this department subdirectory.
    """
    crops_dir = labels_dir / "crops"
    crops_dir.mkdir(parents=True, exist_ok=True)
    index_path = crops_dir / _INDEX_FILENAME

    # Load already-indexed crop_ids to avoid duplicate index entries
    indexed_ids: set[str] = set()
    if index_path.exists():
        with open(index_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    cid = row.get("crop_id")
                    if cid:
                        indexed_ids.add(cid)
                except json.JSONDecodeError:
                    continue

    # Build priority list
    priority_list = _build_priority_list(pdfs_root, dept_filter)
    if not priority_list:
        print(f"No PDFs found under {pdfs_root}")
        return

    engine = SegmentedEngine()
    field_specs = _field_regions()

    processed = 0
    crops_written = 0

    for priority, pdf_path in priority_list:
        if processed >= limit:
            break

        print(f"[{processed + 1}/{min(limit, len(priority_list))}] {pdf_path.name}")

        try:
            pages = render_pdf_pages(pdf_path)
        except Exception as e:
            print(f"  ERROR rendering {pdf_path}: {e}")
            continue

        processed += 1

        for field_name, region in field_specs:
            page_index = region[0]
            cell_crop = _crop(pages, region)
            if cell_crop is None:
                continue

            # Compute full-cell crop_id (used as context panel in UI)
            full_cell_crop_id = _make_crop_id(str(pdf_path), field_name, -999)
            full_cell_png = crops_dir / f"{full_cell_crop_id}.png"
            if not full_cell_png.exists():
                try:
                    _write_png_atomic(cell_crop, full_cell_png)
                except Exception as e:
                    print(f"  WARN writing full-cell PNG {field_name}: {e}")

            digit_imgs = engine.segment_digits(cell_crop)

            if not digit_imgs:
                # Task 2.4: 0-segment fallback — write full-cell crop as the digit crop
                crop_id = _make_crop_id(str(pdf_path), field_name, -1)
                if crop_id in indexed_ids:
                    continue

                dest_png = crops_dir / f"{crop_id}.png"
                if not dest_png.exists():
                    try:
                        _write_png_atomic(cell_crop, dest_png)
                    except Exception as e:
                        print(f"  WARN writing fallback PNG {field_name}: {e}")
                        continue

                label_ocr = "?"
                record = CropRecord(
                    crop_id=crop_id,
                    pdf_path=str(pdf_path),
                    field_name=field_name,
                    digit_index=-1,
                    label_ocr=label_ocr,
                    confidence=0.0,
                    priority=priority,
                    full_cell_crop_id=full_cell_crop_id,
                    page_index=page_index,
                )
                _append_to_index(index_path, record)
                indexed_ids.add(crop_id)
                crops_written += 1
                continue

            # Normal path: one crop per segmented digit
            for digit_index, digit_img in enumerate(digit_imgs):
                crop_id = _make_crop_id(str(pdf_path), field_name, digit_index)
                if crop_id in indexed_ids:
                    continue

                dest_png = crops_dir / f"{crop_id}.png"
                if not dest_png.exists():
                    try:
                        _write_png_atomic(digit_img, dest_png)
                    except Exception as e:
                        print(f"  WARN writing digit PNG {field_name}[{digit_index}]: {e}")
                        continue

                try:
                    label_ocr, confidence = engine.read_single_digit(digit_img)
                except Exception:
                    label_ocr, confidence = "?", 0.0

                record = CropRecord(
                    crop_id=crop_id,
                    pdf_path=str(pdf_path),
                    field_name=field_name,
                    digit_index=digit_index,
                    label_ocr=label_ocr,
                    confidence=confidence,
                    priority=priority,
                    full_cell_crop_id=full_cell_crop_id,
                    page_index=page_index,
                )
                _append_to_index(index_path, record)
                indexed_ids.add(crop_id)
                crops_written += 1

    print(f"\nDone. {processed} PDFs processed, {crops_written} new crops written.")
    print(f"Index: {index_path}")
