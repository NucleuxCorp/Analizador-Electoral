"""
Unit tests for the E-14D fallback chain (grid_detector_e14d) and the underlying
corner-mark detection primitives (grid_detector_corners).

The unified detect_grid entry point is imported from grid_detector_e14d so that
the full corner → frame → fixed-fallback chain is exercised.  Primitive helpers
(find_corner_marks, project_grid_from_corners, CornerMark) are still imported
directly from grid_detector_corners.
"""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from src.modules.analyzer.grid_detector_corners import (
    CornerMark,
    find_corner_marks,
    project_grid_from_corners,
)
from src.modules.analyzer.grid_detector_e14d import detect_grid


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def _corner_by_label(result, label: str) -> CornerMark:
    for c in result.corners:
        if c.corner_label == label:
            return c
    raise KeyError(label)


# -----------------------------------------------------------------------------
# find_corner_marks
# -----------------------------------------------------------------------------
def test_find_corners_synthetic():
    """Black squares near the four page corners must be detected and labeled."""
    h, w = 800, 1000
    img = np.full((h, w), 255, dtype=np.uint8)
    size = 35
    positions = [
        (30, 30),  # TL
        (w - 30 - size, 30),  # TR
        (30, h - 30 - size),  # BL
        (w - 30 - size, h - 30 - size),  # BR
    ]
    for x, y in positions:
        img[y : y + size, x : x + size] = 0

    marks = find_corner_marks(img)

    assert len(marks) == 4
    labels = {m.corner_label for m in marks}
    assert labels == {"TL", "TR", "BL", "BR"}

    for mark in marks:
        assert 0.0 <= mark.confidence <= 1.0
        if mark.corner_label in {"TL", "BL"}:
            assert mark.x < w * 0.15
        else:
            assert mark.x > w * 0.85
        if mark.corner_label in {"TL", "TR"}:
            assert mark.y < h * 0.15
        else:
            assert mark.y > h * 0.85


# -----------------------------------------------------------------------------
# project_grid_from_corners
# -----------------------------------------------------------------------------
def test_project_two_corners():
    """A TL+BR diagonal pair should reconstruct the full grid."""
    page_w, page_h = 1000, 1400
    # Aspect ratio 900/1260 == page_w/page_h, so the reconstruction is axis-aligned.
    tl = CornerMark(50, 50, 1.0, "TL")
    br = CornerMark(950, 1310, 1.0, "BR")

    result = project_grid_from_corners([tl, br], page_w, page_h)

    assert result.method == "corners"
    assert result.page_width == page_w
    assert result.page_height == page_h
    assert len(result.corners) == 4
    assert {c.corner_label for c in result.corners} == {"TL", "TR", "BL", "BR"}
    assert len(result.cells) == 9 * 3  # 9 rows, 3 subcells each

    tr = _corner_by_label(result, "TR")
    bl = _corner_by_label(result, "BL")
    assert tr.x == pytest.approx(950, abs=2)
    assert tr.y == pytest.approx(50, abs=2)
    assert bl.x == pytest.approx(50, abs=2)
    assert bl.y == pytest.approx(1310, abs=2)

    # Every subcell must have a positive area and live inside the page.
    for cell in result.cells:
        assert cell.x1 < cell.x2
        assert cell.y1 < cell.y2
        assert 0 <= cell.x1 <= page_w
        assert 0 <= cell.x2 <= page_w
        assert 0 <= cell.y1 <= page_h
        assert 0 <= cell.y2 <= page_h
        assert cell.subcell_idx in {0, 1, 2}


def test_project_three_corners():
    """Three known corners should infer the fourth and produce a full grid."""
    page_w, page_h = 1000, 1400
    tl = CornerMark(50, 50, 1.0, "TL")
    tr = CornerMark(950, 50, 1.0, "TR")
    bl = CornerMark(50, 1310, 1.0, "BL")

    result = project_grid_from_corners([tl, tr, bl], page_w, page_h)

    assert result.method == "corners"
    assert len(result.corners) == 4
    br = _corner_by_label(result, "BR")
    assert br.x == pytest.approx(950, abs=2)
    assert br.y == pytest.approx(1310, abs=2)
    assert len(result.cells) == 9 * 3


def test_no_corners():
    """With no corners the detector must gracefully fall back to the full page."""
    page_w, page_h = 1000, 1400
    result = project_grid_from_corners([], page_w, page_h)

    assert result.method == "fixed"
    assert len(result.corners) == 4
    assert result.confidence < 0.5
    assert len(result.cells) == 9 * 3

    # Sanity: the cells are inside the page and have positive area.
    for cell in result.cells:
        assert cell.x1 < cell.x2
        assert cell.y1 < cell.y2


def test_diagonal_pair_validation():
    """Consistent 4-corner input gets high confidence; inconsistent gets lower."""
    page_w, page_h = 1000, 1400
    consistent = [
        CornerMark(50, 50, 1.0, "TL"),
        CornerMark(950, 50, 1.0, "TR"),
        CornerMark(50, 1310, 1.0, "BL"),
        CornerMark(950, 1310, 1.0, "BR"),
    ]
    good = project_grid_from_corners(consistent, page_w, page_h)
    assert good.confidence >= 0.9
    assert good.method == "corners"

    # Perturb BR so the diagonal pairs no longer agree.
    inconsistent = list(consistent)
    inconsistent[3] = CornerMark(700, 1200, 1.0, "BR")
    bad = project_grid_from_corners(inconsistent, page_w, page_h)
    assert bad.method == "corners"
    assert bad.confidence < good.confidence

    # Both diagonal pairs should produce compatible grids on consistent input.
    tlbr = project_grid_from_corners([consistent[0], consistent[3]], page_w, page_h)
    trbl = project_grid_from_corners([consistent[1], consistent[2]], page_w, page_h)
    assert tlbr.method == "corners"
    assert trbl.method == "corners"
    assert len(tlbr.cells) == len(trbl.cells)

    for label in {"TL", "TR", "BL", "BR"}:
        a = _corner_by_label(tlbr, label)
        b = _corner_by_label(trbl, label)
        assert a.x == pytest.approx(b.x, abs=3)
        assert a.y == pytest.approx(b.y, abs=3)


# -----------------------------------------------------------------------------
# detect_grid entry point
# -----------------------------------------------------------------------------
def test_detect_grid_blank_page():
    """A blank white page has no corner marks and should use the fixed fallback."""
    img = np.full((800, 1000, 3), 255, dtype=np.uint8)
    result = detect_grid(img)

    assert result.method == "fixed"
    assert result.page_width == 1000
    assert result.page_height == 800
    assert len(result.cells) == 9 * 3


def test_detect_grid_with_synthetic_marks():
    """A synthetic page with four corner marks should use the corner method."""
    h, w = 800, 1000
    img = np.full((h, w, 3), 255, dtype=np.uint8)
    size = 35
    positions = [
        (30, 30),
        (w - 30 - size, 30),
        (30, h - 30 - size),
        (w - 30 - size, h - 30 - size),
    ]
    for x, y in positions:
        cv2.rectangle(img, (x, y), (x + size, y + size), (0, 0, 0), thickness=-1)

    result = detect_grid(img)
    assert result.method == "corners"
    assert len(result.corners) == 4
    assert len(result.cells) == 9 * 3
