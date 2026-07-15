"""Tests for transversal queue module (T6)."""
from __future__ import annotations

import json
from pathlib import Path

from src.modules.review.exclusions import load_excluded_keys
from src.modules.review.queue import _pending_field_count, build_queue_page, iter_conflictivas_jsonl
from src.modules.review.alerts import build_mesa_alert_package

LAB_DIR = Path(
    r"E:\Nucleux\tools\Analizador de Elecciones\Laboratorio\analisis_transversal\E14C_conflictivas_pendientes"
)
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_lab_index() -> list[dict]:
    rows = []
    with open(LAB_DIR / "index.jsonl", encoding="utf-8") as fp:
        for line in fp:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _all_sources(_mk: str, _src: str) -> bool:
    return True


class TestBuildQueuePage:
    def test_pending_count_respects_decisions(self):
        row = {
            "mesa_key": "01_001_001_01_001",
            "dept": "01", "mpio": "001", "zona": "001", "puesto": "01", "mesa": "001",
            "blank_fields": ["SUMA_TOTAL"],
            "field_class": {
                "VOTANTES": "has_digits",
                "URNA": "has_digits",
                "SUMA_TOTAL": "confirmed_blank",
            },
        }
        pkg = build_mesa_alert_package(row, source_available=_all_sources)
        assert _pending_field_count(pkg, {}) == 1
        assert _pending_field_count(pkg, {"SUMA_TOTAL": {"e14c": "accepted", "e14d": "accepted", "e14t": "accepted"}}) == 0

    def test_lab_index_count_4117(self):
        rows = _load_lab_index()
        meta = json.loads((LAB_DIR / "index_meta.json").read_text(encoding="utf-8"))
        assert len(rows) == meta["count"] == 4117

        result = build_queue_page(
            rows,
            set(),
            source_available=_all_sources,
        )
        assert result["total"] == 4117
        assert result["stats"]["mesas"] == 4117

    def test_pending_only_real_blank_mode(self):
        rows = _load_lab_index()
        all_q = build_queue_page(rows, set(), source_available=_all_sources)
        pending_q = build_queue_page(
            rows, set(), pending_only=True, source_available=_all_sources,
        )
        # Real-blank-only mode: no human partials; all conflictivas have ≥1 real blank.
        assert pending_q["total"] == all_q["total"]
        assert all(item.get("real_blank_count", 0) > 0 for item in pending_q["items"])

    def test_queue_skips_rows_without_real_blank(self):
        rows = [
            {
                "mesa_key": "01_001_005_08_005",
                "dept": "01", "mpio": "001", "zona": "005", "puesto": "08", "mesa": "005",
                "blank_fields": [],
                "field_class": {
                    "VOTANTES": "partial",
                    "URNA": "has_digits",
                    "SUMA_TOTAL": "has_digits",
                },
            },
            {
                "mesa_key": "01_001_001_01_001",
                "dept": "01", "mpio": "001", "zona": "001", "puesto": "01", "mesa": "001",
                "blank_fields": ["SUMA_TOTAL"],
                "field_class": {
                    "VOTANTES": "has_digits",
                    "URNA": "has_digits",
                    "SUMA_TOTAL": "confirmed_blank",
                },
            },
        ]
        result = build_queue_page(rows, set(), source_available=_all_sources)
        assert result["total"] == 1
        assert result["items"][0]["mesa_key"] == "01_001_001_01_001"
        assert result["items"][0]["real_blank_count"] == 1

    def test_dept_filter(self):
        rows = _load_lab_index()
        result = build_queue_page(
            rows, set(), dept="01", source_available=_all_sources,
        )
        assert result["total"] > 0
        assert all(item["dept"] == "01" for item in result["items"])

    def test_exclusions_reduce_total_vs_jsonl(self):
        excluded = load_excluded_keys("confirmed", decisions_dir=FIXTURES)
        assert len(excluded) == 28
        rows = list(iter_conflictivas_jsonl())
        # Full JSONL conflictivas includes excluded keys; lab index does not.
        full = build_queue_page(rows, set(), source_available=_all_sources)
        filtered = build_queue_page(rows, excluded, source_available=_all_sources)
        assert filtered["total"] < full["total"]