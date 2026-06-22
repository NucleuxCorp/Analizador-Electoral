"""
Tests para label_rows_by_structure() — etiquetado estructural de filas E-14C.

Caso bueno: mesa 001 zona 01_01 — 9 etiquetas conocidas + UNK en espaciadores.
Caso malo: filas sintéticas / PDF 01_03 — espaciadores UNK@{y_mid}.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from debug_sv.grid_detector_v2 import (
    BLOCK_A_REF_PAGE_H,
    BLOCK_A_Y_FLOOR,
    KNOWN_ROW_LABELS,
    SUBCELL_X_FALLBACK,
    anchor_block_a,
    build_grid,
    detect_gray_h_lines,
    detect_gray_v_lines,
    is_known_label,
    label_rows_by_structure,
    process_h_lines,
    render_page,
    validate_labeled_rows,
)

PDF_GOOD = Path("data/pdfs_e14c_segunda/01_001_01_01_E14_PRE_01_001_001_01_01_001_5002.pdf")
PDF_BAD = Path("data/pdfs_e14c_segunda/01_001_01_03_E14_PRE_01_001_001_01_03_001_5003.pdf")
PDF_OFFSET = Path("data/pdfs_e14c_segunda/01_001_01_01_E14_PRE_01_001_001_01_01_003_5002.pdf")
PDF_HEALTHY_010 = Path("data/pdfs_e14c_segunda/01_001_01_01_E14_PRE_01_001_001_01_01_010_5002.pdf")
PDF_LOW_URNA_029 = Path("data/pdfs_e14c_segunda/01_001_01_01_E14_PRE_01_001_001_01_01_029_5002.pdf")
JSON_GOOD = Path("debug_sv/grid_cells_v2_detected.json")

EXPECTED_GOOD = {
    "VOTANTES": 1023,
    "URNA": 1149,
    "INCINER": 1268,
    "C1_CEPEDA": 1796,
    "C2_ABELARDO": 2397,
    "BLANCO": 2834,
    "NULOS": 2953,
    "NO_MARCADOS": 3071,
    "SUMA_TOTAL": 3188,
}


def _grid_from_pdf(pdf: Path) -> list[dict]:
    img = render_page(pdf)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    v = detect_gray_v_lines(gray)
    if len(v) < 4:
        v = SUBCELL_X_FALLBACK
    h_raw = detect_gray_h_lines(gray)
    h = process_h_lines(h_raw, page_height=gray.shape[0])
    return build_grid(v, h), gray.shape[0]


def _labeled_from_pdf(pdf: Path) -> list[dict]:
    grid_rows, page_h = _grid_from_pdf(pdf)
    return label_rows_by_structure(grid_rows, page_h=page_h)


def _labels_by_name(rows: list[dict]) -> dict[str, dict]:
    return {r["label"]: r for r in rows if is_known_label(r["label"])}


def _y_mid(row: dict) -> int:
    return (row["top"] + row["bot"]) // 2


def _synthetic_compact_row(idx: int, y_mid: int, height: int = 90) -> dict:
    half = height // 2
    return {
        "row_idx": idx,
        "top": y_mid - half,
        "bot": y_mid + half,
        "height": height,
        "cells": [],
    }


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------
def test_is_known_label_vocabulary():
    for label in EXPECTED_GOOD:
        assert is_known_label(label)
    assert not is_known_label("UNK@1470")
    assert not is_known_label("VOTACION")


def test_empty_rows_returns_empty():
    assert label_rows_by_structure([]) == []


def test_synthetic_spacer_gets_unk_at_1470():
    """Spacer row between INCINER and C1 must stay UNK (y ~1459–1470)."""
    rows = [
        {"row_idx": 0, "top": 980, "bot": 1070, "height": 90, "cells": []},
        {"row_idx": 1, "top": 1095, "bot": 1185, "height": 90, "cells": []},
        {"row_idx": 2, "top": 1210, "bot": 1300, "height": 90, "cells": []},
        {"row_idx": 3, "top": 1420, "bot": 1510, "height": 90, "cells": []},
        {"row_idx": 4, "top": 1740, "bot": 1830, "height": 90, "cells": []},
        {"row_idx": 5, "top": 2340, "bot": 2430, "height": 90, "cells": []},
        {"row_idx": 6, "top": 2780, "bot": 2870, "height": 90, "cells": []},
        {"row_idx": 7, "top": 2900, "bot": 2990, "height": 90, "cells": []},
        {"row_idx": 8, "top": 3020, "bot": 3110, "height": 90, "cells": []},
        {"row_idx": 9, "top": 3140, "bot": 3230, "height": 90, "cells": []},
    ]
    labeled = label_rows_by_structure(rows)
    by_label = {r["label"]: r for r in labeled}

    assert by_label["VOTANTES"]["y_mid"] == rows[0]["top"] + 45
    assert by_label["URNA"]["y_mid"] == rows[1]["top"] + 45
    assert by_label["INCINER"]["y_mid"] == rows[2]["top"] + 45
    assert labeled[3]["label"] == "UNK@1465"
    assert by_label["C1_CEPEDA"]["y_mid"] < by_label["C2_ABELARDO"]["y_mid"]


def test_anchor_block_a_empty_when_c1_zero():
    rows = [_synthetic_compact_row(0, 1035), _synthetic_compact_row(1, 1152)]
    assert anchor_block_a(rows, c1_idx=0, y_floor=980) == []


def test_anchor_block_a_y_floor_excludes_spurious_rows():
    rows = [
        _synthetic_compact_row(0, 846),
        _synthetic_compact_row(1, 946),
        _synthetic_compact_row(2, 1035),
        _synthetic_compact_row(3, 1152),
        _synthetic_compact_row(4, 1796),
    ]
    assert anchor_block_a(rows, c1_idx=4, y_floor=980) == [2, 3]


def test_anchor_block_a_ratio_scaling():
    assert int(3500 * BLOCK_A_Y_FLOOR / BLOCK_A_REF_PAGE_H) == 980
    assert min(int(3890 * BLOCK_A_Y_FLOOR / BLOCK_A_REF_PAGE_H), 1020) == 1020

    rows = [
        _synthetic_compact_row(0, 946),
        _synthetic_compact_row(1, 1035),
        _synthetic_compact_row(2, 1152),
        _synthetic_compact_row(3, 1796),
    ]
    assert anchor_block_a(rows, c1_idx=3, page_h=3890) == [1, 2]
    assert 946 not in [
        (rows[i]["top"] + rows[i]["bot"]) // 2
        for i in anchor_block_a(rows, c1_idx=3, page_h=3890)
    ]


def test_anchor_block_a_skips_non_compact_rows():
    rows = [
        _synthetic_compact_row(0, 1035),
        {"row_idx": 1, "top": 1050, "bot": 1250, "height": 200, "cells": []},
        _synthetic_compact_row(2, 1152),
        _synthetic_compact_row(3, 1796),
    ]
    assert anchor_block_a(rows, c1_idx=3, y_floor=980) == [0, 2]


def test_validate_labeled_rows_order_and_arithmetic():
    rows = [
        {"label": "URNA", "y_mid": 1100},
        {"label": "C1_CEPEDA", "y_mid": 1800},
        {"label": "C2_ABELARDO", "y_mid": 2400},
    ]
    assert validate_labeled_rows(rows) == []

    bad_order = [
        {"label": "C1_CEPEDA", "y_mid": 2400},
        {"label": "C2_ABELARDO", "y_mid": 1800},
    ]
    assert "order_c1_c2" in validate_labeled_rows(bad_order)

    fields = {"URNA": 100, "C1_CEPEDA": 60, "C2_ABELARDO": 50}
    assert "c1_c2_exceed_urna" in validate_labeled_rows(rows, fields)


# ---------------------------------------------------------------------------
# Integration: JSON validation grid (01_01)
# ---------------------------------------------------------------------------
def test_json_grid_structural_labels():
    data = json.loads(JSON_GOOD.read_text(encoding="utf-8"))
    grid_rows = build_grid(data["v_lines"], data["h_lines"])
    labeled = label_rows_by_structure(grid_rows, page_h=BLOCK_A_REF_PAGE_H)
    by_name = _labels_by_name(labeled)

    for label, y_mid in EXPECTED_GOOD.items():
        assert label in by_name, f"missing {label}"
        assert abs(by_name[label]["y_mid"] - y_mid) <= 80, (
            f"{label}: expected y_mid~{y_mid}, got {by_name[label]['y_mid']}"
        )

    unk_spacers = [r for r in labeled if r["label"].startswith("UNK@")]
    assert len(unk_spacers) >= 1
    assert any(1380 <= int(r["label"].split("@")[1]) <= 1480 for r in unk_spacers)


# ---------------------------------------------------------------------------
# Integration: D1 offset / healthy / low-urna PDFs
# ---------------------------------------------------------------------------
def test_offset_pdf_003_urna_at_nivelacion_band():
    if not PDF_OFFSET.exists():
        pytest.skip("offset PDF not available")

    labeled = _labeled_from_pdf(PDF_OFFSET)
    by_name = _labels_by_name(labeled)

    assert "URNA" in by_name
    assert 1066 <= by_name["URNA"]["y_mid"] <= 1238

    for label, row in by_name.items():
        if label in ("VOTANTES", "URNA", "INCINER"):
            assert not (866 <= row["y_mid"] <= 1026), (
                f"{label} anchored too high at y={row['y_mid']}"
            )


def test_healthy_pdf_010_votantes_840_urna_1147():
    if not PDF_HEALTHY_010.exists():
        pytest.skip("healthy 010 PDF not available")

    labeled = _labeled_from_pdf(PDF_HEALTHY_010)
    by_name = _labels_by_name(labeled)

    assert "URNA" in by_name
    assert abs(by_name["URNA"]["y_mid"] - 1147) <= 80


def test_low_urna_pdf_029_urna_at_1025():
    if not PDF_LOW_URNA_029.exists():
        pytest.skip("low urna 029 PDF not available")

    labeled = _labeled_from_pdf(PDF_LOW_URNA_029)
    by_name = _labels_by_name(labeled)

    assert "URNA" in by_name
    assert abs(by_name["URNA"]["y_mid"] - 1025) <= 80


# ---------------------------------------------------------------------------
# Integration: good PDF 001 (01_01)
# ---------------------------------------------------------------------------
def test_good_pdf_nine_known_labels():
    grid_rows, page_h = _grid_from_pdf(PDF_GOOD)
    assert len(grid_rows) >= 11

    labeled = label_rows_by_structure(grid_rows, page_h=page_h)
    by_name = _labels_by_name(labeled)

    assert len(by_name) == 9
    for label in EXPECTED_GOOD:
        assert label in by_name

    assert by_name["C1_CEPEDA"]["y_mid"] < by_name["C2_ABELARDO"]["y_mid"]
    assert by_name["URNA"]["y_mid"] < by_name["C1_CEPEDA"]["y_mid"]
    assert by_name["C2_ABELARDO"]["y_mid"] < by_name["BLANCO"]["y_mid"]

    block_a = {"VOTANTES", "URNA", "INCINER"}
    for row in labeled:
        if row["label"] in block_a:
            assert row["label_source"] == "anchor_block_a"
        elif is_known_label(row["label"]):
            assert row["label_source"] == "structure"
        assert row["y_mid"] == (row["top"] + row["bot"]) // 2


def test_good_pdf_page_height_drift_tolerant():
    """Relative gaps must work across page heights 3869–3897."""
    img = render_page(PDF_GOOD)
    assert 3860 <= img.shape[0] <= 3900

    labeled = _labeled_from_pdf(PDF_GOOD)
    by_name = _labels_by_name(labeled)
    assert "VOTANTES" in by_name
    assert "SUMA_TOTAL" in by_name


# ---------------------------------------------------------------------------
# Integration: bad PDF / noisy grid (01_03)
# ---------------------------------------------------------------------------
def test_bad_pdf_finds_candidates_and_spacers():
    if not PDF_BAD.exists():
        pytest.skip("bad PDF not available")

    grid_rows, page_h = _grid_from_pdf(PDF_BAD)
    labeled = label_rows_by_structure(grid_rows, page_h=page_h)
    by_name = _labels_by_name(labeled)

    assert "C1_CEPEDA" in by_name
    assert "C2_ABELARDO" in by_name
    assert by_name["C1_CEPEDA"]["y_mid"] < by_name["C2_ABELARDO"]["y_mid"]

    unk = [r for r in labeled if r["label"].startswith("UNK@")]
    assert unk, "expected at least one spacer UNK row"
    assert any(int(r["label"].split("@")[1]) >= 1200 for r in unk)


def test_bad_case_preserves_row_order():
    """Spacer at y~1470 stays UNK; output order matches input."""
    rows = [
        {"row_idx": 0, "top": 980, "bot": 1070, "height": 90, "cells": []},
        {"row_idx": 1, "top": 1095, "bot": 1185, "height": 90, "cells": []},
        {"row_idx": 2, "top": 1210, "bot": 1300, "height": 90, "cells": []},
        {"row_idx": 3, "top": 1459, "bot": 1540, "height": 81, "cells": []},
        {"row_idx": 4, "top": 1750, "bot": 1840, "height": 90, "cells": []},
        {"row_idx": 5, "top": 2350, "bot": 2440, "height": 90, "cells": []},
        {"row_idx": 6, "top": 2680, "bot": 2770, "height": 90, "cells": []},
        {"row_idx": 7, "top": 2785, "bot": 2875, "height": 90, "cells": []},
        {"row_idx": 8, "top": 2900, "bot": 2990, "height": 90, "cells": []},
        {"row_idx": 9, "top": 3020, "bot": 3110, "height": 90, "cells": []},
        {"row_idx": 10, "top": 3140, "bot": 3230, "height": 90, "cells": []},
    ]

    labeled = label_rows_by_structure(rows)
    assert [r["row_idx"] for r in labeled] == [r["row_idx"] for r in rows]
    assert labeled[3]["label"] == "UNK@1499"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])