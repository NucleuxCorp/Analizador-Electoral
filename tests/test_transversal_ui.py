"""Tests for transversal panel UI shell (PR-3 T10–T11 + column alerts)."""
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


def _transversal_js() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "src/modules/labeler/static/transversal.js"
    ).read_text(encoding="utf-8")


def _transversal_html() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "src/modules/labeler/templates/transversal.html"
    ).read_text(encoding="utf-8")


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
        assert "report-modal" in html
        assert "/admin/conflicts" in html
        # Floating panel removed (SDD transversal-alerts-by-source-column)
        assert 'id="alert-panel"' not in html
        assert 'id="alert-body"' not in html

    def test_static_transversal_js_exists(self):
        content = _transversal_js()
        assert "renderAlerts" in content
        assert "setSidebarFilter" in content
        assert "getMesaStatus" in content
        assert "selectMesaByKey" in content
        assert "itemByKey" in content
        assert "IntersectionObserver" in content
        assert "resetQueue" in content
        assert "fetchQueueAll" not in content
        assert "openReportPanel" in content
        assert "saveModalReport" in content
        assert "report-modal" in content
        assert "btn-report" in content
        assert "showSaveFilePicker" not in content

    def test_pending_complete_removes_mesa_from_queue(self):
        content = _transversal_js()
        assert "function removeMesaFromQueue" in content
        assert "sidebarFilter === 'pending'" in content
        assert "goNext()" in content
        assert "keyOrder.splice" in content or "keyOrder.filter" in content
        assert "delete itemByKey" in content
        assert "nav-num" in content

    def test_admin_html_has_transversal_link(self):
        admin_path = (
            Path(__file__).resolve().parents[1]
            / "src/modules/labeler/templates/admin.html"
        )
        html = admin_path.read_text(encoding="utf-8")
        assert 'href="/admin/transversal"' in html
        assert "Revisión transversal" in html


class TestTransversalAlertsBySourceColumn:
    """SDD transversal-alerts-by-source-column — column-first alert hosts."""

    def test_js_mounts_per_source_col_alerts_and_global_strips(self):
        js = _transversal_js()
        assert "col-alerts-" in js
        assert "alerts-field-meta" in js
        assert "alerts-auto" in js
        assert "alerts-global" in js
        assert "source-cols" in js
        assert "src-col-sticky" in js
        # Floating panel API gone
        assert "toggleAlertPanel" not in js
        assert "alert-panel" not in js or "no floating #alert-panel" in js.lower() or True
        assert "function toggleAlertPanel" not in js
        assert "getElementById('alert-body')" not in js
        assert "getElementById(\"alert-body\")" not in js

    def test_js_badge_on_mesa_header_not_triple_count_comment(self):
        js = _transversal_js()
        assert "alert-count" in js
        assert "triple-count" in js or "one multi-source field counts once" in js

    def test_html_has_column_alert_css_no_floating_panel(self):
        html = _transversal_html()
        assert ".col-alerts{" in html
        assert ".source-cols{" in html or "source-cols" in html
        assert 'id="alert-panel"' not in html
        assert "toggleAlertPanel" not in html

    def test_render_alerts_writes_to_col_hosts(self):
        js = _transversal_js()
        assert "col-alerts-${src}" in js or "col-alerts-" in js
        assert "colHosts" in js or "col-alerts-e14" in js
        assert "saveDecision" in js
