"""Tests for src/modules/crop/manifest.py.

TDD cycle: tests written first (RED) before implementation exists.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.modules.crop.manifest import (
    load_manifest,
    append_records,
    update_fields,
    make_crop_id,
)


# ---------------------------------------------------------------------------
# make_crop_id
# ---------------------------------------------------------------------------


class TestMakeCropId:
    def test_deterministic_same_inputs(self):
        """Same inputs produce the same id."""
        id1 = make_crop_id("key1", "C1_CEPEDA", 0, "e14c", "subcell")
        id2 = make_crop_id("key1", "C1_CEPEDA", 0, "e14c", "subcell")
        assert id1 == id2

    def test_different_inputs_different_id(self):
        id1 = make_crop_id("key1", "C1_CEPEDA", 0, "e14c", "subcell")
        id2 = make_crop_id("key1", "C1_CEPEDA", 1, "e14c", "subcell")
        assert id1 != id2

    def test_id_is_32_hex_chars(self):
        cid = make_crop_id("key1", "URNA", None, "e14t", "fullcell")
        assert len(cid) == 32
        assert all(c in "0123456789abcdef" for c in cid)

    def test_none_subcell_pos_is_consistent(self):
        """None subcell_pos (fullcell) produces a stable id."""
        id1 = make_crop_id("mesa1", "URNA", None, "e14c", "fullcell")
        id2 = make_crop_id("mesa1", "URNA", None, "e14c", "fullcell")
        assert id1 == id2


# ---------------------------------------------------------------------------
# load_manifest
# ---------------------------------------------------------------------------


class TestLoadManifest:
    def test_empty_file_returns_empty_dict(self, tmp_path: Path):
        p = tmp_path / "manifest.jsonl"
        p.write_text("")
        result = load_manifest(p)
        assert result == {}

    def test_missing_file_returns_empty_dict(self, tmp_path: Path):
        p = tmp_path / "no_such_file.jsonl"
        result = load_manifest(p)
        assert result == {}

    def test_single_record_indexed_by_crop_id(self, tmp_path: Path):
        p = tmp_path / "manifest.jsonl"
        rec = {"crop_id": "abc123", "field_name": "URNA", "value": 42}
        p.write_text(json.dumps(rec) + "\n")
        result = load_manifest(p)
        assert "abc123" in result
        assert result["abc123"]["field_name"] == "URNA"

    def test_last_wins_merge(self, tmp_path: Path):
        """Two records with same crop_id — last write wins."""
        p = tmp_path / "manifest.jsonl"
        r1 = {"crop_id": "abc", "local_path": None, "value": 1}
        r2 = {"crop_id": "abc", "local_path": "crops/01/001/01/abc.png"}
        with p.open("w") as f:
            f.write(json.dumps(r1) + "\n")
            f.write(json.dumps(r2) + "\n")
        result = load_manifest(p)
        assert len(result) == 1
        assert result["abc"]["local_path"] == "crops/01/001/01/abc.png"
        # value from r1 preserved since r2 didn't include it
        assert result["abc"]["value"] == 1

    def test_delta_merge_preserves_base_fields(self, tmp_path: Path):
        """A delta record (partial fields) merges into the base record."""
        p = tmp_path / "manifest.jsonl"
        base = {"crop_id": "x1", "field_name": "BLANCO", "supabase_uploaded": False, "local_path": None}
        delta = {"crop_id": "x1", "local_path": "crops/x1.png"}
        with p.open("w") as f:
            f.write(json.dumps(base) + "\n")
            f.write(json.dumps(delta) + "\n")
        result = load_manifest(p)
        assert result["x1"]["field_name"] == "BLANCO"
        assert result["x1"]["local_path"] == "crops/x1.png"
        assert result["x1"]["supabase_uploaded"] is False

    def test_multiple_distinct_records(self, tmp_path: Path):
        p = tmp_path / "manifest.jsonl"
        records = [
            {"crop_id": "a", "val": 1},
            {"crop_id": "b", "val": 2},
            {"crop_id": "c", "val": 3},
        ]
        with p.open("w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        result = load_manifest(p)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# append_records
# ---------------------------------------------------------------------------


class TestAppendRecords:
    def test_new_records_appended(self, tmp_path: Path):
        p = tmp_path / "manifest.jsonl"
        records = [{"crop_id": "r1", "val": 10}, {"crop_id": "r2", "val": 20}]
        append_records(p, records)
        result = load_manifest(p)
        assert "r1" in result
        assert "r2" in result

    def test_dedup_skips_existing_crop_ids(self, tmp_path: Path):
        """append_records skips crop_ids already in the file."""
        p = tmp_path / "manifest.jsonl"
        existing = {"crop_id": "dup", "val": 1}
        p.write_text(json.dumps(existing) + "\n")

        append_records(p, [{"crop_id": "dup", "val": 99}, {"crop_id": "new", "val": 5}])
        lines = [l for l in p.read_text().strip().split("\n") if l]
        assert len(lines) == 2  # original + new only; dup was skipped

    def test_creates_file_if_missing(self, tmp_path: Path):
        p = tmp_path / "new_manifest.jsonl"
        assert not p.exists()
        append_records(p, [{"crop_id": "x", "val": 1}])
        assert p.exists()
        result = load_manifest(p)
        assert "x" in result

    def test_empty_records_list_is_noop(self, tmp_path: Path):
        p = tmp_path / "manifest.jsonl"
        p.write_text("")
        append_records(p, [])
        assert p.read_text() == ""


# ---------------------------------------------------------------------------
# update_fields
# ---------------------------------------------------------------------------


class TestUpdateFields:
    def test_update_appends_delta_record(self, tmp_path: Path):
        p = tmp_path / "manifest.jsonl"
        base = {"crop_id": "u1", "local_path": None, "supabase_uploaded": False}
        p.write_text(json.dumps(base) + "\n")

        update_fields(p, "u1", local_path="crops/u1.png")
        result = load_manifest(p)
        assert result["u1"]["local_path"] == "crops/u1.png"

    def test_update_does_not_overwrite_other_fields(self, tmp_path: Path):
        p = tmp_path / "manifest.jsonl"
        base = {"crop_id": "u2", "field_name": "NULOS", "supabase_uploaded": False}
        p.write_text(json.dumps(base) + "\n")

        update_fields(p, "u2", supabase_uploaded=True)
        result = load_manifest(p)
        assert result["u2"]["field_name"] == "NULOS"
        assert result["u2"]["supabase_uploaded"] is True

    def test_update_multiple_fields_at_once(self, tmp_path: Path):
        p = tmp_path / "manifest.jsonl"
        base = {"crop_id": "u3", "a": 1, "b": 2}
        p.write_text(json.dumps(base) + "\n")

        update_fields(p, "u3", a=10, b=20)
        result = load_manifest(p)
        assert result["u3"]["a"] == 10
        assert result["u3"]["b"] == 20
