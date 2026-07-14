"""tests/test_cross_report.py — Unit tests for generate_summary_report() (Phase 5).

Run:
    pytest tests/test_cross_report.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.modules.analyzer.cross_validator_cli import (
    _render_markdown,
    generate_summary_report,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_record(
    *,
    dept: str = "01",
    mpio: str = "001",
    zona: str = "001",
    puesto: str = "01",
    mesa: str = "001",
    e14c_status: str = "not_available",
    e14t_status: str = "not_available",
    e14d_status: str = "not_available",
    e14c_arith: dict | None = None,
    e14t_arith: dict | None = None,
    e14d_arith: dict | None = None,
    cross_discrepancy: bool = False,
    cross_discrepant_fields: list[str] | None = None,
) -> dict:
    def _arith(a: dict | None) -> dict:
        return a if a is not None else {"ok": None, "sum_votes": None, "urna": None, "delta": None}

    return {
        "dept": dept,
        "mpio": mpio,
        "zona": zona,
        "puesto": puesto,
        "mesa": mesa,
        "sources": {
            "e14c": {"status": e14c_status, "fields": {}},
            "e14t": {"status": e14t_status, "fields": {}},
            "e14d": {"status": e14d_status, "fields": {}},
        },
        "congruencia": {
            "summary": {
                "cross_discrepancy": cross_discrepancy,
                "cross_discrepant_fields": cross_discrepant_fields or [],
            }
        },
        "aritmetica": {
            "e14c": _arith(e14c_arith),
            "e14t": _arith(e14t_arith),
            "e14d": _arith(e14d_arith),
        },
        "tachones": None,
    }


# ---------------------------------------------------------------------------
# Test 1: Empty JSONL — all zeros, no crash
# ---------------------------------------------------------------------------

def test_empty_records_no_crash():
    report = generate_summary_report([])

    assert report["total_mesas"] == 0
    assert report["sources"]["distribution"] == {"0": 0, "1": 0, "2": 0, "3": 0}
    assert report["sources"]["e14c_ok"] == 0
    assert report["sources"]["e14t_ok"] == 0
    assert report["sources"]["e14d_ok"] == 0
    for src in ("e14c", "e14t", "e14d"):
        assert report["arithmetic"][src] == {"ok": 0, "fail": 0, "skip": 0}
    assert report["congruencia"]["cross_discrepancy_count"] == 0
    assert report["congruencia"]["cross_discrepancy_rate"] == 0.0
    assert report["congruencia"]["both_arithmetic_ok_count"] == 0
    assert report["congruencia"]["both_arithmetic_ok_cross_discrepancy"] == 0
    assert report["congruencia"]["discrepant_fields_frequency"] == {}
    assert report["top_arithmetic_deltas"] == []


# ---------------------------------------------------------------------------
# Test 2: Single mesa, E14T ok arithmetic — correct counts
# ---------------------------------------------------------------------------

def test_single_mesa_e14t_ok():
    rec = _make_record(
        e14t_status="ok",
        e14t_arith={"ok": True, "sum_votes": 122, "urna": 122, "delta": 0},
    )
    report = generate_summary_report([rec])

    assert report["total_mesas"] == 1
    # One source available (e14t)
    assert report["sources"]["distribution"]["1"] == 1
    assert report["sources"]["e14t_ok"] == 1
    assert report["sources"]["e14c_ok"] == 0
    assert report["sources"]["e14d_ok"] == 0

    assert report["arithmetic"]["e14t"]["ok"] == 1
    assert report["arithmetic"]["e14t"]["fail"] == 0
    assert report["arithmetic"]["e14t"]["skip"] == 0
    # e14c and e14d are not_available → skip
    assert report["arithmetic"]["e14c"]["skip"] == 1
    assert report["arithmetic"]["e14d"]["skip"] == 1

    # Only 1 source with ok=True → not ≥2
    assert report["congruencia"]["both_arithmetic_ok_count"] == 0
    assert report["congruencia"]["cross_discrepancy_count"] == 0
    # Arithmetic ok → no delta candidate
    assert report["top_arithmetic_deltas"] == []


# ---------------------------------------------------------------------------
# Test 3: Two mesas, cross_discrepancy — correct rate
# ---------------------------------------------------------------------------

def test_two_mesas_cross_discrepancy_rate():
    # rec_disc: e14t ok, e14d arithmetic FAIL (sum != urna) → cross discrepancy
    rec_disc = _make_record(
        e14t_status="ok",
        e14d_status="ok",
        e14t_arith={"ok": True, "sum_votes": 100, "urna": 100, "delta": 0},
        e14d_arith={"ok": False, "sum_votes": 200, "urna": 100, "delta": 100},
        cross_discrepancy=True,
        cross_discrepant_fields=["URNA", "C1_CEPEDA"],
    )
    rec_ok = _make_record(
        e14t_status="ok",
        e14t_arith={"ok": True, "sum_votes": 50, "urna": 50, "delta": 0},
        cross_discrepancy=False,
    )
    report = generate_summary_report([rec_disc, rec_ok])

    assert report["total_mesas"] == 2
    assert report["congruencia"]["cross_discrepancy_count"] == 1
    assert abs(report["congruencia"]["cross_discrepancy_rate"] - 0.5) < 1e-6

    # rec_disc: e14t ok=True (1 source), e14d ok=False — only 1 arith-ok source → not ≥2
    assert report["congruencia"]["both_arithmetic_ok_count"] == 0
    assert report["congruencia"]["both_arithmetic_ok_cross_discrepancy"] == 0

    # discrepant_fields_frequency
    freq = report["congruencia"]["discrepant_fields_frequency"]
    assert freq["URNA"] == 1
    assert freq["C1_CEPEDA"] == 1

    # rec_disc e14d has delta=100 and ok=False → 1 failure candidate
    assert len(report["top_arithmetic_deltas"]) == 1
    assert report["top_arithmetic_deltas"][0]["delta"] == 100


# ---------------------------------------------------------------------------
# Test 4: top_arithmetic_deltas sorted by abs(delta) descending
# ---------------------------------------------------------------------------

def test_top_arithmetic_deltas_sorted():
    deltas = [50, 200, 10, 661, 300, 5, 120, 400, 80, 250, 350, 15]
    records = [
        _make_record(
            mesa=str(i).zfill(3),
            e14t_status="ok",
            e14t_arith={"ok": False, "sum_votes": 999, "urna": 0, "delta": d},
        )
        for i, d in enumerate(deltas)
    ]
    report = generate_summary_report(records)

    top = report["top_arithmetic_deltas"]
    assert len(top) == 10  # capped at 10
    # Must be sorted descending by delta magnitude
    abs_vals = [abs(e["delta"]) for e in top]
    assert abs_vals == sorted(abs_vals, reverse=True)
    # The largest delta (661) must be first
    assert top[0]["delta"] == 661


# ---------------------------------------------------------------------------
# Test 5: Markdown output contains expected headers and data
# ---------------------------------------------------------------------------

def test_markdown_contains_expected_headers():
    rec = _make_record(
        e14t_status="ok",
        e14d_status="ok",
        e14t_arith={"ok": True, "sum_votes": 100, "urna": 100, "delta": 0},
        e14d_arith={"ok": False, "sum_votes": 500, "urna": 100, "delta": 400},
        cross_discrepancy=True,
        cross_discrepant_fields=["VOTANTES"],
    )
    report = generate_summary_report([rec])
    report["input_file"] = "data/test.jsonl"

    md = _render_markdown(report, "data/test.jsonl")

    assert "# Cross-Mesa Validation Report" in md
    assert "## Sources Available" in md
    assert "## Arithmetic Check" in md
    assert "## Cross-Source Congruence" in md
    assert "## Top Arithmetic Failures (by |delta|)" in md
    assert "### Most Discrepant Fields" in md
    assert "VOTANTES" in md
    # The failure delta of 400 must appear in the top failures table
    assert "400" in md
    # Cross-discrepancy rate 1/1 (100.0%)
    assert "1/1" in md
