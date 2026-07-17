"""Tests for POST /api/mesa-report (SDD: public-mesa-report, Phase 2).

TDD cycle: RED -> GREEN -> REFACTOR.

Mirrors the POST /feedback pattern (@require_auth only, no @require_role,
_verify_recaptcha spam guard) but adds a server-side mesa_result_exists()
anti-forgery check before any insert into the shared transversal_review_reports
table (client-supplied mesa_key must never be trusted directly).
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
def prod_app(tmp_path: Path):
    """Flask app in production mode (SUPABASE_URL set, Supabase mocked)."""
    env_vars = {
        "SUPABASE_URL": "https://fake.supabase.co",
        "SUPABASE_ANON_KEY": "fake-anon-key",
        "SECRET_KEY": "test-secret-key-mesa-report",
        "FLASK_ENV": "production",
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


@pytest.fixture
def mock_auth():
    """Mock decode_jwt so an injected session is treated as authenticated."""
    with patch("src.modules.labeler.auth.decode_jwt") as mock_decode:
        mock_decode.return_value = {"sub": "test-mesa-report-user", "email": "reporter@test.local"}
        yield mock_decode


def _inject_session(client):
    """Set up a session with a fake access token (authenticated user)."""
    with client.session_transaction() as sess:
        sess["access_token"] = "fake-mesa-report-token"
        sess["refresh_token"] = "fake-mesa-report-refresh"


VALID_PAYLOAD = {
    "mesa_key": "01_001_01_01_1",
    "notes": "El acta parece tener un valor tachado en la casilla de votos blancos.",
    "g_recaptcha_response": "fake-token",
}


# ---------------------------------------------------------------------------
# RED — 2.1 unauthenticated rejected
# ---------------------------------------------------------------------------

class TestUnauthenticatedRejected:
    def test_json_request_returns_401(self, client):
        with patch("src.modules.labeler.db.insert_transversal_reports") as mock_insert:
            resp = client.post(
                "/api/mesa-report",
                json=VALID_PAYLOAD,
                headers={"Accept": "application/json"},
            )
        assert resp.status_code == 401
        mock_insert.assert_not_called()

    def test_html_request_redirects_to_login(self, client):
        with patch("src.modules.labeler.db.insert_transversal_reports") as mock_insert:
            resp = client.post(
                "/api/mesa-report",
                json=VALID_PAYLOAD,
                headers={"Accept": "text/html"},
            )
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers.get("Location", "")
        mock_insert.assert_not_called()


# ---------------------------------------------------------------------------
# RED — 2.2 missing notes rejected
# ---------------------------------------------------------------------------

class TestMissingNotesRejected:
    def test_empty_notes_rejected(self, client, mock_auth):
        _inject_session(client)
        payload = dict(VALID_PAYLOAD, notes="")
        with patch("src.modules.labeler.db.mesa_result_exists", return_value=True), \
             patch("src.modules.labeler.db.insert_transversal_reports") as mock_insert:
            resp = client.post("/api/mesa-report", json=payload, headers={"Accept": "application/json"})
        assert resp.status_code == 400
        mock_insert.assert_not_called()

    def test_whitespace_only_notes_rejected(self, client, mock_auth):
        _inject_session(client)
        payload = dict(VALID_PAYLOAD, notes="   ")
        with patch("src.modules.labeler.db.mesa_result_exists", return_value=True), \
             patch("src.modules.labeler.db.insert_transversal_reports") as mock_insert:
            resp = client.post("/api/mesa-report", json=payload, headers={"Accept": "application/json"})
        assert resp.status_code == 400
        mock_insert.assert_not_called()

    def test_missing_notes_field_rejected(self, client, mock_auth):
        _inject_session(client)
        payload = {"mesa_key": VALID_PAYLOAD["mesa_key"], "g_recaptcha_response": "fake-token"}
        with patch("src.modules.labeler.db.mesa_result_exists", return_value=True), \
             patch("src.modules.labeler.db.insert_transversal_reports") as mock_insert:
            resp = client.post("/api/mesa-report", json=payload, headers={"Accept": "application/json"})
        assert resp.status_code == 400
        mock_insert.assert_not_called()


# ---------------------------------------------------------------------------
# RED — 2.3 forged mesa_key rejected (anti-forgery guard)
# ---------------------------------------------------------------------------

class TestForgedMesaKeyRejected:
    def test_unknown_mesa_key_rejected(self, client, mock_auth):
        _inject_session(client)
        with patch("src.modules.labeler.db.mesa_result_exists", return_value=False) as mock_exists, \
             patch("src.modules.labeler.db.insert_transversal_reports") as mock_insert:
            resp = client.post("/api/mesa-report", json=VALID_PAYLOAD, headers={"Accept": "application/json"})
        assert resp.status_code == 400
        body = resp.get_json(silent=True) or {}
        assert body.get("error") == "unknown_mesa"
        mock_exists.assert_called_once_with(VALID_PAYLOAD["mesa_key"])
        mock_insert.assert_not_called()


# ---------------------------------------------------------------------------
# RED — 2.4 reCAPTCHA failure rejected
# ---------------------------------------------------------------------------

class TestRecaptchaFailureRejected:
    def test_recaptcha_failure_returns_403(self, client, mock_auth):
        _inject_session(client)
        with patch("src.modules.labeler.server._verify_recaptcha", return_value=False), \
             patch("src.modules.labeler.db.mesa_result_exists") as mock_exists, \
             patch("src.modules.labeler.db.insert_transversal_reports") as mock_insert:
            resp = client.post("/api/mesa-report", json=VALID_PAYLOAD, headers={"Accept": "application/json"})
        assert resp.status_code == 403
        mock_exists.assert_not_called()
        mock_insert.assert_not_called()


# ---------------------------------------------------------------------------
# RED — 2.5 valid submit inserts row
# ---------------------------------------------------------------------------

class TestValidSubmitInsertsRow:
    def test_happy_path_inserts_and_returns_ok(self, client, mock_auth):
        _inject_session(client)
        with patch("src.modules.labeler.server._verify_recaptcha", return_value=True), \
             patch("src.modules.labeler.db.mesa_result_exists", return_value=True), \
             patch("src.modules.labeler.db.insert_transversal_reports", return_value=[{"id": "row-1"}]) as mock_insert:
            resp = client.post("/api/mesa-report", json=VALID_PAYLOAD, headers={"Accept": "application/json"})

        assert resp.status_code == 200
        body = resp.get_json()
        assert body["ok"] is True

        mock_insert.assert_called_once_with(
            VALID_PAYLOAD["mesa_key"],
            [{"source": "e14c", "report_type": "otro", "notes": VALID_PAYLOAD["notes"]}],
            "test-mesa-report-user",
        )
