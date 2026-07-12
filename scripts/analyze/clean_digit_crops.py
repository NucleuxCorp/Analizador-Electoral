"""
clean_digit_crops.py — Phase 0.4 Data Quality for retrain-digit-classifier.

Scans extracted digit crops (digits/, train_pool/, confirmed/), runs the
oval template filter + simple VOTACIÓN header heuristic, and quarantines
contaminated crops so they are excluded from training.

Usage:
    python scripts/clean_digit_crops.py
    python scripts/clean_digit_crops.py --dry-run
    python scripts/clean_digit_crops.py --also-scan data/labels/train_pool data/labels/confirmed

Outputs:
    data/labels/digits/_quarantine/{0-9}/img_*.png (moved bad crops)
    data/labels/digits/clean_report.json
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
from pathlib import Path
from typing import Iterable, Optional

import cv2
import numpy as np

from src.modules.analyzer.ocr_engines import es_ovalo_plantilla


# ---------------------------------------------------------------------------
# Tunables (simple VOT heuristic; keep first-pass conservative)
# ---------------------------------------------------------------------------

VOT_WIDTH_THRESH = 90
VOT_ASPECT_MAX = 1.55
VOT_ASPECT_MIN = 0.35

logger = logging.getLogger(__name__)


def extract_crop_id(p: Path) -> str:
    """Extract crop_id from 'img_{crop_id}.png' or fall back to stem."""
    name = p.name
    if name.startswith("img_") and name.endswith(".png"):
        return name[4:-4]
    return p.stem


def is_vot_header_crop(gray: np.ndarray, w: int, h: int) -> bool:
    """Simple geometric heuristic for VOTACIÓN / header text crops.

    VOT text is wide horizontal strings. Normal digit crops are ~square
    (50-85 px, aspect ~0.8-1.4). Flag outliers.
    """
    if w <= 0 or h <= 0:
        return False
    aspect = w / h
    if w > VOT_WIDTH_THRESH:
        return True
    if aspect > VOT_ASPECT_MAX or aspect < VOT_ASPECT_MIN:
        return True
    return False


def should_quarantine(
    gray: np.ndarray,
    fullbox: tuple[int, int, int, int],
    crop_id: str,
    cls: str,
) -> bool:
    """Return True if this crop should be quarantined (oval or VOT header)."""
    if es_ovalo_plantilla(gray, fullbox):
        return True
    h, w = gray.shape[:2]
    if is_vot_header_crop(gray, w, h):
        return True
    return False


def find_class_dirs(root: Path) -> list[Path]:
    """Return list of {root}/{0-9} directories that exist."""
    dirs: list[Path] = []
    for c in "0123456789":
        d = root / c
        if d.is_dir():
            dirs.append(d)
    return dirs


def iter_pngs(class_dir: Path) -> Iterable[Path]:
    return class_dir.glob("*.png")


def ensure_quarantine_dir(quarantine_root: Path, cls: str) -> Path:
    q = quarantine_root / cls
    q.mkdir(parents=True, exist_ok=True)
    return q


def already_quarantined(quarantine_root: Path, cls: str, crop_id: str) -> bool:
    """Idempotency: skip if the crop_id already lives in quarantine."""
    q = quarantine_root / cls / f"img_{crop_id}.png"
    return q.exists()


def move_to_quarantine(src: Path, quarantine_root: Path, cls: str, crop_id: str) -> Path:
    """Move src to quarantine/{cls}/img_{crop_id}.png. Overwrite not expected (idempotent caller)."""
    dst_dir = ensure_quarantine_dir(quarantine_root, cls)
    dst = dst_dir / f"img_{crop_id}.png"
    if dst.exists():
        # Idempotent: remove source if it's a duplicate elsewhere
        if src.resolve() != dst.resolve():
            src.unlink(missing_ok=True)
        return dst
    shutil.move(str(src), str(dst))
    return dst


def clean_location(
    root: Path,
    quarantine_root: Path,
    *,
    dry_run: bool = False,
    known_bad: Optional[set[str]] = None,
) -> dict:
    """Scan one root (digits/ or train_pool/ or confirmed/), quarantine bad crops.

    If known_bad is provided, use cheap filename/crop_id match (no image load or oval call)
    — this is the fast path for secondary trees (train_pool, confirmed) after primary digits/ pass.

    Returns per-class counts for this location.
    """
    counts: dict[str, dict[str, int]] = {c: {"scanned": 0, "quarantined": 0} for c in "0123456789"}
    quarantined_ids: list[str] = []
    use_cheap = known_bad is not None

    for cls_dir in find_class_dirs(root):
        cls = cls_dir.name
        for png in iter_pngs(cls_dir):
            counts[cls]["scanned"] += 1
            crop_id = extract_crop_id(png)

            if already_quarantined(quarantine_root, cls, crop_id):
                if not dry_run:
                    png.unlink(missing_ok=True)
                continue

            is_bad = False
            if use_cheap:
                is_bad = crop_id in known_bad
            else:
                gray = cv2.imread(str(png), cv2.IMREAD_GRAYSCALE)
                if gray is None or gray.size == 0:
                    continue
                h, w = gray.shape[:2]
                fullbox = (0, 0, w, h)
                is_bad = should_quarantine(gray, fullbox, crop_id, cls)

            if is_bad:
                counts[cls]["quarantined"] += 1
                quarantined_ids.append(crop_id)
                if not dry_run:
                    move_to_quarantine(png, quarantine_root, cls, crop_id)
                    logger.debug("QUARANTINED %s/%s (from %s)", cls, crop_id, root)
                else:
                    logger.debug("DRY would quarantine %s/%s (from %s)", cls, crop_id, root)

    return {"counts": counts, "quarantined_crop_ids": quarantined_ids}


def aggregate_report(reports: list[dict], roots: list[str]) -> dict:
    """Combine per-location reports into the final clean_report.json shape."""
    per_class: dict[str, dict[str, int]] = {c: {"kept": 0, "quarantined": 0} for c in "0123456789"}
    all_q: set[str] = set()
    for rep in reports:
        for cls, c in rep["counts"].items():
            per_class[cls]["quarantined"] += c["quarantined"]
            # kept is computed after: scanned - quarantined (this location only)
            # For final report we report global quarantined; kept is source-of-truth digits after run.
    # Final kept is best computed by re-counting digits/ after moves (or by subtraction if single source).
    # We keep it simple: report quarantined list + per-class quarantined counts.
    for rep in reports:
        all_q.update(rep.get("quarantined_crop_ids", []))

    return {
        "quarantined_crop_ids": sorted(all_q),
        "per_class_quarantined": {c: per_class[c]["quarantined"] for c in "0123456789"},
        "scanned_locations": roots,
        "note": "kept counts reflect digits/ tree after run; manifests filtered in 0.5",
    }


def write_report(report: dict, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Quarantine oval + VOT header crops from digit label trees.")
    parser.add_argument("--dry-run", action="store_true", help="Do not move/delete files; only count and report.")
    parser.add_argument(
        "--also-scan",
        nargs="*",
        default=[],
        help="Additional roots to scan (e.g. data/labels/train_pool data/labels/confirmed)",
    )
    parser.add_argument(
        "--digits-root",
        default="data/labels/digits",
        help="Primary digits/ tree (default: data/labels/digits)",
    )
    parser.add_argument(
        "--quarantine-root",
        default="data/labels/digits/_quarantine",
        help="Where to move bad crops (default under digits/_quarantine)",
    )
    parser.add_argument(
        "--report",
        default="data/labels/digits/clean_report.json",
        help="Path for clean_report.json",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    digits_root = Path(args.digits_root)
    quarantine_root = Path(args.quarantine_root)
    report_path = Path(args.report)

    locations: list[Path] = [digits_root]
    secondary: list[Path] = []
    for extra in args.also_scan:
        p = Path(extra)
        if p.is_dir():
            secondary.append(p)
            locations.append(p)

    logger.info("Starting cleaner (dry_run=%s)", args.dry_run)
    logger.info("Primary: %s ; secondaries: %s", digits_root, [str(s) for s in secondary])

    # Before counts (digits/ only for headline numbers)
    before_counts = {}
    for cls in "0123456789":
        d = digits_root / cls
        before_counts[cls] = len(list(d.glob("*.png"))) if d.is_dir() else 0
    logger.info("Before (digits/): %s", before_counts)

    per_loc_reports = []

    # Primary: digits/ — full oval + VOT analysis (slow path)
    primary_rep = clean_location(digits_root, quarantine_root, dry_run=args.dry_run, known_bad=None)
    per_loc_reports.append(primary_rep)
    logger.info("Primary digits/ done: quarantined this pass %s", {c: primary_rep["counts"][c]["quarantined"] for c in "0123456789"})

    # Collect the bad set from primary (or previous report if resuming)
    bad_from_primary = set(primary_rep["quarantined_crop_ids"])
    # If quarantine dir already has files from prior partial run, include them too for secondary purge
    for cls in "0123456789":
        qdir = quarantine_root / cls
        if qdir.is_dir():
            for qp in qdir.glob("img_*.png"):
                bad_from_primary.add(extract_crop_id(qp))

    # Secondaries: cheap purge by crop_id (no image load, no oval)
    for sec in secondary:
        sec_rep = clean_location(sec, quarantine_root, dry_run=args.dry_run, known_bad=bad_from_primary)
        per_loc_reports.append(sec_rep)
        logger.info("Secondary %s (cheap): removed %s", sec, {c: sec_rep["counts"][c]["quarantined"] for c in "0123456789"})

    # After counts (digits/)
    after_counts = {}
    for cls in "0123456789":
        d = digits_root / cls
        after_counts[cls] = len(list(d.glob("*.png"))) if d.is_dir() else 0
    logger.info("After (digits/): %s", after_counts)

    report = aggregate_report(per_loc_reports, [str(digits_root)] + [str(s) for s in secondary])
    report["before_digits_counts"] = before_counts
    report["after_digits_counts"] = after_counts
    report["per_class"] = {
        c: {"kept": after_counts[c], "quarantined": report["per_class_quarantined"][c]}
        for c in "0123456789"
    }

    if not args.dry_run:
        write_report(report, report_path)
        logger.info("Wrote %s", report_path)
    else:
        logger.info("DRY RUN — no report written")

    # Summary for class 0 (the one that mattered in review)
    q0 = report["per_class_quarantined"].get("0", 0)
    logger.info("Class 0: quarantined %d (target ~248-284 per review)", q0)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
