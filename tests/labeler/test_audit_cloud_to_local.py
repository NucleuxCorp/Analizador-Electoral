"""PR-C cloud-to-local audit script tests."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.skip(reason="audit_cloud_to_local.py moved to scripts/archive/ — one-time script")


def _client(storage=None, crops=None, labels=None, assignments=None):
    client = MagicMock()
    storage_chain = MagicMock()
    storage_chain.list.return_value = [{"name": n} for n in (storage or [])]
    client.storage.from_.return_value = storage_chain

    def _table(name):
        chain = MagicMock()
        result = MagicMock()
        if name == "crops":
            result.data = crops or []
        elif name == "labels":
            result.data = labels or []
        elif name == "assignments":
            result.data = assignments or []
            result.count = len(assignments or [])
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.execute.return_value = result
        return chain

    client.table.side_effect = _table
    return client


def _write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


@pytest.fixture
def labels_dir(tmp_path: Path):
    d = tmp_path / "labels"
    crops_dir = d / "crops"
    crops_dir.mkdir(parents=True)
    (d / "labels").mkdir(parents=True)
    (crops_dir / "c1.png").write_text("png")
    (crops_dir / "c2.png").write_text("png")
    _write_jsonl(crops_dir / "index.jsonl", [{"crop_id": "c1"}, {"crop_id": "c2"}])
    _write_jsonl(
        d / "labels" / "manifest.jsonl",
        [
            {"crop_id": "c1", "annotator_id": "u1"},
            {"crop_id": "c2", "annotator_id": "u2"},
        ],
    )
    return d


class TestAuditCloudToLocal:
    def test_audit_exits_zero_on_match(self, labels_dir: Path):
        from scripts import audit_cloud_to_local

        client = _client(
            storage=["c1.png", "c2.png"],
            crops=[{"crop_id": "c1"}, {"crop_id": "c2"}],
            labels=[{"crop_id": "c1", "annotator_id": "u1"}, {"crop_id": "c2", "annotator_id": "u2"}],
            assignments=[],
        )
        output = labels_dir / "primera_backup.jsonl"
        with patch("src.modules.labeler.db.supabase", client):
            code = audit_cloud_to_local.main(["--labels-dir", str(labels_dir), "--output", str(output)])
        assert code == 0
        assert not output.exists()

    def test_audit_exits_non_zero_on_mismatch(self, labels_dir: Path):
        from scripts import audit_cloud_to_local

        client = _client(
            storage=["c1.png", "c2.png", "c3.png"],
            crops=[{"crop_id": "c1"}, {"crop_id": "c2"}],
            labels=[{"crop_id": "c1", "annotator_id": "u1"}, {"crop_id": "c2", "annotator_id": "u2"}],
            assignments=[],
        )
        output = labels_dir / "primera_backup.jsonl"
        with patch("src.modules.labeler.db.supabase", client):
            code = audit_cloud_to_local.main(["--labels-dir", str(labels_dir), "--output", str(output)])
        assert code == 1
        assert not output.exists()

    def test_audit_incremental_flag(self, labels_dir: Path):
        from scripts import audit_cloud_to_local

        client = _client(
            storage=["c1.png", "c2.png"],
            crops=[{"crop_id": "c1"}, {"crop_id": "c2"}],
            labels=[{"crop_id": "c1", "annotator_id": "u1"}, {"crop_id": "c2", "annotator_id": "u2"}],
            assignments=[],
        )
        output = labels_dir / "primera_backup.jsonl"
        checkpoint = labels_dir / ".audit_checkpoint"
        with patch("src.modules.labeler.db.supabase", client):
            assert audit_cloud_to_local.main(["--labels-dir", str(labels_dir), "--output", str(output), "--incremental"]) == 0
        assert checkpoint.exists()
        assert json.loads(checkpoint.read_text(encoding="utf-8"))["verified"] == ["storage", "crops", "labels", "assignments"]

        # Second run must skip verified storage even if storage mock is broken.
        client.storage = MagicMock()
        client.storage.from_.side_effect = RuntimeError("should not be called")
        with patch("src.modules.labeler.db.supabase", client):
            assert audit_cloud_to_local.main(["--labels-dir", str(labels_dir), "--output", str(output), "--incremental"]) == 0

    def test_audit_backup_jsonl_format(self, labels_dir: Path):
        from scripts import audit_cloud_to_local

        client = _client(
            storage=["c1.png", "c2.png"],
            crops=[{"crop_id": "c1"}, {"crop_id": "c2"}],
            labels=[
                {"crop_id": "c1", "annotator_id": "u1"},
                {"crop_id": "c2", "annotator_id": "u2"},
                {"crop_id": "c3", "annotator_id": "u3"},
            ],
            assignments=[],
        )
        output = labels_dir / "primera_backup.jsonl"
        with patch("src.modules.labeler.db.supabase", client):
            code = audit_cloud_to_local.main(["--labels-dir", str(labels_dir), "--output", str(output)])
        assert code == 1
        lines = output.read_text(encoding="utf-8").strip().split("\n")
        row = json.loads(lines[0])
        assert set(row.keys()) == {"layer", "kind", "id", "reason"}
        assert row["layer"] == "labels"
        assert row["kind"] == "missing_in_local"
        assert row["id"] == "c3"
