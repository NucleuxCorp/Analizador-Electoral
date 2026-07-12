"""
tests/labeler/test_auth.py — Unit tests for src/modules/labeler/auth.py

Covers:
  - decode_jwt: valid token, expired token, malformed token
  - @require_auth: JSON route (401), HTML route (redirect), dev bypass
  - refresh flow: expired token triggers refresh, session updated

decode_jwt() now delegates verification to the Supabase client
(``init_supabase_client().auth.get_user(token)``) instead of local JWKS/RS256
verification, so these tests mock ``init_supabase_client`` rather than signing
real JWTs.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

VALID_TOKEN = "valid-token-string"
EXPIRED_TOKEN = "expired-token-string"


def _make_supabase_client(*, user_id: str | None = None, email: str | None = None,
                           side_effect: Exception | None = None) -> MagicMock:
    """Build a MagicMock standing in for the supabase-py client returned by
    init_supabase_client(), with client.auth.get_user(token) configured."""
    client = MagicMock()
    if side_effect is not None:
        client.auth.get_user.side_effect = side_effect
    else:
        response = MagicMock()
        response.user = MagicMock()
        response.user.id = user_id
        response.user.email = email
        client.auth.get_user.return_value = response
    return client


# ---------------------------------------------------------------------------
# 6.1a  decode_jwt — valid token
# ---------------------------------------------------------------------------

class TestDecodeJwtValid:
    def test_returns_claims_dict(self):
        """decode_jwt with a valid token must return a dict with 'sub'."""
        mock_client = _make_supabase_client(user_id="user-uuid-123", email="test@example.com")

        with patch(
            "src.modules.labeler.auth.init_supabase_client",
            return_value=mock_client,
        ):
            from src.modules.labeler.auth import decode_jwt

            claims = decode_jwt(VALID_TOKEN)

        assert isinstance(claims, dict)
        assert claims["sub"] == "user-uuid-123"
        assert claims["email"] == "test@example.com"

    def test_sub_claim_present(self):
        """sub claim must be present and non-empty."""
        mock_client = _make_supabase_client(user_id="user-uuid-123", email="test@example.com")

        with patch(
            "src.modules.labeler.auth.init_supabase_client",
            return_value=mock_client,
        ):
            from src.modules.labeler.auth import decode_jwt

            claims = decode_jwt(VALID_TOKEN)

        assert "sub" in claims
        assert claims["sub"]


# ---------------------------------------------------------------------------
# 6.1b  decode_jwt — expired token
# ---------------------------------------------------------------------------

class TestDecodeJwtExpired:
    def test_raises_expired_signature_error(self):
        """decode_jwt with expired token must raise jwt.ExpiredSignatureError."""
        import jwt as pyjwt

        mock_client = _make_supabase_client(side_effect=Exception("token expired"))

        with patch(
            "src.modules.labeler.auth.init_supabase_client",
            return_value=mock_client,
        ):
            from src.modules.labeler.auth import decode_jwt

            with pytest.raises(pyjwt.ExpiredSignatureError):
                decode_jwt(EXPIRED_TOKEN)


# ---------------------------------------------------------------------------
# 6.1c  decode_jwt — malformed token
# ---------------------------------------------------------------------------

class TestDecodeJwtMalformed:
    def test_raises_invalid_token_error_on_garbage(self):
        """decode_jwt with garbage string must raise jwt.InvalidTokenError."""
        import jwt as pyjwt

        mock_client = _make_supabase_client(side_effect=Exception("invalid jwt"))

        with patch(
            "src.modules.labeler.auth.init_supabase_client",
            return_value=mock_client,
        ):
            from src.modules.labeler.auth import decode_jwt

            with pytest.raises(pyjwt.InvalidTokenError):
                decode_jwt("not.a.valid.jwt.string")

    def test_raises_on_empty_string(self):
        """decode_jwt with empty string must raise jwt.InvalidTokenError."""
        import jwt as pyjwt

        mock_client = _make_supabase_client(side_effect=Exception("invalid jwt"))

        with patch(
            "src.modules.labeler.auth.init_supabase_client",
            return_value=mock_client,
        ):
            from src.modules.labeler.auth import decode_jwt

            with pytest.raises(pyjwt.InvalidTokenError):
                decode_jwt("")


# ---------------------------------------------------------------------------
# 6.1d  @require_auth — dev bypass (SUPABASE_URL unset)
# ---------------------------------------------------------------------------

class TestRequireAuthDevBypass:
    def test_sets_g_user_id_to_fake_user_id(self):
        """
        When SUPABASE_URL is unset, @require_auth must set g.user_id = FAKE_USER_ID
        and call the decorated view without any JWT decode.
        """
        from flask import Flask, g, jsonify

        app = Flask(__name__)
        app.config["SECRET_KEY"] = "test-secret"

        with app.app_context():
            with patch.dict(
                os.environ,
                {"SUPABASE_URL": "", "FAKE_USER_ID": "fake-dev-user"},
                clear=False,
            ):
                from src.modules.labeler.auth import require_auth

                @app.route("/protected")
                @require_auth
                def _protected():
                    return jsonify({"user_id": g.user_id})

                client = app.test_client()
                resp = client.get("/protected")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["user_id"] == "fake-dev-user"

    def test_default_fake_user_id_is_dev_user(self):
        """When FAKE_USER_ID is unset, g.user_id falls back to 'dev-user'."""
        from flask import Flask, g, jsonify

        app = Flask(__name__)
        app.config["SECRET_KEY"] = "test-secret"

        env_no_fake = {k: v for k, v in os.environ.items() if k != "FAKE_USER_ID"}
        env_no_fake["SUPABASE_URL"] = ""

        with app.app_context():
            with patch.dict(os.environ, env_no_fake, clear=True):
                from src.modules.labeler.auth import require_auth

                @app.route("/protected2")
                @require_auth
                def _protected2():
                    return jsonify({"user_id": g.user_id})

                client = app.test_client()
                resp = client.get("/protected2")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["user_id"] == "dev-user"


# ---------------------------------------------------------------------------
# 6.1e  @require_auth — JSON route returns 401 when no token
# ---------------------------------------------------------------------------

class TestRequireAuthJsonRoute:
    def test_returns_401_json_when_no_token(self):
        """JSON API route (Accept: application/json) must return 401, not redirect."""
        from flask import Flask, jsonify

        app = Flask(__name__)
        app.config["SECRET_KEY"] = "test-secret"

        with patch.dict(os.environ, {"SUPABASE_URL": "https://fake.supabase.co"}):
            from src.modules.labeler.auth import require_auth

            @app.route("/api/data")
            @require_auth
            def _api_data():
                return jsonify({"data": "secret"})

            client = app.test_client()
            resp = client.get(
                "/api/data",
                headers={"Accept": "application/json"},
            )

        assert resp.status_code == 401
        data = resp.get_json()
        assert "error" in data


# ---------------------------------------------------------------------------
# 6.1f  @require_auth — HTML route redirects to /auth/login when no token
# ---------------------------------------------------------------------------

class TestRequireAuthHtmlRoute:
    def test_redirects_to_login_when_no_token(self):
        """HTML route (Accept: text/html) must redirect 302 to /auth/login."""
        from flask import Flask

        app = Flask(__name__)
        app.config["SECRET_KEY"] = "test-secret"

        with patch.dict(os.environ, {"SUPABASE_URL": "https://fake.supabase.co"}):
            from src.modules.labeler.auth import require_auth

            @app.route("/dashboard")
            @require_auth
            def _dashboard():
                return "<h1>Dashboard</h1>"

            client = app.test_client()
            resp = client.get(
                "/dashboard",
                headers={"Accept": "text/html,application/xhtml+xml"},
            )

        assert resp.status_code == 302
        assert "/auth/login" in resp.headers.get("Location", "")


# ---------------------------------------------------------------------------
# 6.1g  @require_auth — expired token triggers refresh, session updated
# ---------------------------------------------------------------------------

class TestRequireAuthRefreshFlow:
    def test_expired_token_triggers_refresh_and_updates_session(self):
        """
        When the session has an expired access_token plus a refresh_token,
        @require_auth must call refresh_jwt and update the session with the
        new access token.
        """
        from flask import Flask, g, jsonify

        app = Flask(__name__)
        app.config["SECRET_KEY"] = "test-secret"

        def get_user_side_effect(token):
            if token == EXPIRED_TOKEN:
                raise Exception("token expired")
            if token == VALID_TOKEN:
                response = MagicMock()
                response.user = MagicMock()
                response.user.id = "user-uuid-123"
                response.user.email = "test@example.com"
                return response
            raise Exception("invalid jwt")

        mock_client = MagicMock()
        mock_client.auth.get_user.side_effect = get_user_side_effect

        with patch.dict(os.environ, {"SUPABASE_URL": "https://fake.supabase.co"}):
            with patch(
                "src.modules.labeler.auth.init_supabase_client",
                return_value=mock_client,
            ):
                with patch(
                    "src.modules.labeler.auth.refresh_jwt",
                    return_value=(VALID_TOKEN, "new-refresh-token"),
                ) as mock_refresh:
                    from src.modules.labeler.auth import require_auth

                    @app.route("/secure")
                    @require_auth
                    def _secure():
                        return jsonify({"user_id": g.user_id})

                    client = app.test_client()

                    with client.session_transaction() as sess:
                        sess["access_token"] = EXPIRED_TOKEN
                        sess["refresh_token"] = "old-refresh-token"

                    resp = client.get(
                        "/secure",
                        headers={"Accept": "application/json"},
                    )

        assert resp.status_code == 200
        mock_refresh.assert_called_once_with("old-refresh-token")
        data = resp.get_json()
        assert data["user_id"] == "user-uuid-123"
