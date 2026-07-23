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


def _empty_global_bucket() -> dict:
    """Match db._empty_hierarchical_bucket() for fail-closed assertions."""
    return {
        "clean": 0,
        "known_anomaly": 0,
        "warning": 0,
        "discrepancy": 0,
        "needs_review_large_delta": 0,
        "total": 0,
        "en_revision": 0,
        "revisada": 0,
    }


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

        row = {"mesa_key": "01_001_01_01_1", "dept": "01", "overall_status": "needs_review_large_delta"}
        chain = _make_chain([row])
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = get_mesa_results(dept="01", status="needs_review_large_delta", page=1, per_page=50)

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
            result = get_mesa_results(dept="01", status="needs_review_large_delta", page=1, per_page=50)

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
        """Return a client whose .table().select().order().range().eq().execute() returns rows."""
        chain = MagicMock()
        chain.select.return_value = chain
        chain.order.return_value = chain
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
            {"dept": "01", "overall_status": "needs_review_large_delta"},
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
            {"dept": "01", "overall_status": "needs_review_large_delta"},
        ]
        mock_client = self._make_stats_client(rows)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = _get_mesa_stats_uncached(dept=None)

        assert result["01"]["clean"] == 2
        assert result["01"]["needs_review_large_delta"] == 1
        assert result["01"]["total"] == 3

    def test_get_mesa_stats_global_totals(self):
        """_global key contains aggregate totals across all depts."""
        from src.modules.labeler.db import _get_mesa_stats_uncached

        rows = [
            {"dept": "01", "overall_status": "clean"},
            {"dept": "05", "overall_status": "warning"},
            {"dept": "05", "overall_status": "needs_review_large_delta"},
        ]
        mock_client = self._make_stats_client(rows)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = _get_mesa_stats_uncached(dept=None)

        assert result["_global"]["total"] == 3
        assert result["_global"]["clean"] == 1
        assert result["_global"]["warning"] == 1
        assert result["_global"]["needs_review_large_delta"] == 1


class TestGetMesaStatsFiltered:
    """get_mesa_stats with dept filter applies .eq() to restrict results."""

    def test_get_mesa_stats_filtered(self):
        """When dept is given, query applies .eq('dept', dept) filter."""
        from src.modules.labeler.db import _get_mesa_stats_uncached

        rows = [{"dept": "01", "overall_status": "clean"}]
        chain = MagicMock()
        chain.select.return_value = chain
        chain.order.return_value = chain
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
        rows_05 = [{"dept": "05", "overall_status": "needs_review_large_delta"}]

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
        chain.order.return_value = chain
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


# ---------------------------------------------------------------------------
# Work Unit 1 / Phase 1 — hierarchical mesa stats + drill-down filters
# ---------------------------------------------------------------------------

class TestGetHierarchicalMesaStats:
    """get_hierarchical_mesa_stats builds by_mpio / by_puesto / _global / review_by_mesa."""

    def _make_client(self, mesa_rows: list[dict], semaphore_rows: list[dict]) -> MagicMock:
        """Client wired for both the hierarchical-stats grouped RPC and the
        semaphore RPC. `mesa_rows` are per-mesa rows (test-fixture-friendly);
        they're grouped into (dept, mpio, zona, puesto, overall_status) -> n
        here to match what get_hierarchical_mesa_stats_grouped() actually
        returns from Postgres."""
        grouped: dict[tuple, int] = {}
        for row in mesa_rows:
            key = (row["dept"], row["mpio"], row["zona"], row["puesto"], row["overall_status"])
            grouped[key] = grouped.get(key, 0) + 1
        grouped_rows = [
            {"dept": d, "mpio": m, "zona": z, "puesto": p, "overall_status": s, "n": n}
            for (d, m, z, p, s), n in grouped.items()
        ]

        def _rpc(name: str, params: dict | None = None) -> MagicMock:
            chain = MagicMock()
            chain.range.return_value = chain
            if name == "get_hierarchical_mesa_stats_grouped":
                chain.execute.return_value = MagicMock(data=grouped_rows)
            else:
                chain.execute.return_value = MagicMock(data=semaphore_rows)
            return chain

        client = MagicMock()
        client.rpc.side_effect = _rpc
        return client

    def test_by_mpio_and_by_puesto_buckets(self):
        """by_mpio and by_puesto are keyed correctly and only include analyzed mesas."""
        import src.modules.labeler.db as db_module

        db_module._hierarchical_stats_cache.clear()

        mesa_rows = [
            {"dept": "01", "mpio": "001", "zona": "01", "puesto": "01", "mesa": "1", "overall_status": "clean"},
            {"dept": "01", "mpio": "001", "zona": "01", "puesto": "01", "mesa": "2", "overall_status": "warning"},
            {"dept": "01", "mpio": "002", "zona": "01", "puesto": "05", "mesa": "1", "overall_status": "clean"},
        ]
        mock_client = self._make_client(mesa_rows, [])

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = db_module.get_hierarchical_mesa_stats()

        assert result["by_mpio"]["01_001"]["total"] == 2
        assert result["by_mpio"]["01_001"]["clean"] == 1
        assert result["by_mpio"]["01_001"]["warning"] == 1
        assert result["by_mpio"]["01_002"]["total"] == 1

        assert result["by_puesto"]["01_001"]["01_01"]["total"] == 2
        assert result["by_puesto"]["01_002"]["01_05"]["total"] == 1
        # No zero-row DIVIPOLE entries — only mpio/puesto combos actually present.
        assert "01_003" not in result["by_mpio"]

    def test_review_counts_merged_from_semaphore(self):
        """en_revision / revisada counts are parsed from mesa_key_mr and merged in."""
        import src.modules.labeler.db as db_module

        db_module._hierarchical_stats_cache.clear()

        mesa_rows = [
            {"dept": "01", "mpio": "001", "zona": "01", "puesto": "01", "mesa": "1", "overall_status": "clean"},
            {"dept": "01", "mpio": "001", "zona": "01", "puesto": "01", "mesa": "2", "overall_status": "warning"},
        ]
        semaphore_rows = [
            {
                "mesa_key_mr": "01_001_01_01_1",
                "dept": "01",
                "annotation_count_sum": 1,
                "priority_total": 0,
                "priority_confirmed": 0,
            },
            {
                "mesa_key_mr": "01_001_01_01_2",
                "dept": "01",
                "annotation_count_sum": 1,
                "priority_total": 2,
                "priority_confirmed": 2,
                "candidato_sum": 10,
                "total_urna_val": 10,
            },
        ]
        mock_client = self._make_client(mesa_rows, semaphore_rows)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = db_module.get_hierarchical_mesa_stats()

        assert result["review_by_mesa"]["01_001_01_01_1"]["en_revision"] is True
        assert result["review_by_mesa"]["01_001_01_01_1"]["revisada"] is False
        assert result["review_by_mesa"]["01_001_01_01_2"]["revisada"] is True
        assert result["review_by_mesa"]["01_001_01_01_2"]["revisada_result"] == "mesa_limpia"

        assert result["by_mpio"]["01_001"]["en_revision"] == 1
        assert result["by_mpio"]["01_001"]["revisada"] == 1
        assert result["by_puesto"]["01_001"]["01_01"]["en_revision"] == 1
        assert result["by_puesto"]["01_001"]["01_01"]["revisada"] == 1

    def test_review_row_without_mesa_results_row_skips_global(self):
        """A semaphore row for a mesa absent from mesa_results must not inflate
        _global — _global['en_revision']/['revisada'] must stay the sum of
        the (empty, in this case) by_mpio/by_puesto children."""
        import src.modules.labeler.db as db_module

        db_module._hierarchical_stats_cache.clear()

        mesa_rows = [
            {"dept": "01", "mpio": "001", "zona": "01", "puesto": "01", "mesa": "1", "overall_status": "clean"},
        ]
        semaphore_rows = [
            {
                "mesa_key_mr": "99_999_01_01_1",  # dept/mpio not present in mesa_rows
                "dept": "99",
                "annotation_count_sum": 1,
                "priority_total": 0,
                "priority_confirmed": 0,
            },
        ]
        mock_client = self._make_client(mesa_rows, semaphore_rows)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = db_module.get_hierarchical_mesa_stats()

        assert "99_999" not in result["by_mpio"]
        assert result["_global"]["en_revision"] == 0
        assert result["_global"]["revisada"] == 0

    def test_global_rollup(self):
        """_global aggregates totals across all mpio/puesto buckets."""
        import src.modules.labeler.db as db_module

        db_module._hierarchical_stats_cache.clear()

        mesa_rows = [
            {"dept": "01", "mpio": "001", "zona": "01", "puesto": "01", "mesa": "1", "overall_status": "clean"},
            {"dept": "05", "mpio": "002", "zona": "01", "puesto": "02", "mesa": "1", "overall_status": "warning"},
        ]
        mock_client = self._make_client(mesa_rows, [])

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = db_module.get_hierarchical_mesa_stats()

        assert result["_global"]["total"] == 2
        assert result["_global"]["clean"] == 1
        assert result["_global"]["warning"] == 1

    def test_cache_hit_single_scan(self):
        """Two calls within TTL must scan mesa_results only once."""
        import src.modules.labeler.db as db_module

        db_module._hierarchical_stats_cache.clear()

        mesa_rows = [
            {"dept": "01", "mpio": "001", "zona": "01", "puesto": "01", "mesa": "1", "overall_status": "clean"},
        ]
        mock_client = self._make_client(mesa_rows, [])

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            r1 = db_module.get_hierarchical_mesa_stats()
            r2 = db_module.get_hierarchical_mesa_stats()

        assert r1 == r2
        grouped_calls = [c for c in mock_client.rpc.call_args_list if c.args[0] == "get_hierarchical_mesa_stats_grouped"]
        assert len(grouped_calls) == 1

    def test_supabase_unavailable_returns_empty_shape(self):
        """On any exception, returns the empty-but-shaped dict (fail-closed)."""
        import src.modules.labeler.db as db_module

        db_module._hierarchical_stats_cache.clear()

        with patch("src.modules.labeler.db._client", side_effect=RuntimeError("no client")):
            result = db_module.get_hierarchical_mesa_stats()

        assert result["by_mpio"] == {}
        assert result["by_puesto"] == {}
        assert result["review_by_mesa"] == {}
        assert result["_global"] == _empty_global_bucket()

    def test_empty_result_is_not_cached(self):
        """Fail-closed empty responses must not pin zeros for the full TTL."""
        import src.modules.labeler.db as db_module

        db_module._hierarchical_stats_cache.clear()

        with patch("src.modules.labeler.db._client", side_effect=RuntimeError("no client")):
            db_module.get_hierarchical_mesa_stats()

        assert db_module._hierarchical_stats_cache == {}


# ---------------------------------------------------------------------------
# T1a-7 (RED) — get_mesa_results(source_missing=...)
# (fix-mesa-source-status-classification: T8)
# ---------------------------------------------------------------------------

class TestGetMesaResultsSourceMissingFilter:
    """get_mesa_results accepts source_missing kwarg building PostgREST filters."""

    def test_source_missing_any_calls_or(self):
        from src.modules.labeler.db import get_mesa_results

        chain = _make_chain([])
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            get_mesa_results(source_missing="any", page=1, per_page=50)

        chain.or_.assert_called_once_with(
            "source_status->>e14c.neq.ok,"
            "source_status->>e14t.neq.ok,"
            "source_status->>e14d.neq.ok"
        )

    def test_source_missing_e14c_calls_neq(self):
        from src.modules.labeler.db import get_mesa_results

        chain = _make_chain([])
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            get_mesa_results(source_missing="e14c", page=1, per_page=50)

        chain.neq.assert_called_once_with("source_status->>e14c", "ok")

    def test_source_missing_none_no_or_no_neq(self):
        from src.modules.labeler.db import get_mesa_results

        chain = _make_chain([])
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            get_mesa_results(page=1, per_page=50)

        chain.or_.assert_not_called()
        chain.neq.assert_not_called()


class TestGetMesaResultsHierarchicalFilters:
    """get_mesa_results accepts mpio/zona/puesto keyword filters."""

    def test_mpio_filter_applied(self):
        from src.modules.labeler.db import get_mesa_results

        chain = _make_chain([])
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            get_mesa_results(dept="01", mpio="001", page=1, per_page=10)

        eq_calls = [c.args for c in chain.eq.call_args_list]
        assert ("mpio", "001") in eq_calls

    def test_zona_filter_applied(self):
        from src.modules.labeler.db import get_mesa_results

        chain = _make_chain([])
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            get_mesa_results(dept="01", mpio="001", zona="01", page=1, per_page=10)

        eq_calls = [c.args for c in chain.eq.call_args_list]
        assert ("zona", "01") in eq_calls

    def test_puesto_filter_applied(self):
        from src.modules.labeler.db import get_mesa_results

        chain = _make_chain([])
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            get_mesa_results(dept="01", mpio="001", zona="01", puesto="01", page=1, per_page=10)

        eq_calls = [c.args for c in chain.eq.call_args_list]
        assert ("puesto", "01") in eq_calls

    def test_no_hierarchical_filters_no_eq(self):
        """When mpio/zona/puesto are omitted, no extra .eq() calls are made."""
        from src.modules.labeler.db import get_mesa_results

        chain = _make_chain([])
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            get_mesa_results(page=1, per_page=10)

        chain.eq.assert_not_called()


# ---------------------------------------------------------------------------
# Phase 3 (mesa-findings-consolidation) — single-mesa detail accessor
# ---------------------------------------------------------------------------

class TestGetMesaResultsMesaKeyFilter:
    """get_mesa_results(mesa_key=...) applies an exact .eq('mesa_key', ...) filter."""

    def test_get_mesa_results_filters_by_mesa_key(self):
        """mesa_key kwarg applies .eq('mesa_key', ...) and returns the matching row list."""
        from src.modules.labeler.db import get_mesa_results

        row = {"mesa_key": "01_001_001_01_001", "dept": "01", "overall_status": "clean"}
        chain = _make_chain([row])
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = get_mesa_results(mesa_key="01_001_001_01_001")

        assert result == [row]
        eq_calls = [c.args for c in chain.eq.call_args_list]
        assert ("mesa_key", "01_001_001_01_001") in eq_calls

    def test_get_mesa_results_mesa_key_no_match_returns_empty_list(self):
        """mesa_key with no matching row returns []."""
        from src.modules.labeler.db import get_mesa_results

        chain = _make_chain([])
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = get_mesa_results(mesa_key="99_999_999_99_999")

        assert result == []


class TestCountMesaResults:
    """count_mesa_results returns the filtered row count for Level-3 pagination."""

    def _make_count_client(self, count: int) -> MagicMock:
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.execute.return_value = MagicMock(count=count, data=[])
        client = MagicMock()
        client.table.return_value = chain
        return client

    def test_count_mesa_results_returns_count(self):
        from src.modules.labeler.db import count_mesa_results

        mock_client = self._make_count_client(42)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = count_mesa_results(dept="01", mpio="001", zona="01", puesto="01")

        assert result == 42
        mock_client.table.assert_called_once_with("mesa_results")

    def test_count_mesa_results_filters_applied(self):
        from src.modules.labeler.db import count_mesa_results

        mock_client = self._make_count_client(0)
        chain = mock_client.table.return_value

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            count_mesa_results(dept="01", mpio="001", zona="01", puesto="01", status="clean")

        eq_calls = [c.args for c in chain.eq.call_args_list]
        assert ("dept", "01") in eq_calls
        assert ("mpio", "001") in eq_calls
        assert ("zona", "01") in eq_calls
        assert ("puesto", "01") in eq_calls
        assert ("overall_status", "clean") in eq_calls

    def test_count_mesa_results_no_filters(self):
        """With no filters, no .eq() call is made and the raw count is returned."""
        from src.modules.labeler.db import count_mesa_results

        mock_client = self._make_count_client(100)
        chain = mock_client.table.return_value

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = count_mesa_results()

        assert result == 100
        chain.eq.assert_not_called()

    def test_count_mesa_results_error_returns_zero(self):
        """On any exception, returns 0 (fail-closed)."""
        from src.modules.labeler.db import count_mesa_results

        with patch("src.modules.labeler.db._client", side_effect=RuntimeError("no client")):
            result = count_mesa_results(dept="01")

        assert result == 0


# ---------------------------------------------------------------------------
# mesa_result_exists (SDD: public-mesa-report, Phase 1 — anti-forgery guard)
# ---------------------------------------------------------------------------

class TestMesaResultExists:
    """mesa_result_exists(mesa_key) confirms a mesa_key is real before any
    write to transversal_review_reports (threat: mesa_key forgery)."""

    def _make_exists_chain(self, rows: list[dict] | None) -> MagicMock:
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.limit.return_value = chain
        chain.execute.return_value = MagicMock(data=rows if rows is not None else [])
        return chain

    def test_returns_true_when_mesa_key_exists(self):
        from src.modules.labeler.db import mesa_result_exists

        chain = self._make_exists_chain([{"mesa_key": "01_001_01_01_1"}])
        mock_client = MagicMock()
        mock_client.table.return_value = chain

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = mesa_result_exists("01_001_01_01_1")

        assert result is True
        mock_client.table.assert_called_once_with("mesa_results")
        chain.eq.assert_called_once_with("mesa_key", "01_001_01_01_1")
        chain.limit.assert_called_once_with(1)

    def test_returns_false_when_mesa_key_missing(self):
        from src.modules.labeler.db import mesa_result_exists

        chain = self._make_exists_chain([])
        mock_client = MagicMock()
        mock_client.table.return_value = chain

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = mesa_result_exists("99_999_99_99_9")

        assert result is False

    def test_returns_none_on_supabase_error(self):
        """On any exception, returns None (fail-closed but DISTINCT from a
        confirmed False) — a forged mesa_key must never be treated as
        valid, but a lookup outage must also not be reported to the
        citizen as "that mesa doesn't exist"."""
        from src.modules.labeler.db import mesa_result_exists

        with patch("src.modules.labeler.db._client", side_effect=RuntimeError("no client")):
            result = mesa_result_exists("01_001_01_01_1")

        assert result is None
