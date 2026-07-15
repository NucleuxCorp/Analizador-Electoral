"""Tests for alerts module (T3)."""
from __future__ import annotations

import json
from pathlib import Path

from src.modules.review.alerts import build_mesa_alert_package

FIXTURES = Path(__file__).resolve().parent / "fixtures"
LAB_DIR = Path(__file__).resolve().parents[2] / (
    "Laboratorio/analisis_transversal/E14C_conflictivas_pendientes"
)


def _load_index_row(mesa_key: str) -> dict:
    index_path = LAB_DIR / "index.jsonl"
    with open(index_path, encoding="utf-8") as fp:
        for line in fp:
            if line.strip():
                row = json.loads(line)
                if row["mesa_key"] == mesa_key:
                    return row
    raise KeyError(mesa_key)


def _load_alert_expected(mesa_key: str) -> dict:
    alert_path = LAB_DIR / "alert_index.json"
    data = json.loads(alert_path.read_text(encoding="utf-8"))
    return data[mesa_key]


def _all_sources_available(_mesa_key: str, _src: str) -> bool:
    return True


class TestBuildMesaAlertPackage:
    def test_auto_only_mesa_pending_zero(self):
        row = _load_index_row("01_001_001_01_001")
        pkg = build_mesa_alert_package(
            row,
            source_available=_all_sources_available,
        )
        assert pkg["pending_human_count"] == 0
        assert len(pkg["auto"]) == 1
        assert pkg["auto"][0]["class"] == "confirmed_blank"
        assert pkg["human"] == []

    def test_partial_mesa_has_human_tier(self):
        row = _load_index_row("01_001_005_08_005")
        pkg = build_mesa_alert_package(
            row,
            source_available=_all_sources_available,
        )
        assert pkg["pending_human_count"] == 1
        assert len(pkg["human"]) == 1
        assert pkg["human"][0]["field"] == "VOTANTES"
        assert pkg["human"][0]["class"] == "partial"
        assert len(pkg["human"][0]["sources"]) == 3

    def test_confirmed_blank_has_no_decision_buttons_data(self):
        row = _load_index_row("01_001_001_01_001")
        pkg = build_mesa_alert_package(row, source_available=_all_sources_available)
        for alert in pkg["auto"]:
            assert alert["tier"] == "auto"
            assert "decision_key" not in alert
        assert pkg["human"] == []

    def test_matches_lab_alert_index_sample(self):
        for mk in (
            "01_001_001_01_001",
            "01_001_005_08_005",
            "01_001_009_05_002",
        ):
            row = _load_index_row(mk)
            pkg = build_mesa_alert_package(
                row,
                source_available=_all_sources_available,
            )
            expected = _load_alert_expected(mk)
            assert pkg == expected