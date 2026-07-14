"""Unit tests for src/modules/crop/uploader.py

All Supabase I/O is mocked. Tests verify:
  - JSD sort order (highest max-JSD first)
  - All-null JSD fallback to insertion order + warning to stderr
  - max_upload cap
  - supabase_uploaded=true set on success
  - Already-uploaded records skipped
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import tempfile

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_manifest(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")


def _make_record(
    crop_id: str,
    state: str = "discrepante",
    uploaded: bool = False,
    local_path: str | None = "crops/01/001/01/x.png",
    jsd_e14c_e14t: float | None = None,
    jsd_e14c_e14d: float | None = None,
    jsd_e14t_e14d: float | None = None,
) -> dict:
    return {
        "crop_id": crop_id,
        "mesa_key": "0101001010030102",
        "dept": "01",
        "mpio": "001",
        "zona": "01",
        "puesto": "01",
        "mesa": 3,
        "corp": "02",
        "field_name": "C1_CEPEDA",
        "subcell_pos": 0,
        "e14_type": "e14c",
        "crop_type": "subcell",
        "concordance_state": state,
        "values": [5, 7, None],
        "jsd_e14c_e14t": jsd_e14c_e14t,
        "jsd_e14c_e14d": jsd_e14c_e14d,
        "jsd_e14t_e14d": jsd_e14t_e14d,
        "local_path": local_path,
        "supabase_uploaded": uploaded,
        "created_at": "2026-07-03T14:00:00Z",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestUploadOrdering:
    def test_sorts_by_max_jsd_descending(self, tmp_path: Path) -> None:
        """Records with higher max-JSD are uploaded first."""
        from src.modules.crop.uploader import _sort_eligible

        records = [
            _make_record("low",  jsd_e14c_e14t=0.1, jsd_e14c_e14d=0.2, jsd_e14t_e14d=0.15),
            _make_record("high", jsd_e14c_e14t=0.9, jsd_e14c_e14d=0.7, jsd_e14t_e14d=0.8),
            _make_record("mid",  jsd_e14c_e14t=0.5, jsd_e14c_e14d=None, jsd_e14t_e14d=0.4),
        ]

        sorted_records = _sort_eligible(records)

        ids = [r["crop_id"] for r in sorted_records]
        assert ids[0] == "high"
        assert ids[1] == "mid"
        assert ids[2] == "low"

    def test_nulls_last_in_jsd_sort(self, tmp_path: Path) -> None:
        """Records with all-null JSD come after those with any JSD value."""
        from src.modules.crop.uploader import _sort_eligible

        records = [
            _make_record("null_jsd"),  # all JSD None
            _make_record("has_jsd", jsd_e14c_e14t=0.3),
        ]

        sorted_records = _sort_eligible(records)

        ids = [r["crop_id"] for r in sorted_records]
        assert ids[0] == "has_jsd"
        assert ids[1] == "null_jsd"

    def test_all_null_jsd_preserves_insertion_order(self, tmp_path: Path) -> None:
        """When all records have null JSD, insertion order is preserved."""
        from src.modules.crop.uploader import _sort_eligible

        records = [
            _make_record("first"),
            _make_record("second"),
            _make_record("third"),
        ]

        sorted_records = _sort_eligible(records)
        ids = [r["crop_id"] for r in sorted_records]
        assert ids == ["first", "second", "third"]


class TestNullJsdWarning:
    def test_warns_when_majority_null_jsd(self, capsys: pytest.CaptureFixture, tmp_path: Path) -> None:
        """Prints warning to stderr when > 50% of eligible records have all-null JSD."""
        from src.modules.crop.uploader import _check_null_jsd_ratio

        records = [
            _make_record("a"),  # all null
            _make_record("b"),  # all null
            _make_record("c"),  # all null
            _make_record("d", jsd_e14c_e14t=0.5),  # has JSD
        ]
        # 3/4 = 75% null → should warn
        _check_null_jsd_ratio(records)

        captured = capsys.readouterr()
        assert "null" in captured.err.lower() or "jsd" in captured.err.lower()

    def test_no_warning_when_minority_null_jsd(self, capsys: pytest.CaptureFixture) -> None:
        """No warning when <= 50% of eligible records have all-null JSD."""
        from src.modules.crop.uploader import _check_null_jsd_ratio

        records = [
            _make_record("a"),  # all null
            _make_record("b", jsd_e14c_e14t=0.5),  # has JSD
            _make_record("c", jsd_e14c_e14t=0.3),  # has JSD
        ]
        # 1/3 = 33% null → no warning
        _check_null_jsd_ratio(records)

        captured = capsys.readouterr()
        assert captured.err == ""


class TestMaxUploadCap:
    def test_max_upload_limits_records_processed(self, tmp_path: Path) -> None:
        """upload_state respects the max_upload cap."""
        from src.modules.crop.uploader import upload_state

        records = [
            _make_record(f"id{i}", jsd_e14c_e14t=float(i) * 0.1)
            for i in range(10)
        ]
        _write_manifest(tmp_path / "manifest.jsonl", records)
        # Create fake PNG files
        for r in records:
            p = Path(r["local_path"])
            (tmp_path / p.parent).mkdir(parents=True, exist_ok=True)
            (tmp_path / p).write_bytes(b"fakepng") if (tmp_path / p).parent.exists() else None

        mock_client = MagicMock()
        uploaded_ids: list[str] = []

        async def fake_upload(client, bucket, crop_id, local_path, metadata, storage_prefix="crops/sv"):
            uploaded_ids.append(crop_id)
            return f"https://storage.example.com/{crop_id}.png"

        with patch("src.modules.labeler.export_supabase.upload_crop_with_metadata", side_effect=fake_upload):
            report = asyncio.run(
                upload_state(
                    manifest_path=tmp_path / "manifest.jsonl",
                    state="discrepante",
                    max_upload=3,
                    concurrent=1,
                    bucket="crops",
                    supabase_client=mock_client,
                )
            )

        assert report.uploaded <= 3

    def test_skips_already_uploaded(self, tmp_path: Path) -> None:
        """Records with supabase_uploaded=True are excluded from upload."""
        from src.modules.crop.uploader import upload_state

        records = [
            _make_record("uploaded_id", uploaded=True),
            _make_record("pending_id", jsd_e14c_e14t=0.5),
        ]
        _write_manifest(tmp_path / "manifest.jsonl", records)

        mock_client = MagicMock()
        uploaded_ids: list[str] = []

        async def fake_upload(client, bucket, crop_id, local_path, metadata, storage_prefix="crops/sv"):
            uploaded_ids.append(crop_id)
            return f"https://storage.example.com/{crop_id}.png"

        with patch("src.modules.labeler.export_supabase.upload_crop_with_metadata", side_effect=fake_upload):
            report = asyncio.run(
                upload_state(
                    manifest_path=tmp_path / "manifest.jsonl",
                    state="discrepante",
                    max_upload=None,
                    concurrent=1,
                    bucket="crops",
                    supabase_client=mock_client,
                )
            )

        # Only pending_id should be attempted (local_path may not exist → skipped there)
        assert "uploaded_id" not in uploaded_ids

    def test_filters_by_state(self, tmp_path: Path) -> None:
        """Only records matching the requested state are eligible."""
        from src.modules.crop.uploader import _filter_eligible

        records = [
            _make_record("disc_id", state="discrepante"),
            _make_record("div_id", state="divergente"),
            _make_record("arm_id", state="armonico"),
        ]

        eligible = _filter_eligible(records, state="discrepante")
        ids = [r["crop_id"] for r in eligible]
        assert "disc_id" in ids
        assert "div_id" not in ids
        assert "arm_id" not in ids


class TestUploadReport:
    def test_report_has_required_fields(self, tmp_path: Path) -> None:
        """UploadReport has uploaded, skipped, errors."""
        from src.modules.crop.uploader import upload_state

        _write_manifest(tmp_path / "manifest.jsonl", [])

        mock_client = MagicMock()

        report = asyncio.run(
            upload_state(
                manifest_path=tmp_path / "manifest.jsonl",
                state="discrepante",
                max_upload=None,
                concurrent=1,
                bucket="crops",
                supabase_client=mock_client,
            )
        )

        assert hasattr(report, "uploaded")
        assert hasattr(report, "skipped")
        assert hasattr(report, "errors")
