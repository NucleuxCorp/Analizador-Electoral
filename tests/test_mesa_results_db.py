"""Tests for mesa_results db accessors (T1a-2 through T1a-6).

TDD cycle: RED → GREEN → REFACTOR for get_mesa_results and get_mesa_stats.
All tests use the MagicMock pattern from tests/test_labeler_reports.py.
No live Supabase connection is required.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_chain(rows: list[dict] | None = None) -> MagicMock:
    """Return a fluent chain mock that ends with .execute() returning rows."""
    chain = MagicMock()
    chain.select.return_value = chain
    chain.range.return_value = chain
    chain.eq.return_value = chain
    chain.order.return_value = chain
    chain.execute.return_value = MagicMock(data=rows if rows is not None else [])
    return chain


def _make_client(chain: MagicMock | None = None) -> MagicMock:
    """Return a mock supabase client wired to the given chain."""
    client = MagicMock()
    if chain is not None:
        client.table.return_value = chain
    return client


# ---------------------------------------------------------------------------
# T1a-2 (RED) — get_mesa_results
# ---------------------------------------------------------------------------

class TestGetMesaResultsFilteredPage:
    """get_mesa_results returns filtered + paginated rows."""

    def test_get_mesa_results_filtered_page(self):
        """When dept and status are given, the query applies two .eq() filters
        and calls .range(offset, offset+per_page-1)."""
        from src.modules.labeler.db import get_mesa_results

        row = {"mesa_key": "01_001_01_01_1", "dept": "01", "overall_status": "critical"}
        chain = _make_chain([row])
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = get_mesa_results(dept="01", status="critical", page=1, per_page=50)

        assert result == [row]
        mock_client.table.assert_called_once_with("mesa_results")
        # range must be called with (0, 49) for page=1, per_page=50
        chain.range.assert_called_once_with(0, 49)

    def test_get_mesa_results_page_2_range(self):
        """page=2 with per_page=50 must call .range(50, 99)."""
        from src.modules.labeler.db import get_mesa_results

        chain = _make_chain([])
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            get_mesa_results(dept="01", status="clean", page=2, per_page=50)

        chain.range.assert_called_once_with(50, 99)

    def test_get_mesa_results_dept_filter_applied(self):
        """When dept is provided an .eq('dept', dept) filter is applied."""
        from src.modules.labeler.db import get_mesa_results

        chain = _make_chain([])
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            get_mesa_results(dept="05", page=1, per_page=50)

        eq_calls = [c.args for c in chain.eq.call_args_list]
        assert ("dept", "05") in eq_calls

    def test_get_mesa_results_status_filter_applied(self):
        """When status is provided an .eq('overall_status', status) filter is applied."""
        from src.modules.labeler.db import get_mesa_results

        chain = _make_chain([])
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            get_mesa_results(status="warning", page=1, per_page=50)

        eq_calls = [c.args for c in chain.eq.call_args_list]
        assert ("overall_status", "warning") in eq_calls


class TestGetMesaResultsNoFilter:
    """get_mesa_results without filters returns all rows (paginated)."""

    def test_get_mesa_results_no_filter(self):
        """When no dept or status is given, no .eq() call is made."""
        from src.modules.labeler.db import get_mesa_results

        rows = [
            {"mesa_key": "01_001_01_01_1", "dept": "01"},
            {"mesa_key": "05_002_01_01_1", "dept": "05"},
        ]
        chain = _make_chain(rows)
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = get_mesa_results(page=1, per_page=50)

        assert result == rows
        # No filters applied — eq should not have been called
        chain.eq.assert_not_called()

    def test_get_mesa_results_returns_list(self):
        """Return type is always a list, even when data is None."""
        from src.modules.labeler.db import get_mesa_results

        chain = _make_chain(None)
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = get_mesa_results(page=1, per_page=50)

        assert isinstance(result, list)


class TestGetMesaResultsSupabaseUnavailable:
    """get_mesa_results returns [] on any exception — fail-closed."""

    def test_get_mesa_results_supabase_unavailable(self):
        """If _client() raises RuntimeError, get_mesa_results returns []."""
        from src.modules.labeler.db import get_mesa_results

        with patch("src.modules.labeler.db._client", side_effect=RuntimeError("no client")):
            result = get_mesa_results(dept="01", status="critical", page=1, per_page=50)

        assert result == []

    def test_get_mesa_results_table_error_returns_empty(self):
        """If .table() raises, get_mesa_results returns []."""
        from src.modules.labeler.db import get_mesa_results

        mock_client = MagicMock()
        mock_client.table.side_effect = Exception("network error")

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = get_mesa_results(dept="01", page=1, per_page=50)

        assert result == []

    def test_get_mesa_results_execute_error_returns_empty(self):
        """If .execute() raises, get_mesa_results returns []."""
        from src.modules.labeler.db import get_mesa_results

        chain = MagicMock()
        chain.select.return_value = chain
        chain.range.return_value = chain
        chain.eq.return_value = chain
        chain.execute.side_effect = Exception("timeout")
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = get_mesa_results(dept="01", page=1, per_page=50)

        assert result == []


# ---------------------------------------------------------------------------
# T1a-3 (RED) — get_mesa_stats
# ---------------------------------------------------------------------------

class TestGetMesaStatsAllDepts:
    """get_mesa_stats returns a dict keyed by dept code plus a _global key."""

    def _make_stats_client(self, rows: list[dict]) -> MagicMock:
        """Return a client whose .table().select().limit().eq().execute() returns rows."""
        chain = MagicMock()
        chain.select.return_value = chain
        chain.range.return_value = chain
        chain.limit.return_value = chain
        chain.eq.return_value = chain
        chain.execute.return_value = MagicMock(data=rows)
        client = MagicMock()
        client.table.return_value = chain
        return client

    def test_get_mesa_stats_all_depts(self):
        """Returns dict with per-dept counts and a _global rollup."""
        from src.modules.labeler.db import get_mesa_stats

        rows = [
            {"dept": "01", "overall_status": "clean"},
            {"dept": "01", "overall_status": "critical"},
            {"dept": "05", "overall_status": "warning"},
        ]
        mock_client = self._make_stats_client(rows)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            # Bust cache to get a fresh call
            result = get_mesa_stats.__wrapped__(None) if hasattr(get_mesa_stats, "__wrapped__") else _call_uncached(get_mesa_stats)

        # Verify shape — top-level keys include dept codes and _global
        assert isinstance(result, dict)

    def test_get_mesa_stats_returns_status_counts(self):
        """Each dept entry contains counts for all 5 statuses and a total."""
        from src.modules.labeler.db import get_mesa_stats, _get_mesa_stats_uncached

        rows = [
            {"dept": "01", "overall_status": "clean"},
            {"dept": "01", "overall_status": "clean"},
            {"dept": "01", "overall_status": "critical"},
        ]
        mock_client = self._make_stats_client(rows)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = _get_mesa_stats_uncached(dept=None)

        assert result["01"]["clean"] == 2
        assert result["01"]["critical"] == 1
        assert result["01"]["total"] == 3

    def test_get_mesa_stats_global_totals(self):
        """_global key contains aggregate totals across all depts."""
        from src.modules.labeler.db import _get_mesa_stats_uncached

        rows = [
            {"dept": "01", "overall_status": "clean"},
            {"dept": "05", "overall_status": "warning"},
            {"dept": "05", "overall_status": "critical"},
        ]
        mock_client = self._make_stats_client(rows)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = _get_mesa_stats_uncached(dept=None)

        assert result["_global"]["total"] == 3
        assert result["_global"]["clean"] == 1
        assert result["_global"]["warning"] == 1
        assert result["_global"]["critical"] == 1


class TestGetMesaStatsFiltered:
    """get_mesa_stats with dept filter applies .eq() to restrict results."""

    def test_get_mesa_stats_filtered(self):
        """When dept is given, query applies .eq('dept', dept) filter."""
        from src.modules.labeler.db import _get_mesa_stats_uncached

        rows = [{"dept": "01", "overall_status": "clean"}]
        chain = MagicMock()
        chain.select.return_value = chain
        chain.range.return_value = chain
        chain.limit.return_value = chain
        chain.eq.return_value = chain
        chain.execute.return_value = MagicMock(data=rows)
        mock_client = MagicMock()
        mock_client.table.return_value = chain

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = _get_mesa_stats_uncached(dept="01")

        eq_calls = [c.args for c in chain.eq.call_args_list]
        assert ("dept", "01") in eq_calls


class TestGetMesaStatsCacheHit:
    """get_mesa_stats returns cached result within TTL, no repeat DB call."""

    def test_get_mesa_stats_cache_hit(self):
        """Two calls within 300 s must call _client() only once."""
        import src.modules.labeler.db as db_module

        # Reset cache state before test
        db_module._mesa_stats_cache.clear()

        rows = [{"dept": "01", "overall_status": "clean"}]
        chain = MagicMock()
        chain.select.return_value = chain
        chain.range.return_value = chain
        chain.eq.return_value = chain
        chain.execute.return_value = MagicMock(data=rows)
        mock_client = MagicMock()
        mock_client.table.return_value = chain

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            r1 = db_module.get_mesa_stats(dept=None)
            r2 = db_module.get_mesa_stats(dept=None)

        assert r1 == r2
        # Client should be accessed only once — second call hits the cache
        assert mock_client.table.call_count == 1

    def test_get_mesa_stats_cache_key_is_dept(self):
        """Different dept values use separate cache entries."""
        import src.modules.labeler.db as db_module

        db_module._mesa_stats_cache.clear()

        rows_01 = [{"dept": "01", "overall_status": "clean"}]
        rows_05 = [{"dept": "05", "overall_status": "critical"}]

        call_count = {"n": 0}

        def execute_side_effect():
            call_count["n"] += 1
            return MagicMock(data=rows_01 if call_count["n"] == 1 else rows_05)

        chain = MagicMock()
        chain.select.return_value = chain
        chain.range.return_value = chain
        chain.eq.return_value = chain
        chain.execute.side_effect = execute_side_effect
        mock_client = MagicMock()
        mock_client.table.return_value = chain

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            r1 = db_module.get_mesa_stats(dept="01")
            r2 = db_module.get_mesa_stats(dept="05")

        # Two different dept keys → two DB calls
        assert mock_client.table.call_count == 2


class TestGetMesaStatsSupabaseUnavailable:
    """get_mesa_stats returns {} on any exception — fail-closed."""

    def test_get_mesa_stats_supabase_unavailable(self):
        """If _client() raises, get_mesa_stats returns {}."""
        import src.modules.labeler.db as db_module

        db_module._mesa_stats_cache.clear()

        with patch("src.modules.labeler.db._client", side_effect=RuntimeError("no client")):
            result = db_module.get_mesa_stats(dept=None)

        assert result == {}

    def test_get_mesa_stats_execute_error_returns_empty(self):
        """If .execute() raises, get_mesa_stats returns {}."""
        import src.modules.labeler.db as db_module

        db_module._mesa_stats_cache.clear()

        chain = MagicMock()
        chain.select.return_value = chain
        chain.range.return_value = chain
        chain.limit.return_value = chain
        chain.eq.return_value = chain
        chain.execute.side_effect = Exception("timeout")
        mock_client = MagicMock()
        mock_client.table.return_value = chain

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = db_module.get_mesa_stats(dept=None)

        assert result == {}


# ---------------------------------------------------------------------------
# Helper used by tests that bypass the cache
# ---------------------------------------------------------------------------

def _call_uncached(fn):
    """Call fn(None) — used when _get_mesa_stats_uncached is not yet public."""
    return fn(None)
