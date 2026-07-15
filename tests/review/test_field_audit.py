"""Tests for field_audit module (T2)."""
from __future__ import annotations

from src.modules.review.field_audit import (
    build_index_row,
    candidate_votes,
    field_audit,
    is_conflictiva,
    is_e14c_conflictiva,
)


def _base_row(**overrides) -> dict:
    row = {
        "dept": "01",
        "mpio": "001",
        "zona": "001",
        "puesto": "01",
        "mesa": "001",
        "sources": {
            "e14c": {
                "status": "ok",
                "fields": {
                    "C1_CEPEDA": ["1", "0", "0"],
                    "C2_ABELARDO": ["3", "6"],
                    "VOTANTES": ["1", "4", "1"],
                    "URNA": ["1", "4", "1"],
                    "SUMA_TOTAL": [None, None, None],
                },
            }
        },
    }
    row.update(overrides)
    return row


class TestFieldAudit:
    def test_confirmed_blank_class(self):
        audit = field_audit({"SUMA_TOTAL": [None, None, None]})
        assert audit["fields"]["SUMA_TOTAL"]["class"] == "confirmed_blank"
        assert audit["fields"]["SUMA_TOTAL"]["confirmed_blank"] is True

    def test_partial_class(self):
        audit = field_audit({"VOTANTES": ["3", None, "1"]})
        assert audit["fields"]["VOTANTES"]["class"] == "partial"

    def test_has_digits_not_blank_alert(self):
        audit = field_audit({"URNA": ["1", "2", "3"]})
        assert audit["fields"]["URNA"]["class"] == "has_digits"
        assert audit["fields"]["URNA"]["confirmed_blank"] is False


class TestIsE14cConflictiva:
    def test_conflictiva_with_blank_total(self):
        assert is_e14c_conflictiva(_base_row()) is True

    def test_not_conflictiva_zero_candidate_votes(self):
        row = _base_row()
        row["sources"]["e14c"]["fields"]["C1_CEPEDA"] = [None, None, None]
        row["sources"]["e14c"]["fields"]["C2_ABELARDO"] = [None, None, None]
        assert is_e14c_conflictiva(row) is False

    def test_not_conflictiva_e14c_not_ok(self):
        row = _base_row()
        row["sources"]["e14c"]["status"] = "missing"
        assert is_e14c_conflictiva(row) is False


class TestCandidateVotes:
    def test_sums_c1_c2(self):
        fields = {
            "C1_CEPEDA": ["1", "0", "0"],
            "C2_ABELARDO": ["3", "6"],
        }
        assert candidate_votes(fields) == 136


class TestBuildIndexRow:
    def test_builds_slim_row(self):
        row = build_index_row(_base_row())
        assert row is not None
        assert row["mesa_key"] == "01_001_001_01_001"
        assert row["candidate_votes"] == 136
        assert row["field_class"]["SUMA_TOTAL"] == "confirmed_blank"
        assert "SUMA_TOTAL" in row["blank_fields"]

    def test_returns_none_when_not_conflictiva(self):
        row = _base_row()
        row["sources"]["e14c"]["fields"]["SUMA_TOTAL"] = ["1", "3", "6"]
        assert build_index_row(row) is None


def _scalar_row(**overrides) -> dict:
    row = {
        "dept": "01",
        "mpio": "001",
        "zona": "001",
        "puesto": "01",
        "mesa": "002",
        "sources": {
            "e14d": {
                "status": "ok",
                "fields": {
                    "C1_CEPEDA": 100,
                    "C2_ABELARDO": 50,
                    "VOTANTES": 171,
                    "URNA": 141,
                    "SUMA_TOTAL": 0,
                },
            }
        },
    }
    row.update(overrides)
    return row


class TestE14dConflictiva:
    def test_scalar_blank_detected(self):
        audit = field_audit(_scalar_row()["sources"]["e14d"]["fields"], "e14d")
        assert audit["fields"]["SUMA_TOTAL"]["class"] == "confirmed_blank"
        assert "SUMA_TOTAL" in audit["blank_fields"]

    def test_is_conflictiva_e14d(self):
        assert is_conflictiva(_scalar_row(), "e14d") is True

    def test_build_index_row_e14d(self):
        row = build_index_row(_scalar_row(), "e14d")
        assert row is not None
        assert row["primary_source"] == "e14d"
        assert row["candidate_votes"] == 150