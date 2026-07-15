"""
upload_transversal_mesas.py

Upload lab transversal page images to Supabase Storage (public CDN).

Object layout:
  {bucket}/{E14C|D|T_conflictivas}/{mesa_key}/{e14c|e14d|e14t}_pNN.webp  (new)
  {bucket}/.../{e14c|e14d|e14t}_pNN.jpg  (legacy, panel supports both)

Usage:
    python scripts/upload_transversal_mesas.py stats
    python scripts/upload_transversal_mesas.py stats --dataset E14D_conflictivas
    python scripts/upload_transversal_mesas.py upload --workers 4
    python scripts/upload_transversal_mesas.py upload --dataset E14T_conflictivas --dry-run --limit 10

Environment:
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY  (required for bulk upload)
    TRANSVERSAL_STORAGE_BUCKET (default: bunker-e14)
    TRANSVERSAL_DATASET        (default: E14C_conflictivas)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from src.modules.review.datasets import lab_mesas_path, normalize_dataset, storage_object_prefix
from src.modules.review.images import GALLERY_EXTS, page_content_type

DEFAULT_BUCKET = "bunker-e14"
CHECKPOINT_NAME = "upload_storage.checkpoint.json"


def _resolve_dataset(args: argparse.Namespace) -> str:
    return normalize_dataset(getattr(args, "dataset", None))


def _mesas_dir(args: argparse.Namespace) -> Path:
    if getattr(args, "mesas_dir", None):
        return Path(args.mesas_dir)
    return lab_mesas_path(_resolve_dataset(args))


def _require_env() -> tuple[str, str, str]:
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    bucket = os.environ.get("TRANSVERSAL_STORAGE_BUCKET", DEFAULT_BUCKET).strip()
    missing = []
    if not url:
        missing.append("SUPABASE_URL")
    if not key:
        missing.append("SUPABASE_SERVICE_ROLE_KEY")
    if missing:
        raise SystemExit(f"Missing env: {', '.join(missing)}")
    return url, key, bucket


def _client():
    from supabase import create_client
    url, key, _ = _require_env()
    return create_client(url, key)


def collect_files(mesas_dir: Path, dataset: str) -> list[tuple[Path, str]]:
    """Return (local_path, storage_object_key) for every page image (WebP + legacy JPEG)."""
    prefix = storage_object_prefix(dataset)
    out: list[tuple[Path, str]] = []
    for mesa_dir in sorted(p for p in mesas_dir.iterdir() if p.is_dir()):
        mk = mesa_dir.name
        for ext in GALLERY_EXTS:
            for img in sorted(mesa_dir.glob(f"*.{ext}")):
                out.append((img, f"{prefix}/{mk}/{img.name}"))
    return out


def _load_checkpoint(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(data.get("done_keys", []))
    except Exception:
        return set()


def _save_checkpoint(path: Path, done: set[str]) -> None:
    path.write_text(
        json.dumps({"done_keys": sorted(done), "ts": time.time()}, indent=2),
        encoding="utf-8",
    )


def cmd_stats(args: argparse.Namespace) -> None:
    dataset = _resolve_dataset(args)
    mesas_dir = _mesas_dir(args)
    files = collect_files(mesas_dir, dataset)
    total_bytes = sum(p.stat().st_size for p, _ in files)
    print(f"dataset: {dataset}")
    print(f"storage_prefix: {storage_object_prefix(dataset)}")
    print(f"mesas_dir: {mesas_dir}")
    print(f"mesa_folders: {len({k.split('/')[1] for _, k in files})}")
    print(f"image_files: {len(files):,}")
    print(f"total_bytes: {total_bytes / (1 << 30):.2f} GB")
    _, _, bucket = _require_env()
    print(f"target_bucket: {bucket}")
    base = os.environ["SUPABASE_URL"].rstrip("/")
    print(f"public_base: {base}/storage/v1/object/public/{bucket}/{storage_object_prefix(dataset)}")


def _upload_one(client, bucket: str, local_path: Path, object_key: str, upsert: bool) -> str:
    ext = local_path.suffix.lstrip(".").lower().replace("jpeg", "jpg")
    data = local_path.read_bytes()
    client.storage.from_(bucket).upload(
        path=object_key,
        file=data,
        file_options={
            "content-type": page_content_type(ext),
            "upsert": "true" if upsert else "false",
        },
    )
    return object_key


def _is_retryable_error(msg: str) -> bool:
    low = msg.lower()
    return any(
        token in low
        for token in ("disconnected", "timeout", "timed out", "connection", "503", "502", "429")
    )


def _upload_with_retries(
    bucket: str,
    local_path: Path,
    object_key: str,
    *,
    upsert: bool,
    retries: int,
    delay: float,
) -> str | None:
    last_err = ""
    for attempt in range(max(1, retries)):
        try:
            client = _client()
            _upload_one(client, bucket, local_path, object_key, upsert)
            time.sleep(delay)
            return None
        except Exception as exc:
            last_err = str(exc)
            if "already exists" in last_err.lower() or "duplicate" in last_err.lower():
                return None
            if attempt + 1 < retries and _is_retryable_error(last_err):
                time.sleep(min(30.0, 2.0 ** attempt))
                continue
            return last_err
    return last_err


def cmd_upload_index(args: argparse.Namespace) -> None:
    """Upload lab index.jsonl so Railway can load the queue from Storage."""
    dataset = _resolve_dataset(args)
    _, _, bucket = _require_env()
    lab_root = _mesas_dir(args).parent
    index_path = lab_root / "index.jsonl"
    if not index_path.is_file():
        raise SystemExit(f"Missing index: {index_path} — run lab_e14_conflictivas.py build-index first.")

    object_key = f"{storage_object_prefix(dataset)}/index.jsonl"
    data = index_path.read_bytes()
    print(f"[upload-index] local={index_path} ({len(data)/1024:.1f} KB)")
    print(f"[upload-index] target=s3://{bucket}/{object_key}")

    if args.dry_run:
        return

    client = _client()
    client.storage.from_(bucket).upload(
        path=object_key,
        file=data,
        # bunker-e14 bucket allows image/jpeg only — content is JSONL bytes
        file_options={"content-type": "image/jpeg", "upsert": "true"},
    )
    base = os.environ["SUPABASE_URL"].rstrip("/")
    print(f"[upload-index] OK → {base}/storage/v1/object/public/{bucket}/{object_key}")


def cmd_upload(args: argparse.Namespace) -> None:
    dataset = _resolve_dataset(args)
    _, _, bucket = _require_env()
    mesas_dir = _mesas_dir(args)
    if not mesas_dir.is_dir():
        raise SystemExit(f"Missing mesas dir: {mesas_dir}")

    checkpoint_path = mesas_dir.parent / f"{CHECKPOINT_NAME}.{dataset}"
    done = _load_checkpoint(checkpoint_path) if not args.reset_checkpoint else set()
    all_files = collect_files(mesas_dir, dataset)
    pending = [(p, k) for p, k in all_files if k not in done]
    pending_total = len(pending)
    if args.limit:
        pending = pending[: args.limit]

    print(
        f"[upload] dataset={dataset} prefix={storage_object_prefix(dataset)} "
        f"bucket={bucket} files_local={len(all_files):,} checkpoint_done={len(done):,} "
        f"pending_total={pending_total:,} batch={len(pending):,}"
    )
    if not pending:
        print("[upload] Nothing to upload — all files in scope are already in checkpoint.")
        print(f"  checkpoint: {checkpoint_path}")
        print("  Tip: omit --limit to continue with remaining files, or use --reset-checkpoint to force re-upload.")
        return

    if args.dry_run:
        for p, k in pending[:20]:
            print(f"  would upload: {k} ({p.stat().st_size} bytes)")
        if len(pending) > 20:
            print(f"  ... and {len(pending) - 20:,} more")
        return

    stats = {"uploaded": 0, "errors": 0, "skipped": len(done)}
    delay = 1.0 / max(args.rate_limit, 0.1)
    workers = max(1, args.workers)

    def task(item: tuple[Path, str]) -> tuple[str, str | None]:
        local_path, object_key = item
        err = _upload_with_retries(
            bucket,
            local_path,
            object_key,
            upsert=args.upsert,
            retries=args.retries,
            delay=delay,
        )
        return object_key, err

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(task, item): item for item in pending}
        for i, fut in enumerate(as_completed(futs), 1):
            object_key, err = fut.result()
            if err:
                stats["errors"] += 1
                print(f"  ERROR {object_key}: {err}")
            else:
                stats["uploaded"] += 1
                done.add(object_key)
            if i % 50 == 0:
                _save_checkpoint(checkpoint_path, done)
                print(f"  [{i:,}/{len(pending):,}] uploaded={stats['uploaded']:,} errors={stats['errors']}")

    _save_checkpoint(checkpoint_path, done)
    print(f"[upload] Done. uploaded={stats['uploaded']:,} errors={stats['errors']:,} checkpoint={checkpoint_path}")


def _add_dataset_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dataset",
        default=None,
        help="E14C_conflictivas | E14D_conflictivas | E14T_conflictivas (or TRANSVERSAL_DATASET)",
    )
    parser.add_argument(
        "--mesas-dir",
        type=Path,
        default=None,
        help="Override lab mesas folder (default from --dataset)",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload transversal mesa JPEGs to Supabase Storage")
    _add_dataset_args(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    p_stats = sub.add_parser("stats", help="Count local files and show target bucket")
    _add_dataset_args(p_stats)
    p_stats.set_defaults(func=cmd_stats)

    p_up = sub.add_parser("upload", help="Upload JPEGs to Supabase Storage")
    _add_dataset_args(p_up)
    p_up.add_argument("--workers", type=int, default=2, help="Parallel upload workers (2 is safer for Supabase)")
    p_up.add_argument("--rate-limit", type=float, default=2.0, help="Max uploads per second per worker")
    p_up.add_argument("--retries", type=int, default=4, help="Retries on transient network errors")
    p_up.add_argument("--dry-run", action="store_true")
    p_up.add_argument("--limit", type=int, default=0, help="Upload only first N pending files (test)")
    p_up.add_argument("--upsert", action="store_true", help="Overwrite existing objects")
    p_up.add_argument("--reset-checkpoint", action="store_true")
    p_up.set_defaults(func=cmd_upload)

    p_idx = sub.add_parser("upload-index", help="Upload index.jsonl for Railway queue")
    _add_dataset_args(p_idx)
    p_idx.add_argument("--dry-run", action="store_true")
    p_idx.set_defaults(func=cmd_upload_index)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()