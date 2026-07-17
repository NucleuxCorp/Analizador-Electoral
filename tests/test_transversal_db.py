"""Tests for transversal_review_reports and transversal_review_decisions db
accessors (Phase 0 + transversal-decisions-port).

TDD cycle: RED -> GREEN -> REFACTOR. insert_transversal_reports and
list_transversal_reports were ported verbatim from origin/production
(Phase 0). The transversal_review_decisions accessors (edit window, reopen,
upsert, nested reads, TTL cache) are ported verbatim from origin/production's
db.py per openspec/changes/transversal-decisions-port/exploration.md.
All tests use the MagicMock pattern from tests/test_mesa_results_db.py.
No live Supabase connection is required.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import src.modules.labeler.db as db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_chain(rows: list[dict] | None = None, count: int | None = None) -> MagicMock:
    """Return a fluent chain mock that ends with .execute() returning rows."""
    chain = MagicMock()
    chain.select.return_value = chain
    chain.insert.return_value = chain
    chain.eq.return_value = chain
    chain.order.return_value = chain
    chain.limit.return_value = chain
    chain.range.return_value = chain
    chain.delete.return_value = chain
    chain.upsert.return_value = chain
    chain.in_.return_value = chain
    chain.gte.return_value = chain
    chain.lte.return_value = chain
    chain.execute.return_value = MagicMock(data=rows if rows is not None else [], count=count)
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
    """list_recent_transversal_reports returns a paginated, global listing of
    transversal_review_reports (all mesas, all sources), newest first —
    mirroring get_mesa_results()'s page/per_page/.range() pagination
    convention (design D7), but pointed at transversal_review_reports
    instead of mesa_results."""

    def test_default_page_1_calls_range_0_49(self):
        """No page arg defaults to page=1, per_page=50 -> .range(0, 49)."""
        from src.modules.labeler.db import list_recent_transversal_reports

        chain = _make_chain([])
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            list_recent_transversal_reports()

        chain.range.assert_called_once_with(0, 49)

    def test_page_2_calls_range_50_99(self):
        """page=2 with per_page=50 must call .range(50, 99)."""
        from src.modules.labeler.db import list_recent_transversal_reports

        chain = _make_chain([])
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            list_recent_transversal_reports(page=2)

        chain.range.assert_called_once_with(50, 99)

    def test_returns_rows_desc_by_created_at(self):
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
            result = list_recent_transversal_reports(page=1, per_page=50)

        assert [r["id"] for r in result] == ["row-2", "row-1"]
        assert [r["mesa_key"] for r in result] == ["01_001_01_01_2", "01_001_01_01_1"]
        mock_client.table.assert_called_once_with("transversal_review_reports")
        chain.eq.assert_not_called()
        chain.order.assert_called_once_with("created_at", desc=True)

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

    def test_page_beyond_total_returns_empty(self):
        """A page number past the total range still returns [] gracefully
        (PostgREST just returns an empty rows list, no exception)."""
        from src.modules.labeler.db import list_recent_transversal_reports

        chain = _make_chain([])
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = list_recent_transversal_reports(page=999, per_page=50)

        assert result == []
        chain.range.assert_called_once_with(49900, 49949)


# ---------------------------------------------------------------------------
# count_recent_transversal_reports
# ---------------------------------------------------------------------------

class TestCountRecentTransversalReports:
    """count_recent_transversal_reports returns the total row count for
    'Reportes de usuarios' tab pagination, mirroring count_mesa_results()'s
    count="exact" convention."""

    def _make_count_client(self, count: int) -> MagicMock:
        chain = MagicMock()
        chain.select.return_value = chain
        chain.execute.return_value = MagicMock(count=count, data=[])
        client = MagicMock()
        client.table.return_value = chain
        return client

    def test_returns_count(self):
        from src.modules.labeler.db import count_recent_transversal_reports

        mock_client = self._make_count_client(123)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = count_recent_transversal_reports()

        assert result == 123
        mock_client.table.assert_called_once_with("transversal_review_reports")

    def test_no_rows_returns_zero(self):
        from src.modules.labeler.db import count_recent_transversal_reports

        mock_client = self._make_count_client(0)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = count_recent_transversal_reports()

        assert result == 0

    def test_error_returns_zero(self):
        """On any exception, returns 0 (fail-closed)."""
        from src.modules.labeler.db import count_recent_transversal_reports

        with patch("src.modules.labeler.db._client", side_effect=RuntimeError("no client")):
            result = count_recent_transversal_reports()

        assert result == 0


# ---------------------------------------------------------------------------
# T4 — mock-chain regression smoke test (extended chain still round-trips)
# ---------------------------------------------------------------------------

def test_extended_chain_still_round_trips_select_execute():
    """Regression guard: extending _make_chain with delete/upsert/in_/gte/lte
    must not break the existing .select().execute() round trip used by the
    reports-port tests above."""
    chain = _make_chain([{"id": "row-1"}])
    mock_client = _make_client(chain)

    with patch("src.modules.labeler.db._client", return_value=mock_client):
        result = db.list_transversal_reports("01_001_01_01_1")

    assert result[0]["id"] == "row-1"


# ---------------------------------------------------------------------------
# T1-T3 — Collection import + constants (transversal_review_decisions port)
# ---------------------------------------------------------------------------

class TestPortedConstantsAndImport:
    def test_fetch_transversal_decision_rows_importable(self):
        """T1: the module must expose _fetch_transversal_decision_rows once
        ported (also confirms the Collection[str] annotation resolves with
        no NameError at import time)."""
        from src.modules.labeler.db import _fetch_transversal_decision_rows

        assert callable(_fetch_transversal_decision_rows)

    def test_transversal_fields_constant(self):
        assert db._TRANSVERSAL_FIELDS == frozenset({"VOTANTES", "URNA", "SUMA_TOTAL"})
        assert isinstance(db._TRANSVERSAL_FIELDS, frozenset)

    def test_transversal_decisions_constant(self):
        assert db._TRANSVERSAL_DECISIONS == frozenset({"accepted", "rejected"})
        assert isinstance(db._TRANSVERSAL_DECISIONS, frozenset)

    def test_edit_hours_constant(self):
        assert db.TRANSVERSAL_DECISION_EDIT_HOURS == 3

    def test_pending_cache_ttl_constant(self):
        assert db._PENDING_CACHE_TTL == 30.0

    def test_decided_slots_cache_none_at_import(self):
        # Reset to guarantee we observe the module-level default, not a
        # value left over from another test in this file.
        db.clear_transversal_decided_slots_cache()
        assert db._DECIDED_SLOTS_CACHE is None

    def test_transversal_fields_not_imported_from_alerts(self):
        """Duplicated locally, not imported from alerts.TOTAL_FIELDS."""
        import inspect

        source = inspect.getsource(db)
        assert "from src.modules.review.alerts import" not in source
        assert "from ..review.alerts import" not in source


# ---------------------------------------------------------------------------
# T5 — tiny private helpers: _parse_utc_ts, _empty_field_window,
# _field_window_from_rows
# ---------------------------------------------------------------------------

class TestParseUtcTs:
    def test_parses_z_suffix(self):
        result = db._parse_utc_ts("2026-01-01T00:00:00Z")
        assert result == datetime(2026, 1, 1, tzinfo=timezone.utc)

    def test_parses_offset_suffix(self):
        result = db._parse_utc_ts("2026-01-01T00:00:00+00:00")
        assert result == datetime(2026, 1, 1, tzinfo=timezone.utc)


class TestEmptyFieldWindow:
    def test_shape(self):
        window = db._empty_field_window()
        assert window == {
            "editable": True,
            "editable_until": None,
            "first_decision_at": None,
            "decision_count": 0,
        }


class TestFieldWindowFromRows:
    def test_empty_rows_returns_empty_window(self):
        now = datetime.now(timezone.utc)
        assert db._field_window_from_rows([], now=now) == db._empty_field_window()

    def test_one_hour_ago_is_editable(self):
        now = datetime.now(timezone.utc)
        created = (now - timedelta(hours=1)).isoformat()
        window = db._field_window_from_rows([{"created_at": created}], now=now)

        assert window["editable"] is True
        assert window["decision_count"] == 1
        expected_until = db._parse_utc_ts(created) + timedelta(hours=3)
        assert window["editable_until"] == expected_until.isoformat()

    def test_four_hours_ago_is_not_editable(self):
        now = datetime.now(timezone.utc)
        created = (now - timedelta(hours=4)).isoformat()
        window = db._field_window_from_rows([{"created_at": created}], now=now)

        assert window["editable"] is False


# ---------------------------------------------------------------------------
# T6 — get_transversal_decision_edit_window
# ---------------------------------------------------------------------------

class TestGetTransversalDecisionEditWindow:
    def test_field_within_window(self):
        now = datetime.now(timezone.utc)
        created = (now - timedelta(hours=1)).isoformat()
        chain = _make_chain([{"field": "VOTANTES", "created_at": created}])
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = db.get_transversal_decision_edit_window("mesa-1")

        assert result["fields"]["VOTANTES"]["editable"] is True
        assert result["fields"]["VOTANTES"]["decision_count"] == 1

    def test_field_past_window(self):
        now = datetime.now(timezone.utc)
        created = (now - timedelta(hours=4)).isoformat()
        chain = _make_chain([{"field": "URNA", "created_at": created}])
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = db.get_transversal_decision_edit_window("mesa-1")

        assert result["fields"]["URNA"]["editable"] is False

    def test_field_with_no_decisions_matches_empty_shape(self):
        chain = _make_chain([])
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = db.get_transversal_decision_edit_window("mesa-1")

        assert result["fields"]["SUMA_TOTAL"] == db._empty_field_window()

    def test_single_field_query_scopes_to_that_field_only(self):
        now = datetime.now(timezone.utc)
        created = (now - timedelta(hours=1)).isoformat()
        chain = _make_chain([{"field": "VOTANTES", "created_at": created}])
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = db.get_transversal_decision_edit_window("mesa-1", field="VOTANTES")

        # Single-field query returns the field window dict directly (legacy
        # single-window shape), not the multi-field "fields" wrapper.
        assert result["decision_count"] == 1
        assert result["editable"] is True
        chain.eq.assert_any_call("field", "VOTANTES")

    def test_supabase_error_returns_empty_window_shape(self):
        with patch("src.modules.labeler.db._client", side_effect=RuntimeError("boom")):
            result = db.get_transversal_decision_edit_window("mesa-1")

        assert result["fields"]["VOTANTES"] == db._empty_field_window()
        assert result["fields"]["URNA"] == db._empty_field_window()
        assert result["fields"]["SUMA_TOTAL"] == db._empty_field_window()
        assert result["decision_count"] == 0

    def test_supabase_error_with_field_scope_returns_empty_field_window(self):
        with patch("src.modules.labeler.db._client", side_effect=RuntimeError("boom")):
            result = db.get_transversal_decision_edit_window("mesa-1", field="VOTANTES")

        assert result == db._empty_field_window()


# ---------------------------------------------------------------------------
# T7 — reopen_transversal_decisions
# ---------------------------------------------------------------------------

class TestReopenTransversalDecisions:
    def test_reopen_within_window_succeeds(self):
        now = datetime.now(timezone.utc)
        created = (now - timedelta(minutes=30)).isoformat()
        chain = _make_chain([{"field": "VOTANTES", "created_at": created}])
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            ok, err = db.reopen_transversal_decisions("mesa-1", field="VOTANTES")

        assert (ok, err) == (True, None)
        chain.delete.assert_called_once()

    def test_reopen_after_window_expires_is_rejected(self):
        now = datetime.now(timezone.utc)
        created = (now - timedelta(hours=4)).isoformat()
        chain = _make_chain([{"field": "URNA", "created_at": created}])
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            ok, err = db.reopen_transversal_decisions("mesa-1", field="URNA")

        assert (ok, err) == (False, "edit_window_expired")
        chain.delete.assert_not_called()

    def test_reopen_with_no_decisions_is_rejected(self):
        chain = _make_chain([])
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            ok, err = db.reopen_transversal_decisions("mesa-1", field="SUMA_TOTAL")

        assert (ok, err) == (False, "no_decisions")

    def test_unscoped_reopen_affects_only_still_open_fields(self):
        now = datetime.now(timezone.utc)
        open_created = (now - timedelta(minutes=30)).isoformat()
        expired_created = (now - timedelta(hours=4)).isoformat()
        rows = [
            {"field": "VOTANTES", "created_at": open_created},
            {"field": "URNA", "created_at": expired_created},
        ]
        chain = _make_chain(rows)
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            ok, err = db.reopen_transversal_decisions("mesa-1")

        assert (ok, err) == (True, None)
        # Only one delete round-trip (for VOTANTES); URNA is untouched.
        chain.delete.assert_called_once()
        chain.eq.assert_any_call("field", "VOTANTES")
        for call in chain.eq.call_args_list:
            assert call != (("field", "URNA"),)

    def test_supabase_error_during_delete_fails_closed(self):
        now = datetime.now(timezone.utc)
        created = (now - timedelta(minutes=30)).isoformat()

        query_chain = _make_chain([{"field": "VOTANTES", "created_at": created}])
        delete_chain = MagicMock()
        delete_chain.eq.return_value = delete_chain
        delete_chain.execute.side_effect = RuntimeError("boom")

        table_mock = MagicMock()
        table_mock.select.return_value = query_chain
        table_mock.delete.return_value = delete_chain
        # Route select()... via query_chain, delete()... via delete_chain.
        client = MagicMock()

        def _table(name):
            m = MagicMock()
            m.select = query_chain.select
            m.delete = MagicMock(return_value=delete_chain)
            return m

        client.table.side_effect = _table

        with patch("src.modules.labeler.db._client", return_value=client):
            ok, err = db.reopen_transversal_decisions("mesa-1", field="VOTANTES")

        assert ok is False
        assert err == "db_error"


# ---------------------------------------------------------------------------
# T8 — upsert_transversal_decision
# ---------------------------------------------------------------------------

class TestUpsertTransversalDecision:
    def test_invalid_field_rejected(self):
        mock_client = _make_client(_make_chain([]))

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = db.upsert_transversal_decision(
                "mesa-1", "NOT_A_FIELD", "e14c", "accepted", "user-1"
            )

        assert result is False
        mock_client.table.assert_not_called()

    def test_invalid_source_rejected(self):
        mock_client = _make_client(_make_chain([]))

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = db.upsert_transversal_decision(
                "mesa-1", "VOTANTES", "NOT_A_SOURCE", "accepted", "user-1"
            )

        assert result is False
        mock_client.table.assert_not_called()

    def test_invalid_decision_rejected(self):
        mock_client = _make_client(_make_chain([]))

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = db.upsert_transversal_decision(
                "mesa-1", "VOTANTES", "e14c", "maybe", "user-1"
            )

        assert result is False
        mock_client.table.assert_not_called()

    def test_valid_first_decision_succeeds(self):
        chain = _make_chain([])  # edit-window lookup: no prior rows
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = db.upsert_transversal_decision(
                "mesa-1", "VOTANTES", "e14c", "accepted", "user-1"
            )

        assert result is True
        chain.upsert.assert_called_once()
        _, kwargs = chain.upsert.call_args
        assert kwargs.get("on_conflict") == "mesa_key,field,source"

    def test_valid_update_within_window_succeeds(self):
        now = datetime.now(timezone.utc)
        created = (now - timedelta(hours=1)).isoformat()
        chain = _make_chain([{"field": "URNA", "created_at": created}])
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = db.upsert_transversal_decision(
                "mesa-1", "URNA", "e14c", "rejected", "user-1"
            )

        assert result is True
        chain.upsert.assert_called_once()

    def test_write_rejected_once_window_closed(self):
        now = datetime.now(timezone.utc)
        created = (now - timedelta(hours=4)).isoformat()
        chain = _make_chain([{"field": "SUMA_TOTAL", "created_at": created}])
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = db.upsert_transversal_decision(
                "mesa-1", "SUMA_TOTAL", "e14c", "accepted", "user-1"
            )

        assert result is False
        chain.upsert.assert_not_called()

    def test_supabase_error_during_upsert_fails_closed(self):
        query_chain = _make_chain([])  # window lookup succeeds, empty

        upsert_chain = MagicMock()
        upsert_chain.execute.side_effect = RuntimeError("boom")

        def _table(name):
            m = MagicMock()
            m.select = query_chain.select
            m.upsert = MagicMock(return_value=upsert_chain)
            return m

        client = MagicMock()
        client.table.side_effect = _table

        with patch("src.modules.labeler.db._client", return_value=client):
            result = db.upsert_transversal_decision(
                "mesa-1", "VOTANTES", "e14c", "accepted", "user-1"
            )

        assert result is False


# ---------------------------------------------------------------------------
# T9 — _nest_transversal_rows
# ---------------------------------------------------------------------------

class TestNestTransversalRows:
    def test_nests_by_mesa_field_source(self):
        rows = [
            {"mesa_key": "mesa-1", "field": "VOTANTES", "source": "e14c", "decision": "accepted"},
            {"mesa_key": "mesa-1", "field": "URNA", "source": "e14d", "decision": "rejected"},
            {"mesa_key": "mesa-2", "field": "SUMA_TOTAL", "source": "e14t", "decision": "accepted"},
        ]

        result = db._nest_transversal_rows(rows)

        assert result == {
            "mesa-1": {
                "VOTANTES": {"e14c": "accepted"},
                "URNA": {"e14d": "rejected"},
            },
            "mesa-2": {
                "SUMA_TOTAL": {"e14t": "accepted"},
            },
        }


# ---------------------------------------------------------------------------
# T10 — _fetch_transversal_decision_rows
# ---------------------------------------------------------------------------

class TestFetchTransversalDecisionRows:
    def test_single_mesa_key_scopes_query(self):
        chain = _make_chain([{"mesa_key": "mesa-1"}])
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = db._fetch_transversal_decision_rows("mesa-1")

        assert result == [{"mesa_key": "mesa-1"}]
        chain.eq.assert_called_once_with("mesa_key", "mesa-1")

    def test_mesa_keys_collection_scopes_via_in(self):
        chain = _make_chain([{"mesa_key": "mesa-1"}, {"mesa_key": "mesa-2"}])
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = db._fetch_transversal_decision_rows(mesa_keys=["mesa-1", "mesa-2"])

        assert len(result) == 2
        chain.in_.assert_called_once_with("mesa_key", ["mesa-1", "mesa-2"])

    def test_empty_mesa_keys_collection_returns_empty_without_query(self):
        mock_client = _make_client(_make_chain([]))

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = db._fetch_transversal_decision_rows(mesa_keys=[])

        assert result == []
        mock_client.table.assert_not_called()

    def test_neither_arg_fetches_all_rows_unscoped(self):
        chain = _make_chain([{"mesa_key": "mesa-1"}])
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = db._fetch_transversal_decision_rows()

        assert result == [{"mesa_key": "mesa-1"}]
        chain.eq.assert_not_called()

    def test_supabase_error_propagates_verbatim(self):
        """Ported verbatim from production: _fetch_transversal_decision_rows
        itself has no try/except — the exception propagates to its caller
        (get_transversal_decisions), which is the layer that fails closed to
        {}. This matches origin/production's exact source (confirmed via
        direct read), not a reconstruction."""
        import pytest

        with patch("src.modules.labeler.db._client", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                db._fetch_transversal_decision_rows("mesa-1")


# ---------------------------------------------------------------------------
# T11 — get_transversal_decisions
# ---------------------------------------------------------------------------

class TestGetTransversalDecisions:
    def test_single_mesa_nested_read(self):
        rows = [
            {"mesa_key": "mesa-1", "field": "VOTANTES", "source": "e14c", "decision": "accepted"},
            {"mesa_key": "mesa-1", "field": "URNA", "source": "e14d", "decision": "rejected"},
        ]
        chain = _make_chain(rows)
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = db.get_transversal_decisions("mesa-1")

        assert result == {
            "mesa-1": {
                "VOTANTES": {"e14c": "accepted"},
                "URNA": {"e14d": "rejected"},
            }
        }

    def test_multi_mesa_read_via_mesa_keys(self):
        rows = [
            {"mesa_key": "mesa-1", "field": "VOTANTES", "source": "e14c", "decision": "accepted"},
            {"mesa_key": "mesa-2", "field": "URNA", "source": "e14d", "decision": "rejected"},
        ]
        chain = _make_chain(rows)
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = db.get_transversal_decisions(mesa_keys=["mesa-1", "mesa-2"])

        assert set(result.keys()) == {"mesa-1", "mesa-2"}

    def test_neither_arg_returns_everything(self):
        rows = [
            {"mesa_key": "mesa-1", "field": "VOTANTES", "source": "e14c", "decision": "accepted"},
        ]
        chain = _make_chain(rows)
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = db.get_transversal_decisions()

        assert "mesa-1" in result

    def test_supabase_error_returns_empty_dict(self):
        with patch("src.modules.labeler.db._client", side_effect=RuntimeError("boom")):
            result = db.get_transversal_decisions("mesa-1")

        assert result == {}


# ---------------------------------------------------------------------------
# T12 — get_transversal_decided_slots TTL cache + clear_...cache
# ---------------------------------------------------------------------------

class TestGetTransversalDecidedSlotsCache:
    def setup_method(self):
        db.clear_transversal_decided_slots_cache()

    def teardown_method(self):
        db.clear_transversal_decided_slots_cache()

    def test_first_call_populates_cache_and_fetches(self):
        rows = [{"mesa_key": "mesa-1", "field": "VOTANTES", "source": "e14c", "decision": "accepted"}]
        chain = _make_chain(rows)
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            with patch("src.modules.labeler.db.time.time", return_value=1000.0):
                result = db.get_transversal_decided_slots()

        assert "mesa-1" in result
        assert db._DECIDED_SLOTS_CACHE is not None
        mock_client.table.assert_called_once()

    def test_second_call_within_ttl_returns_cached_no_new_fetch(self):
        rows = [{"mesa_key": "mesa-1", "field": "VOTANTES", "source": "e14c", "decision": "accepted"}]
        chain = _make_chain(rows)
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            with patch("src.modules.labeler.db.time.time", return_value=1000.0):
                db.get_transversal_decided_slots()
            with patch("src.modules.labeler.db.time.time", return_value=1005.0):
                result = db.get_transversal_decided_slots()

        assert "mesa-1" in result
        mock_client.table.assert_called_once()

    def test_call_after_ttl_elapses_refetches(self):
        rows = [{"mesa_key": "mesa-1", "field": "VOTANTES", "source": "e14c", "decision": "accepted"}]
        chain = _make_chain(rows)
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            with patch("src.modules.labeler.db.time.time", return_value=1000.0):
                db.get_transversal_decided_slots()
            with patch("src.modules.labeler.db.time.time", return_value=1031.0):
                db.get_transversal_decided_slots()

        assert mock_client.table.call_count == 2

    def test_force_reload_bypasses_cache_within_ttl(self):
        rows = [{"mesa_key": "mesa-1", "field": "VOTANTES", "source": "e14c", "decision": "accepted"}]
        chain = _make_chain(rows)
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            with patch("src.modules.labeler.db.time.time", return_value=1000.0):
                db.get_transversal_decided_slots()
            with patch("src.modules.labeler.db.time.time", return_value=1002.0):
                db.get_transversal_decided_slots(force_reload=True)

        assert mock_client.table.call_count == 2

    def test_clear_cache_resets_state_and_forces_refetch(self):
        rows = [{"mesa_key": "mesa-1", "field": "VOTANTES", "source": "e14c", "decision": "accepted"}]
        chain = _make_chain(rows)
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            with patch("src.modules.labeler.db.time.time", return_value=1000.0):
                db.get_transversal_decided_slots()

            db.clear_transversal_decided_slots_cache()
            assert db._DECIDED_SLOTS_CACHE is None

            with patch("src.modules.labeler.db.time.time", return_value=1001.0):
                db.get_transversal_decided_slots()

        assert mock_client.table.call_count == 2

    def test_supabase_error_on_ttl_refetch_overwrites_cache_with_empty(self):
        """Verbatim production behavior: get_transversal_decided_slots() has
        NO stale-cache fallback. A Supabase error on a TTL-triggered refetch
        does not preserve the last-known-good cached value — it overwrites
        the cache with the fail-closed {} for the next _PENDING_CACHE_TTL
        window. This is a known, pre-existing risk (not introduced by this
        port) worth surfacing once mesa-findings-consolidation Phase B wires
        a live caller to this function."""
        rows = [{"mesa_key": "mesa-1", "field": "VOTANTES", "source": "e14c", "decision": "accepted"}]
        chain = _make_chain(rows)
        mock_client = _make_client(chain)

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            with patch("src.modules.labeler.db.time.time", return_value=1000.0):
                first = db.get_transversal_decided_slots()

        assert "mesa-1" in first

        with patch("src.modules.labeler.db._client", side_effect=RuntimeError("boom")):
            with patch("src.modules.labeler.db.time.time", return_value=1031.0):
                result = db.get_transversal_decided_slots()

        # get_transversal_decisions fails closed to {} internally, but the
        # TTL-cache layer itself does not special-case that: it overwrites
        # the cache with the fail-closed {} it received (matches verbatim
        # production behavior — there is no additional staleness fallback
        # inside get_transversal_decided_slots beyond what
        # get_transversal_decisions already returns).
        assert result == {}
        assert db._DECIDED_SLOTS_CACHE is not None
        assert db._DECIDED_SLOTS_CACHE[1] == {}

    def test_cold_cache_plus_exception_returns_empty_safe_shape(self):
        with patch("src.modules.labeler.db._client", side_effect=RuntimeError("boom")):
            with patch("src.modules.labeler.db.time.time", return_value=1000.0):
                result = db.get_transversal_decided_slots()

        assert result == {}
