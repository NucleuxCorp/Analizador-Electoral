"""
tests/labeler/test_maintenance_mode.py — Maintenance guard + landing page tests.

Covers the PR-A behavior: when MAINTENANCE_MODE=true, non-whitelisted routes
are blocked by a 503 maintenance response, while /status and /auth/register
remain available. The guard is off by default.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# App fixture (production mode — SUPABASE_URL set, mocked Supabase)
# ---------------------------------------------------------------------------

@pytest.fixture
def prod_app(tmp_path):
    """
    Create a Flask app in production mode with mocked Supabase client.

    Uses tmp_path as labels_dir; SUPABASE_URL is set to a fake value so
    the app enters production mode, but all Supabase calls are mocked.
    """
    env_vars = {
        "SUPABASE_URL": "https://fake.supabase.co",
        "SUPABASE_ANON_KEY": "fake-anon-key",
        "SECRET_KEY": "test-secret-key-for-maintenance",
    }
    with patch.dict(os.environ, env_vars):
        from src.modules.labeler.server import create_app
        index_path = tmp_path / "crops" / "index.jsonl"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        app = create_app(index_path=index_path, labels_dir=tmp_path)
        app.config["TESTING"] = True
        yield app


@pytest.fixture
def client(prod_app):
    return prod_app.test_client()


# ---------------------------------------------------------------------------
# Maintenance mode tests
# ---------------------------------------------------------------------------

class TestMaintenanceMode:
    def test_status_200_under_maintenance(self, client, monkeypatch):
        """GET /status must remain available when maintenance mode is on."""
        monkeypatch.setenv("MAINTENANCE_MODE", "true")

        # Mock the module-level Supabase client used by db._client().
        mock_client = MagicMock()
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.gt.return_value = chain
        chain.execute.return_value = MagicMock(count=0, data=[])
        mock_client.table.return_value = chain
        rpc_result = MagicMock()
        rpc_result.data = 0
        mock_client.rpc.return_value.execute.return_value = rpc_result

        with patch("src.modules.labeler.db.supabase", mock_client):
            resp = client.get("/status")

        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, dict)
        assert "labeled" in data or "error" in data

    def test_work_route_returns_landing(self, client, monkeypatch):
        """Non-whitelisted HTML routes must return the maintenance landing page (503)."""
        monkeypatch.setenv("MAINTENANCE_MODE", "true")

        resp = client.get("/", headers={"Accept": "text/html"})

        assert resp.status_code == 503
        body = resp.get_data(as_text=True)
        assert "Verificación Ciudadana de Actas" in body or "mantenimiento" in body.lower()
        assert resp.headers.get("X-Request-ID")

    def test_json_client_gets_503(self, client, monkeypatch):
        """JSON clients must receive a 503 maintenance JSON response."""
        monkeypatch.setenv("MAINTENANCE_MODE", "true")

        resp = client.get("/", headers={"Accept": "application/json"})

        assert resp.status_code == 503
        data = resp.get_json()
        assert data.get("ok") is False
        assert data.get("error") == "maintenance_mode"

    def test_register_allowed_under_maintenance(self, client, monkeypatch):
        """GET and POST /auth/register must remain available during maintenance."""
        monkeypatch.setenv("MAINTENANCE_MODE", "true")

        get_resp = client.get("/auth/register")
        assert get_resp.status_code == 200

        mock_supabase = MagicMock()
        mock_supabase.auth.sign_up.return_value = MagicMock()

        with patch("src.modules.labeler.auth.init_supabase_client", return_value=mock_supabase):
            post_resp = client.post(
                "/auth/register",
                json={"email": "new@example.com", "password": "password123"},
            )

        assert post_resp.status_code in (200, 201)

    def test_login_blocked_under_maintenance(self, client, monkeypatch):
        """POST /auth/login must be blocked by the maintenance guard."""
        monkeypatch.setenv("MAINTENANCE_MODE", "true")

        resp = client.post(
            "/auth/login",
            json={"email": "user@example.com", "password": "password123"},
        )

        assert resp.status_code == 503
        data = resp.get_json()
        assert data.get("ok") is False
        assert data.get("error") == "maintenance_mode"

    def test_guard_off_by_default(self, client, monkeypatch):
        """With MAINTENANCE_MODE unset, normal routing applies."""
        monkeypatch.delenv("MAINTENANCE_MODE", raising=False)

        resp = client.get("/")
        assert resp.status_code != 503
        assert "maintenance" not in resp.get_data(as_text=True).lower()

    def test_landing_has_h1(self, client, monkeypatch):
        """The maintenance landing page must contain an <h1> heading."""
        monkeypatch.setenv("MAINTENANCE_MODE", "true")

        resp = client.get("/", headers={"Accept": "text/html"})
        body = resp.get_data(as_text=True)

        assert resp.status_code == 503
        assert "<h1" in body

    def test_landing_reuses_existing_tutorial_embed(self, client, monkeypatch):
        """The landing page must reuse the existing Loom tutorial iframe."""
        monkeypatch.setenv("MAINTENANCE_MODE", "true")

        resp = client.get("/", headers={"Accept": "text/html"})
        body = resp.get_data(as_text=True)

        assert resp.status_code == 503
        assert '<iframe' in body
        assert "loom.com/embed/40f6d1a46315479f969c1f6296b1624b" in body
