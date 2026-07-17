"""
tests/test_admin_user_roles.py — Admin user list + role-change panel (admin-user-roles, PR2).

Covers:
  Phase 2 — auth.py foundation: NON_ADMIN_ROLES frozenset, evict_role_cache().
  Phase 3 — GET /admin/users: list view (fields present, admin rows read-only-eligible,
            access gate for non-admin).
  Phase 4 — POST /admin/users/role: self-change reject, peer-admin-target reject,
            admin-self as a DISTINCT guard, whitelist/escalation reject, mass-assignment
            ignored, happy path, cache eviction, audit log (incl. CRLF-strip), access gate.

Follows the MagicMock Supabase-admin-client conventions established in
tests/labeler/test_auth.py and tests/labeler/test_routes.py.
"""
from __future__ import annotations

import logging
import os
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Phase 2 — auth.py foundation
# ---------------------------------------------------------------------------

class TestNonAdminRoles:
    def test_excludes_admin(self):
        from src.modules.labeler.auth import NON_ADMIN_ROLES, ROLE_ADMIN
        assert ROLE_ADMIN not in NON_ADMIN_ROLES

    def test_contains_all_other_valid_roles(self):
        from src.modules.labeler.auth import (
            NON_ADMIN_ROLES, ROLE_MODERATOR, ROLE_VALIDATOR, ROLE_REVIEWER, ROLE_READER,
        )
        assert NON_ADMIN_ROLES == {ROLE_MODERATOR, ROLE_VALIDATOR, ROLE_REVIEWER, ROLE_READER}

    def test_is_frozenset(self):
        from src.modules.labeler.auth import NON_ADMIN_ROLES
        assert isinstance(NON_ADMIN_ROLES, frozenset)


class TestEvictRoleCache:
    def test_removes_cached_entry(self):
        from src.modules.labeler import auth as auth_module

        auth_module._role_cache["some-user"] = ("moderator", 999999999.0)
        auth_module.evict_role_cache("some-user")

        assert "some-user" not in auth_module._role_cache

    def test_noop_when_entry_absent(self):
        from src.modules.labeler import auth as auth_module

        auth_module._role_cache.pop("absent-user", None)
        # Must not raise
        auth_module.evict_role_cache("absent-user")
        assert "absent-user" not in auth_module._role_cache


# ---------------------------------------------------------------------------
# Shared app/client fixtures — production mode, mocked Supabase admin client
# ---------------------------------------------------------------------------

@pytest.fixture
def prod_app(tmp_path):
    env_vars = {
        "SUPABASE_URL": "https://fake.supabase.co",
        "SUPABASE_ANON_KEY": "fake-anon-key",
        "SECRET_KEY": "test-secret-admin-user-roles",
    }
    with patch.dict(os.environ, env_vars):
        from src.modules.labeler.server import create_app
        index_path = tmp_path / "crops" / "index.jsonl"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        app = create_app(index_path=index_path, labels_dir=tmp_path)
        app.config["TESTING"] = True
        yield app


@pytest.fixture
def admin_client(prod_app):
    """Client authenticated as an admin (acting user id = 'acting-admin-id')."""
    client = prod_app.test_client()
    with client.session_transaction() as sess:
        sess["access_token"] = "fake-valid-token"
        sess["refresh_token"] = "fake-refresh"
        sess["user_email"] = "acting-admin@example.com"
    return client


def _admin_auth_patches(acting_user_id: str = "acting-admin-id"):
    return (
        patch("src.modules.labeler.auth.decode_jwt", return_value={"sub": acting_user_id}),
        patch("src.modules.labeler.auth._get_user_role", return_value="admin"),
    )


def _make_user(uid: str, email: str, role: str, **extra) -> MagicMock:
    u = MagicMock()
    u.id = uid
    u.email = email
    u.app_metadata = {"role": role} if role is not None else {}
    for k, v in extra.items():
        setattr(u, k, v)
    return u


# ---------------------------------------------------------------------------
# Phase 3 — GET /admin/users
# ---------------------------------------------------------------------------

class TestAdminUsersListView:
    def test_admin_views_user_list_all_fields_present(self, prod_app, admin_client):
        users = [
            _make_user(
                "u1", "mod@example.com", "moderator",
                email_confirmed_at="2026-01-01T00:00:00Z",
                created_at="2026-01-01T00:00:00Z",
                last_sign_in_at="2026-07-01T00:00:00Z",
            ),
        ]
        mock_client = MagicMock()
        mock_client.auth.admin.list_users.return_value = users

        p1, p2 = _admin_auth_patches()
        with p1, p2, patch("src.modules.labeler.db._client", return_value=mock_client):
            resp = admin_client.get("/admin/users", headers={"Accept": "text/html"})

        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "mod@example.com" in body
        assert "moderator" in body

    def test_admin_rows_render_without_role_change_control(self, prod_app, admin_client):
        users = [_make_user("admin-2", "peer-admin@example.com", "admin")]
        mock_client = MagicMock()
        mock_client.auth.admin.list_users.return_value = users

        p1, p2 = _admin_auth_patches()
        with p1, p2, patch("src.modules.labeler.db._client", return_value=mock_client):
            resp = admin_client.get("/admin/users", headers={"Accept": "text/html"})

        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "peer-admin@example.com" in body
        # No <select> or role-change button should reference the admin row's id.
        assert 'changeRole(\'admin-2\'' not in body and 'changeRole("admin-2"' not in body

    def test_non_admin_denied_access_html(self, prod_app):
        client = prod_app.test_client()
        with client.session_transaction() as sess:
            sess["access_token"] = "fake-valid-token"
            sess["refresh_token"] = "fake-refresh"
        with patch("src.modules.labeler.auth.decode_jwt", return_value={"sub": "validator-1"}), \
             patch("src.modules.labeler.auth._get_user_role", return_value="validator"):
            resp = client.get("/admin/users", headers={"Accept": "text/html"})
        assert resp.status_code == 302

    def test_non_admin_denied_access_json(self, prod_app):
        client = prod_app.test_client()
        with client.session_transaction() as sess:
            sess["access_token"] = "fake-valid-token"
            sess["refresh_token"] = "fake-refresh"
        with patch("src.modules.labeler.auth.decode_jwt", return_value={"sub": "validator-1"}), \
             patch("src.modules.labeler.auth._get_user_role", return_value="validator"):
            resp = client.get("/admin/users", headers={"Accept": "application/json"})
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Phase 4 — POST /admin/users/role
# ---------------------------------------------------------------------------

class TestAdminUsersRoleChange:
    def test_happy_path_role_change(self, prod_app, admin_client):
        target = _make_user("target-1", "target@example.com", "validator")
        mock_client = MagicMock()
        get_resp = MagicMock()
        get_resp.user = target
        mock_client.auth.admin.get_user_by_id.return_value = get_resp

        p1, p2 = _admin_auth_patches()
        with p1, p2, patch("src.modules.labeler.db._client", return_value=mock_client):
            resp = admin_client.post(
                "/admin/users/role",
                json={"user_id": "target-1", "role": "moderator"},
                headers={"Accept": "application/json"},
            )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        mock_client.auth.admin.update_user_by_id.assert_called_once_with(
            "target-1", {"app_metadata": {"role": "moderator"}}
        )

    def test_mass_assignment_payload_ignored(self, prod_app, admin_client):
        """Extra fields in the request body must never reach the write call."""
        target = _make_user("target-1", "target@example.com", "validator")
        mock_client = MagicMock()
        get_resp = MagicMock()
        get_resp.user = target
        mock_client.auth.admin.get_user_by_id.return_value = get_resp

        p1, p2 = _admin_auth_patches()
        with p1, p2, patch("src.modules.labeler.db._client", return_value=mock_client):
            resp = admin_client.post(
                "/admin/users/role",
                json={
                    "user_id": "target-1",
                    "role": "moderator",
                    "app_metadata": {"role": "admin", "extra": "hax"},
                    "user_metadata": {"pwned": True},
                    "banned_until": "3000-01-01",
                },
                headers={"Accept": "application/json"},
            )

        assert resp.status_code == 200
        mock_client.auth.admin.update_user_by_id.assert_called_once_with(
            "target-1", {"app_metadata": {"role": "moderator"}}
        )

    def test_non_whitelisted_role_rejected_before_write(self, prod_app, admin_client):
        mock_client = MagicMock()

        p1, p2 = _admin_auth_patches()
        with p1, p2, patch("src.modules.labeler.db._client", return_value=mock_client):
            resp = admin_client.post(
                "/admin/users/role",
                json={"user_id": "target-1", "role": "superuser"},
                headers={"Accept": "application/json"},
            )

        assert resp.status_code == 400
        mock_client.auth.admin.get_user_by_id.assert_not_called()
        mock_client.auth.admin.update_user_by_id.assert_not_called()

    def test_escalation_to_admin_rejected_before_write(self, prod_app, admin_client):
        """role='admin' in the body must never be accepted as a target role."""
        mock_client = MagicMock()

        p1, p2 = _admin_auth_patches()
        with p1, p2, patch("src.modules.labeler.db._client", return_value=mock_client):
            resp = admin_client.post(
                "/admin/users/role",
                json={"user_id": "target-1", "role": "admin"},
                headers={"Accept": "application/json"},
            )

        assert resp.status_code == 400
        mock_client.auth.admin.update_user_by_id.assert_not_called()

    def test_peer_admin_target_rejected_no_write(self, prod_app, admin_client):
        """RED/GREEN mandatory case (a): another admin attempts to change an admin's
        role — must be rejected 403, and the write call must never happen, even though
        the request body claims a legitimate non-admin target role."""
        target_admin = _make_user("peer-admin-id", "peer-admin@example.com", "admin")
        mock_client = MagicMock()
        get_resp = MagicMock()
        get_resp.user = target_admin
        mock_client.auth.admin.get_user_by_id.return_value = get_resp

        p1, p2 = _admin_auth_patches(acting_user_id="acting-admin-id")
        with p1, p2, patch("src.modules.labeler.db._client", return_value=mock_client):
            resp = admin_client.post(
                "/admin/users/role",
                json={"user_id": "peer-admin-id", "role": "moderator"},
                headers={"Accept": "application/json"},
            )

        assert resp.status_code == 403
        data = resp.get_json()
        assert data["ok"] is False
        mock_client.auth.admin.update_user_by_id.assert_not_called()

    def test_self_role_change_rejected_independent_of_admin_immutability_check(
        self, prod_app, admin_client
    ):
        """RED/GREEN mandatory case (b): an admin attempts to change THEIR OWN role.
        Must be rejected via the separate self-check path — verified independent of
        case (a) by asserting get_user_by_id (the admin-immutability lookup) is never
        even called, proving this guard does not depend on it."""
        mock_client = MagicMock()

        p1, p2 = _admin_auth_patches(acting_user_id="acting-admin-id")
        with p1, p2, patch("src.modules.labeler.db._client", return_value=mock_client):
            resp = admin_client.post(
                "/admin/users/role",
                json={"user_id": "acting-admin-id", "role": "moderator"},
                headers={"Accept": "application/json"},
            )

        assert resp.status_code == 403
        data = resp.get_json()
        assert data["ok"] is False
        # Self-check must short-circuit BEFORE the admin-immutability re-read.
        mock_client.auth.admin.get_user_by_id.assert_not_called()
        mock_client.auth.admin.update_user_by_id.assert_not_called()

    def test_forged_role_hint_in_body_ignored_server_side_lookup_still_rejects(
        self, prod_app, admin_client
    ):
        """Target's current role is resolved server-side, never trusted from the
        request — a body claiming (falsely) the target isn't an admin must not help."""
        target_admin = _make_user("peer-admin-id", "peer-admin@example.com", "admin")
        mock_client = MagicMock()
        get_resp = MagicMock()
        get_resp.user = target_admin
        mock_client.auth.admin.get_user_by_id.return_value = get_resp

        p1, p2 = _admin_auth_patches()
        with p1, p2, patch("src.modules.labeler.db._client", return_value=mock_client):
            resp = admin_client.post(
                "/admin/users/role",
                json={"user_id": "peer-admin-id", "role": "moderator", "current_role": "moderator"},
                headers={"Accept": "application/json"},
            )

        assert resp.status_code == 403
        mock_client.auth.admin.update_user_by_id.assert_not_called()

    def test_cache_evicted_on_success(self, prod_app, admin_client):
        from src.modules.labeler import auth as auth_module

        auth_module._role_cache["target-1"] = ("validator", 999999999.0)

        target = _make_user("target-1", "target@example.com", "validator")
        mock_client = MagicMock()
        get_resp = MagicMock()
        get_resp.user = target
        mock_client.auth.admin.get_user_by_id.return_value = get_resp

        p1, p2 = _admin_auth_patches()
        with p1, p2, patch("src.modules.labeler.db._client", return_value=mock_client):
            resp = admin_client.post(
                "/admin/users/role",
                json={"user_id": "target-1", "role": "moderator"},
                headers={"Accept": "application/json"},
            )

        assert resp.status_code == 200
        assert "target-1" not in auth_module._role_cache

    def test_successful_change_emits_audit_log_with_all_fields(self, prod_app, admin_client, caplog):
        target = _make_user("target-1", "target@example.com", "validator")
        mock_client = MagicMock()
        get_resp = MagicMock()
        get_resp.user = target
        mock_client.auth.admin.get_user_by_id.return_value = get_resp

        p1, p2 = _admin_auth_patches(acting_user_id="acting-admin-id")
        with caplog.at_level(logging.INFO, logger="labeler"):
            with p1, p2, patch("src.modules.labeler.db._client", return_value=mock_client):
                resp = admin_client.post(
                    "/admin/users/role",
                    json={"user_id": "target-1", "role": "moderator"},
                    headers={"Accept": "application/json"},
                )

        assert resp.status_code == 200
        audit_lines = [r.message for r in caplog.records if "role-change" in r.message]
        assert len(audit_lines) == 1
        line = audit_lines[0]
        assert "acting-admin-id" in line
        assert "target-1" in line
        assert "target@example.com" in line
        assert "old=validator" in line
        assert "new=moderator" in line

    def test_audit_log_strips_crlf_from_email(self, prod_app, admin_client, caplog):
        """Log-injection guard (threat T7): CR/LF in a logged email must be stripped."""
        malicious_email = "evil@example.com\r\nFAKE LOG LINE: admin granted"
        target = _make_user("target-1", malicious_email, "validator")
        mock_client = MagicMock()
        get_resp = MagicMock()
        get_resp.user = target
        mock_client.auth.admin.get_user_by_id.return_value = get_resp

        p1, p2 = _admin_auth_patches()
        with caplog.at_level(logging.INFO, logger="labeler"):
            with p1, p2, patch("src.modules.labeler.db._client", return_value=mock_client):
                resp = admin_client.post(
                    "/admin/users/role",
                    json={"user_id": "target-1", "role": "moderator"},
                    headers={"Accept": "application/json"},
                )

        assert resp.status_code == 200
        audit_lines = [r.message for r in caplog.records if "role-change" in r.message]
        assert len(audit_lines) == 1
        assert "\r" not in audit_lines[0]
        assert "\n" not in audit_lines[0]

    def test_rejected_change_does_not_emit_success_log_line(self, prod_app, admin_client, caplog):
        target_admin = _make_user("peer-admin-id", "peer-admin@example.com", "admin")
        mock_client = MagicMock()
        get_resp = MagicMock()
        get_resp.user = target_admin
        mock_client.auth.admin.get_user_by_id.return_value = get_resp

        p1, p2 = _admin_auth_patches()
        with caplog.at_level(logging.INFO, logger="labeler"):
            with p1, p2, patch("src.modules.labeler.db._client", return_value=mock_client):
                resp = admin_client.post(
                    "/admin/users/role",
                    json={"user_id": "peer-admin-id", "role": "moderator"},
                    headers={"Accept": "application/json"},
                )

        assert resp.status_code == 403
        audit_lines = [r.message for r in caplog.records if "role-change" in r.message]
        assert audit_lines == []

    def test_non_admin_denied_access_json(self, prod_app):
        client = prod_app.test_client()
        with client.session_transaction() as sess:
            sess["access_token"] = "fake-valid-token"
            sess["refresh_token"] = "fake-refresh"
        with patch("src.modules.labeler.auth.decode_jwt", return_value={"sub": "validator-1"}), \
             patch("src.modules.labeler.auth._get_user_role", return_value="validator"):
            resp = client.post(
                "/admin/users/role",
                json={"user_id": "target-1", "role": "moderator"},
                headers={"Accept": "application/json"},
            )
        assert resp.status_code == 403

    def test_non_admin_denied_access_html(self, prod_app):
        client = prod_app.test_client()
        with client.session_transaction() as sess:
            sess["access_token"] = "fake-valid-token"
            sess["refresh_token"] = "fake-refresh"
        with patch("src.modules.labeler.auth.decode_jwt", return_value={"sub": "validator-1"}), \
             patch("src.modules.labeler.auth._get_user_role", return_value="validator"):
            resp = client.post(
                "/admin/users/role",
                json={"user_id": "target-1", "role": "moderator"},
                headers={"Accept": "text/html"},
            )
        assert resp.status_code == 302
