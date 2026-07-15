"""
package_transversal_mesas.py

Package lab JPEG gallery for Railway Volume deploy (no PDFs).

Source (default):
  Laboratorio/analisis_transversal/E14C_conflictivas_pendientes/mesas/

Output:
  transversal_mesas_YYYYMMDD.tar.gz + transversal_mesas_YYYYMMDD.manifest.json

Usage:
    python scripts/package_transversal_mesas.py stats
    python scripts/package_transversal_mesas.py package
    python scripts/package_transversal_mesas.py package --output D:/backups/transversal_mesas.tar.gz
    python scripts/package_transversal_mesas.py verify --archive path/to.tar.gz
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LAB = ROOT / "Laboratorio/analisis_transversal/E14C_conflictivas_pendientes"
DEFAULT_MESAS = DEFAULT_LAB / "mesas"
SOURCES = ("e14t", "e14d", "e14c")


def _human_size(nbytes: int) -> str:
    if nbytes >= 1 << 30:
        return f"{nbytes / (1 << 30):.2f} GB"
    if nbytes >= 1 << 20:
        return f"{nbytes / (1 << 20):.0f} MB"
    return f"{nbytes / 1024:.1f} KB"


def collect_stats(mesas_dir: Path) -> dict:
    if not mesas_dir.is_dir():
        raise SystemExit(f"Missing mesas dir: {mesas_dir}")

    folders = sorted(p for p in mesas_dir.iterdir() if p.is_dir())
    files = list(mesas_dir.rglob("*.jpg"))
    total_bytes = sum(f.stat().st_size for f in files)

    by_source: dict[str, int] = {s: 0 for s in SOURCES}
    missing_any_source = 0
    sample_issues: list[str] = []

    for folder in folders:
        counts = {s: len(list(folder.glob(f"{s}_p*.jpg"))) for s in SOURCES}
        for s, n in counts.items():
            by_source[s] += n
        if not any(counts.values()):
            missing_any_source += 1
            if len(sample_issues) < 5:
                sample_issues.append(folder.name)

    return {
        "mesas_dir": str(mesas_dir),
        "mesa_folders": len(folders),
        "jpeg_files": len(files),
        "total_bytes": total_bytes,
        "total_human": _human_size(total_bytes),
        "jpeg_by_source": by_source,
        "folders_without_jpeg": missing_any_source,
        "sample_empty_folders": sample_issues,
        "railway_volume_hint_gb": round(total_bytes / (1 << 30) * 1.05, 1),
    }


def cmd_stats(args: argparse.Namespace) -> None:
    stats = collect_stats(args.mesas_dir)
    print(json.dumps(stats, indent=2, ensure_ascii=False))
    print("\n--- Railway checklist (preview) ---")
    print(f"  Allocate Volume: >= {stats['railway_volume_hint_gb']} GB (uncompressed + headroom)")
    print("  Mount path:      /data/transversal_mesas")
    print("  Env var:         TRANSVERSAL_LAB_MESAS_DIR=/data/transversal_mesas")
    print("  PDFs required:   NO (JPEGs only)")


def _sha256_file(path: Path, chunk: int = 8 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fp:
        while True:
            block = fp.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def cmd_package(args: argparse.Namespace) -> None:
    mesas_dir = args.mesas_dir.resolve()
    if not mesas_dir.is_dir():
        raise SystemExit(f"Missing mesas dir: {mesas_dir}")

    stats = collect_stats(mesas_dir)
    ts = time.strftime("%Y%m%d")
    out = args.output or (DEFAULT_LAB / f"transversal_mesas_{ts}.tar.gz")
    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    if out.exists() and not args.force:
        raise SystemExit(f"Output exists (use --force): {out}")

    print(f"[package] Source: {mesas_dir}")
    print(f"[package] Mesas: {stats['mesa_folders']:,} | JPEGs: {stats['jpeg_files']:,} | {stats['total_human']}")
    print(f"[package] Writing: {out}")
    print("[package] This may take 10-30+ minutes for ~16 GB...")

    t0 = time.time()
    with tarfile.open(out, "w:gz") as tar:
        for folder in sorted(mesas_dir.iterdir()):
            if folder.is_dir():
                arcname = folder.name
                tar.add(folder, arcname=arcname)

    elapsed = time.time() - t0
    archive_bytes = out.stat().st_size
    digest = _sha256_file(out)

    manifest = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_mesas_dir": str(mesas_dir),
        "archive": str(out),
        "archive_bytes": archive_bytes,
        "archive_human": _human_size(archive_bytes),
        "archive_sha256": digest,
        "uncompressed_stats": stats,
        "package_seconds": round(elapsed, 1),
        "railway": {
            "mount_path": "/data/transversal_mesas",
            "env_var": "TRANSVERSAL_LAB_MESAS_DIR=/data/transversal_mesas",
            "extract_command": f"mkdir -p /data/transversal_mesas && tar -xzf {out.name} -C /data/transversal_mesas",
            "note": "Extract creates /data/transversal_mesas/<mesa_key>/e14c_p01.jpg — set env to parent dir.",
        },
    }
    manifest_path = out.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"\n[package] Done in {elapsed / 60:.1f} min")
    print(f"  Archive:  {out} ({_human_size(archive_bytes)})")
    print(f"  SHA256:   {digest}")
    print(f"  Manifest: {manifest_path}")
    print("\n--- Upload to Railway ---")
    print("  1. Create Volume (>= {:.0f} GB mount at /data)".format(stats["railway_volume_hint_gb"]))
    print("  2. Upload archive (Railway CLI / SCP / one-off job)")
    print(f"  3. Extract: tar -xzf {out.name} -C /data/transversal_mesas")
    print("  4. Set TRANSVERSAL_LAB_MESAS_DIR=/data/transversal_mesas")
    print("  5. Redeploy production")


def cmd_verify(args: argparse.Namespace) -> None:
    archive = Path(args.archive).resolve()
    if not archive.is_file():
        raise SystemExit(f"Archive not found: {archive}")

    manifest_path = archive.with_suffix(".manifest.json")
    expected_sha = None
    if manifest_path.exists():
        expected_sha = json.loads(manifest_path.read_text(encoding="utf-8")).get("archive_sha256")

    print(f"[verify] SHA256 {archive.name} ...")
    digest = _sha256_file(archive)
    ok = expected_sha is None or digest == expected_sha
    print(f"  computed: {digest}")
    if expected_sha:
        print(f"  expected: {expected_sha}")
        print(f"  match:    {ok}")

    with tarfile.open(archive, "r:gz") as tar:
        names = tar.getnames()
    top_dirs = {n.split("/")[0] for n in names if "/" in n}
    top_dirs |= {n for n in names if n and "/" not in n}
    print(f"  entries:  {len(names):,}")
    print(f"  mesas:    {len(top_dirs):,}")
    if not ok:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Package transversal mesas JPEGs for Railway")
    parser.add_argument("--mesas-dir", type=Path, default=DEFAULT_MESAS)
    sub = parser.add_subparsers(dest="command", required=True)

    p_stats = sub.add_parser("stats", help="Show size/counts and Railway hints")
    p_stats.set_defaults(func=cmd_stats)

    p_pkg = sub.add_parser("package", help="Create tar.gz + manifest")
    p_pkg.add_argument("--output", type=Path, default=None)
    p_pkg.add_argument("--force", action="store_true", help="Overwrite existing archive")
    p_pkg.set_defaults(func=cmd_package)

    p_ver = sub.add_parser("verify", help="Verify archive checksum and contents")
    p_ver.add_argument("--archive", type=Path, required=True)
    p_ver.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()