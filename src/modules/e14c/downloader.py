"""
E-14C PDF Downloader

Downloads PDFs from e14c_urls.jsonl into a directory tree that mirrors
the electoral hierarchy:

  data/pdfs/{DEPARTAMENTO}/{MUNICIPIO}/zona_{zona}/puesto_{puesto}/{filename}.pdf

Features:
  - Resumable: skips files that already exist and have correct size
  - Department filter: process one dept at a time
  - Concurrent downloads with semaphore
  - Failed URLs saved to data/e14c_failed.jsonl for retry
  - Rich progress bar
"""
import asyncio
import json
import re
import ssl
import urllib.request
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from src.utils.logger import get_logger

logger = get_logger(__name__)
console = Console()

BASE       = "https://escrutiniospresidente2026.registraduria.gov.co"
URLS_FILE  = Path("data/e14c_urls.jsonl")
PDFS_DIR   = Path("data/pdfs")
FAILED_FILE = Path("data/e14c_failed.jsonl")

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*]')


def _safe_name(name: str) -> str:
    """Sanitize a string for use as a directory/file name."""
    return _INVALID_CHARS.sub("_", name.strip())


def _dest_path(record: dict) -> Path:
    """
    Build the destination path for a PDF record.

    Structure: data/pdfs/{DEPT}/{MPIO}/zona_{zona}/puesto_{puesto}/{filename}
    """
    dept  = _safe_name(record["departamento"])
    mpio  = _safe_name(record["municipio"])
    zona  = record["zona"].zfill(2) if record["zona"].isdigit() else _safe_name(record["zona"])
    puesto = record["cod_puesto"].zfill(2)
    filename = record["pdf_url"].split("/")[-1]

    return PDFS_DIR / dept / mpio / f"zona_{zona}" / f"puesto_{puesto}" / filename


def _already_downloaded(path: Path) -> bool:
    """Return True if the file exists and is non-empty."""
    return path.exists() and path.stat().st_size > 0


def _download_sync(url: str, dest: Path) -> tuple[bool, str]:
    """Download url to dest. Returns (success, error_message)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=_ssl_ctx, timeout=30) as resp:
            data = resp.read()
        if not data.startswith(b"%PDF"):
            return False, "Not a valid PDF"
        tmp.write_bytes(data)
        tmp.replace(dest)
        return True, ""
    except Exception as e:
        tmp.unlink(missing_ok=True)
        return False, str(e)


async def _download_one(
    record: dict,
    semaphore: asyncio.Semaphore,
    progress,
    task_id,
    stats: dict,
    failed_lock: asyncio.Lock,
) -> None:
    dest = _dest_path(record)

    if _already_downloaded(dest):
        stats["skipped"] += 1
        progress.advance(task_id)
        return

    async with semaphore:
        url = record["pdf_url"]
        ok, err = await asyncio.to_thread(_download_sync, url, dest)

    if ok:
        stats["downloaded"] += 1
    else:
        stats["errors"] += 1
        logger.debug(f"FAIL {url}: {err}")
        async with failed_lock:
            with open(FAILED_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps({**record, "error": err}, ensure_ascii=False) + "\n")

    progress.advance(task_id)


async def download_pdfs(
    departamento: Optional[str] = None,
    max_concurrent: int = 10,
    output_dir: Optional[Path] = None,
) -> None:
    """
    Download E-14C PDFs from e14c_urls.jsonl.

    Args:
        departamento: If set, only download this department (case-insensitive).
        max_concurrent: Simultaneous downloads.
        output_dir: Override default data/pdfs directory.
    """
    global PDFS_DIR
    if output_dir:
        PDFS_DIR = output_dir

    if not URLS_FILE.exists():
        logger.error(f"{URLS_FILE} not found. Run 'collect-e14c' first.")
        return

    # Load and filter records
    all_records = [
        json.loads(l)
        for l in URLS_FILE.read_text(encoding="utf-8").strip().split("\n")
        if l.strip()
    ]

    if departamento:
        records = [
            r for r in all_records
            if r["departamento"].upper() == departamento.upper()
        ]
        if not records:
            # Try partial match
            records = [
                r for r in all_records
                if departamento.upper() in r["departamento"].upper()
            ]
        if not records:
            available = sorted({r["departamento"] for r in all_records})
            logger.error(f"Department '{departamento}' not found. Available: {available}")
            return
    else:
        records = all_records

    total      = len(records)
    to_download = sum(1 for r in records if not _already_downloaded(_dest_path(r)))
    already    = total - to_download

    dept_label = departamento or "TODOS LOS DEPARTAMENTOS"
    console.print(f"\n[bold cyan]Descargando E-14C — {dept_label}[/bold cyan]")
    console.print(f"  Total PDFs  : {total:,}")
    console.print(f"  Ya descarg. : {already:,}")
    console.print(f"  A descargar : {to_download:,}")
    console.print(f"  Concurrencia: {max_concurrent}")
    console.print(f"  Destino     : {PDFS_DIR / _safe_name(departamento or '')}\n")

    stats = {"downloaded": 0, "skipped": 0, "errors": 0}
    semaphore = asyncio.Semaphore(max_concurrent)
    failed_lock = asyncio.Lock()

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task(dept_label, total=total)

        tasks = [
            _download_one(r, semaphore, progress, task_id, stats, failed_lock)
            for r in records
        ]

        # Process in batches to keep memory low
        BATCH = 200
        for i in range(0, len(tasks), BATCH):
            await asyncio.gather(*tasks[i : i + BATCH])

    console.print(f"\n[bold green]Descarga completada[/bold green]")
    console.print(f"  Descargados : {stats['downloaded']:,}")
    console.print(f"  Saltados    : {stats['skipped']:,}")
    console.print(f"  Errores     : {stats['errors']:,}")
    if stats["errors"]:
        console.print(f"  Fallidos    : {FAILED_FILE}")
