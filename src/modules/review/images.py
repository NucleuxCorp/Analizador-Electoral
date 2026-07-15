"""On-demand PDF→page image cache for transversal acta pages (WebP primary, JPEG legacy)."""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from src.modules.review.datasets import lab_mesas_path, storage_object_prefix

logger = logging.getLogger("review.images")

_SOURCES = ("e14c", "e14d", "e14t")
_PAGE_NAME_RE = re.compile(r"^(e14[cdt])_p(\d{2})\.(webp|jpe?g)$", re.I)
_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DATA_DIR = _ROOT / "data"
_CROSS_INDEX_PATH = _DEFAULT_DATA_DIR / "cross_mesa_index.jsonl"
GALLERY_DPI = 150
GALLERY_EXT_PRIMARY = "webp"
GALLERY_EXT_LEGACY = "jpg"
GALLERY_EXTS = (GALLERY_EXT_PRIMARY, GALLERY_EXT_LEGACY)
WEBP_QUALITY = 82
WEBP_METHOD = 1  # faster encode; backlog may tune after E14D/E14T bulk run

_cross_index: dict[str, dict] | None = None
_storage_counts_cache: dict[str, dict[str, int]] = {}


def page_basename(source: str, page: int) -> str:
    return f"{source}_p{page:02d}"


def page_filename(source: str, page: int, ext: str = GALLERY_EXT_PRIMARY) -> str:
    return f"{page_basename(source, page)}.{ext}"


def page_content_type(ext: str) -> str:
    if ext == "webp":
        return "image/webp"
    return "image/jpeg"


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


def storage_public_url(
    mesa_key: str,
    source: str,
    page: int,
    *,
    ext: str = GALLERY_EXT_PRIMARY,
) -> str | None:
    base = storage_public_base()
    if not base or source not in _SOURCES or page < 1:
        return None
    return f"{base}/{mesa_key}/{page_filename(source, page, ext)}"


def storage_public_urls(mesa_key: str, source: str, page: int) -> dict[str, str | None]:
    return {ext: storage_public_url(mesa_key, source, page, ext=ext) for ext in GALLERY_EXTS}


def _storage_list_path(mesa_key: str) -> str:
    return f"{storage_object_prefix()}/{mesa_key}"


def _parse_page_name(name: str) -> tuple[str, int] | None:
    m = _PAGE_NAME_RE.match(name)
    if not m:
        return None
    return m.group(1).lower(), int(m.group(2))


def _count_pages_from_names(names: list[str], source: str) -> int:
    pages: set[int] = set()
    for name in names:
        parsed = _parse_page_name(name)
        if parsed and parsed[0] == source:
            pages.add(parsed[1])
    return len(pages)


def _storage_page_counts(mesa_key: str) -> dict[str, int]:
    if mesa_key in _storage_counts_cache:
        return _storage_counts_cache[mesa_key]
    bucket = storage_bucket()
    if not bucket:
        return {}
    try:
        from src.modules.labeler.db import _client

        items = _client().storage.from_(bucket).list(_storage_list_path(mesa_key)) or []
        names = [item.get("name") or "" for item in items]
        counts = {src: _count_pages_from_names(names, src) for src in _SOURCES}
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


def save_pixmap_as_webp(pix, out_path: Path) -> None:
    from PIL import Image

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    img.save(out_path, "WEBP", quality=WEBP_QUALITY, method=WEBP_METHOD)


def pdf_to_page_images(
    pdf_path: Path,
    out_dir: Path,
    prefix: str,
    *,
    ext: str = GALLERY_EXT_PRIMARY,
    dpi: int = GALLERY_DPI,
) -> list[Path]:
    """Convert each PDF page to {prefix}_pNN.{ext} (WebP by default)."""
    import fitz

    out_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    doc = fitz.open(str(pdf_path))
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    for i in range(len(doc)):
        pix = doc[i].get_pixmap(matrix=mat, alpha=False)
        out_path = out_dir / page_filename(prefix, i + 1, ext)
        if ext == GALLERY_EXT_PRIMARY:
            save_pixmap_as_webp(pix, out_path)
        else:
            pix.save(str(out_path))
        outputs.append(out_path)
    doc.close()
    return outputs


def _lab_page_path(mesa_key: str, source: str, page: int) -> Path | None:
    lab = lab_mesas_dir()
    if not lab:
        return None
    mesa_dir = lab / mesa_key
    if not mesa_dir.is_dir():
        return None
    for ext in GALLERY_EXTS:
        candidate = mesa_dir / page_filename(source, page, ext)
        if candidate.is_file():
            return candidate
    if page == 1:
        for ext in GALLERY_EXTS:
            matches = sorted(mesa_dir.glob(f"{source}_p*.{ext}"))
            if matches:
                return matches[0]
    return None


def _mesa_dir_page_count(mesa_dir: Path, source: str) -> int:
    pages: set[int] = set()
    for ext in GALLERY_EXTS:
        for path in mesa_dir.glob(f"{source}_p*.{ext}"):
            parsed = _parse_page_name(path.name)
            if parsed and parsed[0] == source:
                pages.add(parsed[1])
    return len(pages)


def source_available(mesa_key: str, source: str, *, data_dir: Path | None = None) -> bool:
    lab = lab_mesas_dir()
    if lab and (lab / mesa_key).is_dir():
        if _mesa_dir_page_count(lab / mesa_key, source) > 0:
            return True
    if storage_bucket():
        return _storage_page_counts(mesa_key).get(source, 0) > 0
    pdf = resolve_pdf_path(mesa_key, source, data_dir=data_dir)
    return pdf is not None


def queue_source_available(mesa_key: str, source: str, *, data_dir: Path | None = None) -> bool:
    """Fast path for queue API — avoid thousands of Storage list() calls on CDN deploy."""
    if storage_bucket():
        return source in _SOURCES
    return source_available(mesa_key, source, data_dir=data_dir)


def get_page_count(mesa_key: str, source: str, *, data_dir: Path | None = None) -> int:
    lab = lab_mesas_dir()
    if lab:
        mesa_dir = lab / mesa_key
        if mesa_dir.is_dir():
            n = _mesa_dir_page_count(mesa_dir, source)
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


def resolve_cached_page(mesa_key: str, source: str, page: int) -> tuple[Path, str] | None:
    """Return (path, mimetype) for an existing cached or lab page image."""
    out_dir = cache_dir() / mesa_key
    for ext in GALLERY_EXTS:
        candidate = out_dir / page_filename(source, page, ext)
        if candidate.is_file():
            return candidate, page_content_type(ext)
    lab_path = _lab_page_path(mesa_key, source, page)
    if lab_path:
        out_dir.mkdir(parents=True, exist_ok=True)
        ext = lab_path.suffix.lstrip(".").lower().replace("jpeg", "jpg")
        out_path = out_dir / page_filename(source, page, ext)
        if not out_path.exists():
            out_path.write_bytes(lab_path.read_bytes())
        return out_path, page_content_type(ext)
    return None


def render_page_to_cache(
    mesa_key: str,
    source: str,
    page: int,
    *,
    data_dir: Path | None = None,
) -> tuple[Path, str] | None:
    """Return (path, mimetype), rendering from PDF on miss (WebP primary)."""
    if page < 1:
        return None

    existing = resolve_cached_page(mesa_key, source, page)
    if existing:
        return existing

    pdf = resolve_pdf_path(mesa_key, source, data_dir=data_dir)
    if not pdf:
        return None

    try:
        import fitz
        doc = fitz.open(str(pdf))
        if page > len(doc):
            doc.close()
            return None
        zoom = GALLERY_DPI / 72
        mat = fitz.Matrix(zoom, zoom)
        pix = doc[page - 1].get_pixmap(matrix=mat, alpha=False)
        out_path = cache_dir() / mesa_key / page_filename(source, page, GALLERY_EXT_PRIMARY)
        save_pixmap_as_webp(pix, out_path)
        doc.close()
        return out_path, page_content_type(GALLERY_EXT_PRIMARY)
    except Exception as exc:
        logger.warning("render_page_to_cache failed %s %s p%s: %s", mesa_key, source, page, exc)
        return None