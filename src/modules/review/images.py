"""On-demand PDF→JPEG cache for transversal acta pages."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from src.modules.review.datasets import lab_mesas_path, storage_object_prefix

logger = logging.getLogger("review.images")

_SOURCES = ("e14c", "e14d", "e14t")
_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DATA_DIR = _ROOT / "data"
_CROSS_INDEX_PATH = _DEFAULT_DATA_DIR / "cross_mesa_index.jsonl"
_RENDER_DPI = 150

_cross_index: dict[str, dict] | None = None
_storage_counts_cache: dict[str, dict[str, int]] = {}


def storage_bucket() -> str | None:
    bucket = os.environ.get("TRANSVERSAL_STORAGE_BUCKET", "").strip()
    return bucket or None


def storage_public_base() -> str | None:
    """Public CDN base including dataset prefix (E14C|D|T_conflictivas)."""
    base_url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    bucket = storage_bucket()
    if not base_url or not bucket:
        return None
    prefix = storage_object_prefix()
    return f"{base_url}/storage/v1/object/public/{bucket}/{prefix}"


def storage_public_url(mesa_key: str, source: str, page: int) -> str | None:
    base = storage_public_base()
    if not base or source not in _SOURCES or page < 1:
        return None
    return f"{base}/{mesa_key}/{source}_p{page:02d}.jpg"


def _storage_list_path(mesa_key: str) -> str:
    return f"{storage_object_prefix()}/{mesa_key}"


def _storage_page_counts(mesa_key: str) -> dict[str, int]:
    if mesa_key in _storage_counts_cache:
        return _storage_counts_cache[mesa_key]
    bucket = storage_bucket()
    if not bucket:
        return {}
    try:
        from src.modules.labeler.db import _client

        items = _client().storage.from_(bucket).list(_storage_list_path(mesa_key)) or []
        counts = {src: 0 for src in _SOURCES}
        for item in items:
            name = item.get("name") or ""
            for src in _SOURCES:
                if name.startswith(f"{src}_p") and name.endswith(".jpg"):
                    counts[src] += 1
        _storage_counts_cache[mesa_key] = counts
        return counts
    except Exception as exc:
        logger.warning("storage list failed %s: %s", mesa_key, exc)
        return {}


def cache_dir() -> Path:
    return Path(os.environ.get("TRANSVERSAL_CACHE_DIR", _ROOT / "data/cache/transversal_pages"))


def lab_mesas_dir() -> Path | None:
    env = os.environ.get("TRANSVERSAL_LAB_MESAS_DIR", "").strip()
    if env:
        return Path(env)
    default = lab_mesas_path()
    return default if default.is_dir() else None


def parse_mesa_key(mesa_key: str) -> tuple[str, str, str, str, str]:
    parts = mesa_key.split("_")
    if len(parts) != 5:
        raise ValueError(f"Invalid mesa_key: {mesa_key}")
    return parts[0], parts[1], parts[2], parts[3], parts[4]


def _load_cross_index(data_dir: Path | None = None) -> dict[str, dict]:
    global _cross_index
    if _cross_index is not None:
        return _cross_index
    path = (data_dir or _DEFAULT_DATA_DIR) / "cross_mesa_index.jsonl"
    index: dict[str, dict] = {}
    if not path.exists():
        _cross_index = index
        return index
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            if not line.strip():
                continue
            row = json.loads(line)
            mk = f"{row['dept']}_{row['mpio']}_{row['zona']}_{row['puesto']}_{row['mesa']}"
            index[mk] = row
    _cross_index = index
    return index


def resolve_pdf_path(mesa_key: str, source: str, *, data_dir: Path | None = None) -> Path | None:
    if source not in _SOURCES:
        return None
    row = _load_cross_index(data_dir).get(mesa_key)
    if not row:
        return None
    pdf_str = row.get(f"{source}_path")
    if not pdf_str:
        return None
    pdf = Path(pdf_str)
    return pdf if pdf.exists() else None


def _lab_jpeg_exists(mesa_key: str, source: str, page: int) -> Path | None:
    lab = lab_mesas_dir()
    if not lab:
        return None
    mesa_dir = lab / mesa_key
    if not mesa_dir.is_dir():
        return None
    candidates = sorted(mesa_dir.glob(f"{source}_p*.jpg"))
    for path in candidates:
        if f"_p{page:02d}." in path.name:
            return path
    if page == 1 and candidates:
        return candidates[0]
    return None


def source_available(mesa_key: str, source: str, *, data_dir: Path | None = None) -> bool:
    lab = lab_mesas_dir()
    if lab and (lab / mesa_key).is_dir():
        if any((lab / mesa_key).glob(f"{source}_p*.jpg")):
            return True
    if storage_bucket():
        return _storage_page_counts(mesa_key).get(source, 0) > 0
    pdf = resolve_pdf_path(mesa_key, source, data_dir=data_dir)
    return pdf is not None


def get_page_count(mesa_key: str, source: str, *, data_dir: Path | None = None) -> int:
    lab = lab_mesas_dir()
    if lab:
        mesa_dir = lab / mesa_key
        if mesa_dir.is_dir():
            n = len(list(mesa_dir.glob(f"{source}_p*.jpg")))
            if n:
                return n
    if storage_bucket():
        return _storage_page_counts(mesa_key).get(source, 0)
    pdf = resolve_pdf_path(mesa_key, source, data_dir=data_dir)
    if not pdf:
        return 0
    try:
        import fitz
        doc = fitz.open(str(pdf))
        n = len(doc)
        doc.close()
        return n
    except Exception as exc:
        logger.warning("get_page_count failed %s %s: %s", mesa_key, source, exc)
        return 0


def render_page_to_cache(
    mesa_key: str,
    source: str,
    page: int,
    *,
    data_dir: Path | None = None,
) -> Path | None:
    """Return cached JPEG path, rendering from PDF on miss."""
    if page < 1:
        return None

    out_dir = cache_dir() / mesa_key
    out_path = out_dir / f"{source}_p{page:02d}.jpg"
    if out_path.exists():
        return out_path

    lab_jpeg = _lab_jpeg_exists(mesa_key, source, page)
    if lab_jpeg:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(lab_jpeg.read_bytes())
        return out_path

    pdf = resolve_pdf_path(mesa_key, source, data_dir=data_dir)
    if not pdf:
        return None

    try:
        import fitz
        doc = fitz.open(str(pdf))
        if page > len(doc):
            doc.close()
            return None
        zoom = _RENDER_DPI / 72
        mat = fitz.Matrix(zoom, zoom)
        pix = doc[page - 1].get_pixmap(matrix=mat)
        out_dir.mkdir(parents=True, exist_ok=True)
        pix.save(str(out_path))
        doc.close()
        return out_path
    except Exception as exc:
        logger.warning("render_page_to_cache failed %s %s p%s: %s", mesa_key, source, page, exc)
        return None