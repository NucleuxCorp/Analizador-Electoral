"""Tests for transversal_review_decisions db accessors (T7)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_chain(rows: list[dict] | None = None) -> MagicMock:
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.limit.return_value = chain
    chain.order.return_value = chain
    chain.range.return_value = chain
    chain.upsert.return_value = chain
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
        assert window["editable"] is True
        assert window["decision_count"] == 0
        assert window["first_decision_at"] is None

    def test_reopen_blocked_after_window(self):
        from datetime import datetime, timedelta, timezone
        from src.modules.labeler import db

        old = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
        chain = _make_chain([{"created_at": old}])
        client = MagicMock()
        client.table.return_value = chain
        with patch("src.modules.labeler.db._client", return_value=client):
            window = db.get_transversal_decision_edit_window("01_001_026_08_011")
            ok, err = db.reopen_transversal_decisions("01_001_026_08_011")
        assert window["editable"] is False
        assert ok is False
        assert err == "edit_window_expired"

    def test_reopen_deletes_within_window(self):
        from datetime import datetime, timedelta, timezone
        from src.modules.labeler import db

        recent = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        select_chain = _make_chain([{"created_at": recent}])
        delete_chain = MagicMock()
        delete_chain.delete.return_value = delete_chain
        delete_chain.eq.return_value = delete_chain
        delete_chain.execute.return_value = MagicMock(data=[])
        client = MagicMock()
        client.table.side_effect = [select_chain, delete_chain]
        with patch("src.modules.labeler.db._client", return_value=client):
            ok, err = db.reopen_transversal_decisions("01_001_026_08_011")
        assert ok is True
        assert err is None
        delete_chain.delete.assert_called_once()


class TestExportTransversalDecisions:
    def test_export_envelope_shape(self):
        from src.modules.labeler import db

        chain = _make_chain([
            {
                "mesa_key": "13_001_001_03_011",
                "field": "SUMA_TOTAL",
                "source": "e14c",
                "decision": "accepted",
            },
        ])
        with patch("src.modules.labeler.db._client", return_value=_make_client(chain)):
            payload = db.export_transversal_decisions()

        assert "generated" in payload
        assert payload["project"] == "transversal_review_E14C_conflictivas"
        assert payload["decisions"]["13_001_001_03_011"]["SUMA_TOTAL"]["e14c"] == "accepted"