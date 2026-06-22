"""
Tests para merge_duplicate_h_lines y filter_vote_row_boundaries.

Caso bueno: mesa 001 zona 01_01 (Antioquia) — no debe perder filas vs build_grid sin filtro.
Caso malo: mesa 001 zona 01_03 — 49 líneas crudas deben reducirse a ~7–9 filas de grilla.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from debug_sv.grid_detector_v2 import (
    ROW_MAX_H,
    ROW_MIN_H,
    build_grid,
    detect_gray_h_lines,
    filter_vote_row_boundaries,
    merge_duplicate_h_lines,
    process_h_lines,
    render_page,
)

PDF_GOOD = Path("data/pdfs_e14c_segunda/01_001_01_01_E14_PRE_01_001_001_01_01_001_5002.pdf")
PDF_BAD = Path("data/pdfs_e14c_segunda/01_001_01_03_E14_PRE_01_001_001_01_03_001_5003.pdf")
JSON_GOOD = Path("debug_sv/grid_cells_v2_detected.json")
V_LINES_FALLBACK = [904, 1001, 1090, 1172]


def _h_lines_from_pdf(pdf: Path) -> list[int]:
    img = render_page(pdf)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return detect_gray_h_lines(gray)


# ---------------------------------------------------------------------------
# Unit tests (synthetic)
# ---------------------------------------------------------------------------
def test_merge_duplicate_h_lines_fuses_close_pairs():
    raw = [100, 108, 200, 210, 212, 350]
    merged = merge_duplicate_h_lines(raw, threshold=15)
    assert merged == [104, 207, 350]


def test_merge_duplicate_h_lines_preserves_order():
    raw = [500, 300, 400, 310]
    merged = merge_duplicate_h_lines(raw)
    assert merged == [305, 400, 500]


def test_filter_header_short_gaps():
    """En encabezado (Y < 900) los gaps 20–50 px se conservan."""
    lines = [800, 840, 930]
    accepted, discarded = filter_vote_row_boundaries(lines, header_y_max=900)
    assert 840 in accepted
    assert 930 in accepted
    assert discarded == []


def test_filter_discards_body_noise_without_row_ahead():
    """Ruido 20–50 px en cuerpo sin fila válida detrás se descarta."""
    lines = [2000, 2025, 2030]
    accepted, discarded = filter_vote_row_boundaries(lines, header_y_max=900)
    assert 2025 in discarded
    assert 2030 in discarded


# ---------------------------------------------------------------------------
# Integration: caso bueno
# ---------------------------------------------------------------------------
def test_good_case_json_h_lines_not_worse_than_baseline():
    data = json.loads(JSON_GOOD.read_text(encoding="utf-8"))
    h_raw = data["h_lines"]
    v_lines = data["v_lines"]

    baseline_rows = len(build_grid(v_lines, h_raw))
    processed = process_h_lines(h_raw)
    filtered_rows = len(build_grid(v_lines, processed))

    assert 11 <= baseline_rows <= 14
    assert 11 <= filtered_rows <= 14
    assert filtered_rows >= baseline_rows


def test_good_case_pdf_h_lines_not_worse_than_baseline():
    h_raw = _h_lines_from_pdf(PDF_GOOD)
    baseline_rows = len(build_grid(V_LINES_FALLBACK, h_raw))
    processed = process_h_lines(h_raw)
    filtered_rows = len(build_grid(V_LINES_FALLBACK, processed))

    assert 11 <= baseline_rows <= 14
    assert 11 <= filtered_rows <= 14
    assert filtered_rows >= baseline_rows


# ---------------------------------------------------------------------------
# Integration: caso malo (zona 01_03)
# ---------------------------------------------------------------------------
def test_bad_case_reduces_noise_and_recovers_rows():
    h_raw = _h_lines_from_pdf(PDF_BAD)

    assert len(h_raw) >= 45, f"se esperaban ~49 líneas crudas, got {len(h_raw)}"

    baseline_rows = len(build_grid(V_LINES_FALLBACK, h_raw))
    processed = process_h_lines(h_raw)
    accepted, discarded = filter_vote_row_boundaries(merge_duplicate_h_lines(h_raw))
    filtered_rows = len(build_grid(V_LINES_FALLBACK, processed))

    assert baseline_rows <= 5, "sin filtro el caso malo debe colapsar (pocas filas)"
    assert 7 <= filtered_rows <= 12
    assert len(accepted) > len(discarded)
    assert len(discarded) > 10


if __name__ == "__main__":
    pytest.main([__file__, "-v"])