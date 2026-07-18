"""Tests for get_public_stats(), /api/public-stats route, home_view extension,
and the format_number Jinja filter (home-public-stats SDD change).

TDD cycle: RED → GREEN → REFACTOR.
All Supabase calls are mocked. No live connection required.
"""
from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers (matching pattern in test_mesa_results_db.py)
# ---------------------------------------------------------------------------

def _make_count_chain(count: int) -> MagicMock:
    """Return a fluent chain mock whose .execute() returns a response with count=N."""
    chain = MagicMock()
    chain.select.return_value = chain
    chain.not_ = chain  # .not_ is an attribute access (PostgREST)
    chain.is_.return_value = chain
    chain.limit.return_value = chain
    resp = MagicMock()
    resp.count = count
    resp.data = []
    chain.execute.return_value = resp
    return chain


def _make_client(chain: MagicMock | None = None) -> MagicMock:
    """Return a mock Supabase client wired to the given chain."""
    client = MagicMock()
    if chain is not None:
        client.table.return_value = chain
    return client


def _global_stats(
    total: int = 1000,
    clean: int = 900,
    known_anomaly: int = 50,
    warning: int = 30,
    discrepancy: int = 15,
    needs_review: int = 5,
) -> dict:
    """Return a stats dict with a _global key, matching get_mesa_stats() shape."""
    return {
        "_global": {
            "total": total,
            "clean": clean,
            "known_anomaly": known_anomaly,
            "warning": warning,
            "discrepancy": discrepancy,
            "needs_review_large_delta": needs_review,
        }
    }


ZEROS_DICT = {
    "mesas_all_three": 118_543,
    "mesas_analyzed": 0,
    "mesas_remaining": 122_020,
    "total_anomalias": 0,
    "total_universe": 122_020,
    "mesas_sin_e14c": 0,
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_public_stats_cache():
    """Clear the module-level cache before every test that needs a cold call."""
    from src.modules.labeler import db as db_module
    db_module._mesa_stats_cache.pop("public_stats", None)
    yield
    db_module._mesa_stats_cache.pop("public_stats", None)


@pytest.fixture
def prod_app(tmp_path):
    """Flask app in production mode with all Supabase calls mocked."""
    env_vars = {
        "SUPABASE_URL": "https://fake.supabase.co",
        "SUPABASE_ANON_KEY": "fake-anon-key",
        "SECRET_KEY": "test-secret-public-stats",
    }
    with patch.dict(os.environ, env_vars):
        from src.modules.labeler.server import create_app
        index_path = tmp_path / "crops" / "index.jsonl"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        app = create_app(index_path=index_path, labels_dir=tmp_path)
        app.config["TESTING"] = True
        yield app


# ---------------------------------------------------------------------------
# Phase 1.2 — test_happy_path
# ---------------------------------------------------------------------------

class TestGetPublicStatsHappyPath:
    """get_public_stats() returns correct dict on the happy path."""

    def test_happy_path_all_keys_present(self):
        """Returns a dict with all five required keys."""
        from src.modules.labeler.db import get_public_stats

        chain = _make_count_chain(5000)
        mock_client = _make_client(chain)
        stats = _global_stats(total=1000)

        with patch("src.modules.labeler.db._client", return_value=mock_client), \
             patch("src.modules.labeler.db.get_mesa_stats", return_value=stats):
            result = get_public_stats()

        assert set(result.keys()) == {
            "mesas_all_three", "mesas_analyzed", "mesas_remaining",
            "total_anomalias", "total_universe", "mesas_sin_e14c",
        }

    def test_happy_path_mesas_all_three_is_constant(self):
        """mesas_all_three equals the MESAS_ALL_THREE disk-audit constant."""
        from src.modules.labeler.db import get_public_stats, MESAS_ALL_THREE

        stats = _global_stats()

        with patch("src.modules.labeler.db.get_mesa_stats", return_value=stats):
            result = get_public_stats()

        assert result["mesas_all_three"] == MESAS_ALL_THREE

    def test_happy_path_mesas_analyzed_from_global_total(self):
        """mesas_analyzed equals _global.total from get_mesa_stats()."""
        from src.modules.labeler.db import get_public_stats

        chain = _make_count_chain(0)
        mock_client = _make_client(chain)
        stats = _global_stats(total=3500)

        with patch("src.modules.labeler.db._client", return_value=mock_client), \
             patch("src.modules.labeler.db.get_mesa_stats", return_value=stats):
            result = get_public_stats()

        assert result["mesas_analyzed"] == 3500

    def test_happy_path_mesas_remaining(self):
        """mesas_remaining == total_universe - mesas_analyzed."""
        from src.modules.labeler.db import get_public_stats, TOTAL_UNIVERSE

        chain = _make_count_chain(0)
        mock_client = _make_client(chain)
        stats = _global_stats(total=3500)

        with patch("src.modules.labeler.db._client", return_value=mock_client), \
             patch("src.modules.labeler.db.get_mesa_stats", return_value=stats):
            result = get_public_stats()

        assert result["mesas_remaining"] == TOTAL_UNIVERSE - 3500

    def test_happy_path_total_anomalias_sum(self):
        """total_anomalias = known_anomaly + warning + discrepancy + needs_review_large_delta."""
        from src.modules.labeler.db import get_public_stats

        chain = _make_count_chain(0)
        mock_client = _make_client(chain)
        stats = _global_stats(known_anomaly=50, warning=30, discrepancy=15, needs_review=5)

        with patch("src.modules.labeler.db._client", return_value=mock_client), \
             patch("src.modules.labeler.db.get_mesa_stats", return_value=stats):
            result = get_public_stats()

        assert result["total_anomalias"] == 50 + 30 + 15 + 5

    def test_happy_path_total_universe_constant(self):
        """total_universe is always 122_020 regardless of other values."""
        from src.modules.labeler.db import get_public_stats

        chain = _make_count_chain(0)
        mock_client = _make_client(chain)
        stats = _global_stats()

        with patch("src.modules.labeler.db._client", return_value=mock_client), \
             patch("src.modules.labeler.db.get_mesa_stats", return_value=stats):
            result = get_public_stats()

        assert result["total_universe"] == 122_020

    def test_happy_path_mesas_sin_e14c_is_distinct_count(self):
        """mesas_sin_e14c reflects count_mesa_results(), not the mesas_all_three constant."""
        from src.modules.labeler.db import get_public_stats, MESAS_ALL_THREE

        stats = _global_stats()

        with patch("src.modules.labeler.db.get_mesa_stats", return_value=stats), \
             patch("src.modules.labeler.db.count_mesa_results", return_value=4_321) as mock_count:
            result = get_public_stats()

        assert result["mesas_sin_e14c"] == 4_321
        assert result["mesas_all_three"] == MESAS_ALL_THREE
        assert mock_count.call_count == 1

    def test_happy_path_mesas_sin_e14c_uses_hardcoded_national_scope(self):
        """count_mesa_results() is called with source_missing='e14c' only — no dept filter."""
        from src.modules.labeler.db import get_public_stats

        stats = _global_stats()

        with patch("src.modules.labeler.db.get_mesa_stats", return_value=stats), \
             patch("src.modules.labeler.db.count_mesa_results", return_value=0) as mock_count:
            get_public_stats()

        mock_count.assert_called_once_with(source_missing="e14c")


# ---------------------------------------------------------------------------
# Phase 1.3 — test_cache_hit
# ---------------------------------------------------------------------------

class TestGetPublicStatsCacheHit:
    """Second call within TTL does not trigger a new Supabase query."""

    def test_cache_hit_no_second_query(self):
        """Calling get_public_stats() twice within TTL makes only one Supabase call."""
        from src.modules.labeler.db import get_public_stats

        chain = _make_count_chain(500)
        mock_client = _make_client(chain)
        stats = _global_stats()
        now = time.monotonic()

        with patch("src.modules.labeler.db._client", return_value=mock_client), \
             patch("src.modules.labeler.db.get_mesa_stats", return_value=stats) as mock_stats, \
             patch("time.monotonic", side_effect=[now, now + 1, now + 1]):
            get_public_stats()
            get_public_stats()

        # get_mesa_stats should be called only once (cache hit on second call)
        assert mock_stats.call_count == 1

    def test_cache_hit_no_second_count_mesa_results_call(self):
        """Calling get_public_stats() twice within TTL issues count_mesa_results() only once."""
        from src.modules.labeler.db import get_public_stats

        chain = _make_count_chain(500)
        mock_client = _make_client(chain)
        stats = _global_stats()
        now = time.monotonic()

        with patch("src.modules.labeler.db._client", return_value=mock_client), \
             patch("src.modules.labeler.db.get_mesa_stats", return_value=stats), \
             patch("src.modules.labeler.db.count_mesa_results", return_value=100) as mock_count, \
             patch("time.monotonic", side_effect=[now, now + 1, now + 1]):
            get_public_stats()
            get_public_stats()

        assert mock_count.call_count == 1


# ---------------------------------------------------------------------------
# Phase 1.4 — test_cache_miss_after_ttl
# ---------------------------------------------------------------------------

class TestGetPublicStatsCacheMissAfterTTL:
    """After 300 seconds the cache is stale and a fresh query is issued."""

    def test_cache_miss_after_ttl_issues_second_query(self):
        """Advancing time past TTL causes a second Supabase query."""
        from src.modules.labeler.db import get_public_stats

        chain = _make_count_chain(500)
        mock_client = _make_client(chain)
        stats = _global_stats()
        now = time.monotonic()

        with patch("src.modules.labeler.db._client", return_value=mock_client), \
             patch("src.modules.labeler.db.get_mesa_stats", return_value=stats) as mock_stats, \
             patch("time.monotonic", side_effect=[now, now + 301, now + 301]):
            get_public_stats()
            get_public_stats()

        assert mock_stats.call_count == 2

    def test_cache_miss_after_ttl_issues_second_count_mesa_results_call(self):
        """Advancing time past TTL causes a second count_mesa_results() call."""
        from src.modules.labeler.db import get_public_stats

        chain = _make_count_chain(500)
        mock_client = _make_client(chain)
        stats = _global_stats()
        now = time.monotonic()

        with patch("src.modules.labeler.db._client", return_value=mock_client), \
             patch("src.modules.labeler.db.get_mesa_stats", return_value=stats), \
             patch("src.modules.labeler.db.count_mesa_results", return_value=100) as mock_count, \
             patch("time.monotonic", side_effect=[now, now + 301, now + 301]):
            get_public_stats()
            get_public_stats()

        assert mock_count.call_count == 2


# ---------------------------------------------------------------------------
# Phase 1.5 — test_supabase_exception_returns_zeros
# ---------------------------------------------------------------------------

class TestGetPublicStatsException:
    """On any exception, get_public_stats() returns zeros dict without raising."""

    def test_supabase_exception_returns_zeros(self):
        """When _client() raises, returns zeros dict."""
        from src.modules.labeler.db import get_public_stats

        with patch("src.modules.labeler.db._client", side_effect=RuntimeError("no client")):
            result = get_public_stats()

        assert result == ZEROS_DICT

    def test_count_mesa_results_exception_returns_zeros(self):
        """When count_mesa_results() itself raises, returns zeros dict (fail-closed)."""
        from src.modules.labeler.db import get_public_stats

        stats = _global_stats()

        with patch("src.modules.labeler.db.get_mesa_stats", return_value=stats), \
             patch("src.modules.labeler.db.count_mesa_results", side_effect=RuntimeError("down")):
            result = get_public_stats()

        assert result == ZEROS_DICT

    def test_supabase_exception_does_not_raise(self):
        """get_public_stats never re-raises exceptions."""
        from src.modules.labeler.db import get_public_stats

        with patch("src.modules.labeler.db._client", side_effect=Exception("network down")):
            try:
                get_public_stats()
            except Exception:
                pytest.fail("get_public_stats() raised an exception — it must not")


# ---------------------------------------------------------------------------
# Phase 1.6 — test_get_mesa_stats_empty_returns_zeros
# ---------------------------------------------------------------------------

class TestGetPublicStatsMesaStatsEmpty:
    """When get_mesa_stats() returns empty dict, analyzed and anomalias are 0."""

    def test_empty_global_returns_zeros_for_analyzed_and_anomalias(self):
        """_global key absent → mesas_analyzed=0, total_anomalias=0."""
        from src.modules.labeler.db import get_public_stats

        chain = _make_count_chain(0)
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client), \
             patch("src.modules.labeler.db.get_mesa_stats", return_value={}):
            result = get_public_stats()

        assert result["mesas_analyzed"] == 0
        assert result["total_anomalias"] == 0

    def test_empty_global_does_not_raise(self):
        """No exception when _global is missing."""
        from src.modules.labeler.db import get_public_stats

        chain = _make_count_chain(0)
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client), \
             patch("src.modules.labeler.db.get_mesa_stats", return_value={}):
            try:
                get_public_stats()
            except Exception:
                pytest.fail("get_public_stats() raised when _global is missing")


# ---------------------------------------------------------------------------
# Phase 1.10 — test_home_view_injects_stats


# ---------------------------------------------------------------------------
# Phase 1.10 — test_home_view_injects_stats
# ---------------------------------------------------------------------------

class TestHomeViewInjectsStats:
    """home_view() passes public_stats variables to the template context."""

    def test_home_view_renders_stats_in_html(self, prod_app):
        """GET / includes the numeric values from get_public_stats() in the HTML."""
        known = {
            "mesas_all_three": 500,
            "mesas_analyzed": 1000,
            "mesas_remaining": 121_020,
            "total_anomalias": 100,
            "total_universe": 122_020,
            "mesas_sin_e14c": 3_670,
        }
        with patch("src.modules.labeler.db.get_public_stats", return_value=known):
            client = prod_app.test_client()
            resp = client.get("/")

        assert resp.status_code == 200
        html = resp.data.decode()
        # The template should render these values somewhere in the page
        assert "500" in html or "122.020" in html or "1.000" in html
        assert "3.670" in html or "3670" in html
        assert "Mesas sin acta oficial E14C" in html

    def test_home_view_renders_dash_when_mesas_sin_e14c_is_zero(self, prod_app):
        """mesas_sin_e14c == 0 renders '—' for the new card, not the literal '0'."""
        known = {
            "mesas_all_three": 500,
            "mesas_analyzed": 1000,
            "mesas_remaining": 121_020,
            "total_anomalias": 100,
            "total_universe": 122_020,
            "mesas_sin_e14c": 0,
        }
        with patch("src.modules.labeler.db.get_public_stats", return_value=known):
            client = prod_app.test_client()
            resp = client.get("/")

        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'id="counter-sin-e14c" data-target="0">—<' in html

    def test_home_view_new_counter_script_omits_localstorage_floor(self, prod_app):
        """The counter-sin-e14c IIFE does not reuse the analyzed counter's LS floor."""
        known = {
            "mesas_all_three": 500,
            "mesas_analyzed": 1000,
            "mesas_remaining": 121_020,
            "total_anomalias": 100,
            "total_universe": 122_020,
            "mesas_sin_e14c": 3_670,
        }
        with patch("src.modules.labeler.db.get_public_stats", return_value=known):
            client = prod_app.test_client()
            resp = client.get("/")

        html = resp.data.decode()
        assert 'getElementById("counter-sin-e14c")' in html

        scripts = html.split("<script>")[1:]
        sin_e14c_script = next(
            (s for s in scripts if 'getElementById("counter-sin-e14c")' in s), None
        )
        assert sin_e14c_script is not None
        assert "mesas_analyzed_v1" not in sin_e14c_script


# ---------------------------------------------------------------------------
# Phase 1.11 — test_home_view_catches_exception_passes_zeros
# ---------------------------------------------------------------------------

class TestHomeViewExceptionFallback:
    """home_view() catches exceptions from get_public_stats() and passes zeros."""

    def test_home_view_exception_still_returns_200(self, prod_app):
        """GET / returns 200 even when get_public_stats() raises."""
        with patch("src.modules.labeler.db.get_public_stats", side_effect=RuntimeError("boom")):
            client = prod_app.test_client()
            resp = client.get("/")

        assert resp.status_code == 200

    def test_home_view_exception_renders_zero_state_marker(self, prod_app):
        """When get_public_stats() raises, the page shows the '—' zero-state marker."""
        with patch("src.modules.labeler.db.get_public_stats", side_effect=RuntimeError("boom")):
            client = prod_app.test_client()
            resp = client.get("/")

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "—" in html


# ---------------------------------------------------------------------------
# Security tripwire — no new public filterable surface (defense-in-depth)
# ---------------------------------------------------------------------------

class TestNoPublicFilterableSurface:
    """home_view() ignores mesa-filter query params; /admin/mesas/data stays auth-gated."""

    def test_home_view_ignores_source_missing_query_params(self, prod_app):
        """GET / with mesa-filter query params renders the same counter as plain GET /."""
        known = {
            "mesas_all_three": 500,
            "mesas_analyzed": 1000,
            "mesas_remaining": 121_020,
            "total_anomalias": 100,
            "total_universe": 122_020,
            "mesas_sin_e14c": 3_670,
        }
        with patch("src.modules.labeler.db.get_public_stats", return_value=known):
            client = prod_app.test_client()
            plain = client.get("/")
            filtered = client.get(
                "/?source=e14c&dept=88&mesa_key=X&mpio=001&zona=01&puesto=001"
            )

        assert plain.status_code == 200
        assert filtered.status_code == 200
        assert plain.data == filtered.data

    def test_admin_mesas_data_source_param_still_requires_auth(self, prod_app):
        """GET /admin/mesas/data?source=e14c unauthenticated is rejected, not filtered."""
        client = prod_app.test_client()
        resp = client.get("/admin/mesas/data?source=e14c")

        assert resp.status_code != 200


# ---------------------------------------------------------------------------
# Phase 1.12 — test_format_number_filter
# ---------------------------------------------------------------------------

class TestFormatNumberFilter:
    """format_number Jinja filter formats integers with dot-thousands separator."""

    def _get_filter(self, prod_app):
        return prod_app.jinja_env.filters["format_number"]

    def test_format_number_122020(self, prod_app):
        """122020 → '122.020'."""
        f = self._get_filter(prod_app)
        assert f(122_020) == "122.020"

    def test_format_number_zero(self, prod_app):
        """0 → '0' (NOT '—', zero-state is handled in the template)."""
        f = self._get_filter(prod_app)
        assert f(0) == "0"

    def test_format_number_none_returns_dash(self, prod_app):
        """None → '—'."""
        f = self._get_filter(prod_app)
        assert f(None) == "—"

    def test_format_number_large(self, prod_app):
        """1000000 → '1.000.000'."""
        f = self._get_filter(prod_app)
        assert f(1_000_000) == "1.000.000"

    def test_format_number_small(self, prod_app):
        """999 → '999' (no separator needed)."""
        f = self._get_filter(prod_app)
        assert f(999) == "999"


# ---------------------------------------------------------------------------
# Phase 1.2b — TOTAL_UNIVERSE constant
# ---------------------------------------------------------------------------

class TestTotalUniverseConstant:
    """TOTAL_UNIVERSE constant is exported from db.py."""

    def test_total_universe_value(self):
        """TOTAL_UNIVERSE equals 122_020."""
        from src.modules.labeler.db import TOTAL_UNIVERSE
        assert TOTAL_UNIVERSE == 122_020
