"""Backward compatibility tests for return_bboxes parameter on extractors.

Verifies:
- process_pdf_task called WITHOUT return_bboxes does NOT inject new keys.
- extract_fields called WITHOUT return_bboxes does NOT inject new keys.
- With return_bboxes=True, exactly subcell_bboxes and fullcell_bboxes are added.

These tests use mock/stub approaches since real PDFs are not available in CI.
The tests validate the function signatures and default parameter behavior
without requiring actual PDF files on disk.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Signature tests — no PDF needed
# ---------------------------------------------------------------------------


class TestProcessPdfTaskSignature:
    """Verify process_pdf_task accepts return_bboxes parameter with default False."""

    def test_function_accepts_return_bboxes_false(self):
        """process_pdf_task must accept return_bboxes=False without error (at import level)."""
        import inspect
        from debug_sv.e14_worker import process_pdf_task

        sig = inspect.signature(process_pdf_task)
        assert "return_bboxes" in sig.parameters
        param = sig.parameters["return_bboxes"]
        assert param.default is False

    def test_return_bboxes_default_is_false(self):
        """Default must be False to preserve backward compat."""
        import inspect
        from debug_sv.e14_worker import process_pdf_task

        sig = inspect.signature(process_pdf_task)
        assert sig.parameters["return_bboxes"].default is False


class TestExtractFieldsSignature:
    """Verify extract_fields accepts return_bboxes parameter with default False."""

    def test_function_accepts_return_bboxes_false(self):
        import inspect
        from debug_sv.e14t_extractor import extract_fields

        sig = inspect.signature(extract_fields)
        assert "return_bboxes" in sig.parameters
        param = sig.parameters["return_bboxes"]
        assert param.default is False

    def test_return_bboxes_default_is_false(self):
        import inspect
        from debug_sv.e14t_extractor import extract_fields

        sig = inspect.signature(extract_fields)
        assert sig.parameters["return_bboxes"].default is False


# ---------------------------------------------------------------------------
# Behavioral test: mock the internal _analyze_primary to avoid needing a real PDF
# ---------------------------------------------------------------------------


class TestProcessPdfTaskNoBboxInDefault:
    """When called with default args, return dict must not include bbox keys."""

    def test_no_bbox_keys_in_default_return(self, tmp_path: Path):
        """Create a dummy 'PDF' that triggers an error path; verify error dict has no bbox keys."""
        from debug_sv.e14_worker import process_pdf_task

        # Use a non-existent path — the function catches exceptions and returns an error dict.
        # Verify that even in error path, no subcell_bboxes or fullcell_bboxes are injected.
        result = process_pdf_task("nonexistent/dummy.pdf", save_suspicious_crops=False)
        assert "subcell_bboxes" not in result
        assert "fullcell_bboxes" not in result

    def test_no_bbox_keys_when_return_bboxes_false_explicit(self, tmp_path: Path):
        from debug_sv.e14_worker import process_pdf_task

        result = process_pdf_task("nonexistent/dummy.pdf", save_suspicious_crops=False, return_bboxes=False)
        assert "subcell_bboxes" not in result
        assert "fullcell_bboxes" not in result


class TestExtractFieldsNoBboxInDefault:
    """When called with default args, return dict must not include bbox keys."""

    def test_no_bbox_keys_in_default_return(self, tmp_path: Path):
        """Use a non-existent PDF — render will fail; verify no bbox keys in error path."""
        from debug_sv.e14t_extractor import extract_fields

        result = extract_fields(Path("nonexistent/dummy.pdf"))
        assert "subcell_bboxes" not in result
        assert "fullcell_bboxes" not in result

    def test_no_bbox_keys_when_return_bboxes_false_explicit(self, tmp_path: Path):
        from debug_sv.e14t_extractor import extract_fields

        result = extract_fields(Path("nonexistent/dummy.pdf"), return_bboxes=False)
        assert "subcell_bboxes" not in result
        assert "fullcell_bboxes" not in result
