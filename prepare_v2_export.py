"""
prepare_v2_export.py — Export digit crops from suspicious actas for portal v2.

Reads data/suspicious_summary.jsonl, picks a geographically distributed sample,
and exports their digit crops to data/labels_v2/ (separate from the original
data/labels/ tree used for CNN training).

Usage:
    python prepare_v2_export.py                  # default 500 actas
    python prepare_v2_export.py --limit 200
    python prepare_v2_export.py --all            # all suspicious actas
    python prepare_v2_export.py --dept ARAUCA    # one department only
"""
import argparse
import json
import os
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path

SUSPICIOUS_JSONL = Path("data/suspicious_summary.jsonl")
V2_LABELS_DIR    = Path("data/labels_v2")
V2_CROPS_DIR     = V2_LABELS_DIR / "crops"
V2_INDEX         = V2_CROPS_DIR / "index.jsonl"


# ---------------------------------------------------------------------------
# Sample selection: geographically distributed
# ---------------------------------------------------------------------------

def select_sample(all_rows: list[dict], limit: int, dept_filter: str = "") -> list[dict]:
    """Pick up to `limit` actas, spread evenly across departments."""
    if dept_filter:
        rows = [r for r in all_rows if dept_filter.upper() in r.get("_dept", "").upper()
                or dept_filter.upper() in r.get("pdf_path", "").upper()]
    else:
        rows = all_rows

    if not rows:
        return []

    # Group by department
    by_dept: dict[str, list] = defaultdict(list)
    for r in rows:
        dept = Path(r["pdf_path"]).parts[-5] if len(Path(r["pdf_path"]).parts) > 4 else "unknown"
        by_dept[dept].append(r)

    if len(rows) <= limit:
        return rows

    # Round-robin across departments
    depts = sorted(by_dept, key=lambda d: -len(by_dept[d]))
    selected = []
    dept_iters = {d: iter(by_dept[d]) for d in depts}
    while len(selected) < limit:
        advanced = False
        for d in depts:
            if len(selected) >= limit:
                break
            try:
                selected.append(next(dept_iters[d]))
                advanced = True
            except StopIteration:
                pass
        if not advanced:
            break

    return selected


# ---------------------------------------------------------------------------
# Export logic (mirrors exporter.py but targeted at a specific PDF list)
# ---------------------------------------------------------------------------

def export_pdfs(pdf_paths: list[Path], out_crops_dir: Path, out_index: Path) -> int:
    """
    Export digit crops from a list of PDFs into out_crops_dir.
    Returns number of crops written.
    """
    from src.modules.labeler.exporter import (
        _field_regions, _make_crop_id, _write_png_atomic, _append_to_index, CropRecord,
    )
    from src.modules.analyzer.form_extractor import render_pdf_pages, _crop
    from src.modules.analyzer.ocr_engines import SegmentedEngine

    out_crops_dir.mkdir(parents=True, exist_ok=True)

    # Load already-indexed ids to skip duplicates
    indexed_ids: set[str] = set()
    if out_index.exists():
        with open(out_index, encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line.strip())
                    cid = row.get("crop_id")
                    if cid:
                        indexed_ids.add(cid)
                except json.JSONDecodeError:
                    pass

    engine = SegmentedEngine()
    field_specs = _field_regions()
    crops_written = 0

    for i, pdf_path in enumerate(pdf_paths, 1):
        print(f"[{i}/{len(pdf_paths)}] {pdf_path.name}")
        try:
            pages = render_pdf_pages(pdf_path)
        except Exception as e:
            print(f"  ERROR rendering: {e}")
            continue

        for field_name, region in field_specs:
            page_index = region[0]
            cell_crop = _crop(pages, region)
            if cell_crop is None:
                continue

            full_cell_crop_id = _make_crop_id(str(pdf_path), field_name, -999)
            full_cell_png = out_crops_dir / f"{full_cell_crop_id}.png"
            if not full_cell_png.exists():
                try:
                    _write_png_atomic(cell_crop, full_cell_png)
                except Exception:
                    pass

            digit_imgs = engine.segment_digits(cell_crop)

            if not digit_imgs:
                crop_id = _make_crop_id(str(pdf_path), field_name, -1)
                if crop_id in indexed_ids:
                    continue
                dest_png = out_crops_dir / f"{crop_id}.png"
                if not dest_png.exists():
                    try:
                        _write_png_atomic(cell_crop, dest_png)
                    except Exception:
                        continue
                record = CropRecord(
                    crop_id=crop_id, pdf_path=str(pdf_path), field_name=field_name,
                    digit_index=-1, label_ocr="?", confidence=0.0, priority=1,
                    full_cell_crop_id=full_cell_crop_id, page_index=page_index,
                )
                _append_to_index(out_index, record)
                indexed_ids.add(crop_id)
                crops_written += 1
                continue

            for digit_index, digit_img in enumerate(digit_imgs):
                crop_id = _make_crop_id(str(pdf_path), field_name, digit_index)
                if crop_id in indexed_ids:
                    continue
                dest_png = out_crops_dir / f"{crop_id}.png"
                if not dest_png.exists():
                    try:
                        _write_png_atomic(digit_img, dest_png)
                    except Exception:
                        continue
                try:
                    label_ocr, confidence = engine.read_single_digit(digit_img)
                except Exception:
                    label_ocr, confidence = "?", 0.0

                record = CropRecord(
                    crop_id=crop_id, pdf_path=str(pdf_path), field_name=field_name,
                    digit_index=digit_index, label_ocr=label_ocr, confidence=confidence,
                    priority=1, full_cell_crop_id=full_cell_crop_id, page_index=page_index,
                )
                _append_to_index(out_index, record)
                indexed_ids.add(crop_id)
                crops_written += 1

    return crops_written


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=500,
                        help="Max number of suspicious actas to export (default: 500)")
    parser.add_argument("--all", action="store_true",
                        help="Export all suspicious actas (ignores --limit)")
    parser.add_argument("--dept", help="Restrict to one department")
    args = parser.parse_args()

    if not SUSPICIOUS_JSONL.exists():
        print(f"ERROR: {SUSPICIOUS_JSONL} not found. Run: python summarize_suspicious.py first.")
        return

    all_rows = []
    with open(SUSPICIOUS_JSONL, encoding="utf-8") as f:
        for line in f:
            try:
                all_rows.append(json.loads(line.strip()))
            except json.JSONDecodeError:
                pass

    print(f"Suspicious actas available: {len(all_rows)}")

    limit = len(all_rows) if args.all else args.limit
    sample = select_sample(all_rows, limit, dept_filter=args.dept or "")
    print(f"Selected: {len(sample)} actas")

    # Show distribution
    from collections import Counter
    dist = Counter(Path(r["pdf_path"]).parts[-5]
                   for r in sample if len(Path(r["pdf_path"]).parts) > 4)
    for dept, count in dist.most_common():
        print(f"  {dept:<25} {count}")

    pdf_paths = [Path(r["pdf_path"]) for r in sample]
    missing = [p for p in pdf_paths if not p.exists()]
    if missing:
        print(f"\nWARN: {len(missing)} PDFs not found locally — will skip them.")
        pdf_paths = [p for p in pdf_paths if p.exists()]

    print(f"\nExporting crops to {V2_CROPS_DIR} ...")
    crops_written = export_pdfs(pdf_paths, V2_CROPS_DIR, V2_INDEX)

    print(f"\nDone. {crops_written} crops written.")
    print(f"Index: {V2_INDEX}")
    print(f"\nNext step:")
    print(f"  Set LABELS_DIR=data/labels_v2 in .env and run: python main.py label")


if __name__ == "__main__":
    main()
