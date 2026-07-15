"""Tests for transversal queue module (T6)."""
from __future__ import annotations

import json
from pathlib import Path

from src.modules.review.exclusions import load_excluded_keys
from src.modules.review.queue import build_queue_page, iter_conflictivas_jsonl

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

    def test_pending_only_filter(self):
        rows = _load_lab_index()
        all_q = build_queue_page(rows, set(), source_available=_all_sources)
        pending_q = build_queue_page(
            rows, set(), pending_only=True, source_available=_all_sources,
        )
        assert pending_q["total"] < all_q["total"]
        assert pending_q["total"] > 0

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