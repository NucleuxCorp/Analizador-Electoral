"""Tests for cross-mesa index builder (cross_mesa_index.py).

TDD cycle: tests written FIRST (RED phase) before production code.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from src.modules.analyzer.cross_mesa_index import (
    normalize_key,
    parse_e14c_index,
    parse_transmission_index,
    build_e14t_sha256_index,
    resolve_e14t_paths,
    resolve_e14d_paths,
    build_cross_index,
)


# ---------------------------------------------------------------------------
# normalize_key helpers
# ---------------------------------------------------------------------------

class TestNormalizeKey:
    """geo_key normalization: dept=2, mpio=3, zona=3, puesto=2, mesa=3."""

    def test_basic_padding(self):
        key = normalize_key("1", "1", "1", "1", "1")
        assert key == ("01", "001", "001", "01", "001")

    def test_already_padded_unchanged(self):
        key = normalize_key("05", "001", "014", "02", "025")
        assert key == ("05", "001", "014", "02", "025")

    def test_2char_zona_pads_to_3(self):
        """E14C zona '01' must pad to '001'."""
        key = normalize_key("01", "001", "01", "08", "1")
        assert key == ("01", "001", "001", "08", "001")

    def test_3char_zona_unchanged(self):
        """E14T/E14D zona '002' stays '002'."""
        key = normalize_key("21", "031", "002", "03", "011")
        assert key == ("21", "031", "002", "03", "011")

    def test_strip_then_pad(self):
        """Leading/trailing whitespace stripped before padding."""
        key = normalize_key(" 5 ", " 1 ", " 2 ", " 3 ", " 4 ")
        assert key == ("05", "001", "002", "03", "004")

    def test_integer_mesa_string(self):
        """mesa as int-string ('1') pads to '001'."""
        key = normalize_key("60", "001", "001", "03", "1")
        assert key == ("60", "001", "001", "03", "001")


# ---------------------------------------------------------------------------
# parse_e14c_index
# ---------------------------------------------------------------------------

class TestParseE14cIndex:
    """parse_e14c_index reads e14c_sv_urls.jsonl and resolves paths."""

    def _write_jsonl(self, tmp_path: Path, entries: list[dict]) -> Path:
        p = tmp_path / "e14c_sv_urls.jsonl"
        with p.open("w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")
        return p

    def _make_entry(
        self,
        dept="01", mpio="001", zona="01", puesto="08", mesa=1,
        pdf_url: str | None = None,
    ) -> dict:
        if pdf_url is None:
            pdf_url = (
                f"https://example.com/docs/E14/"
                f"E14_PRE_{dept}_{mpio}_{zona.zfill(3)}_{puesto}_{mesa:03d}_5007.pdf"
            )
        return {
            "cod_dept": dept,
            "cod_mpio": mpio,
            "zona": zona,
            "cod_puesto": puesto,
            "mesa": mesa,
            "pdf_url": pdf_url,
        }

    def test_returns_dict(self, tmp_path):
        jsonl = self._write_jsonl(tmp_path, [self._make_entry()])
        result = parse_e14c_index(jsonl, tmp_path)
        assert isinstance(result, dict)

    def test_key_is_normalized_tuple(self, tmp_path):
        """Keys must be normalized tuples (dept, mpio, zona, puesto, mesa)."""
        jsonl = self._write_jsonl(tmp_path, [self._make_entry(zona="01")])
        result = parse_e14c_index(jsonl, tmp_path)
        # zona "01" must pad to "001"
        assert ("01", "001", "001", "08", "001") in result

    def test_path_is_none_when_file_missing(self, tmp_path):
        """When PDF is not on disk, path is None."""
        jsonl = self._write_jsonl(tmp_path, [self._make_entry()])
        result = parse_e14c_index(jsonl, tmp_path)
        key = ("01", "001", "001", "08", "001")
        assert result[key] is None

    def test_path_is_str_when_file_present(self, tmp_path):
        """When PDF exists on disk, path is an absolute path string."""
        # Create a matching file
        fname = "E14_PRE_01_001_001_00_08_001_5007.pdf"
        pdf = tmp_path / fname
        pdf.write_bytes(b"%PDF-1.4")
        entry = self._make_entry(
            pdf_url=f"https://example.com/docs/E14/{fname}"
        )
        jsonl = self._write_jsonl(tmp_path, [entry])
        result = parse_e14c_index(jsonl, tmp_path)
        key = ("01", "001", "001", "08", "001")
        assert result[key] is not None
        assert result[key] == str(pdf)

    def test_multiple_entries_all_indexed(self, tmp_path):
        entries = [
            self._make_entry(zona="01", mesa=1),
            self._make_entry(zona="02", mesa=1),
        ]
        jsonl = self._write_jsonl(tmp_path, entries)
        result = parse_e14c_index(jsonl, tmp_path)
        assert len(result) == 2

    def test_duplicate_key_last_wins(self, tmp_path):
        """If same geo_key appears twice, later entry overwrites earlier."""
        entries = [
            self._make_entry(pdf_url="https://example.com/A.pdf"),
            self._make_entry(pdf_url="https://example.com/B.pdf"),
        ]
        jsonl = self._write_jsonl(tmp_path, entries)
        result = parse_e14c_index(jsonl, tmp_path)
        # Should have one key, not two
        assert len(result) == 1


# ---------------------------------------------------------------------------
# parse_transmission_index
# ---------------------------------------------------------------------------

class TestParseTransmissionIndex:
    """parse_transmission_index reads allTransmissionCodes_e14t.json."""

    def _write_json(self, tmp_path: Path, nodes: list[dict]) -> Path:
        p = tmp_path / "allTransmissionCodes_e14t.json"
        data = {
            "data": {
                "status3": {"nodes": nodes}
            }
        }
        with p.open("w") as f:
            json.dump(data, f)
        return p

    def _make_node(
        self,
        tx_code="1234567",
        mesa="001",
        dept="05",
        mpio="001",
        zona="001",
        puesto="01",
    ) -> dict:
        return {
            "idTransmissionCode": tx_code,
            "numberStand": mesa,
            "idDepartmentCode": dept,
            "municipalityCode": mpio,
            "idZoneCode": zona,
            "standCode": puesto,
        }

    def test_returns_dict(self, tmp_path):
        p = self._write_json(tmp_path, [self._make_node()])
        result = parse_transmission_index(p)
        assert isinstance(result, dict)

    def test_key_is_normalized_tuple(self, tmp_path):
        """Key must match normalized geo_key convention."""
        node = self._make_node(dept="5", mpio="1", zona="1", puesto="1", mesa="1")
        p = self._write_json(tmp_path, [node])
        result = parse_transmission_index(p)
        assert ("05", "001", "001", "01", "001") in result

    def test_value_is_txcode(self, tmp_path):
        """Value must be the idTransmissionCode string."""
        node = self._make_node(tx_code="9876543", mesa="002")
        p = self._write_json(tmp_path, [node])
        result = parse_transmission_index(p)
        key = ("05", "001", "001", "01", "002")
        assert result[key] == "9876543"

    def test_multiple_status_groups_merged(self, tmp_path):
        """Nodes from different status groups are all included."""
        p = tmp_path / "allTransmissionCodes_e14t.json"
        data = {
            "data": {
                "status3": {"nodes": [self._make_node(tx_code="AAA", mesa="001")]},
                "status11": {"nodes": [self._make_node(tx_code="BBB", mesa="002")]},
            }
        }
        with p.open("w") as f:
            json.dump(data, f)
        result = parse_transmission_index(p)
        assert len(result) == 2

    def test_empty_nodes_returns_empty(self, tmp_path):
        p = self._write_json(tmp_path, [])
        result = parse_transmission_index(p)
        assert result == {}


# ---------------------------------------------------------------------------
# resolve_e14t_paths
# ---------------------------------------------------------------------------

class TestResolveE14tPaths:
    """resolve_e14t_paths maps transmission index → absolute paths on disk.

    E14T files are SHA256-named inside a hierarchy:
        {e14t_root}/{DEPT_NAME}/{MPIO_NAME}/zona_{NNN}/puesto_{NN}/{sha256}.pdf

    Tests supply minimal departamentos.json and divipole.json fixtures so
    the resolver can map dept/mpio names to codes without real data files.
    """

    def _write_fixtures(self, tmp_path, dept_code, dept_name, mpio_code, mpio_name):
        """Write minimal departamentos.json and divipole.json for the test."""
        import json
        depts = [{"id": dept_code, "nombre": dept_name, "url": ""}]
        dept_path = tmp_path / "departamentos.json"
        dept_path.write_text(json.dumps(depts), encoding="utf-8")

        divipole = {
            "departamentos": {
                dept_code: {
                    "municipios": {
                        mpio_code: {
                            "nombre": mpio_name,
                            "zonas": {},
                        }
                    }
                }
            }
        }
        div_path = tmp_path / "divipole.json"
        div_path.write_text(json.dumps(divipole), encoding="utf-8")
        return dept_path, div_path

    def test_returns_none_when_file_missing(self, tmp_path):
        dept_path, div_path = self._write_fixtures(
            tmp_path, "01", "ANTIOQUIA", "001", "MEDELLIN"
        )
        tx_idx = {("01", "001", "001", "01", "001"): "TX1"}
        result = resolve_e14t_paths(
            tx_idx, tmp_path,
            divipole_path=div_path, departamentos_path=dept_path,
        )
        assert result[("01", "001", "001", "01", "001")] is None

    def test_finds_file_when_present(self, tmp_path):
        """Files are SHA256-named inside dept/mpio/zona_NNN/puesto_NN/."""
        dept_path, div_path = self._write_fixtures(
            tmp_path, "01", "ANTIOQUIA", "001", "MEDELLIN"
        )
        tx_idx = {("01", "001", "001", "01", "001"): "TX1"}
        # Create hierarchical directory structure
        puesto_dir = tmp_path / "ANTIOQUIA" / "MEDELLIN" / "zona_001" / "puesto_01"
        puesto_dir.mkdir(parents=True)
        pdf = puesto_dir / "abcdef1234.pdf"
        pdf.write_bytes(b"%PDF")
        result = resolve_e14t_paths(
            tx_idx, tmp_path,
            divipole_path=div_path, departamentos_path=dept_path,
        )
        assert result[("01", "001", "001", "01", "001")] == str(pdf)

    def test_returns_none_for_all_when_dir_empty(self, tmp_path):
        dept_path, div_path = self._write_fixtures(
            tmp_path, "01", "ANTIOQUIA", "001", "MEDELLIN"
        )
        tx_idx = {
            ("01", "001", "001", "01", "001"): "TX1",
            ("01", "001", "001", "01", "002"): "TX2",
        }
        result = resolve_e14t_paths(
            tx_idx, tmp_path,
            divipole_path=div_path, departamentos_path=dept_path,
        )
        assert all(v is None for v in result.values())

    def test_all_keys_present_in_result(self, tmp_path):
        dept_path, div_path = self._write_fixtures(
            tmp_path, "01", "ANTIOQUIA", "001", "MEDELLIN"
        )
        tx_idx = {
            ("01", "001", "001", "01", "001"): "TX1",
            ("01", "001", "001", "01", "002"): "TX2",
        }
        result = resolve_e14t_paths(
            tx_idx, tmp_path,
            divipole_path=div_path, departamentos_path=dept_path,
        )
        assert set(result.keys()) == set(tx_idx.keys())

    def _write_tc_json(self, tmp_path, nodes: list) -> Path:
        """Write a minimal allTransmissionCodes JSON with expectedName fields."""
        data = {"data": {"status3": {"nodes": nodes}}}
        p = tmp_path / "tc.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def test_sha256_direct_lookup_assigns_correct_mesa(self, tmp_path):
        """SHA256 lookup correctly maps PDF filenames to geo_keys bypassing sort."""
        dept_path, div_path = self._write_fixtures(
            tmp_path, "01", "ANTIOQUIA", "001", "MEDELLIN"
        )
        # Two PDFs in the same puesto: alphabetically pdf_a < pdf_b,
        # but pdf_a is mesa 009 and pdf_b is mesa 001 (reversed order).
        sha_a = "aaaa000000000000000000000000000000000000000000000000000000000001"
        sha_b = "bbbb000000000000000000000000000000000000000000000000000000000002"

        puesto_dir = tmp_path / "ANTIOQUIA" / "MEDELLIN" / "zona_001" / "puesto_01"
        puesto_dir.mkdir(parents=True)
        pdf_a = puesto_dir / f"{sha_a}.pdf"
        pdf_b = puesto_dir / f"{sha_b}.pdf"
        pdf_a.write_bytes(b"%PDF")
        pdf_b.write_bytes(b"%PDF")

        tc_json = self._write_tc_json(tmp_path, [
            {
                "idTransmissionCode": "TX1",
                "expectedName": f"{sha_a}.pdf",
                "numberStand": "009",
                "idDepartmentCode": "01",
                "municipalityCode": "001",
                "idZoneCode": "01",
                "standCode": "01",
            },
            {
                "idTransmissionCode": "TX2",
                "expectedName": f"{sha_b}.pdf",
                "numberStand": "001",
                "idDepartmentCode": "01",
                "municipalityCode": "001",
                "idZoneCode": "01",
                "standCode": "01",
            },
        ])

        tx_idx = {
            ("01", "001", "001", "01", "009"): "TX1",
            ("01", "001", "001", "01", "001"): "TX2",
        }
        result = resolve_e14t_paths(
            tx_idx, tmp_path,
            divipole_path=div_path, departamentos_path=dept_path,
            e14t_json_path=tc_json,
        )

        # pdf_a (alphabetically first) must map to mesa 009, NOT mesa 001
        assert result[("01", "001", "001", "01", "009")] == str(pdf_a)
        assert result[("01", "001", "001", "01", "001")] == str(pdf_b)

    def test_sha256_lookup_unknown_file_stays_none(self, tmp_path):
        """A PDF whose SHA256 stem is not in expectedName → remains None."""
        dept_path, div_path = self._write_fixtures(
            tmp_path, "01", "ANTIOQUIA", "001", "MEDELLIN"
        )
        sha_known = "aaaa000000000000000000000000000000000000000000000000000000000001"
        sha_unknown = "ffff000000000000000000000000000000000000000000000000000000000099"

        puesto_dir = tmp_path / "ANTIOQUIA" / "MEDELLIN" / "zona_001" / "puesto_01"
        puesto_dir.mkdir(parents=True)
        (puesto_dir / f"{sha_unknown}.pdf").write_bytes(b"%PDF")  # on disk but not in JSON

        tc_json = self._write_tc_json(tmp_path, [
            {
                "idTransmissionCode": "TX1",
                "expectedName": f"{sha_known}.pdf",  # different SHA256, not on disk
                "numberStand": "001",
                "idDepartmentCode": "01",
                "municipalityCode": "001",
                "idZoneCode": "01",
                "standCode": "01",
            },
        ])
        tx_idx = {("01", "001", "001", "01", "001"): "TX1"}
        result = resolve_e14t_paths(
            tx_idx, tmp_path,
            divipole_path=div_path, departamentos_path=dept_path,
            e14t_json_path=tc_json,
        )
        assert result[("01", "001", "001", "01", "001")] is None


# ---------------------------------------------------------------------------
# resolve_e14d_paths
# ---------------------------------------------------------------------------

class TestResolveE14dPaths:
    """resolve_e14d_paths maps e14d_sv_urls.jsonl entries → absolute paths."""

    def _write_jsonl(self, tmp_path: Path, entries: list[dict]) -> Path:
        p = tmp_path / "e14d_sv_urls.jsonl"
        with p.open("w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")
        return p

    def _make_entry(
        self,
        dept="21", mpio="031", zona="002", puesto="03", mesa="011",
        sha256: str = "abc123",
    ) -> dict:
        return {
            "cod_dept": dept,
            "cod_mpio": mpio,
            "zona": zona,
            "puesto": puesto,
            "mesa": mesa,
            "pdf_url": f"https://example.com/pdf/{sha256}.pdf?uuid=xxx",
        }

    def test_returns_none_when_file_missing(self, tmp_path):
        jsonl = self._write_jsonl(tmp_path, [self._make_entry(sha256="deadbeef")])
        result = resolve_e14d_paths(jsonl, tmp_path)
        key = ("21", "031", "002", "03", "011")
        assert result[key] is None

    def test_finds_file_by_sha256(self, tmp_path):
        """E14D files are named {sha256}.pdf anywhere under e14d_root."""
        sha = "abcdef1234567890" * 4  # 64-char hex
        jsonl = self._write_jsonl(tmp_path, [self._make_entry(sha256=sha)])
        # Create file in a nested directory
        subdir = tmp_path / "AMAZONAS" / "LETICIA" / "zona_002" / "puesto_03"
        subdir.mkdir(parents=True)
        pdf = subdir / f"{sha}.pdf"
        pdf.write_bytes(b"%PDF")
        result = resolve_e14d_paths(jsonl, tmp_path)
        key = ("21", "031", "002", "03", "011")
        assert result[key] == str(pdf)

    def test_key_normalization(self, tmp_path):
        """Zona 2-char '01' must pad to '001'."""
        jsonl = self._write_jsonl(tmp_path, [self._make_entry(zona="01")])
        result = resolve_e14d_paths(jsonl, tmp_path)
        assert ("21", "031", "001", "03", "011") in result

    def test_all_keys_present(self, tmp_path):
        entries = [
            self._make_entry(dept="01", mpio="001", zona="001", puesto="01", mesa="001"),
            self._make_entry(dept="05", mpio="001", zona="001", puesto="02", mesa="001"),
        ]
        jsonl = self._write_jsonl(tmp_path, entries)
        result = resolve_e14d_paths(jsonl, tmp_path)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# build_cross_index
# ---------------------------------------------------------------------------

class TestBuildCrossIndex:
    """build_cross_index joins three path dicts on geo_key union."""

    def test_all_three_sources_present(self):
        key = ("01", "001", "001", "08", "001")
        e14c = {key: "/path/e14c.pdf"}
        e14t = {key: "/path/e14t.pdf"}
        e14d = {key: "/path/e14d.pdf"}
        records = build_cross_index(e14c, e14t, e14d)
        assert len(records) == 1
        r = records[0]
        assert r["dept"] == "01"
        assert r["mpio"] == "001"
        assert r["zona"] == "001"
        assert r["puesto"] == "08"
        assert r["mesa"] == "001"
        assert r["e14c_path"] == "/path/e14c.pdf"
        assert r["e14t_path"] == "/path/e14t.pdf"
        assert r["e14d_path"] == "/path/e14d.pdf"
        assert r["all_available"] is True

    def test_partial_overlap_sources_available(self):
        """When only two sources resolve, all_available=False."""
        key = ("01", "001", "001", "08", "001")
        e14c = {key: "/path/e14c.pdf"}
        e14t = {key: None}
        e14d = {key: "/path/e14d.pdf"}
        records = build_cross_index(e14c, e14t, e14d)
        r = records[0]
        assert r["all_available"] is False
        assert r["e14t_path"] is None

    def test_union_of_all_keys(self):
        """Keys in any source are included even if absent from others."""
        key_a = ("01", "001", "001", "08", "001")
        key_b = ("05", "001", "001", "01", "001")
        e14c = {key_a: "/a.pdf"}
        e14t = {key_b: "/b.pdf"}
        e14d = {}
        records = build_cross_index(e14c, e14t, e14d)
        assert len(records) == 2
        keys_in_result = {(r["dept"], r["mpio"], r["zona"], r["puesto"], r["mesa"]) for r in records}
        assert key_a in keys_in_result
        assert key_b in keys_in_result

    def test_missing_key_gets_none_path(self):
        """Key only in e14c → e14t_path and e14d_path are None."""
        key = ("60", "001", "001", "03", "001")
        e14c = {key: "/e14c.pdf"}
        records = build_cross_index(e14c, {}, {})
        r = records[0]
        assert r["e14t_path"] is None
        assert r["e14d_path"] is None

    def test_all_null_paths_all_available_false(self):
        """Record with all None paths: all_available=False."""
        key = ("01", "001", "001", "08", "001")
        records = build_cross_index({key: None}, {key: None}, {key: None})
        assert records[0]["all_available"] is False

    def test_record_structure_complete(self):
        """Every record has all required fields."""
        key = ("05", "027", "001", "01", "003")
        records = build_cross_index({key: "/c.pdf"}, {key: "/t.pdf"}, {key: "/d.pdf"})
        r = records[0]
        required = {"dept", "mpio", "zona", "puesto", "mesa", "e14c_path", "e14t_path", "e14d_path", "all_available"}
        assert required.issubset(set(r.keys()))
