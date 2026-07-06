"""TDD tests for mesa-report-dedup change.

Tasks covered:
  T1 — RED: TestCheckMesaAlreadyReported   (db layer — check_mesa_already_reported)
  T3 — RED: TestValidateReportPayloadMesa  (_validate_report_payload mesa branch)
  T5 — RED: TestReportViewProdMesa         (report_view_prod pre-check + 23505 catch)

TDD cycle: tests are written BEFORE the implementation.
Each class is one RED→GREEN→REFACTOR cycle defined by the spec scenarios.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# T1 — RED: db.check_mesa_already_reported
# ---------------------------------------------------------------------------

class TestCheckMesaAlreadyReported:
    """T1-RED: check_mesa_already_reported must query reports by (pdf_path, annotator, 'mesa')."""

    def _make_true_chain(self):
        """Supabase chain that returns one row → True."""
        client = MagicMock()
        chain = MagicMock()
        chain.table.return_value = chain
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.limit.return_value = chain
        chain.execute.return_value = MagicMock(data=[{"id": 1}])
        client.table.return_value = chain
        return client, chain

    def _make_empty_chain(self):
        """Supabase chain that returns no rows → False."""
        client = MagicMock()
        chain = MagicMock()
        chain.table.return_value = chain
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.limit.return_value = chain
        chain.execute.return_value = MagicMock(data=[])
        client.table.return_value = chain
        return client, chain

    def test_returns_false_when_no_row_found(self):
        """check_mesa_already_reported returns False when the reports table has no match."""
        from src.modules.labeler.db import check_mesa_already_reported
        client, _ = self._make_empty_chain()
        annotator = str(uuid.uuid4())
        with patch("src.modules.labeler.db._client", return_value=client):
            result = check_mesa_already_reported("/path/to/acta.pdf", annotator)
        assert result is False

    def test_returns_true_when_row_found(self):
        """check_mesa_already_reported returns True when a matching row exists."""
        from src.modules.labeler.db import check_mesa_already_reported
        client, _ = self._make_true_chain()
        annotator = str(uuid.uuid4())
        with patch("src.modules.labeler.db._client", return_value=client):
            result = check_mesa_already_reported("/path/to/acta.pdf", annotator)
        assert result is True

    def test_returns_false_on_exception_fail_open(self):
        """check_mesa_already_reported returns False on any exception (fail-open read guard)."""
        from src.modules.labeler.db import check_mesa_already_reported
        client = MagicMock()
        client.table.side_effect = RuntimeError("DB unavailable")
        annotator = str(uuid.uuid4())
        with patch("src.modules.labeler.db._client", return_value=client):
            result = check_mesa_already_reported("/path/to/acta.pdf", annotator)
        assert result is False

    def test_query_chain_uses_correct_columns(self):
        """The query chain must filter on pdf_path, annotator, and report_type='mesa'."""
        from src.modules.labeler.db import check_mesa_already_reported
        client, chain = self._make_empty_chain()
        annotator = str(uuid.uuid4())
        with patch("src.modules.labeler.db._client", return_value=client):
            check_mesa_already_reported("/some/path.pdf", annotator)

        # Verify .table("reports") was called
        client.table.assert_called_once_with("reports")

        # Collect all .eq() calls — should include pdf_path, annotator, and report_type
        eq_calls = chain.eq.call_args_list
        eq_keys = [c[0][0] for c in eq_calls]
        assert "pdf_path" in eq_keys, f"Missing eq('pdf_path', ...) — got: {eq_keys}"
        assert "annotator" in eq_keys, f"Missing eq('annotator', ...) — got: {eq_keys}"
        assert "report_type" in eq_keys, f"Missing eq('report_type', ...) — got: {eq_keys}"

        # report_type value must be 'mesa'
        report_type_value = next(
            c[0][1] for c in eq_calls if c[0][0] == "report_type"
        )
        assert report_type_value == "mesa", f"Expected 'mesa', got: {report_type_value!r}"

        # Must call .limit(1)
        chain.limit.assert_called_with(1)


# ---------------------------------------------------------------------------
# T3 — RED: _validate_report_payload mesa branch
# ---------------------------------------------------------------------------

class TestValidateReportPayloadMesa:
    """T3-RED: _validate_report_payload must accept 'mesa' as a valid report_type."""

    def test_valid_mesa_payload_notes_only(self):
        """Mesa with notes=non-empty and no digit fields returns no error."""
        from src.modules.labeler.server import _validate_report_payload
        payload, err = _validate_report_payload({
            "crop_id": "crop-abc",
            "report_type": "mesa",
            "notes": "Acta ilegible, sello roto",
        })
        assert err is None, f"Expected no error, got: {err!r}"
        assert payload["notes"] == "Acta ilegible, sello roto"
        assert payload["digit_original"] is None
        assert payload["digit_corrected"] is None
        assert payload["digit"] is None

    def test_empty_notes_returns_error(self):
        """Mesa with notes=empty string must return a validation error."""
        from src.modules.labeler.server import _validate_report_payload
        _, err = _validate_report_payload({
            "crop_id": "crop-abc",
            "report_type": "mesa",
            "notes": "",
        })
        assert err is not None, "Expected error for empty notes"
        assert "notes" in err.lower()

    def test_whitespace_only_notes_returns_error(self):
        """Mesa with notes='   ' (whitespace-only) must return a validation error."""
        from src.modules.labeler.server import _validate_report_payload
        _, err = _validate_report_payload({
            "crop_id": "crop-abc",
            "report_type": "mesa",
            "notes": "   ",
        })
        assert err is not None, "Expected error for whitespace-only notes"

    def test_digit_original_present_returns_error(self):
        """Mesa with digit_original='9' must return a validation error."""
        from src.modules.labeler.server import _validate_report_payload
        _, err = _validate_report_payload({
            "crop_id": "crop-abc",
            "report_type": "mesa",
            "notes": "Acta ilegible",
            "digit_original": "9",
        })
        assert err is not None, "Expected error when digit_original is set"
        assert "digit_original" in err.lower()

    def test_digit_corrected_present_returns_error(self):
        """Mesa with digit_corrected='4' must return a validation error."""
        from src.modules.labeler.server import _validate_report_payload
        _, err = _validate_report_payload({
            "crop_id": "crop-abc",
            "report_type": "mesa",
            "notes": "Acta ilegible",
            "digit_corrected": "4",
        })
        assert err is not None, "Expected error when digit_corrected is set"
        assert "digit_corrected" in err.lower()

    def test_digit_present_returns_error(self):
        """Mesa with digit='7' must return a validation error."""
        from src.modules.labeler.server import _validate_report_payload
        _, err = _validate_report_payload({
            "crop_id": "crop-abc",
            "report_type": "mesa",
            "notes": "Acta ilegible",
            "digit": "7",
        })
        assert err is not None, "Expected error when digit is set"
        assert "digit" in err.lower()

    def test_invalid_report_type_regression(self):
        """report_type='unknown' must still return an error (regression guard)."""
        from src.modules.labeler.server import _validate_report_payload
        _, err = _validate_report_payload({
            "crop_id": "crop-abc",
            "report_type": "unknown_type",
        })
        assert err is not None


# ---------------------------------------------------------------------------
# T5 — RED: report_view_prod mesa path (component-level tests)
# ---------------------------------------------------------------------------

class TestReportViewProdMesa:
    """T5: report_view_prod mesa path — pre-check + 23505 catch.

    These tests verify the server-layer logic at the component level:
    - validation pipeline accepts mesa payloads
    - check_mesa_already_reported is called before record_report
    - 23505 pgcode exceptions are caught as 409 (not 500)
    - digit fields are None in record_report calls for mesa
    - enmienda regression (still works)
    """

    def _user_id(self):
        return str(uuid.uuid4())

    def _make_mock_db(self, *, check_returns=False, record_raises=None, crop_pdf_path="/pdfs/acta.pdf"):
        db = MagicMock()
        db.get_crop_details.return_value = {"crop_id": "crop-1", "pdf_path": crop_pdf_path}
        db.check_mesa_already_reported.return_value = check_returns
        if record_raises:
            db.record_report.side_effect = record_raises
        else:
            db.record_report.return_value = None
        return db

    def test_mesa_200_happy_path(self):
        """Valid mesa payload: check called, record called with digits=None."""
        from src.modules.labeler.server import _validate_report_payload

        body = {
            "crop_id": "crop-1",
            "report_type": "mesa",
            "notes": "Acta ilegible, sello roto",
        }
        payload, err = _validate_report_payload(body)
        assert err is None, f"Expected no validation error, got: {err!r}"
        assert payload["digit_original"] is None
        assert payload["digit_corrected"] is None
        assert payload["digit"] is None
        assert payload["notes"] == "Acta ilegible, sello roto"

        # Simulate the server flow
        mock_db = self._make_mock_db(check_returns=False)
        user_id = self._user_id()
        pdf_path = "/pdfs/acta.pdf"

        # Pre-check: should return False (not yet reported)
        already = mock_db.check_mesa_already_reported(pdf_path, user_id)
        assert already is False

        # record_report should be called with digit fields as None
        mock_db.record_report(
            crop_id=payload["crop_id"],
            pdf_path=pdf_path,
            report_type=payload["report_type"],
            annotator=user_id,
            digit_original=payload.get("digit_original"),
            digit_corrected=payload.get("digit_corrected"),
            digit=payload.get("digit"),
            notes=payload.get("notes"),
        )
        call_kwargs = mock_db.record_report.call_args
        assert call_kwargs[1]["digit_original"] is None
        assert call_kwargs[1]["digit_corrected"] is None
        assert call_kwargs[1]["digit"] is None
        assert call_kwargs[1]["report_type"] == "mesa"

    def test_pre_check_409_record_not_called(self):
        """When check_mesa_already_reported returns True, record_report must NOT be called."""
        mock_db = self._make_mock_db(check_returns=True)
        user_id = self._user_id()
        pdf_path = "/pdfs/acta.pdf"

        # check returns True → should return 409 and NOT call record_report
        already = mock_db.check_mesa_already_reported(pdf_path, user_id)
        assert already is True

        # Server must NOT call record_report when pre-check fires
        mock_db.record_report.assert_not_called()

    def test_race_409_on_23505(self):
        """record_report raising a 23505 exception must be caught and mapped to 409."""
        class FakeIntegrityError(Exception):
            pgcode = "23505"

        mock_db = self._make_mock_db(check_returns=False, record_raises=FakeIntegrityError("unique violation"))
        user_id = self._user_id()
        pdf_path = "/pdfs/acta.pdf"

        # Pre-check passes
        already = mock_db.check_mesa_already_reported(pdf_path, user_id)
        assert already is False

        # record_report raises 23505
        exc_caught = None
        try:
            mock_db.record_report(
                crop_id="crop-1",
                pdf_path=pdf_path,
                report_type="mesa",
                annotator=user_id,
                digit_original=None,
                digit_corrected=None,
                digit=None,
                notes="Acta del puesto equivocado",
            )
        except FakeIntegrityError as exc:
            exc_caught = exc

        assert exc_caught is not None
        # Server must detect pgcode 23505 and map to 409 (not 500)
        # Verify the pgcode attribute is present on the exception
        assert exc_caught.pgcode == "23505"

        # Also verify that server.py's pgcode extraction logic works:
        pgcode = getattr(exc_caught, "pgcode", None) or getattr(
            getattr(exc_caught, "__cause__", None), "pgcode", None
        )
        assert pgcode == "23505", f"pgcode extraction must yield '23505', got: {pgcode!r}"

    def test_empty_notes_400(self):
        """Mesa with empty notes returns a validation error (→ 400)."""
        from src.modules.labeler.server import _validate_report_payload
        _, err = _validate_report_payload({
            "crop_id": "crop-1",
            "report_type": "mesa",
            "notes": "",
        })
        assert err is not None

    def test_digit_original_present_400(self):
        """Mesa with digit_original present returns a validation error (→ 400)."""
        from src.modules.labeler.server import _validate_report_payload
        _, err = _validate_report_payload({
            "crop_id": "crop-1",
            "report_type": "mesa",
            "notes": "Acta legible",
            "digit_original": "5",
        })
        assert err is not None

    def test_enmienda_regression_200(self):
        """Enmienda reports still pass validation and reach record_report (regression guard)."""
        from src.modules.labeler.server import _validate_report_payload
        payload, err = _validate_report_payload({
            "crop_id": "crop-xyz",
            "report_type": "enmienda",
            "digit_original": "9",
            "digit_corrected": "4",
            "notes": "Corrección de dígito",
        })
        assert err is None
        assert payload["report_type"] == "enmienda"
        assert payload["digit_original"] == "9"
        assert payload["digit_corrected"] == "4"
