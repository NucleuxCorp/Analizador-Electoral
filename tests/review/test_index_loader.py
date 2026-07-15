"""Tests for production transversal index loading."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from src.modules.review.queue import clear_transversal_index_cache, load_transversal_index_rows

LAB_DIR = Path(__file__).resolve().parents[2] / (
    "Laboratorio/analisis_transversal/E14C_conflictivas_pendientes"
)


class TestLoadTransversalIndex:
    def setup_method(self) -> None:
        clear_transversal_index_cache()

    def test_load_from_lab_index_file(self):
        index_path = LAB_DIR / "index.jsonl"
        meta = json.loads((LAB_DIR / "index_meta.json").read_text(encoding="utf-8"))
        env = {"TRANSVERSAL_INDEX_JSONL": str(index_path), "TRANSVERSAL_DATASET": "E14C_conflictivas"}
        with patch.dict("os.environ", env, clear=False):
            rows = load_transversal_index_rows(force_reload=True)
        assert len(rows) == meta["count"] == 4117