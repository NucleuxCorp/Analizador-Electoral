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


def _strip_crlf(value: str) -> str:
    """Strip CR/LF from a value before it is written to logs (log-injection guard)."""
    return (value or "").replace("\r", "").replace("\n", "")


def _fmt_admin_timestamp(value) -> str:
    """
    Format a Supabase Auth timestamp field for display.

    The real gotrue SDK parses these fields into `datetime.datetime` objects,
    but tests (and possibly other callers) may pass plain ISO strings — accept
    both so template rendering never breaks on a live user list.
    """
    if not value:
        return ""
    if hasattr(value, "isoformat"):
        value = value.isoformat()
    return str(value)[:16].replace("T", " ")


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


def _public_base_url() -> str:
    """Return the public URL used for auth email links and external redirects.

    Precedence:
      1. APP_URL environment variable (explicit operator override)
      2. RAILWAY_STATIC_URL (Railway-provided public URL)
      3. Production default domain
    """
    app_url = os.environ.get("APP_URL", "").strip()
    if app_url:
        return app_url.rstrip("/")
    railway_url = os.environ.get("RAILWAY_STATIC_URL", "").strip()
    if railway_url:
        return railway_url.rstrip("/")
    return "https://analizadore14.porciudad.com"


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
# Repo root (…/src/modules/labeler/server.py → parents[3]) — more reliable than cwd on Railway.
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_divipole(root: Path | None = None) -> None:
    """Load DIVIPOLE hierarchy for readable municipio/puesto names on /mesas.

    Source of truth: ``data/divipole.json`` (Registraduría-derived codes + school names).
    Shape: departamentos → municipios → zonas → puestos, each with ``nombre``.
    """
    global _DIVIPOLE
    candidates = []
    if root is not None:
        candidates.append(Path(root) / "data" / "divipole.json")
    candidates.append(_REPO_ROOT / "data" / "divipole.json")
    candidates.append(Path.cwd() / "data" / "divipole.json")
    for path in candidates:
        if not path.is_file():
            continue
        try:
            _DIVIPOLE = json.loads(path.read_text(encoding="utf-8")).get("departamentos", {})
            logger.info("DIVIPOLE loaded from %s (%s depts)", path, len(_DIVIPOLE))
            return
        except Exception as exc:
            logger.warning("DIVIPOLE load failed %s: %s", path, exc)
    _DIVIPOLE = {}
    logger.warning("DIVIPOLE not found — municipio/puesto names will fall back to codes")


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


_FRAUD_MARKS_PATH    = Path("data/fraud_marks.jsonl")
_FEEDBACK_MARKS_PATH = Path("data/feedback_marks.jsonl")


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


def _record_feedback(crop_id: str, pdf_path: str, message: str, annotator: str) -> None:
    """Append a user feedback report to data/feedback_marks.jsonl."""
    _FEEDBACK_MARKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "crop_id": crop_id,
        "pdf_path": pdf_path,
        "message": message,
        "annotator": annotator,
        "reported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with open(_FEEDBACK_MARKS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


_BASE_E14C_SV = "https://escrutinios2vueltapresidente2026.registraduria.gov.co"


def _reconstruct_source_url(pdf_path: str) -> str:
    """
    Reconstruct the Registraduría public URL from a local pdf_path when
    source_url was not stored in the database.

    Local files may be named {prefix}_E14_PRE_{dept}_{mpio}_{zona_3digit}_{??}_{puesto}_...
    or just E14_PRE_{dept}_{mpio}_{zona_3digit}_{??}_{puesto}_...
    URL format: /docs/E14/{dept}/{mpio}/{zona_2digit}/{puesto}/{E14_PRE_...}.pdf
    """
    full_stem = Path(pdf_path).stem
    marker = "E14_PRE_"
    idx = full_stem.find(marker)
    if idx == -1:
        return ""
    stem = full_stem[idx:]  # e.g. E14_PRE_05_001_004_01_03_011_5403
    tail = stem[len(marker):]  # "05_001_004_01_03_011_5403"
    parts = tail.split("_")
    if len(parts) < 5:
        return ""
    dept   = parts[0]                     # "05"
    mpio   = parts[1]                     # "001"
    zona   = str(int(parts[2])).zfill(2)  # "004" → "04"
    puesto = parts[4]                     # "03"
    nombre_archivo = f"/docs/E14/{dept}/{mpio}/{zona}/{puesto}/{stem}.pdf"
    return _BASE_E14C_SV + nombre_archivo


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
# Semaphore data builder — algorithm alert state per department
# ---------------------------------------------------------------------------

# Loaded once at module level (populated in _load_departamentos).
_DEPT_NAMES: dict[str, str] = {}  # dept_code → dept_name


def _load_departamentos(root: Path) -> None:
    """Load dept_code → dept_name mapping from data/departamentos.json."""
    global _DEPT_NAMES
    path = root / "data" / "departamentos.json"
    if path.exists():
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
            _DEPT_NAMES = {e["id"]: e["nombre"] for e in entries if "id" in e and "nombre" in e}
        except Exception:
            _DEPT_NAMES = {}


def _build_semaphore_data() -> dict | None:
    """
    Build per-department semaphore data combining algorithm alerts (⚪/🔴) and
    human-review state (🟡/🟢) from their respective db functions.

    Mapping (algorithm):
        overall_status == 'clean'  → sin_alerta (⚪)
        any other status           → alerta (🔴)

    Mapping (human review — Slice 2):
        🟡 EN REVISIÓN: mesa has annotation_count >= 1 and not all priority crops confirmed
        🟢 REVISADA:    all priority <= 1 crops have status = 'confirmed'

    Returns:
        {
            "by_dept": [
                {"dept_code": "01", "dept_name": "ANTIOQUIA",
                 "sin_alerta": N, "alerta": N, "total": N,
                 "en_revision": N, "revisada": N},
                ...  (sorted by dept_name)
            ],
            "global": {"sin_alerta": N, "alerta": N, "total": N,
                       "en_revision": N, "revisada": N},
        }
        Returns None if get_mesa_stats() returns empty (Supabase unreachable).
    """
    try:
        import src.modules.labeler.db as _db
        stats = _db.get_mesa_stats()
    except Exception:
        return None

    if not stats:
        return None

    # Fetch 🟡/🟢 counts — fail gracefully (Slice 2 may not be deployed yet).
    review_stats: dict = {}
    try:
        import src.modules.labeler.db as _db
        review_stats = _db.get_mesa_semaphore_stats() or {}
    except Exception:
        review_stats = {}

    by_dept = []
    for dept_code, counts in stats.items():
        if dept_code == "_global":
            continue
        sin_alerta = counts.get("clean", 0)
        total = counts.get("total", 0)
        alerta = total - sin_alerta
        review = review_stats.get(dept_code, {})
        by_dept.append({
            "dept_code": dept_code,
            "dept_name": _DEPT_NAMES.get(dept_code, dept_code),
            "sin_alerta": sin_alerta,
            "alerta": max(0, alerta),
            "total": total,
            "en_revision": review.get("en_revision", 0),
            "revisada": review.get("revisada", 0),
            "limpia_count": review.get("limpia_count", 0),
            "fraude_count": review.get("fraude_count", 0),
        })

    by_dept.sort(key=lambda r: r["dept_name"])

    global_counts = stats.get("_global", {})
    global_sin_alerta = global_counts.get("clean", 0)
    global_total = global_counts.get("total", 0)
    global_alerta = max(0, global_total - global_sin_alerta)
    global_review = review_stats.get("_global", {})

    return {
        "by_dept": by_dept,
        "global": {
            "sin_alerta": global_sin_alerta,
            "alerta": global_alerta,
            "total": global_total,
            "en_revision": global_review.get("en_revision", 0),
            "revisada": global_review.get("revisada", 0),
        },
    }


# ---------------------------------------------------------------------------
# Mesas hierarchical drill-down — Work Unit 2 (Routes & Builders)
#
# Municipio -> Puesto -> Mesa. Replaces the old flat dept-only /mesas table.
# Level 1/2 read from the single cached get_hierarchical_mesa_stats() blob;
# Level 3 reads directly from get_mesa_results()/count_mesa_results() (design
# decision: avoid loading the full national scan just to paginate one puesto).
# ---------------------------------------------------------------------------

# Public /mesas table page sizes (Level 3 already uses 10 via get_mesa_results).
MESAS_L1_PAGE_SIZE = 50
MESAS_L2_PAGE_SIZE = 50
MESAS_L3_PAGE_SIZE = 10


def _pad_divipole_code(code: str, width: int) -> str:
    """Normalize numeric DIVIPOLE codes (e.g. '1' → '01') for lookup keys."""
    c = str(code or "").strip()
    if c.isdigit():
        return c.zfill(width)
    return c


def _divipole_key_candidates(code: str, width: int) -> list[str]:
    """Generate lookup keys for mesa_results codes that may differ in zero-padding.

    Registraduría / mesa_results often use ``001`` for a zone stored as ``01`` in
    divipole.json. Plain zfill(width) does not shrink longer strings, so we also
    try int-normalized forms (``001`` → ``1`` → ``01``).
    """
    raw = str(code or "").strip()
    out: list[str] = []

    def add(key: str) -> None:
        if key and key not in out:
            out.append(key)

    add(raw)
    if raw.isdigit():
        add(raw.zfill(width))
        # Common DIVIPOLE widths in this project
        for w in (2, 3):
            add(raw.zfill(w))
        bare = str(int(raw))  # strip leading zeros
        add(bare)
        add(bare.zfill(width))
        for w in (2, 3):
            add(bare.zfill(w))
    return out


def _mpio_name(dept: str, mpio: str) -> str:
    """Look up the municipio name from _DIVIPOLE, falling back to the code."""
    try:
        for d in _divipole_key_candidates(dept, 2):
            dept_node = _DIVIPOLE.get(d) or {}
            mpios = dept_node.get("municipios") or {}
            for m in _divipole_key_candidates(mpio, 3):
                name = (mpios.get(m) or {}).get("nombre")
                if name:
                    return name
        return str(mpio or "")
    except Exception:
        return str(mpio or "")


def _puesto_name(dept: str, mpio: str, zona: str, puesto: str) -> str:
    """Look up the puesto de votación name from _DIVIPOLE, falling back to the code."""
    try:
        for d in _divipole_key_candidates(dept, 2):
            dept_node = _DIVIPOLE.get(d) or {}
            mpios = dept_node.get("municipios") or {}
            for m in _divipole_key_candidates(mpio, 3):
                zonas = (mpios.get(m) or {}).get("zonas") or {}
                for z in _divipole_key_candidates(zona, 2):
                    puestos = (zonas.get(z) or {}).get("puestos") or {}
                    for p in _divipole_key_candidates(puesto, 2):
                        name = (puestos.get(p) or {}).get("nombre")
                        if name:
                            return name
        return str(puesto or "")
    except Exception:
        return str(puesto or "")


def _parse_mesas_page(raw) -> int:
    try:
        page = int(raw or 1)
    except (TypeError, ValueError):
        page = 1
    return max(1, page)


def _paginate_rows(rows: list[dict], page: int, page_size: int) -> tuple[list[dict], int, int]:
    """Return (page_slice, clamped_page, total_pages)."""
    total = len(rows)
    total_pages = max(1, (total + page_size - 1) // page_size) if total else 1
    page = min(max(1, page), total_pages)
    start = (page - 1) * page_size
    return rows[start:start + page_size], page, total_pages


def _dept_filter_options(stats: dict) -> list[dict]:
    """Departments present in hierarchical stats, for the L1 filter dropdown."""
    codes = {
        str(bucket.get("dept") or "").strip()
        for bucket in (stats.get("by_mpio") or {}).values()
        if bucket.get("dept")
    }
    options = [
        {"code": code, "name": _DEPT_NAMES.get(code, code)}
        for code in codes
    ]
    options.sort(key=lambda o: o["name"])
    return options


def _semaphore_from_level_rows(rows: list[dict]) -> dict:
    """Aggregate ⚪/🔴 summary from already-built L1/L2 row dicts."""
    sin_alerta = sum(int(r.get("sin_alerta") or 0) for r in rows)
    alerta = sum(int(r.get("alerta") or 0) for r in rows)
    en_revision = sum(int(r.get("en_revision") or 0) for r in rows)
    revisada = sum(int(r.get("revisada") or 0) for r in rows)
    return {
        "sin_alerta": sin_alerta,
        "alerta": alerta,
        "en_revision": en_revision,
        "revisada": revisada,
        "total": sin_alerta + alerta,
    }


def _hierarchical_global_semaphore(stats: dict) -> dict:
    """Build the {sin_alerta, alerta, en_revision, revisada, total} national summary block."""
    g = stats.get("_global") or {}
    total = g.get("total", 0)
    sin_alerta = g.get("clean", 0)
    return {
        "sin_alerta": sin_alerta,
        "alerta": max(0, total - sin_alerta),
        "en_revision": g.get("en_revision", 0),
        "revisada": g.get("revisada", 0),
        "total": total,
    }


def _build_mesas_level1_rows(stats: dict, dept_filter: str | None = None) -> list[dict]:
    """Build Level-1 (Municipio) rows from the by_mpio buckets of get_hierarchical_mesa_stats()."""
    rows = []
    want = (dept_filter or "").strip()
    want_padded = _pad_divipole_code(want, 2) if want else ""
    for bucket in (stats.get("by_mpio") or {}).values():
        dept = bucket.get("dept", "")
        if want and str(dept) != want and _pad_divipole_code(str(dept), 2) != want_padded:
            continue
        mpio = bucket.get("mpio", "")
        total = bucket.get("total", 0)
        sin_alerta = bucket.get("clean", 0)
        rows.append({
            "dept": dept,
            "dept_name": _DEPT_NAMES.get(dept, dept) or _DEPT_NAMES.get(_pad_divipole_code(str(dept), 2), dept),
            "mpio": mpio,
            "mpio_name": _mpio_name(dept, mpio),
            "sin_alerta": sin_alerta,
            "alerta": max(0, total - sin_alerta),
            "en_revision": bucket.get("en_revision", 0),
            "revisada": bucket.get("revisada", 0),
            "total": total,
        })
    rows.sort(key=lambda r: (r["mpio_name"], r["dept_name"]))
    return rows


def _build_mesas_level2_rows(stats: dict, dept: str, mpio: str) -> list[dict]:
    """Build Level-2 (Puesto) rows for one municipality from the by_puesto buckets."""
    # Try both raw and zero-padded keys — mesa_results keys may not match DIVIPOLE padding.
    candidates = [
        f"{dept}_{mpio}",
        f"{_pad_divipole_code(dept, 2)}_{_pad_divipole_code(mpio, 3)}",
    ]
    puestos: dict = {}
    for key in candidates:
        puestos = (stats.get("by_puesto") or {}).get(key) or {}
        if puestos:
            break
    rows = []
    for bucket in puestos.values():
        zona = bucket.get("zona", "")
        puesto = bucket.get("puesto", "")
        total = bucket.get("total", 0)
        sin_alerta = bucket.get("clean", 0)
        rows.append({
            "dept": dept,
            "mpio": mpio,
            "zona": zona,
            "puesto": puesto,
            "puesto_name": _puesto_name(dept, mpio, zona, puesto),
            "sin_alerta": sin_alerta,
            "alerta": max(0, total - sin_alerta),
            "en_revision": bucket.get("en_revision", 0),
            "revisada": bucket.get("revisada", 0),
            "total": total,
        })
    rows.sort(key=lambda r: (r["zona"], r["puesto"], r["puesto_name"]))
    return rows


def _build_mesas_level3_rows(
    dept: str, mpio: str, zona: str, puesto: str, page: int,
) -> tuple[list[dict], int]:
    """
    Build Level-3 (Mesa) rows for one puesto, 10 per page.

    Reads directly from get_mesa_results()/count_mesa_results() (not the cached
    hierarchical blob), per design. Both db-layer functions are fail-closed
    (return [] / 0 on any exception), so this never raises.
    """
    import src.modules.labeler.db as _db

    results = _db.get_mesa_results(
        dept=dept, mpio=mpio, zona=zona, puesto=puesto, page=page, per_page=10,
    )
    total = _db.count_mesa_results(dept=dept, mpio=mpio, zona=zona, puesto=puesto)

    try:
        review_by_mesa = _db.get_hierarchical_mesa_stats().get("review_by_mesa", {}) or {}
    except Exception:
        review_by_mesa = {}

    rows = []
    for r in results:
        mesa_key = r.get("mesa_key", "")
        status = r.get("overall_status", "")
        review = review_by_mesa.get(mesa_key, {})
        rows.append({
            "mesa": r.get("mesa", ""),
            "mesa_key": mesa_key,
            "sin_alerta": status == "clean",
            "alerta": status != "clean",
            "en_revision": bool(review.get("en_revision")),
            "revisada": bool(review.get("revisada")),
            "revisada_result": review.get("revisada_result"),
        })
    return rows, total


def _mesas_l1_breadcrumbs() -> list[dict]:
    return [{"label": "Municipios", "url": None}]


def _mesas_l2_breadcrumbs(dept: str, mpio: str) -> list[dict]:
    return [
        {"label": "Municipios", "url": "/mesas"},
        {"label": _mpio_name(dept, mpio), "url": None},
    ]


def _mesas_l3_breadcrumbs(dept: str, mpio: str, zona: str, puesto: str) -> list[dict]:
    return [
        {"label": "Municipios", "url": "/mesas"},
        {"label": _mpio_name(dept, mpio), "url": f"/mesas/{dept}/{mpio}"},
        {"label": _puesto_name(dept, mpio, zona, puesto), "url": None},
    ]


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
_VALUE_TOKEN_PATTERN = _re.compile(r'^(|[0-9]{1,5}|E[0-9]{1,5}|\*|-|\.|\+|/{1,3})$')

# ---------------------------------------------------------------------------
# Report glyph validation — single canonical source shared with label.html JS
# ---------------------------------------------------------------------------

# Canonical glyph pattern: digits 0-9 and zero-variant glyphs (*, ., -, +, o, O, /, //, ///).
# The + quantifier allows //, /// because / is in the character class.
# Length cap for slash-only sequences is enforced by _validate_report_payload, not the regex.
VALID_REPORT_GLYPH_RE = _re.compile(r'^[0-9*.+oO/-]+$')

# JS-safe regex string injected into label.html templates via |tojson filter.
VALID_REPORT_GLYPH_JS = r'^[0-9*.+oO/-]+$'

# Maximum length for a slash-only glyph sequence (/, //, ///)
_MAX_SLASH_LEN = 3


def _validate_glyph(value: str) -> bool:
    """Return True if value is a non-empty, valid glyph of acceptable length."""
    if not value:
        return False
    if not VALID_REPORT_GLYPH_RE.match(value):
        return False
    # Slash-only sequences longer than 3 chars are not valid glyphs
    if set(value) == {"/"}:
        return len(value) <= _MAX_SLASH_LEN
    return True


_TRANSVERSAL_REPORT_SOURCES = frozenset({"e14c", "e14d", "e14t"})
_TRANSVERSAL_REPORT_TYPES = frozenset({"campos_vacios", "enmienda", "otro"})
_TRANSVERSAL_BLANK_FIELDS = frozenset({"VOTANTES", "URNA", "SUMA_TOTAL"})


def _validate_transversal_reports_payload(body: dict) -> tuple[dict, str | None]:
    """Validate POST /api/transversal/reports body."""
    mesa_key = (body.get("mesa_key") or "").strip()
    entries = body.get("entries")
    if not mesa_key or not isinstance(entries, list) or not entries:
        return {}, "missing_fields"
    cleaned_entries: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            return {}, "invalid_entry"
        source = (entry.get("source") or "").strip()
        report_type = (entry.get("report_type") or "").strip()
        notes = (entry.get("notes") or "").strip()
        if source not in _TRANSVERSAL_REPORT_SOURCES:
            return {}, "invalid_source"
        if report_type not in _TRANSVERSAL_REPORT_TYPES:
            return {}, "invalid_report_type"
        if not notes:
            return {}, "notes_required"
        cleaned: dict = {
            "source": source,
            "report_type": report_type,
            "notes": notes,
        }
        if report_type == "campos_vacios" and isinstance(entry.get("fields"), dict):
            cleaned["fields"] = {
                field: bool(entry["fields"].get(field))
                for field in _TRANSVERSAL_BLANK_FIELDS
                if field in entry["fields"]
            }
        cleaned_entries.append(cleaned)
    return {"mesa_key": mesa_key, "entries": cleaned_entries}, None


def _validate_report_payload(body: dict) -> tuple[dict, str | None]:
    """Validate a POST /report JSON body.

    Returns:
        (cleaned_payload, error_message)  — error_message is None on success.

    Validation rules:
        - crop_id: required
        - report_type: must be 'enmienda' or 'otro'
        - enmienda: digit_original and digit_corrected required; notes optional;
                    digit_original and digit_corrected must be valid glyphs; digit forced None
        - otro:     notes required non-empty; digit optional but must be valid glyph if present;
                    digit_original and digit_corrected forced None
    """
    crop_id = (body.get("crop_id") or "").strip()
    if not crop_id:
        return {}, "crop_id is required"

    report_type = (body.get("report_type") or "").strip()
    if report_type not in ("enmienda", "otro", "mesa"):
        return {}, f"report_type must be 'enmienda', 'otro', or 'mesa', got: {report_type!r}"

    payload: dict = {
        "crop_id": crop_id,
        "report_type": report_type,
        "pdf_path": body.get("pdf_path") or None,
        "digit_original": None,
        "digit_corrected": None,
        "digit": None,
        "notes": (body.get("notes") or "").strip() or None,
    }

    if report_type == "enmienda":
        digit_original = (body.get("digit_original") or "").strip()
        digit_corrected = (body.get("digit_corrected") or "").strip()
        if not digit_original:
            return {}, "digit_original is required for enmienda reports"
        if not digit_corrected:
            return {}, "digit_corrected is required for enmienda reports"
        if not _validate_glyph(digit_original):
            return {}, f"digit_original contains invalid glyph: {digit_original!r}"
        if not _validate_glyph(digit_corrected):
            return {}, f"digit_corrected contains invalid glyph: {digit_corrected!r}"
        payload["digit_original"] = digit_original
        payload["digit_corrected"] = digit_corrected
        payload["notes"] = (body.get("notes") or "").strip() or None
        # digit is irrelevant for enmienda — force None
        payload["digit"] = None

    elif report_type == "otro":
        notes = (body.get("notes") or "").strip()
        digit = (body.get("digit") or "").strip()
        if not digit:
            return {}, "digit is required for otro reports"
        if not _validate_glyph(digit):
            return {}, f"digit contains invalid glyph: {digit!r}"
        if not notes:
            return {}, "notes is required for otro reports"
        payload["digit"] = digit
        payload["notes"] = notes
        # digit_original and digit_corrected are irrelevant for otro — force None
        payload["digit_original"] = None
        payload["digit_corrected"] = None

    elif report_type == "mesa":
        notes = (body.get("notes") or "").strip()
        if not notes:
            return {}, "notes is required for mesa reports"
        # digit fields must be absent for mesa — reject any non-empty value
        for field in ("digit_original", "digit_corrected", "digit"):
            if (body.get(field) or "").strip():
                return {}, f"{field} must be absent for mesa reports"
        payload["notes"] = notes
        # Force all digit fields to None — mesa is text-only
        payload["digit"] = None
        payload["digit_original"] = None
        payload["digit_corrected"] = None

    return payload, None


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
    static_dir = Path(__file__).parent / "static"
    app = Flask(
        __name__,
        template_folder=str(templates_dir),
        static_folder=str(static_dir),
    )

    # ----------------------------------------------------------------
    # Custom Jinja filters
    # ----------------------------------------------------------------
    app.jinja_env.filters["format_number"] = (
        lambda n: f"{int(n):,}".replace(",", ".") if isinstance(n, int) else "—"
    )

    # ----------------------------------------------------------------
    # Public URL scheme / domain for external links (verification emails)
    # ----------------------------------------------------------------
    app.config["PREFERRED_URL_SCHEME"] = "https"
    _server_name = os.environ.get("SERVER_NAME", "").strip()
    if not _server_name:
        from urllib.parse import urlparse

        _server_name = urlparse(_public_base_url()).netloc
    if _server_name:
        app.config["SERVER_NAME"] = _server_name

    # ----------------------------------------------------------------
    # Voting-round module switch (PR-B): primera | segunda
    # ----------------------------------------------------------------
    _ALLOWED_MODULES = frozenset({"primera", "segunda"})
    _module_env = os.environ.get("MODULE", "primera").strip().lower()
    if _module_env not in _ALLOWED_MODULES:
        raise RuntimeError(
            f"Invalid MODULE env value: {_module_env!r}. "
            f"Must be one of {sorted(_ALLOWED_MODULES)}."
        )
    app.config["MODULE"] = _module_env

    # ----------------------------------------------------------------
    # LABELS_DIR resolution (PR-B W7)
    #
    # Precedence (highest to lowest):
    #   1. LABELS_DIR environment variable (explicit operator override)
    #   2. MODULE=segunda → default data/labels_segunda
    #   3. MODULE=primera (or unset) → use the labels_dir passed to create_app()
    # ----------------------------------------------------------------
    _labels_dir_env = os.environ.get("LABELS_DIR", "").strip()
    if _labels_dir_env:
        resolved_labels_dir = Path(_labels_dir_env).resolve()
    elif app.config["MODULE"] == "segunda":
        resolved_labels_dir = Path("data/labels_segunda").resolve()
    else:
        resolved_labels_dir = labels_dir.resolve()
    app.config["LABELS_DIR"] = str(resolved_labels_dir)
    resolved_labels_dir.mkdir(parents=True, exist_ok=True)

    # ----------------------------------------------------------------
    # Request correlation + global exception handler
    # ----------------------------------------------------------------
    @app.before_request
    def _assign_request_id() -> None:
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        g.request_id = rid

    # Routes allowed while the portal is in maintenance mode.
    # Static assets are required by the landing page; /status is the healthcheck;
    # /auth/register remains open so new volunteers can sign up during cutover.
    _MAINTENANCE_WHITELIST = frozenset({
        "static",
        "status_view",
        "auth_register_get",
        "auth_register_post",
    })

    @app.before_request
    def _check_maintenance_mode() -> tuple[Response, int] | None:
        """Gate non-whitelisted traffic when MAINTENANCE_MODE=true."""
        if os.environ.get("MAINTENANCE_MODE", "").lower() != "true":
            return None
        if request.endpoint in _MAINTENANCE_WHITELIST:
            return None
        if (
            request.is_json
            or request.path.startswith("/api/")
            or request.accept_mimetypes.best == "application/json"
        ):
            return jsonify({"ok": False, "error": "maintenance_mode"}), 503
        return render_template("maintenance.html"), 503

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
            "user_role": getattr(g, "user_role", ""),
        }

    # Load lookup tables for both modes (mesa info + fraud flags + dept names)
    _load_divipole(Path.cwd())
    _load_acta_flags(Path.cwd())
    _load_departamentos(Path.cwd())

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
        from src.modules.labeler.auth import require_auth, resolve_user_role, require_role
        from src.modules.labeler.auth import ROLE_ADMIN, ROLE_MODERATOR, ROLE_VALIDATOR, ROLE_REVIEWER, ROLE_READER
        from src.modules.labeler.auth import _role_cache, _ROLE_CACHE_TTL
        from src.modules.labeler.auth import NON_ADMIN_ROLES, evict_role_cache
        import src.modules.labeler.db as _db

        # Register role resolver — runs after _check_maintenance_mode, sets g.user_role
        app.before_request(resolve_user_role)

        _use_storage = os.environ.get("USE_SUPABASE_STORAGE", "false").lower() == "true"
        _index_path = resolved_labels_dir / "crops" / "index.jsonl"

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
                    valid_report_glyph_js=VALID_REPORT_GLYPH_JS,
                    supabase_mode=True,
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

        @app.route("/privacy", methods=["GET"])
        def privacy_get() -> str:
            return render_template("privacy.html")

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
                base_url = _public_base_url()
                client.auth.sign_up({
                    "email": email,
                    "password": password,
                    "options": {
                        "data": {
                            "first_name": first_name,
                            "last_name": last_name,
                            "phone": phone,
                            "full_name": (first_name + " " + last_name).strip(),
                        },
                        "email_redirect_to": f"{base_url}/auth/confirm",
                    },
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
                from src.modules.labeler.auth import init_supabase_client
                base_url = _public_base_url()
                # SDK method sends token_hash as query param (?token_hash=xxx&type=recovery)
                # so the GET /auth/recovery handler can call verify_otp() correctly.
                # The old REST /auth/v1/recover sent the token in the URL fragment (#access_token=...)
                # which never reaches the server.
                _recovery_client = init_supabase_client()
                _recovery_client.auth.reset_password_for_email(
                    email,
                    options={"redirect_to": f"{base_url}/auth/recovery"},
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
            # Supabase implicit flow: tokens arrive in the URL fragment (#access_token=...),
            # which browsers never send to the server. The JS in reset_password.html reads
            # the fragment and handles both success and error cases client-side.
            # token_hash flow (PKCE/OTP) is also supported as a secondary path via the same JS.
            return render_template("reset_password.html")

        @app.route("/auth/recovery", methods=["POST"])
        def auth_recovery_post() -> Response:
            body = request.get_json(force=True, silent=True) or {}
            new_password = body.get("password", "")
            access_token = body.get("access_token", "").strip()
            if len(new_password) < 8:
                return jsonify({"error": "La contraseña debe tener al menos 8 caracteres."}), 400
            if not access_token:
                return jsonify({"error": "Sesión de recuperación inválida. Solicitá un nuevo link."}), 403
            from src.modules.labeler.auth import init_supabase_client
            import src.modules.labeler.db as _db
            try:
                # Validate the access_token and get the user_id.
                client = init_supabase_client()
                user_resp = client.auth.get_user(access_token)
                if not user_resp or not user_resp.user:
                    return jsonify({"error": "Link expirado o inválido. Solicitá uno nuevo."}), 403
                # Update password via admin API (no user session needed).
                admin_client = _db._client()
                admin_client.auth.admin.update_user_by_id(
                    user_resp.user.id, {"password": new_password}
                )
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
            import time as _time
            client = init_supabase_client()
            try:
                response = client.auth.sign_in_with_password({"email": email, "password": password})
                sess = response.session
                if sess is None:
                    return jsonify({"error": "Login failed — no session returned"}), 401
                session["access_token"] = sess.access_token
                session["refresh_token"] = sess.refresh_token
                session["user_email"] = response.user.email if response.user else email

                # Determine role — assign ROLE_VALIDATOR to first-time users
                user_id = response.user.id
                app_meta = (response.user.app_metadata or {}) if response.user else {}
                if "role" not in app_meta:
                    try:
                        client.auth.admin.update_user_by_id(
                            user_id, {"app_metadata": {"role": ROLE_VALIDATOR}}
                        )
                    except Exception:
                        pass
                    role = ROLE_VALIDATOR
                else:
                    role = app_meta["role"]
                    if role not in {ROLE_ADMIN, ROLE_MODERATOR, ROLE_VALIDATOR, ROLE_REVIEWER, ROLE_READER}:
                        role = ROLE_VALIDATOR

                # Prime the in-process cache so the first request after login is instant
                _role_cache[user_id] = (role, _time.monotonic() + _ROLE_CACHE_TTL)

                # Redirect by role
                if role in (ROLE_ADMIN, ROLE_MODERATOR):
                    return redirect("/admin/conflicts", 302)
                elif role == ROLE_READER:
                    return redirect("/", 302)
                else:
                    return redirect("/work", 302)
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
            try:
                public_stats = _db.get_public_stats()
            except Exception:
                public_stats = {
                    "mesas_all_three": 0,
                    "mesas_analyzed": 0,
                    "mesas_remaining": 0,
                    "total_anomalias": 0,
                    "total_universe": 122_020,
                }
            semaphore_data = _build_semaphore_data()
            semaphore_global = semaphore_data["global"] if semaphore_data else None
            return render_template(
                "home.html",
                logged_in=bool(email),
                user_email=email,
                is_open=state["is_open"],
                launch_iso=state["launch_iso"],
                mesas_all_three=public_stats["mesas_all_three"],
                mesas_analyzed=public_stats["mesas_analyzed"],
                mesas_remaining=public_stats["mesas_remaining"],
                total_anomalias=public_stats["total_anomalias"],
                total_universe=public_stats["total_universe"],
                semaphore_global=semaphore_global,
            )

        # ----------------------------------------------------------------
        # GET /mesas — public hierarchical drill-down (Municipio -> Puesto -> Mesa)
        # ----------------------------------------------------------------

        @app.route("/mesas")
        def mesas_view() -> str:
            page = _parse_mesas_page(request.args.get("page"))
            dept_filter = (request.args.get("dept") or "").strip() or None

            try:
                stats = _db.get_hierarchical_mesa_stats()
            except Exception as exc:
                logger.warning("mesas_view (L1) get_hierarchical_mesa_stats failed: %s", exc)
                stats = None

            # Empty hierarchical with known national data = backend failure, not "no mesas".
            hier_total = ((stats or {}).get("_global") or {}).get("total", 0) or 0
            if stats is None or (hier_total == 0 and not (stats or {}).get("by_mpio")):
                try:
                    flat = _db.get_mesa_stats() or {}
                    flat_total = (flat.get("_global") or {}).get("total", 0) or 0
                except Exception:
                    flat_total = 0
                if flat_total > 0 or stats is None:
                    return render_template(
                        "mesas.html", level=1, unavailable=True,
                        breadcrumbs=_mesas_l1_breadcrumbs(), rows=[],
                        page=1, total_pages=1, semaphore=None,
                        dept_filter=None, dept_options=[],
                    )

            all_rows = _build_mesas_level1_rows(stats, dept_filter=dept_filter)
            page_rows, page, total_pages = _paginate_rows(
                all_rows, page, MESAS_L1_PAGE_SIZE,
            )
            return render_template(
                "mesas.html", level=1, unavailable=False,
                breadcrumbs=_mesas_l1_breadcrumbs(), rows=page_rows,
                page=page, total_pages=total_pages,
                semaphore=_semaphore_from_level_rows(all_rows),
                dept_filter=dept_filter,
                dept_options=_dept_filter_options(stats),
            )

        # ----------------------------------------------------------------
        # GET /mesas/<dept>/<mpio> — Level 2: puestos within a municipality
        # ----------------------------------------------------------------

        @app.route("/mesas/<dept>/<mpio>")
        def mesas_level2_view(dept: str, mpio: str) -> str:
            page = _parse_mesas_page(request.args.get("page"))
            try:
                stats = _db.get_hierarchical_mesa_stats()
            except Exception as exc:
                logger.warning("mesas_level2_view get_hierarchical_mesa_stats failed: %s", exc)
                stats = None

            breadcrumbs = _mesas_l2_breadcrumbs(dept, mpio)
            if stats is None:
                return render_template(
                    "mesas.html", level=2, unavailable=True,
                    breadcrumbs=breadcrumbs, rows=[],
                    page=1, total_pages=1, semaphore=None,
                    dept_filter=None, dept_options=[],
                )

            all_rows = _build_mesas_level2_rows(stats, dept, mpio)
            page_rows, page, total_pages = _paginate_rows(
                all_rows, page, MESAS_L2_PAGE_SIZE,
            )
            return render_template(
                "mesas.html", level=2, unavailable=False,
                breadcrumbs=breadcrumbs, rows=page_rows,
                page=page, total_pages=total_pages,
                semaphore=_semaphore_from_level_rows(all_rows),
                dept_filter=None, dept_options=[],
            )

        # ----------------------------------------------------------------
        # GET /mesas/<dept>/<mpio>/<zona>/<puesto> — Level 3: mesas, 10/page
        # ----------------------------------------------------------------

        @app.route("/mesas/<dept>/<mpio>/<zona>/<puesto>")
        def mesas_level3_view(dept: str, mpio: str, zona: str, puesto: str) -> str:
            page = _parse_mesas_page(request.args.get("page"))

            breadcrumbs = _mesas_l3_breadcrumbs(dept, mpio, zona, puesto)
            try:
                rows, total = _build_mesas_level3_rows(dept, mpio, zona, puesto, page)
            except Exception as exc:
                logger.warning("mesas_level3_view failed: %s", exc)
                return render_template(
                    "mesas.html", level=3, unavailable=True,
                    breadcrumbs=breadcrumbs, rows=[],
                    page=1, total_pages=1, semaphore=None,
                    dept_filter=None, dept_options=[],
                    sin_actas=False, logged_in=bool(session.get("access_token")),
                )

            total_pages = max(1, (total + MESAS_L3_PAGE_SIZE - 1) // MESAS_L3_PAGE_SIZE) if total else 1
            return render_template(
                "mesas.html", level=3, unavailable=False,
                breadcrumbs=breadcrumbs, rows=rows,
                page=page, total_pages=total_pages, semaphore=None,
                dept_filter=None, dept_options=[],
                sin_actas=(total == 0),
                logged_in=bool(session.get("access_token")),
            )

        # ----------------------------------------------------------------
        # Main labeling route (production) — gated by launch time
        # ----------------------------------------------------------------

        @app.route("/work")
        @require_auth
        @require_role(ROLE_VALIDATOR, ROLE_ADMIN)
        def work_view() -> str:
            from flask import g
            if not _launch_state()["is_open"]:
                return redirect("/", code=302)
            _db.release_expired_assignments()
            crop_id = _db.assign_next_crop(g.user_id, app.config["MODULE"])

            # Real progress metrics (with fallbacks to avoid template crashes)
            try:
                progress = _db.get_real_progress()
                started = progress["started"]
                confirmed = progress["confirmed"]
                total = progress["total"]
            except Exception:
                started = 0
                confirmed = 0
                total = 0
            pct = round((started + confirmed) / (2 * total) * 100, 2) if total > 0 else 0

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
                    valid_report_glyph_js=VALID_REPORT_GLYPH_JS,
                    supabase_mode=True,
                )

            crop = _db.get_crop_details(crop_id)

            # Full cell crop id — look up from local index (not stored in Supabase)
            # Full cell crop id
            full_cell_crop_id = crop.get("full_cell_crop_id") or crop_id
            pdf_path = crop.get("pdf_path", "")
            # Auto-find full cell if link missing (segunda vuelta)
            if full_cell_crop_id == crop_id or not crop.get("full_cell_crop_id"):
                try:
                    fc_resp = _db._client().table("crops").select("crop_id").eq("pdf_path", pdf_path).eq("field_name", crop.get("field_name", "")).eq("digit_index", -1).limit(1).execute()
                    if fc_resp.data:
                        full_cell_crop_id = fc_resp.data[0]["crop_id"]
                except Exception:
                    pass
            recent = session.get("recent_labels", [])

            # Concordancias — same PDF, same label_ocr, from local index
            pdf_path = crop.get("pdf_path", "")
            label_ocr = crop.get("label_ocr") or "?"
            if label_ocr.lower() in ("undefined", "null", "none"):
                label_ocr = "?"
            concordancias = _db.get_concordancias(pdf_path, label_ocr, crop_id)

            # Global stats — cache in session
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
                pdf_source_url=crop.get("source_url", ""),
                valid_report_glyph_js=VALID_REPORT_GLYPH_JS,
                supabase_mode=True,
            )

        # ----------------------------------------------------------------
        # POST /label (production)
        # ----------------------------------------------------------------

        @app.route("/label", methods=["POST"])
        @require_auth
        @require_role(ROLE_VALIDATOR, ROLE_ADMIN)
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
                    vuelta=app.config["MODULE"],
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
        @require_role(ROLE_VALIDATOR, ROLE_ADMIN)
        def skip_view() -> Response:
            from flask import g
            body = request.get_json(force=True, silent=True) or {}
            crop_id = body.get("crop_id", "")
            try:
                _cli = _db._client()
                # Delete active assignment (only for this crop, not all)
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
                                "vuelta": app.config["MODULE"],
                            }).execute()
                    except Exception:
                        logger.warning("skip_view: _skip label insert failed for crop=%s user=%s", crop_id, g.user_id)
                    # Check skip count and escalate
                    try:
                        skip_count_resp = _cli.table("labels").select("id", count="exact").eq("crop_id", crop_id).eq("label_human", "_skip").execute()
                        skip_count = skip_count_resp.count or 0
                        if skip_count >= 3:
                            _cli.table("crops").update({"status": "disputed", "confirmed_label": "_skip_x3"}).eq("crop_id", crop_id).execute()
                        elif skip_count >= 2:
                            _cli.table("crops").update({"status": "needs_third"}).eq("crop_id", crop_id).execute()
                    except Exception:
                        logger.warning("skip_view: skip escalation failed for crop=%s", crop_id)
            except Exception as exc:
                return _error_response(f"Skip failed: {exc}", 500)
            return jsonify({"ok": True})

        # ----------------------------------------------------------------
        # GET /image/<crop_id> (production)
        # ----------------------------------------------------------------

        @app.route("/image/<crop_id>")
        @require_auth
        @require_role(ROLE_VALIDATOR, ROLE_REVIEWER, ROLE_ADMIN)
        def image_view(crop_id: str) -> Response:
            # Sanitize: allow only safe characters
            if not crop_id.replace("-", "").replace("_", "").isalnum():
                return Response("Invalid crop_id", status=400)

            if _use_storage:
                storage_url = _db.get_storage_url(crop_id)
                return redirect(storage_url, code=302)
            else:
                png_path = resolved_labels_dir / "crops" / f"{crop_id}.png"
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
                conflicts = sum(1 for r in rows if r.get("status") == "disputed")
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
        # GET /admin, /admin/ — redirect to the admin panel landing page.
        # Auth/role enforcement happens on the target route itself.
        # ----------------------------------------------------------------

        @app.route("/admin")
        @app.route("/admin/")
        def admin_root_redirect() -> Response:
            return redirect("/admin/conflicts", 302)

        # ----------------------------------------------------------------
        # GET /admin/conflicts (production — admin and moderator)
        # ----------------------------------------------------------------

        @app.route("/admin/conflicts")
        @require_auth
        @require_role(ROLE_ADMIN, ROLE_MODERATOR)
        def admin_conflicts_view() -> Response:
            conflicts      = _db.get_conflict_crops()
            fraud_marks    = _db.get_fraud_marks()
            feedback_marks = _db.get_feedback_marks()
            amended_crops  = _db.get_amended_crops()
            reports        = _db.get_reports()
            mesa_reports   = _db.get_mesa_reports()

            try:
                user_reports_page = int(request.args.get("page", 1))
            except (TypeError, ValueError):
                user_reports_page = 1
            if user_reports_page < 1:
                user_reports_page = 1

            user_reports       = _db.list_recent_transversal_reports(page=user_reports_page, per_page=50)
            user_reports_total = _db.count_recent_transversal_reports()
            user_reports_total_pages = (
                max(1, (user_reports_total + 49) // 50) if user_reports_total else 1
            )

            user_role      = getattr(g, "user_role", "")
            return render_template(
                "admin.html",
                conflicts=conflicts,
                fraud_marks=fraud_marks,
                feedback_marks=feedback_marks,
                amended_crops=amended_crops,
                reports=reports,
                mesa_reports=mesa_reports,
                user_reports=user_reports,
                user_reports_page=user_reports_page,
                user_reports_total=user_reports_total,
                user_reports_total_pages=user_reports_total_pages,
                user_role=user_role,
            )

        # ----------------------------------------------------------------
        # GET /admin/mesas/<mesa_key> — single-mesa detail (algorithm status
        # + user reports + decision-confirmation status), Phase A of
        # mesa-findings-consolidation plus mesa-detail-decision-status.
        # View-only: renders transversal_review_decisions state (field x
        # source matrix + per-field edit window); no write path/route here.
        # ----------------------------------------------------------------

        @app.route("/admin/mesas/<mesa_key>")
        @require_auth
        @require_role(ROLE_ADMIN, ROLE_MODERATOR)
        def admin_mesa_detail_view(mesa_key: str) -> Response:
            results = _db.get_mesa_results(mesa_key=mesa_key)
            mesa_status = results[0] if results else None
            reports = _db.list_transversal_reports(mesa_key)
            decisions = _db.get_transversal_decisions(mesa_key).get(mesa_key, {})
            edit_window = _db.get_transversal_decision_edit_window(mesa_key)

            user_role = getattr(g, "user_role", "")
            return render_template(
                "mesa_detail.html",
                mesa_key=mesa_key,
                mesa_status=mesa_status,
                reports=reports,
                decisions=decisions,
                edit_window=edit_window,
                user_role=user_role,
            )

        # ----------------------------------------------------------------
        # Transversal review panel (moderator + admin)
        # ----------------------------------------------------------------

        from src.modules.review import images as _review_images
        from src.modules.review.alerts import SOURCES as _TRANSVERSAL_SOURCES
        from src.modules.review.alerts import build_mesa_alert_package
        from src.modules.review.exclusions import load_excluded_keys
        from src.modules.review.datasets import dataset_config
        from src.modules.review.field_audit import build_index_row, resolve_primary_source
        from src.modules.review.queue import (
            build_queue_page,
            get_transversal_index_row,
            list_queue_page_mesa_keys,
            load_transversal_index_rows,
        )

        def _transversal_dataset() -> dict:
            return dataset_config()

        def _transversal_load_index_rows() -> list[dict]:
            src = resolve_primary_source()
            excluded = load_excluded_keys("confirmed", source=src)
            return load_transversal_index_rows(excluded=excluded, source=src)

        def _transversal_raw_for_mesa(mesa_key: str) -> dict | None:
            raw = _db.get_mesa_raw_data(mesa_key)
            if raw:
                return raw
            from src.modules.review.queue import iter_cross_rows
            from src.modules.review.field_audit import mesa_key as _mk_fn
            for row in iter_cross_rows():
                if _mk_fn(row) == mesa_key:
                    return row
            return None

        @app.route("/admin/transversal")
        @require_auth
        @require_role(ROLE_ADMIN, ROLE_MODERATOR)
        def admin_transversal_view() -> Response:
            cfg = _transversal_dataset()
            return render_template(
                "transversal.html",
                dataset_key=cfg["key"],
                dataset_label=cfg["label"],
            )

        @app.route("/api/transversal/queue")
        @require_auth
        @require_role(ROLE_ADMIN, ROLE_MODERATOR)
        def api_transversal_queue() -> Response:
            try:
                page = max(1, int(request.args.get("page", 1)))
            except (TypeError, ValueError):
                page = 1
            try:
                page_size = min(200, max(1, int(request.args.get("page_size", 50))))
            except (TypeError, ValueError):
                page_size = 50
            dept = request.args.get("dept") or None
            status = (request.args.get("status") or "all").lower()
            pending_only = (
                status == "pending"
                or request.args.get("pending_only", "").lower() in ("1", "true", "yes")
            )
            done_only = status == "done"
            q = request.args.get("q") or None

            cfg = _transversal_dataset()
            rows = _transversal_load_index_rows()
            exclusions = load_excluded_keys("confirmed", source=cfg["primary_source"])
            decided_slots = _db.get_transversal_decided_slots()
            page_keys = list_queue_page_mesa_keys(
                rows,
                exclusions,
                page=page,
                page_size=page_size,
                dept=dept,
                pending_only=pending_only,
                done_only=done_only,
                q=q,
                decided_slots=decided_slots,
                source_available=_review_images.queue_source_available,
            )
            scoped_decisions = (
                _db.get_transversal_decisions(mesa_keys=page_keys) if page_keys else {}
            )
            result = build_queue_page(
                rows,
                exclusions,
                page=page,
                page_size=page_size,
                dept=dept,
                pending_only=pending_only,
                done_only=done_only,
                q=q,
                decisions=scoped_decisions,
                decided_slots=decided_slots,
                source_available=_review_images.queue_source_available,
            )
            queue_mesas = result.get("queue_mesas", result["total"])
            pending_human = result["stats"]["pending_human"]
            result["pending"] = pending_human
            result["queue_mesas"] = queue_mesas
            result["done"] = max(0, queue_mesas - pending_human)
            result["has_more"] = page * page_size < result["total"]
            result["dataset"] = {
                "key": cfg["key"],
                "label": cfg["label"],
                "primary_source": cfg["primary_source"],
            }
            return jsonify(result)

        @app.route("/api/transversal/mesa/<mesa_key>")
        @require_auth
        @require_role(ROLE_ADMIN, ROLE_MODERATOR)
        def api_transversal_mesa(mesa_key: str) -> Response:
            index_row = get_transversal_index_row(mesa_key, source=resolve_primary_source())
            if not index_row:
                raw = _transversal_raw_for_mesa(mesa_key)
                if not raw:
                    return jsonify({"error": "mesa_not_found"}), 404
                index_row = build_index_row(raw, resolve_primary_source())
                if not index_row:
                    return jsonify({"error": "not_conflictiva"}), 404
            alerts = build_mesa_alert_package(
                index_row,
                mesa_key,
                source_available=_review_images.source_available,
            )
            pages = {
                src: _review_images.get_page_count(mesa_key, src)
                for src in _TRANSVERSAL_SOURCES
                if _review_images.source_available(mesa_key, src)
            }
            decisions = _db.get_transversal_decisions(mesa_key).get(mesa_key, {})
            reports = _db.list_transversal_reports(mesa_key)
            edit_window = _db.get_transversal_decision_edit_window(mesa_key)
            storage_base = _review_images.storage_public_base()
            return jsonify({
                "mesa_key": mesa_key,
                "dept": index_row.get("dept"),
                "mpio": index_row.get("mpio"),
                "zona": index_row.get("zona"),
                "puesto": index_row.get("puesto"),
                "mesa": index_row.get("mesa"),
                "candidate_votes": index_row.get("candidate_votes"),
                "blank_fields": index_row.get("blank_fields"),
                "alerts": alerts,
                "pages": pages,
                "decisions": decisions,
                "reports": reports,
                "decision_edit": edit_window,
                "storage_base": storage_base,
                "image_formats": list(_review_images.GALLERY_EXTS),
            })

        @app.route("/api/transversal/mesa/<mesa_key>/page/<source>/<int:page>")
        @require_auth
        @require_role(ROLE_ADMIN, ROLE_MODERATOR)
        def api_transversal_page(mesa_key: str, source: str, page: int) -> Response:
            if source not in _TRANSVERSAL_SOURCES:
                return jsonify({"error": "invalid_source"}), 400
            fmt = (request.args.get("fmt") or _review_images.GALLERY_EXT_PRIMARY).lower()
            if fmt not in _review_images.GALLERY_EXTS:
                fmt = _review_images.GALLERY_EXT_PRIMARY
            cdn_url = _review_images.storage_public_url(mesa_key, source, page, ext=fmt)
            if cdn_url and _review_images.storage_bucket():
                return redirect(cdn_url, code=302)
            rendered = _review_images.render_page_to_cache(mesa_key, source, page)
            if not rendered:
                return jsonify({"error": "page_not_found"}), 404
            path, mimetype = rendered
            return send_file(path, mimetype=mimetype)

        @app.route("/api/transversal/decisions", methods=["POST"])
        @require_auth
        @require_role(ROLE_ADMIN, ROLE_MODERATOR)
        def api_transversal_decisions_post() -> Response:
            from flask import g
            body = request.get_json(force=True, silent=True) or {}
            mesa_key = body.get("mesa_key")
            field = body.get("field")
            source = body.get("source")
            decision = body.get("decision")
            notes = body.get("notes")
            if not mesa_key or not field or not source or not decision:
                return jsonify({"ok": False, "error": "missing_fields"}), 400
            ok = _db.upsert_transversal_decision(
                mesa_key, field, source, decision, g.user_id, notes=notes
            )
            if not ok:
                # Edit lock is per-field (3 h from first decision on that field).
                window = _db.get_transversal_decision_edit_window(mesa_key, field=field)
                if window["decision_count"] > 0 and not window["editable"]:
                    return jsonify({"ok": False, "error": "edit_window_expired"}), 403
                return jsonify({"ok": False, "error": "invalid_or_failed"}), 400
            return jsonify({"ok": True})

        @app.route("/api/transversal/decisions/reopen", methods=["POST"])
        @require_auth
        @require_role(ROLE_ADMIN, ROLE_MODERATOR)
        def api_transversal_decisions_reopen() -> Response:
            body = request.get_json(force=True, silent=True) or {}
            mesa_key = body.get("mesa_key")
            field = body.get("field")  # optional: reopen one field only
            if not mesa_key:
                return jsonify({"ok": False, "error": "missing_fields"}), 400
            ok, err = _db.reopen_transversal_decisions(mesa_key, field=field)
            if not ok:
                code = 403 if err == "edit_window_expired" else 400
                return jsonify({"ok": False, "error": err or "failed"}), code
            return jsonify({"ok": True})

        @app.route("/api/transversal/decisions/export")
        @require_auth
        @require_role(ROLE_ADMIN, ROLE_MODERATOR)
        def api_transversal_decisions_export() -> Response:
            payload = _db.export_transversal_decisions()
            dataset = payload.get("project", "transversal_review_E14C_conflictivas")
            filename = f"decisiones_transversal_{dataset}.json"
            return Response(
                json.dumps(payload, ensure_ascii=False, indent=2),
                mimetype="application/json",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

        @app.route("/api/transversal/reports", methods=["GET", "POST"])
        @require_auth
        @require_role(ROLE_ADMIN, ROLE_MODERATOR)
        def api_transversal_reports() -> Response:
            from flask import g

            if request.method == "GET":
                mesa_key = (request.args.get("mesa_key") or "").strip()
                if not mesa_key:
                    return jsonify({"error": "missing_fields"}), 400
                return jsonify({"reports": _db.list_transversal_reports(mesa_key)})

            body = request.get_json(force=True, silent=True) or {}
            payload, err = _validate_transversal_reports_payload(body)
            if err:
                return jsonify({"ok": False, "error": err}), 400
            inserted = _db.insert_transversal_reports(
                payload["mesa_key"],
                payload["entries"],
                g.user_id,
            )
            if not inserted:
                return jsonify({"ok": False, "error": "insert_failed"}), 400
            return jsonify({"ok": True, "reports": inserted})

        @app.route("/api/transversal/reports/<report_id>", methods=["DELETE"])
        @require_auth
        @require_role(ROLE_ADMIN, ROLE_MODERATOR)
        def api_transversal_report_delete(report_id: str) -> Response:
            from flask import g
            from src.modules.labeler.auth import _get_user_role

            allow_any = _get_user_role(g.user_id) == ROLE_ADMIN
            ok = _db.delete_transversal_report(
                report_id,
                g.user_id,
                allow_any=allow_any,
            )
            if not ok:
                return jsonify({"ok": False, "error": "not_found_or_forbidden"}), 403
            return jsonify({"ok": True})

        # ----------------------------------------------------------------
        # POST /admin/hide — soft-delete a fraud/feedback/report record (admin only)
        # ----------------------------------------------------------------

        @app.route("/admin/hide", methods=["POST"])
        @require_auth
        @require_role(ROLE_ADMIN)
        def admin_hide_view() -> Response:
            body = request.get_json(force=True, silent=True) or {}
            table = body.get("table", "")
            record_id = body.get("id")
            if table not in {"fraud_marks", "feedback_marks", "reports"}:
                return jsonify({"ok": False, "error": "Invalid table"}), 400
            if not isinstance(record_id, int):
                return jsonify({"ok": False, "error": "Invalid id"}), 400
            _db.hide_mark(table, record_id)
            return jsonify({"ok": True})

        # ----------------------------------------------------------------
        # GET /admin/users — list registered Supabase Auth users (admin only)
        # ----------------------------------------------------------------

        @app.route("/admin/users")
        @require_auth
        @require_role(ROLE_ADMIN)
        def admin_users_view() -> Response:
            try:
                raw_users = _db._client().auth.admin.list_users()
                unavailable = False
            except Exception as exc:
                logger.error("admin_users_view: list_users failed: %s", exc)
                raw_users = []
                unavailable = True

            users = []
            for u in raw_users:
                app_meta = getattr(u, "app_metadata", None) or {}
                role = app_meta.get("role", ROLE_VALIDATOR)
                if role not in {ROLE_ADMIN, ROLE_MODERATOR, ROLE_VALIDATOR, ROLE_REVIEWER, ROLE_READER}:
                    role = ROLE_VALIDATOR
                users.append({
                    "id": getattr(u, "id", ""),
                    "email": getattr(u, "email", "") or "",
                    "role": role,
                    "email_confirmed_at": getattr(u, "email_confirmed_at", None),
                    "created_at": _fmt_admin_timestamp(getattr(u, "created_at", None)),
                    "last_sign_in_at": _fmt_admin_timestamp(getattr(u, "last_sign_in_at", None)),
                })

            return render_template(
                "admin_users.html",
                users=users,
                non_admin_roles=sorted(NON_ADMIN_ROLES),
                unavailable=unavailable,
            )

        # ----------------------------------------------------------------
        # POST /admin/users/role — reassign a non-admin user's role (admin only)
        #
        # Guardrails (spec: admin-user-management):
        #   - Self role-change is rejected independently of the admin-immutability
        #     check (own guard clause, own test).
        #   - Target's CURRENT role is re-read server-side (never trusted from the
        #     request body) — if it resolves to ROLE_ADMIN, the request is rejected
        #     and no write occurs, whether the target is a peer admin or (were the
        #     self-check ever bypassed) the acting admin themself.
        #   - Only app_metadata.role is ever written — the write payload is built
        #     from scratch server-side; the request body is never forwarded.
        # ----------------------------------------------------------------

        @app.route("/admin/users/role", methods=["POST"])
        @require_auth
        @require_role(ROLE_ADMIN)
        def admin_users_role_view() -> Response:
            body = request.get_json(force=True, silent=True) or {}
            target_id = body.get("user_id", "")
            new_role = body.get("role", "")

            if not target_id:
                return jsonify({"ok": False, "error": "missing user_id"}), 400

            # Self role-change guard — independent of the admin-immutability check below.
            if target_id == g.user_id:
                return jsonify({"ok": False, "error": "cannot change your own role"}), 403

            if new_role not in NON_ADMIN_ROLES:
                return jsonify({"ok": False, "error": "invalid role"}), 400

            admin_client = _db._client()
            try:
                current_resp = admin_client.auth.admin.get_user_by_id(target_id)
                current_user = current_resp.user if current_resp else None
            except Exception as exc:
                logger.error("admin_users_role_view: get_user_by_id failed target=%s: %s", target_id, exc)
                return jsonify({"ok": False, "error": "user not found"}), 400

            if current_user is None:
                return jsonify({"ok": False, "error": "user not found"}), 400

            current_app_meta = getattr(current_user, "app_metadata", None) or {}
            current_role = current_app_meta.get("role", ROLE_VALIDATOR)

            # Admin-immutability guardrail — server-side re-read, never trusted from request.
            if current_role == ROLE_ADMIN:
                return jsonify({"ok": False, "error": "cannot modify an admin account"}), 403

            try:
                admin_client.auth.admin.update_user_by_id(
                    target_id, {"app_metadata": {"role": new_role}}
                )
            except Exception as exc:
                logger.error("admin_users_role_view: update_user_by_id failed target=%s: %s", target_id, exc)
                return jsonify({"ok": False, "error": "role change failed"}), 400

            # The write already committed — cache eviction and the audit log are
            # best-effort follow-ups and must never turn a successful write into
            # an apparent failure (or a 500) for the caller.
            try:
                evict_role_cache(target_id)
            except Exception as exc:
                logger.error("admin_users_role_view: evict_role_cache failed target=%s: %s", target_id, exc)

            try:
                actor_email = _strip_crlf(session.get("user_email", ""))
                target_email = _strip_crlf(getattr(current_user, "email", "") or "")
                logger.info(
                    "role-change actor=%s (%s) target=%s (%s) old=%s new=%s",
                    g.user_id, actor_email, target_id, target_email, current_role, new_role,
                )
            except Exception as exc:
                logger.error("admin_users_role_view: audit log failed target=%s: %s", target_id, exc)

            return jsonify({"ok": True, "role": new_role})

        # ----------------------------------------------------------------
        # GET /debug/sentry-test (admin only) — raises a controlled exception
        # to verify Sentry backend capture is working end-to-end.
        # ----------------------------------------------------------------

        @app.route("/debug/sentry-test")
        @require_auth
        @require_role(ROLE_ADMIN)
        def sentry_test_view() -> Response:
            raise RuntimeError("Sentry backend test — intentional exception")

        # ----------------------------------------------------------------
        # GET /next — return next crop as JSON (SPA update, no page reload)
        # ----------------------------------------------------------------

        @app.route("/next")
        @require_auth
        @require_role(ROLE_VALIDATOR, ROLE_ADMIN)
        def next_view() -> Response:
            from flask import g
            if not _launch_state()["is_open"]:
                return jsonify({"locked": True, "launch_iso": _launch_state()["launch_iso"]})
            _db.release_expired_assignments()
            crop_id = _db.assign_next_crop(g.user_id, app.config["MODULE"])

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
            pct = round((started + confirmed) / (2 * total) * 100, 2) if total > 0 else 0

            if not crop_id:
                done_reason = "queue_exhausted" if confirmed < total else "all_done"
                return jsonify({"done": True, "done_reason": done_reason,
                                "started": started, "confirmed": confirmed,
                                "total": total, "pct": pct, "labeled": 0})

            crop = _db.get_crop_details(crop_id)
            pdf_path = crop.get("pdf_path", "")
            label_ocr = crop.get("label_ocr") or "?"
            if label_ocr.lower() in ("undefined", "null", "none"):
                label_ocr = "?"
            full_cell_crop_id = crop.get("full_cell_crop_id") or crop_id
            # Auto-find full cell if link missing (segunda vuelta)
            if full_cell_crop_id == crop_id or not crop.get("full_cell_crop_id"):
                try:
                    fc_resp = _db._client().table("crops").select("crop_id").eq("pdf_path", pdf_path).eq("field_name", crop.get("field_name", "")).eq("digit_index", -1).limit(1).execute()
                    if fc_resp.data:
                        full_cell_crop_id = fc_resp.data[0]["crop_id"]
                except Exception:
                    pass
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
                "source_url": crop.get("source_url", ""),
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
        @require_role(ROLE_VALIDATOR, ROLE_ADMIN)
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
            # Best-effort retraction of recent reports/marks for the rolled-back crop.
            # Errors are swallowed — retraction failure must never block /back.
            try:
                _db.retract_recent_marks(g.user_id, last_crop)
            except Exception as exc:
                app.logger.warning("retract_recent_marks failed: %s", exc)
            return jsonify({"ok": True})

        # ----------------------------------------------------------------
        # GET /pdf?v=<crop_id> — serve PDF from local disk (production mode)
        # ----------------------------------------------------------------

        @app.route("/pdf")
        @require_auth
        @require_role(ROLE_VALIDATOR, ROLE_REVIEWER, ROLE_ADMIN)
        def pdf_view_prod() -> Response:
            crop_id = request.args.get("v", "")
            if not crop_id:
                return Response("Missing crop_id", status=400)
            details = _db.get_crop_details(crop_id)
            if not details:
                return Response("Crop not found", status=404)
            source_url = (details.get("source_url") or "").strip()
            if not source_url:
                source_url = _reconstruct_source_url(details.get("pdf_path", ""))
            if source_url:
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
        @require_role(ROLE_VALIDATOR, ROLE_MODERATOR, ROLE_ADMIN)
        def mark_fraud_prod() -> Response:
            from flask import g
            body = request.get_json(force=True, silent=True) or {}
            crop_id = body.get("crop_id", "")
            reason = (body.get("reason", "") or "").strip()
            if not reason:
                return jsonify({"ok": False, "error": "Reason required"}), 400
            try:
                details = _db.get_crop_details(crop_id) if crop_id else None
            except Exception:
                details = None
            pdf_path = (details or {}).get("pdf_path", "")
            try:
                _db.record_fraud_mark(crop_id, pdf_path, reason, g.user_id)
            except Exception as exc:
                return jsonify({"ok": False, "error": f"Could not record: {exc}"}), 500
            return jsonify({"ok": True, "pdf_path": pdf_path})

        # ----------------------------------------------------------------
        # POST /feedback — report a minor observation about a crop
        # ----------------------------------------------------------------

        @app.route("/feedback", methods=["POST"])
        @require_auth
        def feedback_prod() -> Response:
            from flask import g
            body = request.get_json(force=True, silent=True) or {}
            recaptcha_token = body.get("g_recaptcha_response", "")
            if not _verify_recaptcha(recaptcha_token, "feedback"):
                return jsonify({"ok": False, "error": "Verificación de seguridad fallada."}), 403
            crop_id = body.get("crop_id", "")
            message = (body.get("message", "") or "").strip()
            if not message:
                return jsonify({"ok": False, "error": "Message required"}), 400
            try:
                details = _db.get_crop_details(crop_id) if crop_id else None
            except Exception:
                details = None
            pdf_path = (details or {}).get("pdf_path", "")
            try:
                _db.record_feedback(crop_id, pdf_path, message, g.user_id)
            except Exception as exc:
                return jsonify({"ok": False, "error": f"Could not record: {exc}"}), 500
            return jsonify({"ok": True})

        # ----------------------------------------------------------------
        # POST /api/mesa-report — public per-mesa citizen report
        # (SDD: public-mesa-report). Any authenticated role may submit;
        # mesa_key is verified server-side against mesa_results before
        # any write into the shared transversal_review_reports table
        # (anti-forgery guard — the client-supplied mesa_key is never
        # trusted directly).
        # ----------------------------------------------------------------

        @app.route("/api/mesa-report", methods=["POST"])
        @require_auth
        def mesa_report_prod() -> Response:
            import src.modules.labeler.db as _db
            body = request.get_json(force=True, silent=True) or {}
            recaptcha_token = body.get("g_recaptcha_response", "")
            if not _verify_recaptcha(recaptcha_token, "mesa_report"):
                return jsonify({"ok": False, "error": "Verificación de seguridad fallada."}), 403
            mesa_key = (body.get("mesa_key", "") or "").strip()
            notes = (body.get("notes", "") or "").strip()
            if not notes:
                return jsonify({"ok": False, "error": "notes_required"}), 400
            if not mesa_key:
                return jsonify({"ok": False, "error": "unknown_mesa"}), 400
            exists = _db.mesa_result_exists(mesa_key)
            if exists is None:
                return jsonify({"ok": False, "error": "service_unavailable"}), 503
            if not exists:
                return jsonify({"ok": False, "error": "unknown_mesa"}), 400
            inserted = _db.insert_transversal_reports(
                mesa_key,
                [{"source": "e14c", "report_type": "otro", "notes": notes}],
                g.user_id,
            )
            if not inserted:
                return jsonify({"ok": False, "error": "insert_failed"}), 500
            return jsonify({"ok": True, "reports": inserted})

        # ----------------------------------------------------------------
        # GET /admin/feedback — list feedback reports (admin + moderator)
        # ----------------------------------------------------------------

        @app.route("/admin/feedback")
        @require_auth
        @require_role(ROLE_ADMIN, ROLE_MODERATOR)
        def admin_feedback_view() -> Response:
            rows = _db.get_feedback_marks()
            return jsonify({"feedback": rows, "total": len(rows)})

        # ----------------------------------------------------------------
        # GET /admin/mesas/data — paginated mesa_results list (admin only)
        # ----------------------------------------------------------------

        @app.route("/admin/mesas/data")
        @require_auth
        @require_role(ROLE_ADMIN, ROLE_MODERATOR)
        def admin_mesas_data_view() -> Response:
            try:
                dept = request.args.get("dept") or None
                status = request.args.get("status") or None
                source_missing = request.args.get("source") or None
                try:
                    page = int(request.args.get("page", 1))
                except (TypeError, ValueError):
                    page = 1
                rows = _db.get_mesa_results(
                    dept=dept, status=status, source_missing=source_missing, page=page
                )
                for row in rows:
                    row["dept_name"] = _DEPT_NAMES.get(row.get("dept"), row.get("dept"))
                stats = _db.get_mesa_stats()
                total = (stats.get("_global") or {}).get("total", 0)
                return jsonify({"rows": rows, "page": page, "total": total})
            except Exception as exc:
                logger.warning("admin_mesas_data_view error: %s", exc)
                return jsonify({"rows": [], "page": 1, "total": 0})

        # ----------------------------------------------------------------
        # GET /admin/mesas/stats — aggregated mesa status counts (admin only)
        # ----------------------------------------------------------------

        @app.route("/admin/mesas/stats")
        @require_auth
        @require_role(ROLE_ADMIN, ROLE_MODERATOR)
        def admin_mesas_stats_view() -> Response:
            try:
                stats = _db.get_mesa_stats()
                return jsonify(stats)
            except Exception as exc:
                logger.warning("admin_mesas_stats_view error: %s", exc)
                return jsonify({})

        # ----------------------------------------------------------------
        # POST /report — structured anomaly report (enmienda or otro)
        # ----------------------------------------------------------------

        @app.route("/report", methods=["POST"])
        @require_auth
        @require_role(ROLE_VALIDATOR, ROLE_ADMIN)
        def report_view_prod() -> Response:
            from flask import g
            body = request.get_json(force=True, silent=True) or {}
            payload, err = _validate_report_payload(body)
            if err:
                return jsonify({"ok": False, "error": err}), 400
            # Enrich pdf_path from crop details when not provided by client
            try:
                details = _db.get_crop_details(payload["crop_id"])
            except Exception:
                details = None
            pdf_path = (details or {}).get("pdf_path", "") or payload.get("pdf_path") or ""
            # Mesa dedup pre-check: one mesa report per (annotator, pdf_path)
            if payload.get("report_type") == "mesa":
                if _db.check_mesa_already_reported(pdf_path, g.user_id):
                    return jsonify({"ok": False, "error": "already_reported"}), 409
            try:
                _db.record_report(
                    crop_id=payload["crop_id"],
                    pdf_path=pdf_path or None,
                    report_type=payload["report_type"],
                    annotator=g.user_id,
                    digit_original=payload.get("digit_original"),
                    digit_corrected=payload.get("digit_corrected"),
                    digit=payload.get("digit"),
                    notes=payload.get("notes"),
                )
            except Exception as exc:
                # Catch DB unique-constraint violation (race between pre-check and INSERT)
                pgcode = getattr(exc, "pgcode", None) or getattr(
                    getattr(exc, "__cause__", None), "pgcode", None
                )
                if pgcode == "23505":
                    return jsonify({"ok": False, "error": "already_reported"}), 409
                return jsonify({"ok": False, "error": f"Could not record: {exc}"}), 500
            return jsonify({"ok": True})

    else:
        # ----------------------------------------------------------------
        # LOCAL DEV MODE: preserve existing _STATE singleton + all routes
        # ----------------------------------------------------------------
        labels_dir = resolved_labels_dir
        index_path = labels_dir / "crops" / "index.jsonl"
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
            started = labeled
            confirmed = labeled
            pct = round((started + confirmed) / (2 * total) * 100, 2) if total > 0 else 0
            if item is None:
                done_reason = "queue_exhausted" if confirmed < total else "all_done"
                return jsonify({
                    "done": True, "done_reason": done_reason,
                    "started": started, "confirmed": confirmed,
                    "total": total, "pct": pct, "labeled": labeled,
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

    Thin wrapper over _get_user_role — delegates all caching and API calls
    to the canonical role resolver in auth.py.
    Returns False on any error (fail-closed via _get_user_role).
    """
    from src.modules.labeler.auth import _get_user_role, ROLE_ADMIN
    return _get_user_role(user_id) == ROLE_ADMIN
