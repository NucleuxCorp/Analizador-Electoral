"""
Grid detector using corner mark detection.

Finds black square markers at form corners and projects the voting grid
as percentage-based subcells relative to the corner quadrilateral.

The approach is intentionally form-agnostic: it only assumes that the
E-14 scan has four dark square registration marks near the page corners
and that the digit grid lives at fixed relative positions inside the
quadrilateral defined by those marks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

# -----------------------------------------------------------------------------
# Calibration constants
# -----------------------------------------------------------------------------
# Column layout for the 3-digit subcells (percentages relative to the corner
# quadrilateral). Extracted from debug_sv/corner_grid_calibrator.py.
_COL_LEFT_PCT = 74.1242
_COL_SUBCELL_WIDTH_PCT = 8.3187
_N_SUBCELLS = 3

# Row layout: semantic label and (top%, bottom%) inside the corner quadrilateral.
# The percentages come from the reference calibration at 300 DPI and are reused
# for all forms because the printed E-14 layout is constant nationwide.
_ROW_DEFINITIONS: list[tuple[str, float, float]] = [
    ("VOTERS", 23.8515, 27.0032),
    ("URN", 27.5641, 30.1015),
    ("INCINERATED", 30.8226, 33.3867),
    ("CANDIDATE_1", 44.9252, 47.4092),
    ("CANDIDATE_2", 61.0844, 63.5684),
    ("BLANK", 72.1421, 75.2404),
    ("NULLS", 75.8814, 78.4188),
    ("NOT_MARKED", 79.1399, 81.6506),
    ("TOTAL_SUM", 82.2115, 84.6688),
]

# Corner detection parameters
_AREA_MIN = 200
_AREA_MAX = 5000
_SQUARE_CIRCULARITY_MIN = 0.60
_SQUARE_CIRCULARITY_MAX = 1.30
_ASPECT_MIN = 0.70
_ASPECT_MAX = 1.30
_SOLIDITY_MIN = 0.70

# Position margin as a fraction of the page dimension. A 10% margin tolerates
# slight scanning/printing drift while still rejecting interior noise.
_CORNER_MARGIN_X = 0.10
_CORNER_MARGIN_Y = 0.10

# Tolerance for cross-validating the two diagonal corner pairs (pixels).
_DIAG_AGREEMENT_TOLERANCE = 20.0


# -----------------------------------------------------------------------------
# Data classes
# -----------------------------------------------------------------------------
@dataclass
class CornerMark:
    x: int
    y: int
    confidence: float
    corner_label: str  # "TL", "TR", "BL", "BR"


@dataclass
class SubCell:
    x1: int
    y1: int
    x2: int
    y2: int
    row_idx: int
    subcell_idx: int  # 0, 1, 2 (left, center, right)
    cell_label: str


@dataclass
class GridResult:
    corners: list[CornerMark]
    cells: list[SubCell]
    page_width: int
    page_height: int
    confidence: float
    method: str  # "corners", "fixed"


# -----------------------------------------------------------------------------
# Corner detection
# -----------------------------------------------------------------------------
def find_corner_marks(gray: np.ndarray) -> list[CornerMark]:
    """
    Find black square markers at page corners via contour detection.

    Pipeline:
      1. Otsu threshold (binary inverse).
      2. Find external contours.
      3. Filter by area, square-like circularity, aspect ratio and solidity.
      4. Keep only candidates near a page corner.
      5. Pick the best candidate per corner and label TL/TR/BL/BR.
    """
    h, w = gray.shape[:2]
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    margin_x = w * _CORNER_MARGIN_X
    margin_y = h * _CORNER_MARGIN_Y

    candidates: list[tuple[float, float, float, float]] = []  # cx, cy, conf, area
    for contour in contours:
        area = cv2.contourArea(contour)
        if not (_AREA_MIN <= area <= _AREA_MAX):
            continue

        perimeter = cv2.arcLength(contour, True)
        if perimeter <= 0:
            continue

        circularity = (4.0 * math.pi * area) / (perimeter * perimeter)
        if not (_SQUARE_CIRCULARITY_MIN <= circularity <= _SQUARE_CIRCULARITY_MAX):
            continue

        x, y, cw, ch = cv2.boundingRect(contour)
        if cw <= 0 or ch <= 0:
            continue

        aspect = cw / ch
        if not (_ASPECT_MIN <= aspect <= _ASPECT_MAX):
            continue

        bbox_area = cw * ch
        solidity = area / bbox_area if bbox_area > 0 else 0.0
        if solidity < _SOLIDITY_MIN:
            continue

        cx = x + cw / 2.0
        cy = y + ch / 2.0

        # Keep only candidates that are close to one of the page corners.
        near_left = cx < margin_x
        near_right = cx > w - margin_x
        near_top = cy < margin_y
        near_bottom = cy > h - margin_y
        if not ((near_left or near_right) and (near_top or near_bottom)):
            continue

        confidence = _corner_confidence(circularity, aspect, solidity)
        candidates.append((cx, cy, confidence, area))

    if not candidates:
        return []

    # Pick the best candidate for each corner.
    quadrants = {
        "TL": lambda cx, cy: cx < margin_x and cy < margin_y,
        "TR": lambda cx, cy: cx > w - margin_x and cy < margin_y,
        "BL": lambda cx, cy: cx < margin_x and cy > h - margin_y,
        "BR": lambda cx, cy: cx > w - margin_x and cy > h - margin_y,
    }
    distance_to_corner = {
        "TL": lambda cx, cy: cx + cy,
        "TR": lambda cx, cy: (w - cx) + cy,
        "BL": lambda cx, cy: cx + (h - cy),
        "BR": lambda cx, cy: (w - cx) + (h - cy),
    }

    marks: list[CornerMark] = []
    used: set[int] = set()
    for label, in_quadrant in quadrants.items():
        in_quad = [
            (i, c) for i, c in enumerate(candidates) if i not in used and in_quadrant(c[0], c[1])
        ]
        if not in_quad:
            continue
        best_idx, best = min(in_quad, key=lambda item: distance_to_corner[label](item[1][0], item[1][1]))
        used.add(best_idx)
        marks.append(
            CornerMark(
                x=int(round(best[0])),
                y=int(round(best[1])),
                confidence=best[2],
                corner_label=label,
            )
        )

    # Return in a deterministic order.
    order = {"TL": 0, "TR": 1, "BL": 2, "BR": 3}
    marks.sort(key=lambda m: order[m.corner_label])
    return marks


def _corner_confidence(circularity: float, aspect: float, solidity: float) -> float:
    """Combine shape cues into a 0..1 confidence score."""
    squareness = 1.0 - abs(circularity - math.pi / 4.0) / (math.pi / 4.0)
    aspect_score = 1.0 - abs(aspect - 1.0)
    score = (squareness + aspect_score + solidity) / 3.0
    return float(max(0.0, min(1.0, score)))


# -----------------------------------------------------------------------------
# Geometry helpers
# -----------------------------------------------------------------------------
def _to_points(corners: list[CornerMark]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return (tl, tr, bl, br) as float arrays from a sorted corner list."""
    by_label = {m.corner_label: m for m in corners}
    tl = np.array([by_label["TL"].x, by_label["TL"].y], dtype=float)
    tr = np.array([by_label["TR"].x, by_label["TR"].y], dtype=float)
    bl = np.array([by_label["BL"].x, by_label["BL"].y], dtype=float)
    br = np.array([by_label["BR"].x, by_label["BR"].y], dtype=float)
    return tl, tr, bl, br


def _label_order(corners: list[CornerMark]) -> list[CornerMark]:
    """Sort corners TL, TR, BL, BR."""
    order = {"TL": 0, "TR": 1, "BL": 2, "BR": 3}
    return sorted(corners, key=lambda m: order[m.corner_label])


def _infer_missing_corner(by_label: dict[str, CornerMark]) -> dict[str, CornerMark]:
    """Infer the fourth corner of a parallelogram from any three known corners."""
    labels = set(by_label)
    if len(labels) == 4:
        return by_label

    missing = ({"TL", "TR", "BL", "BR"} - labels).pop()
    existing = list(by_label.values())
    avg_conf = float(np.mean([m.confidence for m in existing]))

    if missing == "BR":
        # BR = TR + BL - TL
        tl, tr, bl = by_label["TL"], by_label["TR"], by_label["BL"]
        x = tr.x + bl.x - tl.x
        y = tr.y + bl.y - tl.y
    elif missing == "TL":
        # TL = TR + BL - BR
        tr, bl, br = by_label["TR"], by_label["BL"], by_label["BR"]
        x = tr.x + bl.x - br.x
        y = tr.y + bl.y - br.y
    elif missing == "TR":
        # TR = TL + BR - BL
        tl, bl, br = by_label["TL"], by_label["BL"], by_label["BR"]
        x = tl.x + br.x - bl.x
        y = tl.y + br.y - bl.y
    else:  # missing == "BL"
        # BL = TL + BR - TR
        tl, tr, br = by_label["TL"], by_label["TR"], by_label["BR"]
        x = tl.x + br.x - tr.x
        y = tl.y + br.y - tr.y

    by_label[missing] = CornerMark(int(round(x)), int(round(y)), avg_conf * 0.9, missing)
    return by_label


def _project_from_diagonal(
    p1: CornerMark, p2: CornerMark, aspect: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Given two diagonal corners and the page aspect ratio (W/H), reconstruct
    the full rectangle and return (tl, tr, bl, br).

    Handles both TL+BR and TR+BL diagonal pairs.
    """
    labels = {p1.corner_label, p2.corner_label}
    diagonal_type = "tr-bl" if labels == {"TR", "BL"} else "tl-br"

    p1_arr = np.array([p1.x, p1.y], dtype=float)
    p2_arr = np.array([p2.x, p2.y], dtype=float)
    center = (p1_arr + p2_arr) / 2.0
    half_diag = (p2_arr - p1_arr) / 2.0
    diag_len = float(np.linalg.norm(half_diag) * 2.0)

    # Half side lengths consistent with the aspect ratio and diagonal length.
    half_w = diag_len * aspect / (2.0 * math.sqrt(1.0 + aspect * aspect))
    half_h = diag_len / (2.0 * math.sqrt(1.0 + aspect * aspect))
    phi = math.atan2(half_h, half_w)

    diag_angle = math.atan2(half_diag[1], half_diag[0])
    if diagonal_type == "tl-br":
        # d = half_w * u1 + half_h * u2
        theta = diag_angle - phi
    else:
        # d = -half_w * u1 + half_h * u2
        theta = diag_angle + phi - math.pi

    u1 = np.array([math.cos(theta), math.sin(theta)], dtype=float)
    u2 = np.array([-math.sin(theta), math.cos(theta)], dtype=float)

    w_vec = half_w * u1
    h_vec = half_h * u2

    tl = center - w_vec - h_vec
    tr = center + w_vec - h_vec
    bl = center - w_vec + h_vec
    br = center + w_vec + h_vec
    return tl, tr, bl, br


def _sort_corners(points: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Sort four convex corner points into TL, TR, BL, BR."""
    # Sort by y, then split top/bottom halves and sort each by x.
    pts = sorted(points, key=lambda p: (p[1], p[0]))
    top = sorted(pts[:2], key=lambda p: p[0])
    bottom = sorted(pts[2:], key=lambda p: p[0])
    return top[0], top[1], bottom[0], bottom[1]


def _predict_quad_from_diagonal(
    p1: CornerMark, p2: CornerMark, aspect: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Convenience wrapper that predicts the full quadrilateral from a diagonal pair."""
    return _project_from_diagonal(p1, p2, aspect)


def _page_quad(page_w: int, page_h: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Fallback quadrilateral that covers the whole page."""
    return (
        np.array([0.0, 0.0]),
        np.array([float(page_w), 0.0]),
        np.array([0.0, float(page_h)]),
        np.array([float(page_w), float(page_h)]),
    )


def _bilinear_point(
    tl: np.ndarray,
    tr: np.ndarray,
    bl: np.ndarray,
    br: np.ndarray,
    u: float,
    v: float,
) -> tuple[float, float]:
    """Map normalized coordinates (u, v) into the quadrilateral."""
    top = (1.0 - u) * tl + u * tr
    bottom = (1.0 - u) * bl + u * br
    point = (1.0 - v) * top + v * bottom
    return float(point[0]), float(point[1])


def _build_cells(
    quad: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray],
    page_w: int,
    page_h: int,
) -> list[SubCell]:
    """Project every row/subcell from percentage coordinates to pixel boxes."""
    tl, tr, bl, br = quad
    cells: list[SubCell] = []
    for row_idx, (label, y_top_pct, y_bot_pct) in enumerate(_ROW_DEFINITIONS):
        for sub_idx in range(_N_SUBCELLS):
            x_left_pct = _COL_LEFT_PCT + sub_idx * _COL_SUBCELL_WIDTH_PCT
            x_right_pct = x_left_pct + _COL_SUBCELL_WIDTH_PCT

            x1f, y1f = _bilinear_point(tl, tr, bl, br, x_left_pct / 100.0, y_top_pct / 100.0)
            x2f, y2f = _bilinear_point(tl, tr, bl, br, x_right_pct / 100.0, y_bot_pct / 100.0)

            x1 = max(0, min(page_w, int(round(x1f))))
            y1 = max(0, min(page_h, int(round(y1f))))
            x2 = max(0, min(page_w, int(round(x2f))))
            y2 = max(0, min(page_h, int(round(y2f))))

            cells.append(
                SubCell(
                    x1=x1,
                    y1=y1,
                    x2=x2,
                    y2=y2,
                    row_idx=row_idx,
                    subcell_idx=sub_idx,
                    cell_label=label,
                )
            )
    return cells


def _validate_diagonal_pairs(
    quad: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray], aspect: float
) -> bool:
    """Return True if both diagonal pairs project to the observed quadrilateral."""
    tl, tr, bl, br = quad
    tl_m = CornerMark(int(round(tl[0])), int(round(tl[1])), 1.0, "TL")
    br_m = CornerMark(int(round(br[0])), int(round(br[1])), 1.0, "BR")
    tr_m = CornerMark(int(round(tr[0])), int(round(tr[1])), 1.0, "TR")
    bl_m = CornerMark(int(round(bl[0])), int(round(bl[1])), 1.0, "BL")

    pred1 = _predict_quad_from_diagonal(tl_m, br_m, aspect)
    pred2 = _predict_quad_from_diagonal(tr_m, bl_m, aspect)

    def distance_to_closest(point: np.ndarray, preds: tuple[np.ndarray, ...]) -> float:
        return min(float(np.linalg.norm(point - p)) for p in preds)

    agree1 = all(distance_to_closest(p, pred1) <= _DIAG_AGREEMENT_TOLERANCE for p in quad)
    agree2 = all(distance_to_closest(p, pred2) <= _DIAG_AGREEMENT_TOLERANCE for p in quad)
    return agree1 and agree2


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------
def project_grid_from_corners(
    corners: list[CornerMark],
    page_w: int,
    page_h: int,
    known_aspect: Optional[float] = None,
) -> GridResult:
    """
    Reconstruct the full corner quadrilateral from the detected corners and
    project the voting grid as percentage-based subcells.

    Supported inputs:
      - 4 corners: direct quadrilateral, with diagonal-pair cross-validation.
      - 3 corners: infer the fourth as a parallelogram completion.
      - TL+BR or TR+BL diagonal pair: project the remaining two corners using
        the known page aspect ratio.
      - 0/1 corner or adjacent pair: fall back to the full page rectangle.
    """
    aspect = known_aspect if known_aspect is not None else (page_w / page_h if page_h else 1.0)
    by_label = {m.corner_label: m for m in corners}
    n = len(by_label)

    if n == 4:
        quad = _to_points(_label_order(corners))
        avg_conf = float(np.mean([m.confidence for m in corners]))
        if _validate_diagonal_pairs(quad, aspect):
            confidence = 0.95 * avg_conf
        else:
            confidence = 0.75 * avg_conf
        method = "corners"
        result_corners = _label_order(corners)
    elif n == 3:
        by_label = _infer_missing_corner(by_label)
        ordered = [
            by_label["TL"],
            by_label["TR"],
            by_label["BL"],
            by_label["BR"],
        ]
        quad = _to_points(ordered)
        avg_conf = float(np.mean([m.confidence for m in ordered]))
        confidence = 0.85 * avg_conf
        method = "corners"
        result_corners = ordered
    elif {"TL", "BR"} <= by_label.keys():
        quad = _project_from_diagonal(by_label["TL"], by_label["BR"], aspect)
        confidence = 0.70
        method = "corners"
        result_corners = [
            CornerMark(int(round(quad[0][0])), int(round(quad[0][1])), confidence, "TL"),
            CornerMark(int(round(quad[1][0])), int(round(quad[1][1])), confidence, "TR"),
            CornerMark(int(round(quad[2][0])), int(round(quad[2][1])), confidence, "BL"),
            CornerMark(int(round(quad[3][0])), int(round(quad[3][1])), confidence, "BR"),
        ]
    elif {"TR", "BL"} <= by_label.keys():
        quad = _project_from_diagonal(by_label["TR"], by_label["BL"], aspect)
        confidence = 0.70
        method = "corners"
        result_corners = [
            CornerMark(int(round(quad[0][0])), int(round(quad[0][1])), confidence, "TL"),
            CornerMark(int(round(quad[1][0])), int(round(quad[1][1])), confidence, "TR"),
            CornerMark(int(round(quad[2][0])), int(round(quad[2][1])), confidence, "BL"),
            CornerMark(int(round(quad[3][0])), int(round(quad[3][1])), confidence, "BR"),
        ]
    else:
        quad = _page_quad(page_w, page_h)
        confidence = 0.40
        method = "fixed"
        result_corners = [
            CornerMark(0, 0, confidence, "TL"),
            CornerMark(page_w, 0, confidence, "TR"),
            CornerMark(0, page_h, confidence, "BL"),
            CornerMark(page_w, page_h, confidence, "BR"),
        ]

    cells = _build_cells(quad, page_w, page_h)
    return GridResult(
        corners=result_corners,
        cells=cells,
        page_width=page_w,
        page_height=page_h,
        confidence=float(np.clip(confidence, 0.0, 1.0)),
        method=method,
    )


def detect_grid(page_image: np.ndarray) -> GridResult:
    """
    Main entry point: detect black corner marks and project the digit grid.

    Accepts a grayscale or BGR page image.
    """
    if page_image.ndim == 3:
        gray = cv2.cvtColor(page_image, cv2.COLOR_BGR2GRAY)
    else:
        gray = page_image

    page_h, page_w = gray.shape[:2]
    corners = find_corner_marks(gray)
    return project_grid_from_corners(corners, page_w, page_h)
