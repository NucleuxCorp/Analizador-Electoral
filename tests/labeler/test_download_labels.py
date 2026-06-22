"""PR-D download labels + crops index from Supabase tests."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _table_client(**pages):
    """Return a mock supabase client that yields paged responses per table.

    ``pages`` maps a table name to a list of pages, where each page is a list
    of row dicts.  The same query chain is reused per table so that
    ``table(name).select('*').range(...).limit(...).execute()`` advances
    through pages across multiple pagination calls.
    """
    client = MagicMock()
    chains: dict[str, MagicMock] = {}
    for name, pages_for_table in pages.items():
        chain = MagicMock()
        chain.select.return_value = chain
        chain.range.return_value = chain
        chain.limit.return_value = chain
        chain.execute.side_effect = [MagicMock(data=page) for page in pages_for_table]
        chains[name] = chain

    def _table(name):
        return chains.get(name, MagicMock())

    client.table.side_effect = _table
    return client


def test_download_labels_writes_manifest(tmp_path: Path):
    from scripts.download_labels_to_local import download_labels

    client = _table_client(
        labels=[
            [
                {"id": "l1", "crop_id": "c1", "annotator_id": "u1", "label_human": "5"},
                {"id": "l2", "crop_id": "c2", "annotator_id": "u2", "label_human": "3"},
                {"id": "l3", "crop_id": "c3", "annotator_id": "u1", "label_human": "0"},
            ]
        ]
    )

    count, written = download_labels(client, tmp_path)

    assert count == 3
    assert written == 3
    manifest = tmp_path / "labels" / "manifest.jsonl"
    assert manifest.exists()
    lines = [json.loads(line) for line in manifest.read_text(encoding="utf-8").strip().split("\n")]
    assert len(lines) == 3
    assert {line["crop_id"] for line in lines} == {"c1", "c2", "c3"}
    assert {line["annotator_id"] for line in lines} == {"u1", "u2"}


def test_download_labels_paginates(tmp_path: Path, monkeypatch):
    from scripts import download_labels_to_local as dll

    monkeypatch.setattr(dll, "PAGE", 2)
    client = _table_client(
        labels=[
            [
                {"id": "l1", "crop_id": "c1", "annotator_id": "u1"},
                {"id": "l2", "crop_id": "c2", "annotator_id": "u2"},
            ],
            [{"id": "l3", "crop_id": "c3", "annotator_id": "u1"}],
        ]
    )

    count, written = dll.download_labels(client, tmp_path)

    assert count == 3
    assert written == 3
    manifest = tmp_path / "labels" / "manifest.jsonl"
    lines = [json.loads(line) for line in manifest.read_text(encoding="utf-8").strip().split("\n")]
    assert len(lines) == 3
    assert [line["crop_id"] for line in lines] == ["c1", "c2", "c3"]


def test_download_crops_index(tmp_path: Path):
    from scripts.download_labels_to_local import download_crops_index

    crops_dir = tmp_path / "crops"
    crops_dir.mkdir(parents=True)
    index_path = crops_dir / "index.jsonl"
    index_path.write_text(
        json.dumps({"crop_id": "c1", "label_ocr": "old"}) + "\n"
        + json.dumps({"crop_id": "c3", "label_ocr": "7"}) + "\n"
    )

    client = _table_client(
        crops=[
            [
                {"crop_id": "c1", "label_ocr": "5"},
                {"crop_id": "c2", "label_ocr": "3"},
                {"crop_id": "c1", "label_ocr": "5"},  # duplicate in page
            ]
        ]
    )

    count, written = download_crops_index(client, tmp_path)

    assert count == 3
    assert written == 3
    index = tmp_path / "crops" / "index.jsonl"
    lines = [json.loads(line) for line in index.read_text(encoding="utf-8").strip().split("\n")]
    assert len(lines) == 3
    by_id = {row["crop_id"]: row for row in lines}
    assert [row["crop_id"] for row in lines] == ["c1", "c2", "c3"]
    assert by_id["c1"]["label_ocr"] == "5"
    assert by_id["c2"]["label_ocr"] == "3"
    assert by_id["c3"]["label_ocr"] == "7"
