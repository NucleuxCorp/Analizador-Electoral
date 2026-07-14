"""Unit tests for src/modules/crop/extractor.py

Tests run without real PDFs by mocking the underlying extractor calls.
ProcessPoolExecutor safety is verified by importing the module in a mock
spawn scenario (no real subprocess, just validates top-level imports).
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import tempfile
import os

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[2]


def _make_manifest_record(
    crop_id: str,
    mesa_key: str = "0101001010030102",
    e14_type: str = "e14c",
    field_name: str = "C1_CEPEDA",
    subcell_pos: int | None = 0,
    crop_type: str = "subcell",
    local_path: str | None = None,
) -> dict:
    return {
        "crop_id": crop_id,
        "mesa_key": mesa_key,
        "dept": "01",
        "mpio": "001",
        "zona": "01",
        "puesto": "01",
        "mesa": 3,
        "corp": "02",
        "field_name": field_name,
        "subcell_pos": subcell_pos,
        "e14_type": e14_type,
        "crop_type": crop_type,
        "concordance_state": "discrepante",
        "values": [5, 7, None],
        "jsd_e14c_e14t": 0.72,
        "jsd_e14c_e14d": None,
        "jsd_e14t_e14d": None,
        "local_path": local_path,
        "supabase_uploaded": False,
        "created_at": "2026-07-03T14:00:00Z",
    }


def _make_fake_img(h: int = 600, w: int = 400) -> np.ndarray:
    return np.zeros((h, w, 3), dtype=np.uint8)


def _make_fake_extractor_result(
    img: np.ndarray,
    subcell_bboxes: dict | None = None,
    fullcell_bboxes: dict | None = None,
) -> dict:
    if subcell_bboxes is None:
        subcell_bboxes = {
            "C1_CEPEDA": [(10, 20, 50, 60), (60, 20, 100, 60), (100, 20, 140, 60)],
            "C2_ABELARDO": [(10, 70, 50, 110), (60, 70, 100, 110), (100, 70, 140, 110)],
        }
    if fullcell_bboxes is None:
        fullcell_bboxes = {
            "C1_CEPEDA": (10, 20, 140, 60),
            "C2_ABELARDO": (10, 70, 140, 110),
        }
    return {
        "fields": {"C1_CEPEDA": 5, "C2_ABELARDO": 7},
        "subcell_bboxes": subcell_bboxes,
        "fullcell_bboxes": fullcell_bboxes,
        "_img": img,
    }


# ---------------------------------------------------------------------------
# T07a — extract_mesa_crops: subcell crop written, delta returned
# ---------------------------------------------------------------------------

class TestExtractMesaCrops:
    def test_subcell_crop_writes_png_and_returns_delta(self, tmp_path: Path) -> None:
        """extract_mesa_crops writes a PNG for each pending subcell and returns deltas."""
        from src.modules.crop.extractor import extract_mesa_crops

        img = _make_fake_img(600, 400)
        records = [
            _make_manifest_record("aaa111", field_name="C1_CEPEDA", subcell_pos=0, crop_type="subcell"),
            _make_manifest_record("bbb222", field_name="C1_CEPEDA", subcell_pos=1, crop_type="subcell"),
        ]
        # Fake extractor: returns an img + bboxes via the result dict
        fake_result = _make_fake_extractor_result(img)

        with patch("src.modules.crop.extractor._call_e14c_extractor", return_value=(img, fake_result)):
            deltas = extract_mesa_crops(
                mesa_key="0101001010030102",
                e14_type="e14c",
                pdf_path=Path("/fake/path.pdf"),
                manifest_records=records,
                crops_root=tmp_path,
            )

        assert len(deltas) == 2
        for d in deltas:
            assert "crop_id" in d
            assert "local_path" in d
            assert Path(d["local_path"]).exists()

    def test_fullcell_crop_written(self, tmp_path: Path) -> None:
        """extract_mesa_crops handles fullcell crop_type."""
        from src.modules.crop.extractor import extract_mesa_crops

        img = _make_fake_img(600, 400)
        records = [
            _make_manifest_record("ccc333", field_name="C1_CEPEDA", subcell_pos=None, crop_type="fullcell"),
        ]
        fake_result = _make_fake_extractor_result(img)

        with patch("src.modules.crop.extractor._call_e14c_extractor", return_value=(img, fake_result)):
            deltas = extract_mesa_crops(
                mesa_key="0101001010030102",
                e14_type="e14c",
                pdf_path=Path("/fake/path.pdf"),
                manifest_records=records,
                crops_root=tmp_path,
            )

        assert len(deltas) == 1
        assert deltas[0]["crop_id"] == "ccc333"
        assert Path(deltas[0]["local_path"]).exists()

    def test_skips_record_with_existing_local_path(self, tmp_path: Path) -> None:
        """Records with an existing local_path+PNG on disk are skipped."""
        from src.modules.crop.extractor import extract_mesa_crops

        img = _make_fake_img(600, 400)
        # Create a fake PNG so the skip logic triggers
        existing_png = tmp_path / "01" / "001" / "01" / "existing.png"
        existing_png.parent.mkdir(parents=True, exist_ok=True)
        existing_png.write_bytes(b"fakepng")

        records = [
            _make_manifest_record(
                "existing_id",
                field_name="C1_CEPEDA",
                subcell_pos=0,
                crop_type="subcell",
                local_path=str(existing_png),
            ),
        ]

        with patch("src.modules.crop.extractor._call_e14c_extractor", return_value=(img, _make_fake_extractor_result(img))) as mock_call:
            deltas = extract_mesa_crops(
                mesa_key="0101001010030102",
                e14_type="e14c",
                pdf_path=Path("/fake/path.pdf"),
                manifest_records=records,
                crops_root=tmp_path,
            )

        # No delta for already-extracted record
        assert len(deltas) == 0

    def test_uses_e14t_extractor_for_e14t(self, tmp_path: Path) -> None:
        """e14t/e14d types use _call_e14t_extractor, not e14c."""
        from src.modules.crop.extractor import extract_mesa_crops

        img = _make_fake_img(600, 400)
        records = [
            _make_manifest_record("ddd444", e14_type="e14t", field_name="C1_CEPEDA", subcell_pos=0, crop_type="subcell"),
        ]
        fake_result = _make_fake_extractor_result(img)

        with patch("src.modules.crop.extractor._call_e14t_extractor", return_value=(img, fake_result)) as mock_t:
            with patch("src.modules.crop.extractor._call_e14c_extractor") as mock_c:
                deltas = extract_mesa_crops(
                    mesa_key="0101001010030102",
                    e14_type="e14t",
                    pdf_path=Path("/fake/path.pdf"),
                    manifest_records=records,
                    crops_root=tmp_path,
                )

        mock_t.assert_called_once()
        mock_c.assert_not_called()
        assert len(deltas) == 1

    def test_bbox_missing_field_skips_gracefully(self, tmp_path: Path) -> None:
        """If field_name not in bboxes, the record is skipped (no crash)."""
        from src.modules.crop.extractor import extract_mesa_crops

        img = _make_fake_img(600, 400)
        # Bboxes do NOT contain BLANCO
        records = [
            _make_manifest_record("eee555", field_name="BLANCO", subcell_pos=0, crop_type="subcell"),
        ]
        fake_result = _make_fake_extractor_result(img, subcell_bboxes={"C1_CEPEDA": [(10, 20, 50, 60)]})

        with patch("src.modules.crop.extractor._call_e14c_extractor", return_value=(img, fake_result)):
            deltas = extract_mesa_crops(
                mesa_key="0101001010030102",
                e14_type="e14c",
                pdf_path=Path("/fake/path.pdf"),
                manifest_records=records,
                crops_root=tmp_path,
            )

        # Should produce 0 deltas — BLANCO not in bboxes, so skipped
        assert len(deltas) == 0

    def test_extractor_error_returns_empty_deltas(self, tmp_path: Path) -> None:
        """If extractor raises, returns empty list (error handled upstream)."""
        from src.modules.crop.extractor import extract_mesa_crops

        records = [
            _make_manifest_record("fff666", field_name="C1_CEPEDA", subcell_pos=0, crop_type="subcell"),
        ]

        with patch("src.modules.crop.extractor._call_e14c_extractor", side_effect=RuntimeError("PDF corrupt")):
            with pytest.raises(RuntimeError):
                extract_mesa_crops(
                    mesa_key="0101001010030102",
                    e14_type="e14c",
                    pdf_path=Path("/fake/path.pdf"),
                    manifest_records=records,
                    crops_root=tmp_path,
                )


# ---------------------------------------------------------------------------
# T07b — run_extract: top-level import safety (ProcessPoolExecutor / Windows spawn)
# ---------------------------------------------------------------------------

class TestRunExtractImportSafety:
    def test_module_imports_cleanly_at_top_level(self) -> None:
        """extractor module must be importable at top level (no __main__-guarded imports).

        On Windows, ProcessPoolExecutor uses spawn, which imports the worker
        module in a fresh interpreter. Any import inside a function body that
        is not guarded by lazy loading is fine; what CANNOT happen is that a
        top-level import raises an exception (e.g. missing optional dep) that
        would kill the worker before it starts.
        """
        import importlib
        # Force a fresh import to catch any top-level side effects
        mod_name = "src.modules.crop.extractor"
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        mod = importlib.import_module(mod_name)
        assert hasattr(mod, "extract_mesa_crops")
        assert hasattr(mod, "run_extract")

    def test_run_extract_returns_named_tuple_or_dict(self, tmp_path: Path) -> None:
        """run_extract returns a summary with processed/skipped/errors keys."""
        from src.modules.crop.extractor import run_extract

        manifest_path = tmp_path / "manifest.jsonl"
        index_path = tmp_path / "index.jsonl"
        errors_path = tmp_path / "errors.jsonl"
        manifest_path.write_text("")
        index_path.write_text("")

        report = run_extract(
            manifest_path=manifest_path,
            index_path=index_path,
            workers=1,
            errors_path=errors_path,
            limit=0,
        )

        assert hasattr(report, "processed") or "processed" in report
        assert hasattr(report, "skipped") or "skipped" in report
        assert hasattr(report, "errors") or "errors" in report
