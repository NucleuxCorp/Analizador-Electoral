"""
consulta_defunciones.py — Batch NUIP vigencia lookup against Registraduria API.

Standalone citizen audit tool. Consults Colombian cédulas (NUIP) against the
public VigenciaCédula endpoint and classifies vigencia status (vivo, fallecido, etc.).

Usage:
    python consulta_defunciones.py              # interactive menu
    python consulta_defunciones.py --file X     # CLI batch mode

Dependencies: Python 3.8+ stdlib + tqdm (auto-installed by ejecutar.bat).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import platform
import signal
import ssl
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

os.system("")
_COLOR = "\033[38;2;140;193;211m"
_RESET = "\033[0m"

try:
    from tqdm import tqdm
except ImportError:
    print("tqdm is required: pip install tqdm", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent
VERSION = "1.0.0"

API_URL = "https://defunciones.registraduria.gov.co:8443/VigenciaCedula/consulta"
API_REFERER = "https://defunciones.registraduria.gov.co/"
USER_AGENT = "ConsultaDefunciones-PAE/1.0"
IPIFY_URL = "https://api.ipify.org?format=json"

DEFAULT_DELAY = 1.0
DEFAULT_COOLDOWN = 60
DEFAULT_FAIL_THRESHOLD = 3
DEFAULT_WORKERS = 1
HTTP_TIMEOUT = 30
CHECKPOINT_EVERY = 10

CONFIG_FILENAME = ".consulta_config.json"
CHECKPOINT_FILENAME = ".consulta_checkpoint.json"
RESULTADOS_FILENAME = "resultados.csv"
FALLECIDOS_FILENAME = "fallecidos.csv"
REPORT_FILENAME = "informe.md"
DEFAULT_INPUT = "cedulas.txt"
REPORTS_DIRNAME = "reportes"
LOG_FILENAME = "consulta.log"

logger = logging.getLogger("consulta_defunciones")


def reports_dir() -> Path:
    path = SCRIPT_DIR / REPORTS_DIRNAME
    path.mkdir(exist_ok=True)
    return path


def setup_logging() -> Path:
    """Log to reportes/consulta.log so failures can be diagnosed from a shareable file.

    Intentionally excludes NUIPs from log messages (only exception types/messages
    and HTTP/network metadata) since this file is meant to be shared for support.
    """
    log_path = reports_dir() / LOG_FILENAME
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.info(
        "Inicio de sesion — version=%s python=%s so=%s",
        VERSION,
        sys.version.split()[0],
        platform.platform(),
    )
    return log_path

NUIP_MIN_LEN = 6
NUIP_MAX_LEN = 10
NUIP_COLUMN_NAMES = frozenset({"nuip", "cedula", "documento", "cc", "identificacion"})

CSV_COLUMNS = [
    "nuip",
    "clasificacion",
    "vigencia_raw",
    "codigo",
    "fecha_consulta",
    "http_status",
    "error",
    "timestamp_consulta",
    "archivo_origen",
    "linea_origen",
]

VIGENCIA_MAP = {
    "Vigente (Vivo)": "VIVO",
    "Cancelada por Muerte": "FALLECIDO",
    "Cedula no Existe": "NO_EXISTE",
    "Sin datos para consultar": "SIN_DATOS",
}

LOGO = """
                  +++++++++++++++++++
              +++++++++++++++++++++++++++
           +++++++++++++++++++++++++++++++++
         ++++++++++++++++++=====++++++++++++++
       ++++++++++++++=----------------=+++++++++
      ++++++++++++--------==+++++==----=++++++++++
    +++++++++++=-----=+++++++++++++++++++++++++++++
   +++++++++++----=++++++++=====++++++++++++++++++++
  ++++++++++=---=++++++++=---------==++++++++++++++++
 ++++++++++----+++++++++=--------------=++++++++++++++
 +++++++++=---+++++++++---------------------=++++++++++
+++++++++=---++++++++------------------------=+++++++++
+++++++++=--+++++++++++++==----------------++++++++++++
+++++++++--=+++++++++++++++++++++=----------+++++++++++
++++++++=--+++++++++++++++++++++++++=--------++++++++++
++++++++=--+++++++++++++++++++++++++++-------++++++++++
++++++++=--++++++++++++++++++++++++++++------=+++++++++
+++++++++--++++++++++++++++++++++++++++=-----=+++++++++
+++++++++=-=+++++++++++++++++++++++++++=-----++++++++++
 +++++++++--+++++++++++++++++++++++++++=-----++++++++++
 ++++++++++--++++=-++++++++++++++++++++=----++++++++++
  ++++++++++--+=---=++++++++++++++++++=----++++++++++
   ++++++++++=------=++++++++++++++++=----++++++++++
    +++++++++++-------=++++++++++++=----=++++++++++
     *+++++++++++=--------======------=+++++++++++
       +++++++++++++=--------------=++++++++++++
         ++++++++++++++++=====++++++++++++++++
           +++++++++++++++++++++++++++++++++
              +++++++++++++++++++++++++++
                  +++++++++++++++++++
"""

_interrupted = False


def _handle_sigint(_signum: int, _frame: Any) -> None:
    global _interrupted
    _interrupted = True
    print("\n  Interrupcion detectada. Guardando progreso...", flush=True)


signal.signal(signal.SIGINT, _handle_sigint)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------
@dataclass
class NuipRecord:
    nuip: str
    archivo_origen: str = ""
    linea_origen: int = 0


@dataclass
class ConsultResult:
    nuip: str
    clasificacion: str
    vigencia_raw: str = ""
    codigo: str = ""
    fecha_consulta: str = ""
    http_status: int = 0
    error: str = ""
    timestamp_consulta: str = ""
    archivo_origen: str = ""
    linea_origen: int = 0

    def to_row(self) -> dict[str, str]:
        return {
            "nuip": self.nuip,
            "clasificacion": self.clasificacion,
            "vigencia_raw": self.vigencia_raw,
            "codigo": str(self.codigo),
            "fecha_consulta": self.fecha_consulta,
            "http_status": str(self.http_status),
            "error": self.error,
            "timestamp_consulta": self.timestamp_consulta,
            "archivo_origen": self.archivo_origen,
            "linea_origen": str(self.linea_origen),
        }


@dataclass
class BatchConfig:
    delay: float = DEFAULT_DELAY
    workers: int = DEFAULT_WORKERS
    ip_mode: str = "vacio"  # vacio | auto | manual
    ip_value: str = ""
    last_input_path: str = DEFAULT_INPUT


@dataclass
class RateLimiter:
    delay: float = DEFAULT_DELAY
    cooldown: float = DEFAULT_COOLDOWN
    fail_threshold: int = DEFAULT_FAIL_THRESHOLD
    consecutive_fails: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def wait_before_request(self) -> None:
        with self._lock:
            time.sleep(self.delay)
            if self.consecutive_fails >= self.fail_threshold:
                print(
                    f"  Pausa de enfriamiento: {self.cooldown:.0f}s "
                    f"({self.consecutive_fails} fallos consecutivos)...",
                    flush=True,
                )
                time.sleep(self.cooldown)
                self.consecutive_fails = 0

    def record_success(self) -> None:
        with self._lock:
            self.consecutive_fails = 0

    def record_transport_failure(self) -> None:
        with self._lock:
            self.consecutive_fails += 1

    def trigger_immediate_cooldown(self) -> None:
        with self._lock:
            print(f"  HTTP 429 — pausa inmediata de {self.cooldown:.0f}s...", flush=True)
            time.sleep(self.cooldown)
            self.consecutive_fails = 0


# ---------------------------------------------------------------------------
# NUIP validation
# ---------------------------------------------------------------------------
def normalize_nuip(raw: str) -> str:
    s = raw.strip()
    s = s.replace(".", "").replace(",", "").replace(" ", "")
    return s


def validate_nuip(nuip: str) -> tuple[bool, str]:
    if not nuip:
        return False, "VACIO"
    if not nuip.isdigit():
        return False, "NO_NUMERICO"
    if len(nuip) < NUIP_MIN_LEN:
        return False, "LONGITUD_INVALIDA"
    if len(nuip) > NUIP_MAX_LEN:
        return False, "LONGITUD_INVALIDA"
    return True, ""


def nuip_to_api_int(nuip: str) -> int:
    """Convert NUIP string to API integer (leading zeros are lost)."""
    return int(nuip)


# ---------------------------------------------------------------------------
# Input parsing
# ---------------------------------------------------------------------------
def _detect_csv_column(header: list[str]) -> int:
    for idx, col in enumerate(header):
        if col.strip().lower() in NUIP_COLUMN_NAMES:
            return idx
    return 0


def parse_input(path: Path) -> tuple[list[NuipRecord], list[ConsultResult]]:
    if not path.exists():
        raise FileNotFoundError(f"Archivo no encontrado: {path}")

    suffix = path.suffix.lower()
    valid_queue: list[NuipRecord] = []
    invalid_results: list[ConsultResult] = []
    seen: set[str] = set()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if suffix == ".csv":
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            rows = list(reader)
        if not rows:
            return [], []
        col_idx = _detect_csv_column(rows[0]) if rows[0] else 0
        start_line = 1 if rows[0] and any(
            c.strip().lower() in NUIP_COLUMN_NAMES for c in rows[0]
        ) else 0
        data_rows = rows[start_line:]
        for line_no, row in enumerate(data_rows, start=start_line + 1):
            if not row:
                continue
            token = row[col_idx] if col_idx < len(row) else row[0]
            _process_token(
                token, str(path), line_no, seen, valid_queue, invalid_results, now
            )
    else:
        with open(path, encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                token = stripped.split()[0] if stripped.split() else stripped
                _process_token(
                    token, str(path), line_no, seen, valid_queue, invalid_results, now
                )

    return valid_queue, invalid_results


def _process_token(
    token: str,
    archivo: str,
    linea: int,
    seen: set[str],
    valid_queue: list[NuipRecord],
    invalid_results: list[ConsultResult],
    now: str,
) -> None:
    nuip = normalize_nuip(token)
    ok, err_code = validate_nuip(nuip)
    if not ok:
        invalid_results.append(
            ConsultResult(
                nuip=nuip or token.strip(),
                clasificacion="INVALIDO",
                error=err_code,
                timestamp_consulta=now,
                archivo_origen=archivo,
                linea_origen=linea,
            )
        )
        return
    if nuip in seen:
        return
    seen.add(nuip)
    valid_queue.append(NuipRecord(nuip=nuip, archivo_origen=archivo, linea_origen=linea))


# ---------------------------------------------------------------------------
# Classification & HTTP
# ---------------------------------------------------------------------------
def classify_vigencia(
    http_status: int,
    parsed: dict[str, Any] | None,
    local_error: str = "",
) -> tuple[str, str]:
    if local_error:
        return "ERROR", local_error
    if http_status == 0:
        return "ERROR", "SIN_RESPUESTA"
    if http_status == 429:
        return "ERROR", "RATE_LIMITED"
    if http_status >= 500:
        return "ERROR", f"HTTP_{http_status}"
    if http_status >= 400:
        return "ERROR", f"HTTP_{http_status}"
    if not parsed:
        return "ERROR", "JSON_PARSE"
    vigencia = str(parsed.get("vigencia", "")).strip()
    if vigencia in VIGENCIA_MAP:
        return VIGENCIA_MAP[vigencia], ""
    if vigencia:
        return "DESCONOCIDO", "VIGENCIA_NO_MAPEADA"
    return "ERROR", "RESPUESTA_VACIA"


def _post_json(url: str, payload: dict[str, Any], timeout: int = HTTP_TIMEOUT) -> tuple[bytes, int]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Referer": API_REFERER,
            "User-Agent": USER_AGENT,
        },
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
        return resp.read(), resp.status


def consult_nuip(nuip: str, ip_value: str) -> tuple[dict[str, Any] | None, int, str]:
    try:
        payload = {"nuip": nuip_to_api_int(nuip), "ip": ip_value}
        raw, status = _post_json(API_URL, payload)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None, status, "JSON_PARSE"
        return parsed, status, ""
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:
            pass
        logger.warning("HTTP_%s en consulta a la API%s", exc.code, f": {body}" if body else "")
        return None, exc.code, f"HTTP_{exc.code}" + (f": {body}" if body else "")
    except urllib.error.URLError as exc:
        logger.warning(
            "Fallo de red/SSL: %s: %s", type(exc.reason).__name__, exc.reason
        )
        return None, 0, f"NETWORK: {exc.reason}"
    except TimeoutError:
        logger.warning("Timeout consultando la API (%ss)", HTTP_TIMEOUT)
        return None, 0, "TIMEOUT"
    except Exception as exc:
        logger.warning("Error inesperado consultando la API: %s: %s", type(exc).__name__, exc)
        return None, 0, f"ERROR: {exc}"


# ---------------------------------------------------------------------------
# SSL diagnostics
# ---------------------------------------------------------------------------
_AV_PROXY_KEYWORDS = (
    "kaspersky", "eset", "avast", "avg", "mcafee", "norton", "bitdefender",
    "fortinet", "fortigate", "zscaler", "netskope", "cisco umbrella",
    "sonicwall", "forcepoint", "websense", "sophos", "trendmicro",
    "trend micro", "gdata", "malwarebytes", "ssl inspection", "deep packet",
    "content filter", "proxy", "web filter", "surfshark", "nordvpn",
)


def _cert_cn(cert_dict: dict[str, Any], field: str) -> str:
    parts = cert_dict.get(field, ())
    for rdn in parts:
        for key, value in rdn:
            if key == "commonName":
                return value
    return ""


def diagnose_ssl(host: str, port: int) -> dict[str, Any]:
    """Connect without verifying and inspect the certificate actually received.

    Distinguishes a local TLS-interception proxy/antivirus (self-signed cert
    issued by a recognizable security-product CA) from a genuine server-side
    chain problem (missing intermediate, foreign root CA, etc.).
    """
    import socket

    result: dict[str, Any] = {
        "host": host,
        "port": port,
        "connected": False,
        "verify_error": "",
        "subject_cn": "",
        "issuer_cn": "",
        "self_signed": False,
        "likely_cause": "DESCONOCIDO",
    }

    try:
        default_ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=10) as sock:
            with default_ctx.wrap_socket(sock, server_hostname=host):
                result["connected"] = True
                result["verify_error"] = ""
    except ssl.SSLCertVerificationError as exc:
        result["verify_error"] = str(exc)
    except Exception as exc:
        result["verify_error"] = f"{type(exc).__name__}: {exc}"

    try:
        insecure_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        insecure_ctx.check_hostname = False
        insecure_ctx.verify_mode = ssl.CERT_NONE
        with socket.create_connection((host, port), timeout=10) as sock:
            with insecure_ctx.wrap_socket(sock, server_hostname=host) as tls_sock:
                der = tls_sock.getpeercert(binary_form=True)
        if der:
            pem = ssl.DER_cert_to_PEM_cert(der)
            try:
                cert_dict = ssl._ssl._test_decode_cert(_write_temp_pem(pem))  # type: ignore[attr-defined]
                result["subject_cn"] = _cert_cn(cert_dict, "subject")
                result["issuer_cn"] = _cert_cn(cert_dict, "issuer")
                result["self_signed"] = (
                    bool(result["subject_cn"])
                    and result["subject_cn"] == result["issuer_cn"]
                )
            except Exception as exc:
                logger.warning("No se pudo decodificar el certificado: %s", exc)
    except Exception as exc:
        logger.warning("Fallo obteniendo certificado sin verificar: %s: %s", type(exc).__name__, exc)

    issuer_lower = result["issuer_cn"].lower()
    if any(kw in issuer_lower for kw in _AV_PROXY_KEYWORDS):
        result["likely_cause"] = "INTERCEPTACION_LOCAL"
    elif result["self_signed"] or "self-signed" in result["verify_error"].lower():
        result["likely_cause"] = "CADENA_AUTOFIRMADA_DESCONOCIDA"
    elif "unable to get local issuer" in result["verify_error"].lower():
        result["likely_cause"] = "CADENA_INCOMPLETA_SERVIDOR"
    elif result["connected"]:
        result["likely_cause"] = "OK"

    logger.info(
        "Diagnostico SSL %s:%s — subject=%s issuer=%s self_signed=%s causa=%s error=%s",
        host, port, result["subject_cn"], result["issuer_cn"],
        result["self_signed"], result["likely_cause"], result["verify_error"],
    )
    return result


def _write_temp_pem(pem: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".pem")
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(pem)
    return path


# ---------------------------------------------------------------------------
# IP resolution
# ---------------------------------------------------------------------------
_ip_cache: str | None = None


def resolve_ip(config: BatchConfig) -> str:
    global _ip_cache
    mode = config.ip_mode.lower()
    if mode == "manual":
        return config.ip_value.strip()
    if mode == "auto":
        if _ip_cache is not None:
            return _ip_cache
        try:
            req = urllib.request.Request(IPIFY_URL, headers={"User-Agent": USER_AGENT})
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, context=ctx, timeout=5) as resp:
                data = json.loads(resp.read().decode())
            _ip_cache = str(data.get("ip", ""))
            return _ip_cache
        except Exception:
            return ""
    return ""


# ---------------------------------------------------------------------------
# Config persistence
# ---------------------------------------------------------------------------
def load_config() -> BatchConfig:
    path = SCRIPT_DIR / CONFIG_FILENAME
    if not path.exists():
        return BatchConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return BatchConfig(
            delay=float(data.get("delay", DEFAULT_DELAY)),
            workers=int(data.get("workers", DEFAULT_WORKERS)),
            ip_mode=str(data.get("ip_mode", "vacio")),
            ip_value=str(data.get("ip_value", "")),
            last_input_path=str(data.get("last_input_path", DEFAULT_INPUT)),
        )
    except (json.JSONDecodeError, ValueError, TypeError):
        print("  AVISO: configuracion corrupta — usando valores por defecto.")
        return BatchConfig()


def save_config(config: BatchConfig) -> None:
    path = SCRIPT_DIR / CONFIG_FILENAME
    data = {
        "delay": config.delay,
        "workers": config.workers,
        "ip_mode": config.ip_mode,
        "ip_value": config.ip_value,
        "last_input_path": config.last_input_path,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# CSV I/O
# ---------------------------------------------------------------------------
def write_csv(
    path: Path,
    rows: list[ConsultResult],
    *,
    mode: str = "a",
) -> None:
    write_header = mode == "w" or not path.exists() or path.stat().st_size == 0
    with open(path, mode, encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow(row.to_row())


def read_results_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def rebuild_fallecidos(resultados_path: Path, fallecidos_path: Path) -> int:
    rows = read_results_csv(resultados_path)
    fallecidos = [r for r in rows if r.get("clasificacion") == "FALLECIDO"]
    with open(fallecidos_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in fallecidos:
            writer.writerow({k: row.get(k, "") for k in CSV_COLUMNS})
    return len(fallecidos)


# ---------------------------------------------------------------------------
# Informe
# ---------------------------------------------------------------------------
def _pct(n: int, total: int) -> str:
    if total == 0:
        return "0.00%"
    return f"{100 * n / total:.2f}%"


def _count_by_class(rows: list[dict[str, str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        cls = row.get("clasificacion", "DESCONOCIDO")
        counts[cls] = counts.get(cls, 0) + 1
    return counts


def write_informe(
    config: BatchConfig,
    input_path: Path,
    *,
    interrupted: bool = False,
) -> Path:
    now = datetime.now()
    ts = now.strftime("%Y-%m-%d_%H-%M")
    report_path = reports_dir() / REPORT_FILENAME
    timestamped_path = reports_dir() / f"informe_{ts}.md"

    resultados_path = reports_dir() / RESULTADOS_FILENAME
    rows = read_results_csv(resultados_path)
    counts = _count_by_class(rows)
    total = len(rows)
    fallecidos = [r for r in rows if r.get("clasificacion") == "FALLECIDO"]
    invalidos = [r for r in rows if r.get("clasificacion") == "INVALIDO"]
    errores = [r for r in rows if r.get("clasificacion") == "ERROR"]

    now_str = now.strftime("%Y-%m-%d %H:%M")
    lines: list[str] = []

    lines.append("# Informe de Consulta de Vigencia — Registraduria")
    lines.append("")
    if interrupted:
        lines.append("> **CONSULTA INTERRUMPIDA** — resultados parciales guardados.")
        lines.append("")
    lines.append(
        "> **AVISO DE PRIVACIDAD:** Este informe puede contener numeros de "
        "identificacion personal (PII). Tratalo conforme a la Ley 1581 de 2012 "
        "(Habeas Data). No publiques ni compartas estos archivos sin autorizacion."
    )
    lines.append("")
    lines.append(
        "> **LIMITACION:** La API de vigencia no expone nombre del titular ni "
        "fecha de defuncion. Un resultado `FALLECIDO` indica cancelacion por muerte "
        "en el registro de la Registraduria; no constituye por si solo evidencia "
        "de fraude electoral."
    )
    lines.append("")
    lines.append(f"**Fecha:** {now_str}  ")
    lines.append(f"**Archivo de entrada:** {input_path}  ")
    lines.append(f"**Total registros:** {total:,}  ")
    lines.append(f"**Delay entre consultas:** {config.delay}s  ")
    lines.append(f"**Workers:** {config.workers}  ")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Resumen por clasificacion")
    lines.append("")
    lines.append("| Clasificacion | Cantidad | Porcentaje |")
    lines.append("|---------------|--------:|----------:|")
    order = ["VIVO", "FALLECIDO", "NO_EXISTE", "SIN_DATOS", "INVALIDO", "ERROR", "DESCONOCIDO"]
    for cls in order:
        n = counts.get(cls, 0)
        if n:
            lines.append(f"| {cls} | {n:,} | {_pct(n, total)} |")
    for cls, n in sorted(counts.items()):
        if cls not in order:
            lines.append(f"| {cls} | {n:,} | {_pct(n, total)} |")
    lines.append(f"| **Total** | **{total:,}** | **100%** |")
    lines.append("")

    lines.append(f"## FALLECIDO — Cancelada por Muerte ({len(fallecidos)})")
    lines.append("")
    if fallecidos:
        lines.append("| NUIP | Vigencia API | Fecha consulta |")
        lines.append("|------|-------------|----------------|")
        for r in fallecidos[:500]:
            lines.append(
                f"| {r.get('nuip', '')} | {r.get('vigencia_raw', '')} | "
                f"{r.get('fecha_consulta', '')} |"
            )
        if len(fallecidos) > 500:
            lines.append(f"| ... | ({len(fallecidos) - 500} mas en fallecidos.csv) | |")
    else:
        lines.append("Ninguno.")
    lines.append("")

    lines.append(f"## INVALIDO — formato rechazado ({len(invalidos)})")
    lines.append("")
    if invalidos:
        lines.append("| NUIP | Error | Linea |")
        lines.append("|------|-------|------:|")
        for r in invalidos[:50]:
            lines.append(
                f"| {r.get('nuip', '')} | {r.get('error', '')} | "
                f"{r.get('linea_origen', '')} |"
            )
        if len(invalidos) > 50:
            lines.append(f"| ... | ({len(invalidos) - 50} mas) | |")
    else:
        lines.append("Ninguno.")
    lines.append("")

    lines.append(f"## ERROR — fallo de consulta ({len(errores)})")
    lines.append("")
    if errores:
        lines.append("| NUIP | Error | HTTP |")
        lines.append("|------|-------|-----:|")
        for r in errores[:50]:
            lines.append(
                f"| {r.get('nuip', '')} | {r.get('error', '')} | "
                f"{r.get('http_status', '')} |"
            )
        if len(errores) > 50:
            lines.append(f"| ... | ({len(errores) - 50} mas) | |")
    else:
        lines.append("Ninguno.")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"*Generado por consulta-defunciones-cc v{VERSION} — Proyecto Analizador Electoral*")

    content = "\n".join(lines) + "\n"
    timestamped_path.write_text(content, encoding="utf-8-sig")
    report_path.write_text(content, encoding="utf-8-sig")
    return timestamped_path


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def load_checkpoint(input_path: Path) -> dict[str, Any] | None:
    path = reports_dir() / CHECKPOINT_FILENAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        stored_sha = data.get("input_sha256", "")
        current_sha = sha256_file(input_path)
        if stored_sha and stored_sha != current_sha:
            print(
                "  AVISO: el archivo de entrada cambio desde el ultimo checkpoint.\n"
                "  No se puede reanudar automaticamente. Inicia una consulta nueva."
            )
            return None
        return data
    except (json.JSONDecodeError, OSError):
        print("  AVISO: checkpoint corrupto — iniciando consulta nueva.")
        return None


def save_checkpoint(
    input_path: Path,
    ok: set[str],
    err: set[str],
    invalid: set[str],
    total: int,
) -> None:
    dir_path = reports_dir()
    path = dir_path / CHECKPOINT_FILENAME
    data = {
        "version": 1,
        "input_path": str(input_path),
        "input_sha256": sha256_file(input_path),
        "total": total,
        "ok": sorted(ok),
        "err": sorted(err),
        "invalid": sorted(invalid),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    fd, tmp = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def delete_checkpoint() -> None:
    path = reports_dir() / CHECKPOINT_FILENAME
    if path.exists():
        path.unlink()


def checkpoint_exists() -> bool:
    return (reports_dir() / CHECKPOINT_FILENAME).exists()


# ---------------------------------------------------------------------------
# Single consult helper
# ---------------------------------------------------------------------------
def _make_result(
    record: NuipRecord,
    parsed: dict[str, Any] | None,
    http_status: int,
    transport_error: str,
    ip_value: str,
) -> ConsultResult:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    clasificacion, extra_error = classify_vigencia(http_status, parsed, transport_error)
    vigencia_raw = str(parsed.get("vigencia", "")) if parsed else ""
    codigo = str(parsed.get("codigo", "")) if parsed else ""
    fecha = str(parsed.get("fecha", "")) if parsed else ""
    error = extra_error or transport_error
    return ConsultResult(
        nuip=record.nuip,
        clasificacion=clasificacion,
        vigencia_raw=vigencia_raw,
        codigo=codigo,
        fecha_consulta=fecha,
        http_status=http_status,
        error=error,
        timestamp_consulta=now,
        archivo_origen=record.archivo_origen,
        linea_origen=record.linea_origen,
    )


def consult_one(
    record: NuipRecord,
    config: BatchConfig,
    limiter: RateLimiter,
) -> ConsultResult:
    ip_value = resolve_ip(config)
    limiter.wait_before_request()
    parsed, http_status, transport_error = consult_nuip(record.nuip, ip_value)

    if http_status == 429:
        limiter.trigger_immediate_cooldown()
        return _make_result(record, parsed, http_status, "RATE_LIMITED", ip_value)

    is_transport_fail = bool(transport_error) or http_status == 0 or http_status >= 500
    if is_transport_fail:
        limiter.record_transport_failure()
    else:
        limiter.record_success()

    return _make_result(record, parsed, http_status, transport_error, ip_value)


# ---------------------------------------------------------------------------
# Batch orchestration
# ---------------------------------------------------------------------------
def run_batch(
    input_path: Path,
    config: BatchConfig,
    *,
    resume: bool = False,
) -> None:
    global _interrupted
    _interrupted = False

    input_path = input_path if input_path.is_absolute() else SCRIPT_DIR / input_path
    if not input_path.exists():
        print(f"\n  ERROR: Archivo no encontrado: {input_path}")
        return

    config.last_input_path = str(input_path.name if input_path.parent == SCRIPT_DIR else input_path)
    save_config(config)

    valid_queue, invalid_results = parse_input(input_path)
    resultados_path = reports_dir() / RESULTADOS_FILENAME
    fallecidos_path = reports_dir() / FALLECIDOS_FILENAME

    ok_set: set[str] = set()
    err_set: set[str] = set()
    invalid_set: set[str] = {r.nuip for r in invalid_results if r.nuip}

    skip_set: set[str] = set()
    truncate = True

    if resume:
        ckpt = load_checkpoint(input_path)
        if ckpt:
            ok_set = set(ckpt.get("ok", []))
            err_set = set(ckpt.get("err", []))
            invalid_set = set(ckpt.get("invalid", []))
            skip_set = ok_set | invalid_set
            truncate = False
            print(f"  Reanudando: {len(skip_set)} ya procesados, {len(err_set)} a reintentar.")
        else:
            resume = False

    if truncate:
        if resultados_path.exists():
            resultados_path.unlink()
        if fallecidos_path.exists():
            fallecidos_path.unlink()
        delete_checkpoint()

    if invalid_results and truncate:
        write_csv(resultados_path, invalid_results, mode="w")
    elif invalid_results and not truncate:
        write_csv(resultados_path, invalid_results, mode="a")

    pending = [r for r in valid_queue if r.nuip not in skip_set]
    total_valid = len(valid_queue)

    if not pending and not invalid_results:
        print("\n  No hay cedulas validas para consultar.")
        return

    print(f"\n  Cedulas validas: {total_valid:,}  |  Pendientes: {len(pending):,}")
    if invalid_results:
        print(f"  Invalidas (sin consulta API): {len(invalid_results):,}")

    limiter = RateLimiter(
        delay=config.delay,
        cooldown=DEFAULT_COOLDOWN,
        fail_threshold=DEFAULT_FAIL_THRESHOLD,
    )

    api_calls = 0

    def process_record(record: NuipRecord) -> ConsultResult:
        return consult_one(record, config, limiter)

    if config.workers <= 1:
        iterator = tqdm(pending, desc="  Consultando", unit="NUIP")
        for record in iterator:
            if _interrupted:
                break
            result = process_record(record)
            write_csv(resultados_path, [result], mode="a")
            api_calls += 1
            if result.clasificacion == "ERROR":
                err_set.add(record.nuip)
                ok_set.discard(record.nuip)
            else:
                ok_set.add(record.nuip)
                err_set.discard(record.nuip)
            if api_calls % CHECKPOINT_EVERY == 0:
                save_checkpoint(input_path, ok_set, err_set, invalid_set, total_valid)
    else:
        with ThreadPoolExecutor(max_workers=config.workers) as pool:
            futures = {pool.submit(process_record, r): r for r in pending}
            with tqdm(total=len(pending), desc="  Consultando", unit="NUIP") as bar:
                for future in as_completed(futures):
                    if _interrupted:
                        break
                    record = futures[future]
                    try:
                        result = future.result()
                    except Exception as exc:
                        result = ConsultResult(
                            nuip=record.nuip,
                            clasificacion="ERROR",
                            error=f"WORKER: {exc}",
                            timestamp_consulta=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            archivo_origen=record.archivo_origen,
                            linea_origen=record.linea_origen,
                        )
                    write_csv(resultados_path, [result], mode="a")
                    api_calls += 1
                    if result.clasificacion == "ERROR":
                        err_set.add(record.nuip)
                    else:
                        ok_set.add(record.nuip)
                        err_set.discard(record.nuip)
                    bar.update(1)
                    if api_calls % CHECKPOINT_EVERY == 0:
                        save_checkpoint(input_path, ok_set, err_set, invalid_set, total_valid)

    n_fallecidos = rebuild_fallecidos(resultados_path, fallecidos_path)

    if _interrupted or err_set:
        save_checkpoint(input_path, ok_set, err_set, invalid_set, total_valid)
        report = write_informe(config, input_path, interrupted=_interrupted)
        print(f"\n  Progreso guardado. Fallecidos: {n_fallecidos:,}")
        print(f"  Informe: {report}")
    else:
        delete_checkpoint()
        report = write_informe(config, input_path, interrupted=False)
        print(f"\n  Consulta completada. Fallecidos: {n_fallecidos:,}")
        print(f"  resultados.csv — {resultados_path.name}")
        print(f"  fallecidos.csv  — {fallecidos_path.name}")
        print(f"  Informe: {report}")


# ---------------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------------
def show_logo() -> None:
    print(f"{_COLOR}{LOGO}{_RESET}")
    print("  Consulta Defunciones CC  |  Auditoria ciudadana Colombia 2026")
    print("  API Vigencia Cedula       |  Codigo abierto")
    print()


def _pause() -> None:
    input("\n  Presiona Enter para continuar...")


def _resolve_input_path(prompt: str, default: str) -> Path | None:
    raw = input(prompt).strip().strip('"')
    if not raw:
        raw = default
    path = Path(raw)
    if not path.is_absolute():
        path = SCRIPT_DIR / path
    if not path.exists():
        print(f"\n  ERROR: Archivo no encontrado: {path}")
        return None
    return path


def submenu_lote(config: BatchConfig) -> None:
    print("  ┌──────────────────────────────────────────────────┐")
    print("  │  CONSULTA EN LOTE                                 │")
    print("  ├──────────────────────────────────────────────────┤")
    print(f"  │  [1] {DEFAULT_INPUT:<44} │")
    print("  │  [2] Otro archivo (.txt / .csv)                  │")
    print("  │  [0] Volver                                         │")
    print("  └──────────────────────────────────────────────────┘")
    print()
    sub = input("  Selecciona una opcion: ").strip()
    if sub == "0":
        return
    if sub == "1":
        path = SCRIPT_DIR / DEFAULT_INPUT
        if not path.exists():
            print(f"\n  Coloca tu lista como '{DEFAULT_INPUT}' o usa la opcion [2].")
            _pause()
            return
    elif sub == "2":
        path = _resolve_input_path("  Ruta del archivo: ", DEFAULT_INPUT)
        if path is None:
            _pause()
            return
    else:
        print("  Opcion no valida.")
        return

    resume = False
    if checkpoint_exists():
        print()
        print("  Hay una consulta anterior sin completar.")
        ans = input("  [R] Reanudar  [N] Nueva consulta  [C] Cancelar: ").strip().upper()
        if ans == "C":
            return
        if ans == "R":
            resume = True
        elif ans != "N":
            print("  Opcion no valida.")
            return

    if not resume:
        confirm = input("\n  Iniciar consulta? [S/n]: ").strip().upper()
        if confirm == "N":
            return

    run_batch(path, config, resume=resume)
    _pause()


def submenu_individual(config: BatchConfig) -> None:
    raw = input("  Numero de cedula (NUIP): ").strip()
    nuip = normalize_nuip(raw)
    ok, err_code = validate_nuip(nuip)
    if not ok:
        print(f"\n  Cedula invalida: {err_code}")
        _pause()
        return

    print(f"\n  Consultando {nuip}...")
    record = NuipRecord(nuip=nuip)
    limiter = RateLimiter(delay=config.delay)
    result = consult_one(record, config, limiter)

    print()
    print(f"  NUIP:          {result.nuip}")
    print(f"  Clasificacion: {result.clasificacion}")
    print(f"  Vigencia API:  {result.vigencia_raw or '(vacio)'}")
    print(f"  Codigo:        {result.codigo}")
    print(f"  Fecha:         {result.fecha_consulta}")
    if result.error:
        print(f"  Error:         {result.error}")
    _pause()


def submenu_resume(config: BatchConfig) -> None:
    if not checkpoint_exists():
        print("\n  No hay consulta pendiente para reanudar.")
        _pause()
        return
    try:
        data = json.loads((reports_dir() / CHECKPOINT_FILENAME).read_text(encoding="utf-8"))
        input_path = Path(data.get("input_path", DEFAULT_INPUT))
        if not input_path.is_absolute():
            input_path = SCRIPT_DIR / input_path
    except (json.JSONDecodeError, OSError):
        print("\n  Checkpoint corrupto.")
        _pause()
        return

    if not input_path.exists():
        print(f"\n  Archivo original no encontrado: {input_path}")
        _pause()
        return

    print(f"\n  Reanudando consulta: {input_path.name}")
    run_batch(input_path, config, resume=True)
    _pause()


def submenu_config(config: BatchConfig) -> None:
    print("  ┌──────────────────────────────────────────────────┐")
    print("  │  CONFIGURACION                                    │")
    print("  ├──────────────────────────────────────────────────┤")
    print(f"  │  Delay actual:     {config.delay}s{' ' * 35}"[:52] + "│")
    print(f"  │  Workers actual:  {config.workers}{' ' * 35}"[:52] + "│")
    print(f"  │  Modo IP:         {config.ip_mode}{' ' * 35}"[:52] + "│")
    print("  ├──────────────────────────────────────────────────┤")
    print("  │  [1] Cambiar delay (segundos entre consultas)    │")
    print("  │  [2] Cambiar workers (1-4)                       │")
    print("  │  [3] Modo IP (vacio / auto / manual)             │")
    print("  │  [0] Volver                                         │")
    print("  └──────────────────────────────────────────────────┘")
    print()
    sub = input("  Selecciona una opcion: ").strip()
    if sub == "1":
        raw = input(f"  Delay en segundos [{config.delay}]: ").strip()
        if raw:
            try:
                config.delay = max(0.0, float(raw))
            except ValueError:
                print("  Valor invalido.")
                return
    elif sub == "2":
        raw = input(f"  Workers [1-4, actual {config.workers}]: ").strip()
        if raw:
            try:
                w = int(raw)
                if w < 1 or w > 4:
                    raise ValueError
                if w > 1:
                    print("  AVISO: workers > 1 puede provocar bloqueo del servidor.")
                config.workers = w
            except ValueError:
                print("  Valor invalido (usa 1-4).")
                return
    elif sub == "3":
        print("  [1] vacio  [2] auto (ipify)  [3] manual")
        mode = input("  Modo IP: ").strip()
        if mode == "1":
            config.ip_mode = "vacio"
        elif mode == "2":
            config.ip_mode = "auto"
        elif mode == "3":
            config.ip_mode = "manual"
            config.ip_value = input("  IP manual: ").strip()
        else:
            print("  Opcion no valida.")
            return
    elif sub == "0":
        return
    else:
        print("  Opcion no valida.")
        return

    save_config(config)
    print("  Configuracion guardada.")
    _pause()


def submenu_ver_informe() -> None:
    report = reports_dir() / REPORT_FILENAME
    if not report.exists():
        print("\n  No hay informe generado aun.")
        _pause()
        return
    print(f"\n  Ultimo informe: {report}")
    if sys.platform == "win32":
        try:
            os.startfile(report)  # type: ignore[attr-defined]
        except OSError:
            print("  No se pudo abrir automaticamente. Abre el archivo manualmente.")
    else:
        print(report.read_text(encoding="utf-8-sig")[:2000])
    _pause()


_CAUSE_MESSAGES = {
    "INTERCEPTACION_LOCAL": (
        "El certificado que recibe esta maquina esta firmado por un antivirus/proxy "
        "local (inspeccion de trafico HTTPS), no por la Registraduria. Hay que "
        "desactivar la inspeccion SSL/HTTPS de ese antivirus o proxy para este sitio, "
        "o agregar una excepcion para defunciones.registraduria.gov.co."
    ),
    "CADENA_AUTOFIRMADA_DESCONOCIDA": (
        "El servidor (o algo en el camino) esta presentando un certificado "
        "autofirmado que no coincide con antivirus conocidos. Puede ser un proxy "
        "corporativo no identificado, o un problema de configuracion del servidor."
    ),
    "CADENA_INCOMPLETA_SERVIDOR": (
        "El servidor no esta enviando la cadena completa de certificados "
        "(falta la CA intermedia). Es un problema del lado de la Registraduria, "
        "no de esta maquina."
    ),
    "OK": "La verificacion SSL funciona correctamente en esta maquina.",
    "DESCONOCIDO": "No se pudo determinar la causa exacta. Revisa el log completo.",
}


def _print_diag_ssl_report() -> None:
    parsed = urllib.parse.urlsplit(API_URL)
    host = parsed.hostname or ""
    port = parsed.port or 443
    print(f"\n  Conectando a {host}:{port} para inspeccionar el certificado...")
    result = diagnose_ssl(host, port)

    print()
    print(f"  Conexion verificada (SSL valido): {'si' if result['connected'] else 'no'}")
    if result["verify_error"]:
        print(f"  Error de verificacion: {result['verify_error']}")
    print(f"  Certificado recibido — subject: {result['subject_cn'] or '(desconocido)'}")
    print(f"  Certificado recibido — issuer:  {result['issuer_cn'] or '(desconocido)'}")
    print(f"  Autofirmado: {'si' if result['self_signed'] else 'no'}")
    print()
    print(f"  DIAGNOSTICO: {result['likely_cause']}")
    print(f"  {_CAUSE_MESSAGES.get(result['likely_cause'], '')}")
    print()
    print("  Detalle guardado en reportes/consulta.log — comparte ese archivo si necesitas ayuda.")


def submenu_diag_ssl() -> None:
    _print_diag_ssl_report()
    _pause()


def menu_loop(config: BatchConfig) -> None:
    show_logo()
    while True:
        ckpt = "si" if checkpoint_exists() else "no"
        print("  ┌──────────────────────────────────────────────────┐")
        print("  │  MENU PRINCIPAL                                   │")
        print("  ├──────────────────────────────────────────────────┤")
        print("  │  [1] Consulta en lote (archivo)                  │")
        print("  │  [2] Consulta individual (una cedula)            │")
        print(f"  │  [3] Reanudar consulta pendiente ({ckpt}){' ' * 10}"[:52] + "│")
        print("  │  [4] Configuracion                               │")
        print("  │  [5] Ver ultimo informe                          │")
        print("  │  [6] Diagnostico SSL (probar conexion)           │")
        print("  │  [0] Salir                                        │")
        print("  └──────────────────────────────────────────────────┘")
        print()

        opcion = input("  Selecciona una opcion: ").strip()
        print()

        if opcion == "0":
            break
        elif opcion == "1":
            submenu_lote(config)
            show_logo()
        elif opcion == "2":
            submenu_individual(config)
            show_logo()
        elif opcion == "3":
            submenu_resume(config)
            show_logo()
        elif opcion == "4":
            submenu_config(config)
            show_logo()
        elif opcion == "5":
            submenu_ver_informe()
            show_logo()
        elif opcion == "6":
            submenu_diag_ssl()
            show_logo()
        else:
            print("  Opcion no valida.\n")


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------
def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Consulta en lote el estado de vigencia de cedulas colombianas."
    )
    parser.add_argument("--file", "-f", help="Archivo de entrada (.txt o .csv)")
    parser.add_argument("--delay", type=float, default=None, help="Segundos entre consultas")
    parser.add_argument("--workers", type=int, default=None, help="Hilos paralelos (1-4)")
    parser.add_argument("--ip", default=None, help="IP a enviar en el JSON (vacio si omitido)")
    parser.add_argument("--resume", action="store_true", help="Reanudar desde checkpoint")
    parser.add_argument("--no-menu", action="store_true", help="Omitir menu interactivo")
    parser.add_argument(
        "--diag-ssl", action="store_true",
        help="Probar la conexion SSL contra la API y mostrar el certificado recibido",
    )
    return parser


def main() -> None:
    setup_logging()
    config = load_config()
    parser = build_argparser()
    args = parser.parse_args()

    if args.diag_ssl:
        _print_diag_ssl_report()
        return

    run_flags = any([
        args.file,
        args.delay is not None,
        args.workers is not None,
        args.ip is not None,
        args.resume,
        args.no_menu,
    ])

    if args.delay is not None:
        config.delay = max(0.0, args.delay)
    if args.workers is not None:
        config.workers = max(1, min(4, args.workers))
    if args.ip is not None:
        config.ip_mode = "manual"
        config.ip_value = args.ip

    if run_flags:
        if not args.file:
            print("ERROR: --file es requerido en modo CLI.", file=sys.stderr)
            sys.exit(1)
        path = Path(args.file)
        if not path.is_absolute():
            path = SCRIPT_DIR / path
        if not path.exists():
            print(f"ERROR: Archivo no encontrado: {path}", file=sys.stderr)
            sys.exit(1)
        run_batch(path, config, resume=args.resume)
        return

    menu_loop(config)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise
    except Exception:
        logger.error("Fallo no controlado:\n%s", traceback.format_exc())
        log_path = reports_dir() / LOG_FILENAME
        print(
            f"\n  ERROR INESPERADO. Se guardo el detalle en: {log_path}\n"
            "  Comparti ese archivo para poder diagnosticar el problema.",
            file=sys.stderr,
        )
        sys.exit(1)