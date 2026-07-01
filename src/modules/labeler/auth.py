"""
auth — Supabase Auth integration for the labeler web portal.

Provides:
  - init_supabase_client()  — build the supabase-py sync client from env vars
  - JWKS cache              — fetched once from Supabase on first JWT decode
  - decode_jwt(token)       — local PyJWT decode; raises jwt.ExpiredSignatureError
  - refresh_jwt(token)      — exchange refresh token for a new session pair
  - @require_auth           — Flask decorator; handles expiry, dev bypass, 401/redirect

Design references: ADR-1 (Supabase Auth + PyJWT JWKS local decode),
ADR-4 (dev bypass when SUPABASE_URL unset), spec invariant I5 (local mode
must remain functional without Supabase env vars).
"""
from __future__ import annotations

import os
import time
import urllib.request
import json
import functools
from typing import Any, Callable

import jwt  # PyJWT
from flask import g, jsonify, redirect, request, session

# ---------------------------------------------------------------------------
# Role constants and in-process role cache
# ---------------------------------------------------------------------------

ROLE_ADMIN     = "admin"
ROLE_MODERATOR = "moderator"
ROLE_VALIDATOR = "validator"
ROLE_REVIEWER  = "reviewer"
ROLE_READER    = "reader"
_VALID_ROLES   = frozenset({ROLE_ADMIN, ROLE_MODERATOR, ROLE_VALIDATOR, ROLE_REVIEWER, ROLE_READER})
_ROLE_CACHE_TTL = 60.0
_role_cache: dict[str, tuple[str, float]] = {}  # user_id → (role, expires_at)

# ---------------------------------------------------------------------------
# Lazy Supabase client — only imported/instantiated when SUPABASE_URL is set
# ---------------------------------------------------------------------------

_supabase_client: Any = None  # supabase.Client | None


def init_supabase_client() -> Any:
    """
    Build and return a synchronous supabase-py client from environment variables.

    Required env vars: SUPABASE_URL, SUPABASE_ANON_KEY.
    Raises RuntimeError immediately (at import time of the calling module) if
    either required var is missing.

    Returns the cached client on subsequent calls.
    """
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client

    supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    supabase_key = os.environ.get("SUPABASE_ANON_KEY", "").strip()

    if not supabase_url:
        raise RuntimeError(
            "Missing required environment variable: SUPABASE_URL. "
            "Set it to your Supabase project URL (e.g. https://xyz.supabase.co)."
        )
    if not supabase_key:
        raise RuntimeError(
            "Missing required environment variable: SUPABASE_ANON_KEY. "
            "Set it to your Supabase project anon/public key."
        )

    from supabase import create_client  # type: ignore[import]

    _supabase_client = create_client(supabase_url, supabase_key)
    return _supabase_client


# ---------------------------------------------------------------------------
# JWKS cache — fetched once from Supabase, cached in process memory
# ---------------------------------------------------------------------------

_jwks_cache: dict = {}  # keyed by kid → public key object


def decode_jwt(token: str) -> dict:
    """
    Validate a Supabase JWT using the Supabase client (server-side verification).
    Works with both legacy anon keys and new publishable key format.

    Returns:
        Claims dict with at minimum ``sub`` (user UUID) and ``email``.

    Raises:
        jwt.ExpiredSignatureError: token expired.
        jwt.InvalidTokenError:     any other validation failure.
    """
    try:
        client = init_supabase_client()
        response = client.auth.get_user(token)
        if response is None or response.user is None:
            raise jwt.InvalidTokenError("Invalid token — no user returned")
        user = response.user
        return {
            "sub": user.id,
            "email": user.email or "",
            "role": "authenticated",
        }
    except jwt.ExpiredSignatureError:
        raise
    except Exception as exc:
        err = str(exc).lower()
        if "expired" in err or "exp" in err:
            raise jwt.ExpiredSignatureError("Token expired") from exc
        raise jwt.InvalidTokenError(str(exc)) from exc


# ---------------------------------------------------------------------------
# JWT refresh
# ---------------------------------------------------------------------------

def refresh_jwt(refresh_token: str) -> tuple[str, str]:
    """
    Exchange a Supabase refresh token for a new (access_token, refresh_token) pair.

    Args:
        refresh_token: The refresh token stored in the Flask session.

    Returns:
        Tuple of (new_access_token, new_refresh_token).

    Raises:
        RuntimeError: if the refresh call fails or the client is not initialised.
    """
    client = init_supabase_client()
    response = client.auth.refresh_session(refresh_token)
    session_data = response.session
    if session_data is None:
        raise RuntimeError("Supabase refresh_session returned no session data")
    return session_data.access_token, session_data.refresh_token


# ---------------------------------------------------------------------------
# Role resolution helpers
# ---------------------------------------------------------------------------

def _get_user_role(user_id: str) -> str:
    """
    Return the role string for user_id.

    Checks the in-process cache first (60 s TTL). On miss, calls the Supabase
    Admin API to read app_metadata.role. Always returns a value in _VALID_ROLES.
    Falls back to ROLE_VALIDATOR — NEVER ROLE_ADMIN — on any error.
    """
    now = time.monotonic()
    cached = _role_cache.get(user_id)
    if cached is not None and cached[1] > now:
        return cached[0]

    try:
        from src.modules.labeler import db as _db
        client = _db._client()  # service_role client — required for admin API
        user_resp = client.auth.admin.get_user_by_id(user_id)
        if user_resp and user_resp.user:
            app_meta = user_resp.user.app_metadata or {}
            role = app_meta.get("role", ROLE_VALIDATOR)
            if role not in _VALID_ROLES:
                role = ROLE_VALIDATOR
        else:
            role = ROLE_VALIDATOR
    except Exception:
        role = ROLE_VALIDATOR

    _role_cache[user_id] = (role, now + _ROLE_CACHE_TTL)
    return role


def resolve_user_role() -> None:
    """
    Flask before_request hook: resolve and attach g.user_role.

    Dev bypass paths (LOCAL_DEV_BYPASS=1 or SUPABASE_URL unset) grant
    ROLE_ADMIN — but ONLY when FLASK_ENV is not "production".
    Public routes where g.user_id is not set are silently skipped.
    """
    flask_env = os.environ.get("FLASK_ENV", "")
    is_production = flask_env == "production"

    if not is_production:
        if os.environ.get("LOCAL_DEV_BYPASS", "").strip() == "1":
            g.user_role = ROLE_ADMIN
            return
        if not os.environ.get("SUPABASE_URL", "").strip():
            g.user_role = ROLE_ADMIN
            return

    user_id = getattr(g, "user_id", None)
    if not user_id:
        return  # public route — no-op

    g.user_role = _get_user_role(user_id)


def require_role(*roles: str) -> Callable:
    """
    Decorator factory that enforces role-based access control.

    Usage::

        @app.route("/admin/conflicts")
        @require_auth
        @require_role(ROLE_ADMIN)
        def admin_view(): ...

    Returns a 302 redirect to "/" for HTML requests or a 403 JSON response
    for API/JSON requests when the user's role is not in `roles`.
    """
    def decorator(view_func: Callable) -> Callable:
        @functools.wraps(view_func)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            user_role = getattr(g, "user_role", "")
            # before_request runs before @require_auth, so g.user_role may not be set yet.
            # Resolve it here lazily if g.user_id is already available (set by @require_auth).
            if not user_role:
                uid = getattr(g, "user_id", None)
                if uid:
                    user_role = _get_user_role(uid)
                    g.user_role = user_role
            if user_role not in roles:
                if _is_html_request():
                    return redirect("/", 302)
                return jsonify({"error": "forbidden"}), 403
            return view_func(*args, **kwargs)
        return wrapped
    return decorator


# ---------------------------------------------------------------------------
# @require_auth decorator
# ---------------------------------------------------------------------------

def _is_html_request() -> bool:
    """Return True if the request expects an HTML response."""
    accept = request.headers.get("Accept", "")
    return "text/html" in accept


def require_auth(view_func: Callable) -> Callable:
    """
    Flask route decorator that enforces authentication.

    Behaviour (per ADR-1 and ADR-4):
    1. Dev bypass: if SUPABASE_URL is unset, sets g.user_id from FAKE_USER_ID
       and skips all Supabase calls (invariant I5 — local mode stays functional).
    2. Normal path: reads ``access_token`` from the Flask session, calls
       decode_jwt().  On success sets g.user_id = claims["sub"].
    3. Expired token: calls refresh_jwt() with ``refresh_token`` from session.
       On success updates session and retries with the new token.
    4. No token / refresh failure:
       - HTML requests  → redirect 302 to /auth/login
       - JSON/API requests → return 401 JSON {"error": "authentication required"}
    """
    @functools.wraps(view_func)
    def decorated(*args: Any, **kwargs: Any) -> Any:
        # --- Local dev bypass: set LOCAL_DEV_BYPASS=1 to skip auth on localhost ---
        if os.environ.get("LOCAL_DEV_BYPASS", "").strip():
            g.user_id = os.environ.get("FAKE_USER_ID", "dev-user")
            if not session.get("user_email"):
                session["user_email"] = os.environ.get("FAKE_USER_EMAIL", "dev@local.test")
            return view_func(*args, **kwargs)

        # --- Dev bypass (ADR-4) ---
        if not os.environ.get("SUPABASE_URL", "").strip():
            g.user_id = os.environ.get("FAKE_USER_ID", "dev-user")
            if not session.get("user_email"):
                session["user_email"] = os.environ.get("FAKE_USER_EMAIL", "dev@local.test")
            return view_func(*args, **kwargs)

        # --- Normal auth path ---
        access_token: str | None = session.get("access_token")
        refresh_token: str | None = session.get("refresh_token")

        if not access_token:
            return _auth_failure()

        try:
            claims = decode_jwt(access_token)
        except jwt.ExpiredSignatureError:
            # Token expired — attempt refresh
            if not refresh_token:
                return _auth_failure()
            try:
                new_access, new_refresh = refresh_jwt(refresh_token)
                session["access_token"] = new_access
                session["refresh_token"] = new_refresh
                claims = decode_jwt(new_access)
            except Exception:
                session.clear()
                return _auth_failure()
        except jwt.InvalidTokenError:
            session.clear()
            return _auth_failure()

        g.user_id = claims.get("sub", "")
        return view_func(*args, **kwargs)

    return decorated


def _auth_failure():
    """Return the appropriate auth failure response based on request Accept header."""
    if _is_html_request():
        # Use a hard-coded path so this works before the auth blueprint is registered.
        # When the auth blueprint is wired in PR 3, this path matches GET /auth/login.
        return redirect("/auth/login")
    return jsonify({"error": "authentication required"}), 401
