"""Tests for /api/transversal/* routes (T9)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

LAB_DIR = Path(__file__).resolve().parent / (
    "Laboratorio/analisis_transversal/E14C_conflictivas_pendientes"
)


@pytest.fixture
def prod_app(tmp_path):
    env_vars = {
        "SUPABASE_URL": "https://fake.supabase.co",
        "SUPABASE_ANON_KEY": "fake-anon-key",
        "SECRET_KEY": "test-secret-transversal",
        "TRANSVERSAL_LAB_MESAS_DIR": str(LAB_DIR / "mesas"),
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
        sess["refresh_token"] = "fake-refresh"
    return c


def _auth_as(role: str):
    return (
        patch("src.modules.labeler.auth.decode_jwt", return_value={"sub": "test-user"}),
        patch("src.modules.labeler.auth._get_user_role", return_value=role),
    )


class TestTransversalRoutesAuth:
    def test_moderator_queue_200(self, client):
        with patch("src.modules.labeler.auth.decode_jwt", return_value={"sub": "mod-user"}), \
             patch("src.modules.labeler.auth._get_user_role", return_value="moderator"), \
             patch("src.modules.labeler.db.get_transversal_decided_slots", return_value={}), \
             patch("src.modules.labeler.db.get_transversal_decisions", return_value={}):
            resp = client.get("/api/transversal/queue?page_size=5")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "items" in data
        assert "total" in data
        assert data["total"] >= 4117
        assert "pending" in data
        assert "has_more" in data
        assert "queue_mesas" in data
        assert "done" in data
        assert data["has_more"] is True

    def test_queue_uses_scoped_decisions_for_page_keys(self, client):
        with patch("src.modules.labeler.auth.decode_jwt", return_value={"sub": "mod-user"}), \
             patch("src.modules.labeler.auth._get_user_role", return_value="moderator"), \
             patch("src.modules.labeler.db.get_transversal_decided_slots", return_value={}) as mock_slots, \
             patch("src.modules.labeler.db.get_transversal_decisions", return_value={}) as mock_scoped:
            resp = client.get("/api/transversal/queue?page=1&page_size=3")
        assert resp.status_code == 200
        mock_slots.assert_called_once()
        mock_scoped.assert_called_once()
        page_keys = mock_scoped.call_args.kwargs.get("mesa_keys") or mock_scoped.call_args.args
        if mock_scoped.call_args.kwargs:
            assert len(mock_scoped.call_args.kwargs["mesa_keys"]) == 3
        else:
            assert len(page_keys) == 3

    def test_validator_queue_403(self, client):
        with patch("src.modules.labeler.auth.decode_jwt", return_value={"sub": "val-user"}), \
             patch("src.modules.labeler.auth._get_user_role", return_value="validator"):
            resp = client.get("/api/transversal/queue")
        assert resp.status_code == 403

    def test_moderator_transversal_page_200(self, client):
        with patch("src.modules.labeler.auth.decode_jwt", return_value={"sub": "mod-user"}), \
             patch("src.modules.labeler.auth._get_user_role", return_value="moderator"):
            resp = client.get("/admin/transversal")
        assert resp.status_code == 200
        assert b"Revisi" in resp.data

    def test_moderator_export_200(self, client):
        export_payload = {
            "generated": "2026-07-14T12:00:00Z",
            "project": "transversal_review_E14C_conflictivas",
            "decisions": {},
        }
        with patch("src.modules.labeler.auth.decode_jwt", return_value={"sub": "mod-user"}), \
             patch("src.modules.labeler.auth._get_user_role", return_value="moderator"), \
             patch("src.modules.labeler.db.export_transversal_decisions", return_value=export_payload):
            resp = client.get("/api/transversal/decisions/export")
        assert resp.status_code == 200
        assert resp.mimetype == "application/json"
        assert "attachment" in resp.headers.get("Content-Disposition", "")

    def test_moderator_post_decision(self, client):
        with patch("src.modules.labeler.auth.decode_jwt", return_value={"sub": "mod-user"}), \
             patch("src.modules.labeler.auth._get_user_role", return_value="moderator"), \
             patch("src.modules.labeler.db.upsert_transversal_decision", return_value=True) as mock_upsert:
            resp = client.post(
                "/api/transversal/decisions",
                data=json.dumps({
                    "mesa_key": "01_001_001_01_001",
                    "field": "SUMA_TOTAL",
                    "source": "e14c",
                    "decision": "accepted",
                }),
                content_type="application/json",
            )
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        mock_upsert.assert_called_once()

    def test_moderator_reopen_decisions(self, client):
        with patch("src.modules.labeler.auth.decode_jwt", return_value={"sub": "mod-user"}), \
             patch("src.modules.labeler.auth._get_user_role", return_value="moderator"), \
             patch("src.modules.labeler.db.reopen_transversal_decisions", return_value=(True, None)) as mock_reopen:
            resp = client.post(
                "/api/transversal/decisions/reopen",
                data=json.dumps({"mesa_key": "01_001_026_08_011"}),
                content_type="application/json",
            )
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        mock_reopen.assert_called_once_with("01_001_026_08_011")

    def test_reopen_expired_returns_403(self, client):
        with patch("src.modules.labeler.auth.decode_jwt", return_value={"sub": "mod-user"}), \
             patch("src.modules.labeler.auth._get_user_role", return_value="moderator"), \
             patch("src.modules.labeler.db.reopen_transversal_decisions", return_value=(False, "edit_window_expired")):
            resp = client.post(
                "/api/transversal/decisions/reopen",
                data=json.dumps({"mesa_key": "01_001_026_08_011"}),
                content_type="application/json",
            )
        assert resp.status_code == 403
        assert resp.get_json()["error"] == "edit_window_expired"