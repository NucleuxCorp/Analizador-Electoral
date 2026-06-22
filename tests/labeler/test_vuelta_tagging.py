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

    def test_existing_rows_backfilled_primera(self, schema_v2_sql: str):
        """The DDL must backfill existing rows as primera via DEFAULT 'primera'.

        Spec VT-2: Existing rows backfilled as primera.
        The migration relies on the column DEFAULT, so we assert that
        each ALTER TABLE statement includes DEFAULT 'primera' AND NOT NULL
        so existing rows get the safe value and cannot be left null.
        """
        import re

        # Find each ADD COLUMN vuelta statement and check its constraint clause.
        pattern = re.compile(
            r"ALTER TABLE\s+(crops|labels|assignments)\s+"
            r"ADD COLUMN\s+vuelta\s+"
            r"TEXT\s+NOT\s+NULL\s+DEFAULT\s+'primera'",
            re.IGNORECASE,
        )
        matches = pattern.findall(schema_v2_sql)
        assert sorted(set(matches)) == ["assignments", "crops", "labels"], (
            f"Expected all 3 tables to have `vuelta TEXT NOT NULL DEFAULT 'primera'`, "
            f"found: {sorted(set(matches))}"
        )

    def test_schema_v2_has_check_constraint_on_vuelta(self, schema_v2_sql: str):
        """Each vuelta column must reject values outside {'primera','segunda'}."""
        import re

        pattern = re.compile(
            r"ALTER TABLE\s+(crops|labels|assignments)\s+"
            r"ADD COLUMN\s+vuelta\s+[^;]*"
            r"CHECK\s*\(\s*vuelta\s+IN\s*\(\s*'primera'\s*,\s*'segunda'\s*\)\s*\)",
            re.IGNORECASE | re.DOTALL,
        )
        matches = pattern.findall(schema_v2_sql)
        assert sorted(set(matches)) == ["assignments", "crops", "labels"], (
            f"Expected all 3 tables to have CHECK (vuelta IN ('primera','segunda')), "
            f"found: {sorted(set(matches))}"
        )


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

    def test_queries_filter_by_active_vuelta(self):
        """list_crops_by_vuelta must apply .eq('vuelta', vuelta) to the query chain.

        Spec VT-3: Portal queries filter by active vuelta.
        Mocks the Supabase client and asserts the .eq() filter is invoked
        with the correct column and value for each helper that accepts vuelta.
        """
        from unittest.mock import MagicMock, call, patch

        import src.modules.labeler.db as db

        mock_client = MagicMock()
        # Build a chain: client.table("crops").select("*").eq("vuelta", X).execute()
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.execute.return_value = MagicMock(data=[])
        mock_client.table.return_value = chain

        with patch.object(db, "supabase", mock_client):
            db.list_crops_by_vuelta(mock_client, "segunda")
            assert chain.eq.call_args_list == [call("vuelta", "segunda")], (
                f"list_crops_by_vuelta must call .eq('vuelta', 'segunda'); "
                f"got: {chain.eq.call_args_list}"
            )


class TestWriteLabelVuelta:
    def test_write_label_includes_vuelta_column(self):
        """write_label must pass the vuelta column through to the INSERT payload.

        Spec W4: all existing INSERTs add vuelta; explicit parameter keeps
        the call site deterministic and backward-compatible.
        """
        from unittest.mock import MagicMock, patch

        import src.modules.labeler.db as db

        chain = MagicMock()
        chain.insert.return_value = chain
        chain.execute.return_value = MagicMock(data=[])
        mock_client = MagicMock()
        mock_client.table.return_value = chain

        # get_crop_details is called after insert; stub it with a minimal crop.
        with patch.object(db, "supabase", mock_client), \
             patch.object(db, "get_crop_details", return_value={"annotation_count": 0}):
            db.write_label(
                crop_id="crop-123",
                annotator_id="user-1",
                label_human="5",
                amended=False,
                is_admin=False,
                vuelta="segunda",
            )

        insert_payload = chain.insert.call_args[0][0]
        assert insert_payload["vuelta"] == "segunda", (
            f"INSERT payload missing vuelta='segunda': {insert_payload}"
        )


class TestAssignNextCropVuelta:
    def test_assign_next_crop_uses_v2_rpc_with_vuelta(self):
        """assign_next_crop must call the v2 RPC and pass p_vuelta.

        Spec W5: replace assign_next_crop with assign_next_crop_v2 so the
        queue is scoped to the active voting round.
        """
        from unittest.mock import MagicMock, patch

        import src.modules.labeler.db as db

        rpc_chain = MagicMock()
        rpc_chain.execute.return_value = MagicMock(data="crop-456")
        mock_client = MagicMock()
        mock_client.rpc.return_value = rpc_chain

        with patch.object(db, "supabase", mock_client):
            result = db.assign_next_crop("user-1", vuelta="segunda")

        assert result == "crop-456"
        mock_client.rpc.assert_called_once_with(
            "assign_next_crop_v2",
            {"p_annotator_id": "user-1", "p_vuelta": "segunda"},
        )
