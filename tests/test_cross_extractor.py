"""Tests for cross_validator._extract_source() — T3.3 (TDD gate for T3.1/T3.2)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.modules.analyzer.cross_validator import _extract_source, VOTE_FIELDS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok_e14t_raw(c1=125, c2=45):
    """Minimal e14t_extractor.extract_fields() return value."""
    def _digits(v):
        s = str(v).zfill(3)
        return [s[0], s[1], s[2]]

    fields = {
        "VOTANTES": 200, "URNA": 198, "INCINER": 2,
        "C1_CEPEDA": c1, "C2_ABELARDO": c2,
        "BLANCO": 10, "NULOS": 8, "NO_MARCADOS": 5, "SUMA_TOTAL": 198,
    }
    return {
        "fields": fields,
        "digits": {k: _digits(v) for k, v in fields.items()},
        "method": "corner_calibrator",
        "corners_found": 4,
        "rows_extracted": 9,
        "error": None,
    }


def _ok_e14c_process_result(c1=123, c2=44):
    """Minimal e14_worker.process_pdf_task() return value."""
    def _digits(v):
        s = str(v).zfill(3)
        return [s[0], s[1], s[2]]

    fields = {
        "VOTANTES": 200, "URNA": 198, "INCINER": 2,
        "C1_CEPEDA": c1, "C2_ABELARDO": c2,
        "BLANCO": 10, "NULOS": 8, "NO_MARCADOS": 5, "SUMA_TOTAL": 198,
    }
    rows = [
        {"label": k, "value": v, "digits": _digits(v), "confidences": [0.99, 0.99, 0.99]}
        for k, v in fields.items()
    ]
    return {
        "pdf": "some/path.pdf",
        "dept": "ANTIOQUIA",
        "fields": fields,
        "rows": rows,
        "is_suspicious": False,
        "suspicious_reasons": [],
    }


# ---------------------------------------------------------------------------
# T3.3-A: None path → not_available (no exception)
# ---------------------------------------------------------------------------

def test_none_path_returns_not_available_e14t():
    result = _extract_source(None, "e14t")
    assert result["status"] == "not_available"
    assert result["digits"] == {}


def test_none_path_returns_not_available_e14c():
    result = _extract_source(None, "e14c")
    assert result["status"] == "not_available"
    assert result["digits"] == {}


# ---------------------------------------------------------------------------
# T3.3-B: Non-existent path → not_available
# ---------------------------------------------------------------------------

def test_nonexistent_path_returns_not_available(tmp_path):
    missing = tmp_path / "missing.pdf"
    result = _extract_source(missing, "e14t")
    assert result["status"] == "not_available"


# ---------------------------------------------------------------------------
# T3.3-C: Exception raised by extractor → extraction_error
# ---------------------------------------------------------------------------

def test_extractor_exception_returns_extraction_error(tmp_path):
    pdf = tmp_path / "ok.pdf"
    pdf.write_bytes(b"%PDF")

    with patch(
        "src.modules.analyzer.cross_validator._run_e14t",
        side_effect=RuntimeError("GPU OOM"),
    ):
        result = _extract_source(pdf, "e14t")

    assert result["status"] == "extraction_error"
    assert "GPU OOM" in result.get("error", "")


def test_e14c_exception_returns_extraction_error(tmp_path):
    pdf = tmp_path / "ok.pdf"
    pdf.write_bytes(b"%PDF")

    with patch(
        "src.modules.analyzer.cross_validator._run_e14c",
        side_effect=ValueError("bad shape"),
    ):
        result = _extract_source(pdf, "e14c")

    assert result["status"] == "extraction_error"
    assert "bad shape" in result.get("error", "")


# ---------------------------------------------------------------------------
# T3.3-D: Error key in result → extraction_error (even without exception)
# ---------------------------------------------------------------------------

def test_e14t_error_key_returns_extraction_error(tmp_path):
    pdf = tmp_path / "ok.pdf"
    pdf.write_bytes(b"%PDF")

    bad_raw = {"fields": {}, "digits": {}, "error": "too_few_corners:1",
               "corners_found": 1, "rows_extracted": 0, "method": "corner_calibrator"}

    with patch("src.modules.analyzer.cross_validator._run_e14t", return_value=bad_raw):
        result = _extract_source(pdf, "e14t")

    assert result["status"] == "extraction_error"
    assert "too_few_corners" in result.get("error", "")


# ---------------------------------------------------------------------------
# T3.3-E: Valid E14T result → ok with fields + digits
# ---------------------------------------------------------------------------

def test_valid_e14t_result_ok(tmp_path):
    pdf = tmp_path / "ok.pdf"
    pdf.write_bytes(b"%PDF")

    raw = _ok_e14t_raw(c1=125, c2=45)

    with patch("src.modules.analyzer.cross_validator._run_e14t", return_value=raw):
        result = _extract_source(pdf, "e14t")

    assert result["status"] == "ok"
    assert result["fields"]["C1_CEPEDA"] == 125
    assert result["digits"]["C1_CEPEDA"] == ["1", "2", "5"]
    assert result["digits"]["C2_ABELARDO"] == ["0", "4", "5"]
    # All 9 VOTE_FIELDS present in digits
    for field in VOTE_FIELDS:
        assert field in result["digits"], f"Missing digit for {field}"


# ---------------------------------------------------------------------------
# T3.3-F: Valid E14C result → ok with fields + digits
# ---------------------------------------------------------------------------

def test_valid_e14c_result_ok(tmp_path):
    pdf = tmp_path / "ok.pdf"
    pdf.write_bytes(b"%PDF")

    raw = _ok_e14c_process_result(c1=123, c2=44)

    with patch("src.modules.analyzer.cross_validator._run_e14c", return_value=raw):
        result = _extract_source(pdf, "e14c")

    assert result["status"] == "ok"
    assert result["fields"]["C1_CEPEDA"] == 123
    assert result["digits"]["C1_CEPEDA"] == ["1", "2", "3"]
    assert result["digits"]["C2_ABELARDO"] == ["0", "4", "4"]


# ---------------------------------------------------------------------------
# T3.3-G: E14D uses e14t_extractor (same physical form)
# ---------------------------------------------------------------------------

def test_e14d_routes_to_e14t_extractor(tmp_path):
    """E14D must route through _run_e14t (same form — not the old stub)."""
    pdf = tmp_path / "ok.pdf"
    pdf.write_bytes(b"%PDF")

    raw = _ok_e14t_raw(c1=99, c2=55)

    with patch("src.modules.analyzer.cross_validator._run_e14t", return_value=raw):
        result = _extract_source(pdf, "e14d")

    assert result["status"] == "ok"
    assert result["fields"]["C1_CEPEDA"] == 99


# ---------------------------------------------------------------------------
# T3.3-H: Unknown acta_type → extraction_error (defensive)
# ---------------------------------------------------------------------------

def test_unknown_type_returns_extraction_error(tmp_path):
    pdf = tmp_path / "ok.pdf"
    pdf.write_bytes(b"%PDF")
    result = _extract_source(pdf, "e14x")
    assert result["status"] == "extraction_error"
