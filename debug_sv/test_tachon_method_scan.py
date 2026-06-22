"""Tests for per-method isolated tachon scan runner (T-S01–T-D01)."""
from __future__ import annotations

import importlib
import inspect
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "debug_sv"))

import tachon_method_scan as tms  # noqa: E402
from build_tachon_scan_manifest import compute_manifest_hash  # noqa: E402


def _sample_record(**overrides) -> dict:
    base = {
        "run_id": "tachon-pattern-scan-500pdf",
        "pdf": "data/pdfs_e14c_segunda/01_test.pdf",
        "dept": "01",
        "block": "block_b",
        "label": "C1_CEPEDA",
        "subcell_idx": 0,
        "digit": "2",
        "confidence": 1.0,
        "has_ink": True,
        "scores": {
            "tachon_score": 1.0,
            "double_score": 0.0,
            "density_score": 0.0,
            "noise_score": 0.308,
            "combined_score": 0.431,
        },
        "flags_by_method": {
            "TACHON": True,
            "DOBLE_ESCRITURA": False,
            "DENSIDAD_ALTA": False,
            "ZONA_SUCIA": False,
        },
        "is_suspicious_combined": False,
        "scan_mode": "all_methods",
    }
    base.update(overrides)
    return base


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


@pytest.fixture
def tiny_manifest(tmp_path: Path) -> Path:
    entries = [
        {"pdf": "data/pdfs_e14c_segunda/01_a.pdf", "dept": "01", "stratum": "dept_01", "validate_holdout": False},
        {"pdf": "data/pdfs_e14c_segunda/16_b.pdf", "dept": "16", "stratum": "dept_16", "validate_holdout": True},
    ]
    manifest = {
        "seed": 42,
        "total_pdfs": 2,
        "total_corpus": 100,
        "departments": 2,
        "allocation_formula": "proportional_with_min_1",
        "created_at": "2026-06-22T00:00:00+00:00",
        "manifest_hash": compute_manifest_hash(entries),
        "entries": entries,
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_scan_emits_all_methods(tiny_manifest: Path, tmp_path: Path):
    """T-S01: scan pipeline emits all four method scores per subcell."""
    fake_records = [
        _sample_record(pdf="data/pdfs_e14c_segunda/01_a.pdf", subcell_idx=0),
        _sample_record(pdf="data/pdfs_e14c_segunda/01_a.pdf", subcell_idx=1, label="C2_ABELARDO"),
        _sample_record(pdf="data/pdfs_e14c_segunda/16_b.pdf", dept="16", subcell_idx=0),
    ]

    def _fake_process(pdf_path: str, **kwargs):
        return [r for r in fake_records if r["pdf"] == pdf_path.replace("\\", "/")]

    out_jsonl = tmp_path / "scan.jsonl"
    with patch.object(tms, "_worker_task", side_effect=lambda args: (args[0], _fake_process(args[0]), None)):
        completion = tms.run_scan(
            tiny_manifest,
            scan_mode="all",
            workers=1,
            skip_ocr=True,
            out_jsonl=out_jsonl,
            run_id="tachon-pattern-scan-500pdf",
        )

    lines = out_jsonl.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    for line in lines:
        row = json.loads(line)
        assert set(row["scores"].keys()) == {
            "tachon_score",
            "double_score",
            "density_score",
            "noise_score",
            "combined_score",
        }
        assert set(row["flags_by_method"].keys()) == set(tms.METHOD_NAMES)
    assert completion["pdfs_ok"] == 2


def test_per_method_flags_isolated():
    """T-S02: per-method flags use isolated thresholds, independent of combined."""
    crop = np.zeros((40, 40, 3), dtype=np.uint8)
    with (
        patch("tachon_method_scan.detect_tachon", return_value=0.50),
        patch("tachon_method_scan.detect_double_writing", return_value=0.49),
        patch("tachon_method_scan.detect_density", return_value=0.70),
        patch("tachon_method_scan.detect_noise", return_value=0.55),
    ):
        result = tms.analyze_subcell_isolated(crop, has_ink=True, scan_mode="all")

    assert result["flags_by_method"]["TACHON"] is True
    assert result["flags_by_method"]["DOBLE_ESCRITURA"] is False
    assert result["flags_by_method"]["DENSIDAD_ALTA"] is True
    assert result["flags_by_method"]["ZONA_SUCIA"] is True
    assert result["is_suspicious_combined"] == (result["scores"]["combined_score"] >= tms.COMBINED_THRESHOLD)


def test_skip_ocr_omits_digit():
    """T-S03: skip-OCR mode sets digit and confidence to null."""
    record = tms.build_subcell_record(
        pdf="data/pdfs_e14c_segunda/01_a.pdf",
        dept="01",
        block="block_b",
        label="C1_CEPEDA",
        subcell={"idx": 0, "digit": "5", "confidence": 0.99, "has_ink": True},
        analysis=tms.analyze_subcell_isolated(np.zeros((20, 20, 3), dtype=np.uint8), has_ink=True),
        run_id="test-run",
        skip_ocr=True,
    )
    assert record["digit"] is None
    assert record["confidence"] is None


def test_empty_cell_neutral_scores():
    """T-S04: empty cells return zero scores and no flags."""
    crop = np.zeros((40, 40, 3), dtype=np.uint8)
    with patch("tachon_method_scan.detect_tachon") as mock_tachon:
        result = tms.analyze_subcell_isolated(crop, has_ink=False, scan_mode="all")
        mock_tachon.assert_not_called()

    assert result["scores"]["tachon_score"] == 0.0
    assert result["scores"]["double_score"] == 0.0
    assert result["scores"]["density_score"] == 0.0
    assert result["scores"]["noise_score"] == 0.0
    assert result["scores"]["combined_score"] == 0.0
    assert all(v is False for v in result["flags_by_method"].values())
    assert result["is_suspicious_combined"] is False


def test_jsonl_native_types(tmp_path: Path):
    """T-S05: JSONL records use native Python types (no numpy scalars)."""
    scores = {
        "tachon_score": np.float64(0.5),
        "double_score": np.float64(0.0),
        "density_score": np.float64(0.0),
        "noise_score": np.float64(0.308),
        "combined_score": np.float64(0.431),
    }
    flags = {name: np.bool_(name == "TACHON") for name in tms.METHOD_NAMES}
    safe = tms._json_safe_record(
        _sample_record(
            scores=scores,
            flags_by_method=flags,
            is_suspicious_combined=np.bool_(False),
        )
    )
    out = tmp_path / "row.jsonl"
    out.write_text(json.dumps(safe) + "\n", encoding="utf-8")
    row = json.loads(out.read_text(encoding="utf-8"))
    for key, value in row["scores"].items():
        assert type(value) is float
    for value in row["flags_by_method"].values():
        assert type(value) is bool
    assert type(row["is_suspicious_combined"]) is bool


def test_jsonl_schema_double_score_present():
    """T-J01: ink subcells always include double_score."""
    record = _sample_record(has_ink=True)
    errors = tms.validate_jsonl_record(record)
    assert errors == []
    assert isinstance(record["scores"]["double_score"], float)


def test_jsonl_schema_validation():
    """T-J02: schema validator rejects malformed records."""
    record = _sample_record()
    del record["flags_by_method"]["DOBLE_ESCRITURA"]
    errors = tms.validate_jsonl_record(record)
    assert any("DOBLE_ESCRITURA" in err for err in errors)


def test_aggregates_from_fixture(tmp_path: Path):
    """T-A01: aggregate builder computes summary, co-occurrence, histograms."""
    records = [
        _sample_record(
            dept="01",
            has_ink=True,
            scores={"tachon_score": 1.0, "double_score": 0.0, "density_score": 0.0, "noise_score": 0.6, "combined_score": 0.46},
            flags_by_method={"TACHON": True, "DOBLE_ESCRITURA": False, "DENSIDAD_ALTA": False, "ZONA_SUCIA": True},
            is_suspicious_combined=True,
        ),
        _sample_record(
            dept="16",
            label="C2_ABELARDO",
            has_ink=True,
            scores={"tachon_score": 0.2, "double_score": 0.0, "density_score": 0.0, "noise_score": 0.1, "combined_score": 0.08},
            flags_by_method={"TACHON": False, "DOBLE_ESCRITURA": False, "DENSIDAD_ALTA": False, "ZONA_SUCIA": False},
            is_suspicious_combined=False,
        ),
        _sample_record(has_ink=False, digit=None, confidence=None),
    ]
    jsonl_path = tmp_path / "fixture.jsonl"
    _write_jsonl(jsonl_path, records)

    manifest = {"manifest_hash": "sha256:test", "entries": []}
    aggregates = tms.build_aggregates(
        jsonl_path,
        manifest=manifest,
        scan_mode="all",
        elapsed_s=1.5,
        workers=2,
        errors=[],
        run_id="tachon-pattern-scan-500pdf",
    )

    summary = aggregates["summary"]
    assert summary["ink_subcells"] == 2
    assert summary["per_method_flag_rates"]["TACHON"]["flagged"] == 1
    assert summary["per_method_flag_rates"]["ZONA_SUCIA"]["flagged"] == 1
    assert "per_dept" in summary
    assert "per_label" in summary

    cooc = aggregates["cooccurrence"]
    assert cooc["ink_subcells"] == 2
    assert len(cooc["matrix"]) == 4
    assert "conditional" in cooc

    hist = aggregates["histograms"]
    assert "tachon_score" in hist
    assert hist["tachon_score"]["n"] == 2
    assert "p50" in hist["tachon_score"]


def test_completion_manifest_hash(tiny_manifest: Path, tmp_path: Path):
    """T-A02: completion metadata includes manifest hash."""
    manifest = json.loads(tiny_manifest.read_text(encoding="utf-8"))
    completion = tms.build_completion_metadata(
        manifest=manifest,
        scan_mode="all",
        workers=2,
        skip_ocr=True,
        pdfs_requested=2,
        pdfs_ok=2,
        pdfs_error=0,
        subcell_records=5,
        elapsed_s=3.0,
        errors=[],
        run_id="tachon-pattern-scan-500pdf",
    )
    assert completion["manifest_hash"] == manifest["manifest_hash"]


def test_report_sections_present(tmp_path: Path):
    """T-R01: markdown report contains all seven required sections."""
    aggregates = {
        "summary": {
            "run_id": "tachon-pattern-scan-500pdf",
            "manifest_hash": "sha256:abc",
            "ink_subcells": 10,
            "pdf_count": 2,
            "error_count": 0,
            "per_method_flag_rates": {
                "TACHON": {"flagged": 8, "total_ink": 10, "rate": 0.8},
                "DOBLE_ESCRITURA": {"flagged": 1, "total_ink": 10, "rate": 0.1},
                "DENSIDAD_ALTA": {"flagged": 0, "total_ink": 10, "rate": 0.0},
                "ZONA_SUCIA": {"flagged": 2, "total_ink": 10, "rate": 0.2},
                "COMBINED": {"flagged": 3, "total_ink": 10, "rate": 0.3},
            },
            "per_dept": {
                "01": {"pdfs": 1, "tachon_rate": 0.9, "zona_sucia_rate": 0.2},
                "16": {"pdfs": 1, "tachon_rate": 0.7, "zona_sucia_rate": 0.2},
            },
            "per_label": {
                "C1_CEPEDA": {"tachon_rate": 0.9, "zona_sucia_rate": 0.1},
                "NIVELACION": {"tachon_rate": 0.5, "zona_sucia_rate": 0.0},
            },
            "validate_holdout_count": 1,
        },
        "cooccurrence": {
            "methods": tms.METHOD_NAMES,
            "ink_subcells": 10,
            "joint_counts": {"TACHON+ZONA_SUCIA": 2},
            "matrix": [[8, 1, 0, 2], [1, 1, 0, 0], [0, 0, 0, 0], [2, 0, 0, 2]],
            "conditional": {"P_TACHON_given_ZONA_SUCIA": 1.0},
        },
        "histograms": {
            "tachon_score": {"bins": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0], "counts": [1, 0, 1, 0, 0, 0, 0, 0, 0, 8], "p50": 1.0, "p90": 1.0, "p99": 1.0, "max": 1.0, "n": 10},
            "double_score": {"bins": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0], "counts": [9, 1, 0, 0, 0, 0, 0, 0, 0, 0], "p50": 0.0, "p90": 0.0, "p99": 0.0, "max": 0.1, "n": 10},
            "density_score": {"bins": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0], "counts": [10, 0, 0, 0, 0, 0, 0, 0, 0, 0], "p50": 0.0, "p90": 0.0, "p99": 0.0, "max": 0.0, "n": 10},
            "noise_score": {"bins": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0], "counts": [8, 0, 0, 0, 0, 2, 0, 0, 0, 0], "p50": 0.0, "p90": 0.6, "p99": 0.6, "max": 0.6, "n": 10},
        },
        "completion": {
            "run_id": "tachon-pattern-scan-500pdf",
            "manifest_hash": "sha256:abc",
            "scan_mode": "all_methods",
            "workers": 2,
            "skip_ocr": True,
            "pdfs_requested": 2,
            "pdfs_ok": 2,
            "pdfs_error": 0,
            "error_rate": 0.0,
            "subcell_records": 10,
            "elapsed_s": 1.0,
            "completed_at": "2026-06-22T12:00:00+00:00",
            "errors": [],
        },
    }
    report = tms.render_markdown_report(aggregates)
    required = [
        "## 1. Run Metadata",
        "## 2. Per-Method Flag Rates",
        "## 3. Top Departments by TACHON Rate",
        "## 4. Per-Label Hit Rates",
        "## 5. Co-occurrence Matrix",
        "## 6. Score Percentiles",
        "## 7. Validate Baseline Note",
    ]
    for section in required:
        assert section in report


def test_missing_prereq_exit(tmp_path: Path, monkeypatch):
    """T-P01: missing D2 prerequisite exits with code 1."""
    monkeypatch.setattr(tms, "PREREQ_FILES", [tmp_path / "missing_worker.py"])
    assert tms.check_prerequisites() is False
    with pytest.raises(SystemExit) as exc:
        tms.ensure_prerequisites()
    assert exc.value.code == 1


def test_direct_detector_import_not_analyze_cell():
    """T-D01: scan uses direct detector functions, never analyze_cell()."""
    from src.modules.analyzer.tachon_detector import (
        detect_density,
        detect_double_writing,
        detect_noise,
        detect_tachon,
    )

    assert tms.METHODS["TACHON"][0] is detect_tachon
    assert tms.METHODS["DOBLE_ESCRITURA"][0] is detect_double_writing
    assert tms.METHODS["DENSIDAD_ALTA"][0] is detect_density
    assert tms.METHODS["ZONA_SUCIA"][0] is detect_noise

    module_source = inspect.getsource(tms)
    assert "from src.modules.analyzer.tachon_detector import" in module_source
    assert "analyze_cell(" not in module_source