"""Tests for transversal_review_reports db accessors (Phase 0 port).

TDD cycle: RED -> GREEN -> REFACTOR for insert_transversal_reports and
list_transversal_reports, ported verbatim from origin/production.
All tests use the MagicMock pattern from tests/test_mesa_results_db.py.
No live Supabase connection is required.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_chain(rows: list[dict] | None = None) -> MagicMock:
    """Return a fluent chain mock that ends with .execute() returning rows."""
    chain = MagicMock()
    chain.select.return_value = chain
    chain.insert.return_value = chain
    chain.eq.return_value = chain
    chain.order.return_value = chain
    chain.limit.return_value = chain
    chain.execute.return_value = MagicMock(data=rows if rows is not None else [])
    return chain


def _make_client(chain: MagicMock | None = None) -> MagicMock:
    """Return a mock supabase client wired to the given chain."""
    client = MagicMock()
    if chain is not None:
        client.table.return_value = chain
    return client


# ---------------------------------------------------------------------------
# insert_transversal_reports
# ---------------------------------------------------------------------------

class TestInsertTransversalReports:
    """insert_transversal_reports inserts one or more report entries and
    returns them serialized."""

    def test_insert_single_entry_returns_serialized_row(self):
        from src.modules.labeler.db import insert_transversal_reports

        inserted_row = {
            "id": "row-1",
            "mesa_key": "01_001_01_01_1",
            "source": "e14c",
            "report_type": "otro",
            "notes": "campo dudoso",
            "fields": None,
            "annotator": "user-1",
            "created_at": "2026-01-01T00:00:00Z",
        }
        chain = _make_chain([inserted_row])
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = insert_transversal_reports(
                "01_001_01_01_1",
                [{"source": "e14c", "report_type": "otro", "notes": "campo dudoso"}],
                "user-1",
            )

        assert result == [
            {
                "id": "row-1",
                "source": "e14c",
                "report_type": "otro",
                "notes": "campo dudoso",
                "fields": None,
                "annotator": "user-1",
                "created_at": "2026-01-01T00:00:00Z",
            }
        ]
        mock_client.table.assert_called_once_with("transversal_review_reports")

    def test_insert_multiple_entries(self):
        from src.modules.labeler.db import insert_transversal_reports

        rows = [
            {"id": "row-1", "mesa_key": "01_001_01_01_1", "source": "e14c",
             "report_type": "otro", "notes": "n1", "fields": None,
             "annotator": "user-1", "created_at": "2026-01-01T00:00:00Z"},
            {"id": "row-2", "mesa_key": "01_001_01_01_1", "source": "e14d",
             "report_type": "campos_vacios", "notes": "n2", "fields": None,
             "annotator": "user-1", "created_at": "2026-01-01T00:00:01Z"},
        ]
        chain = _make_chain(rows)
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = insert_transversal_reports(
                "01_001_01_01_1",
                [
                    {"source": "e14c", "report_type": "otro", "notes": "n1"},
                    {"source": "e14d", "report_type": "campos_vacios", "notes": "n2"},
                ],
                "user-1",
            )

        assert len(result) == 2
        assert result[0]["id"] == "row-1"
        assert result[1]["id"] == "row-2"

    def test_insert_rejects_invalid_source(self):
        from src.modules.labeler.db import insert_transversal_reports

        mock_client = _make_client(_make_chain([]))

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = insert_transversal_reports(
                "01_001_01_01_1",
                [{"source": "bogus", "report_type": "otro", "notes": "n1"}],
                "user-1",
            )

        assert result == []
        mock_client.table.assert_not_called()

    def test_insert_rejects_invalid_report_type(self):
        from src.modules.labeler.db import insert_transversal_reports

        mock_client = _make_client(_make_chain([]))

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = insert_transversal_reports(
                "01_001_01_01_1",
                [{"source": "e14c", "report_type": "bogus", "notes": "n1"}],
                "user-1",
            )

        assert result == []
        mock_client.table.assert_not_called()

    def test_insert_rejects_empty_notes(self):
        from src.modules.labeler.db import insert_transversal_reports

        mock_client = _make_client(_make_chain([]))

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = insert_transversal_reports(
                "01_001_01_01_1",
                [{"source": "e14c", "report_type": "otro", "notes": "   "}],
                "user-1",
            )

        assert result == []
        mock_client.table.assert_not_called()

    def test_insert_returns_empty_on_supabase_error(self):
        """On any exception, insert_transversal_reports returns [] (fail-closed)."""
        from src.modules.labeler.db import insert_transversal_reports

        with patch("src.modules.labeler.db._client", side_effect=RuntimeError("no client")):
            result = insert_transversal_reports(
                "01_001_01_01_1",
                [{"source": "e14c", "report_type": "otro", "notes": "n1"}],
                "user-1",
            )

        assert result == []


# ---------------------------------------------------------------------------
# list_transversal_reports
# ---------------------------------------------------------------------------

class TestListTransversalReports:
    """list_transversal_reports returns all reports for a given mesa_key,
    newest first."""

    def test_list_returns_serialized_rows_for_mesa_key(self):
        from src.modules.labeler.db import list_transversal_reports

        rows = [
            {"id": "row-2", "mesa_key": "01_001_01_01_1", "source": "e14c",
             "report_type": "otro", "notes": "n2", "fields": None,
             "annotator": "user-1", "created_at": "2026-01-02T00:00:00Z"},
            {"id": "row-1", "mesa_key": "01_001_01_01_1", "source": "e14c",
             "report_type": "otro", "notes": "n1", "fields": None,
             "annotator": "user-1", "created_at": "2026-01-01T00:00:00Z"},
        ]
        chain = _make_chain(rows)
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = list_transversal_reports("01_001_01_01_1")

        assert [r["id"] for r in result] == ["row-2", "row-1"]
        mock_client.table.assert_called_once_with("transversal_review_reports")
        chain.eq.assert_called_once_with("mesa_key", "01_001_01_01_1")
        chain.order.assert_called_once_with("created_at", desc=True)

    def test_list_returns_empty_list_when_no_rows(self):
        from src.modules.labeler.db import list_transversal_reports

        chain = _make_chain([])
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = list_transversal_reports("01_001_01_01_1")

        assert result == []

    def test_list_returns_empty_on_supabase_error(self):
        """On any exception, list_transversal_reports returns [] (fail-closed)."""
        from src.modules.labeler.db import list_transversal_reports

        with patch("src.modules.labeler.db._client", side_effect=RuntimeError("no client")):
            result = list_transversal_reports("01_001_01_01_1")

        assert result == []


# ---------------------------------------------------------------------------
# list_recent_transversal_reports
# ---------------------------------------------------------------------------

class TestListRecentTransversalReports:
    """list_recent_transversal_reports returns a global, bounded listing of
    transversal_review_reports (all mesas, all sources), newest first —
    mirroring get_mesa_reports(limit=200)'s bounded/fail-closed conventions,
    but pointed at transversal_review_reports instead of mesa_reports_view."""

    def test_returns_rows_desc_by_created_at_bounded_to_limit(self):
        from src.modules.labeler.db import list_recent_transversal_reports

        rows = [
            {"id": "row-2", "mesa_key": "01_001_01_01_2", "source": "e14c",
             "report_type": "otro", "notes": "n2", "fields": None,
             "annotator": "user-2", "created_at": "2026-01-02T00:00:00Z"},
            {"id": "row-1", "mesa_key": "01_001_01_01_1", "source": "e14d",
             "report_type": "campos_vacios", "notes": "n1", "fields": None,
             "annotator": "user-1", "created_at": "2026-01-01T00:00:00Z"},
        ]
        chain = _make_chain(rows)
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = list_recent_transversal_reports(limit=50)

        assert [r["id"] for r in result] == ["row-2", "row-1"]
        assert [r["mesa_key"] for r in result] == ["01_001_01_01_2", "01_001_01_01_1"]
        mock_client.table.assert_called_once_with("transversal_review_reports")
        chain.eq.assert_not_called()
        chain.order.assert_called_once_with("created_at", desc=True)
        chain.limit.assert_called_once_with(50)

    def test_returns_empty_list_when_no_rows(self):
        from src.modules.labeler.db import list_recent_transversal_reports

        chain = _make_chain([])
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = list_recent_transversal_reports()

        assert result == []

    def test_returns_empty_on_supabase_error(self):
        """On any exception, list_recent_transversal_reports returns [] (fail-closed)."""
        from src.modules.labeler.db import list_recent_transversal_reports

        with patch("src.modules.labeler.db._client", side_effect=RuntimeError("no client")):
            result = list_recent_transversal_reports()

        assert result == []

    def test_default_limit_is_200(self):
        from src.modules.labeler.db import list_recent_transversal_reports

        chain = _make_chain([])
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            list_recent_transversal_reports()

        chain.limit.assert_called_once_with(200)
