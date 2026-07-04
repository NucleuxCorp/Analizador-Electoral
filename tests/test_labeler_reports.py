"""Tests for labeler reporting features (T2, T3, T4, T5).

TDD cycle: RED -> GREEN -> REFACTOR for each task group.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# T2: record_report + get_reports
# ---------------------------------------------------------------------------

class TestRecordReport:
    """T2-RED: record_report inserts correct dict into reports table."""

    def _make_mock_client(self):
        client = MagicMock()
        client.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{"id": 1}])
        return client

    def test_record_report_calls_insert_with_correct_shape(self):
        """record_report must call supabase.table('reports').insert({...}).execute()."""
        from src.modules.labeler.db import record_report
        mock_client = self._make_mock_client()
        with patch("src.modules.labeler.db._client", return_value=mock_client):
            annotator_id = str(uuid.uuid4())
            record_report(
                crop_id="crop-abc",
                pdf_path="/some/path.pdf",
                report_type="enmienda",
                annotator=annotator_id,
                digit_original="9",
                digit_corrected="4",
                digit=None,
                notes="test note",
            )
        mock_client.table.assert_called_once_with("reports")
        insert_call_kwargs = mock_client.table.return_value.insert.call_args[0][0]
        assert insert_call_kwargs["crop_id"] == "crop-abc"
        assert insert_call_kwargs["pdf_path"] == "/some/path.pdf"
        assert insert_call_kwargs["report_type"] == "enmienda"
        assert insert_call_kwargs["digit_original"] == "9"
        assert insert_call_kwargs["digit_corrected"] == "4"
        assert insert_call_kwargs["digit"] is None
        assert insert_call_kwargs["notes"] == "test note"
        # annotator should be a valid UUID string
        uuid.UUID(insert_call_kwargs["annotator"])  # raises ValueError if not valid

    def test_record_report_annotator_uuid_roundtrip(self):
        """record_report casts annotator via str(uuid.UUID(...)) — no ValueError raised."""
        from src.modules.labeler.db import record_report
        mock_client = self._make_mock_client()
        with patch("src.modules.labeler.db._client", return_value=mock_client):
            valid_uuid = "550e8400-e29b-41d4-a716-446655440000"
            # Should not raise
            record_report(
                crop_id="crop-xyz",
                pdf_path=None,
                report_type="otro",
                annotator=valid_uuid,
                notes="observation",
            )
        insert_call_kwargs = mock_client.table.return_value.insert.call_args[0][0]
        assert insert_call_kwargs["annotator"] == valid_uuid


class TestGetReports:
    """T2-RED: get_reports calls correct supabase query chain."""

    def test_get_reports_calls_correct_query_chain(self):
        """get_reports must call .table('reports').select('*').eq(...).order(...).limit(500).execute()."""
        from src.modules.labeler.db import get_reports
        mock_client = MagicMock()
        mock_chain = mock_client.table.return_value
        mock_chain.select.return_value = mock_chain
        mock_chain.eq.return_value = mock_chain
        mock_chain.order.return_value = mock_chain
        mock_chain.limit.return_value = mock_chain
        mock_chain.execute.return_value = MagicMock(data=[{"id": 1}])

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = get_reports()

        mock_client.table.assert_called_once_with("reports")
        mock_chain.select.assert_called_once_with("*")
        mock_chain.order.assert_called_once_with("created_at", desc=True)
        mock_chain.limit.assert_called_once_with(500)
        assert result == [{"id": 1}]

    def test_get_reports_returns_empty_on_exception(self):
        """get_reports returns [] when the client raises — resilience guard."""
        from src.modules.labeler.db import get_reports
        mock_client = MagicMock()
        mock_client.table.side_effect = RuntimeError("network error")

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = get_reports()

        assert result == []


# ---------------------------------------------------------------------------
# T3: retract_recent_marks
# ---------------------------------------------------------------------------

class TestRetractRecentMarks:
    """T3-RED: retract_recent_marks issues three independent DELETEs."""

    def _make_chain(self, mock_client, table_name):
        """Return a chain mock that supports .delete().eq(...).eq(...).gt(...).execute()."""
        chain = MagicMock()
        chain.delete.return_value = chain
        chain.eq.return_value = chain
        chain.gt.return_value = chain
        chain.execute.return_value = MagicMock()
        return chain

    def test_retract_issues_three_deletes(self):
        """retract_recent_marks must DELETE from reports, fraud_marks, and feedback_marks."""
        from src.modules.labeler.db import retract_recent_marks
        mock_client = MagicMock()
        reports_chain = MagicMock()
        reports_chain.delete.return_value = reports_chain
        reports_chain.eq.return_value = reports_chain
        reports_chain.gt.return_value = reports_chain
        reports_chain.execute.return_value = MagicMock()

        fraud_chain = MagicMock()
        fraud_chain.delete.return_value = fraud_chain
        fraud_chain.eq.return_value = fraud_chain
        fraud_chain.gt.return_value = fraud_chain
        fraud_chain.execute.return_value = MagicMock()

        feedback_chain = MagicMock()
        feedback_chain.delete.return_value = feedback_chain
        feedback_chain.eq.return_value = feedback_chain
        feedback_chain.gt.return_value = feedback_chain
        feedback_chain.execute.return_value = MagicMock()

        def table_side_effect(name):
            if name == "reports":
                return reports_chain
            elif name == "fraud_marks":
                return fraud_chain
            elif name == "feedback_marks":
                return feedback_chain
            return MagicMock()

        mock_client.table.side_effect = table_side_effect

        user_id = str(uuid.uuid4())
        with patch("src.modules.labeler.db._client", return_value=mock_client):
            retract_recent_marks(user_id, "crop-123")

        # All three tables should have been accessed
        table_calls = [c[0][0] for c in mock_client.table.call_args_list]
        assert "reports" in table_calls
        assert "fraud_marks" in table_calls
        assert "feedback_marks" in table_calls

    def test_reports_failure_does_not_block_other_deletes(self):
        """A failure in reports DELETE must NOT prevent fraud_marks/feedback_marks DELETEs."""
        from src.modules.labeler.db import retract_recent_marks
        mock_client = MagicMock()

        reports_chain = MagicMock()
        reports_chain.delete.side_effect = RuntimeError("reports error")

        fraud_chain = MagicMock()
        fraud_chain.delete.return_value = fraud_chain
        fraud_chain.eq.return_value = fraud_chain
        fraud_chain.gt.return_value = fraud_chain
        fraud_chain.execute.return_value = MagicMock()

        feedback_chain = MagicMock()
        feedback_chain.delete.return_value = feedback_chain
        feedback_chain.eq.return_value = feedback_chain
        feedback_chain.gt.return_value = feedback_chain
        feedback_chain.execute.return_value = MagicMock()

        def table_side_effect(name):
            if name == "reports":
                return reports_chain
            elif name == "fraud_marks":
                return fraud_chain
            elif name == "feedback_marks":
                return feedback_chain
            return MagicMock()

        mock_client.table.side_effect = table_side_effect
        user_id = str(uuid.uuid4())

        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = retract_recent_marks(user_id, "crop-123")

        # fraud and feedback should still have been called
        fraud_chain.delete.assert_called_once()
        feedback_chain.delete.assert_called_once()
        assert result is None

    def test_all_failures_returns_none_never_raises(self):
        """If all three DELETEs fail, retract_recent_marks returns None and never raises."""
        from src.modules.labeler.db import retract_recent_marks
        mock_client = MagicMock()
        mock_client.table.side_effect = RuntimeError("everything broken")

        user_id = str(uuid.uuid4())
        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = retract_recent_marks(user_id, "crop-456")

        assert result is None


# ---------------------------------------------------------------------------
# T4: VALID_REPORT_GLYPH_RE + _validate_report_payload
# ---------------------------------------------------------------------------

class TestValidReportGlyphRe:
    """T4-RED: VALID_REPORT_GLYPH_RE is importable and matches/rejects canonical glyphs."""

    def test_valid_glyphs_match(self):
        """Single valid glyphs and multi-char sequences must match."""
        from src.modules.labeler.server import VALID_REPORT_GLYPH_RE
        valid = ["0", "9", "*", ".", "-", "+", "o", "O", "/", "//", "///"]
        for v in valid:
            assert VALID_REPORT_GLYPH_RE.match(v), f"Expected {v!r} to match"

    def test_invalid_glyphs_rejected(self):
        """Invalid glyphs must NOT match."""
        from src.modules.labeler.server import VALID_REPORT_GLYPH_RE
        invalid = ["a", "", " ", "X", "10!"]
        for v in invalid:
            assert not VALID_REPORT_GLYPH_RE.match(v), f"Expected {v!r} to NOT match"

    def test_four_slashes_rejected_by_validator(self):
        """////-like overlong slash sequences should be rejected by _validate_report_payload."""
        from src.modules.labeler.server import _validate_report_payload
        payload, err = _validate_report_payload({
            "crop_id": "crop-1",
            "report_type": "otro",
            "digit": "////",
            "notes": "test",
        })
        assert err is not None


class TestValidateReportPayload:
    """T4-RED: _validate_report_payload returns correct errors for bad inputs."""

    def test_missing_crop_id_returns_error(self):
        from src.modules.labeler.server import _validate_report_payload
        _, err = _validate_report_payload({"report_type": "otro", "notes": "hi"})
        assert err is not None
        assert "crop_id" in err.lower()

    def test_invalid_report_type_returns_error(self):
        from src.modules.labeler.server import _validate_report_payload
        _, err = _validate_report_payload({"crop_id": "abc", "report_type": "unknown"})
        assert err is not None

    def test_invalid_glyph_in_digit_original_returns_error(self):
        from src.modules.labeler.server import _validate_report_payload
        _, err = _validate_report_payload({
            "crop_id": "abc",
            "report_type": "enmienda",
            "digit_original": "X",
            "digit_corrected": "4",
        })
        assert err is not None

    def test_missing_notes_for_otro_returns_error(self):
        from src.modules.labeler.server import _validate_report_payload
        _, err = _validate_report_payload({
            "crop_id": "abc",
            "report_type": "otro",
            "notes": "",
        })
        assert err is not None

    def test_missing_digit_original_for_enmienda_returns_error(self):
        from src.modules.labeler.server import _validate_report_payload
        _, err = _validate_report_payload({
            "crop_id": "abc",
            "report_type": "enmienda",
            "digit_original": "",
            "digit_corrected": "4",
        })
        assert err is not None

    def test_valid_enmienda_payload_returns_no_error(self):
        from src.modules.labeler.server import _validate_report_payload
        payload, err = _validate_report_payload({
            "crop_id": "abc",
            "report_type": "enmienda",
            "digit_original": "9",
            "digit_corrected": "4",
        })
        assert err is None
        assert payload["report_type"] == "enmienda"
        assert payload["digit"] is None  # forced None for enmienda

    def test_valid_otro_payload_returns_no_error(self):
        from src.modules.labeler.server import _validate_report_payload
        payload, err = _validate_report_payload({
            "crop_id": "abc",
            "report_type": "otro",
            "digit": "7",
            "notes": "Columna ilegible",
        })
        assert err is None
        assert payload["digit"] == "7"
        # digit_original and digit_corrected should be None for otro
        assert payload["digit_original"] is None
        assert payload["digit_corrected"] is None

    def test_missing_digit_for_otro_returns_error(self):
        from src.modules.labeler.server import _validate_report_payload
        _, err = _validate_report_payload({
            "crop_id": "abc",
            "report_type": "otro",
            "notes": "Columna ilegible",
        })
        assert err is not None
        assert "digit" in err.lower()


# ---------------------------------------------------------------------------
# T5: POST /back calls retract_recent_marks
# ---------------------------------------------------------------------------

class TestBackViewRetraction:
    """T5: back_view_prod calls retract_recent_marks after successful label deletion.

    Full route integration is validated manually (require_auth is a local import
    inside the production block of create_app and cannot be patched at module level).
    These tests verify the db contract and server-module importability.
    """

    def test_retract_recent_marks_is_callable_in_db(self):
        """retract_recent_marks is importable and callable from db module."""
        from src.modules.labeler.db import retract_recent_marks
        assert callable(retract_recent_marks)

    def test_retract_recent_marks_called_from_db_with_correct_args(self):
        """retract_recent_marks accepts (user_id, crop_id) and does not raise on mock."""
        from src.modules.labeler.db import retract_recent_marks
        mock_client = MagicMock()
        chain = MagicMock()
        chain.delete.return_value = chain
        chain.eq.return_value = chain
        chain.gt.return_value = chain
        chain.execute.return_value = MagicMock()
        mock_client.table.return_value = chain

        user_id = str(uuid.uuid4())
        with patch("src.modules.labeler.db._client", return_value=mock_client):
            result = retract_recent_marks(user_id, "crop-789")

        assert result is None  # always returns None

    def test_server_module_imports_retract_recent_marks_from_db(self):
        """server.py imports _db which exposes retract_recent_marks — smoke test."""
        import src.modules.labeler.db as _db
        assert hasattr(_db, "retract_recent_marks")
        assert callable(_db.retract_recent_marks)
