"""
server — Flask labeling portal for the E-14C digit validation web portal.

Two operation modes (ADR-4, invariant I5):

LOCAL DEV MODE  (SUPABASE_URL unset):
  All state lives in the module-level _STATE singleton (ValidationQueue +
  ManifestWriter).  The dev server runs single-threaded so no lock is needed
  between requests (ManifestWriter carries its own lock for threaded=True).
  Routes: GET /, GET /image/<id>, POST /label, POST /skip, POST /back,
          GET /pdf, GET /status

PRODUCTION MODE  (SUPABASE_URL set):
  Stateless per-request flow backed by Supabase Postgres (db.py) and JWT auth
  (auth.py).  The _STATE singleton is NOT used.
  Routes (auth-protected): GET /, GET /image/<id>, POST /label, POST /skip,
  Auth routes (no auth): GET/POST /auth/login, GET/POST /auth/register,
                         GET /auth/confirm, POST /auth/logout
  Admin routes: GET /admin/conflicts
  Public: GET /status

create_app(index_path, labels_dir) signature is preserved for I5.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from flask import Flask, Response, g, jsonify, redirect, render_template, request, send_file, session

logger = logging.getLogger("labeler")


def _error_response(message: str, status: int = 400) -> Response:
    """Return JSON error + log + Sentry capture for business-logic 4xx errors."""
    logger.warning("error %s: %s", status, message)
    try:
        import sentry_sdk
        sentry_sdk.capture_message(f"{status}: {message}", level="warning")
    except Exception:
        pass
    return jsonify({"ok": False, "error": message}), status


def _configure_logging() -> None:
    """Configure root logger once. JSON-ish line format to stdout (Railway captures it)."""
    root = logging.getLogger()
    if getattr(_configure_logging, "_done", False):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s rid=%(request_id)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    handler.addFilter(_RequestIdFilter())
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    _configure_logging._done = True  # type: ignore[attr-defined]


class _RequestIdFilter(logging.Filter):
    """Inject Flask g.request_id into log records (or '-' if outside request ctx)."""
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.request_id = getattr(g, "request_id", "-")
        except Exception:
            record.request_id = "-"
        return True


def _init_sentry() -> bool:
    """Initialize Sentry if SENTRY_DSN is set. Returns True if active."""
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration

        env = os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("FLASK_ENV") or "local"
        try:
            from src.version import __version__ as _v
        except Exception:
            _v = "dev"
        sentry_sdk.init(
            dsn=dsn,
            environment=env,
            release=f"analizador-e14@{_v}",
            integrations=[
                FlaskIntegration(),
                LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
            ],
            traces_sample_rate=0.0,
            send_default_pii=False,
        )
        return True
    except Exception as exc:
        logger.error("sentry init failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# reCAPTCHA Enterprise verification helper
# ---------------------------------------------------------------------------

def _verify_recaptcha(response_token: str, action: str = "") -> bool:
    """
    Verify a reCAPTCHA Enterprise token using the Assessment API.

    Args:
        response_token: The ``g-recaptcha-response`` value from the frontend.
        action: The expected action name (e.g. "register", "login", "forgot").

    Returns:
        True if the token is valid, False otherwise.
    """
    site_key = os.environ.get("RECAPTCHA_SITE_KEY", "").strip()
    project_id = os.environ.get("RECAPTCHA_PROJECT_ID", "").strip()
    api_key = os.environ.get("RECAPTCHA_API_KEY", "").strip()
    if not site_key or not project_id or not api_key:
        # No key configured — skip verification (local dev or misconfig).
        return True
    try:
        import requests as _req
        resp = _req.post(
            f"https://recaptchaenterprise.googleapis.com/v1/projects/{project_id}/assessments?key={api_key}",
            json={
                "event": {
                    "token": response_token,
                    "siteKey": site_key,
                    "expectedAction": action,
                }
            },
            timeout=10,
        )
        result = resp.json()
        # The assessment is valid if the token was successfully verified.
        # score is 0.0-1.0 for score-based keys; for checkbox keys it's 0.0.
        return result.get("tokenProperties", {}).get("valid", False)
    except Exception as exc:
        logger.error("recaptcha verify failed: %s", exc)
        try:
            import sentry_sdk
            sentry_sdk.capture_exception(exc)
        except Exception:
            pass
        # Fail open — better to let a human through than block them on error.
        return True


# ---------------------------------------------------------------------------
# Divipole lookup — puesto nombre (LUGAR)
# ---------------------------------------------------------------------------

_DIVIPOLE: dict = {}


def _load_divipole(root: Path) -> None:
    global _DIVIPOLE
    path = root / "data" / "divipole.json"
    if path.exists():
        try:
            _DIVIPOLE = json.loads(path.read_text(encoding="utf-8")).get("departamentos", {})
        except Exception:
            _DIVIPOLE = {}


# ---------------------------------------------------------------------------
# Acta fraud flags — why an acta is suspicious (from CNN analysis output)
# ---------------------------------------------------------------------------

_ACTA_FLAGS: dict = {}  # pdf_path → list of flag strings

# Human-readable explanations for each flag prefix
_FLAG_DESCRIPTIONS = {
    "ARITMETICA_SUMA": "La suma total escrita no coincide con la suma de los votos por candidato + blancos + nulos.",
    "URNA_VS_SUMA": "El total de votos en la urna no coincide con la suma total del acta.",
    "VOTOS_EXCEDEN_VOTANTES": "Hay más votos registrados que votantes habilitados en la mesa.",
    "VALOR_NEGATIVO": "Se detectó un valor negativo, imposible en un conteo de votos.",
}


def _load_acta_flags(root: Path) -> None:
    """Load pdf_path → flags from data/suspicious_summary.jsonl (CNN analysis)."""
    global _ACTA_FLAGS
    path = root / "data" / "suspicious_summary.jsonl"
    if not path.exists():
        return
    flags: dict = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    pdf = row.get("pdf_path", "")
                    if pdf and row.get("flags"):
                        flags[pdf] = row["flags"]
                except json.JSONDecodeError:
                    continue
        _ACTA_FLAGS = flags
    except Exception:
        _ACTA_FLAGS = {}


def _get_acta_flags(pdf_path: str) -> list[dict]:
    """
    Return a list of {raw, description} for each fraud flag of the acta.
    Empty list if the acta has no recorded flags.
    """
    raw_flags = _ACTA_FLAGS.get(pdf_path, [])
    result = []
    for flag in raw_flags:
        prefix = flag.split(":")[0].strip()
        detail = flag.split(":", 1)[1].strip() if ":" in flag else ""
        result.append({
            "raw": flag,
            "type": prefix,
            "detail": detail,
            "description": _FLAG_DESCRIPTIONS.get(prefix, "Discrepancia detectada en el acta."),
        })
    return result


# ---------------------------------------------------------------------------
# Launch gate — public countdown before labeling opens
# ---------------------------------------------------------------------------

# Default launch: 2026-06-04 10:00 Colombia time (UTC-5). Override with env
# LAUNCH_AT (ISO 8601). Set LAUNCH_AT=off to disable the gate (labeling open).
_DEFAULT_LAUNCH = "2026-06-04T10:00:00-05:00"


def _launch_state() -> dict:
    """Return {is_open: bool, launch_iso: str} for the labeling launch gate."""
    raw = os.environ.get("LAUNCH_AT", _DEFAULT_LAUNCH).strip()
    if raw.lower() in ("off", "open", ""):
        return {"is_open": True, "launch_iso": ""}
    try:
        target = datetime.fromisoformat(raw)
    except ValueError:
        return {"is_open": True, "launch_iso": ""}
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return {"is_open": now >= target, "launch_iso": target.isoformat()}


_FRAUD_MARKS_PATH = Path("data/fraud_marks.jsonl")


def _record_fraud_mark(pdf_path: str, reason: str, annotator: str) -> None:
    """Append a manual fraud mark (acta + reason) to data/fraud_marks.jsonl."""
    _FRAUD_MARKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "pdf_path": pdf_path,
        "reason": reason,
        "annotator": annotator,
        "marked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with open(_FRAUD_MARKS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _mesa_info(pdf_path: str) -> dict:
    """Extract mesa metadata from the PDF directory path + divipole lookup."""
    parts = Path(pdf_path).parts  # e.g. ['data','pdfs','AMAZONAS','LETICIA','zona_01','puesto_03','E14_...pdf']
    info = {"departamento": "", "municipio": "", "zona": "", "puesto": "", "mesa": "", "lugar": ""}
    try:
        # Directory structure: data/pdfs/{DEPT}/{MPIO}/zona_{z}/puesto_{p}/{filename}
        idx = next(i for i, p in enumerate(parts) if p == "pdfs")
        info["departamento"] = parts[idx + 1] if idx + 1 < len(parts) else ""
        info["municipio"]    = parts[idx + 2] if idx + 2 < len(parts) else ""
        zona_str  = parts[idx + 3] if idx + 3 < len(parts) else ""  # zona_01
        puesto_str= parts[idx + 4] if idx + 4 < len(parts) else ""  # puesto_03
        info["zona"]   = zona_str.replace("zona_", "").lstrip("0") or "0"
        info["puesto"] = puesto_str.replace("puesto_", "").lstrip("0") or "0"

        # Mesa from filename: E14_PRE_60_001_001_00_03_002_6948 → segment[-2] = '002'
        stem = Path(pdf_path).stem  # E14_PRE_60_001_001_00_03_002_6948
        segs = stem.split("_")
        if len(segs) >= 2:
            info["mesa"] = segs[-2].lstrip("0") or "0"

        # Divipole LUGAR lookup using filename codes: dept=segs[2], mpio=segs[3], zona=segs[5], puesto=segs[6]
        if _DIVIPOLE and len(segs) >= 7:
            dept_code  = segs[2]          # '60'
            mpio_code  = segs[3]          # '001'
            zona_code  = segs[5]          # '00' or '01'
            puesto_code= segs[6]          # '03'
            dept = _DIVIPOLE.get(dept_code, {})
            mpio = dept.get("municipios", {}).get(mpio_code, {})
            zona = mpio.get("zonas", {}).get(zona_code, {})
            puesto = zona.get("puestos", {}).get(puesto_code, {})
            info["lugar"] = puesto.get("nombre", "")
    except Exception:
        pass
    return info

from src.modules.labeler.manifest import ManifestWriter
from src.modules.labeler.models import QueueItem
from src.modules.labeler.queue import ValidationQueue


# ---------------------------------------------------------------------------
# Auto-skip heuristics — check PNG before presenting to human
# ---------------------------------------------------------------------------

def _get_full_cell_crop_id(crop_id: str, index_path: Path) -> str:
    """Look up full_cell_crop_id from local index.jsonl for a given crop_id."""
    if not index_path.exists():
        return crop_id
    try:
        with open(index_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    if row.get("crop_id") == crop_id:
                        return row.get("full_cell_crop_id") or crop_id
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass
    return crop_id


def _get_concordancias(pdf_path: str, index_path: Path, label_ocr: str, limit: int = 5) -> list[str]:
    """
    Returns crop_ids from the same PDF whose label_ocr matches the current digit.
    Reads index.jsonl so it works even before any human labeling is done.
    """
    if not index_path.exists() or not label_ocr or label_ocr == "?":
        return []
    matches: list[str] = []
    try:
        with open(index_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("pdf_path") != pdf_path:
                    continue
                if row.get("label_ocr") != label_ocr:
                    continue
                cid = row.get("crop_id", "")
                if cid and cid not in matches:
                    matches.append(cid)
                if len(matches) >= limit:
                    break
    except Exception:
        pass
    return matches


def _should_auto_skip(png_path: Path) -> str:
    """
    Returns a reason string if the crop should be auto-skipped, else ''.

    Reasons:
      'blank'    — ink ratio < 2% (empty cell, no digits written)
      'border'   — aspect ratio h/w > 6 (vertical cell divider line)
    """
    try:
        import cv2
        import numpy as np
        img = cv2.imread(str(png_path), cv2.IMREAD_GRAYSCALE)
        if img is None:
            return ""
        h, w = img.shape
        if w == 0:
            return ""
        # Blank cell: almost no dark pixels
        _, bw = cv2.threshold(img, 120, 255, cv2.THRESH_BINARY_INV)
        ink_ratio = (bw.sum() / 255) / (h * w)
        if ink_ratio < 0.02:
            return "blank"
        # Vertical border line: very tall and very narrow
        if h > 0 and (w / h) < 0.15:
            return "border"
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Session state singleton (local dev mode only — ADR-4, invariant I5)
# ---------------------------------------------------------------------------

class SessionState:
    """Module-level singleton — holds queue + manifest + counters."""

    def __init__(self, queue: ValidationQueue, manifest: ManifestWriter, labels_dir: Path) -> None:
        self.queue = queue
        self.manifest = manifest
        self.labels_dir = labels_dir
        self.crops_dir = labels_dir / "crops"
        self.recent: list[dict] = []  # last 10 labeled items for the UI log

    def push_recent(self, crop_id: str, label: str, field_name: str, label_ocr: str = "", confidence: float = 0.0) -> None:
        self.recent.insert(0, {
            "crop_id": crop_id,
            "label": label,
            "field": field_name,
            "ocr": label_ocr,
            "conf": round(confidence * 100),
        })
        self.recent = self.recent[:30]


_STATE: Optional[SessionState] = None


# ---------------------------------------------------------------------------
# Value-token validation (spec: inter-annotator-agreement / Label Submission)
# ---------------------------------------------------------------------------

import re as _re

# Accepted value tokens: "", "0"–"9", "E<digit>", "*", "-", "."
_VALUE_TOKEN_PATTERN = _re.compile(r'^(|[0-9]{1,5}|E[0-9]{1,5}|\*|-|\.|\+)$')


def _parse_value_token(raw: str) -> tuple[str, bool]:
    """
    Validate and parse a label value token.

    Returns:
        (label_human, amended) tuple.

    Raises:
        ValueError: if the token is not in the accepted set.
    """
    if not _VALUE_TOKEN_PATTERN.match(raw):
        raise ValueError(f"Invalid value token: {raw!r}")
    amended = raw.startswith("E")
    label_human = raw[1:] if amended else raw
    return label_human, amended


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(index_path: Path, labels_dir: Path) -> Flask:
    """
    Build and configure the Flask application.

    Operates in two modes (ADR-4, invariant I5):
    - LOCAL DEV: SUPABASE_URL unset → _STATE singleton, local file routes
    - PRODUCTION: SUPABASE_URL set  → Supabase db + auth; raises on missing SECRET_KEY

    Args:
        index_path: Path to data/labels/crops/index.jsonl.
        labels_dir: Root of data/labels/ tree.

    Returns:
        Configured Flask app. Call app.run() in the CLI handler.
    """
    global _STATE  # noqa: WPS420

    # ----------------------------------------------------------------
    # Observability: structured logging + Sentry (no-op if SENTRY_DSN unset)
    # ----------------------------------------------------------------
    _configure_logging()
    _sentry_active = _init_sentry()
    logger.info("startup sentry=%s", "on" if _sentry_active else "off")

    # ----------------------------------------------------------------
    # Mode detection (ADR-4)
    # ----------------------------------------------------------------
    _supabase_url = os.environ.get("SUPABASE_URL", "").strip()
    _production_mode = bool(_supabase_url)

    templates_dir = Path(__file__).parent / "templates"
    app = Flask(__name__, template_folder=str(templates_dir))

    # ----------------------------------------------------------------
    # Request correlation + global exception handler
    # ----------------------------------------------------------------
    @app.before_request
    def _assign_request_id() -> None:
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        g.request_id = rid

    @app.after_request
    def _emit_request_id(resp: Response) -> Response:
        rid = getattr(g, "request_id", None)
        if rid:
            resp.headers["X-Request-ID"] = rid
        # Security headers — upgrade any HTTP subresource to HTTPS (eliminates
        # mixed-content browser warnings) and enable HSTS for repeat visits.
        resp.headers["Content-Security-Policy"] = "upgrade-insecure-requests"
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return resp

    @app.errorhandler(Exception)
    def _unhandled(exc: Exception):
        rid = getattr(g, "request_id", "-")
        from werkzeug.exceptions import HTTPException
        if isinstance(exc, HTTPException):
            return exc
        logger.exception("unhandled exception in %s %s", request.method, request.path)
        try:
            import sentry_sdk
            sentry_sdk.capture_exception(exc)
        except Exception:
            pass
        body = {
            "error": "internal_server_error",
            "request_id": rid,
            "message": "Algo falló. Reportá este ID si el problema persiste.",
        }
        return jsonify(body), 500

    # Expose the project version to every template (both modes).
    try:
        from src.version import __version__ as _app_version
    except Exception:
        _app_version = "dev"

    _sentry_dsn = os.environ.get("SENTRY_DSN", "").strip()
    _flask_env = os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("FLASK_ENV") or "local"

    @app.context_processor
    def _inject_globals() -> dict:
        # work_url: where the labeling SPA lives (differs by mode).
        return {
            "app_version": _app_version,
            "work_url": "/work" if _production_mode else "/",
            "sentry_dsn": _sentry_dsn,
            "flask_env": _flask_env,
            "RECAPTCHA_SITE_KEY": os.environ.get("RECAPTCHA_SITE_KEY", "").strip(),
        }

    # Load lookup tables for both modes (mesa info + fraud flags)
    _load_divipole(Path.cwd())
    _load_acta_flags(Path.cwd())

    if _production_mode:
        # --- Production: validate required env vars at startup (not at request time) ---
        _supabase_anon_key = os.environ.get("SUPABASE_ANON_KEY", "").strip()
        _secret_key = os.environ.get("SECRET_KEY", "").strip()

        if not _supabase_anon_key:
            raise RuntimeError(
                "Missing required environment variable: SUPABASE_ANON_KEY. "
                "Set it to your Supabase project anon/public key."
            )
        if not _secret_key:
            raise RuntimeError(
                "Missing required environment variable: SECRET_KEY. "
                "Set it to a strong random string used to sign Flask session cookies."
            )

        app.config["SECRET_KEY"] = _secret_key
        app.config["SESSION_COOKIE_HTTPONLY"] = True
        app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
        # Secure flag only in production (Railway sets RAILWAY_ENVIRONMENT)
        if os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("FLASK_ENV") == "production":
            app.config["SESSION_COOKIE_SECURE"] = True

        # Import auth + db modules (they guard their own client init)
        from src.modules.labeler.auth import require_auth
        import src.modules.labeler.db as _db

        _use_storage = os.environ.get("USE_SUPABASE_STORAGE", "false").lower() == "true"
        _labels_dir_env = Path(os.environ.get("LABELS_DIR", str(labels_dir))).resolve()
        _index_path = _labels_dir_env / "crops" / "index.jsonl"

        # ----------------------------------------------------------------
        # Demo route — UI preview only, no DB, only when LOCAL_DEV_BYPASS set
        # ----------------------------------------------------------------

        if os.environ.get("LOCAL_DEV_BYPASS", "").strip():
            @app.route("/demo")
            def demo_view() -> str:
                _FAKE_CROP_ID = "00000000-0000-0000-0000-000000000001"
                return render_template(
                    "label.html",
                    done=False,
                    crop_id=_FAKE_CROP_ID,
                    full_cell_crop_id=_FAKE_CROP_ID,
                    label_ocr="7",
                    field_name="votos_blanco",
                    digit_index=0,
                    is_fallback=False,
                    labeled=1284,
                    my_labeled=47,
                    remaining=14167,
                    total=15451,
                    priority=1,
                    pdf_filename="E14_PRE_60_001_001_00_01_003_6947.pdf",
                    mesa={"departamento": "BOLIVAR", "municipio": "CARTAGENA", "zona": "zona_01", "puesto": "puesto_01", "mesa": "053", "lugar": "COLEGIO DISTRITAL"},
                    recent=[
                        {"crop_id": _FAKE_CROP_ID, "label": "4", "field": "votos_candidato_1", "ocr": "4", "conf": 98},
                        {"crop_id": _FAKE_CROP_ID, "label": "2", "field": "votos_candidato_2", "ocr": "2", "conf": 91},
                        {"crop_id": _FAKE_CROP_ID, "label": "0", "field": "votos_blanco", "ocr": "0", "conf": 85},
                    ],
                    concordancias=[_FAKE_CROP_ID, _FAKE_CROP_ID, _FAKE_CROP_ID],
                    acta_flags=[{"type": "ARITMETICA_SUMA", "description": "La suma total escrita no coincide con la suma de los votos por candidato + blancos + nulos.", "detail": "suma_total=114 != calculado=109"}],
                    user_email="demo@local.test",
                )

        # ----------------------------------------------------------------
        # Auth routes (no auth required)
        # ----------------------------------------------------------------

        @app.route("/auth/login", methods=["GET"])
        def auth_login_get() -> str:
            return render_template("login.html")

        @app.route("/auth/register", methods=["GET"])
        def auth_register_get() -> str:
            return render_template("register.html")

        @app.route("/auth/register", methods=["POST"])
        def auth_register_post() -> Response:
            body = request.get_json(force=True, silent=True) or {}
            # Support both JSON and form data
            if not body:
                body = {
                    "email": request.form.get("email", ""),
                    "password": request.form.get("password", ""),
                    "first_name": request.form.get("first_name", ""),
                    "last_name": request.form.get("last_name", ""),
                    "phone": request.form.get("phone", ""),
                    "g_recaptcha_response": request.form.get("g-recaptcha-response", ""),
                }
            email = body.get("email", "").strip()
            password = body.get("password", "")
            first_name = body.get("first_name", "").strip()
            last_name = body.get("last_name", "").strip()
            phone = body.get("phone", "").strip()

            if not email or not password:
                return jsonify({"error": "email and password are required"}), 400

            # reCAPTCHA verification
            recaptcha_token = body.get("g_recaptcha_response", body.get("g-recaptcha-response", ""))
            if not _verify_recaptcha(recaptcha_token, "register"):
                return jsonify({"error": "Verificación de seguridad fallada. Recargá la página e intentá de nuevo."}), 403

            from src.modules.labeler.auth import init_supabase_client
            client = init_supabase_client()
            try:
                # Store profile fields as Supabase user_metadata (no schema change).
                client.auth.sign_up({
                    "email": email,
                    "password": password,
                    "options": {"data": {
                        "first_name": first_name,
                        "last_name": last_name,
                        "phone": phone,
                        "full_name": (first_name + " " + last_name).strip(),
                    }},
                })
                return jsonify({"message": "check your email"}), 201
            except Exception as exc:
                err_str = str(exc).lower()
                if "already registered" in err_str or "already exists" in err_str or "duplicate" in err_str:
                    logger.warning("register duplicate email=%s ip=%s", email, request.remote_addr)
                    return jsonify({"error": "email already registered"}), 409
                logger.error("register failed email=%s ip=%s: %s", email, request.remote_addr, exc)
                try:
                    import sentry_sdk
                    sentry_sdk.capture_exception(exc)
                except Exception:
                    pass
                return jsonify({"error": "registration failed", "detail": str(exc)}), 400

        @app.route("/auth/confirm", methods=["GET"])
        def auth_confirm() -> Response:
            token = request.args.get("token", "").strip()
            token_hash = request.args.get("token_hash", "").strip()
            token_type = request.args.get("type", "email").strip()

            if not token and not token_hash:
                return Response("Missing confirmation token", status=400)

            from src.modules.labeler.auth import init_supabase_client
            client = init_supabase_client()
            try:
                if token_hash:
                    # Supabase PKCE flow: verify OTP with token_hash
                    client.auth.verify_otp({"token_hash": token_hash, "type": token_type})
                else:
                    # Legacy token flow
                    client.auth.verify_otp({"token": token, "type": token_type})
                return redirect("/auth/login", code=302)
            except Exception as exc:
                return Response(f"Confirmation failed: {exc}", status=400)

        @app.route("/auth/forgot-password", methods=["GET"])
        def auth_forgot_get() -> str:
            return render_template("forgot_password.html")

        @app.route("/auth/forgot-password", methods=["POST"])
        def auth_forgot_post() -> Response:
            body = request.get_json(force=True, silent=True) or {}
            if not body:
                body = {
                    "email": request.form.get("email", ""),
                    "g_recaptcha_response": request.form.get("g-recaptcha-response", ""),
                }
            email = body.get("email", "").strip()
            if not email:
                return jsonify({"error": "Email is required"}), 400

            # reCAPTCHA verification
            recaptcha_token = body.get("g_recaptcha_response", body.get("g-recaptcha-response", ""))
            if not _verify_recaptcha(recaptcha_token, "forgot"):
                return jsonify({"error": "Verificación de seguridad fallada. Recargá la página e intentá de nuevo."}), 403

            try:
                import requests as _requests
                _supabase_url = os.environ.get("SUPABASE_URL", "").strip()
                _anon_key = os.environ.get("SUPABASE_ANON_KEY", "").strip()
                base_url = os.environ.get("RAILWAY_STATIC_URL", "http://localhost:5000")
                # Use REST API directly to ensure redirect_to is honored
                _requests.post(
                    f"{_supabase_url}/auth/v1/recover",
                    headers={
                        "apikey": _anon_key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "email": email,
                        "redirect_to": f"{base_url}/auth/recovery",
                    },
                    timeout=15,
                )
            except Exception as exc:
                logger.error("forgot-password email=%s ip=%s: %s", email, request.remote_addr, exc)
                try:
                    import sentry_sdk
                    sentry_sdk.capture_exception(exc)
                except Exception:
                    pass
            return jsonify({"message": "check your email"}), 200

        @app.route("/auth/recovery", methods=["GET"])
        def auth_recovery_get() -> str:
            token_hash = request.args.get("token_hash", "").strip()
            if not token_hash:
                return render_template("reset_password.html", error="Link inválido o faltante.")
            from src.modules.labeler.auth import init_supabase_client
            client = init_supabase_client()
            try:
                client.auth.verify_otp({"token_hash": token_hash, "type": "recovery"})
                session["recovery_verified"] = True
                return render_template("reset_password.html", token_hash=token_hash)
            except Exception as exc:
                err_str = str(exc).lower()
                logger.warning("recovery verify token ip=%s: %s", request.remote_addr, exc)
                try:
                    import sentry_sdk
                    sentry_sdk.capture_exception(exc)
                except Exception:
                    pass
                if "expired" in err_str or "invalid" in err_str:
                    return render_template("reset_password.html", error="El link expiró o es inválido. Solicitá uno nuevo.")
                return render_template("reset_password.html", error="Error al verificar el link. Intentá de nuevo.")

        @app.route("/auth/recovery", methods=["POST"])
        def auth_recovery_post() -> Response:
            if not session.get("recovery_verified"):
                return jsonify({"error": "No verificaste tu identidad. Usá el link del correo."}), 403
            body = request.get_json(force=True, silent=True) or {}
            if not body:
                body = {"password": request.form.get("password", "")}
            new_password = body.get("password", "")
            if len(new_password) < 8:
                return jsonify({"error": "La contraseña debe tener al menos 8 caracteres."}), 400
            from src.modules.labeler.auth import init_supabase_client
            client = init_supabase_client()
            try:
                client.auth.update_user({"password": new_password})
                session.pop("recovery_verified", None)
                return jsonify({"message": "password updated", "redirect": "/auth/login?reset=1"}), 200
            except Exception as exc:
                logger.error("recovery update password ip=%s: %s", request.remote_addr, exc)
                try:
                    import sentry_sdk
                    sentry_sdk.capture_exception(exc)
                except Exception:
                    pass
                return jsonify({"error": "No se pudo actualizar la contraseña. Intentá de nuevo."}), 400

        @app.route("/auth/login", methods=["POST"])
        def auth_login_post() -> Response:
            body = request.get_json(force=True, silent=True) or {}
            if not body:
                body = {
                    "email": request.form.get("email", ""),
                    "password": request.form.get("password", ""),
                    "g_recaptcha_response": request.form.get("g-recaptcha-response", ""),
                }
            email = body.get("email", "").strip()
            password = body.get("password", "")

            if not email or not password:
                return jsonify({"error": "email and password are required"}), 400

            # reCAPTCHA verification
            recaptcha_token = body.get("g_recaptcha_response", body.get("g-recaptcha-response", ""))
            if not _verify_recaptcha(recaptcha_token, "login"):
                return jsonify({"error": "Verificación de seguridad fallada. Recargá la página e intentá de nuevo."}), 403

            from src.modules.labeler.auth import init_supabase_client
            client = init_supabase_client()
            try:
                response = client.auth.sign_in_with_password({"email": email, "password": password})
                sess = response.session
                if sess is None:
                    return jsonify({"error": "Login failed — no session returned"}), 401
                session["access_token"] = sess.access_token
                session["refresh_token"] = sess.refresh_token
                session["user_email"] = response.user.email if response.user else email
                return redirect("/", code=302)
            except Exception as exc:
                err_str = str(exc).lower()
                if "email not confirmed" in err_str or "not confirmed" in err_str:
                    logger.warning("login unconfirmed email=%s ip=%s", email, request.remote_addr)
                    return jsonify({"error": "Please confirm your email before logging in"}), 403
                logger.warning("login failed email=%s ip=%s: %s", email, request.remote_addr, exc)
                return jsonify({"error": "Invalid credentials"}), 401

        @app.route("/auth/logout", methods=["POST"])
        def auth_logout() -> Response:
            session.clear()
            return redirect("/auth/login", code=302)

        # ----------------------------------------------------------------
        # Public home / landing (no auth) — shows countdown + auth state
        # ----------------------------------------------------------------

        @app.route("/")
        def home_view() -> str:
            email = session.get("user_email", "")
            state = _launch_state()
            return render_template(
                "home.html",
                logged_in=bool(email),
                user_email=email,
                is_open=state["is_open"],
                launch_iso=state["launch_iso"],
            )

        # ----------------------------------------------------------------
        # Main labeling route (production) — gated by launch time
        # ----------------------------------------------------------------

        @app.route("/work")
        @require_auth
        def work_view() -> str:
            from flask import g
            if not _launch_state()["is_open"]:
                return redirect("/", code=302)
            _db.release_expired_assignments()
            crop_id = _db.assign_next_crop(g.user_id)

            # Real progress metrics
            try:
                progress = _db.get_real_progress()
                started = progress["started"]
                confirmed = progress["confirmed"]
                total = progress["total"]
            except Exception:
                started = 0
                confirmed = 0
                total = 0
            pct = round((started + confirmed) / (2 * total) * 100, 1) if total > 0 else 0

            if crop_id is None:
                done_reason = "queue_exhausted" if confirmed < total else "all_done"
                return render_template(
                    "label.html",
                    done=True,
                    done_reason=done_reason,
                    started=started,
                    confirmed=confirmed,
                    total=total,
                    pct=pct,
                    labeled=0,
                    user_email=session.get("user_email", ""),
                )

            crop = _db.get_crop_details(crop_id)

            # Full cell crop id — look up from local index (not stored in Supabase)
            full_cell_crop_id = crop.get("full_cell_crop_id") or crop_id

            # Recent log — stored in Flask session (per-user, last 30)
            recent = session.get("recent_labels", [])

            # Concordancias — same PDF, same label_ocr, from local index
            pdf_path = crop.get("pdf_path", "")
            label_ocr = crop.get("label_ocr") or "?"
            if label_ocr.lower() in ("undefined", "null", "none"):
                label_ocr = "?"
            concordancias = _db.get_concordancias(pdf_path, label_ocr, crop_id)

            # Global stats — fetch real values on page load, cache in session
            try:
                stats = _db.get_global_stats(g.user_id)
                global_labeled = stats["global_labeled"]
                my_labeled = stats["my_labeled"]
                session["global_labeled"] = global_labeled
                session["my_labeled"] = my_labeled
                session["total_count"] = total
            except Exception:
                global_labeled = session.get("global_labeled", 0)
                my_labeled = session.get("my_labeled", 0)
            remaining = max(0, total - global_labeled)

            return render_template(
                "label.html",
                done=False,
                crop_id=crop_id,
                full_cell_crop_id=full_cell_crop_id,
                label_ocr=label_ocr,
                field_name=crop.get("field_name", ""),
                digit_index=crop.get("digit_index", -1),
                is_fallback=(crop.get("digit_index", -1) == -1),
                labeled=global_labeled,
                my_labeled=my_labeled,
                remaining=remaining,
                total=total,
                started=started,
                confirmed=confirmed,
                pct=pct,
                priority=crop.get("priority", 2),
                pdf_filename=Path(pdf_path).name,
                mesa=_mesa_info(pdf_path),
                recent=recent,
                concordancias=concordancias,
                acta_flags=_get_acta_flags(pdf_path),
                user_email=session.get("user_email", ""),
            )

        # ----------------------------------------------------------------
        # POST /label (production)
        # ----------------------------------------------------------------

        @app.route("/label", methods=["POST"])
        @require_auth
        def label_view() -> Response:
            from flask import g
            body = request.get_json(force=True, silent=True) or {}
            crop_id = body.get("crop_id", "")
            raw_value = body.get("value", "")

            if not crop_id:
                return _error_response("crop_id is required", 400)

            # Validate token
            try:
                label_human, amended = _parse_value_token(raw_value)
            except ValueError as exc:
                return _error_response(str(exc), 400)

            # Validate assignment ownership: check an active assignment row exists
            try:
                _cli = _db._client()
                asgn_resp = (
                    _cli.table("assignments")
                    .select("crop_id")
                    .eq("crop_id", crop_id)
                    .eq("annotator_id", g.user_id)
                    .execute()
                )
                if not asgn_resp.data:
                    return _error_response("No active assignment for this crop", 403)
            except Exception as exc:
                return _error_response(f"Assignment check failed: {exc}", 500)

            # Determine if this is an admin resolution
            is_admin = _is_admin_user(g.user_id)

            try:
                _db.write_label(
                    crop_id=crop_id,
                    annotator_id=g.user_id,
                    label_human=label_human,
                    amended=amended,
                    is_admin=is_admin,
                )
                crop = _db.get_crop_details(crop_id)
                if (crop.get("annotation_count") or 0) >= 2 or is_admin:
                    _db.evaluate_agreement(crop_id)
            except Exception as exc:
                return _error_response(f"Label write failed: {exc}", 500)

            # Update recent log and labeled count in session
            recent = session.get("recent_labels", [])
            recent.insert(0, {
                "crop_id": crop_id,
                "label": label_human,
                "field": crop.get("field_name", ""),
                "ocr": crop.get("label_ocr", ""),
                "conf": round((crop.get("confidence") or 0) * 100),
            })
            session["recent_labels"] = recent[:30]
            session["global_labeled"] = session.get("global_labeled", 0) + 1
            session["my_labeled"] = session.get("my_labeled", 0) + 1
            session.modified = True

            return jsonify({"ok": True})

        # ----------------------------------------------------------------
        # POST /skip (production)
        # ----------------------------------------------------------------

        @app.route("/skip", methods=["POST"])
        @require_auth
        def skip_view() -> Response:
            from flask import g
            body = request.get_json(force=True, silent=True) or {}
            crop_id = body.get("crop_id", "")
            try:
                _cli = _db._client()
                # Delete active assignment
                _cli.table("assignments").delete().eq("annotator_id", g.user_id).eq("crop_id", crop_id).execute()
                # Insert a skip label so assign_next_crop excludes this crop for this user
                if crop_id:
                    try:
                        existing = _cli.table("labels").select("id").eq("crop_id", crop_id).eq("annotator_id", g.user_id).execute()
                        if not existing.data:
                            _cli.table("labels").insert({
                                "crop_id": crop_id,
                                "annotator_id": g.user_id,
                                "label_human": "_skip",
                                "amended": False,
                                "is_admin_resolution": False,
                            }).execute()
                    except Exception:
                        logger.warning("skip_view: _skip label insert failed for crop=%s user=%s", crop_id, g.user_id)
            except Exception as exc:
                return _error_response(f"Skip failed: {exc}", 500)
            return jsonify({"ok": True})

        # ----------------------------------------------------------------
        # GET /image/<crop_id> (production)
        # ----------------------------------------------------------------

        @app.route("/image/<crop_id>")
        @require_auth
        def image_view(crop_id: str) -> Response:
            # Sanitize: allow only safe characters
            if not crop_id.replace("-", "").replace("_", "").isalnum():
                return Response("Invalid crop_id", status=400)

            if _use_storage:
                storage_url = _db.get_storage_url(crop_id)
                return redirect(storage_url, code=302)
            else:
                png_path = _labels_dir_env / "crops" / f"{crop_id}.png"
                if not png_path.exists():
                    return Response("Not found", status=404)
                return send_file(str(png_path), mimetype="image/png")

        # ----------------------------------------------------------------
        # GET /status (no auth — public monitoring endpoint)
        # ----------------------------------------------------------------

        @app.route("/status")
        def status_view() -> Response:
            try:
                _cli = _db._client()

                # Total crops
                crops_resp = _cli.table("crops").select("status", count="exact").execute()
                total = crops_resp.count or 0
                rows = crops_resp.data or []

                confirmed = sum(1 for r in rows if r.get("status") == "confirmed")
                conflicts = sum(1 for r in rows if r.get("status") == "conflict")
                labeled = confirmed + conflicts
                remaining = total - labeled

                # my_labeled: requires auth — return 0 for unauthenticated callers
                my_labeled = 0
                access_token = session.get("access_token")
                if access_token:
                    try:
                        from src.modules.labeler.auth import decode_jwt
                        claims = decode_jwt(access_token)
                        user_id = claims.get("sub", "")
                        if user_id:
                            my_resp = (
                                _cli.table("labels")
                                .select("id", count="exact")
                                .eq("annotator_id", user_id)
                                .execute()
                            )
                            my_labeled = my_resp.count or 0
                    except Exception:
                        pass

                try:
                    global_touched = _cli.rpc("count_distinct_labeled_crops", {}).execute().data or 0
                    if not global_touched:
                        raise ValueError("rpc returned zero or null")
                except Exception:
                    try:
                        fb = _cli.table("crops").select("crop_id", count="exact").gt("annotation_count", 0).execute()
                        global_touched = fb.count or 0
                    except Exception:
                        global_touched = 0

                return jsonify({
                    "labeled": labeled,
                    "remaining": remaining,
                    "confirmed": confirmed,
                    "conflicts": conflicts,
                    "my_labeled": my_labeled,
                    "global_touched": global_touched,
                })
            except Exception as exc:
                return jsonify({"error": str(exc)}), 500

        # ----------------------------------------------------------------
        # GET /admin/conflicts (production — admin only)
        # ----------------------------------------------------------------

        @app.route("/admin/conflicts")
        @require_auth
        def admin_conflicts_view() -> Response:
            from flask import g
            if not _is_admin_user(g.user_id):
                return jsonify({"error": "Admin access required"}), 403
            conflicts = _db.get_conflict_crops()
            return jsonify({"conflicts": conflicts})

        # ----------------------------------------------------------------
        # GET /debug/sentry-test (admin only) — raises a controlled exception
        # to verify Sentry backend capture is working end-to-end.
        # ----------------------------------------------------------------

        @app.route("/debug/sentry-test")
        @require_auth
        def sentry_test_view() -> Response:
            from flask import g
            if not _is_admin_user(g.user_id):
                return jsonify({"error": "Admin access required"}), 403
            raise RuntimeError("Sentry backend test — intentional exception")

        # ----------------------------------------------------------------
        # GET /next — return next crop as JSON (SPA update, no page reload)
        # ----------------------------------------------------------------

        @app.route("/next")
        @require_auth
        def next_view() -> Response:
            from flask import g
            if not _launch_state()["is_open"]:
                return jsonify({"locked": True, "launch_iso": _launch_state()["launch_iso"]})
            _db.release_expired_assignments()
            crop_id = _db.assign_next_crop(g.user_id)

            # Real progress metrics
            try:
                progress = _db.get_real_progress()
                started = progress["started"]
                confirmed = progress["confirmed"]
                total = progress["total"]
            except Exception:
                started = 0
                confirmed = 0
                total = 0
            pct = round((started + confirmed) / (2 * total) * 100, 1) if total > 0 else 0

            if not crop_id:
                done_reason = "queue_exhausted" if confirmed < total else "all_done"
                return jsonify({"done": True, "done_reason": done_reason,
                                "started": started, "confirmed": confirmed,
                                "total": total, "pct": pct})

            crop = _db.get_crop_details(crop_id)
            pdf_path = crop.get("pdf_path", "")
            label_ocr = crop.get("label_ocr") or "?"
            if label_ocr.lower() in ("undefined", "null", "none"):
                label_ocr = "?"
            full_cell_crop_id = crop.get("full_cell_crop_id") or crop_id
            concordancias = _db.get_concordancias(pdf_path, label_ocr, crop_id)
            try:
                stats = _db.get_global_stats(g.user_id)
                global_labeled = stats["global_labeled"]
                my_labeled = stats["my_labeled"]
                session["global_labeled"] = global_labeled
                session["my_labeled"] = my_labeled
                session["total_count"] = total
            except Exception:
                global_labeled = session.get("global_labeled", 0)
                my_labeled = session.get("my_labeled", 0)
            return jsonify({
                "done": False,
                "crop_id": crop_id,
                "full_cell_crop_id": full_cell_crop_id,
                "label_ocr": label_ocr,
                "field_name": crop.get("field_name", ""),
                "digit_index": crop.get("digit_index", -1),
                "is_fallback": crop.get("digit_index", -1) == -1,
                "priority": crop.get("priority", 2),
                "pdf_filename": Path(pdf_path).name,
                "mesa": _mesa_info(pdf_path),
                "concordancias": concordancias,
                "acta_flags": _get_acta_flags(pdf_path),
                "recent": session.get("recent_labels", []),
                "labeled": global_labeled,
                "my_labeled": my_labeled,
                "total": total,
                "started": started,
                "confirmed": confirmed,
                "pct": pct,
            })

        # ----------------------------------------------------------------
        # POST /back (production) — undo the user's last label
        # ----------------------------------------------------------------

        @app.route("/back", methods=["POST"])
        @require_auth
        def back_view_prod() -> Response:
            from flask import g
            try:
                _cli = _db._client()
                # Find the user's most recent label
                last = (
                    _cli.table("labels")
                    .select("id, crop_id, label_human")
                    .eq("annotator_id", g.user_id)
                    .order("id", desc=True)
                    .limit(1)
                    .execute()
                )
                if not last.data:
                    return _error_response("No labels to undo", 400)
                row = last.data[0]
                last_crop = row["crop_id"]
                was_skip = row.get("label_human") == "_skip"
                # Delete the label
                _cli.table("labels").delete().eq("id", row["id"]).execute()
                # Decrement annotation_count on the crop (unless it was a skip)
                if not was_skip:
                    crop = _db.get_crop_details(last_crop)
                    new_count = max(0, (crop.get("annotation_count") or 1) - 1)
                    _cli.table("crops").update({
                        "annotation_count": new_count,
                        "status": "pending",
                        "confirmed_label": None,
                    }).eq("crop_id", last_crop).execute()
                    # Roll back session counters + recent log
                    session["labeled_count"] = max(0, session.get("labeled_count", 0) - 1)
                    recent = session.get("recent_labels", [])
                    session["recent_labels"] = recent[1:] if recent else []
                    session.modified = True
                # Clear any active assignment so the crop can be re-served
                _cli.table("assignments").delete().eq("annotator_id", g.user_id).execute()
            except Exception as exc:
                return _error_response(f"Back failed: {exc}", 500)
            return jsonify({"ok": True})

        # ----------------------------------------------------------------
        # GET /pdf?v=<crop_id> — serve PDF from local disk (production mode)
        # ----------------------------------------------------------------

        @app.route("/pdf")
        @require_auth
        def pdf_view_prod() -> Response:
            crop_id = request.args.get("v", "")
            if not crop_id:
                return Response("Missing crop_id", status=400)
            details = _db.get_crop_details(crop_id)
            if not details:
                return Response("Crop not found", status=404)
            # Proxy the PDF so the browser displays it inline (the Registraduria
            # serves PDFs as application/octet-stream, which forces a download).
            source_url = (details.get("source_url") or "").strip()
            if source_url:
                # Pilot strategy: redirect the user's browser directly to
                # Registraduria. Their browser can reach registraduria.gov.co
                # fine; Railway's egress cannot. PDFs come down as a download
                # because Registraduria sends Content-Type: octet-stream and
                # X-Content-Type-Options: nosniff (both anti-embed). Inline
                # rendering would require us to own the bytes (see backlog:
                # Cloudflare R2 / Google Drive Phase B).
                logger.info("pdf redirect crop=%s -> registraduria", crop_id)
                return redirect(source_url, code=302)
            # Fallback: serve from local disk (dev / not-yet-backfilled actas).
            pdf_path = Path(details.get("pdf_path", "")).resolve()
            if not pdf_path.exists():
                return Response(f"PDF not found: {pdf_path.name}", status=404)
            return send_file(str(pdf_path), mimetype="application/pdf")

        # ----------------------------------------------------------------
        # POST /mark-fraud — flag the whole acta as fraud with a reason
        # ----------------------------------------------------------------

        @app.route("/mark-fraud", methods=["POST"])
        @require_auth
        def mark_fraud_prod() -> Response:
            from flask import g
            body = request.get_json(force=True, silent=True) or {}
            crop_id = body.get("crop_id", "")
            reason = (body.get("reason", "") or "").strip()
            if not reason:
                return jsonify({"ok": False, "error": "Reason required"}), 400
            details = _db.get_crop_details(crop_id) if crop_id else None
            pdf_path = (details or {}).get("pdf_path", "")
            try:
                _record_fraud_mark(pdf_path, reason, g.user_id)
            except Exception as exc:
                return jsonify({"ok": False, "error": f"Could not record: {exc}"}), 500
            return jsonify({"ok": True, "pdf_path": pdf_path})

    else:
        # ----------------------------------------------------------------
        # LOCAL DEV MODE: preserve existing _STATE singleton + all routes
        # ----------------------------------------------------------------
        labels_dir  = labels_dir.resolve()
        index_path  = index_path.resolve()
        _load_divipole(Path.cwd())
        manifest_path = labels_dir / "manifest.jsonl"
        manifest = ManifestWriter(manifest_path, labels_dir)
        queue = ValidationQueue.load(index_path, known_crop_ids=manifest.known_crop_ids)

        _STATE = SessionState(queue=queue, manifest=manifest, labels_dir=labels_dir)

        # Use a default secret key in dev (no session security needed)
        app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key-not-for-production")

        @app.route("/")
        def index_view() -> str:
            state = _STATE

            # Auto-skip blank cells and border lines (up to 200 consecutive auto-skips)
            for _ in range(200):
                item = state.queue.current()
                if item is None:
                    break
                png_path = state.crops_dir / f"{item.crop_id}.png"
                reason = _should_auto_skip(png_path)
                if reason:
                    state.queue.advance(labeled=False)
                else:
                    break

            item = state.queue.current()

            if item is None:
                return render_template(
                    "label.html",
                    done=True,
                    labeled=state.queue.labeled,
                    total=state.queue.total,
                    user_email="dev@local.test",
                )

            return render_template(
                "label.html",
                done=False,
                crop_id=item.crop_id,
                full_cell_crop_id=item.full_cell_crop_id,
                label_ocr=item.label_ocr,
                field_name=item.field_name,
                digit_index=item.digit_index,
                is_fallback=(item.digit_index == -1),
                labeled=state.queue.labeled,
                remaining=state.queue.remaining(),
                total=state.queue.total,
                priority=item.priority,
                pdf_filename=Path(item.pdf_path).name,
                mesa=_mesa_info(item.pdf_path),
                recent=state.recent,
                concordancias=_get_concordancias(item.pdf_path, index_path, item.label_ocr),
                acta_flags=_get_acta_flags(item.pdf_path),
                user_email="dev@local.test",
            )

        @app.route("/image/<crop_id>")
        def image_view(crop_id: str) -> Response:
            # Sanitize: allow only hex characters (crop_id is sha1[:16])
            if not crop_id.replace("-", "").replace("_", "").isalnum():
                return Response("Invalid crop_id", status=400)

            png_path = _STATE.crops_dir / f"{crop_id}.png"
            if not png_path.exists():
                return Response("Not found", status=404)

            return send_file(str(png_path), mimetype="image/png")

        @app.route("/label", methods=["POST"])
        def label_view() -> Response:
            state = _STATE
            body = request.get_json(force=True, silent=True) or {}

            crop_id = body.get("crop_id", "")
            raw_value = body.get("value", "")

            current = state.queue.current()
            if current is None:
                return jsonify({"ok": False, "error": "Queue exhausted"}), 400

            if current.crop_id != crop_id:
                return jsonify({"ok": False, "error": "crop_id mismatch — reload the page"}), 400

            # Parse input
            try:
                decision = ManifestWriter.parse_input(raw_value, current.label_ocr)
            except ValueError as exc:
                return jsonify({"ok": False, "error": str(exc)}), 400

            # Build manifest row
            now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            row = {
                "crop_id": current.crop_id,
                "img_path": "",  # filled by ManifestWriter.append
                "label_human": decision.label_human,
                "label_ocr": current.label_ocr,
                "confirmed": decision.label_human == current.label_ocr,
                "amended": decision.amended,
                "pdf_path": current.pdf_path,
                "field_name": current.field_name,
                "digit_index": current.digit_index,
                "confidence": current.confidence,
                "labeled_at": now_utc,
            }

            crop_src = state.crops_dir / f"{current.crop_id}.png"
            try:
                state.manifest.append(row, crop_src)
            except Exception as exc:
                return jsonify({"ok": False, "error": f"Write error: {exc}"}), 500

            state.push_recent(current.crop_id, decision.label_human, current.field_name, current.label_ocr, current.confidence)
            state.queue.advance(labeled=True)

            next_item = state.queue.current()
            return jsonify({
                "ok": True,
                "next_crop_id": next_item.crop_id if next_item else None,
                "remaining": state.queue.remaining(),
            })

        @app.route("/pdf")
        def pdf_view() -> Response:
            crop_id = request.args.get("v", "")
            # Look up pdf_path from queue by crop_id; fall back to current item
            item = next((i for i in _STATE.queue._items if i.crop_id == crop_id), None)
            if item is None:
                item = _STATE.queue.current()
            if item is None:
                return Response("No current item", status=404)
            pdf_path = Path(item.pdf_path).resolve()
            if not pdf_path.exists():
                return Response(f"PDF not found: {pdf_path.name}", status=404)
            return send_file(str(pdf_path), mimetype="application/pdf")

        @app.route("/back", methods=["POST"])
        def back_view() -> Response:
            moved = _STATE.queue.back()
            return jsonify({"ok": moved, "cursor": _STATE.queue.cursor})

        @app.route("/skip", methods=["POST"])
        def skip_view() -> Response:
            state = _STATE
            current = state.queue.current()
            if current is None:
                return jsonify({"ok": False, "error": "Queue exhausted"}), 400
            state.queue.advance(labeled=False)
            next_item = state.queue.current()
            return jsonify({
                "ok": True,
                "next_crop_id": next_item.crop_id if next_item else None,
                "remaining": state.queue.remaining(),
            })

        @app.route("/mark-fraud", methods=["POST"])
        def mark_fraud_local() -> Response:
            body = request.get_json(force=True, silent=True) or {}
            reason = (body.get("reason", "") or "").strip()
            if not reason:
                return jsonify({"ok": False, "error": "Reason required"}), 400
            item = _STATE.queue.current()
            pdf_path = item.pdf_path if item else ""
            try:
                _record_fraud_mark(pdf_path, reason, "local")
            except Exception as exc:
                return jsonify({"ok": False, "error": f"Could not record: {exc}"}), 500
            return jsonify({"ok": True, "pdf_path": pdf_path})

        @app.route("/next")
        def next_view_local() -> Response:
            """Return the next crop as JSON for SPA updates (no page reload)."""
            state = _STATE
            item = state.queue.current()
            labeled = state.queue.labeled
            total = state.queue.total
            started = labeled  # local mode: no Supabase, simplified
            confirmed = labeled
            pct = round((started + confirmed) / (2 * total) * 100, 1) if total > 0 else 0
            if item is None:
                done_reason = "queue_exhausted" if confirmed < total else "all_done"
                return jsonify({
                    "done": True,
                    "done_reason": done_reason,
                    "started": started,
                    "confirmed": confirmed,
                    "total": total,
                    "pct": pct,
                    "labeled": labeled,
                })
            return jsonify({
                "done": False,
                "crop_id": item.crop_id,
                "full_cell_crop_id": item.full_cell_crop_id,
                "label_ocr": item.label_ocr,
                "field_name": item.field_name,
                "digit_index": item.digit_index,
                "is_fallback": item.digit_index == -1,
                "priority": item.priority,
                "pdf_filename": Path(item.pdf_path).name,
                "mesa": _mesa_info(item.pdf_path),
                "concordancias": _get_concordancias(item.pdf_path, index_path, item.label_ocr),
                "acta_flags": _get_acta_flags(item.pdf_path),
                "recent": state.recent,
                "labeled": labeled,
                "total": total,
                "started": started,
                "confirmed": confirmed,
                "pct": pct,
            })

        @app.route("/status")
        def status_view() -> Response:
            state = _STATE
            distribution: dict[str, int] = {str(i): 0 for i in range(10)}
            manifest_path = state.labels_dir / "manifest.jsonl"
            if manifest_path.exists():
                for line in manifest_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                        lbl = r.get("label_human", "")
                        if lbl in distribution:
                            distribution[lbl] += 1
                    except json.JSONDecodeError:
                        pass
            return jsonify({
                "labeled": state.queue.labeled,
                "pending": state.queue.remaining(),
                "total": state.queue.total,
                "distribution": distribution,
            })

    return app


# ---------------------------------------------------------------------------
# Admin role check helper
# ---------------------------------------------------------------------------

def _is_admin_user(user_id: str) -> bool:
    """
    Check if the authenticated user has the admin role.

    Reads the user's app_metadata from Supabase Auth to check for role='admin'.
    Returns False on any error (fail-closed).
    """
    try:
        from src.modules.labeler import db as _db
        client = _db._client()  # service_role — required for the admin API
        # Use the admin API to get user metadata
        user_resp = client.auth.admin.get_user_by_id(user_id)
        if user_resp and user_resp.user:
            app_meta = user_resp.user.app_metadata or {}
            return app_meta.get("role") == "admin"
    except Exception:
        pass
    return False
