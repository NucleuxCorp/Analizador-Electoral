"""
build_hash_index.py — Maintainer tool.

Reads the .verify_checkpoint.json produced by verify_e14c.py --full --purge,
downloads every confirmed-OK PDF from the Registraduría server, streams a
SHA-256 over each response, and writes hash_index_e14c.json.

The 30 known-DIF files are also processed: local SHA-256 from disk, server
SHA-256 from a fresh download. Both are stored under `known_dif`.

This script is NEVER shipped to collaborators. It runs once on the maintainer
machine (online, with the checkpoint and local DIF copies accessible).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import ssl
import sys
import time as _time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

try:
    from tqdm import tqdm
except ImportError:
    print("tqdm is required: pip install tqdm", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Server base URL — same as verify_e14c.py
# ---------------------------------------------------------------------------
BASE = "https://escrutinios2vueltapresidente2026.registraduria.gov.co"

# ---------------------------------------------------------------------------
# Rate limiting — copied verbatim from verify_e14c.py
# ---------------------------------------------------------------------------
_RATE = {"delay": 1.0, "cooldown": 60, "consecutive_fails": 0}


# ---------------------------------------------------------------------------
# URL reconstruction — adapted from verify_e14c.py::build_url()
# Accepts a bare filename string instead of a Path object.
# ---------------------------------------------------------------------------
def build_url(filename: str) -> str | None:
    """Reconstruct the Registraduria CDN URL from a bare PDF filename."""
    stem = Path(filename).stem
    parts = stem.split("_", 5)
    if len(parts) > 5 and parts[4] == "E14":
        dept, mpio, zona, puesto = parts[0], parts[1], parts[2], parts[3]
        url_fn = "_".join(parts[4:]) + ".pdf"
        return f"{BASE}/docs/E14/{dept}/{mpio}/{zona}/{puesto}/{url_fn}"
    return None


# ---------------------------------------------------------------------------
# SSL context — same bypass as verify_e14c.py
# ---------------------------------------------------------------------------
def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


_CTX = _ssl_ctx()


# ---------------------------------------------------------------------------
# Hashing helpers
# ---------------------------------------------------------------------------
CHUNK = 64 * 1024  # 64 KB


def hash_local(path: Path) -> str:
    """Compute SHA-256 of a local file by streaming 64 KB chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def hash_remote(url: str) -> tuple[str, int] | None:
    """Download a URL in 64 KB chunks, compute SHA-256. Returns (hex, size) or None."""
    global _RATE
    # Rate limiting
    _time.sleep(_RATE["delay"])
    if _RATE["consecutive_fails"] >= 3:
        print(f"\n  Rate-limit cooldown {_RATE['cooldown']}s...", flush=True)
        _time.sleep(_RATE["cooldown"])
        _RATE["consecutive_fails"] = 0
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, context=_CTX, timeout=60)
        h = hashlib.sha256()
        size = 0
        while chunk := resp.read(CHUNK):
            h.update(chunk)
            size += len(chunk)
        resp.close()
        _RATE["consecutive_fails"] = 0
        return h.hexdigest(), size
    except Exception as exc:
        _RATE["consecutive_fails"] += 1
        return None


# ---------------------------------------------------------------------------
# Checkpoint reading
# ---------------------------------------------------------------------------
def read_checkpoint(path: Path) -> tuple[list[str], list[str]]:
    """
    Read .verify_checkpoint.json and return (ok_paths, dif_paths).
    Supports nested format {full: {ok: [...], dif: [...]}} and flat format.
    Exits non-zero with a message if the file is absent.
    """
    if not path.exists():
        print(
            f"ERROR: Missing checkpoint file at {path}\n"
            f"  Run verify_e14c.py --full --purge first to generate it.",
            file=sys.stderr,
        )
        sys.exit(1)

    raw = json.loads(path.read_text(encoding="utf-8"))

    # Determine shape — same logic as verify_e14c.py lines 116-128
    if isinstance(raw, list):
        ok = raw
        dif: list[str] = []
    elif isinstance(raw, dict):
        if any(k in raw for k in ("ok", "dif", "err")):
            # Flat format
            ok = raw.get("ok", [])
            dif = raw.get("dif", [])
        else:
            # Nested format {head: {...}, full: {...}}
            # full.ok is reset to [] after --purge — fall back to head.ok which
            # contains the same verified paths (size-matched = content-matched
            # for these PDFs, confirmed by the subsequent full run).
            full = raw.get("full", {})
            ok = full.get("ok", [])
            dif = full.get("dif", [])
            if not ok:
                head = raw.get("head", {})
                ok = head.get("ok", [])
                if ok:
                    print(
                        f"INFO: full.ok is empty (reset by --purge). "
                        f"Using head.ok ({len(ok):,} paths) as source list.",
                        flush=True,
                    )
    else:
        ok = []
        dif = []

    return list(ok), list(dif)


# ---------------------------------------------------------------------------
# Build-progress checkpoint (resume support)
# ---------------------------------------------------------------------------
def load_build_progress(progress_path: Path) -> dict:
    if progress_path.exists():
        data = json.loads(progress_path.read_text(encoding="utf-8"))
        print(
            f"Resuming from {progress_path}: "
            f"{len(data.get('by_hash', {}))} hashes already computed.",
            flush=True,
        )
        return data
    return {
        "by_hash": {},
        "by_filename": {},
        "known_dif": {},
        "processed_ok": [],
        "processed_dif": [],
        "errors": [],
    }


def save_build_progress(progress_path: Path, progress: dict) -> None:
    """Atomic write via .tmp + os.replace."""
    tmp = progress_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(progress, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, progress_path)


# ---------------------------------------------------------------------------
# Worker callable for ThreadPoolExecutor
# ---------------------------------------------------------------------------
def _download_one(filename: str) -> tuple[str, str | None, int]:
    """Returns (filename, sha256_hex_or_None, size)."""
    url = build_url(filename)
    if not url:
        return filename, None, 0
    result = hash_remote(url)
    if result is None:
        return filename, None, 0
    sha256, size = result
    return filename, sha256, size


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build SHA-256 hash index from Registraduría server (maintainer tool)."
    )
    parser.add_argument(
        "--checkpoint",
        default="Data/e14_segunda/E14C/.verify_checkpoint.json",
        help="Path to .verify_checkpoint.json produced by verify_e14c.py --full",
    )
    parser.add_argument(
        "--output",
        default="hash_index_e14c.json",
        help="Output path for the hash index JSON (default: hash_index_e14c.json)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=10,
        help="Concurrent download workers (default: 10)",
    )
    parser.add_argument(
        "--dif-dir",
        default=None,
        help="Override base directory for local DIF files (default: inferred from checkpoint paths)",
    )
    args = parser.parse_args()

    checkpoint_path = Path(args.checkpoint)
    output_path = Path(args.output)
    progress_path = output_path.parent / "hash_index_build_progress.json"

    # --- Read checkpoint -------------------------------------------------------
    ok_paths, dif_paths = read_checkpoint(checkpoint_path)
    print(f"Checkpoint: {len(ok_paths)} OK, {len(dif_paths)} DIF", flush=True)

    # --- Load resume state -----------------------------------------------------
    progress = load_build_progress(progress_path)
    already_processed: set[str] = set(progress["processed_ok"])
    already_dif: set[str] = set(progress["processed_dif"])

    by_hash: dict[str, str] = progress["by_hash"]
    by_filename: dict[str, str] = progress["by_filename"]
    known_dif: dict[str, dict] = progress["known_dif"]
    errors: list[dict] = progress["errors"]

    # --- Build list of filenames to process ------------------------------------
    pending_ok: list[str] = []
    for p in ok_paths:
        fn = Path(p).name
        if fn not in already_processed:
            pending_ok.append(fn)

    print(
        f"Pending downloads: {len(pending_ok)} "
        f"(skipping {len(already_processed)} already done)",
        flush=True,
    )

    # --- Concurrent download loop ----------------------------------------------
    save_every = 50
    completed_count = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_download_one, fn): fn for fn in pending_ok}
        with tqdm(total=len(pending_ok), desc="Downloading & hashing", unit="PDF") as pbar:
            for future in as_completed(futures):
                filename, sha256, _size = future.result()
                if sha256 is not None:
                    by_hash[sha256] = filename
                    by_filename[filename] = sha256
                    progress["processed_ok"].append(filename)
                else:
                    errors.append({"filename": filename, "error": "download_failed"})
                    print(f"\n  WARN: failed to hash {filename}", flush=True)

                completed_count += 1
                pbar.update(1)

                if completed_count % save_every == 0:
                    progress["by_hash"] = by_hash
                    progress["by_filename"] = by_filename
                    progress["errors"] = errors
                    save_build_progress(progress_path, progress)

    # Save after OK phase
    progress["by_hash"] = by_hash
    progress["by_filename"] = by_filename
    progress["errors"] = errors
    save_build_progress(progress_path, progress)

    # --- DIF processing --------------------------------------------------------
    print(f"\nProcessing {len(dif_paths)} known DIF files...", flush=True)
    for dif_path_str in dif_paths:
        dif_path = Path(dif_path_str)
        filename = dif_path.name

        if filename in already_dif:
            print(f"  SKIP (already done): {filename}", flush=True)
            continue

        # Override base dir if requested
        if args.dif_dir:
            dif_path = Path(args.dif_dir) / filename

        if not dif_path.exists():
            print(
                f"ERROR: Local DIF file not found: {dif_path}\n"
                f"  The 30 DIF files must be accessible at build time.\n"
                f"  Mount the drive or use --dif-dir to point to their location.",
                file=sys.stderr,
            )
            sys.exit(1)

        local_sha = hash_local(dif_path)

        url = build_url(filename)
        if not url:
            print(f"  WARN: Could not build URL for DIF file {filename}", flush=True)
            continue

        result = hash_remote(url)
        if result is None:
            print(f"  WARN: Failed to download server version of DIF file {filename}", flush=True)
            server_sha = ""
        else:
            server_sha, _ = result

        known_dif[filename] = {
            "local_sha256": local_sha,
            "server_sha256": server_sha,
            "note": "Acta con contenido modificado confirmado por comparacion visual",
        }
        progress["known_dif"] = known_dif
        progress["processed_dif"].append(filename)
        save_build_progress(progress_path, progress)
        print(
            f"  DIF: {filename}  local={local_sha[:12]}...  server={server_sha[:12] if server_sha else 'N/A'}...",
            flush=True,
        )

    # --- Assemble final index --------------------------------------------------
    index = {
        "version": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "entries_count": len(by_hash),
        "known_dif_count": len(known_dif),
        "coverage_note": (
            "Aproximadamente 27,000 mesas E-14C no estan en este indice. "
            "Los PDFs de esas mesas apareceran como DESCONOCIDA, no como ALTERADA."
        ),
        "by_hash": by_hash,
        "by_filename": by_filename,
        "known_dif": known_dif,
    }

    # Atomic write
    tmp_path = output_path.with_suffix(".tmp")
    tmp_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=None), encoding="utf-8"
    )
    os.replace(tmp_path, output_path)

    print(f"\nIndex written: {output_path}")
    print(f"  Entries: {index['entries_count']}")
    print(f"  Known DIF: {index['known_dif_count']}")
    print(f"  Errors:  {len(errors)}")

    if errors:
        print(f"\nFirst errors:")
        for e in errors[:5]:
            print(f"  {e['filename']}: {e.get('error', '?')}")


if __name__ == "__main__":
    main()
