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
import re
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
        mun_idx = html.find("<th>Municipio</th>")
        dept_idx = html.find("<th>Departamento</th>")
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

    def test_multiple_municipios_show_correct_per_row_totals(self, prod_app):
        """Two municipios in by_mpio must each render their own semaphore
        totals — no cross-row bleed or dropped counts."""
        stats = _hierarchical_stats_fixture()
        stats["by_mpio"]["01_002"] = _empty_bucket(
            dept="01", mpio="002", clean=1, known_anomaly=3,
            total=4, en_revision=2, revisada=0,
        )
        with patch(
            "src.modules.labeler.db.get_hierarchical_mesa_stats",
            return_value=stats,
        ):
            resp = prod_app.test_client().get("/mesas")

        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'href="/mesas/01/001"' in html
        assert 'href="/mesas/01/002"' in html

        def _row_totals(mpio_href: str) -> str:
            row = re.search(
                rf'<tr>\s*<td class="row-name"><a href="{re.escape(mpio_href)}">.*?</tr>',
                html, re.DOTALL,
            )
            assert row is not None, f"no row found for {mpio_href}"
            return row.group(0)

        row_001 = _row_totals("/mesas/01/001")
        row_002 = _row_totals("/mesas/01/002")
        assert re.search(r'class="num total">\s*6\s*<', row_001)
        assert re.search(r'class="num total">\s*4\s*<', row_002)

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
        rendered_mesas = re.findall(r'<td class="row-name">(\d+)</td>', html)
        assert rendered_mesas == [str(i) for i in range(1, 11)]
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
        rendered_mesas = re.findall(r'<td class="row-name">(\d+)</td>', html)
        assert rendered_mesas == [str(i) for i in range(21, 26)]
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

    def test_mesa_with_algorithm_alert_and_review_both_shown(self, prod_app):
        """A mesa that is BOTH an algorithm alert AND under human review must
        show both indicators — neither should overwrite or suppress the other."""
        mesa_rows = [{
            "mesa_key": "01_001_01_01_1",
            "mesa": "1",
            "dept": "01", "mpio": "001", "zona": "01", "puesto": "01",
            "overall_status": "known_anomaly",
        }]
        stats = _hierarchical_stats_fixture()
        stats["review_by_mesa"] = {
            "01_001_01_01_1": {
                "en_revision": True, "revisada": False, "revisada_result": None,
            },
        }
        with patch(
            "src.modules.labeler.db.get_mesa_results", return_value=mesa_rows,
        ), patch(
            "src.modules.labeler.db.count_mesa_results", return_value=1,
        ), patch(
            "src.modules.labeler.db.get_hierarchical_mesa_stats",
            return_value=stats,
        ):
            resp = prod_app.test_client().get("/mesas/01/001/01/01")

        assert resp.status_code == 200
        html = resp.data.decode()
        # The single mesa row must show BOTH the algorithm alert (🔴, from
        # overall_status) and the human-review flag (🟡, from review_by_mesa)
        # — neither indicator should overwrite or suppress the other.
        row_match = re.search(
            r'<td class="row-name">1</td>\s*<td>([^<]+)</td>\s*<td>([^<]+)</td>',
            html,
        )
        assert row_match is not None
        assert row_match.group(1).strip() == "🔴"
        assert row_match.group(2).strip() == "🟡"


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

    def test_level3_db_exception_shows_unavailable_not_empty(self, prod_app):
        """A backend failure must render the unavailable fallback, not the
        'no mesas here' empty-state — otherwise a transient outage looks
        identical to a legitimately empty puesto."""
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
        html = resp.data.decode()
        assert "Datos no disponibles temporalmente" in html
        assert "No hay mesas analizadas" not in html


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


# ---------------------------------------------------------------------------
# Level 1 pagination + department filter + DIVIPOLE puesto names
# ---------------------------------------------------------------------------

def _many_municipio_stats(n: int, dept: str = "01") -> dict:
    """n municipio buckets under one dept for pagination tests."""
    by_mpio = {}
    for i in range(1, n + 1):
        mpio = f"{i:03d}"
        by_mpio[f"{dept}_{mpio}"] = _empty_bucket(
            dept=dept, mpio=mpio, clean=1, total=1,
        )
    return {
        "by_mpio": by_mpio,
        "by_puesto": {},
        "_global": _empty_bucket(clean=n, total=n),
        "review_by_mesa": {},
    }


class TestLevel1PaginationAndDeptFilter:
    def test_l1_paginates_when_more_than_page_size(self, prod_app):
        stats = _many_municipio_stats(55)
        with patch(
            "src.modules.labeler.db.get_hierarchical_mesa_stats",
            return_value=stats,
        ):
            resp = prod_app.test_client().get("/mesas")

        assert resp.status_code == 200
        html = resp.data.decode()
        # Page 1 shows first 50 only
        assert html.count('href="/mesas/01/') == 50
        assert "page=2" in html
        assert "Página 1 de 2" in html

    def test_l1_page2_shows_remaining(self, prod_app):
        stats = _many_municipio_stats(55)
        with patch(
            "src.modules.labeler.db.get_hierarchical_mesa_stats",
            return_value=stats,
        ):
            resp = prod_app.test_client().get("/mesas?page=2")

        assert resp.status_code == 200
        html = resp.data.decode()
        assert html.count('href="/mesas/01/') == 5
        assert "Página 2 de 2" in html

    def test_l1_dept_filter_limits_rows(self, prod_app):
        stats = _hierarchical_stats_fixture()
        stats["by_mpio"]["05_001"] = _empty_bucket(
            dept="05", mpio="001", clean=2, total=2,
        )
        with patch(
            "src.modules.labeler.db.get_hierarchical_mesa_stats",
            return_value=stats,
        ):
            resp = prod_app.test_client().get("/mesas?dept=01")

        assert resp.status_code == 200
        html = resp.data.decode()
        assert 'href="/mesas/01/001"' in html
        assert 'href="/mesas/05/001"' not in html
        assert 'name="dept"' in html
        assert 'value="01"' in html

    def test_l1_pagination_preserves_dept_filter(self, prod_app):
        stats = _many_municipio_stats(55, dept="01")
        with patch(
            "src.modules.labeler.db.get_hierarchical_mesa_stats",
            return_value=stats,
        ):
            resp = prod_app.test_client().get("/mesas?dept=01&page=2")

        html = resp.data.decode()
        assert "dept=01" in html
        assert "page=1" in html  # back link keeps filter


class TestLevel2PaginationAndPuestoNames:
    def test_l2_paginates_many_puestos(self, prod_app):
        stats = _hierarchical_stats_fixture()
        puestos = {}
        for i in range(1, 56):
            key = f"01_{i:02d}"
            puestos[key] = _empty_bucket(
                dept="01", mpio="001", zona="01", puesto=f"{i:02d}",
                clean=1, total=1,
            )
        stats["by_puesto"]["01_001"] = puestos
        with patch(
            "src.modules.labeler.db.get_hierarchical_mesa_stats",
            return_value=stats,
        ):
            resp = prod_app.test_client().get("/mesas/01/001")

        html = resp.data.decode()
        assert "page=2" in html
        assert "Página 1 de 2" in html

    def test_puesto_name_from_divipole_not_raw_code(self, prod_app):
        """DIVIPOLE nombre is shown instead of the puesto code when available."""
        import src.modules.labeler.server as server_mod

        fake_divipole = {
            "01": {
                "municipios": {
                    "001": {
                        "nombre": "MEDELLIN",
                        "zonas": {
                            "01": {
                                "puestos": {
                                    "01": {"nombre": "SEC. ESC. LA ESPERANZA No 2"},
                                    "02": {"nombre": "INST.EDUC. LA CANDELARIA"},
                                }
                            }
                        },
                    }
                }
            }
        }
        with patch.object(server_mod, "_DIVIPOLE", fake_divipole), patch(
            "src.modules.labeler.db.get_hierarchical_mesa_stats",
            return_value=_hierarchical_stats_fixture(),
        ):
            resp = prod_app.test_client().get("/mesas/01/001")

        html = resp.data.decode()
        assert "SEC. ESC. LA ESPERANZA No 2" in html
        assert "INST.EDUC. LA CANDELARIA" in html

    def test_puesto_name_resolves_unpadded_codes(self, prod_app):
        """mesa_results codes like '1' still resolve DIVIPOLE '01' names."""
        import src.modules.labeler.server as server_mod

        stats = {
            "by_mpio": {
                "1_1": _empty_bucket(dept="1", mpio="1", clean=1, total=1),
            },
            "by_puesto": {
                "1_1": {
                    "1_1": _empty_bucket(
                        dept="1", mpio="1", zona="1", puesto="1",
                        clean=1, total=1,
                    ),
                },
            },
            "_global": _empty_bucket(clean=1, total=1),
            "review_by_mesa": {},
        }
        fake_divipole = {
            "01": {
                "municipios": {
                    "001": {
                        "nombre": "MEDELLIN",
                        "zonas": {
                            "01": {
                                "puestos": {
                                    "01": {"nombre": "COLEGIO EJEMPLO"},
                                }
                            }
                        },
                    }
                }
            }
        }
        with patch.object(server_mod, "_DIVIPOLE", fake_divipole), patch(
            "src.modules.labeler.db.get_hierarchical_mesa_stats",
            return_value=stats,
        ):
            resp = prod_app.test_client().get("/mesas/1/1")

        html = resp.data.decode()
        assert "COLEGIO EJEMPLO" in html
