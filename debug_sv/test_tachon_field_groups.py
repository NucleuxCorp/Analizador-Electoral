"""
D2: field_groups schema + tachon enrichment (strict TDD).

Write tests first; all must fail on pre-D2 codebase, then pass after implementation.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from debug_sv.grid_detector_v2 import CELL_PAD, is_known_label

PDF_GOOD = ROOT / "data/pdfs_e14c_segunda/01_001_01_01_E14_PRE_01_001_001_01_01_001_5002.pdf"
PDF_OFFSET = ROOT / "data/pdfs_e14c_segunda/01_001_01_01_E14_PRE_01_001_001_01_01_003_5002.pdf"

MOCK_IMG = np.full((32, 32, 3), 255, dtype=np.uint8)
TACHON_KEYS = ("score", "tachon_score", "density_score", "noise_score", "flags", "is_suspicious")


def _load_worker():
    spec = importlib.util.spec_from_file_location(
        "e14_worker", ROOT / "debug_sv" / "e14_worker.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_candidate_subcells():
    spec = importlib.util.spec_from_file_location(
        "candidate_subcells", ROOT / "debug_sv" / "candidate_subcells.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_cell_analysis(
    *,
    score: float = 0.0,
    tachon_score: float = 0.0,
    density_score: float = 0.0,
    noise_score: float = 0.0,
    flags: list[str] | None = None,
    is_suspicious: bool = False,
):
    mock = MagicMock()
    mock.to_dict.return_value = {
        "score": score,
        "tachon_score": tachon_score,
        "density_score": density_score,
        "noise_score": noise_score,
        "flags": flags or [],
        "is_suspicious": is_suspicious,
    }
    return mock


@pytest.fixture
def mock_analyze_cell():
    calls: list[tuple] = []

    def _side_effect(crop):
        calls.append(("crop", crop.shape if hasattr(crop, "shape") else None))
        return make_cell_analysis(score=0.12, is_suspicious=False)

    with patch(
        "debug_sv.subcell_tachon.analyze_cell", side_effect=_side_effect
    ) as mocked:
        mocked.calls = calls
        yield mocked


def assert_subcell_schema(subcell: dict) -> None:
    for key in ("idx", "x1", "y1", "x2", "y2", "digit", "confidence", "has_ink", "tachon"):
        assert key in subcell, f"missing subcell key: {key}"
    for key in TACHON_KEYS:
        assert key in subcell["tachon"], f"missing tachon key: {key}"


def assert_field_groups_invariant(record: dict) -> None:
    fg = record["field_groups"]
    fields = record.get("fields", {})
    for block in fg.values():
        for label, entry in block.get("fields", {}).items():
            if label in fields and fields[label] is not None:
                assert entry["value"] == fields[label]


def run_worker_mocked(pdf_rel: str, mock_analyze_cell) -> dict:
    worker = _load_worker()
    worker.init_worker()
    return worker.process_pdf_task(pdf_rel, save_suspicious_crops=False)


@pytest.fixture
def synthetic_labeled_rows():
    return [
        {
            "label": "VOTANTES",
            "top": 978,
            "bot": 1068,
            "y_mid": 1023,
            "label_source": "anchor_block_a",
            "cells": [
                {"x1": 905, "y1": 978, "x2": 998, "y2": 1068},
                {"x1": 998, "y1": 978, "x2": 1091, "y2": 1068},
                {"x1": 1091, "y1": 978, "x2": 1187, "y2": 1068},
            ],
        },
        {
            "label": "URNA",
            "top": 1105,
            "bot": 1193,
            "y_mid": 1149,
            "label_source": "anchor_block_a",
            "cells": [
                {"x1": 905, "y1": 1105, "x2": 998, "y2": 1193},
                {"x1": 998, "y1": 1105, "x2": 1091, "y2": 1193},
                {"x1": 1091, "y1": 1105, "x2": 1187, "y2": 1193},
            ],
        },
        {
            "label": "C1_CEPEDA",
            "top": 1750,
            "bot": 1842,
            "y_mid": 1796,
            "label_source": "structure",
            "cells": [
                {"x1": 905, "y1": 1750, "x2": 998, "y2": 1842},
                {"x1": 998, "y1": 1750, "x2": 1091, "y2": 1842},
                {"x1": 1091, "y1": 1750, "x2": 1187, "y2": 1842},
            ],
        },
    ]


# ---------------------------------------------------------------------------
# Schema tests — T-D2-S01..S05
# ---------------------------------------------------------------------------
class TestFieldGroupsSchema:
    def test_field_groups_has_three_blocks(self, mock_analyze_cell):
        if not PDF_GOOD.exists():
            pytest.skip("gold PDF missing")
        rel = "data/pdfs_e14c_segunda/01_001_01_01_E14_PRE_01_001_001_01_01_001_5002.pdf"
        record = run_worker_mocked(rel, mock_analyze_cell)
        assert "error" not in record
        fg = record["field_groups"]
        for block in ("block_a", "block_b", "block_c"):
            assert block in fg
            assert "labels" in fg[block]
            assert "fields" in fg[block]

    def test_subcell_required_keys(self, mock_analyze_cell):
        if not PDF_GOOD.exists():
            pytest.skip("gold PDF missing")
        rel = "data/pdfs_e14c_segunda/01_001_01_01_E14_PRE_01_001_001_01_01_001_5002.pdf"
        record = run_worker_mocked(rel, mock_analyze_cell)
        fg = record["field_groups"]
        found = False
        for block in fg.values():
            for entry in block["fields"].values():
                for sub in entry.get("subcells", []):
                    assert_subcell_schema(sub)
                    found = True
        assert found, "expected at least one subcell in gold PDF"

    def test_fields_value_invariant(self, mock_analyze_cell):
        if not PDF_GOOD.exists():
            pytest.skip("gold PDF missing")
        rel = "data/pdfs_e14c_segunda/01_001_01_01_E14_PRE_01_001_001_01_01_001_5002.pdf"
        record = run_worker_mocked(rel, mock_analyze_cell)
        assert_field_groups_invariant(record)

    def test_rows_unchanged_shape(self, mock_analyze_cell):
        if not PDF_GOOD.exists():
            pytest.skip("gold PDF missing")
        rel = "data/pdfs_e14c_segunda/01_001_01_01_E14_PRE_01_001_001_01_01_001_5002.pdf"
        record = run_worker_mocked(rel, mock_analyze_cell)
        for row in record.get("rows", []):
            assert set(row.keys()) == {"label", "value", "digits", "confidences", "y_mid"}

    def test_unk_rows_excluded(self, mock_analyze_cell):
        if not PDF_GOOD.exists():
            pytest.skip("gold PDF missing")
        rel = "data/pdfs_e14c_segunda/01_001_01_01_E14_PRE_01_001_001_01_01_001_5002.pdf"
        record = run_worker_mocked(rel, mock_analyze_cell)
        fg = record["field_groups"]
        for block in fg.values():
            for label in block["fields"]:
                assert not label.startswith("UNK@")


# ---------------------------------------------------------------------------
# Mock tachon tests — T-D2-M01..M05
# ---------------------------------------------------------------------------
class TestTachonMocked:
    def test_analyze_cell_mocked_on_inked_crop(self, mock_analyze_cell):
        if not PDF_GOOD.exists():
            pytest.skip("gold PDF missing")
        rel = "data/pdfs_e14c_segunda/01_001_01_01_E14_PRE_01_001_001_01_01_001_5002.pdf"
        run_worker_mocked(rel, mock_analyze_cell)
        assert mock_analyze_cell.call_count > 0
        for call in mock_analyze_cell.call_args_list:
            crop = call[0][0]
            assert isinstance(crop, np.ndarray)
            assert len(crop.shape) == 3

    def test_crop_padding_matches_ocr(self):
        from debug_sv.subcell_tachon import enrich_subcell_tachon

        img = np.full((100, 100, 3), 255, dtype=np.uint8)
        img[30:70, 30:70] = 0
        cell = {"x1": 20, "y1": 20, "x2": 80, "y2": 80}
        with patch("debug_sv.subcell_tachon.analyze_cell") as mocked:
            mocked.return_value = make_cell_analysis()
            enrich_subcell_tachon(img, cell, has_ink=True, pad=CELL_PAD)
            crop = mocked.call_args[0][0]
            assert crop.shape[0] == (80 - CELL_PAD) - (20 + CELL_PAD)
            assert crop.shape[1] == (80 - CELL_PAD) - (20 + CELL_PAD)

    def test_neutral_tachon_when_no_ink(self):
        from debug_sv.subcell_tachon import NEUTRAL_TACHON, enrich_subcell_tachon

        cell = {"x1": 10, "y1": 10, "x2": 50, "y2": 50}
        with patch("debug_sv.subcell_tachon.analyze_cell") as mocked:
            result = enrich_subcell_tachon(MOCK_IMG, cell, has_ink=False)
            mocked.assert_not_called()
            assert result == NEUTRAL_TACHON
            assert result["is_suspicious"] is False

    def test_tachon_summary_aggregation(self):
        worker = _load_worker()
        fg = {
            "block_a": {"labels": [], "fields": {}},
            "block_b": {
                "labels": ["C1_CEPEDA"],
                "fields": {
                    "C1_CEPEDA": {
                        "value": 53,
                        "y_mid": 1796,
                        "y_top": 1750,
                        "y_bot": 1842,
                        "label_source": "structure",
                        "subcells": [
                            {
                                "idx": 0,
                                "has_ink": True,
                                "tachon": {
                                    "score": 0.6,
                                    "tachon_score": 0.5,
                                    "density_score": 0.0,
                                    "noise_score": 0.0,
                                    "flags": ["TACHON"],
                                    "is_suspicious": True,
                                },
                            },
                            {
                                "idx": 1,
                                "has_ink": True,
                                "tachon": {
                                    "score": 0.7,
                                    "tachon_score": 0.6,
                                    "density_score": 0.0,
                                    "noise_score": 0.0,
                                    "flags": ["DENSIDAD_ALTA"],
                                    "is_suspicious": True,
                                },
                            },
                        ],
                    }
                },
            },
            "block_c": {"labels": [], "fields": {}},
        }
        summary = worker._build_tachon_summary(fg)
        assert summary["analyzed_subcells"] == 2
        assert summary["suspicious_subcells"] == 2
        assert summary["flags_by_label"]["C1_CEPEDA"] == ["DENSIDAD_ALTA", "TACHON"]

    def test_assess_appends_tachon_suspicious(self):
        worker = _load_worker()
        record = {
            "fields": {
                "VOTANTES": 100,
                "URNA": 50,
                "INCINER": 0,
                "C1_CEPEDA": 30,
                "C2_ABELARDO": 20,
                "BLANCO": 0,
                "NULOS": 0,
                "NO_MARCADOS": 0,
                "SUMA_TOTAL": 50,
            },
            "rows_accepted": 9,
            "method": "primary",
            "tachon_summary": {"suspicious_subcells": 1, "flags_by_label": {}},
        }
        is_suspicious, reasons = worker._assess(record)
        assert "tachon_suspicious" in reasons
        assert is_suspicious is False


# ---------------------------------------------------------------------------
# Candidate subcells — T-D2-C01..C04
# ---------------------------------------------------------------------------
class TestCandidateSubcells:
    def test_read_candidate_row_returns_three_subcells(self, mock_analyze_cell):
        cand = _load_candidate_subcells()
        grid_mod = cand.grid
        gray = np.full((3500, 1200), 255, dtype=np.uint8)
        img = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        gray[1750:1842, 905:1187] = 50
        row = {"top": 1750, "bot": 1842, "cells": []}
        engine = MagicMock()
        engine.predict_digit.return_value = ("5", 1.0)
        with patch.object(grid_mod, "ocr_cell", return_value=("5", 1.0)):
            with patch.object(grid_mod, "cell_has_ink", return_value=True):
                result = cand.read_candidate_row(
                    gray, img, row, "C1_CEPEDA", grid_mod.SUBCELL_X_FALLBACK, engine, CELL_PAD
                )
        assert "subcells" in result
        assert len(result["subcells"]) == 3
        assert [s["idx"] for s in result["subcells"]] == [0, 1, 2]

    def test_block_b_includes_v_lines_row_on_fallback(self):
        worker = _load_worker()
        fg, _ = worker._build_field_groups(
            labeled_rows=[
                {
                    "label": "C1_CEPEDA",
                    "top": 1750,
                    "bot": 1842,
                    "y_mid": 1796,
                    "label_source": "structure",
                    "cells": [],
                }
            ],
            primary_row_subcells={},
            fallback_candidates=[
                {
                    "label": "C1_CEPEDA",
                    "value": 53,
                    "y_top": 1750,
                    "y_bot": 1842,
                    "v_lines_row": [905, 998, 1091, 1187],
                    "subcells": [],
                }
            ],
            flat_fields={"C1_CEPEDA": 53},
        )
        entry = fg["block_b"]["fields"]["C1_CEPEDA"]
        assert "v_lines_row" in entry
        assert len(entry["v_lines_row"]) == 4

    def test_fallback_subcells_prefer_over_primary(self):
        worker = _load_worker()
        fallback_subcells = [
            {"idx": 0, "digit": "0", "confidence": 0.0, "has_ink": False,
             "x1": 1, "y1": 1, "x2": 2, "y2": 2,
             "tachon": {"score": 0.0, "tachon_score": 0.0, "density_score": 0.0,
                        "noise_score": 0.0, "flags": [], "is_suspicious": False}},
            {"idx": 1, "digit": "5", "confidence": 1.0, "has_ink": True,
             "x1": 3, "y1": 1, "x2": 4, "y2": 2,
             "tachon": {"score": 0.62, "tachon_score": 0.58, "density_score": 0.0,
                        "noise_score": 0.0, "flags": ["TACHON"], "is_suspicious": True}},
            {"idx": 2, "digit": "3", "confidence": 1.0, "has_ink": True,
             "x1": 5, "y1": 1, "x2": 6, "y2": 2,
             "tachon": {"score": 0.1, "tachon_score": 0.05, "density_score": 0.0,
                        "noise_score": 0.0, "flags": [], "is_suspicious": False}},
        ]
        primary_subcells = [
            {"idx": 0, "digit": "9", "confidence": 0.9, "has_ink": True,
             "x1": 1, "y1": 1, "x2": 2, "y2": 2,
             "tachon": {"score": 0.0, "tachon_score": 0.0, "density_score": 0.0,
                        "noise_score": 0.0, "flags": [], "is_suspicious": False}},
        ] * 3
        fg, _ = worker._build_field_groups(
            labeled_rows=[
                {
                    "label": "C1_CEPEDA",
                    "top": 1750,
                    "bot": 1842,
                    "y_mid": 1796,
                    "label_source": "structure",
                    "cells": [],
                }
            ],
            primary_row_subcells={"C1_CEPEDA": primary_subcells},
            fallback_candidates=[
                {
                    "label": "C1_CEPEDA",
                    "value": 53,
                    "y_top": 1750,
                    "y_bot": 1842,
                    "v_lines_row": [905, 998, 1091, 1187],
                    "subcells": fallback_subcells,
                }
            ],
            flat_fields={"C1_CEPEDA": 53},
        )
        entry = fg["block_b"]["fields"]["C1_CEPEDA"]
        assert entry["subcells"][1]["digit"] == "5"
        assert entry["v_lines_row"] == [905, 998, 1091, 1187]

    def test_primary_only_block_b_no_v_lines_row(self, mock_analyze_cell):
        if not PDF_GOOD.exists():
            pytest.skip("gold PDF missing")
        rel = "data/pdfs_e14c_segunda/01_001_01_01_E14_PRE_01_001_001_01_01_001_5002.pdf"
        record = run_worker_mocked(rel, mock_analyze_cell)
        if record.get("fallback_triggered"):
            pytest.skip("gold PDF triggered fallback")
        for entry in record["field_groups"]["block_b"]["fields"].values():
            assert "v_lines_row" not in entry


# ---------------------------------------------------------------------------
# Empty-cell policy — T-D2-E01..E03
# ---------------------------------------------------------------------------
class TestEmptyCellPolicy:
    def test_empty_cell_digit_zero_confidence_zero(self):
        from debug_sv.subcell_tachon import build_subcell_payload

        cell = {"idx": 0, "x1": 10, "y1": 10, "x2": 50, "y2": 50}
        payload = build_subcell_payload(
            cell, digit="0", confidence=0.0, has_ink=False, img=MOCK_IMG
        )
        assert payload["digit"] == "0"
        assert payload["confidence"] == 0.0
        assert payload["has_ink"] is False

    def test_healthy_votantes_zero_no_false_tachon(self, mock_analyze_cell):
        if not PDF_GOOD.exists():
            pytest.skip("gold PDF missing")
        rel = "data/pdfs_e14c_segunda/01_001_01_01_E14_PRE_01_001_001_01_01_001_5002.pdf"
        record = run_worker_mocked(rel, mock_analyze_cell)
        ts = record.get("tachon_summary")
        if ts is not None:
            assert ts.get("suspicious_subcells", 0) == 0

    def test_tachon_summary_omitted_when_no_analysis(self):
        worker = _load_worker()
        fg = {
            "block_a": {
                "labels": ["VOTANTES"],
                "fields": {
                    "VOTANTES": {
                        "value": 0,
                        "y_mid": 1023,
                        "y_top": 978,
                        "y_bot": 1068,
                        "label_source": "anchor_block_a",
                        "subcells": [
                            {
                                "idx": 0,
                                "has_ink": False,
                                "tachon": {
                                    "score": 0.0,
                                    "tachon_score": 0.0,
                                    "density_score": 0.0,
                                    "noise_score": 0.0,
                                    "flags": [],
                                    "is_suspicious": False,
                                },
                            }
                        ],
                    }
                },
            },
            "block_b": {"labels": [], "fields": {}},
            "block_c": {"labels": [], "fields": {}},
        }
        assert worker._build_tachon_summary(fg) is None


# ---------------------------------------------------------------------------
# Regression guards — T-D2-R01..R02
# ---------------------------------------------------------------------------
class TestRegressionGuards:
    def test_gold_pdf_field_groups_mirror_fields(self, mock_analyze_cell):
        if not PDF_GOOD.exists():
            pytest.skip("gold PDF missing")
        rel = "data/pdfs_e14c_segunda/01_001_01_01_E14_PRE_01_001_001_01_01_001_5002.pdf"
        record = run_worker_mocked(rel, mock_analyze_cell)
        fields = record.get("fields", {})
        fg_labels = set()
        for block in record["field_groups"].values():
            fg_labels.update(block["fields"].keys())
        for label in fields:
            if is_known_label(label) and fields[label] is not None:
                assert label in fg_labels

    def test_d1_labels_required_for_block_a_source(self, mock_analyze_cell):
        if not PDF_GOOD.exists():
            pytest.skip("gold PDF missing")
        rel = "data/pdfs_e14c_segunda/01_001_01_01_E14_PRE_01_001_001_01_01_001_5002.pdf"
        record = run_worker_mocked(rel, mock_analyze_cell)
        block_a = record["field_groups"]["block_a"]["fields"]
        for entry in block_a.values():
            assert entry["label_source"] == "anchor_block_a"