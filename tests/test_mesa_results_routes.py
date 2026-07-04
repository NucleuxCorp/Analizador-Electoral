"""Tests for /admin/mesas/data and /admin/mesas/stats routes (T2-1, T2-2).

TDD cycle: RED → GREEN → REFACTOR.
All tests use the Flask test client + mocked auth + mocked db accessors.
No live Supabase connection is required.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

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
        "SECRET_KEY": "test-secret-for-mesa-routes",
    }
    with patch.dict(os.environ, env_vars):
        from src.modules.labeler.server import create_app
        index_path = tmp_path / "crops" / "index.jsonl"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        app = create_app(index_path=index_path, labels_dir=tmp_path)
        app.config["TESTING"] = True
        yield app


@pytest.fixture
def auth_client(prod_app):
    """Test client with a faked valid session (admin role bypassed)."""
    client = prod_app.test_client()
    with client.session_transaction() as sess:
        sess["access_token"] = "fake-valid-token"
        sess["refresh_token"] = "fake-refresh"
    return client


def _auth_patches():
    """Context-manager stack that bypasses require_auth + require_role."""
    return (
        patch("src.modules.labeler.auth.decode_jwt", return_value={"sub": "admin-test-user"}),
        patch("src.modules.labeler.auth._get_user_role", return_value="admin"),
    )


# ---------------------------------------------------------------------------
# T2-1 (RED): GET /admin/mesas/data
# ---------------------------------------------------------------------------

class TestMesasDataRoute:
    """Route GET /admin/mesas/data returns correct JSON shape."""

    def test_returns_json_with_rows_page_total_keys(self, prod_app, auth_client):
        """Happy path: response has rows, page, total keys."""
        rows = [
            {"mesa_key": "01_001_01_01_1", "dept": "01", "overall_status": "clean"},
            {"mesa_key": "01_001_01_01_2", "dept": "01", "overall_status": "critical"},
        ]
        stats = {"01": {"clean": 10, "critical": 5, "total": 15}, "_global": {"total": 15}}

        with patch("src.modules.labeler.auth.decode_jwt", return_value={"sub": "admin-test-user"}), \
             patch("src.modules.labeler.auth._get_user_role", return_value="admin"), \
             patch("src.modules.labeler.db.get_mesa_results", return_value=rows) as mock_results, \
             patch("src.modules.labeler.db.get_mesa_stats", return_value=stats) as mock_stats:
            resp = auth_client.get("/admin/mesas/data")

        assert resp.status_code == 200
        assert resp.content_type.startswith("application/json")
        data = resp.get_json()
        assert "rows" in data
        assert "page" in data
        assert "total" in data

    def test_passes_dept_and_status_to_get_mesa_results(self, prod_app, auth_client):
        """Query params dept and status are forwarded to get_mesa_results."""
        rows = [{"mesa_key": "05_001_01_01_1", "dept": "05", "overall_status": "critical"}]
        stats = {"05": {"critical": 1, "total": 1}, "_global": {"total": 1}}

        with patch("src.modules.labeler.auth.decode_jwt", return_value={"sub": "admin-test-user"}), \
             patch("src.modules.labeler.auth._get_user_role", return_value="admin"), \
             patch("src.modules.labeler.db.get_mesa_results", return_value=rows) as mock_results, \
             patch("src.modules.labeler.db.get_mesa_stats", return_value=stats):
            resp = auth_client.get("/admin/mesas/data?dept=05&status=critical")

        assert resp.status_code == 200
        mock_results.assert_called_once()
        call_kwargs = mock_results.call_args
        # Verify dept and status were passed correctly (positional or keyword)
        args, kwargs = call_kwargs
        assert "05" in args or kwargs.get("dept") == "05"
        assert "critical" in args or kwargs.get("status") == "critical"

    def test_page_param_forwarded_to_get_mesa_results(self, prod_app, auth_client):
        """Query param page is forwarded to get_mesa_results as integer."""
        rows = []
        stats = {"_global": {"total": 200}}

        with patch("src.modules.labeler.auth.decode_jwt", return_value={"sub": "admin-test-user"}), \
             patch("src.modules.labeler.auth._get_user_role", return_value="admin"), \
             patch("src.modules.labeler.db.get_mesa_results", return_value=rows) as mock_results, \
             patch("src.modules.labeler.db.get_mesa_stats", return_value=stats):
            resp = auth_client.get("/admin/mesas/data?page=3")

        assert resp.status_code == 200
        _, kwargs = mock_results.call_args
        assert kwargs.get("page") == 3 or 3 in mock_results.call_args[0]

    def test_returns_empty_on_db_exception(self, prod_app, auth_client):
        """When get_mesa_results raises, route returns {rows:[], page:1, total:0}."""
        with patch("src.modules.labeler.auth.decode_jwt", return_value={"sub": "admin-test-user"}), \
             patch("src.modules.labeler.auth._get_user_role", return_value="admin"), \
             patch("src.modules.labeler.db.get_mesa_results", side_effect=Exception("db down")), \
             patch("src.modules.labeler.db.get_mesa_stats", side_effect=Exception("db down")):
            resp = auth_client.get("/admin/mesas/data")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data == {"rows": [], "page": 1, "total": 0}

    def test_total_comes_from_get_mesa_stats_global(self, prod_app, auth_client):
        """total field in response comes from get_mesa_stats()['_global']['total']."""
        rows = [{"mesa_key": "01_001_01_01_1", "dept": "01", "overall_status": "clean"}]
        stats = {"_global": {"total": 42}}

        with patch("src.modules.labeler.auth.decode_jwt", return_value={"sub": "admin-test-user"}), \
             patch("src.modules.labeler.auth._get_user_role", return_value="admin"), \
             patch("src.modules.labeler.db.get_mesa_results", return_value=rows), \
             patch("src.modules.labeler.db.get_mesa_stats", return_value=stats):
            resp = auth_client.get("/admin/mesas/data")

        data = resp.get_json()
        assert data["total"] == 42

    def test_page_value_reflected_in_response(self, prod_app, auth_client):
        """The page query param value is echoed in the response page field."""
        stats = {"_global": {"total": 0}}
        with patch("src.modules.labeler.auth.decode_jwt", return_value={"sub": "admin-test-user"}), \
             patch("src.modules.labeler.auth._get_user_role", return_value="admin"), \
             patch("src.modules.labeler.db.get_mesa_results", return_value=[]), \
             patch("src.modules.labeler.db.get_mesa_stats", return_value=stats):
            resp = auth_client.get("/admin/mesas/data?page=5")

        data = resp.get_json()
        assert data["page"] == 5


# ---------------------------------------------------------------------------
# T2-2 (RED): GET /admin/mesas/stats
# ---------------------------------------------------------------------------

class TestMesasStatsRoute:
    """Route GET /admin/mesas/stats returns the dict from get_mesa_stats."""

    def test_returns_dict_from_get_mesa_stats(self, prod_app, auth_client):
        """Happy path: response is the dict returned by get_mesa_stats()."""
        stats = {
            "01": {"clean": 100, "critical": 5, "known_anomaly": 2, "warning": 3, "discrepancy": 1, "total": 111},
            "_global": {"clean": 100, "critical": 5, "known_anomaly": 2, "warning": 3, "discrepancy": 1, "total": 111},
        }
        with patch("src.modules.labeler.auth.decode_jwt", return_value={"sub": "admin-test-user"}), \
             patch("src.modules.labeler.auth._get_user_role", return_value="admin"), \
             patch("src.modules.labeler.db.get_mesa_stats", return_value=stats) as mock_stats:
            resp = auth_client.get("/admin/mesas/stats")

        assert resp.status_code == 200
        assert resp.content_type.startswith("application/json")
        data = resp.get_json()
        assert "01" in data
        assert "_global" in data
        assert data["_global"]["total"] == 111
        mock_stats.assert_called_once()

    def test_returns_empty_dict_on_exception(self, prod_app, auth_client):
        """When get_mesa_stats raises, route returns {}."""
        with patch("src.modules.labeler.auth.decode_jwt", return_value={"sub": "admin-test-user"}), \
             patch("src.modules.labeler.auth._get_user_role", return_value="admin"), \
             patch("src.modules.labeler.db.get_mesa_stats", side_effect=Exception("db down")):
            resp = auth_client.get("/admin/mesas/stats")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data == {}

    def test_content_type_is_json(self, prod_app, auth_client):
        """Content-Type header must be application/json."""
        with patch("src.modules.labeler.auth.decode_jwt", return_value={"sub": "admin-test-user"}), \
             patch("src.modules.labeler.auth._get_user_role", return_value="admin"), \
             patch("src.modules.labeler.db.get_mesa_stats", return_value={}):
            resp = auth_client.get("/admin/mesas/stats")

        assert "application/json" in resp.content_type
