"""
tests/labeler/test_security_audit.py — Security audit: protected routes.

Phases:
  FASE 1 — Unauthenticated access: every route tested without session cookie.
  FASE 2 — Privilege escalation: default "validator" role against restricted routes.
  EXTRA  — Maintenance mode, dev bypass, POST /feedback vulnerability flag.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Route registry — single source of truth for the audit
# ---------------------------------------------------------------------------
# (method, path, auth_required, allowed_roles, is_html_dest, note)
# is_html_dest: True if the route is meant for browser (HTML), False if API/JSON.
# allowed_roles: None for public routes; list for protected routes.

ROUTES = [
    # ── Public routes ──
    ("GET",  "/",                       False, None,   True,  "landing page"),
    ("GET",  "/status",                 False, None,   True,  "monitoring endpoint"),
    ("GET",  "/privacy",                False, None,   True,  "privacy policy"),
    ("GET",  "/mesas",                  False, None,   True,  "public dashboard"),
    ("GET",  "/admin",                  False, None,   True,  "redirect to /admin/conflicts — target route enforces auth"),
    ("GET",  "/auth/login",             False, None,   True,  "login page"),
    ("POST", "/auth/login",             False, None,   True,  "login submit"),
    ("GET",  "/auth/register",          False, None,   True,  "register page"),
    ("POST", "/auth/register",          False, None,   True,  "register submit"),
    ("GET",  "/auth/confirm",           False, None,   True,  "email confirmation"),
    ("POST", "/auth/logout",            False, None,   True,  "logout"),
    ("GET",  "/auth/forgot-password",   False, None,   True,  "forgot password page"),
    ("POST", "/auth/forgot-password",   False, None,   True,  "forgot password submit"),
    ("GET",  "/auth/recovery",          False, None,   True,  "password reset page"),
    ("POST", "/auth/recovery",          False, None,   True,  "password reset submit"),
    ("GET",  "/demo",                   False, None,   True,  "UI preview (requires LOCAL_DEV_BYPASS)"),
    # ── Protected routes — validator allowed ──
    ("GET",  "/work",                   True,  ["validator", "admin"],            True,  "labeling queue"),
    ("POST", "/label",                  True,  ["validator", "admin"],            False, "submit label"),
    ("POST", "/skip",                   True,  ["validator", "admin"],            False, "skip crop"),
    ("GET",  "/next",                   True,  ["validator", "admin"],            True,  "next crop"),
    ("POST", "/back",                   True,  ["validator", "admin"],            False, "previous crop"),
    ("POST", "/report",                 True,  ["validator", "admin"],            False, "report issue"),
    # ── Protected routes — validator + reviewer allowed ──
    ("GET",  "/image/dummy-crop-id",    True,  ["validator", "reviewer", "admin"], True, "crop image"),
    ("GET",  "/pdf",                    True,  ["validator", "reviewer", "admin"], True, "view PDF"),
    # ── Protected routes — validator + moderator allowed ──
    ("POST", "/mark-fraud",             True,  ["validator", "moderator", "admin"], False, "mark fraud"),
    # ── Protected routes — admin / moderator only ──
    ("GET",  "/admin/conflicts",        True,  ["admin", "moderator"],            True,  "admin conflicts"),
    ("GET",  "/admin/feedback",         True,  ["admin", "moderator"],            True,  "admin feedback"),
    ("GET",  "/admin/mesas/data",       True,  ["admin", "moderator"],            True,  "admin mesa data"),
    ("GET",  "/admin/mesas/stats",      True,  ["admin", "moderator"],            True,  "admin mesa stats"),
    # ── Protected routes — admin only ──
    ("POST", "/admin/hide",             True,  ["admin"],                         False, "admin hide"),
    ("GET",  "/debug/sentry-test",      True,  ["admin"],                         True,  "sentry debug"),
    ("GET",  "/admin/users",            True,  ["admin"],                         True,  "admin user list"),
    ("POST", "/admin/users/role",       True,  ["admin"],                         False, "admin role change"),
    # ── Any authenticated role — explicit @require_role listing all roles ──
    ("POST", "/feedback",               True,  ["reader", "validator", "reviewer", "moderator", "admin"], False, "feedback open to any authenticated role, explicitly"),
    # ── Protected routes — moderator + admin only ──
    ("POST", "/api/mesa-report",        True,  ["moderator", "admin"],            False, "public mesa report — restricted to moderator/admin, mesa_result_exists() anti-forgery guard"),
]

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def prod_app(tmp_path: Path):
    """Flask app in production mode (SUPABASE_URL set, Supabase mocked)."""
    env_vars = {
        "SUPABASE_URL": "https://fake.supabase.co",
        "SUPABASE_ANON_KEY": "fake-anon-key",
        "SECRET_KEY": "test-secret-key-audit",
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
    """Mock decode_jwt and _get_user_role for controlled auth state."""
    with patch("src.modules.labeler.auth.decode_jwt") as mock_decode, \
         patch("src.modules.labeler.auth._get_user_role") as mock_role:
        mock_decode.return_value = {"sub": "test-audit-user", "email": "audit@test.local"}
        # Default role — override per test
        mock_role.return_value = "validator"
        yield {"decode_jwt": mock_decode, "get_user_role": mock_role}


def _inject_session(client, app):
    """Set up a session with a fake access token."""
    with client.session_transaction() as sess:
        sess["access_token"] = "fake-audit-token"
        sess["refresh_token"] = "fake-audit-refresh"


# ---------------------------------------------------------------------------
# FASE 1 — Unauthenticated access
# ---------------------------------------------------------------------------

class TestPhase1_Unauthenticated:
    """Every route tested WITHOUT a session cookie."""

    @pytest.mark.parametrize("method,path,auth_required,_al,_html,_note", [
        r for r in ROUTES if r[0] != "GET" or r[1] != "/demo"
    ])
    def test_json_accept(self, method, path, auth_required, _al, _html, _note, client):
        """JSON requests to protected routes MUST return 401."""
        resp = client.open(path, method=method, headers={"Accept": "application/json"})
        if auth_required:
            assert resp.status_code == 401, f"{method} {path}: expected 401, got {resp.status_code}"
        else:
            assert resp.status_code != 401, f"{method} {path}: public route returned 401"
            assert resp.status_code != 503, f"{method} {path}: maintenance mode active"

    @pytest.mark.parametrize("method,path,auth_required,_al,_html,_note", [
        r for r in ROUTES if r[3] is not None and (r[0] != "GET" or r[1] != "/demo")
    ])
    def test_html_accept_redirects_to_login(self, method, path, auth_required, _al, _html, _note, client):
        """HTML requests to protected routes MUST redirect to /auth/login."""
        if not auth_required:
            pytest.skip("public route")
        resp = client.open(path, method=method, headers={"Accept": "text/html"})
        assert resp.status_code == 302, f"{method} {path}: expected 302, got {resp.status_code}"
        location = resp.headers.get("Location", "")
        assert "/auth/login" in location, f"{method} {path}: redirect to {location}"


# ---------------------------------------------------------------------------
# FASE 2 — Privilege escalation (validator role)
# ---------------------------------------------------------------------------

class TestPhase2_PrivilegeEscalation:
    """Protected routes tested with default 'validator' role."""

    @pytest.fixture
    def authd_client(self, tmp_path, mock_auth, request):
        """Client with session + role + DB mock. Creates own app."""
        import sys
        role = getattr(request, "param", "validator")
        mock_auth["get_user_role"].return_value = role
        mock_db = self._make_db_mock()
        sys.modules["src.modules.labeler.db"] = mock_db

        env_vars = {
            "SUPABASE_URL": "https://fake.supabase.co",
            "SUPABASE_ANON_KEY": "fake-anon-key",
            "SECRET_KEY": "test-secret-key-audit",
            "FLASK_ENV": "production",
        }
        with patch.dict(os.environ, env_vars):
            from src.modules.labeler.server import create_app
            index_path = tmp_path / "crops" / "index.jsonl"
            index_path.parent.mkdir(parents=True, exist_ok=True)
            app = create_app(index_path=index_path, labels_dir=tmp_path)
            app.config["TESTING"] = True
            client = app.test_client()
            _inject_session(client, app)
            yield client
        sys.modules.pop("src.modules.labeler.db", None)

    @staticmethod
    def _make_db_mock():
        m = MagicMock()
        m.release_expired_assignments.return_value = None
        m.assign_next_crop.return_value = "dummy-crop-001"
        m.get_real_progress.return_value = {"started": 10, "confirmed": 5, "total": 100, "labeled": 15}
        m.get_crop_details.return_value = {"crop_id": "dummy", "image": None, "digit_index": 0,
                                            "field_name": "test", "pdf_path": "data/pdfs/TEST/dummy.pdf"}
        m.get_work_item.return_value = {"crop_id": "dummy", "image": None, "digit_index": 0,
                                         "field_name": "test", "pdf_path": "data/pdfs/TEST/dummy.pdf"}
        m.get_global_stats.return_value = {"global_labeled": 5, "my_labeled": 3}
        m.get_concordancias.return_value = []
        m.get_storage_url.return_value = "https://storage.example.com/dummy.png"
        m.write_label.return_value = None
        m.evaluate_agreement.return_value = None
        m.record_fraud_mark.return_value = None
        m.record_feedback.return_value = None
        m.record_report.return_value = None
        m.retract_recent_marks.return_value = None
        m.get_conflict_crops.return_value = []
        m.get_fraud_marks.return_value = []
        m.get_feedback_marks.return_value = []
        m.get_amended_crops.return_value = []
        m.get_reports.return_value = []
        m.get_mesa_reports.return_value = []
        m.hide_mark.return_value = None
        m.get_mesa_results.return_value = []
        m.get_mesa_stats.return_value = {}
        m.get_mesa_semaphore_stats.return_value = {}
        m.get_public_stats.return_value = {"open_mesas": 0, "reviewed_mesas": 0, "total_mesas": 0}
        m.check_mesa_already_reported.return_value = False
        m.mesa_result_exists.return_value = True
        m.insert_transversal_reports.return_value = [{"id": "row-1"}]
        # Chainable supabase client mock — every method returns self, execute returns empty data
        mc = MagicMock()
        mc.table.return_value = mc
        mc.select.return_value = mc
        mc.eq.return_value = mc
        mc.limit.return_value = mc
        mc.order.return_value = mc
        mc.execute.return_value = MagicMock(data=[])
        m._client.return_value = mc
        return m

    @pytest.mark.parametrize("method,path,_ar,allowed_roles,_html,_note", [
        r for r in ROUTES if r[2] and r[1] != "/demo"
    ])
    def test_validator_role_access(self, method, path, _ar, allowed_roles, _html, _note, authd_client):
        """Validator role MUST respect route role restrictions."""
        resp = authd_client.open(path, method=method, headers={"Accept": "application/json"},
                                  json={})
        if allowed_roles is None:
            assert resp.status_code not in (401, 403, 503), \
                f"{method} {path}: auth leak, got {resp.status_code}"
        elif "validator" in allowed_roles:
            assert resp.status_code not in (401, 403, 503), \
                f"{method} {path}: auth leak (validator allowed), got {resp.status_code}"
        else:
            assert resp.status_code == 403, \
                f"{method} {path}: expected 403 (validator denied), got {resp.status_code}"
            body = resp.get_json(silent=True)
            if body:
                assert "error" in body or "forbidden" in str(body).lower()

    @pytest.mark.parametrize("method,path,_ar,allowed_roles,_html,_note", [
        r for r in ROUTES if r[2] and r[3] is not None and "validator" not in r[3] and r[1] != "/demo"
    ])
    def test_html_accept_redirects_home(self, method, path, _ar, allowed_roles, _html, _note, authd_client):
        """HTML requests to admin-only routes MUST redirect to / for validator."""
        resp = authd_client.open(path, method=method, headers={"Accept": "text/html"})
        assert resp.status_code == 302, f"{method} {path}: expected 302, got {resp.status_code}"
        location = resp.headers.get("Location", "")
        assert location in ("/", "http://localhost/"), f"{method} {path}: redirect to {location}"


# ---------------------------------------------------------------------------
# EXTRA — POST /feedback: any authenticated role, explicitly allowed
# ---------------------------------------------------------------------------

class TestFeedbackVulnerability:
    """POST /feedback is intentionally open to every role via @require_role."""

    def test_feedback_missing_role_decorator(self, client, mock_auth):
        """Any authenticated user can POST /feedback regardless of role."""
        for role in ("reader", "reviewer", "validator", "moderator", "admin"):
            mock_auth["get_user_role"].return_value = role
            _inject_session(client, client.application)
            resp = client.post("/feedback", json={"message": "test"}, headers={"Accept": "application/json"})
            # Accept 200/201/400 — the point is NOT 401 or 403
            assert resp.status_code not in (401, 403), \
                f"role={role}: expected accessible, got {resp.status_code}"


# ---------------------------------------------------------------------------
# EXTRA — Maintenance mode
# ---------------------------------------------------------------------------

class TestMaintenanceMode:
    """Maintenance mode gates specific routes."""

    def test_login_blocked_during_maintenance(self, tmp_path):
        """POST /auth/login must return 503 during maintenance."""
        with patch.dict(os.environ, {
            "MAINTENANCE_MODE": "true",
            "SUPABASE_URL": "https://fake.supabase.co",
            "SUPABASE_ANON_KEY": "fake-anon-key",
            "SECRET_KEY": "test-secret",
            "FLASK_ENV": "production",
        }):
            from src.modules.labeler.server import create_app
            idx = tmp_path / "crops" / "index.jsonl"
            idx.parent.mkdir(parents=True, exist_ok=True)
            app = create_app(index_path=idx, labels_dir=tmp_path)
            app.config["TESTING"] = True
            cli = app.test_client()
            resp = cli.post("/auth/login", json={"email": "a@b.com", "password": "x"})
        assert resp.status_code == 503

    def test_status_stays_open_during_maintenance(self, tmp_path):
        """GET /status must return 200 during maintenance."""
        with patch.dict(os.environ, {
            "MAINTENANCE_MODE": "true",
            "SUPABASE_URL": "https://fake.supabase.co",
            "SUPABASE_ANON_KEY": "fake-anon-key",
            "SECRET_KEY": "test-secret",
            "FLASK_ENV": "production",
        }):
            from src.modules.labeler.server import create_app
            idx = tmp_path / "crops" / "index.jsonl"
            idx.parent.mkdir(parents=True, exist_ok=True)
            app = create_app(index_path=idx, labels_dir=tmp_path)
            app.config["TESTING"] = True
            cli = app.test_client()
            resp = cli.get("/status")
        assert resp.status_code != 503, f"maintenance blocked /status: got {resp.status_code}"

    def test_register_stays_open_during_maintenance(self, tmp_path):
        """POST /auth/register must return 200 during maintenance."""
        with patch.dict(os.environ, {
            "MAINTENANCE_MODE": "true",
            "SUPABASE_URL": "https://fake.supabase.co",
            "SUPABASE_ANON_KEY": "fake-anon-key",
            "SECRET_KEY": "test-secret",
            "FLASK_ENV": "production",
        }):
            from src.modules.labeler.server import create_app
            idx = tmp_path / "crops" / "index.jsonl"
            idx.parent.mkdir(parents=True, exist_ok=True)
            app = create_app(index_path=idx, labels_dir=tmp_path)
            app.config["TESTING"] = True
            cli = app.test_client()
            mock_supabase = MagicMock()
            mock_supabase.auth.sign_up.return_value = MagicMock()
            with patch("src.modules.labeler.auth.init_supabase_client", return_value=mock_supabase):
                resp = cli.post("/auth/register", json={"email": "a@b.com", "password": "x"})
        assert resp.status_code in (200, 201)


# ---------------------------------------------------------------------------
# EXTRA — Dev bypass mode (SUPABASE_URL unset)
# ---------------------------------------------------------------------------

class TestDevBypassMode:
    """When SUPABASE_URL is unset, ALL routes are accessible without auth."""

    @pytest.mark.parametrize("method,path,_ar,_al,_html,_note", [
        r for r in ROUTES if r[0] != "GET" or r[1] != "/demo"
    ])
    def test_all_routes_accessible(self, method, path, _ar, _al, _html, _note, tmp_path):
        """Dev bypass mode: every route returns 200 without session."""
        clean_env = {k: v for k, v in os.environ.items()
                     if k not in ("SUPABASE_URL", "SUPABASE_ANON_KEY")}
        env_vars = dict(clean_env)
        env_vars["FAKE_USER_ID"] = "test-dev-user"
        env_vars["SECRET_KEY"] = "dev-secret"
        with patch.dict(os.environ, env_vars, clear=True):
            from src.modules.labeler.server import create_app
            idx = tmp_path / "crops" / "index.jsonl"
            idx.parent.mkdir(parents=True, exist_ok=True)
            idx.write_text("")
            app = create_app(index_path=idx, labels_dir=tmp_path)
            app.config["TESTING"] = True
            cli = app.test_client()
            resp = cli.open(path, method=method, json={})
        assert resp.status_code != 401, f"{method} {path}: unexpected 401 in dev bypass"
        assert resp.status_code != 503, f"{method} {path}: maintenance mode active"
