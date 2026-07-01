"""
diagnostic_roles.py -- Manual validation script for the roles-de-usuario change.

Exercises:
  1. resolve_user_role() in dev mode  -> g.user_role == "admin"
  2. @require_role("admin") with g.user_role = "validator"  -> 403/redirect
  3. @require_role("validator", "admin") with g.user_role = "validator"  -> passes
  4. _get_user_role() with a bad user_id (no Supabase)  -> "validator" (not "admin")
  5. _get_user_role() cache hit -> returns cached role

Run with:
  LOCAL_DEV_BYPASS=1 python diagnostic_roles.py
"""
from __future__ import annotations

import os
import sys

# Activate dev bypass so Flask test client works without Supabase
os.environ.setdefault("LOCAL_DEV_BYPASS", "1")
os.environ.setdefault("FLASK_ENV", "development")
# Ensure SUPABASE_URL is unset so the dev bypass path fires
os.environ.pop("SUPABASE_URL", None)

_PASS = 0
_FAIL = 0


def _ok(label):
    global _PASS
    _PASS += 1
    print("  PASS  " + label)


def _fail(label, detail=""):
    global _FAIL
    _FAIL += 1
    note = " (" + detail + ")" if detail else ""
    print("  FAIL  " + label + note)


# ---------------------------------------------------------------------------
# Scenario 1 -- resolve_user_role() in dev mode sets g.user_role = "admin"
# ---------------------------------------------------------------------------
def test_resolve_user_role_dev_bypass():
    print("\nScenario 1: resolve_user_role() in dev bypass mode")
    try:
        from src.modules.labeler.auth import resolve_user_role, ROLE_ADMIN
        from flask import Flask, g
        app = Flask(__name__)
        with app.test_request_context("/"):
            g.user_id = "dev-user"
            resolve_user_role()
            role = getattr(g, "user_role", None)
            if role == ROLE_ADMIN:
                _ok("g.user_role == 'admin' in dev bypass")
            else:
                _fail("g.user_role should be 'admin' in dev bypass", "got " + repr(role))
    except Exception as exc:
        _fail("resolve_user_role() import/call failed", str(exc))


# ---------------------------------------------------------------------------
# Scenario 2 -- @require_role("admin") with user_role="validator" -> 403/redirect
# ---------------------------------------------------------------------------
def test_require_role_rejects_wrong_role():
    print("\nScenario 2: @require_role('admin') rejects 'validator'")
    try:
        from src.modules.labeler.auth import require_role, ROLE_ADMIN
        from flask import Flask, g, jsonify

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test"

        # Test JSON rejection (403)
        with app.test_request_context("/test-admin", headers={"Accept": "application/json"}):
            g.user_role = "validator"
            decorated = require_role(ROLE_ADMIN)(lambda: (jsonify({"ok": True}), 200))
            resp = decorated()
            if isinstance(resp, tuple):
                status = resp[1]
            else:
                status = resp.status_code
            if status == 403:
                _ok("validator -> 403 for JSON request")
            else:
                _fail("validator should get 403 for JSON request", "got " + str(status))

        # Test HTML redirect (302)
        with app.test_request_context("/test-admin", headers={"Accept": "text/html,application/xhtml+xml"}):
            g.user_role = "validator"
            decorated2 = require_role(ROLE_ADMIN)(lambda: ("ok", 200))
            resp2 = decorated2()
            if hasattr(resp2, "status_code"):
                status2 = resp2.status_code
            elif isinstance(resp2, tuple):
                status2 = resp2[1]
            else:
                status2 = None
            if status2 == 302:
                _ok("validator -> 302 redirect for HTML request")
            else:
                _fail("validator should get 302 for HTML request", "got " + repr(status2))
    except Exception as exc:
        _fail("require_role rejection test failed", str(exc))


# ---------------------------------------------------------------------------
# Scenario 3 -- @require_role("validator", "admin") with user_role="validator" passes
# ---------------------------------------------------------------------------
def test_require_role_allows_valid_role():
    print("\nScenario 3: @require_role('validator', 'admin') allows 'validator'")
    try:
        from src.modules.labeler.auth import require_role, ROLE_VALIDATOR, ROLE_ADMIN
        from flask import Flask, g, jsonify

        app = Flask(__name__)
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test"

        with app.test_request_context("/work", headers={"Accept": "application/json"}):
            g.user_role = ROLE_VALIDATOR
            called = []

            def _view():
                called.append(True)
                return jsonify({"ok": True}), 200

            decorated = require_role(ROLE_VALIDATOR, ROLE_ADMIN)(_view)
            decorated()
            if called:
                _ok("validator passes @require_role('validator', 'admin')")
            else:
                _fail("view should have been called for validator role")
    except Exception as exc:
        _fail("require_role allow test failed", str(exc))


# ---------------------------------------------------------------------------
# Scenario 4 -- _get_user_role() with bad user_id (no Supabase) -> "validator"
# ---------------------------------------------------------------------------
def test_get_user_role_error_returns_validator():
    print("\nScenario 4: _get_user_role() with bad/missing user_id -> 'validator'")
    try:
        from src.modules.labeler.auth import _get_user_role, ROLE_VALIDATOR, _role_cache

        # Clear any cache entry for the test user_id
        fake_id = "nonexistent-user-00000000"
        _role_cache.pop(fake_id, None)

        # With SUPABASE_URL unset, the import of db._client() will fail
        # _get_user_role must catch all exceptions and return ROLE_VALIDATOR
        role = _get_user_role(fake_id)
        if role == ROLE_VALIDATOR:
            _ok("_get_user_role with bad user_id returned 'validator' (not 'admin')")
        else:
            _fail("_get_user_role should return 'validator' on error", "got " + repr(role))
    except Exception as exc:
        _fail("_get_user_role error-path test raised unexpectedly", str(exc))


# ---------------------------------------------------------------------------
# Scenario 5 -- _get_user_role() cache hit returns cached role
# ---------------------------------------------------------------------------
def test_get_user_role_cache_hit():
    print("\nScenario 5: _get_user_role() cache hit returns cached role")
    try:
        import time
        from src.modules.labeler.auth import _get_user_role, _role_cache, _ROLE_CACHE_TTL, ROLE_ADMIN

        cached_id = "cached-user-test"
        # Prime the cache with admin role
        _role_cache[cached_id] = (ROLE_ADMIN, time.monotonic() + _ROLE_CACHE_TTL)

        role = _get_user_role(cached_id)
        if role == ROLE_ADMIN:
            _ok("Cache hit returns correct cached role without API call")
        else:
            _fail("Cache hit should return 'admin'", "got " + repr(role))
    except Exception as exc:
        _fail("Cache hit test failed", str(exc))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("diagnostic_roles.py -- roles-de-usuario validation")
    print("=" * 60)

    test_resolve_user_role_dev_bypass()
    test_require_role_rejects_wrong_role()
    test_require_role_allows_valid_role()
    test_get_user_role_error_returns_validator()
    test_get_user_role_cache_hit()

    print()
    print("=" * 60)
    print("Results: {} PASS, {} FAIL".format(_PASS, _FAIL))
    print("=" * 60)

    sys.exit(0 if _FAIL == 0 else 1)
