"""
E14C static URL builder — second method.

Unlike the dynamic scraper (src/modules/e14c/scraper.py) which hits
/data/index.json + per-puesto mesas JSON on every run, this script:

  1. Downloads comisiones.json once (commission hierarchy)
  2. Downloads all mesas JSONs from index.json in one batch
  3. Saves everything locally under data/e14c_snapshot_{vuelta}/
  4. Extracts all PDF URLs from the local snapshot — no live server needed

Re-running from an existing snapshot skips the download entirely.

Usage:
    python scripts/e14/e14c/build_urls.py --vuelta segunda   # default
    python scripts/e14/e14c/build_urls.py --vuelta primera
    python scripts/e14/e14c/build_urls.py --refresh           # force re-download
"""
import argparse, asyncio, json, ssl, urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_VUELTA_CONFIG = {
    "primera": {
        "base":       "https://escrutiniospresidente2026.registraduria.gov.co",
        "index_local": Path("data/e14c_index.json"),
        "output":     Path("data/e14c_urls_primera.jsonl"),
        "snapshot":   Path("data/e14c_snapshot_primera"),
    },
    "segunda": {
        "base":       "https://escrutinios2vueltapresidente2026.registraduria.gov.co",
        "index_local": Path("data/index_e14c_segunda.json"),
        "output":     Path("data/e14c_sv_urls.jsonl"),
        "snapshot":   Path("data/e14c_snapshot_segunda"),
    },
}

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _fetch(url: str, timeout: int = 20) -> Optional[bytes]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=_ssl_ctx, timeout=timeout) as r:
            body = r.read()
            if body.lstrip()[:5] in (b"<!doc", b"<html"):
                return None
            return body
    except Exception:
        return None


async def _fetch_async(url: str) -> Optional[bytes]:
    return await asyncio.to_thread(_fetch, url)


# ---------------------------------------------------------------------------
# Commission lookup — comision code from filename
# ---------------------------------------------------------------------------

def _load_comisiones(base: str, snapshot_dir: Path) -> dict:
    """Load comisiones.json from snapshot or download it."""
    local = snapshot_dir / "comisiones.json"
    if local.exists():
        return json.loads(local.read_text(encoding="utf-8"))

    # Discover filename via index pattern
    index_url = f"{base}/data/index.json"
    raw = _fetch(index_url)
    if not raw:
        print("  WARN: could not fetch index to discover comisiones filename")
        return {}

    # Try known pattern: /data/esc/v1/comision/comisiones_*.json
    # Fall back to saved local copy if available
    saved = Path("data/comisiones_segunda.json")
    if saved.exists():
        data = json.loads(saved.read_text(encoding="utf-8"))
        local.write_text(json.dumps(data, ensure_ascii=False))
        return data
    return {}


def build_comision_lookup(comisiones: dict) -> dict[str, str]:
    """Flat lookup: comision_code -> etiqueta for all levels."""
    lookup = {}
    for dk, dv in comisiones.items():
        lookup[dk] = dv.get("etiqueta", "")
        for mk, mv in dv.get("municipales", {}).items():
            lookup[mk] = mv.get("etiqueta", "")
            for ak, av in mv.get("auxiliares", {}).items():
                lookup[ak] = av.get("etiqueta", "")
    return lookup


# ---------------------------------------------------------------------------
# Snapshot: download all mesas JSONs
# ---------------------------------------------------------------------------

async def _download_snapshot(base: str, snapshot_dir: Path, max_concurrent: int = 30) -> dict:
    """Download all mesas JSONs from index and save to snapshot_dir."""
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    index_url = f"{base}/data/index.json"
    print(f"Fetching index: {index_url}")
    raw = _fetch(index_url)
    if not raw:
        print("ERROR: could not fetch index.json")
        return {}

    index = json.loads(raw)
    entries = {k: v for k, v in index.items() if k.endswith("/mesas/")}
    print(f"Puestos in index: {len(entries)}")

    semaphore = asyncio.Semaphore(max_concurrent)
    results = {}

    async def fetch_one(path: str, filename: str) -> None:
        local_path = snapshot_dir / (path.replace("/", "_") + filename)
        if local_path.exists():
            results[path] = json.loads(local_path.read_text(encoding="utf-8"))
            return
        async with semaphore:
            url = f"{base}/{path}{filename}"
            body = await _fetch_async(url)
        if body:
            try:
                data = json.loads(body)
                local_path.write_bytes(body)
                results[path] = data
            except Exception:
                pass

    tasks = [fetch_one(p, f) for p, f in entries.items()]
    BATCH = 500
    for i in range(0, len(tasks), BATCH):
        await asyncio.gather(*tasks[i:i + BATCH])
        print(f"  {min(i + BATCH, len(tasks))}/{len(tasks)} puestos", flush=True)

    return results


def _load_snapshot(snapshot_dir: Path) -> dict:
    """Load already-downloaded mesas JSONs from snapshot_dir."""
    results = {}
    for f in snapshot_dir.glob("*.json"):
        if f.name == "comisiones.json":
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            # Reconstruct path key from filename
            results[f.stem] = data
        except Exception:
            pass
    return results


# ---------------------------------------------------------------------------
# URL builder
# ---------------------------------------------------------------------------

def _parse_path(path: str) -> tuple[str, str, str, str]:
    parts = path.strip("/").split("/")
    return parts[5], parts[6], parts[7], parts[8]


def build_urls(
    snapshot: dict,
    base: str,
    comision_lookup: dict,
    output: Path,
) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with open(output, "w", encoding="utf-8") as f:
        for path, mesas in snapshot.items():
            if not isinstance(mesas, list):
                continue
            # path is either original key or reconstructed from filename stem
            try:
                dept, mpio, zona, puesto = _parse_path(path)
            except (IndexError, ValueError):
                continue

            for mesa in mesas:
                nombre = mesa.get("nombre_archivo", "")
                digital = mesa.get("digitalizado", 0)
                if not (digital > 0 and nombre):
                    continue

                # Extract commission code from filename (last numeric segment)
                stem = Path(nombre).stem  # e.g. E14_PRE_60_010_000_00_00_001_3085
                parts = stem.split("_")
                comision_code = parts[-1] if parts else ""
                comision_label = comision_lookup.get(comision_code, "")

                record = {
                    "cod_dept":   dept,
                    "cod_mpio":   mpio,
                    "zona":       zona,
                    "cod_puesto": puesto,
                    "mesa":       mesa["numero"],
                    "escrutado":  mesa.get("escrutado", False),
                    "digitalizado": digital,
                    "id_mesa":    mesa.get("id_informacion_mesa_corporacion", "").strip(),
                    "comision":   comision_code,
                    "comision_label": comision_label,
                    "pdf_url":    base + nombre,
                    "built_at":   datetime.now(timezone.utc).isoformat(),
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                total += 1
    return total


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vuelta", default="segunda", choices=["primera", "segunda"])
    parser.add_argument("--refresh", action="store_true", help="Re-download snapshot even if exists")
    parser.add_argument("--concurrent", type=int, default=30)
    args = parser.parse_args()

    cfg = _VUELTA_CONFIG[args.vuelta]
    base         = cfg["base"]
    snapshot_dir = cfg["snapshot"]
    output       = cfg["output"]

    print(f"Vuelta   : {args.vuelta}")
    print(f"Base     : {base}")
    print(f"Snapshot : {snapshot_dir}")
    print(f"Output   : {output}")
    print()

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    comisiones = _load_comisiones(base, snapshot_dir)
    comision_lookup = build_comision_lookup(comisiones)
    print(f"Commission codes loaded: {len(comision_lookup)}")

    existing = list(snapshot_dir.glob("*.json"))
    non_comision = [f for f in existing if f.name != "comisiones.json"]

    if non_comision and not args.refresh:
        print(f"Snapshot exists ({len(non_comision)} files) — building URLs from local data.")
        print("Use --refresh to re-download.")
        snapshot = {}
        for f in non_comision:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                snapshot[f.stem] = data
            except Exception:
                pass
    else:
        print("Downloading snapshot...")
        snapshot = asyncio.run(_download_snapshot(base, snapshot_dir, args.concurrent))

    print(f"\nBuilding URLs from {len(snapshot)} puestos...")
    total = build_urls(snapshot, base, comision_lookup, output)
    print(f"Done. {total} URLs -> {output}")


if __name__ == "__main__":
    main()
