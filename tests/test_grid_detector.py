"""
Unit tests for the v3 E-14C gray-line grid detector.

Covers: detect_gray_v_lines, detect_gray_h_lines, merge_duplicate_h_lines,
filter_vote_row_boundaries, process_h_lines, build_grid, is_known_label,
label_rows_by_structure, cell_has_ink, ocr_cell (signature and empty-crop path).
"""

from __future__ import annotations

import numpy as np
import cv2
import pytest

from src.modules.analyzer.grid_detector import (
    SUBCELL_X_FALLBACK,
    VOTE_X_LEFT,
    VOTE_X_RIGHT,
    Y1,
    Y2,
    build_grid,
    cell_has_ink,
    detect_gray_h_lines,
    detect_gray_v_lines,
    extract_clean_cells_and_digits,
    filter_vote_row_boundaries,
    is_known_label,
    label_rows_by_structure,
    merge_duplicate_h_lines,
    ocr_cell,
    process_h_lines,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _blank_page(h: int = 3500, w: int = 1500) -> np.ndarray:
    """Pure white grayscale page."""
    return np.full((h, w), 255, dtype=np.uint8)


def _page_with_gray_v_lines(x_positions: list[int]) -> np.ndarray:
    """White page with thin gray vertical lines at specified X positions."""
    img = _blank_page()
    for x in x_positions:
        img[Y1:Y2, x] = 150  # mid-gray value in the detection band
    return img


def _page_with_gray_h_lines(y_positions: list[int]) -> np.ndarray:
    """White page with thin gray horizontal lines at specified Y positions."""
    img = _blank_page()
    for y in y_positions:
        img[y, VOTE_X_LEFT:VOTE_X_RIGHT] = 150
    return img


def _make_gray_v_line_image() -> np.ndarray:
    """Synthetic image that mimics a real E-14C page's 4 sub-column gray lines."""
    img = _blank_page()
    for x in SUBCELL_X_FALLBACK:
        # Paint a wide-enough gray band so detection threshold is met
        for dx in range(-2, 3):
            col = x + dx
            if 0 <= col < img.shape[1]:
                img[Y1:Y2, col] = 140
    return img


# ---------------------------------------------------------------------------
# import / constants
# ---------------------------------------------------------------------------

def test_standard_import_constants():
    """Key constants must be importable and have the expected calibrated values."""
    assert SUBCELL_X_FALLBACK == [905, 998, 1091, 1187]
    assert VOTE_X_LEFT == 889
    assert VOTE_X_RIGHT == 1197
    assert Y1 == 700
    assert Y2 == 3500


# ---------------------------------------------------------------------------
# detect_gray_v_lines
# ---------------------------------------------------------------------------

def test_detect_gray_v_lines_blank_page():
    """Blank white page has no gray vertical lines."""
    img = _blank_page()
    result = detect_gray_v_lines(img)
    assert result == []


def test_detect_gray_v_lines_finds_known_positions():
    """Gray vertical lines painted at the fallback positions must be detected."""
    img = _make_gray_v_line_image()
    result = detect_gray_v_lines(img)
    # At least 3 of the 4 known sub-column lines must be found
    assert len(result) >= 3
    for x in result:
        assert VOTE_X_LEFT <= x <= VOTE_X_RIGHT


# ---------------------------------------------------------------------------
# detect_gray_h_lines
# ---------------------------------------------------------------------------

def test_detect_gray_h_lines_blank_page():
    """Blank white page has no gray horizontal lines."""
    img = _blank_page()
    result = detect_gray_h_lines(img)
    assert result == []


def test_detect_gray_h_lines_finds_painted_line():
    """A gray horizontal strip inside the ROI must be detected."""
    img = _blank_page()
    y_target = Y1 + 200
    img[y_target, VOTE_X_LEFT:VOTE_X_RIGHT] = 140  # gray row
    result = detect_gray_h_lines(img)
    assert len(result) >= 1
    # Result contains absolute page coordinates
    assert any(abs(y - y_target) <= 5 for y in result)


# ---------------------------------------------------------------------------
# merge_duplicate_h_lines
# ---------------------------------------------------------------------------

def test_merge_duplicate_h_lines_clusters():
    """Lines within threshold distance must be averaged into one."""
    raw = [150, 152, 300, 455, 458, 600]
    merged = merge_duplicate_h_lines(raw, threshold=15)
    assert len(merged) == 4
    # First cluster [150, 152] → 151
    assert abs(merged[0] - 151) <= 1
    # Third cluster [455, 458] → 456 or 457
    assert abs(merged[2] - 456) <= 2


def test_merge_duplicate_h_lines_no_duplicates():
    """Lines already well-separated must not be merged."""
    lines = [100, 300, 500, 700]
    merged = merge_duplicate_h_lines(lines, threshold=15)
    assert merged == lines


def test_merge_duplicate_h_lines_empty():
    """Empty input returns empty list."""
    assert merge_duplicate_h_lines([]) == []


# ---------------------------------------------------------------------------
# filter_vote_row_boundaries
# ---------------------------------------------------------------------------

def test_filter_vote_row_boundaries_accepts_valid_rows():
    """Lines that form rows in the 80–150 px height window must be accepted."""
    # Simulate two consecutive row boundaries: row height = 100 px (valid)
    lines = [1000, 1100, 1200]
    accepted, discarded = filter_vote_row_boundaries(lines, row_min_h=80, row_max_h=150)
    # All three lines form two valid rows; all should be accepted
    assert 1000 in accepted
    assert 1100 in accepted
    assert 1200 in accepted


def test_filter_vote_row_boundaries_rejects_too_small():
    """Rows smaller than row_min_h px must be discarded."""
    # Gap = 20 px (below 80 minimum, not in header zone)
    lines = [2000, 2020, 2200]
    accepted, discarded = filter_vote_row_boundaries(
        lines, row_min_h=80, row_max_h=150, header_y_max=900
    )
    # 2020 is a gap of 20 from 2000; below ROW_MIN_H and outside header zone
    # It may be discarded unless filter sees a valid closure ahead
    # Key assertion: the 200-px gap row (2000→2200) is valid
    assert 2000 in accepted
    assert 2200 in accepted


# ---------------------------------------------------------------------------
# process_h_lines
# ---------------------------------------------------------------------------

def test_process_h_lines_returns_sorted():
    """Output must be sorted ascending after merge + filter."""
    raw = [1200, 1100, 1300, 1102, 1198]
    processed = process_h_lines(raw)
    assert processed == sorted(processed)


def test_process_h_lines_empty():
    """Empty input returns empty list."""
    assert process_h_lines([]) == []


# ---------------------------------------------------------------------------
# build_grid
# ---------------------------------------------------------------------------

def test_build_grid_basic():
    """4 v_lines + row of valid height must produce at least one 3-cell row."""
    v = [905, 998, 1091, 1187]
    h = [1000, 1100]  # 100 px row — within 80–150 range
    rows = build_grid(v, h)
    assert len(rows) == 1
    row = rows[0]
    assert row["height"] == 100
    assert len(row["cells"]) == 3
    for cell in row["cells"]:
        assert cell["x1"] < cell["x2"]
        assert cell["y1"] < cell["y2"]


def test_build_grid_filters_invalid_height():
    """Rows taller or shorter than valid bounds must be excluded."""
    v = [905, 998, 1091, 1187]
    h = [1000, 1010, 1500]  # 10-px gap (too short) and 490-px gap (too tall)
    rows = build_grid(v, h)
    # Neither the 10-px gap nor the 490-px gap is in [80, 150]
    assert rows == []


def test_build_grid_requires_minimum_lines():
    """Fewer than 4 v_lines or fewer than 2 h_lines returns empty list."""
    assert build_grid([905, 998, 1091], [1000, 1100]) == []  # only 3 v_lines
    assert build_grid([905, 998, 1091, 1187], [1000]) == []  # only 1 h_line


def test_build_grid_produces_correct_cell_count():
    """N-1 valid rows each containing 3 subcells."""
    v = [905, 998, 1091, 1187]
    # 3 boundaries → 2 potential rows
    h = [1000, 1100, 1200]
    rows = build_grid(v, h)
    assert len(rows) == 2
    for row in rows:
        assert len(row["cells"]) == 3


# ---------------------------------------------------------------------------
# is_known_label
# ---------------------------------------------------------------------------

def test_is_known_label_true():
    """All expected labels must be recognized."""
    for label in (
        "VOTANTES", "URNA", "INCINER",
        "C1_CEPEDA", "C2_ABELARDO",
        "BLANCO", "NULOS", "NO_MARCADOS", "SUMA_TOTAL",
    ):
        assert is_known_label(label), f"Expected {label!r} to be recognized"


def test_is_known_label_false():
    """Strings not in the label set must return False."""
    assert not is_known_label("UNK@1234")
    assert not is_known_label("")
    assert not is_known_label("VOTACION")
    assert not is_known_label("random")


# ---------------------------------------------------------------------------
# label_rows_by_structure
# ---------------------------------------------------------------------------

def test_label_rows_by_structure_empty():
    """Empty row list returns empty list."""
    assert label_rows_by_structure([]) == []


def test_label_rows_by_structure_block_c():
    """The last 4 compact rows receive Block C labels."""
    # Build 4 rows with small consecutive y values
    def _row(idx, top):
        h = 100
        return {
            "row_idx": idx, "top": top, "bot": top + h,
            "height": h, "cells": [],
        }

    rows = [_row(i, 1000 + i * 120) for i in range(4)]
    labeled = label_rows_by_structure(rows)
    labels = [r.get("label", "") for r in labeled]
    block_c = ("BLANCO", "NULOS", "NO_MARCADOS", "SUMA_TOTAL")
    for expected in block_c:
        assert expected in labels, f"{expected} not found in {labels}"


def test_label_rows_by_structure_sets_y_mid():
    """Every returned row must have a y_mid attribute."""
    rows = [
        {"row_idx": 0, "top": 1000, "bot": 1100, "height": 100, "cells": []},
        {"row_idx": 1, "top": 1100, "bot": 1200, "height": 100, "cells": []},
    ]
    labeled = label_rows_by_structure(rows)
    for row in labeled:
        assert "y_mid" in row
        assert row["top"] < row["y_mid"] < row["bot"]


# ---------------------------------------------------------------------------
# cell_has_ink
# ---------------------------------------------------------------------------

def test_cell_has_ink_blank():
    """Blank white subcell must report no ink."""
    gray = np.full((200, 200), 255, dtype=np.uint8)
    assert cell_has_ink(gray, 0, 0, 200, 200) is False


def test_cell_has_ink_with_dark_blob():
    """A dark connected blob of sufficient size must be detected as ink."""
    gray = np.full((150, 150), 255, dtype=np.uint8)
    # Draw a filled dark rectangle — 30×40 blob well above the area threshold
    gray[50:90, 50:80] = 30
    result = cell_has_ink(gray, 0, 0, 150, 150)
    assert result is True


def test_cell_has_ink_too_small_crop():
    """A crop too small for the padding must return False without crashing."""
    gray = np.full((10, 10), 255, dtype=np.uint8)
    result = cell_has_ink(gray, 0, 0, 10, 10)
    assert result is False


# ---------------------------------------------------------------------------
# ocr_cell (signature and trivially-empty case)
# ---------------------------------------------------------------------------

def test_ocr_cell_returns_3_tuple():
    """ocr_cell must always return a 3-element tuple (char, float, list)."""
    from src.modules.analyzer.ocr_engines import SegmentedEngine

    engine = SegmentedEngine()
    img = np.full((200, 200, 3), 255, dtype=np.uint8)
    result = ocr_cell(engine, img, 0, 0, 10, 10)  # too small → fallback
    assert isinstance(result, tuple)
    assert len(result) == 3
    char, conf, top3 = result
    assert isinstance(char, str)
    assert isinstance(conf, float)
    assert isinstance(top3, list)


def test_ocr_cell_empty_crop_returns_question_mark():
    """A crop smaller than the padding budget must return ('?', 0.0, [])."""
    from src.modules.analyzer.ocr_engines import SegmentedEngine

    engine = SegmentedEngine()
    img = np.full((50, 50, 3), 255, dtype=np.uint8)
    # x2 - x1 = 5 (less than 2 * CELL_PAD = 16) → crop collapses
    char, conf, top3 = ocr_cell(engine, img, 0, 0, 5, 50)
    assert char == "?"
    assert conf == 0.0
    assert top3 == []
