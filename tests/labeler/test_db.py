"""
tests/labeler/test_db.py — Unit tests for src/modules/labeler/db.py

Covers:
  - write_label: insert + annotation_count increment
  - evaluate_agreement: agreement path (confirmed), conflict path
  - release_expired_assignments: DELETE call with correct timestamp filter
"""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Shared fixture: mock supabase client wired into db.supabase
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=False)
def mock_supabase_client():
    """
    Replace the module-level `supabase` client in db.py with a MagicMock.
    Yields the mock so tests can set return values on it.
    """
    mock_client = MagicMock()
    with patch("src.modules.labeler.db.supabase", mock_client):
        yield mock_client


# Convenience: fluent builder returns that resolves .execute() cleanly
def _chain(data=None, count=0):
    """Build a mock fluent chain where every method returns the chain itself
    until .execute() is called, which returns a MagicMock with .data and .count."""
    node = MagicMock()
    result = MagicMock()
    result.data = data or []
    result.count = count
    # Make every chained method return the same node, .execute() returns result
    node.table.return_value = node
    node.select.return_value = node
    node.insert.return_value = node
    node.update.return_value = node
    node.delete.return_value = node
    node.upsert.return_value = node
    node.eq.return_value = node
    node.lt.return_value = node
    node.order.return_value = node
    node.single.return_value = node
    node.execute.return_value = result
    return node, result


# ---------------------------------------------------------------------------
# 6.2a  write_label
# ---------------------------------------------------------------------------

class TestWriteLabel:
    def test_inserts_label_row(self, mock_supabase_client):
        """write_label must INSERT one row into the labels table."""
        import src.modules.labeler.db as db

        # Set up: labels.insert().execute() → ok
        # get_crop_details (called inside write_label) → annotation_count = 0
        # crops.update().execute() → ok
        chain, result = _chain(data=[{"crop_id": "abc", "annotation_count": 0}])
        mock_supabase_client.table.return_value = chain

        db.write_label(
            crop_id="abc",
            annotator_id="user-1",
            label_human="5",
            amended=False,
            is_admin=False,
        )

        # Verify INSERT was called with the correct payload
        insert_calls = [
            c for c in mock_supabase_client.table.return_value.insert.call_args_list
        ]
        assert len(insert_calls) >= 1
        inserted_data = insert_calls[0][0][0]
        assert inserted_data["crop_id"] == "abc"
        assert inserted_data["annotator_id"] == "user-1"
        assert inserted_data["label_human"] == "5"
        assert inserted_data["amended"] is False
        assert inserted_data["is_admin_resolution"] is False

    def test_increments_annotation_count(self, mock_supabase_client):
        """write_label must increment crops.annotation_count by 1."""
        import src.modules.labeler.db as db

        # We need to simulate two calls to .table():
        # First: .table("crops").select("*").eq(...).single().execute() → get_crop_details
        # Second: .table("crops").update(...).eq(...).execute() → increment
        # Both: .table("labels").insert(...).execute() → insert label

        # Track calls to .update() to verify increment
        update_mock = MagicMock()
        update_mock.eq.return_value = update_mock
        update_mock.execute.return_value = MagicMock(data=[])

        # get_crop_details returns annotation_count = 1
        single_mock = MagicMock()
        single_mock.execute.return_value = MagicMock(data={"crop_id": "abc", "annotation_count": 1})

        select_mock = MagicMock()
        select_mock.eq.return_value = select_mock
        select_mock.single.return_value = single_mock
        select_mock.execute.return_value = MagicMock(data={"crop_id": "abc", "annotation_count": 1})

        crops_mock = MagicMock()
        crops_mock.select.return_value = select_mock
        crops_mock.update.return_value = update_mock

        labels_mock = MagicMock()
        labels_mock.insert.return_value = MagicMock(execute=MagicMock(return_value=MagicMock(data=[])))

        def _table(name):
            if name == "labels":
                return labels_mock
            return crops_mock

        mock_supabase_client.table.side_effect = _table

        db.write_label(
            crop_id="abc",
            annotator_id="user-2",
            label_human="7",
            amended=True,
            is_admin=False,
        )

        # Verify update was called with annotation_count=2 (1+1)
        update_mock.eq.assert_called()
        update_call_args = crops_mock.update.call_args
        assert update_call_args is not None
        update_payload = update_call_args[0][0]
        assert update_payload["annotation_count"] == 2


# ---------------------------------------------------------------------------
# 6.2b  evaluate_agreement — agreement path
# ---------------------------------------------------------------------------

class TestEvaluateAgreementAgreement:
    def test_sets_status_confirmed_when_labels_match(self, mock_supabase_client):
        """When two labels have the same label_human value, status becomes 'confirmed'."""
        import src.modules.labeler.db as db

        # Two labels with same value "5"
        labels_data = [
            {"label_human": "5", "is_admin_resolution": False, "annotator_id": "u1"},
            {"label_human": "5", "is_admin_resolution": False, "annotator_id": "u2"},
        ]

        update_mock = MagicMock()
        update_mock.eq.return_value = update_mock
        update_mock.execute.return_value = MagicMock(data=[])

        delete_mock = MagicMock()
        delete_mock.eq.return_value = delete_mock
        delete_mock.execute.return_value = MagicMock(data=[])

        labels_select_mock = MagicMock()
        labels_select_mock.eq.return_value = labels_select_mock
        labels_select_mock.order.return_value = labels_select_mock
        labels_select_mock.execute.return_value = MagicMock(data=labels_data)

        labels_mock = MagicMock()
        labels_mock.select.return_value = labels_select_mock

        crops_mock = MagicMock()
        crops_mock.update.return_value = update_mock

        assignments_mock = MagicMock()
        assignments_mock.delete.return_value = delete_mock

        def _table(name):
            if name == "labels":
                return labels_mock
            if name == "crops":
                return crops_mock
            if name == "assignments":
                return assignments_mock
            return MagicMock()

        mock_supabase_client.table.side_effect = _table

        db.evaluate_agreement("abc")

        # Verify crops was updated with status='confirmed' and confirmed_label='5'
        update_payload = crops_mock.update.call_args[0][0]
        assert update_payload["status"] == "confirmed"
        assert update_payload["confirmed_label"] == "5"

    def test_deletes_assignments_on_agreement(self, mock_supabase_client):
        """evaluate_agreement must delete the assignments row after agreement."""
        import src.modules.labeler.db as db

        labels_data = [
            {"label_human": "3", "is_admin_resolution": False, "annotator_id": "u1"},
            {"label_human": "3", "is_admin_resolution": False, "annotator_id": "u2"},
        ]

        delete_mock = MagicMock()
        delete_mock.eq.return_value = delete_mock
        delete_mock.execute.return_value = MagicMock(data=[])

        labels_select = MagicMock()
        labels_select.eq.return_value = labels_select
        labels_select.order.return_value = labels_select
        labels_select.execute.return_value = MagicMock(data=labels_data)

        labels_mock = MagicMock()
        labels_mock.select.return_value = labels_select

        crops_mock = MagicMock()
        update_ch = MagicMock()
        update_ch.eq.return_value = update_ch
        update_ch.execute.return_value = MagicMock()
        crops_mock.update.return_value = update_ch

        assignments_mock = MagicMock()
        assignments_mock.delete.return_value = delete_mock

        def _table(name):
            if name == "labels":
                return labels_mock
            if name == "crops":
                return crops_mock
            if name == "assignments":
                return assignments_mock
            return MagicMock()

        mock_supabase_client.table.side_effect = _table

        db.evaluate_agreement("abc")

        delete_mock.eq.assert_called_with("crop_id", "abc")


# ---------------------------------------------------------------------------
# 6.2c  evaluate_agreement — conflict path
# ---------------------------------------------------------------------------

class TestEvaluateAgreementConflict:
    def test_sets_status_conflict_when_labels_differ(self, mock_supabase_client):
        """When two labels disagree, status becomes 'conflict' with no confirmed_label."""
        import src.modules.labeler.db as db

        labels_data = [
            {"label_human": "4", "is_admin_resolution": False, "annotator_id": "u1"},
            {"label_human": "7", "is_admin_resolution": False, "annotator_id": "u2"},
        ]

        update_mock = MagicMock()
        update_mock.eq.return_value = update_mock
        update_mock.execute.return_value = MagicMock(data=[])

        delete_mock = MagicMock()
        delete_mock.eq.return_value = delete_mock
        delete_mock.execute.return_value = MagicMock(data=[])

        labels_select = MagicMock()
        labels_select.eq.return_value = labels_select
        labels_select.order.return_value = labels_select
        labels_select.execute.return_value = MagicMock(data=labels_data)

        labels_mock = MagicMock()
        labels_mock.select.return_value = labels_select

        crops_mock = MagicMock()
        crops_mock.update.return_value = update_mock

        assignments_mock = MagicMock()
        assignments_mock.delete.return_value = delete_mock

        def _table(name):
            if name == "labels":
                return labels_mock
            if name == "crops":
                return crops_mock
            if name == "assignments":
                return assignments_mock
            return MagicMock()

        mock_supabase_client.table.side_effect = _table

        db.evaluate_agreement("abc")

        update_payload = crops_mock.update.call_args[0][0]
        assert update_payload["status"] == "conflict"
        assert "confirmed_label" not in update_payload


# ---------------------------------------------------------------------------
# 6.2e  assign_next_crop — v2 + fallback to v1
# ---------------------------------------------------------------------------

class TestAssignNextCrop:
    def test_calls_assign_next_crop_v2(self, mock_supabase_client):
        """When the v2 RPC exists, assign_next_crop must use it and pass vuelta."""
        import src.modules.labeler.db as db

        rpc_chain = MagicMock()
        rpc_chain.execute.return_value = MagicMock(data="crop-123")
        mock_supabase_client.rpc.return_value = rpc_chain

        result = db.assign_next_crop("user-1", vuelta="segunda")

        assert result == "crop-123"
        mock_supabase_client.rpc.assert_called_once_with(
            "assign_next_crop_v2",
            {"p_annotator_id": "user-1", "p_vuelta": "segunda"},
        )

    def test_falls_back_to_v1_when_v2_not_found(self, mock_supabase_client):
        """If Supabase reports the v2 RPC is missing, fall back to the v1 RPC."""
        import src.modules.labeler.db as db

        v2_chain = MagicMock()
        v2_chain.execute.side_effect = Exception(
            '404: function "assign_next_crop_v2" not found'
        )

        v1_chain = MagicMock()
        v1_chain.execute.return_value = MagicMock(data="crop-legacy")

        def _rpc(name, params):
            if name == "assign_next_crop_v2":
                return v2_chain
            if name == "assign_next_crop":
                return v1_chain
            raise ValueError(f"unexpected RPC: {name}")

        mock_supabase_client.rpc.side_effect = _rpc

        result = db.assign_next_crop("user-1", vuelta="segunda")

        assert result == "crop-legacy"
        mock_supabase_client.rpc.assert_any_call(
            "assign_next_crop", {"p_annotator_id": "user-1"}
        )

    def test_propagates_other_v2_errors(self, mock_supabase_client):
        """Non-RPC-not-found errors from the v2 call must not be masked."""
        import src.modules.labeler.db as db

        rpc_chain = MagicMock()
        rpc_chain.execute.side_effect = Exception("connection timeout")
        mock_supabase_client.rpc.return_value = rpc_chain

        with pytest.raises(Exception, match="connection timeout"):
            db.assign_next_crop("user-1", vuelta="segunda")


# ---------------------------------------------------------------------------
# 6.2d  release_expired_assignments
# ---------------------------------------------------------------------------

class TestReleaseExpiredAssignments:
    def test_calls_delete_with_lt_on_expires_at(self, mock_supabase_client):
        """release_expired_assignments must call .lt('expires_at', now_iso)."""
        import src.modules.labeler.db as db

        delete_mock = MagicMock()
        delete_mock.lt.return_value = delete_mock
        delete_mock.execute.return_value = MagicMock(data=[])

        assignments_mock = MagicMock()
        assignments_mock.delete.return_value = delete_mock

        mock_supabase_client.table.return_value = assignments_mock

        db.release_expired_assignments()

        # .table("assignments") must have been called
        mock_supabase_client.table.assert_called_with("assignments")
        # .delete() must have been called
        assignments_mock.delete.assert_called_once()
        # .lt("expires_at", ...) must have been called
        delete_mock.lt.assert_called_once()
        lt_args = delete_mock.lt.call_args[0]
        assert lt_args[0] == "expires_at"
        # The second arg must be a non-empty ISO-8601 string
        assert isinstance(lt_args[1], str)
        assert "T" in lt_args[1]  # ISO-8601 timestamp contains 'T'
