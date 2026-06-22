"""
tests/labeler/test_vuelta_tagging.py — PR-B schema-v2 + db helper contract tests.

Covers:
  - scripts/supabase_schema_v2.sql adds vuelta columns to crops/labels/assignments
  - scripts/supabase_schema_v2.sql contains assign_next_crop_v2 RPC with vuelta filter
  - src.modules.labeler.db exposes vuelta-aware helpers
"""
from __future__ import annotations

from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_V2_PATH = PROJECT_ROOT / "scripts" / "supabase_schema_v2.sql"


class TestSchemaV2:
    @pytest.fixture(scope="class")
    def schema_v2_sql(self):
        """Read the v2 schema file once for the class."""
        if not SCHEMA_V2_PATH.exists():
            pytest.fail(f"Missing schema file: {SCHEMA_V2_PATH}")
        return SCHEMA_V2_PATH.read_text(encoding="utf-8")

    def test_schema_v2_has_vuelta_column_on_all_tables(self, schema_v2_sql: str):
        """Each of crops, labels, assignments must gain a vuelta column."""
        for table in ("crops", "labels", "assignments"):
            assert f"ALTER TABLE {table}" in schema_v2_sql
            assert f"ADD COLUMN vuelta" in schema_v2_sql

    def test_schema_v2_has_assign_next_crop_v2_rpc(self, schema_v2_sql: str):
        """The v2 RPC must exist and filter by p_vuelta."""
        assert "assign_next_crop_v2" in schema_v2_sql
        assert "vuelta = p_vuelta" in schema_v2_sql


class TestDbVueltaHelpers:
    def test_db_helpers_exist_and_accept_vuelta(self):
        """The four vuelta-aware helpers must exist and be callable."""
        from src.modules.labeler import db

        helpers = (
            db.list_crops_by_vuelta,
            db.list_labels_by_vuelta,
            db.list_assignments_by_vuelta,
            db.create_label_with_vuelta,
        )
        for helper in helpers:
            assert callable(helper), f"{helper} is not callable"
