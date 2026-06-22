"""
Export and refine the 3-digit sub-cell group for each candidate row.

Uses the working grid_detector_v2 pipeline, then re-detects vertical
separators scoped to each candidate row (short lines are easier to
find inside a single row than across the full column).

Run:
    python debug_sv/candidate_subcells.py
    python debug_sv/candidate_subcells.py --pdf data/pdfs_e14c_segunda/01_001_...pdf
    python debug_sv/candidate_subcells.py --pad 8 --out debug_sv/candidate_crops
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

spec = importlib.util.spec_from_file_location(
    "grid_v2", ROOT / "debug_sv" / "grid_detector_v2.py"
)
grid = importlib.util.module_from_spec(spec)
spec.loader.exec_module(grid)

CANDIDATE_LABELS = ("C1_CEPEDA", "C2_ABELARDO")
C1_Y_RANGE = (1650, 1950)
C2_Y_RANGE = (2200, 2550)
LABEL_Y_BANDS = {
    "C1_CEPEDA": C1_Y_RANGE,
    "C2_ABELARDO": C2_Y_RANGE,
}


def detect_v_lines_in_row(
    gray: np.ndarray,
    y_top: int,
    y_bot: int,
    x_left: int = grid.VOTE_X_LEFT,
    x_right: int = grid.VOTE_X_RIGHT,
    fallback: list[int] | None = None,
) -> list[int]:
    """Detect 4 vertical boundaries (3 sub-cells) inside one row band."""
    roi = gray[y_top:y_bot, x_left:x_right]
    H, W = roi.shape
    if H < 20 or W < 50:
        return fallback or grid.SUBCELL_X_FALLBACK

    min_run = max(15, int(H * 0.55))
    scored: list[tuple[int, int, float]] = []

    for x in range(W):
        col = roi[:, x]
        gray_mask = (col > 80) & (col < 220)
        black_frac = float(np.mean(col < 80))
        if black_frac > 0.25:
            continue

        best_run = cur = 0
        for g in gray_mask:
            if g:
                cur += 1
                best_run = max(best_run, cur)
            else:
                cur = 0

        if best_run >= min_run:
            scored.append((x, best_run, float(np.mean(gray_mask))))

    if not scored:
        return fallback or grid.SUBCELL_X_FALLBACK

    scored.sort(key=lambda t: t[0])
    clusters: list[list[tuple[int, int, float]]] = [[scored[0]]]
    for item in scored[1:]:
        if item[0] - clusters[-1][-1][0] <= 6:
            clusters[-1].append(item)
        else:
            clusters.append([item])

    peaks = sorted(max(g, key=lambda t: (t[1], t[2]))[0] for g in clusters)

    # Pick best chain of 4 lines with ~93px spacing
    best_group: list[int] | None = None
    best_std = float("inf")
    for i in range(len(peaks)):
        group = [peaks[i]]
        for j in range(i + 1, len(peaks)):
            gap = peaks[j] - group[-1]
            if gap < 70:
                continue
            if gap > 115:
                break
            group.append(peaks[j])
            if len(group) == 4:
                gaps = [group[k + 1] - group[k] for k in range(3)]
                std = float(np.std(gaps))
                if std < best_std:
                    best_std = std
                    best_group = group
                break

    if best_group:
        return [x + x_left for x in best_group]

    return fallback or grid.SUBCELL_X_FALLBACK


def build_subcells(
    v_lines: list[int],
    y_top: int,
    y_bot: int,
) -> list[dict]:
    v = sorted(v_lines)
    cells = []
    for i in range(len(v) - 1):
        cells.append({
            "idx": i,
            "x1": v[i],
            "y1": y_top,
            "x2": v[i + 1],
            "y2": y_bot,
        })
    return cells


def crop_cell(img: np.ndarray, cell: dict, pad: int) -> np.ndarray:
    x1 = cell["x1"] + pad
    y1 = cell["y1"] + pad
    x2 = cell["x2"] - pad
    y2 = cell["y2"] - pad
    if x2 <= x1 or y2 <= y1:
        return np.array([])
    return img[y1:y2, x1:x2].copy()


def _row_ink_score(gray: np.ndarray, row: dict, pad: int) -> int:
    return sum(
        grid.cell_has_ink(gray, c["x1"], c["y1"], c["x2"], c["y2"], pad=pad)
        for c in row["cells"]
    )


def find_candidate_rows(
    gray: np.ndarray,
    grid_rows: list[dict],
    pad: int,
) -> dict[str, dict]:
    """Locate C1/C2 rows via structural labeling, then Y-band fallback."""
    found: dict[str, dict] = {}

    labeled = grid.label_rows_by_structure(grid_rows)
    for row in labeled:
        label = row.get("label", "")
        if label in CANDIDATE_LABELS and label not in found:
            found[label] = row

    for label, (ylo, yhi) in LABEL_Y_BANDS.items():
        if label in found:
            continue
        best_row = None
        best_score = -1
        for row in grid_rows:
            y_mid = (row["top"] + row["bot"]) // 2
            if not (ylo <= y_mid <= yhi):
                continue
            score = _row_ink_score(gray, row, pad)
            if score > best_score:
                best_score = score
                best_row = row
        if best_row is not None:
            found[label] = best_row

    return found


def read_candidate_row(
    gray: np.ndarray,
    img: np.ndarray,
    row: dict,
    label: str,
    v_global: list[int],
    engine,
    pad: int,
    out_dir: Path | None = None,
) -> dict:
    """OCR one candidate row with per-row vertical line refinement."""
    v_row = detect_v_lines_in_row(gray, row["top"], row["bot"], fallback=v_global)
    cells = build_subcells(v_row, row["top"], row["bot"])

    from debug_sv.subcell_tachon import build_subcell_payload

    digits, confs, inks, crop_paths, subcells = [], [], [], [], []
    for cell in cells:
        has_ink = grid.cell_has_ink(
            gray, cell["x1"], cell["y1"], cell["x2"], cell["y2"], pad=pad
        )
        inks.append(has_ink)

        if out_dir is not None:
            crop = crop_cell(img, cell, pad)
            crop_name = f"{label}_d{cell['idx']}.png"
            crop_path = out_dir / crop_name
            if crop.size:
                cv2.imwrite(str(crop_path), crop)
                crop_paths.append(str(crop_path))

        if has_ink:
            ch, conf = grid.ocr_cell(
                engine, img, cell["x1"], cell["y1"], cell["x2"], cell["y2"], pad=pad
            )
        else:
            ch, conf = "0", 0.0
        digits.append(ch)
        confs.append(round(conf, 3))
        subcells.append(
            build_subcell_payload(
                cell,
                digit=ch,
                confidence=round(conf, 3),
                has_ink=has_ink,
                img=img,
                pad=pad,
            )
        )

    raw = "".join(digits)
    value = int(raw) if raw.isdigit() else None

    if out_dir is not None:
        strips = [crop_cell(img, c, pad) for c in cells]
        valid = [s for s in strips if s.size]
        if valid:
            h_max = max(s.shape[0] for s in valid)
            padded = []
            for s in strips:
                if s.size == 0:
                    continue
                if s.shape[0] < h_max:
                    pad_h = h_max - s.shape[0]
                    s = cv2.copyMakeBorder(s, 0, pad_h, 0, 0, cv2.BORDER_CONSTANT, value=255)
                padded.append(s)
            if padded:
                cv2.imwrite(str(out_dir / f"{label}_strip.png"), np.hstack(padded))

        debug = img.copy()
        for x in v_row:
            cv2.line(debug, (x, row["top"]), (x, row["bot"]), (255, 100, 0), 2)
        cv2.rectangle(debug, (v_row[0], row["top"]), (v_row[-1], row["bot"]), (0, 255, 0), 2)
        for j, cell in enumerate(cells):
            color = (0, 200, 0) if inks[j] else (0, 255, 255)
            cv2.rectangle(debug, (cell["x1"], cell["y1"]), (cell["x2"], cell["y2"]), color, 2)
        tag = f"{label}={value} [{','.join(digits)}]"
        cv2.putText(debug, tag, (v_row[0], row["top"] - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        cv2.imwrite(str(out_dir / f"{label}_row.png"), debug)

    return {
        "label": label,
        "y_top": row["top"],
        "y_bot": row["bot"],
        "v_lines_row": v_row,
        "digits": digits,
        "confidences": confs,
        "value": value,
        "ink": inks,
        "crops": crop_paths,
        "subcells": subcells,
    }


def extract_candidates(
    pdf: Path,
    engine,
    pad: int = grid.CELL_PAD,
    save_debug_dir: Path | None = None,
) -> dict:
    """Extract C1/C2 with row-scoped sub-cell detection. No-op if grid empty."""
    img = grid.render_page(pdf)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    v_global = grid.detect_gray_v_lines(gray)
    if len(v_global) < 4:
        v_global = grid.SUBCELL_X_FALLBACK

    h_lines = grid.detect_gray_h_lines(gray)
    grid_rows = grid.build_grid(v_global, h_lines)
    row_map = find_candidate_rows(gray, grid_rows, pad)

    candidates_out = []
    for label in CANDIDATE_LABELS:
        row = row_map.get(label)
        if row is None:
            continue
        entry = read_candidate_row(
            gray, img, row, label, v_global, engine, pad, save_debug_dir
        )
        entry["v_lines_global"] = v_global
        candidates_out.append(entry)

    return {
        "pdf": str(pdf),
        "candidates": candidates_out,
        "found_labels": [c["label"] for c in candidates_out],
    }


def process_pdf(pdf: Path, out_dir: Path, pad: int, engine) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    report = extract_candidates(pdf, engine, pad=pad, save_debug_dir=out_dir)
    for c in report["candidates"]:
        print(f"  {c['label']}: v_row={c['v_lines_row']} digits={c['digits']} value={c['value']}")
    return report


def main():
    parser = argparse.ArgumentParser(description="Export candidate 3-subcell groups")
    parser.add_argument("--pdf", type=Path, default=None, help="Single PDF path")
    parser.add_argument("--pad", type=int, default=grid.CELL_PAD)
    parser.add_argument("--out", type=Path, default=Path("debug_sv/candidate_crops"))
    args = parser.parse_args()

    if args.pdf:
        pdf = args.pdf
    else:
        pdfs = sorted((ROOT / "data/pdfs_e14c_segunda").rglob("*.pdf"))
        if not pdfs:
            print("No PDFs found")
            sys.exit(1)
        pdf = pdfs[0]

    args.out.mkdir(parents=True, exist_ok=True)
    print(f"PDF: {pdf.name}")
    print(f"Output: {args.out}")
    print(f"Pad: {args.pad}px")

    engine = grid.SegmentedEngine()
    report = process_pdf(pdf, args.out, args.pad, engine)

    out_json = args.out / "candidate_subcells.json"
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nSaved crops + debug to {args.out}")
    print(f"Saved: {out_json}")


if __name__ == "__main__":
    main()