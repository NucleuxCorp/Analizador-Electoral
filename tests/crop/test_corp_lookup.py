"""Tests for src/modules/crop/corp_lookup.py.

TDD cycle: tests written first (RED) before implementation exists.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def url_jsonl(tmp_path: Path) -> Path:
    """Write a minimal JSONL with id_informacion_mesa_corporacion entries."""
    records = [
        {
            "dept": "01",
            "mpio": "001",
            "zona": "01",
            "puesto": "01",
            "mesa": "3",
            "id_informacion_mesa_corporacion": "02010001010103",
        },
        {
            "dept": "05",
            "mpio": "001",
            "zona": "02",
            "puesto": "03",
            "mesa": "7",
            "id_informacion_mesa_corporacion": "05050001020307",
        },
        # Record without id_informacion_mesa_corporacion — should be skipped with warning
        {
            "dept": "99",
            "mpio": "999",
        },
    ]
    p = tmp_path / "e14c_urls.jsonl"
    with p.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBuildCorpLookup:
    def test_known_key_returns_corp(self, url_jsonl: Path):
        from src.modules.crop.corp_lookup import build_corp_lookup

        lookup = build_corp_lookup([url_jsonl])
        key = ("01", "001", "01", "01", "3")
        assert key in lookup
        assert lookup[key] == "02"

    def test_second_record_key(self, url_jsonl: Path):
        from src.modules.crop.corp_lookup import build_corp_lookup

        lookup = build_corp_lookup([url_jsonl])
        key = ("05", "001", "02", "03", "7")
        assert key in lookup
        assert lookup[key] == "05"

    def test_absent_corp_field_is_silent(self, url_jsonl: Path, capsys):
        """Records where id_informacion_mesa_corporacion is absent are skipped silently.

        e14c_urls.jsonl uses id_mesa (no corp) — absence means the JSONL simply
        doesn't carry corp data, not a malformed record.
        """
        from src.modules.crop.corp_lookup import build_corp_lookup

        build_corp_lookup([url_jsonl])
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_malformed_corp_field_emits_warning(self, tmp_path: Path, capsys):
        """Records where id_informacion_mesa_corporacion is present but too short warn."""
        import json as _json
        from src.modules.crop.corp_lookup import build_corp_lookup

        bad = tmp_path / "bad.jsonl"
        bad.write_text(_json.dumps({
            "id_informacion_mesa_corporacion": "0",  # present but len < 2
            "dept": "01", "mpio": "001", "zona": "01", "puesto": "01", "mesa": "1",
        }) + "\n")
        build_corp_lookup([bad])
        captured = capsys.readouterr()
        assert "warning" in captured.err.lower()

    def test_multiple_files_merged(self, tmp_path: Path):
        from src.modules.crop.corp_lookup import build_corp_lookup

        f1 = tmp_path / "a.jsonl"
        f2 = tmp_path / "b.jsonl"
        r1 = {"dept": "01", "mpio": "001", "zona": "01", "puesto": "01", "mesa": "1",
               "id_informacion_mesa_corporacion": "02010001010101"}
        r2 = {"dept": "02", "mpio": "002", "zona": "02", "puesto": "02", "mesa": "2",
               "id_informacion_mesa_corporacion": "03020002020202"}
        f1.write_text(json.dumps(r1) + "\n")
        f2.write_text(json.dumps(r2) + "\n")

        lookup = build_corp_lookup([f1, f2])
        assert ("01", "001", "01", "01", "1") in lookup
        assert ("02", "002", "02", "02", "2") in lookup

    def test_empty_paths_list_returns_empty_dict(self):
        from src.modules.crop.corp_lookup import build_corp_lookup

        lookup = build_corp_lookup([])
        assert lookup == {}


class TestLookupCorp:
    def test_found_key_returns_corp(self, url_jsonl: Path):
        from src.modules.crop.corp_lookup import build_corp_lookup, lookup_corp

        lookup = build_corp_lookup([url_jsonl])
        key = ("01", "001", "01", "01", "3")
        result = lookup_corp(key, lookup)
        assert result == "02"

    def test_missing_key_returns_default_and_warns(self, url_jsonl: Path, capsys):
        from src.modules.crop.corp_lookup import build_corp_lookup, lookup_corp

        lookup = build_corp_lookup([url_jsonl])
        key = ("99", "999", "99", "99", "99")
        result = lookup_corp(key, lookup)
        assert result == "00"
        captured = capsys.readouterr()
        assert "warning" in captured.err.lower() or "miss" in captured.err.lower() or "not found" in captured.err.lower()

    def test_override_takes_precedence(self, url_jsonl: Path):
        from src.modules.crop.corp_lookup import build_corp_lookup, lookup_corp

        lookup = build_corp_lookup([url_jsonl])
        key = ("01", "001", "01", "01", "3")
        result = lookup_corp(key, lookup, override="99")
        assert result == "99"

    def test_override_on_missing_key(self):
        from src.modules.crop.corp_lookup import lookup_corp

        result = lookup_corp(("xx", "xxx", "xx", "xx", "0"), {}, override="03")
        assert result == "03"
