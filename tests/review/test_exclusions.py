"""Tests for exclusions module (T4)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.modules.review import exclusions
from src.modules.review.exclusions import (
    clear_exclusions_cache,
    load_excluded_keys,
    merge_decisions,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_exclusions_cache()
    yield
    clear_exclusions_cache()


def _count_reads(monkeypatch):
    """Patch Path.read_text to count actual disk reads, return the counter list."""
    calls = []
    original = Path.read_text

    def spy(self, *args, **kwargs):
        calls.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", spy)
    return calls


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


class TestExclusionsCache:
    def test_merge_decisions_reads_files_once_across_5_calls_within_ttl(self, monkeypatch):
        calls = _count_reads(monkeypatch)

        for _ in range(5):
            merge_decisions(FIXTURES)

        # 3 decision files exist in FIXTURES; only the first call should hit disk.
        assert len(calls) == 3

    def test_merge_decisions_rereads_after_ttl_expiry(self, monkeypatch):
        calls = _count_reads(monkeypatch)

        fake_now = [1000.0]
        monkeypatch.setattr(exclusions.time, "time", lambda: fake_now[0])

        merge_decisions(FIXTURES)
        assert len(calls) == 3

        # Still within TTL: no additional reads.
        fake_now[0] += 1.0
        merge_decisions(FIXTURES)
        assert len(calls) == 3

        # Past the 300s TTL: must re-read from disk.
        fake_now[0] += exclusions._DECISIONS_CACHE_TTL + 1.0
        merge_decisions(FIXTURES)
        assert len(calls) == 6

    def test_load_excluded_keys_reuses_cached_merge(self, monkeypatch):
        calls = _count_reads(monkeypatch)

        merge_decisions(FIXTURES)
        assert len(calls) == 3

        load_excluded_keys("confirmed", decisions_dir=FIXTURES)
        load_excluded_keys("all", decisions_dir=FIXTURES)

        # load_excluded_keys must reuse the cached merge, not re-read disk.
        assert len(calls) == 3

    def test_clear_exclusions_cache_forces_reread(self, monkeypatch):
        calls = _count_reads(monkeypatch)

        merge_decisions(FIXTURES)
        assert len(calls) == 3

        clear_exclusions_cache()
        merge_decisions(FIXTURES)
        assert len(calls) == 6

    def test_force_reload_bypasses_cache(self, monkeypatch):
        calls = _count_reads(monkeypatch)

        merge_decisions(FIXTURES)
        assert len(calls) == 3

        merge_decisions(FIXTURES, force_reload=True)
        assert len(calls) == 6