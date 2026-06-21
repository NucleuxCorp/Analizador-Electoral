"""Unit tests for retrain pipeline — fetch + split logic."""
from __future__ import annotations

import json
import os
import random
import tempfile
from pathlib import Path

import pytest

from split_dataset import (
    cache_split,
    check_min_per_class,
    load_manifest,
    load_or_create_split,
    normalize_label,
    stratified_split,
)
from fetch_confirmed_crops import (
    ZERO_VARIANTS,
    _dedupe_labels_by_crop_id,
    _storage_url_for,
)


# ---------------------------------------------------------------------------
# split_dataset tests
# ---------------------------------------------------------------------------

class TestNormalizeLabel:
    """FR-1.3 / NFR-7: map zero variants to '0', exclude invalid labels."""

    @pytest.mark.parametrize("variant", ["*", "-", ".", "+", "o", "O"])
    def test_zero_variant_maps_to_zero(self, variant):
        assert normalize_label(variant) == "0"

    def test_digits_passthrough(self):
        for d in "0123456789":
            assert normalize_label(d) == d

    def test_skip_excluded(self):
        assert normalize_label("_skip") is None

    def test_invalid_label_excluded(self):
        assert normalize_label("A") is None
        assert normalize_label("10") is None


class TestLoadManifest:
    """FR-1.2: dedupe train_pool_manifest.jsonl by crop_id keeping most recent ts."""

    def test_dedupe_keeps_most_recent_label(self, tmp_path: Path):
        manifest = tmp_path / "pool.jsonl"
        # Manifest rows must be ordered by ts DESC (most recent first) per design.
        lines = [
            {"crop_id": "c_7", "label_human": "3", "ts": "2024-01-02T00:00:00Z"},
            {"crop_id": "c_7", "label_human": "5", "ts": "2024-01-01T00:00:00Z"},
            {"crop_id": "c_8", "label_human": "2", "ts": "2024-01-01T00:00:00Z"},
        ]
        manifest.write_text("\n".join(json.dumps(ln) for ln in lines) + "\n", encoding="utf-8")

        records = load_manifest(manifest)

        assert len(records) == 2
        by_id = {r["crop_id"]: r["label_human"] for r in records}
        assert by_id["c_7"] == "3"
        assert by_id["c_8"] == "2"


class TestStratifiedSplit:
    """FR-1.4: deterministic stratified 90/10 split by class."""

    def test_split_is_deterministic(self, tmp_path: Path):
        manifest = tmp_path / "pool.jsonl"
        records = []
        for cls in "0123456789":
            for i in range(100):
                records.append({"crop_id": f"c_{cls}_{i}", "label_human": cls})
        manifest.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

        pool = load_manifest(manifest)
        train1, val1 = stratified_split(pool, val_ratio=0.1, seed=42)
        train2, val2 = stratified_split(pool, val_ratio=0.1, seed=42)

        assert [r["crop_id"] for r in train1] == [r["crop_id"] for r in train2]
        assert [r["crop_id"] for r in val1] == [r["crop_id"] for r in val2]
        assert len(train1) == 900
        assert len(val1) == 100

    def test_stratification_preserved(self, tmp_path: Path):
        manifest = tmp_path / "pool.jsonl"
        records = []
        for cls in "0123456789":
            for i in range(100):
                records.append({"crop_id": f"c_{cls}_{i}", "label_human": cls})
        manifest.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

        pool = load_manifest(manifest)
        train, val = stratified_split(pool, val_ratio=0.1, seed=42)

        val_by_class = {}
        for r in val:
            val_by_class.setdefault(r["label_human"], 0)
            val_by_class[r["label_human"]] += 1

        for cls in "0123456789":
            assert val_by_class[cls] == 10


class TestMinPerClass:
    """FR-1.5: abort if any class has fewer than 50 examples."""

    def test_abort_when_class_below_threshold(self):
        records = [{"crop_id": f"c_{i}", "label_human": "9"} for i in range(47)]
        for cls in "012345678":
            for i in range(50):
                records.append({"crop_id": f"c_{cls}_{i}", "label_human": cls})

        with pytest.raises(RuntimeError, match=r"Class 9 has 47 samples"):
            check_min_per_class(records, min_per_class=50)

    def test_pass_when_all_classes_above_threshold(self):
        records = []
        for cls in "0123456789":
            for i in range(50):
                records.append({"crop_id": f"c_{cls}_{i}", "label_human": cls})
        # normalize labels so split_dataset sees canonical classes
        for r in records:
            r["label_human"] = normalize_label(r["label_human"])
        check_min_per_class(records, min_per_class=50)  # should not raise


class TestTestTrainIsolation:
    """FR-1.1 / NFR-2: confirmed crop_ids must not leak into train/val."""

    def test_no_overlap_between_test_and_train_val(self, tmp_path: Path):
        confirmed = tmp_path / "confirmed.jsonl"
        confirmed.write_text(
            "\n".join(json.dumps({"crop_id": f"c_{i}", "confirmed_label": str(i % 10)}) for i in range(20))
            + "\n",
            encoding="utf-8",
        )
        pool = tmp_path / "pool.jsonl"
        records = []
        for cls in "0123456789":
            for i in range(60):
                records.append({"crop_id": f"pool_{cls}_{i}", "label_human": cls})
        pool.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

        test_ids, train, val = load_or_create_split(confirmed, pool, tmp_path)

        train_val_ids = {r["crop_id"] for r in train + val}
        assert set(test_ids).isdisjoint(train_val_ids)


class TestCacheSplit:
    """FR-1.6: cached split is reused on re-run."""

    def test_load_from_cache_when_present(self, tmp_path: Path):
        confirmed = tmp_path / "confirmed.jsonl"
        confirmed.write_text(json.dumps({"crop_id": "c_test", "confirmed_label": "1"}) + "\n", encoding="utf-8")
        pool = tmp_path / "pool.jsonl"
        pool_lines = [
            json.dumps({"crop_id": f"c_{cls}_{i}", "label_human": cls}) + "\n"
            for cls in "0123456789"
            for i in range(60)
        ]
        pool.write_text("".join(pool_lines), encoding="utf-8")

        # First run creates cache.
        test_ids1, train1, val1 = load_or_create_split(confirmed, pool, tmp_path)
        assert (tmp_path / "test_crop_ids.json").exists()
        assert (tmp_path / "train_val_split.json").exists()

        # Second run must reuse cache deterministically.
        test_ids2, train2, val2 = load_or_create_split(confirmed, pool, tmp_path)
        assert set(test_ids1) == set(test_ids2)
        assert {r["crop_id"] for r in train1} == {r["crop_id"] for r in train2}
        assert {r["crop_id"] for r in val1} == {r["crop_id"] for r in val2}


# ---------------------------------------------------------------------------
# fetch_confirmed_crops tests
# ---------------------------------------------------------------------------

class TestFetchHelpers:
    """Pure helpers in fetch_confirmed_crops.py — no network."""

    def test_zero_variants_constant_matches_db(self):
        # DB contract: these six glyphs are semantically zero.
        assert ZERO_VARIANTS == {"*", "-", ".", "+", "o", "O"}

    def test_dedupe_labels_keeps_first_most_recent(self):
        rows = [
            {"crop_id": "c_1", "label_human": "5", "ts": "2024-01-02T00:00:00Z"},
            {"crop_id": "c_1", "label_human": "3", "ts": "2024-01-01T00:00:00Z"},
            {"crop_id": "c_2", "label_human": "7", "ts": "2024-01-01T00:00:00Z"},
        ]
        deduped = _dedupe_labels_by_crop_id(rows)
        assert len(deduped) == 2
        assert deduped["c_1"]["label_human"] == "5"
        assert deduped["c_2"]["label_human"] == "7"

    def test_storage_url_uses_supabase_url(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
        assert _storage_url_for("c_42") == "https://example.supabase.co/storage/v1/object/public/crops/c_42.png"


@pytest.mark.skipif(not os.environ.get("SUPABASE_URL"), reason="SUPABASE_URL not set")
class TestFetchIntegration:
    """Integration smoke: module imports and client creation. Run only when Supabase env present."""

    def test_import_does_not_run_main(self):
        # Importing the module must not trigger network I/O.
        import fetch_confirmed_crops as ffc
        assert callable(ffc.fetch_confirmed_crops)
        assert callable(ffc.fetch_train_pool)
