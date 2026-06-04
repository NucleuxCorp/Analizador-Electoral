"""
Tachon (strikethrough) and visual alteration detector for E-14C vote cells.

Detects:
  1. TACHON: ink strokes crossing over a written digit (horizontal/diagonal lines)
  2. DOBLE_ESCRITURA: overlapping digits (a digit written over another)
  3. DENSIDAD_ALTA: abnormal ink density in a cell (too many strokes)
  4. ZONA_SUCIA: smudges or erasure marks around a digit

Each detector returns a score 0.0-1.0 and a label.
Combined score >= threshold → cell flagged as visually suspicious.

These detectors produce BOOTSTRAP LABELS for training the AI model.
They intentionally have HIGH RECALL / LOW PRECISION to catch edge cases.
Human review of flagged cells creates the ground truth dataset.
"""
import cv2
import numpy as np
from dataclasses import dataclass
from typing import Optional


TACHON_THRESHOLD   = 0.45   # score above this → flag as tachon
DENSITY_THRESHOLD  = 0.18   # ink area ratio above this → dense cell
NOISE_THRESHOLD    = 0.08   # background noise ratio above this → smudge/erasure


@dataclass
class CellAnalysis:
    score: float               # overall suspicion score 0.0–1.0
    tachon_score: float        # horizontal/diagonal stroke detection
    density_score: float       # ink density (high = many strokes)
    noise_score: float         # background noise (erasure marks)
    flags: list[str]           # which detectors triggered
    is_suspicious: bool

    def to_dict(self) -> dict:
        return {
            "score": round(self.score, 3),
            "tachon_score": round(self.tachon_score, 3),
            "density_score": round(self.density_score, 3),
            "noise_score": round(self.noise_score, 3),
            "flags": self.flags,
            "is_suspicious": self.is_suspicious,
        }


def _binarize(crop: np.ndarray) -> np.ndarray:
    """Convert to binary (dark ink = 255, white paper = 0)."""
    if len(crop.shape) == 3:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    else:
        gray = crop.copy()
    # Invert: ink becomes white
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return bw


def detect_tachon(cell: np.ndarray) -> float:
    """
    Detect horizontal or diagonal crossing strokes over digit cells.

    Method: morphological opening with long horizontal and diagonal kernels.
    A tachon is a connected ink segment that is much WIDER than a digit stroke
    and crosses the full width of the cell.

    Returns score 0.0-1.0.
    """
    bw = _binarize(cell)
    h, w = bw.shape

    if w < 20 or h < 20:
        return 0.0

    # Horizontal kernel: looks for strokes spanning >40% of cell width
    ksize = max(int(w * 0.4), 10)
    kernel_h = cv2.getStructuringElement(cv2.MORPH_RECT, (ksize, 1))
    h_strokes = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kernel_h)

    # Diagonal kernels (/ and \) — tachones are often diagonal
    kernel_d1 = np.eye(max(ksize // 3, 5), dtype=np.uint8)
    kernel_d2 = np.fliplr(kernel_d1)
    d1_strokes = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kernel_d1)
    d2_strokes = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kernel_d2)

    combined = cv2.bitwise_or(h_strokes, cv2.bitwise_or(d1_strokes, d2_strokes))

    stroke_pixels = combined.sum() / 255
    total_pixels = h * w

    # Normalize: what fraction of the cell area is crossing strokes?
    score = min(stroke_pixels / (total_pixels * 0.05), 1.0)
    return float(score)


def detect_density(cell: np.ndarray) -> float:
    """
    Measure total ink density in the cell.

    A clean 3-digit number has typical ink ratio ~5-12%.
    Tachones, double-writing, or excessive corrections push this higher.

    Returns score 0.0-1.0 (where 0 = normal, 1 = very dense).
    """
    bw = _binarize(cell)
    h, w = bw.shape
    if h * w == 0:
        return 0.0

    ink_ratio = (bw.sum() / 255) / (h * w)

    # Normal range: 0.04-0.15 → score starts rising at 0.15
    if ink_ratio <= DENSITY_THRESHOLD:
        return 0.0
    return min((ink_ratio - DENSITY_THRESHOLD) / 0.15, 1.0)


def detect_noise(cell: np.ndarray) -> float:
    """
    Detect smudges, erasure marks, or background noise around digits.

    Method: after removing the main digit strokes (large connected components),
    check remaining small noise blobs. Erasures leave characteristic gray patches.

    Returns score 0.0-1.0.
    """
    if len(cell.shape) == 3:
        gray = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY)
    else:
        gray = cell.copy()

    h, w = gray.shape

    # Look for intermediate gray values (70-200) — typical of erasure/smudge
    erasure_mask = cv2.inRange(gray, 70, 200)
    noise_ratio = erasure_mask.sum() / 255 / (h * w)

    if noise_ratio <= NOISE_THRESHOLD:
        return 0.0
    return min((noise_ratio - NOISE_THRESHOLD) / 0.20, 1.0)


def detect_double_writing(cell: np.ndarray) -> float:
    """
    Detect overlapping digits (a number written on top of another).

    Method: find connected components and check if any is abnormally complex
    (high number of holes, or skeleton branches > expected for a single digit).

    Returns score 0.0-1.0.
    """
    bw = _binarize(cell)

    # Label connected components
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(bw)

    # Exclude background (label 0) and very small specks
    large_components = [
        stats[i] for i in range(1, num_labels)
        if stats[i, cv2.CC_STAT_AREA] > 50
    ]

    if not large_components:
        return 0.0

    # For a clean 3-digit number, expect 3-4 components max
    # More components → possible double writing
    if len(large_components) <= 4:
        return 0.0

    excess = len(large_components) - 4
    return min(excess / 6.0, 1.0)


def analyze_cell(cell: np.ndarray) -> CellAnalysis:
    """
    Run all detectors on a vote-count cell crop.

    Args:
        cell: BGR numpy array of the digit cell region.

    Returns:
        CellAnalysis with individual scores and combined suspicion flag.
    """
    tachon  = detect_tachon(cell)
    density = detect_density(cell)
    noise   = detect_noise(cell)
    double  = detect_double_writing(cell)

    # Weighted combination — tachon and double_writing are strongest signals
    combined = (
        tachon  * 0.40 +
        double  * 0.30 +
        density * 0.20 +
        noise   * 0.10
    )

    flags = []
    if tachon  >= TACHON_THRESHOLD: flags.append("TACHON")
    if double  >= 0.5:              flags.append("DOBLE_ESCRITURA")
    if density >= 0.6:              flags.append("DENSIDAD_ALTA")
    if noise   >= 0.5:              flags.append("ZONA_SUCIA")

    return CellAnalysis(
        score=round(combined, 3),
        tachon_score=round(tachon, 3),
        density_score=round(density, 3),
        noise_score=round(noise, 3),
        flags=flags,
        is_suspicious=combined >= TACHON_THRESHOLD,
    )


def analyze_form_cells(
    pages: list[np.ndarray],
    vote_regions: list[tuple],
) -> list[tuple[int, CellAnalysis]]:
    """
    Run visual analysis on all vote-count cells from a form.

    Args:
        pages: list of BGR page images.
        vote_regions: list of (page, y1, y2, x1, x2) tuples.

    Returns:
        List of (cell_index, CellAnalysis) for suspicious cells only.
    """
    suspicious = []
    for i, (p, y1, y2, x1, x2) in enumerate(vote_regions, 1):
        if p >= len(pages):
            continue
        cell = pages[p][y1:y2, x1:x2]
        if cell.size == 0:
            continue
        result = analyze_cell(cell)
        if result.is_suspicious:
            suspicious.append((i, result))
    return suspicious


def save_debug_crops(
    pages: list[np.ndarray],
    vote_regions: list[tuple],
    out_dir,
    label_prefix: str = "cell",
) -> None:
    """Save cropped cell images for manual inspection and labeling."""
    from pathlib import Path
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for i, (p, y1, y2, x1, x2) in enumerate(vote_regions, 1):
        if p >= len(pages):
            continue
        cell = pages[p][y1:y2, x1:x2]
        result = analyze_cell(cell)
        tag = "_".join(result.flags) if result.flags else "ok"
        fname = out_dir / f"{label_prefix}_{i:02d}_{tag}.png"
        cv2.imwrite(str(fname), cell)
