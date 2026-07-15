"""Tests for alerts module (T3)."""
from __future__ import annotations

from src.modules.review.alerts import (
    TRANSVERSAL_REAL_BLANK_ONLY,
    build_mesa_alert_package,
    real_blank_fields,
)


def _all_sources_available(_mesa_key: str, _src: str) -> bool:
    return True


class TestBuildMesaAlertPackage:
    def test_real_blank_only_single_field(self):
        row = {
            "mesa_key": "01_001_001_01_001",
            "blank_fields": ["SUMA_TOTAL"],
            "field_class": {
                "VOTANTES": "has_digits",
                "URNA": "has_digits",
                "SUMA_TOTAL": "confirmed_blank",
            },
            "stored_arrays": {
                "VOTANTES": ["1", "1", "1"],
                "URNA": ["1", "1", "1"],
                "SUMA_TOTAL": [None, None, None],
            },
        }
        pkg = build_mesa_alert_package(row, source_available=_all_sources_available)
        assert TRANSVERSAL_REAL_BLANK_ONLY is True
        assert pkg["pending_human_count"] == 1
        assert pkg["real_blank_count"] == 1
        assert pkg["auto"] == []
        assert len(pkg["human"]) == 1
        assert pkg["human"][0]["field"] == "SUMA_TOTAL"
        assert pkg["human"][0]["tier"] == "blank_confirm"
        assert len(pkg["human"][0]["sources"]) == 3

    def test_partial_suppressed_only_real_blanks_shown(self):
        row = {
            "mesa_key": "01_001_005_08_005",
            "blank_fields": ["SUMA_TOTAL"],
            "field_class": {
                "VOTANTES": "partial",
                "URNA": "has_digits",
                "SUMA_TOTAL": "confirmed_blank",
            },
            "stored_arrays": {
                "VOTANTES": ["3", None, "1"],
                "URNA": ["1", "1", "1"],
                "SUMA_TOTAL": [None, None, None],
            },
        }
        pkg = build_mesa_alert_package(row, source_available=_all_sources_available)
        assert len(pkg["human"]) == 1
        assert pkg["pending_human_count"] == 1
        assert pkg["real_blank_count"] == 1
        assert pkg["human"][0]["field"] == "SUMA_TOTAL"
        assert pkg["auto"] == []

    def test_three_real_blanks(self):
        row = {
            "mesa_key": "x",
            "blank_fields": ["VOTANTES", "URNA", "SUMA_TOTAL"],
            "field_class": {
                "VOTANTES": "confirmed_blank",
                "URNA": "confirmed_blank",
                "SUMA_TOTAL": "confirmed_blank",
            },
            "stored_arrays": {},
        }
        pkg = build_mesa_alert_package(row, source_available=_all_sources_available)
        assert pkg["real_blank_count"] == 3
        assert len(pkg["human"]) == 3
        assert pkg["auto"] == []

    def test_real_blank_fields_helper(self):
        fc = {"VOTANTES": "partial", "URNA": "confirmed_blank", "SUMA_TOTAL": "has_digits"}
        assert real_blank_fields(fc) == ["URNA"]