"""Tests for transversal_review_decisions db accessors (T7)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_chain(rows: list[dict] | None = None) -> MagicMock:
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.in_.return_value = chain
    chain.limit.return_value = chain
    chain.order.return_value = chain
    chain.range.return_value = chain
    chain.upsert.return_value = chain
    chain.insert.return_value = chain
    chain.delete.return_value = chain
    chain.execute.return_value = MagicMock(data=rows if rows is not None else [])
    return chain


def _make_client(chain: MagicMock) -> MagicMock:
    client = MagicMock()
    client.table.return_value = chain
    return client


class TestGetMesaRawData:
    def test_returns_raw_data_dict(self):
        from src.modules.labeler import db

        raw = {"sources": {"e14c": {"status": "ok"}}}
        chain = _make_chain([{"raw_data": raw}])
        with patch("src.modules.labeler.db._client", return_value=_make_client(chain)):
            result = db.get_mesa_raw_data("01_001_001_01_001")
        assert result == raw
        chain.eq.assert_called_with("mesa_key", "01_001_001_01_001")

    def test_returns_none_when_missing(self):
        from src.modules.labeler import db

        chain = _make_chain([])
        with patch("src.modules.labeler.db._client", return_value=_make_client(chain)):
            assert db.get_mesa_raw_data("missing_key") is None


class TestUpsertTransversalDecision:
    def test_upsert_round_trip_shape(self):
        from src.modules.labeler import db

        window_chain = _make_chain([])
        upsert_chain = _make_chain([])
        select_chain = _make_chain([
            {
                "mesa_key": "01_001_001_01_001",
                "field": "VOTANTES",
                "source": "e14c",
                "decision": "accepted",
            },
        ])
        client = MagicMock()
        client.table.side_effect = [window_chain, upsert_chain, select_chain]

        with patch("src.modules.labeler.db._client", return_value=client):
            ok = db.upsert_transversal_decision(
                "01_001_001_01_001", "VOTANTES", "e14c", "accepted", "user-uuid",
            )
            nested = db.get_transversal_decisions("01_001_001_01_001")

        assert ok is True
        assert nested["01_001_001_01_001"]["VOTANTES"]["e14c"] == "accepted"

    def test_rejects_invalid_field(self):
        from src.modules.labeler import db

        with patch("src.modules.labeler.db._client") as mock_client:
            assert db.upsert_transversal_decision(
                "mk", "INVALID", "e14c", "accepted", "uid",
            ) is False
            mock_client.assert_not_called()


class TestTransversalDecisionEditWindow:
    def test_edit_window_open_without_decisions(self):
        from src.modules.labeler import db

        chain = _make_chain([])
        with patch("src.modules.labeler.db._client", return_value=_make_client(chain)):
            window = db.get_transversal_decision_edit_window("01_001_026_08_011")
        assert window["scope"] == "field"
        assert window["decision_count"] == 0
        assert window["fields"]["SUMA_TOTAL"]["editable"] is True
        assert window["fields"]["SUMA_TOTAL"]["first_decision_at"] is None

    def test_window_is_per_field_not_mesa(self):
        """Expired SUMA_TOTAL must not lock a fresh VOTANTES field."""
        from datetime import datetime, timedelta, timezone
        from src.modules.labeler import db

        old = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
        recent = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        rows = [
            {"field": "SUMA_TOTAL", "created_at": old},
            {"field": "SUMA_TOTAL", "created_at": old},
            {"field": "VOTANTES", "created_at": recent},
        ]
        chain = _make_chain(rows)
        with patch("src.modules.labeler.db._client", return_value=_make_client(chain)):
            pkg = db.get_transversal_decision_edit_window("01_001_002_05_020")
        assert pkg["scope"] == "field"
        assert pkg["fields"]["SUMA_TOTAL"]["editable"] is False
        assert pkg["fields"]["VOTANTES"]["editable"] is True
        assert pkg["fields"]["URNA"]["editable"] is True  # no decisions yet

        # Single-field query shape (DB would filter; mock returns only that field's rows)
        with patch(
            "src.modules.labeler.db._client",
            return_value=_make_client(
                _make_chain([{"field": "VOTANTES", "created_at": recent}])
            ),
        ):
            vot = db.get_transversal_decision_edit_window(
                "01_001_002_05_020", field="VOTANTES"
            )
        assert vot["editable"] is True
        assert vot["decision_count"] == 1

    def test_upsert_blocked_only_for_expired_field(self):
        from datetime import datetime, timedelta, timezone
        from src.modules.labeler import db

        old = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
        # First call: window check for SUMA_TOTAL (expired)
        window_chain = _make_chain([{"field": "SUMA_TOTAL", "created_at": old}])
        client = MagicMock()
        client.table.return_value = window_chain
        with patch("src.modules.labeler.db._client", return_value=client):
            ok = db.upsert_transversal_decision(
                "01_001_002_05_020", "SUMA_TOTAL", "e14d", "rejected", "uid",
            )
        assert ok is False

    def test_reopen_blocked_after_window(self):
        from datetime import datetime, timedelta, timezone
        from src.modules.labeler import db

        old = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
        chain = _make_chain([{"field": "SUMA_TOTAL", "created_at": old}])
        client = MagicMock()
        client.table.return_value = chain
        with patch("src.modules.labeler.db._client", return_value=client):
            window = db.get_transversal_decision_edit_window(
                "01_001_026_08_011", field="SUMA_TOTAL"
            )
            ok, err = db.reopen_transversal_decisions(
                "01_001_026_08_011", field="SUMA_TOTAL"
            )
        assert window["editable"] is False
        assert ok is False
        assert err == "edit_window_expired"

    def test_reopen_deletes_within_window(self):
        from datetime import datetime, timedelta, timezone
        from src.modules.labeler import db

        recent = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        select_chain = _make_chain([{"field": "SUMA_TOTAL", "created_at": recent}])
        delete_chain = MagicMock()
        delete_chain.delete.return_value = delete_chain
        delete_chain.eq.return_value = delete_chain
        delete_chain.execute.return_value = MagicMock(data=[])
        client = MagicMock()
        client.table.side_effect = [select_chain, delete_chain]
        with patch("src.modules.labeler.db._client", return_value=client):
            ok, err = db.reopen_transversal_decisions(
                "01_001_026_08_011", field="SUMA_TOTAL"
            )
        assert ok is True
        assert err is None
        delete_chain.delete.assert_called_once()


class TestTransversalDecisionsScope:
    def test_mesa_key_single_uses_eq_filter(self):
        from src.modules.labeler import db

        rows = [
            {
                "mesa_key": "01_001_001_01_001",
                "field": "VOTANTES",
                "source": "e14c",
                "decision": "accepted",
            },
        ]
        chain = _make_chain(rows)
        with patch("src.modules.labeler.db._client", return_value=_make_client(chain)):
            result = db.get_transversal_decisions("01_001_001_01_001")

        assert result["01_001_001_01_001"]["VOTANTES"]["e14c"] == "accepted"
        chain.eq.assert_called_once_with("mesa_key", "01_001_001_01_001")
        chain.in_.assert_not_called()

    def test_mesa_keys_collection_uses_in_filter(self):
        from src.modules.labeler import db

        keys = ["01_001_001_01_001", "13_001_001_03_011"]
        rows = [
            {
                "mesa_key": keys[0],
                "field": "VOTANTES",
                "source": "e14c",
                "decision": "accepted",
            },
            {
                "mesa_key": keys[1],
                "field": "SUMA_TOTAL",
                "source": "e14d",
                "decision": "rejected",
            },
        ]
        chain = _make_chain(rows)
        with patch("src.modules.labeler.db._client", return_value=_make_client(chain)):
            result = db.get_transversal_decisions(mesa_keys=keys)

        assert result[keys[0]]["VOTANTES"]["e14c"] == "accepted"
        assert result[keys[1]]["SUMA_TOTAL"]["e14d"] == "rejected"
        chain.in_.assert_called_once_with("mesa_key", keys)
        chain.eq.assert_not_called()

    def test_mesa_keys_empty_returns_empty_without_query(self):
        from src.modules.labeler import db

        client = MagicMock()
        with patch("src.modules.labeler.db._client", return_value=client):
            assert db.get_transversal_decisions(mesa_keys=[]) == {}
        client.table.assert_not_called()

    def test_no_filter_fetches_all_rows(self):
        from src.modules.labeler import db

        chain = _make_chain([
            {
                "mesa_key": "01_001_001_01_001",
                "field": "VOTANTES",
                "source": "e14c",
                "decision": "accepted",
            },
        ])
        with patch("src.modules.labeler.db._client", return_value=_make_client(chain)):
            result = db.get_transversal_decisions()

        assert "01_001_001_01_001" in result
        chain.eq.assert_not_called()
        chain.in_.assert_not_called()


class TestTransversalDecidedSlots:
    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        from src.modules.labeler import db

        db.clear_transversal_decided_slots_cache()
        yield
        db.clear_transversal_decided_slots_cache()

    def test_decided_slots_reuses_query_within_ttl(self, monkeypatch):
        from src.modules.labeler import db

        rows = [
            {
                "mesa_key": "01_001_001_01_001",
                "field": "VOTANTES",
                "source": "e14c",
                "decision": "accepted",
            },
        ]
        chain = _make_chain(rows)
        client = _make_client(chain)

        fake_now = [1000.0]
        monkeypatch.setattr(db.time, "time", lambda: fake_now[0])

        with patch("src.modules.labeler.db._client", return_value=client):
            first = db.get_transversal_decided_slots()
            second = db.get_transversal_decided_slots()

        assert first == second
        assert client.table.call_count == 1

    def test_decided_slots_refetches_after_ttl(self, monkeypatch):
        from src.modules.labeler import db

        chain = _make_chain([])
        client = _make_client(chain)

        fake_now = [1000.0]
        monkeypatch.setattr(db.time, "time", lambda: fake_now[0])

        with patch("src.modules.labeler.db._client", return_value=client):
            db.get_transversal_decided_slots()
            fake_now[0] += db._PENDING_CACHE_TTL + 1.0
            db.get_transversal_decided_slots()

        assert client.table.call_count == 2

    def test_force_reload_bypasses_decided_slots_cache(self, monkeypatch):
        from src.modules.labeler import db

        chain = _make_chain([])
        client = _make_client(chain)

        fake_now = [1000.0]
        monkeypatch.setattr(db.time, "time", lambda: fake_now[0])

        with patch("src.modules.labeler.db._client", return_value=client):
            db.get_transversal_decided_slots()
            db.get_transversal_decided_slots(force_reload=True)

        assert client.table.call_count == 2


class TestTransversalReports:
    def test_list_reports_filters_by_mesa_key(self):
        from src.modules.labeler import db

        rows = [
            {
                "id": "r1",
                "mesa_key": "01_001_001_01_001",
                "source": "e14c",
                "report_type": "enmienda",
                "notes": "tachon visible",
                "fields": None,
                "annotator": "user-1",
                "created_at": "2026-07-16T00:00:00Z",
            },
        ]
        chain = _make_chain(rows)
        with patch("src.modules.labeler.db._client", return_value=_make_client(chain)):
            result = db.list_transversal_reports("01_001_001_01_001")

        assert len(result) == 1
        assert result[0]["report_type"] == "enmienda"
        chain.eq.assert_called_with("mesa_key", "01_001_001_01_001")

    def test_insert_rejects_invalid_source(self):
        """SDD public-mesa-report: insert_transversal_reports validates
        source/report_type/notes before ever touching the client."""
        from src.modules.labeler import db

        mock_client = _make_client(_make_chain([]))
        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = db.insert_transversal_reports(
                "01_001_001_01_001",
                [{"source": "bogus", "report_type": "otro", "notes": "n1"}],
                "user-1",
            )
        assert result == []
        mock_client.table.assert_not_called()

    def test_insert_rejects_invalid_report_type(self):
        from src.modules.labeler import db

        mock_client = _make_client(_make_chain([]))
        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = db.insert_transversal_reports(
                "01_001_001_01_001",
                [{"source": "e14c", "report_type": "bogus", "notes": "n1"}],
                "user-1",
            )
        assert result == []
        mock_client.table.assert_not_called()

    def test_insert_rejects_empty_notes(self):
        from src.modules.labeler import db

        mock_client = _make_client(_make_chain([]))
        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = db.insert_transversal_reports(
                "01_001_001_01_001",
                [{"source": "e14c", "report_type": "otro", "notes": "   "}],
                "user-1",
            )
        assert result == []
        mock_client.table.assert_not_called()

    def test_insert_multiple_entries(self):
        from src.modules.labeler import db

        rows = [
            {"id": "row-1", "mesa_key": "01_001_001_01_001", "source": "e14c",
             "report_type": "otro", "notes": "n1", "fields": None,
             "annotator": "user-1", "created_at": "2026-01-01T00:00:00Z"},
            {"id": "row-2", "mesa_key": "01_001_001_01_001", "source": "e14d",
             "report_type": "campos_vacios", "notes": "n2", "fields": None,
             "annotator": "user-1", "created_at": "2026-01-01T00:00:01Z"},
        ]
        chain = _make_chain(rows)
        mock_client = _make_client(chain)
        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = db.insert_transversal_reports(
                "01_001_001_01_001",
                [
                    {"source": "e14c", "report_type": "otro", "notes": "n1"},
                    {"source": "e14d", "report_type": "campos_vacios", "notes": "n2"},
                ],
                "user-1",
            )
        assert len(result) == 2

    def test_insert_returns_empty_on_supabase_error(self):
        """On any exception, insert_transversal_reports returns [] (fail-closed)."""
        from src.modules.labeler import db

        with patch("src.modules.labeler.db._client", side_effect=RuntimeError("no client")):
            result = db.insert_transversal_reports(
                "01_001_001_01_001",
                [{"source": "e14c", "report_type": "otro", "notes": "n1"}],
                "user-1",
            )
        assert result == []

    def test_list_returns_empty_on_supabase_error(self):
        """On any exception, list_transversal_reports returns [] (fail-closed)."""
        from src.modules.labeler import db

        with patch("src.modules.labeler.db._client", side_effect=RuntimeError("no client")):
            result = db.list_transversal_reports("01_001_001_01_001")
        assert result == []

    def test_insert_reports_batch(self):
        from src.modules.labeler import db

        inserted = [
            {
                "id": "new-1",
                "mesa_key": "01_001_001_01_001",
                "source": "e14d",
                "report_type": "otro",
                "notes": "nota",
                "fields": None,
                "annotator": "mod-user",
                "created_at": "2026-07-16T01:00:00Z",
            },
        ]
        insert_chain = _make_chain(inserted)
        with patch("src.modules.labeler.db._client", return_value=_make_client(insert_chain)):
            result = db.insert_transversal_reports(
                "01_001_001_01_001",
                [{"source": "e14d", "report_type": "otro", "notes": "nota"}],
                "mod-user",
            )

        assert len(result) == 1
        insert_chain.insert.assert_called_once()

    def test_delete_report_by_owner(self):
        from src.modules.labeler import db

        select_chain = _make_chain([{"id": "r1", "annotator": "owner-1"}])
        delete_chain = MagicMock()
        delete_chain.delete.return_value = delete_chain
        delete_chain.eq.return_value = delete_chain
        delete_chain.execute.return_value = MagicMock(data=[])
        client = MagicMock()
        client.table.side_effect = [select_chain, delete_chain]
        with patch("src.modules.labeler.db._client", return_value=client):
            assert db.delete_transversal_report("r1", "owner-1") is True

    def test_delete_report_forbidden_for_other_user(self):
        from src.modules.labeler import db

        select_chain = _make_chain([{"id": "r1", "annotator": "owner-1"}])
        with patch("src.modules.labeler.db._client", return_value=_make_client(select_chain)):
            assert db.delete_transversal_report("r1", "other-user") is False


class TestExportTransversalDecisions:
    def test_export_envelope_shape(self):
        from src.modules.labeler import db

        decision_chain = _make_chain([
            {
                "mesa_key": "13_001_001_03_011",
                "field": "SUMA_TOTAL",
                "source": "e14c",
                "decision": "accepted",
            },
        ])
        report_chain = _make_chain([
            {
                "id": "rep-1",
                "mesa_key": "13_001_001_03_011",
                "source": "e14c",
                "report_type": "enmienda",
                "notes": "hallazgo",
                "fields": None,
                "annotator": "u1",
                "created_at": "2026-07-16T00:00:00Z",
            },
        ])
        client = MagicMock()
        client.table.side_effect = [decision_chain, report_chain]
        with patch("src.modules.labeler.db._client", return_value=client):
            payload = db.export_transversal_decisions()

        assert "generated" in payload
        assert payload["project"] == "transversal_review_E14C_conflictivas"
        assert payload["decisions"]["13_001_001_03_011"]["SUMA_TOTAL"]["e14c"] == "accepted"
        assert payload["reports"]["13_001_001_03_011"][0]["report_type"] == "enmienda"


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
