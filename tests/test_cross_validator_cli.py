"""Tests for cross_validator_cli and the new cross_validator helpers — T4.3."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.modules.analyzer.cross_validator import (
    apply_confidence_filter,
    check_arithmetic,
    SourceResult,
    CONFIDENCE_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ok_result(
    c1: int = 125,
    c2: int = 45,
    confidences: dict | None = None,
) -> SourceResult:
    """Build a minimal SourceResult with status='ok'."""
    def _digits(v: int) -> list[str]:
        s = str(v).zfill(3)
        return [s[0], s[1], s[2]]

    fields = {
        "VOTANTES": 200, "URNA": 198, "INCINER": 2,
        "C1_CEPEDA": c1, "C2_ABELARDO": c2,
        "BLANCO": 10, "NULOS": 8, "NO_MARCADOS": 5, "SUMA_TOTAL": 198,
    }
    digits = {k: _digits(v) for k, v in fields.items()}
    if confidences is None:
        confidences = {k: [0.99, 0.99, 0.99] for k in fields}

    return SourceResult(
        status="ok",
        fields=fields,
        digits=digits,
        confidences=confidences,
        error=None,
    )


# ---------------------------------------------------------------------------
# check_arithmetic tests
# ---------------------------------------------------------------------------

def test_check_arithmetic_ok():
    """C1+C2+BLANCO+NULOS+NO_MARCADOS == URNA → ok=True, delta=0."""
    # 125 + 45 + 10 + 8 + 5 = 193, but we need it to equal URNA=193
    fields = {
        "URNA": 193,
        "C1_CEPEDA": 125,
        "C2_ABELARDO": 45,
        "BLANCO": 10,
        "NULOS": 8,
        "NO_MARCADOS": 5,
    }
    result = check_arithmetic(fields)
    assert result["ok"] is True
    assert result["sum_votes"] == 193
    assert result["urna"] == 193
    assert result["delta"] == 0


def test_check_arithmetic_fail():
    """Sum doesn't match URNA → ok=False, delta reflects the gap."""
    fields = {
        "URNA": 198,
        "C1_CEPEDA": 125,
        "C2_ABELARDO": 45,
        "BLANCO": 10,
        "NULOS": 8,
        "NO_MARCADOS": 5,
        # sum = 193, urna = 198, delta = -5
    }
    result = check_arithmetic(fields)
    assert result["ok"] is False
    assert result["sum_votes"] == 193
    assert result["urna"] == 198
    assert result["delta"] == -5


def test_check_arithmetic_missing_field_urna():
    """URNA is None → ok=None (cannot evaluate)."""
    fields = {
        "URNA": None,
        "C1_CEPEDA": 125, "C2_ABELARDO": 45,
        "BLANCO": 10, "NULOS": 8, "NO_MARCADOS": 5,
    }
    result = check_arithmetic(fields)
    assert result["ok"] is None
    assert result["sum_votes"] is None
    assert result["delta"] is None


def test_check_arithmetic_missing_addend():
    """One addend is None → ok=None."""
    fields = {
        "URNA": 193,
        "C1_CEPEDA": 125, "C2_ABELARDO": None,  # missing
        "BLANCO": 10, "NULOS": 8, "NO_MARCADOS": 5,
    }
    result = check_arithmetic(fields)
    assert result["ok"] is None


def test_check_arithmetic_empty_fields():
    """Empty fields → ok=None."""
    result = check_arithmetic({})
    assert result["ok"] is None


# ---------------------------------------------------------------------------
# apply_confidence_filter tests
# ---------------------------------------------------------------------------

def test_apply_confidence_filter_replaces_low_conf():
    """Digit with conf < 0.70 → replaced by None in filtered result."""
    # All high confidence except C1_CEPEDA subcell 1 (tens digit)
    confs = {
        "C1_CEPEDA": [0.99, 0.40, 0.99],  # tens-digit is low confidence
        "C2_ABELARDO": [0.99, 0.99, 0.99],
        "VOTANTES": [0.99, 0.99, 0.99],
        "URNA": [0.99, 0.99, 0.99],
        "INCINER": [0.99, 0.99, 0.99],
        "BLANCO": [0.99, 0.99, 0.99],
        "NULOS": [0.99, 0.99, 0.99],
        "NO_MARCADOS": [0.99, 0.99, 0.99],
        "SUMA_TOTAL": [0.99, 0.99, 0.99],
    }
    result = _make_ok_result(c1=125, confidences=confs)
    filtered = apply_confidence_filter(result)

    assert filtered["status"] == "ok"
    c1_digits = filtered["digits"]["C1_CEPEDA"]
    assert c1_digits[0] == "1"   # hundreds: high conf → kept
    assert c1_digits[1] is None  # tens: low conf → masked
    assert c1_digits[2] == "5"   # units: high conf → kept


def test_apply_confidence_filter_keeps_high_conf():
    """All digits with conf >= 0.70 remain unchanged."""
    confs = {k: [0.85, 0.90, 0.95] for k in [
        "VOTANTES", "URNA", "INCINER", "C1_CEPEDA", "C2_ABELARDO",
        "BLANCO", "NULOS", "NO_MARCADOS", "SUMA_TOTAL",
    ]}
    result = _make_ok_result(c1=125, c2=45, confidences=confs)
    filtered = apply_confidence_filter(result)

    assert filtered["digits"]["C1_CEPEDA"] == ["1", "2", "5"]
    assert filtered["digits"]["C2_ABELARDO"] == ["0", "4", "5"]


def test_apply_confidence_filter_boundary_at_threshold():
    """Digit at exactly CONFIDENCE_THRESHOLD (0.70) is kept."""
    threshold = CONFIDENCE_THRESHOLD  # 0.70
    confs = {"C1_CEPEDA": [threshold, threshold, threshold],
             **{k: [0.99, 0.99, 0.99] for k in [
                 "VOTANTES", "URNA", "INCINER", "C2_ABELARDO",
                 "BLANCO", "NULOS", "NO_MARCADOS", "SUMA_TOTAL",
             ]}}
    result = _make_ok_result(c1=125, confidences=confs)
    filtered = apply_confidence_filter(result)

    # Exactly at threshold → kept (>=)
    assert filtered["digits"]["C1_CEPEDA"] == ["1", "2", "5"]


def test_apply_confidence_filter_just_below_threshold():
    """Digit just below threshold → masked."""
    confs = {"C1_CEPEDA": [0.699, 0.699, 0.699],
             **{k: [0.99, 0.99, 0.99] for k in [
                 "VOTANTES", "URNA", "INCINER", "C2_ABELARDO",
                 "BLANCO", "NULOS", "NO_MARCADOS", "SUMA_TOTAL",
             ]}}
    result = _make_ok_result(c1=125, confidences=confs)
    filtered = apply_confidence_filter(result)

    assert filtered["digits"]["C1_CEPEDA"] == [None, None, None]


def test_apply_confidence_filter_not_available_passthrough():
    """not_available source is returned unchanged (no error raised)."""
    not_avail = SourceResult(status="not_available", fields={}, digits={}, confidences={}, error=None)
    result = apply_confidence_filter(not_avail)
    assert result["status"] == "not_available"
    assert result["digits"] == {}


def test_apply_confidence_filter_does_not_mutate_original():
    """The original SourceResult is not mutated by the filter."""
    confs = {"C1_CEPEDA": [0.99, 0.30, 0.99],  # subcell 1 low
             **{k: [0.99, 0.99, 0.99] for k in [
                 "VOTANTES", "URNA", "INCINER", "C2_ABELARDO",
                 "BLANCO", "NULOS", "NO_MARCADOS", "SUMA_TOTAL",
             ]}}
    original = _make_ok_result(c1=125, confidences=confs)
    original_digits_before = list(original["digits"]["C1_CEPEDA"])

    apply_confidence_filter(original)

    # Original must be unchanged
    assert original["digits"]["C1_CEPEDA"] == original_digits_before


# ---------------------------------------------------------------------------
# validate_one_mesa integration test
# ---------------------------------------------------------------------------

def test_validate_one_mesa_integration(tmp_path):
    """Mock _extract_source for all 3 sources and verify output record structure."""
    from src.modules.analyzer.cross_validator_cli import _worker_extract

    # Create fake PDF files so _extract_source doesn't hit filesystem guard early
    e14c_pdf = tmp_path / "e14c.pdf"
    e14t_pdf = tmp_path / "e14t.pdf"
    e14d_pdf = tmp_path / "e14d.pdf"
    e14c_pdf.write_bytes(b"%PDF")
    e14t_pdf.write_bytes(b"%PDF")
    e14d_pdf.write_bytes(b"%PDF")

    entry = {
        "dept": "05",
        "mpio": "001",
        "zona": "001",
        "puesto": "01",
        "mesa": "001",
        "e14c_path": str(e14c_pdf),
        "e14t_path": str(e14t_pdf),
        "e14d_path": str(e14d_pdf),
        "all_available": True,
    }

    def _digits(v: int) -> list[str]:
        s = str(v).zfill(3)
        return [s[0], s[1], s[2]]

    fields_ok = {
        "VOTANTES": 200, "URNA": 198, "INCINER": 2,
        "C1_CEPEDA": 125, "C2_ABELARDO": 45,
        "BLANCO": 10, "NULOS": 8, "NO_MARCADOS": 5, "SUMA_TOTAL": 198,
    }
    mock_source = SourceResult(
        status="ok",
        fields=fields_ok,
        digits={k: _digits(v) for k, v in fields_ok.items()},
        confidences={k: [0.99, 0.99, 0.99] for k in fields_ok},
        error=None,
    )

    with patch(
        "src.modules.analyzer.cross_validator_cli._worker_extract",
        wraps=lambda e: _worker_extract_mocked(e, mock_source),
    ):
        # Call the actual _worker_extract but with mocked _extract_source inside it
        with patch("src.modules.analyzer.cross_validator._extract_source", return_value=mock_source):
            result = _worker_extract(entry)

    # Verify top-level structure
    assert result["dept"] == "05"
    assert result["mpio"] == "001"
    assert result["zona"] == "001"
    assert result["puesto"] == "01"
    assert result["mesa"] == "001"

    # sources
    assert "e14c" in result["sources"]
    assert "e14t" in result["sources"]
    assert "e14d" in result["sources"]
    assert result["sources"]["e14c"]["status"] == "ok"

    # congruencia
    assert "fields" in result["congruencia"]
    assert "summary" in result["congruencia"]

    # aritmetica
    assert "e14c" in result["aritmetica"]
    assert "ok" in result["aritmetica"]["e14c"]

    # tachones reserved field
    assert "tachones" in result


def _worker_extract_mocked(entry: dict, mock_source: SourceResult) -> dict:
    """Helper that re-runs _worker_extract logic with a mocked _extract_source."""
    # This is just used as a type hint; the actual mock is in the with patch block
    pass
