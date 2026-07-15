"""Tests for blank_audit classification."""
from __future__ import annotations

from src.modules.review.blank_audit import classify_mesa, aggregate_audit


def _cross(e14t_v, e14d_v, e14t_s=0, e14d_s=0):
    return {
        "sources": {
            "e14t": {"fields": {"VOTANTES": e14t_v, "URNA": e14t_v, "SUMA_TOTAL": e14t_s}},
            "e14d": {"fields": {"VOTANTES": e14d_v, "URNA": e14d_v, "SUMA_TOTAL": e14d_s}},
        }
    }


class TestClassifyMesa:
    def test_auto_false_positive_votantes(self):
        index = {
            "mesa_key": "01_001_015_01_026",
            "dept": "01", "zona": "015", "puesto": "01", "mesa": "026",
            "candidate_votes": 331,
            "blank_fields": ["VOTANTES", "SUMA_TOTAL"],
            "field_class": {
                "VOTANTES": "confirmed_blank",
                "URNA": "partial",
                "SUMA_TOTAL": "confirmed_blank",
            },
        }
        row = classify_mesa(index, _cross(171, 171, 171, 171))
        assert row["per_field"]["VOTANTES"] == "auto_false_positive"
        assert row["per_field"]["SUMA_TOTAL"] == "auto_false_positive"
        assert row["flags"]["queue_eligible"] is True

    def test_partial_only_excluded(self):
        index = {
            "mesa_key": "01_001_005_08_005",
            "dept": "01", "zona": "005", "puesto": "08", "mesa": "005",
            "candidate_votes": 293,
            "blank_fields": ["SUMA_TOTAL"],
            "field_class": {
                "VOTANTES": "partial",
                "URNA": "has_digits",
                "SUMA_TOTAL": "confirmed_blank",
            },
        }
        row = classify_mesa(index, _cross(25, 225, 0, 0))
        assert row["per_field"]["VOTANTES"] == "partial_digit"
        assert row["human_only_partial"] is True
        assert row["mesa_bucket"] == "excluded_partial_only"
        assert row["flags"]["queue_eligible"] is False

    def test_aggregate_counts(self):
        rows = [
            classify_mesa({
                "mesa_key": "a", "blank_fields": [], "field_class": {
                    "VOTANTES": "missing", "URNA": "has_digits", "SUMA_TOTAL": "has_digits",
                },
            }, None),
            classify_mesa({
                "mesa_key": "b", "blank_fields": ["SUMA_TOTAL"], "field_class": {
                    "VOTANTES": "partial", "URNA": "has_digits", "SUMA_TOTAL": "confirmed_blank",
                },
            }, _cross(0, 0, 0, 0)),
        ]
        summary = aggregate_audit(rows)
        assert summary["mesas_total"] == 2
        assert summary["mesa_flags"]["queue_eligible"] == 1