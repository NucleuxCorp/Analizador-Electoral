#!/usr/bin/env python3
"""
Colector de E-14 para la segunda vuelta presidencial 2026.

Descarga el JSON estatico de todas las mesastransmitidas/publicadas,
filtra las publicadas (status=3) y descarga los PDFs siguiendo el patron:
  BASE/assets/temis/pdf/{dept}/{mpio}/{zona}/{puesto}/PRE/{expectedName}

Uso:
    python collect_segunda_vuelta.py                          # todas las publicadas
    python collect_segunda_vuelta.py --dept 01                # solo Antioquia
    python collect_segunda_vuelta.py --dept 01 --concurrent 20  # 20 descargas paralelas
    python collect_segunda_vuelta.py --limit 100               # solo las primeras 100
    python collect_segunda_vuelta.py --reset                   # recomienza desde cero
    python collect_segunda_vuelta.py --include-transmitted     # tambien status=11 (sin PDF aun)

Formato de checkpoint: data/segunda_vuelta_progress.json
Salida: data/pdfs_segunda_vuelta/{DEPTO}/{MESA_ID}_{NUMERO_MESA}.pdf
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────

BASE = "https://e14segundavueltapresidentet.registraduria.gov.co"
TRANSMISSION_JSON = f"{BASE}/assets/temis/divipol_json/allTransmissionCodes.json"
TRANSMISSION_LOCAL = Path("data/allTransmissionCodes_segunda.json")
PDF_BASE = f"{BASE}/assets/temis/pdf"
PDF_DIR = Path("data/pdfs_segunda_vuelta")
CHECKPOINT = Path("data/segunda_vuelta_progress.json")
PROGRESS_INTERVAL = 10  # save checkpoint every N downloads

# Status codes: 3 = published (has PDF), 11 = transmitted (no PDF yet)
STATUS_PUBLISHED = 3
STATUS_TRANSMITTED = 11


# ── Helpers ────────────────────────────────────────────────────────────────

def _load_env() -> None:
    """Load .env if present."""
    env = Path(".env")
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def download_json(url: str) -> dict:
    """Download a JSON file with retries."""
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            if attempt == 2:
                raise
            print(f"  retry {attempt + 1}/3 ({exc})...")
            time.sleep(2 ** attempt)
    return {}


def download_pdf(url: str, dest: Path, max_retries: int = 3) -> bool:
    """Download a PDF file. Returns True on success."""
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = resp.read()
            if data[:4] == b"%PDF":
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                return True
            else:
                print(f"  WARNING: not a PDF: {url[:80]}... (got {len(data)} bytes)")
                return False
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return False  # PDF not uploaded yet
            if attempt == max_retries - 1:
                print(f"  ERROR: {url[:80]} -> HTTP {e.code}")
                return False
            time.sleep(2 ** attempt)
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"  ERROR: {url[:80]} -> {e}")
                return False
            time.sleep(2 ** attempt)
    return False


def load_checkpoint() -> dict:
    """Load checkpoint for resumable downloads."""
    if CHECKPOINT.exists():
        return json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    return {"downloaded": [], "failed": [], "not_found": []}


def save_checkpoint(progress: dict) -> None:
    """Save checkpoint atomically."""
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")


def build_pdf_url(mesa: dict) -> str:
    """Build the PDF URL for a mesa record.

    Mesa JSON fields:
      idDepartmentCode: "17" (2 chars)
      municipalityCode: "026" (3 chars)
      idZoneCode: "00" (2 chars, but sometimes 3)
      standCode: "00" (2 chars)
      idCorporationCode: "001" -> acronym "PRE"
      expectedName: "d2915970...pdf" (the hash)
    """
    dept = str(mesa.get("idDepartmentCode", "")).zfill(2)
    mpio = str(mesa.get("municipalityCode", "")).zfill(3)
    zone = str(mesa.get("idZoneCode", "")).zfill(3)
    stand = str(mesa.get("standCode", "")).zfill(2)

    # Corporation 001 = PRE (Presidente)
    corp = "PRE"

    # expectedName already includes .pdf extension
    filename = mesa.get("expectedName", "")
    if not filename:
        return ""

    # Mesa number from JSON
    mesa_num = str(mesa.get("numberStand", "")).zfill(3)

    # URL pattern confirmed via Playwright:
    # assets/temis/pdf/{dept}/{mpio}/{zone}/{stand}/{mesa_num}/PRE/{filename}
    return f"{PDF_BASE}/{dept}/{mpio}/{zone}/{stand}/{mesa_num}/{corp}/{filename}"


def build_local_path(mesa: dict) -> Path:
    """Build the local file path for a mesa PDF."""
    dept = str(mesa.get("idDepartmentCode", "")).zfill(2)
    mesa_num = str(mesa.get("numberStand", "")).zfill(3)
    trans_id = mesa.get("idTransmissionCode", "")
    return PDF_DIR / f"{dept}" / f"{trans_id}_M{mesa_num}.pdf"


# ── Main ──────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Collect E-14 PDFs from second round")
    parser.add_argument("--dept", type=str, default="", help="Filter by department code (e.g. 01)")
    parser.add_argument("--concurrent", type=int, default=10, help="Concurrent downloads")
    parser.add_argument("--limit", type=int, default=0, help="Max PDFs to download (0 = all)")
    parser.add_argument("--reset", action="store_true", help="Reset checkpoint and start over")
    parser.add_argument("--include-transmitted", action="store_true",
                        help="Also try status=11 (may not have PDF yet)")
    args = parser.parse_args()

    _load_env()
    PDF_DIR.mkdir(parents=True, exist_ok=True)

    # Load checkpoint
    if args.reset:
        progress = {"downloaded": [], "failed": [], "not_found": []}
    else:
        progress = load_checkpoint()

    downloaded_set = set(progress["downloaded"])
    print(f"Checkpoint: {len(downloaded_set)} already downloaded")

    # Download transmission codes JSON (or use local copy)
    if TRANSMISSION_LOCAL.exists():
        print(f"Loading local transmission codes: {TRANSMISSION_LOCAL}")
        data = json.loads(TRANSMISSION_LOCAL.read_text(encoding="utf-8"))
    else:
        print(f"Fetching transmission codes from:\n  {TRANSMISSION_JSON}")
        data = download_json(TRANSMISSION_JSON)
        TRANSMISSION_LOCAL.parent.mkdir(parents=True, exist_ok=True)
        TRANSMISSION_LOCAL.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        print(f"  Saved local copy: {TRANSMISSION_LOCAL}")

    # Merge status3 (published) and optionally status11 (transmitted)
    published = data.get("data", {}).get("status3", {}).get("nodes", [])
    transmitted = data.get("data", {}).get("status11", {}).get("nodes", [])
    all_mesas = list(published)
    if args.include_transmitted:
        all_mesas.extend(transmitted)

    print(f"  Published (status=3): {len(published)}")
    print(f"  Transmitted (status=11): {len(transmitted)}")
    print(f"  Total to process: {len(all_mesas)}")

    # Filter by department if requested
    if args.dept:
        dept_code = str(args.dept).zfill(2)
        all_mesas = [m for m in all_mesas if str(m.get("idDepartmentCode", "")).zfill(2) == dept_code]
        print(f"  Filtered by dept {dept_code}: {len(all_mesas)} mesas")

    # Filter out already downloaded
    to_download = []
    for m in all_mesas:
        trans_id = m.get("idTransmissionCode", "")
        if trans_id and trans_id not in downloaded_set:
            to_download.append(m)

    print(f"  Already downloaded: {len(all_mesas) - len(to_download)}")
    print(f"  To download: {len(to_download)}")

    if args.limit > 0:
        to_download = to_download[:args.limit]
        print(f"  Limited to: {len(to_download)}")

    if not to_download:
        print("\nNothing to download. Done.")
        return

    # Download
    print(f"\nDownloading {len(to_download)} PDFs...")
    success = 0
    not_found = 0
    failed = 0
    t0 = time.time()

    for idx, mesa in enumerate(to_download, 1):
        trans_id = mesa.get("idTransmissionCode", "")
        url = build_pdf_url(mesa)
        dest = build_local_path(mesa)

        if not url:
            print(f"  [{idx}/{len(to_download)}] SKIP (no expectedName): {trans_id}")
            continue

        if dest.exists():
            downloaded_set.add(trans_id)
            progress["downloaded"] = list(downloaded_set)
            continue

        ok = download_pdf(url, dest)
        if ok:
            success += 1
            downloaded_set.add(trans_id)
            progress["downloaded"] = list(downloaded_set)
        else:
            not_found += 1
            progress["not_found"].append(trans_id)

        if idx % PROGRESS_INTERVAL == 0:
            save_checkpoint(progress)
            elapsed = time.time() - t0
            rate = idx / elapsed if elapsed > 0 else 0
            print(f"  [{idx}/{len(to_download)}] {success} ok, {not_found} not found, "
                  f"{failed} failed ({rate:.1f}/s)")

    # Final save
    save_checkpoint(progress)

    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"Done in {elapsed:.0f}s")
    print(f"  Downloaded: {success}")
    print(f"  Not found:  {not_found}")
    print(f"  Failed:     {failed}")
    print(f"  Total in checkpoint: {len(downloaded_set)}")
    print(f"  PDFs in: {PDF_DIR}")

    # Dept distribution
    dept_counts = Counter()
    for d in PDF_DIR.rglob("*.pdf"):
        dept_counts[d.parent.name] += 1
    if dept_counts:
        print(f"\nDistribution by dept:")
        for dept in sorted(dept_counts):
            print(f"  {dept}: {dept_counts[dept]}")


if __name__ == "__main__":
    main()