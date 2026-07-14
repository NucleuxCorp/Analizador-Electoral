"""
Grid detector fallback: detect black frame borders of the voting table.

When the four corner registration marks are missing or unusable, the printed
black outer frame of the E-14 voting table is still usually visible. This
module detects that frame, extracts the internal horizontal and vertical grid
lines when possible, and builds the same SubCell list produced by the corner
based detector.
"""

from __future__ import annotations

import cv2
import numpy as np

from .grid_detector_corners import (
    CornerMark,
    GridResult,
    SubCell,
    _COL_LEFT_PCT,
    _COL_SUBCELL_WIDTH_PCT,
    _N_SUBCELLS,
    _ROW_DEFINITIONS,
)


# ---------------------------------------------------------------------------
# Frame detection
# ---------------------------------------------------------------------------
def detect_voting_frame(gray: np.ndarray) -> tuple[int, int, int, int] | None:
    """
    Detect the outer black frame (bounding box) of the voting table.

    1. Threshold the image (binary inverse + Otsu).
    2. Close small gaps in the binary image.
    3. Find external contours and keep the largest rectangular one that is
       big enough to be the voting table but not the whole page.
    4. Return (x1, y1, x2, y2) of the frame.

    Returns None if no frame found.
    """
    h, w = gray.shape[:2]
    page_area = h * w

    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    best_box: tuple[int, int, int, int] | None = None
    best_score = 0.0

    for contour in contours:
        area = cv2.contourArea(contour)
        if area < page_area * 0.05:
            continue
        if area > page_area * 0.95:
            # This is the page border itself, not the inner voting frame.
            continue

        x, y, cw, ch = cv2.boundingRect(contour)
        if cw <= 0 or ch <= 0:
            continue

        bbox_area = cw * ch
        rectangularity = area / bbox_area if bbox_area > 0 else 0.0
        if rectangularity < 0.5:
            continue

        score = area * rectangularity
        if score > best_score:
            best_score = score
            best_box = (x, y, x + cw, y + ch)

    return best_box


# ---------------------------------------------------------------------------
# Line detection
# ---------------------------------------------------------------------------
def detect_horizontal_lines(gray: np.ndarray, roi: tuple[int, int, int, int]) -> list[int]:
    """
    Detect horizontal row separator lines within the frame ROI.

    Handles both gray lines (E14C) and black lines by using Otsu thresholding
    followed by a morphological close with a wide horizontal kernel and
    HoughLinesP.

    Returns absolute Y coordinates of detected horizontal lines.
    """
    x1, y1, x2, y2 = roi
    x1 = max(0, x1)
    y1 = max(0, y1)
    x2 = min(gray.shape[1], x2)
    y2 = min(gray.shape[0], y2)
    if x2 <= x1 or y2 <= y1:
        return []

    roi_img = gray[y1:y2, x1:x2]
    _, binary = cv2.threshold(roi_img, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    kernel_w = max(1, roi_img.shape[1] // 4)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_w, 1))
    morph = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

    lines = cv2.HoughLinesP(
        morph,
        rho=1,
        theta=np.pi / 180,
        threshold=50,
        minLineLength=max(30, roi_img.shape[1] // 3),
        maxLineGap=20,
    )

    if lines is None:
        return []

    ys = [int(round(y1 + (y1l + y2l) / 2.0)) for [[_x1l, y1l, _x2l, y2l]] in lines]
    return _cluster_1d(ys, threshold=5)


def detect_vertical_lines(gray: np.ndarray, roi: tuple[int, int, int, int]) -> list[int]:
    """
    Detect vertical subcell separator lines within the frame ROI.

    Uses a morphological close with a tall vertical kernel plus HoughLinesP.

    Returns absolute X coordinates of detected vertical lines.
    """
    x1, y1, x2, y2 = roi
    x1 = max(0, x1)
    y1 = max(0, y1)
    x2 = min(gray.shape[1], x2)
    y2 = min(gray.shape[0], y2)
    if x2 <= x1 or y2 <= y1:
        return []

    roi_img = gray[y1:y2, x1:x2]
    _, binary = cv2.threshold(roi_img, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    kernel_h = max(1, roi_img.shape[0] // 4)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, kernel_h))
    morph = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

    lines = cv2.HoughLinesP(
        morph,
        rho=1,
        theta=np.pi / 180,
        threshold=50,
        minLineLength=max(30, roi_img.shape[0] // 3),
        maxLineGap=20,
    )

    if lines is None:
        return []

    xs = [int(round(x1 + (x1l + x2l) / 2.0)) for [[x1l, _y1l, x2l, _y2l]] in lines]
    return _cluster_1d(xs, threshold=5)


def _cluster_1d(values: list[int], threshold: int) -> list[int]:
    """Cluster 1-D coordinates and return the centroid of each cluster."""
    if not values:
        return []

    values = sorted(set(values))
    clusters: list[list[int]] = [[values[0]]]
    for v in values[1:]:
        if v - clusters[-1][-1] <= threshold:
            clusters[-1].append(v)
        else:
            clusters.append([v])

    return [int(round(sum(c) / len(c))) for c in clusters]


# ---------------------------------------------------------------------------
# Grid construction
# ---------------------------------------------------------------------------
def _closest(lines: list[int], target: float, tol: int) -> tuple[int, bool]:
    """Return the line closest to target within tol, else target rounded."""
    if not lines:
        return int(round(target)), False
    best = min(lines, key=lambda v: abs(v - target))
    if abs(best - target) <= tol:
        return best, True
    return int(round(target)), False


def _match_horizontal_lines(
    h_lines: list[int], frame_top: int, frame_bottom: int, tol_px: int = 30
) -> tuple[list[tuple[str, int, int]], bool]:
    """Match detected horizontal lines to expected row boundaries.

    Returns (bands, used_detected) where bands is a list of
    (label, y_top, y_bot) and used_detected is True if at least one boundary
    came from an actual detected line.
    """
    frame_h = frame_bottom - frame_top
    bands: list[tuple[str, int, int]] = []
    used = False

    for label, top_pct, bot_pct in _ROW_DEFINITIONS:
        exp_top = frame_top + top_pct / 100.0 * frame_h
        exp_bot = frame_top + bot_pct / 100.0 * frame_h

        act_top, top_used = _closest(h_lines, exp_top, tol_px)
        act_bot, bot_used = _closest(h_lines, exp_bot, tol_px)

        used = used or top_used or bot_used
        if act_bot < act_top:
            act_top, act_bot = act_bot, act_top
        bands.append((label, act_top, act_bot))

    return bands, used


def _match_vertical_lines(
    v_lines: list[int], frame_left: int, frame_right: int, tol_px: int = 30
) -> tuple[list[int], bool]:
    """Match detected vertical lines to expected subcell X boundaries."""
    frame_w = frame_right - frame_left
    pcts = [_COL_LEFT_PCT + i * _COL_SUBCELL_WIDTH_PCT for i in range(_N_SUBCELLS + 1)]

    bounds: list[int] = []
    used = False
    for pct in pcts:
        exp = frame_left + pct / 100.0 * frame_w
        actual, was_used = _closest(v_lines, exp, tol_px)
        used = used or was_used
        bounds.append(actual)

    return bounds, used


def build_grid_from_frame(gray: np.ndarray, page_w: int, page_h: int) -> GridResult:
    """
    Main function for frame-based grid detection.

    1. Detect voting frame.
    2. Detect horizontal row separators and vertical subcell separators.
    3. Match detected lines to expected percentage-based rows/columns.
    4. Build SubCell list.
    5. If frame is not found, fall back to the full-page fixed grid.
    """
    from .grid_detector_corners import project_grid_from_corners

    frame = detect_voting_frame(gray)
    if frame is None:
        return project_grid_from_corners([], page_w, page_h)

    fx1, fy1, fx2, fy2 = frame

    h_lines = detect_horizontal_lines(gray, frame)
    v_lines = detect_vertical_lines(gray, frame)

    row_bands, h_used = _match_horizontal_lines(h_lines, fy1, fy2)
    x_bounds, v_used = _match_vertical_lines(v_lines, fx1, fx2)

    cells: list[SubCell] = []
    for row_idx, (label, y1, y2) in enumerate(row_bands):
        for sub_idx in range(_N_SUBCELLS):
            x_left = x_bounds[sub_idx]
            x_right = x_bounds[sub_idx + 1]
            cells.append(
                SubCell(
                    x1=max(0, min(page_w, x_left)),
                    y1=max(0, min(page_h, y1)),
                    x2=max(0, min(page_w, x_right)),
                    y2=max(0, min(page_h, y2)),
                    row_idx=row_idx,
                    subcell_idx=sub_idx,
                    cell_label=label,
                )
            )

    corners = [
        CornerMark(fx1, fy1, 0.8, "TL"),
        CornerMark(fx2, fy1, 0.8, "TR"),
        CornerMark(fx1, fy2, 0.8, "BL"),
        CornerMark(fx2, fy2, 0.8, "BR"),
    ]

    if h_used and v_used:
        confidence = 0.85
    elif h_used or v_used:
        confidence = 0.70
    else:
        confidence = 0.60

    return GridResult(
        corners=corners,
        cells=cells,
        page_width=page_w,
        page_height=page_h,
        confidence=confidence,
        method="frame",
    )
