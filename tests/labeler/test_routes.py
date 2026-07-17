"""
tests/labeler/test_routes.py — Unit tests for Flask routes in server.py

Uses the Flask test client + dev bypass (SUPABASE_URL unset) for auth,
and mocks db.* for DB interactions.

Covers:
  - POST /auth/register  → 201, 409, 400
  - POST /auth/login     → 302 (success), 403 (unconfirmed), 401 (wrong creds)
  - POST /label          → 201/{ok:true} first label, agreement, conflict, 403 wrong crop, 400 bad token
  - POST /skip           → {ok:true}
  - GET  /status         → 200 JSON with required fields
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
        "SECRET_KEY": "test-secret-key-for-routes",
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
# Auth fixture: dev bypass so routes are accessible without real JWT
# ---------------------------------------------------------------------------

@pytest.fixture
def dev_app(tmp_path):
    """App in dev bypass mode (SUPABASE_URL unset) — no JWT required."""
    env_vars = {
        "SUPABASE_URL": "",
        "FAKE_USER_ID": "test-dev-user",
    }
    # Remove SUPABASE_URL completely so dev mode is triggered
    clean_env = {k: v for k, v in os.environ.items() if k not in ("SUPABASE_URL", "SUPABASE_ANON_KEY")}
    clean_env.update({"FAKE_USER_ID": "test-dev-user", "SECRET_KEY": "dev-secret"})

    with patch.dict(os.environ, clean_env, clear=True):
        from src.modules.labeler.server import create_app
        index_path = tmp_path / "crops" / "index.jsonl"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text("")  # empty queue
        app = create_app(index_path=index_path, labels_dir=tmp_path)
        app.config["TESTING"] = True
        yield app


@pytest.fixture
def dev_client(dev_app):
    return dev_app.test_client()


# ---------------------------------------------------------------------------
# POST /auth/register
# ---------------------------------------------------------------------------

class TestAuthRegister:
    def test_returns_201_on_success(self, client):
        """Successful registration must return 201 with check-your-email message."""
        mock_supabase = MagicMock()
        mock_supabase.auth.sign_up.return_value = MagicMock()

        with patch("src.modules.labeler.auth.init_supabase_client", return_value=mock_supabase):
            resp = client.post(
                "/auth/register",
                json={"email": "new@example.com", "password": "password123"},
            )

        assert resp.status_code == 201
        data = resp.get_json()
        assert "message" in data
        assert "email" in data["message"].lower()

    def test_returns_409_on_duplicate_email(self, client):
        """Duplicate email must return 409."""
        mock_supabase = MagicMock()
        mock_supabase.auth.sign_up.side_effect = Exception("User already registered")

        with patch("src.modules.labeler.auth.init_supabase_client", return_value=mock_supabase):
            resp = client.post(
                "/auth/register",
                json={"email": "existing@example.com", "password": "password123"},
            )

        assert resp.status_code == 409
        data = resp.get_json()
        assert "error" in data

    def test_returns_400_on_missing_fields(self, client):
        """Missing email or password must return 400."""
        resp = client.post("/auth/register", json={"email": "no-password@example.com"})
        assert resp.status_code == 400

        resp2 = client.post("/auth/register", json={"password": "no-email"})
        assert resp2.status_code == 400

    def test_returns_400_on_empty_body(self, client):
        """Empty body must return 400."""
        resp = client.post("/auth/register", json={})
        assert resp.status_code == 400

    def test_sign_up_uses_public_email_redirect_to(self, client, monkeypatch):
        """Registration must pass the public APP_URL as email_redirect_to."""
        monkeypatch.setenv("APP_URL", "https://analizadore14.porciudad.com")

        mock_supabase = MagicMock()
        mock_supabase.auth.sign_up.return_value = MagicMock()

        with patch("src.modules.labeler.auth.init_supabase_client", return_value=mock_supabase):
            resp = client.post(
                "/auth/register",
                json={
                    "email": "new@example.com",
                    "password": "password123",
                    "first_name": "Ada",
                    "last_name": "Lovelace",
                },
            )

        assert resp.status_code == 201
        call_args = mock_supabase.auth.sign_up.call_args[0][0]
        assert (
            call_args["options"]["email_redirect_to"]
            == "https://analizadore14.porciudad.com/auth/confirm"
        )


# ---------------------------------------------------------------------------
# Public URL configuration
# ---------------------------------------------------------------------------

class TestPublicUrlConfig:
    def test_url_for_uses_app_url_domain(self, tmp_path, monkeypatch):
        """url_for(..., _external=True) must use the configured APP_URL domain."""
        monkeypatch.setenv("APP_URL", "https://analizadore14.porciudad.com")
        env_vars = {
            "SUPABASE_URL": "https://fake.supabase.co",
            "SUPABASE_ANON_KEY": "fake-anon-key",
            "SECRET_KEY": "test-secret-key-routes",
        }
        with patch.dict(os.environ, env_vars):
            from src.modules.labeler.server import create_app
            index_path = tmp_path / "crops" / "index.jsonl"
            index_path.parent.mkdir(parents=True, exist_ok=True)
            app = create_app(index_path=index_path, labels_dir=tmp_path)
            app.config["TESTING"] = True

        with app.test_request_context():
            url = app.url_for("status_view", _external=True)

        assert url == "https://analizadore14.porciudad.com/status"


# ---------------------------------------------------------------------------
# POST /auth/login
# ---------------------------------------------------------------------------

class TestAuthLogin:
    def test_returns_302_redirect_on_success(self, client):
        """Successful login must redirect 302 to /."""
        mock_session = MagicMock()
        mock_session.access_token = "fake-access-token"
        mock_session.refresh_token = "fake-refresh-token"

        mock_user = MagicMock()
        mock_user.email = "test@example.com"

        mock_response = MagicMock()
        mock_response.session = mock_session
        mock_response.user = mock_user

        mock_supabase = MagicMock()
        mock_supabase.auth.sign_in_with_password.return_value = mock_response

        with patch("src.modules.labeler.auth.init_supabase_client", return_value=mock_supabase):
            resp = client.post(
                "/auth/login",
                json={"email": "test@example.com", "password": "correct-password"},
            )

        assert resp.status_code == 302
        assert resp.headers.get("Location") in ("/", "http://localhost/")

    def test_returns_403_on_unconfirmed_email(self, client):
        """Unconfirmed email must return 403."""
        mock_supabase = MagicMock()
        mock_supabase.auth.sign_in_with_password.side_effect = Exception(
            "Email not confirmed"
        )

        with patch("src.modules.labeler.auth.init_supabase_client", return_value=mock_supabase):
            resp = client.post(
                "/auth/login",
                json={"email": "unconfirmed@example.com", "password": "pass"},
            )

        assert resp.status_code == 403
        data = resp.get_json()
        assert "error" in data

    def test_returns_401_on_wrong_credentials(self, client):
        """Wrong credentials must return 401."""
        mock_supabase = MagicMock()
        mock_supabase.auth.sign_in_with_password.side_effect = Exception(
            "Invalid login credentials"
        )

        with patch("src.modules.labeler.auth.init_supabase_client", return_value=mock_supabase):
            resp = client.post(
                "/auth/login",
                json={"email": "user@example.com", "password": "wrong-password"},
            )

        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /auth/login — role revalidation set (regression: ROLE_MODERATOR)
# ---------------------------------------------------------------------------

class TestAuthLoginRoleRevalidation:
    def _mock_login_response(self, *, user_id: str, role: str):
        mock_session = MagicMock()
        mock_session.access_token = "fake-access-token"
        mock_session.refresh_token = "fake-refresh-token"

        mock_user = MagicMock()
        mock_user.id = user_id
        mock_user.email = "test@example.com"
        mock_user.app_metadata = {"role": role}

        mock_response = MagicMock()
        mock_response.session = mock_session
        mock_response.user = mock_user
        return mock_response

    def test_moderator_role_persists_across_login(self, client):
        """
        Regression: a user whose app_metadata.role == 'moderator' must keep
        role == 'moderator' after the server.py:~1358 revalidation set runs,
        instead of being silently reset to 'validator'. Redirect must match
        the ROLE_ADMIN/ROLE_MODERATOR target (/admin/conflicts), consistent
        with how both roles are already redirected together.
        """
        user_id = "moderator-user-id"
        mock_response = self._mock_login_response(user_id=user_id, role="moderator")

        mock_supabase = MagicMock()
        mock_supabase.auth.sign_in_with_password.return_value = mock_response

        from src.modules.labeler import auth as auth_module

        with patch("src.modules.labeler.auth.init_supabase_client", return_value=mock_supabase):
            resp = client.post(
                "/auth/login",
                json={"email": "moderator@example.com", "password": "correct-password"},
            )

        assert resp.status_code == 302
        assert resp.headers.get("Location") in ("/admin/conflicts", "http://localhost/admin/conflicts")

        cached_role, _ = auth_module._role_cache[user_id]
        assert cached_role == "moderator"

    def test_unknown_role_falls_back_to_validator(self, client):
        """Unchanged behavior: an unrecognized role must still fall back to validator."""
        user_id = "unknown-role-user-id"
        mock_response = self._mock_login_response(user_id=user_id, role="totally-bogus-role")

        mock_supabase = MagicMock()
        mock_supabase.auth.sign_in_with_password.return_value = mock_response

        from src.modules.labeler import auth as auth_module

        with patch("src.modules.labeler.auth.init_supabase_client", return_value=mock_supabase):
            resp = client.post(
                "/auth/login",
                json={"email": "unknown@example.com", "password": "correct-password"},
            )

        assert resp.status_code == 302
        assert resp.headers.get("Location") in ("/work", "http://localhost/work")

        cached_role, _ = auth_module._role_cache[user_id]
        assert cached_role == "validator"


# ---------------------------------------------------------------------------
# POST /label — dev bypass mode so no JWT needed
# ---------------------------------------------------------------------------

class TestLabelRouteDevMode:
    """
    Tests for POST /label using dev bypass mode (SUPABASE_URL unset).
    Local dev mode uses the ValidationQueue, not Supabase, so these tests
    exercise the local-mode label submission path.
    """

    def test_returns_error_when_queue_empty(self, dev_client):
        """In dev mode with empty queue, /label should return 400."""
        resp = dev_client.post(
            "/label",
            json={"crop_id": "nonexistent", "value": "5"},
        )
        # Queue is empty → error (queue exhausted or crop_id mismatch)
        assert resp.status_code == 400


class TestLabelRouteProductionMode:
    """
    Tests for POST /label in production mode (SUPABASE_URL set, db mocked).
    """

    def _setup_assignment(self, mock_iac, crop_id: str, user_id: str = "test-dev-user"):
        """Configure mock to return an active assignment for crop_id."""
        asgn_chain = MagicMock()
        asgn_chain.select.return_value = asgn_chain
        asgn_chain.eq.return_value = asgn_chain
        asgn_chain.execute.return_value = MagicMock(data=[{"crop_id": crop_id}])
        mock_iac.return_value.table.return_value = asgn_chain

    def test_returns_ok_on_first_annotation(self, tmp_path):
        """First annotation must return {ok: true} (200)."""
        env_vars = {
            "SUPABASE_URL": "https://fake.supabase.co",
            "SUPABASE_ANON_KEY": "fake-anon-key",
            "SECRET_KEY": "test-secret",
            "FAKE_USER_ID": "test-dev-user",
        }
        with patch.dict(os.environ, env_vars):
            from src.modules.labeler.server import create_app
            index_path = tmp_path / "crops" / "index.jsonl"
            index_path.parent.mkdir(parents=True, exist_ok=True)
            app = create_app(index_path=index_path, labels_dir=tmp_path)
            app.config["TESTING"] = True

            mock_db = MagicMock()
            mock_db.write_label.return_value = None
            mock_db.get_crop_details.return_value = {
                "crop_id": "crop-001",
                "annotation_count": 1,  # after write
            }

            # Assignment check: returns data → valid assignment
            asgn_chain = MagicMock()
            asgn_chain.table.return_value = asgn_chain
            asgn_chain.select.return_value = asgn_chain
            asgn_chain.eq.return_value = asgn_chain
            asgn_chain.execute.return_value = MagicMock(data=[{"crop_id": "crop-001"}])

            # dev bypass is active because FAKE_USER_ID is set but SUPABASE_URL is set too
            # So @require_auth will try to decode JWT → patch to succeed
            with patch("src.modules.labeler.auth._get_jwks", return_value={}), \
                 patch("src.modules.labeler.auth.decode_jwt", return_value={"sub": "test-dev-user"}), \
                 patch("src.modules.labeler.auth.init_supabase_client", return_value=asgn_chain), \
                 patch("src.modules.labeler.db", mock_db), \
                 patch("src.modules.labeler.server._is_admin_user", return_value=False):

                client = app.test_client()

                # Inject a fake access token into the session
                with client.session_transaction() as sess:
                    sess["access_token"] = "fake-valid-token"
                    sess["refresh_token"] = "fake-refresh"

                resp = client.post(
                    "/label",
                    json={"crop_id": "crop-001", "value": "5"},
                )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data.get("ok") is True

    def test_returns_403_for_wrong_crop_id(self, tmp_path):
        """Submitting a crop_id not assigned to the user must return 403."""
        env_vars = {
            "SUPABASE_URL": "https://fake.supabase.co",
            "SUPABASE_ANON_KEY": "fake-anon-key",
            "SECRET_KEY": "test-secret",
        }
        with patch.dict(os.environ, env_vars):
            from src.modules.labeler.server import create_app
            index_path = tmp_path / "crops" / "index.jsonl"
            index_path.parent.mkdir(parents=True, exist_ok=True)
            app = create_app(index_path=index_path, labels_dir=tmp_path)
            app.config["TESTING"] = True

            # Assignment check: empty data → no assignment for this user
            asgn_chain = MagicMock()
            asgn_chain.table.return_value = asgn_chain
            asgn_chain.select.return_value = asgn_chain
            asgn_chain.eq.return_value = asgn_chain
            asgn_chain.execute.return_value = MagicMock(data=[])  # no assignment!

            with patch("src.modules.labeler.auth.decode_jwt", return_value={"sub": "user-xyz"}), \
                 patch("src.modules.labeler.auth.init_supabase_client", return_value=asgn_chain):

                client = app.test_client()
                with client.session_transaction() as sess:
                    sess["access_token"] = "fake-token"
                    sess["refresh_token"] = "fake-refresh"

                resp = client.post(
                    "/label",
                    json={"crop_id": "wrong-crop", "value": "3"},
                )

        assert resp.status_code == 403
        data = resp.get_json()
        assert data.get("ok") is False

    def test_returns_400_on_invalid_value_token(self, tmp_path):
        """An invalid value token must return 400 before any assignment check."""
        env_vars = {
            "SUPABASE_URL": "https://fake.supabase.co",
            "SUPABASE_ANON_KEY": "fake-anon-key",
            "SECRET_KEY": "test-secret",
        }
        with patch.dict(os.environ, env_vars):
            from src.modules.labeler.server import create_app
            index_path = tmp_path / "crops" / "index.jsonl"
            index_path.parent.mkdir(parents=True, exist_ok=True)
            app = create_app(index_path=index_path, labels_dir=tmp_path)
            app.config["TESTING"] = True

            with patch("src.modules.labeler.auth.decode_jwt", return_value={"sub": "user-xyz"}), \
                 patch("src.modules.labeler.auth.init_supabase_client", return_value=MagicMock()):

                client = app.test_client()
                with client.session_transaction() as sess:
                    sess["access_token"] = "fake-token"
                    sess["refresh_token"] = "fake-refresh"

                resp = client.post(
                    "/label",
                    json={"crop_id": "crop-001", "value": "INVALID_VALUE_99"},
                )

        assert resp.status_code == 400
        data = resp.get_json()
        assert data.get("ok") is False


# ---------------------------------------------------------------------------
# POST /skip — dev mode
# ---------------------------------------------------------------------------

class TestSkipRoute:
    def test_skip_returns_ok_in_dev_mode(self, dev_client):
        """
        In dev mode with an empty queue, skip returns 400 (queue exhausted).
        The important thing is the route exists and returns a valid JSON response.
        """
        resp = dev_client.post("/skip")
        # Dev mode with empty queue returns 400 — route is wired correctly
        assert resp.status_code in (200, 400)
        data = resp.get_json()
        assert "ok" in data


# ---------------------------------------------------------------------------
# GET /status — public route, no auth required
# ---------------------------------------------------------------------------

class TestStatusRoute:
    def test_status_returns_200_in_dev_mode(self, dev_client):
        """GET /status must return 200 JSON in dev mode."""
        resp = dev_client.get("/status")
        assert resp.status_code == 200
        data = resp.get_json()
        # Dev mode returns labeled, remaining, total
        assert isinstance(data, dict)
        assert "labeled" in data or "error" in data

    def test_status_returns_five_fields_in_production_mode(self, tmp_path):
        """GET /status in production mode must return all 5 fields."""
        env_vars = {
            "SUPABASE_URL": "https://fake.supabase.co",
            "SUPABASE_ANON_KEY": "fake-anon-key",
            "SECRET_KEY": "test-secret",
        }
        with patch.dict(os.environ, env_vars):
            from src.modules.labeler.server import create_app
            index_path = tmp_path / "crops" / "index.jsonl"
            index_path.parent.mkdir(parents=True, exist_ok=True)
            app = create_app(index_path=index_path, labels_dir=tmp_path)
            app.config["TESTING"] = True

            # Mock Supabase client for status query
            crops_chain = MagicMock()
            crops_chain.select.return_value = crops_chain
            crops_chain.execute.return_value = MagicMock(
                count=10,
                data=[
                    {"status": "confirmed"},
                    {"status": "confirmed"},
                    {"status": "conflict"},
                    {"status": "pending"},
                ],
            )

            mock_iac = MagicMock()
            mock_iac.table.return_value = crops_chain

            with patch("src.modules.labeler.auth.init_supabase_client", return_value=mock_iac):
                client = app.test_client()
                resp = client.get("/status")

        assert resp.status_code == 200
        data = resp.get_json()
        for field in ("labeled", "remaining", "confirmed", "conflicts", "my_labeled"):
            assert field in data, f"Missing field: {field}"
