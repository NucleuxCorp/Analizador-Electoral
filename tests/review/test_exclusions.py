"""Tests for exclusions module (T4)."""
from __future__ import annotations

import json
from pathlib import Path

from src.modules.review.exclusions import load_excluded_keys, merge_decisions

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class TestExclusions:
    def test_fixture_excludes_28_confirmed_keys(self):
        decisions_dir = FIXTURES
        sample = json.loads(
            (FIXTURES / "decisiones_merged_sample.json").read_text(encoding="utf-8")
        )
        assert sample["_meta"]["confirmed_count"] == 28

        excluded = load_excluded_keys("confirmed", decisions_dir=decisions_dir)
        assert len(excluded) == 28

    def test_all_mode_includes_non_confirmed_keys(self):
        decisions_dir = FIXTURES
        merged = merge_decisions(decisions_dir)
        all_keys = load_excluded_keys("all", decisions_dir=decisions_dir)
        confirmed = load_excluded_keys("confirmed", decisions_dir=decisions_dir)
        assert len(all_keys) == len(merged)
        assert len(all_keys) > len(confirmed)