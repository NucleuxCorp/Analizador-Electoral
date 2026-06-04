"""
E-14C (Actas Oficiales de Escrutinio) URL Collector — pure HTTP, no browser.

Strategy:
  1. GET /data/index.json  →  maps every {path}/mesas/ to its current filename
  2. For each path in the index, GET the mesas JSON concurrently
  3. For every mesa with digitalizado=1 and nombre_archivo set, construct PDF URL
  4. Enrich with dept/mpio names from divipole + departamentos.json
  5. Append to e14c_urls.jsonl

PDF URL pattern (from reverse engineering):
  BASE + mesa['nombre_archivo']
  e.g. https://...registraduria.gov.co/docs/E14/05/001/01/01/E14_PRE_05_001_001_01_01_001_5396.pdf
"""
import asyncio
import json
import os
import ssl
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)

BASE = "https://escrutiniospresidente2026.registraduria.gov.co"
INDEX_PATH = "/data/index.json"
DIVIPOLE_FILE = Path("data/divipole.json")
DEPTS_FILE = Path("data/departamentos.json")
OUTPUT_FILE = Path("data/e14c_urls.jsonl")
PROGRESS_FILE = Path("data/e14c_progress.json")

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE


# ---------------------------------------------------------------------------
# HTTP helpers (urllib wrapped in asyncio.to_thread for concurrency)
# ---------------------------------------------------------------------------

def _fetch_sync(url: str, timeout: int = 15) -> Optional[bytes]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=_ssl_ctx, timeout=timeout) as r:
            body = r.read()
            # Skip HTML fallback pages (Angular SPA 404)
            if body.lstrip()[:5] in (b"<!doc", b"<html"):
                return None
            return body
    except Exception as e:
        logger.debug(f"Fetch error {url}: {e}")
        return None


async def _fetch_json(url: str) -> Optional[list | dict]:
    body = await asyncio.to_thread(_fetch_sync, url)
    if body:
        try:
            return json.loads(body)
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# Name lookup helpers
# ---------------------------------------------------------------------------

def _load_dept_names() -> dict[str, str]:
    """Map dept_id → dept_name from departamentos.json."""
    if not DEPTS_FILE.exists():
        return {}
    raw = json.loads(DEPTS_FILE.read_text(encoding="utf-8"))
    return {d["id"]: d["nombre"] for d in raw}


def _load_divipole() -> dict:
    if not DIVIPOLE_FILE.exists():
        return {}
    return json.loads(DIVIPOLE_FILE.read_text(encoding="utf-8"))


def _mpio_name(divipole: dict, dept: str, mpio: str) -> str:
    try:
        return divipole["departamentos"][dept]["municipios"][mpio]["nombre"]
    except (KeyError, TypeError):
        return mpio


def _puesto_name(divipole: dict, dept: str, mpio: str, zona: str, puesto: str) -> str:
    try:
        return divipole["departamentos"][dept]["municipios"][mpio]["zonas"][zona]["puestos"][puesto]["l"]
    except (KeyError, TypeError):
        return puesto


# ---------------------------------------------------------------------------
# Progress tracking (lightweight JSON file — no SQLite overhead needed here)
# ---------------------------------------------------------------------------

def _load_progress() -> set[str]:
    if PROGRESS_FILE.exists():
        return set(json.loads(PROGRESS_FILE.read_text()))
    return set()


def _save_progress(done: set[str]) -> None:
    PROGRESS_FILE.write_text(json.dumps(list(done)))


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

_write_lock = asyncio.Lock()


async def _append_records(records: list[dict]) -> None:
    if not records:
        return
    lines = "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n"
    async with _write_lock:
        with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
            f.write(lines)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def _parse_path(path: str) -> tuple[str, str, str, str]:
    """
    Extract (dept, mpio, zona, puesto) from path like:
    'data/esc/v1/actas-documentos/001/{dept}/{mpio}/{zona}/{puesto}/mesas/'
    """
    parts = path.strip("/").split("/")
    return parts[5], parts[6], parts[7], parts[8]


async def _process_puesto(
    path: str,
    filename: str,
    dept_names: dict,
    divipole: dict,
    semaphore: asyncio.Semaphore,
    done: set[str],
    stats: dict,
) -> None:
    key = path
    if key in done:
        stats["skipped"] += 1
        return

    async with semaphore:
        url = f"{BASE}/{path}{filename}"
        mesas = await _fetch_json(url)

    if not isinstance(mesas, list):
        return

    dept, mpio, zona, puesto = _parse_path(path)
    dept_name  = dept_names.get(dept, dept)
    mpio_name  = _mpio_name(divipole, dept, mpio)
    puesto_name = _puesto_name(divipole, dept, mpio, zona, puesto)

    records: list[dict] = []
    for mesa in mesas:
        nombre = mesa.get("nombre_archivo", "")
        digital = mesa.get("digitalizado", 0)
        if digital > 0 and nombre:
            records.append({
                "departamento":  dept_name,
                "cod_dept":      dept,
                "municipio":     mpio_name,
                "cod_mpio":      mpio,
                "zona":          zona,
                "puesto":        puesto_name,
                "cod_puesto":    puesto,
                "mesa":          mesa["numero"],
                "escrutado":     mesa.get("escrutado", False),
                "digitalizado":  digital,
                "id_mesa":       mesa.get("id_informacion_mesa_corporacion", "").strip(),
                "pdf_url":       BASE + nombre,
                "scraped_at":    datetime.now(timezone.utc).isoformat(),
            })

    if records:
        await _append_records(records)
        stats["urls"] += len(records)

    stats["processed"] += 1
    done.add(key)

    total_mesas = len(mesas)
    pending = total_mesas - len(records)
    if records:
        logger.info(f"  [dept={dept} mpio={mpio} z={zona} p={puesto}] "
                    f"{len(records)}/{total_mesas} PDFs | {mpio_name}")
    else:
        logger.debug(f"  [dept={dept} mpio={mpio} z={zona} p={puesto}] "
                     f"0/{total_mesas} — sin digitalizar aun")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def collect_e14c_urls(
    max_concurrent: int = 30,
    resume: bool = True,
) -> None:
    """
    Collect all E-14C PDF URLs from the official escrutinio platform.
    Runs concurrently using asyncio — no browser required.

    Args:
        max_concurrent: Max simultaneous HTTP requests.
        resume: Skip already-processed puestos (reads progress file).
    """
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Load helpers
    dept_names = _load_dept_names()
    divipole   = _load_divipole()
    done       = _load_progress() if resume else set()

    logger.info(f"Loading master index from {BASE}{INDEX_PATH} ...")
    index: dict = await _fetch_json(BASE + INDEX_PATH)
    if not index:
        logger.error("Failed to fetch index.json")
        return

    # Filter to mesas paths only
    mesa_entries = {k: v for k, v in index.items() if k.endswith("/mesas/")}
    logger.info(f"Total puestos in index : {len(mesa_entries)}")
    logger.info(f"Already processed      : {len(done)}")
    logger.info(f"Remaining              : {len(mesa_entries) - len(done)}")
    logger.info(f"Concurrency            : {max_concurrent} simultaneous requests")
    logger.info(f"Output                 : {OUTPUT_FILE}")

    semaphore = asyncio.Semaphore(max_concurrent)
    stats = {"processed": 0, "skipped": 0, "urls": 0}

    tasks = [
        _process_puesto(path, filename, dept_names, divipole, semaphore, done, stats)
        for path, filename in mesa_entries.items()
    ]

    # Process in batches to save progress periodically
    BATCH = 500
    for i in range(0, len(tasks), BATCH):
        batch = tasks[i: i + BATCH]
        await asyncio.gather(*batch)
        _save_progress(done)
        logger.info(
            f"Progress: {stats['processed']+stats['skipped']}/{len(mesa_entries)} puestos | "
            f"{stats['urls']} PDF URLs so far"
        )

    _save_progress(done)

    # Count actual lines in output
    total_lines = 0
    if OUTPUT_FILE.exists():
        total_lines = sum(1 for _ in OUTPUT_FILE.open(encoding="utf-8"))

    logger.info(f"\n{'='*60}")
    logger.info(f"E-14C COLLECTION COMPLETE")
    logger.info(f"Puestos procesados : {stats['processed']}")
    logger.info(f"URLs recolectadas  : {stats['urls']} (sesion)")
    logger.info(f"Total en archivo   : {total_lines}")
    logger.info(f"Output             : {OUTPUT_FILE}")
    logger.info(f"{'='*60}")
