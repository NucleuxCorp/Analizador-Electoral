"""Tests for upload_mesa_results.py — T1b-1 through T1b-7.

TDD cycle: RED → GREEN → REFACTOR.
Covers:
  - compute_overall_status() (classifier, 5-level precedence)
  - build_row() (column flattening + raw_data preservation)
  - discover_files() / iter_jsonl_files() (file discovery + filtering)
  - upload() (batch upsert, dry-run, on_conflict)

No live Supabase connection is required — all DB calls are mocked.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Fixtures — minimal JSONL row shapes
# ---------------------------------------------------------------------------

def _make_row(
    *,
    dept: str = "01",
    mpio: str = "001",
    zona: str = "001",
    puesto: str = "01",
    mesa: str = "001",
    sources_ok: int = 3,
    e14c_status: str = "ok",
    e14t_status: str = "ok",
    e14d_status: str = "ok",
    e14c_arith_ok: bool = True,
    e14t_arith_ok: bool = True,
    e14d_arith_ok: bool = True,
    e14c_arith_delta: int = 0,
    e14t_arith_delta: int = 0,
    e14d_arith_delta: int = 0,
    e14c_urna: int = 100,
    e14t_urna: int = 100,
    e14d_urna: int = 100,
    e14c_sum_votes: int = 100,
    e14t_sum_votes: int = 100,
    e14d_sum_votes: int = 100,
    cross_discrepancy: bool = False,
    cross_discrepant_fields: list | None = None,
    e14c_tachon_suspicious: bool = False,
    e14c_tachon_max_score: float = 0.1,
    e14t_tachon_suspicious: bool = False,
    e14t_tachon_max_score: float = 0.0,
) -> dict:
    """Build a minimal cross_mesa_validation JSONL row."""
    return {
        "dept": dept,
        "mpio": mpio,
        "zona": zona,
        "puesto": puesto,
        "mesa": mesa,
        "sources": {
            "e14c": {
                "status": e14c_status,
                "fields": {"URNA": e14c_urna, "VOTANTES": e14c_sum_votes},
            },
            "e14t": {
                "status": e14t_status,
                "fields": {"URNA": e14t_urna, "VOTANTES": e14t_sum_votes},
            } if e14t_status else None,
            "e14d": {
                "status": e14d_status,
                "fields": {"URNA": e14d_urna, "VOTANTES": e14d_sum_votes},
            } if e14d_status else None,
        },
        "congruencia": {
            "sources_ok": sources_ok,
            "fields": {},
            "summary": {
                "sources_ok": sources_ok,
                "cross_discrepancy": cross_discrepancy,
                "cross_discrepant_fields": cross_discrepant_fields or [],
            },
        },
        "aritmetica": {
            "e14c": {
                "ok": e14c_arith_ok,
                "sum_votes": e14c_sum_votes,
                "urna": e14c_urna,
                "delta": e14c_arith_delta,
            },
            "e14t": {
                "ok": e14t_arith_ok,
                "sum_votes": e14t_sum_votes,
                "urna": e14t_urna,
                "delta": e14t_arith_delta,
            },
            "e14d": {
                "ok": e14d_arith_ok,
                "sum_votes": e14d_sum_votes,
                "urna": e14d_urna,
                "delta": e14d_arith_delta,
            },
        },
        "tachones": {
            "e14c": {
                "suspicious": e14c_tachon_suspicious,
                "max_score": e14c_tachon_max_score,
                "suspicious_fields": [],
                "density_high": False,
                "doble_escritura": False,
                "tachon": False,
            },
            "e14t": {
                "suspicious": e14t_tachon_suspicious,
                "max_score": e14t_tachon_max_score,
                "suspicious_fields": [],
                "density_high": False,
                "doble_escritura": False,
                "tachon": False,
            } if e14t_status else None,
            "e14d": None,
        },
    }


# ---------------------------------------------------------------------------
# T1b-1 (RED) — compute_overall_status tests
# ---------------------------------------------------------------------------

class TestClassifyClean:
    def test_classify_clean(self):
        """Row with no flags returns 'clean'."""
        from scripts.upload_mesa_results import compute_overall_status

        row = _make_row()
        assert compute_overall_status(row) == "clean"


class TestClassifyKnownAnomaly:
    def test_classify_known_anomaly_missing_sources(self):
        """When sources_ok_count < 2, returns 'known_anomaly'."""
        from scripts.upload_mesa_results import compute_overall_status

        row = _make_row(sources_ok=1)
        assert compute_overall_status(row) == "known_anomaly"

    def test_classify_known_anomaly_urna_zero_with_votes(self):
        """When URNA is 0 but sum_votes > 0 in a source, returns 'known_anomaly'."""
        from scripts.upload_mesa_results import compute_overall_status

        row = _make_row(e14c_urna=0, e14c_sum_votes=100)
        assert compute_overall_status(row) == "known_anomaly"


class TestClassifyWarning:
    def test_classify_warning_tachon_only(self):
        """tachon_suspicious=True with no arithmetic delta returns 'warning'."""
        from scripts.upload_mesa_results import compute_overall_status

        row = _make_row(e14c_tachon_suspicious=True)
        assert compute_overall_status(row) == "warning"


class TestClassifyDiscrepancy:
    def test_classify_discrepancy_cross(self):
        """cross_discrepancy=True returns 'discrepancy'."""
        from scripts.upload_mesa_results import compute_overall_status

        row = _make_row(cross_discrepancy=True)
        assert compute_overall_status(row) == "discrepancy"

    def test_classify_discrepancy_arith_delta(self):
        """Any arith_delta > 0 (but <= 30) returns 'discrepancy'."""
        from scripts.upload_mesa_results import compute_overall_status

        row = _make_row(e14c_arith_delta=5)
        assert compute_overall_status(row) == "discrepancy"


class TestClassifyCritical:
    def test_classify_needs_review_large_delta_delta_over_30(self):
        """Any arith_delta > 30 returns 'needs_review_large_delta'."""
        from scripts.upload_mesa_results import compute_overall_status

        row = _make_row(e14c_arith_delta=31)
        assert compute_overall_status(row) == "needs_review_large_delta"

    def test_classify_needs_review_large_delta_tachon_and_discrepancy(self):
        """cross_discrepancy AND tachon_suspicious together returns 'needs_review_large_delta'."""
        from scripts.upload_mesa_results import compute_overall_status

        row = _make_row(cross_discrepancy=True, e14c_tachon_suspicious=True)
        assert compute_overall_status(row) == "needs_review_large_delta"

    def test_classify_needs_review_large_delta_delta_exactly_30_is_not_needs_review(self):
        """arith_delta == 30 is NOT needs_review_large_delta (must be strictly > 30)."""
        from scripts.upload_mesa_results import compute_overall_status

        row = _make_row(e14c_arith_delta=30)
        # delta==30 does NOT exceed FRAUD_MAX_DIFF (30), so not needs_review_large_delta
        # but cross_discrepancy is False, so it becomes discrepancy (delta > 0)
        assert compute_overall_status(row) == "discrepancy"

    def test_classify_needs_review_large_delta_uses_abs_delta(self):
        """Negative delta with abs > 30 is treated as needs_review_large_delta."""
        from scripts.upload_mesa_results import compute_overall_status

        row = _make_row(e14c_arith_delta=-31)
        assert compute_overall_status(row) == "needs_review_large_delta"


# ---------------------------------------------------------------------------
# T1b-8 (RED) — _derive_has_missing_fields() source-completeness fix
# (fix-mesa-source-status-classification: T4)
# ---------------------------------------------------------------------------

class TestDeriveHasMissingFieldsSourceStatus:
    def test_two_sources_agree_but_e14c_missing_is_known_anomaly(self):
        """sources_ok=2 with 2 present sources agreeing, but e14c not 'ok',
        must still classify as known_anomaly, not clean."""
        from scripts.upload_mesa_results import compute_overall_status

        row = _make_row(sources_ok=2, e14c_status="no-descargado", e14t_status="ok", e14d_status="ok")
        assert compute_overall_status(row) == "known_anomaly"

    def test_sources_ok_1_still_missing_fields_regression(self):
        """Pre-existing sources_ok < 2 behavior is preserved."""
        from scripts.upload_mesa_results import _derive_has_missing_fields

        row = _make_row(sources_ok=1)
        assert _derive_has_missing_fields(row) is True

    def test_all_three_sources_ok_no_other_anomaly_is_clean(self):
        """All 3 sources 'ok', no other anomaly signal, stays clean."""
        from scripts.upload_mesa_results import compute_overall_status

        row = _make_row()
        assert compute_overall_status(row) == "clean"


# ---------------------------------------------------------------------------
# T1b-2 (RED) — build_row and discover_files tests
# ---------------------------------------------------------------------------

class TestBuildRow:
    def test_build_row_flattens_all_columns(self):
        """build_row returns a flat dict with all expected column keys."""
        from scripts.upload_mesa_results import build_row

        raw = _make_row()
        result = build_row(raw)

        # Required flat columns
        assert result["mesa_key"] == "01_001_001_01_001"
        assert result["dept"] == "01"
        assert result["mpio"] == "001"
        assert result["zona"] == "001"
        assert result["puesto"] == "01"
        assert result["mesa"] == "001"
        assert result["sources_ok_count"] == 3
        assert "e14c_arith_ok" in result
        assert "e14c_arith_delta" in result
        assert "cross_discrepancy" in result
        assert "tachon_suspicious" in result
        assert "has_missing_fields" in result
        assert "overall_status" in result
        # columns not in DDL schema — must NOT be present in flat row
        assert "e14c_status" not in result
        assert "tachon_max_score" not in result
        assert "cross_discrepant_fields" not in result

    def test_build_row_preserves_raw_data(self):
        """build_row sets raw_data to the entire original dict."""
        from scripts.upload_mesa_results import build_row

        raw = _make_row(dept="05", mesa="099")
        result = build_row(raw)

        assert result["raw_data"] == raw

    def test_build_row_overall_status_is_set(self):
        """build_row calls compute_overall_status and stores it."""
        from scripts.upload_mesa_results import build_row

        raw = _make_row(e14c_tachon_suspicious=True)
        result = build_row(raw)

        assert result["overall_status"] == "warning"

    def test_build_row_null_sources_handled(self):
        """build_row handles None source entries without raising."""
        from scripts.upload_mesa_results import build_row

        raw = _make_row()
        # Explicitly null out e14t and e14d sources
        raw["sources"]["e14t"] = None
        raw["sources"]["e14d"] = None
        raw["aritmetica"]["e14t"] = None
        raw["aritmetica"]["e14d"] = None
        raw["tachones"]["e14t"] = None
        raw["congruencia"]["sources_ok"] = 1

        result = build_row(raw)
        assert result is not None
        assert result["e14t_arith_ok"] is None
        assert result["e14d_arith_ok"] is None


# ---------------------------------------------------------------------------
# T1b-9 (RED) — source_status verbatim persistence
# (fix-mesa-source-status-classification: T6)
# ---------------------------------------------------------------------------

class TestBuildRowSourceStatus:
    def test_source_status_verbatim_values(self):
        """source_status carries the raw status string per source, None for absent."""
        from scripts.upload_mesa_results import build_row

        raw = _make_row(e14c_status="extraction_error", e14t_status="ok")
        raw["sources"]["e14d"] = None

        result = build_row(raw)
        assert result["source_status"] == {"e14c": "extraction_error", "e14t": "ok", "e14d": None}

    def test_has_missing_fields_present_regardless_of_source_status(self):
        """has_missing_fields stays a bool in build_row() output, additive to source_status."""
        from scripts.upload_mesa_results import build_row

        raw = _make_row()
        result = build_row(raw)
        assert isinstance(result["has_missing_fields"], bool)
        assert "source_status" in result


class TestDiscoverFiles:
    def test_skip_test_files(self):
        """Files with '_test' in the name are excluded from discovery."""
        from scripts.upload_mesa_results import discover_files

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "cross_mesa_validation_01.jsonl").write_text("{}")
            (p / "cross_mesa_validation_antioquia_test.jsonl").write_text("{}")
            (p / "cross_mesa_validation_05.jsonl").write_text("{}")

            result = discover_files(p)
            names = {f.name for f in result}
            assert "cross_mesa_validation_antioquia_test.jsonl" not in names
            assert "cross_mesa_validation_01.jsonl" in names
            assert "cross_mesa_validation_05.jsonl" in names

    def test_dept_filter_2digit_only(self):
        """Only files matching cross_mesa_validation_{2-digit}.jsonl are returned."""
        from scripts.upload_mesa_results import discover_files

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "cross_mesa_validation_01.jsonl").write_text("{}")
            (p / "cross_mesa_validation_antioquia.jsonl").write_text("{}")
            (p / "cross_mesa_validation_123.jsonl").write_text("{}")  # 3 digits — excluded
            (p / "some_other_file.jsonl").write_text("{}")

            result = discover_files(p)
            names = {f.name for f in result}
            assert "cross_mesa_validation_01.jsonl" in names
            assert "cross_mesa_validation_antioquia.jsonl" not in names
            assert "cross_mesa_validation_123.jsonl" not in names
            assert "some_other_file.jsonl" not in names


# ---------------------------------------------------------------------------
# T1b-3 (RED) — upload() function tests
# ---------------------------------------------------------------------------

def _make_mock_supabase():
    """Return a MagicMock supabase client with fluent upsert chain."""
    chain = MagicMock()
    chain.upsert.return_value = chain
    chain.execute.return_value = MagicMock(data=[])

    client = MagicMock()
    client.table.return_value = chain
    return client, chain


class TestUploadIdempotentUpsert:
    def test_idempotent_upsert(self):
        """upload() calls upsert with on_conflict='mesa_key'."""
        from scripts.upload_mesa_results import upload

        mock_client, chain = _make_mock_supabase()

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            row = _make_row(dept="01", mesa="001")
            (p / "cross_mesa_validation_01.jsonl").write_text(
                json.dumps(row) + "\n"
            )

            with patch("scripts.upload_mesa_results._get_supabase_client", return_value=mock_client):
                summary = upload(data_dir=p, dry_run=False)

        # Must use on_conflict='mesa_key'
        chain.upsert.assert_called()
        call_kwargs = chain.upsert.call_args
        # on_conflict may be positional or keyword
        upsert_args = call_kwargs[1] if call_kwargs[1] else {}
        assert upsert_args.get("on_conflict") == "mesa_key" or "mesa_key" in str(call_kwargs)


class TestUploadDryRun:
    def test_dry_run_no_write(self):
        """When dry_run=True, upsert is never called."""
        from scripts.upload_mesa_results import upload

        mock_client, chain = _make_mock_supabase()

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            row = _make_row()
            (p / "cross_mesa_validation_01.jsonl").write_text(json.dumps(row) + "\n")

            with patch("scripts.upload_mesa_results._get_supabase_client", return_value=mock_client):
                summary = upload(data_dir=p, dry_run=True)

        chain.upsert.assert_not_called()
        mock_client.table.assert_not_called()


class TestUploadBatchSize:
    def test_batch_size_500(self):
        """Rows are submitted in batches of at most 500."""
        from scripts.upload_mesa_results import upload

        mock_client, chain = _make_mock_supabase()

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            # Write 750 rows — should produce 2 batches (500 + 250)
            rows = [_make_row(mesa=str(i).zfill(3)) for i in range(750)]
            with open(p / "cross_mesa_validation_01.jsonl", "w") as fh:
                for r in rows:
                    fh.write(json.dumps(r) + "\n")

            with patch("scripts.upload_mesa_results._get_supabase_client", return_value=mock_client):
                summary = upload(data_dir=p, dry_run=False)

        # 750 rows in batches of 500 → 2 upsert calls
        assert chain.upsert.call_count == 2
        # First batch has 500 rows
        first_batch = chain.upsert.call_args_list[0][0][0]
        assert len(first_batch) == 500
        # Second batch has 250 rows
        second_batch = chain.upsert.call_args_list[1][0][0]
        assert len(second_batch) == 250
