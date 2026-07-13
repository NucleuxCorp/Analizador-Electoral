#!/usr/bin/env python3
"""Export portal crops from segunda-vuelta tachon/enmienda focused PDF list.

Reads data/analysis_segunda_vuelta/tachon_enmienda_focused_list.json, runs the
grid_detector (v3) pipeline per PDF, and writes CropRecord-compatible PNGs +
index.jsonl under data/labels_segunda/crops/.

Usage:
    python scripts/export_crops_segunda_focused.py --limit 5
    python scripts/export_crops_segunda_focused.py
    python scripts/export_crops_segunda_focused.py --list path/to/list.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import cv2
import numpy as np

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from src.modules.analyzer import grid_detector as _grid_module  # noqa: E402

DEFAULT_LIST = _REPO / "data/analysis_segunda_vuelta/tachon_enmienda_focused_list.json"
DEFAULT_OUT = _REPO / "data/labels_segunda/crops"
DEFAULT_INDEX = DEFAULT_OUT / "index.jsonl"

ENMIENDA_FLAGS = frozenset({"DENSIDAD_ALTA", "ZONA_SUCIA"})


def _load_grid():
    return _grid_module
    return grid


def _load_crop_cell():
    from debug_sv.candidate_subcells import crop_cell

    return crop_cell


def _load_exporter_helpers():
    from src.modules.labeler.exporter import (
        _append_to_index,
        _make_crop_id,
        _write_png_atomic,
    )
    from src.modules.labeler.models import CropRecord

    return _make_crop_id, _write_png_atomic, _append_to_index, CropRecord


def _load_indexed_ids(index_path: Path) -> set[str]:
    ids: set[str] = set()
    if not index_path.exists():
        return ids
    with open(index_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            cid = row.get("crop_id")
            if cid:
                ids.add(cid)
    return ids


def _select_pdfs(list_path: Path, include_sin_tinta: bool) -> list[dict]:
    data = json.loads(list_path.read_text(encoding="utf-8"))
    issues = data.get("issues", [])
    selected: list[dict] = []
    for item in issues:
        flags = set(item.get("flags", []))
        if not include_sin_tinta and ("SIN_TINTA" in flags or "NO_ESCANEADO" in flags):
            continue
        pdf = item.get("pdf", "").replace("\\", "/")
        if pdf:
            selected.append({**item, "pdf": pdf})
    return selected


def _priority_for_pdf(flags: list[str]) -> int:
    if ENMIENDA_FLAGS.intersection(flags):
        return 1
    return 2


def _export_pdf(
    item: dict,
    *,
    out_dir: Path,
    index_path: Path,
    indexed_ids: set[str],
    grid,
    crop_cell,
    make_crop_id,
    write_png_atomic,
    append_to_index,
    CropRecord,
    engine,
) -> tuple[int, int]:
    pdf_rel = item["pdf"]
    pdf_path = _REPO / pdf_rel
    if not pdf_path.is_file():
        print(f"  SKIP missing: {pdf_rel}")
        return 0, 1

    priority = _priority_for_pdf(item.get("flags", []))

    img = grid.render_page(pdf_path)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    page_h = gray.shape[0]

    v_lines = grid.detect_gray_v_lines(gray)
    if len(v_lines) < 4:
        v_lines = grid.SUBCELL_X_FALLBACK

    h_lines_raw = grid.detect_gray_h_lines(gray)
    h_lines = grid.process_h_lines(h_lines_raw, page_height=page_h)
    labeled_rows = grid.label_rows_by_structure(grid.build_grid(v_lines, h_lines), page_h=page_h)

    written = 0
    for row in labeled_rows:
        label = row.get("label", "")
        known = grid.is_known_label(label)
        inks = [
            grid.cell_has_ink(gray, c["x1"], c["y1"], c["x2"], c["y2"])
            for c in row["cells"]
        ]
        if sum(inks) == 0 and not known:
            continue

        field_name = label if known else f"row_{row['row_idx']}"
        full_cell_crop_id = make_crop_id(pdf_rel, field_name, -999)
        full_cell_png = out_dir / f"{full_cell_crop_id}.png"
        if not full_cell_png.exists() and row["cells"]:
            x1 = min(c["x1"] for c in row["cells"]) + grid.CELL_PAD
            y1 = row["top"] + grid.CELL_PAD
            x2 = max(c["x2"] for c in row["cells"]) - grid.CELL_PAD
            y2 = row["bot"] - grid.CELL_PAD
            if x2 > x1 and y2 > y1:
                try:
                    write_png_atomic(img[y1:y2, x1:x2], full_cell_png)
                except Exception:
                    pass

        for digit_index, cell in enumerate(row["cells"]):
            if not inks[digit_index]:
                continue

            crop_id = make_crop_id(pdf_rel, field_name, digit_index)
            if crop_id in indexed_ids:
                continue

            dest_png = out_dir / f"{crop_id}.png"
            crop = crop_cell(img, cell, grid.CELL_PAD)
            if crop.size == 0:
                continue

            if not dest_png.exists():
                try:
                    write_png_atomic(crop, dest_png)
                except Exception as exc:
                    print(f"  ERROR write {crop_id}: {exc}")
                    continue

            try:
                label_ocr, confidence = engine._read_single_digit(crop)
            except Exception:
                label_ocr, confidence = "?", 0.0

            if label_ocr in {None, "", "null"}:
                label_ocr = "?"
            label_ocr = str(label_ocr)

            record = CropRecord(
                crop_id=crop_id,
                pdf_path=pdf_rel,
                field_name=field_name,
                digit_index=digit_index,
                label_ocr=label_ocr,
                confidence=float(confidence or 0.0),
                priority=priority,
                full_cell_crop_id=full_cell_crop_id,
                page_index=0,
            )
            append_to_index(index_path, record)
            indexed_ids.add(crop_id)
            written += 1

    return written, 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export segunda-vuelta focused-list crops.")
    parser.add_argument("--list", type=Path, default=DEFAULT_LIST, help="Focused PDF list JSON")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT, help="Output crops directory")
    parser.add_argument("--index", type=Path, default=None, help="index.jsonl path (default: out-dir/index.jsonl)")
    parser.add_argument("--limit", type=int, default=0, help="Max PDFs to process (0 = all)")
    parser.add_argument(
        "--include-sin-tinta",
        action="store_true",
        help="Include SIN_TINTA / NO_ESCANEADO PDFs (usually no ink to export)",
    )
    args = parser.parse_args(argv)

    if not args.list.is_file():
        print(f"ERROR: list not found: {args.list}")
        return 1

    out_dir = args.out_dir
    index_path = args.index or (out_dir / "index.jsonl")
    out_dir.mkdir(parents=True, exist_ok=True)

    logging.disable(logging.CRITICAL)

    grid = _load_grid()
    crop_cell = _load_crop_cell()
    make_crop_id, write_png_atomic, append_to_index, CropRecord = _load_exporter_helpers()
    from src.modules.analyzer.ocr_engines import SegmentedEngine

    engine = SegmentedEngine()
    indexed_ids = _load_indexed_ids(index_path)

    pdfs = _select_pdfs(args.list, args.include_sin_tinta)
    if args.limit > 0:
        pdfs = pdfs[: args.limit]

    print(f"Exporting {len(pdfs)} PDFs -> {out_dir}")
    total_crops = 0
    errors = 0

    for i, item in enumerate(pdfs, 1):
        print(f"[{i}/{len(pdfs)}] {Path(item['pdf']).name} flags={item.get('flags', [])}")
        try:
            n, err = _export_pdf(
                item,
                out_dir=out_dir,
                index_path=index_path,
                indexed_ids=indexed_ids,
                grid=grid,
                crop_cell=crop_cell,
                make_crop_id=make_crop_id,
                write_png_atomic=write_png_atomic,
                append_to_index=append_to_index,
                CropRecord=CropRecord,
                engine=engine,
            )
            total_crops += n
            errors += err
            print(f"  +{n} crops")
        except Exception as exc:
            errors += 1
            print(f"  ERROR: {exc}")

    print(f"Done: {total_crops} new crops, {errors} pdf errors, index={index_path}")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())