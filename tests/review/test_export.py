"""Tests for export module (T5)."""
from __future__ import annotations

from src.modules.review.export import build_export_envelope


class TestBuildExportEnvelope:
    def test_shape_matches_spec(self):
        decisions = {
            "13_001_001_03_011": {
                "SUMA_TOTAL": {"e14c": "accepted", "e14d": "rejected"},
            },
        }
        envelope = build_export_envelope(
            decisions,
            dataset="transversal_review_E14C_conflictivas",
            generated="2026-07-14T12:00:00Z",
        )
        assert envelope == {
            "generated": "2026-07-14T12:00:00Z",
            "project": "transversal_review_E14C_conflictivas",
            "decisions": decisions,
        }

    def test_generated_defaults_to_iso8601(self):
        envelope = build_export_envelope({})
        assert "generated" in envelope
        assert envelope["generated"].endswith("Z")
        assert envelope["project"] == "transversal_review_E14C_conflictivas"