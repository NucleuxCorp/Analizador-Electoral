"""Tests for transversal panel UI shell (PR-3 T10–T11)."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def prod_app(tmp_path):
    env_vars = {
        "SUPABASE_URL": "https://fake.supabase.co",
        "SUPABASE_ANON_KEY": "fake-anon-key",
        "SECRET_KEY": "test-secret-transversal-ui",
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
    c = prod_app.test_client()
    with c.session_transaction() as sess:
        sess["access_token"] = "fake-token"
    return c


class TestTransversalUI:
    def test_transversal_html_loads_js_and_stats(self, client):
        with patch("src.modules.labeler.auth.decode_jwt", return_value={"sub": "mod"}), \
             patch("src.modules.labeler.auth._get_user_role", return_value="moderator"):
            resp = client.get("/admin/transversal")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert "transversal.js" in html
        assert "stat-total" in html
        assert "stat-done" in html
        assert "sf-pending" in html
        assert "Revisadas" in html
        assert "/admin/conflicts" in html

    def test_static_transversal_js_exists(self):
        js_path = Path(__file__).resolve().parents[1] / "src/modules/labeler/static/transversal.js"
        assert js_path.exists()
        content = js_path.read_text(encoding="utf-8")
        assert "renderAlerts" in content
        assert "setSidebarFilter" in content
        assert "getMesaStatus" in content
        assert "/api/transversal/decisions/export" in content
        assert "showSaveFilePicker" not in content

    def test_admin_html_has_transversal_link(self):
        admin_path = Path(__file__).resolve().parents[1] / "src/modules/labeler/templates/admin.html"
        html = admin_path.read_text(encoding="utf-8")
        assert 'href="/admin/transversal"' in html
        assert "Revisión transversal" in html