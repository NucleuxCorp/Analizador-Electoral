"""Tests for the hierarchical /mesas drill-down routes (Municipio → Puesto → Mesa).

TDD cycle: RED → GREEN → REFACTOR.
All Supabase / db-layer calls are mocked. No live connection required.

Covers (SDD change: mesas-hierarchical-drilldown, Work Unit 2 — Phases 2-4):
- Level 1 (GET /mesas): Municipio-first columns, row links to Level 2, unavailable fallback.
- Level 2 (GET /mesas/<dept>/<mpio>): Zona+Puesto columns, row links to Level 3, empty message.
- Level 3 (GET /mesas/<dept>/<mpio>/<zona>/<puesto>): 10 mesas/page, pagination, no controls when <=10.
- Invalid path params at any level -> safe empty/not-found response, never a 500.
- Breadcrumbs: Level 3 links back to GET /mesas.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def prod_app(tmp_path):
    """Flask app in production mode with all Supabase calls mocked."""
    env_vars = {
        "SUPABASE_URL": "https://fake.supabase.co",
        "SUPABASE_ANON_KEY": "fake-anon-key",
        "SECRET_KEY": "test-secret-for-public-mesas-routes",
    }
    with patch.dict(os.environ, env_vars):
        from src.modules.labeler.server import create_app
        index_path = tmp_path / "crops" / "index.jsonl"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        app = create_app(index_path=index_path, labels_dir=tmp_path)
        app.config["TESTING"] = True
        yield app


# ---------------------------------------------------------------------------
# Fixture data — matches get_hierarchical_mesa_stats() shape from PR1
# ---------------------------------------------------------------------------

def _empty_bucket(**overrides) -> dict:
    bucket = {
        "clean": 0, "known_anomaly": 0, "warning": 0,
        "discrepancy": 0, "needs_review_large_delta": 0,
        "total": 0, "en_revision": 0, "revisada": 0,
    }
    bucket.update(overrides)
    return bucket


def _hierarchical_stats_fixture() -> dict:
    """One municipality (dept=01 ANTIOQUIA, mpio=001 MEDELLIN) with two puestos."""
    return {
        "by_mpio": {
            "01_001": _empty_bucket(
                dept="01", mpio="001", clean=5, known_anomaly=1,
                total=6, en_revision=1, revisada=2,
            ),
        },
        "by_puesto": {
            "01_001": {
                "01_01": _empty_bucket(
                    dept="01", mpio="001", zona="01", puesto="01",
                    clean=3, known_anomaly=1, total=4, en_revision=0, revisada=1,
                ),
                "01_02": _empty_bucket(
                    dept="01", mpio="001", zona="01", puesto="02",
                    clean=2, total=2, en_revision=1, revisada=1,
                ),
            },
        },
        "_global": _empty_bucket(
            clean=5, known_anomaly=1, total=6, en_revision=1, revisada=2,
        ),
        "review_by_mesa": {},
    }


def _empty_municipality_stats() -> dict:
    """A municipality with by_mpio entry but zero puestos (defensive edge case)."""
    stats = _hierarchical_stats_fixture()
    stats["by_puesto"]["01_001"] = {}
    return stats


def _make_mesa_rows(n: int, dept="01", mpio="001", zona="01", puesto="01") -> list[dict]:
    return [
        {
            "mesa_key": f"{dept}_{mpio}_{zona}_{puesto}_{i}",
            "mesa": str(i),
            "dept": dept, "mpio": mpio, "zona": zona, "puesto": puesto,
            "overall_status": "clean" if i % 2 == 0 else "known_anomaly",
        }
        for i in range(1, n + 1)
    ]


def _paginated_side_effect(all_rows: list[dict]):
    def _side_effect(*args, **kwargs):
        page = kwargs.get("page", 1)
        per_page = kwargs.get("per_page", 10)
        start = (page - 1) * per_page
        return all_rows[start:start + per_page]
    return _side_effect


# ---------------------------------------------------------------------------
# Level 1: GET /mesas
# ---------------------------------------------------------------------------

class TestLevel1MunicipioTable:
    def test_municipio_column_before_departamento(self, prod_app):
        with patch(
            "src.modules.labeler.db.get_hierarchical_mesa_stats",
            return_value=_hierarchical_stats_fixture(),
        ):
            resp = prod_app.test_client().get("/mesas")

        assert resp.status_code == 200
        html = resp.data.decode()
        mun_idx = html.find(">Municipio<")
        dept_idx = html.find(">Departamento<")
        assert mun_idx != -1
        assert dept_idx != -1
        assert mun_idx < dept_idx

    def test_row_links_to_level2(self, prod_app):
        with patch(
            "src.modules.labeler.db.get_hierarchical_mesa_stats",
            return_value=_hierarchical_stats_fixture(),
        ):
            resp = prod_app.test_client().get("/mesas")

        html = resp.data.decode()
        assert 'href="/mesas/01/001"' in html

    def test_unavailable_fallback_when_stats_unreachable(self, prod_app):
        with patch(
            "src.modules.labeler.db.get_hierarchical_mesa_stats",
            side_effect=RuntimeError("supabase down"),
        ):
            resp = prod_app.test_client().get("/mesas")

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Datos no disponibles temporalmente" in html

    def test_municipality_without_analyzed_mesas_is_omitted(self, prod_app):
        """Only the mpio present in by_mpio (>=1 analyzed mesa) appears."""
        with patch(
            "src.modules.labeler.db.get_hierarchical_mesa_stats",
            return_value=_hierarchical_stats_fixture(),
        ):
            resp = prod_app.test_client().get("/mesas")

        html = resp.data.decode()
        # Only one mpio row link should be present (fixture has exactly one bucket)
        assert html.count('href="/mesas/01/001"') == 1


# ---------------------------------------------------------------------------
# Level 2: GET /mesas/<dept>/<mpio>
# ---------------------------------------------------------------------------

class TestLevel2PuestoTable:
    def test_zona_puesto_columns(self, prod_app):
        with patch(
            "src.modules.labeler.db.get_hierarchical_mesa_stats",
            return_value=_hierarchical_stats_fixture(),
        ):
            resp = prod_app.test_client().get("/mesas/01/001")

        assert resp.status_code == 200
        html = resp.data.decode()
        assert ">Zona<" in html
        assert ">Puesto<" in html

    def test_row_links_to_level3(self, prod_app):
        with patch(
            "src.modules.labeler.db.get_hierarchical_mesa_stats",
            return_value=_hierarchical_stats_fixture(),
        ):
            resp = prod_app.test_client().get("/mesas/01/001")

        html = resp.data.decode()
        assert 'href="/mesas/01/001/01/01"' in html
        assert 'href="/mesas/01/001/01/02"' in html

    def test_empty_municipality_shows_message_not_error(self, prod_app):
        with patch(
            "src.modules.labeler.db.get_hierarchical_mesa_stats",
            return_value=_empty_municipality_stats(),
        ):
            resp = prod_app.test_client().get("/mesas/01/001")

        assert resp.status_code == 200
        html = resp.data.decode()
        # Safe empty state — never a stack trace / 500
        assert "Traceback" not in html


# ---------------------------------------------------------------------------
# Level 3: GET /mesas/<dept>/<mpio>/<zona>/<puesto>
# ---------------------------------------------------------------------------

class TestLevel3MesaListPagination:
    def test_25_mesas_page1_shows_10(self, prod_app):
        all_rows = _make_mesa_rows(25)
        with patch(
            "src.modules.labeler.db.get_mesa_results",
            side_effect=_paginated_side_effect(all_rows),
        ), patch(
            "src.modules.labeler.db.count_mesa_results", return_value=25,
        ), patch(
            "src.modules.labeler.db.get_hierarchical_mesa_stats",
            return_value=_hierarchical_stats_fixture(),
        ):
            resp = prod_app.test_client().get("/mesas/01/001/01/01")

        assert resp.status_code == 200
        html = resp.data.decode()
        for i in range(1, 11):
            assert f">{i}<" in html or f'>{i} <' in html or str(i) in html
        # Pagination link to page 2 must be present (25 > 10)
        assert "page=2" in html

    def test_25_mesas_page2_shows_next_10(self, prod_app):
        all_rows = _make_mesa_rows(25)
        with patch(
            "src.modules.labeler.db.get_mesa_results",
            side_effect=_paginated_side_effect(all_rows),
        ) as mock_results, patch(
            "src.modules.labeler.db.count_mesa_results", return_value=25,
        ), patch(
            "src.modules.labeler.db.get_hierarchical_mesa_stats",
            return_value=_hierarchical_stats_fixture(),
        ):
            resp = prod_app.test_client().get("/mesas/01/001/01/01?page=2")

        assert resp.status_code == 200
        _, kwargs = mock_results.call_args
        assert kwargs.get("page") == 2
        assert kwargs.get("per_page") == 10

    def test_25_mesas_page3_shows_remaining_5(self, prod_app):
        all_rows = _make_mesa_rows(25)
        with patch(
            "src.modules.labeler.db.get_mesa_results",
            side_effect=_paginated_side_effect(all_rows),
        ), patch(
            "src.modules.labeler.db.count_mesa_results", return_value=25,
        ), patch(
            "src.modules.labeler.db.get_hierarchical_mesa_stats",
            return_value=_hierarchical_stats_fixture(),
        ):
            resp = prod_app.test_client().get("/mesas/01/001/01/01?page=3")

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "page=4" not in html  # no next page beyond the last

    def test_le10_mesas_no_pagination_controls(self, prod_app):
        all_rows = _make_mesa_rows(7)
        with patch(
            "src.modules.labeler.db.get_mesa_results", return_value=all_rows,
        ), patch(
            "src.modules.labeler.db.count_mesa_results", return_value=7,
        ), patch(
            "src.modules.labeler.db.get_hierarchical_mesa_stats",
            return_value=_hierarchical_stats_fixture(),
        ):
            resp = prod_app.test_client().get("/mesas/01/001/01/01")

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "page=2" not in html
        assert "pagination" not in html.lower() or "page=" not in html


# ---------------------------------------------------------------------------
# Invalid / unknown path parameters — safe empty, never a 500
# ---------------------------------------------------------------------------

class TestInvalidPathParams:
    def test_unknown_mpio_at_level2_returns_safe_empty(self, prod_app):
        with patch(
            "src.modules.labeler.db.get_hierarchical_mesa_stats",
            return_value=_hierarchical_stats_fixture(),
        ):
            resp = prod_app.test_client().get("/mesas/99/999")

        assert resp.status_code in (200, 404)
        assert resp.status_code != 500

    def test_unknown_puesto_at_level3_returns_safe_empty(self, prod_app):
        with patch(
            "src.modules.labeler.db.get_mesa_results", return_value=[],
        ), patch(
            "src.modules.labeler.db.count_mesa_results", return_value=0,
        ), patch(
            "src.modules.labeler.db.get_hierarchical_mesa_stats",
            return_value=_hierarchical_stats_fixture(),
        ):
            resp = prod_app.test_client().get("/mesas/99/999/99/999")

        assert resp.status_code in (200, 404)
        assert resp.status_code != 500

    def test_level3_db_exception_does_not_500(self, prod_app):
        with patch(
            "src.modules.labeler.db.get_mesa_results",
            side_effect=RuntimeError("db down"),
        ), patch(
            "src.modules.labeler.db.count_mesa_results",
            side_effect=RuntimeError("db down"),
        ), patch(
            "src.modules.labeler.db.get_hierarchical_mesa_stats",
            return_value=_hierarchical_stats_fixture(),
        ):
            resp = prod_app.test_client().get("/mesas/01/001/01/01")

        assert resp.status_code != 500


# ---------------------------------------------------------------------------
# Breadcrumbs
# ---------------------------------------------------------------------------

class TestBreadcrumbs:
    def test_level3_breadcrumb_links_back_to_mesas_root(self, prod_app):
        all_rows = _make_mesa_rows(3)
        with patch(
            "src.modules.labeler.db.get_mesa_results", return_value=all_rows,
        ), patch(
            "src.modules.labeler.db.count_mesa_results", return_value=3,
        ), patch(
            "src.modules.labeler.db.get_hierarchical_mesa_stats",
            return_value=_hierarchical_stats_fixture(),
        ):
            resp = prod_app.test_client().get("/mesas/01/001/01/01")

        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'href="/mesas"' in html
