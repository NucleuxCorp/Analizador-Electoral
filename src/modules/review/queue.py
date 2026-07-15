"""Queue builder for transversal conflictivas (compute-on-read, no reprocess)."""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterator

from src.modules.review.alerts import build_mesa_alert_package
from src.modules.review.datasets import lab_dataset_dir, lab_mesas_path, normalize_dataset
from src.modules.review.field_audit import build_index_row, mesa_key as _mesa_key, resolve_primary_source
from src.modules.review.images import storage_public_base

logger = logging.getLogger("review.queue")

_CROSS_FILE_RE = re.compile(r"^cross_mesa_validation_\d{2}\.jsonl$")
_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[3] / "data"
_INDEX_CACHE: dict[str, tuple[float, list[dict]]] = {}
_INDEX_CACHE_TTL = 300.0


def iter_cross_rows(data_dir: Path | None = None) -> Iterator[dict]:
    base = data_dir or _DEFAULT_DATA_DIR
    for path in sorted(p for p in base.iterdir() if _CROSS_FILE_RE.match(p.name)):
        with open(path, encoding="utf-8") as fp:
            for line in fp:
                if line.strip():
                    yield json.loads(line)


def _read_index_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _fetch_index_from_storage(dataset: str) -> list[dict]:
    base = storage_public_base()
    if not base:
        return []
    url = f"{base}/index.jsonl"
    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            text = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        logger.warning("transversal index not in storage (%s): %s", url, exc.code)
        return []
    except Exception as exc:
        logger.warning("transversal index fetch failed (%s): %s", url, exc)
        return []
    rows: list[dict] = []
    for line in text.splitlines():
        if line.strip():
            rows.append(json.loads(line))
    logger.info("loaded transversal index from storage: %s rows dataset=%s", len(rows), dataset)
    return rows


def _resolve_index_path() -> Path | None:
    env = os.environ.get("TRANSVERSAL_INDEX_JSONL", "").strip()
    if env:
        path = Path(env)
        return path if path.is_file() else None
    lab_index = lab_dataset_dir(normalize_dataset()) / "index.jsonl"
    return lab_index if lab_index.is_file() else None


def load_transversal_index_rows(
    *,
    excluded: set[str] | None = None,
    source: str | None = None,
    force_reload: bool = False,
) -> list[dict]:
    """Load pre-built index for production (Storage/lab) or compute from cross JSONL (dev)."""
    src = resolve_primary_source(source)
    dataset = normalize_dataset()
    cache_key = f"{dataset}:{src}"
    now = time.time()
    if not force_reload and cache_key in _INDEX_CACHE:
        ts, cached = _INDEX_CACHE[cache_key]
        if now - ts < _INDEX_CACHE_TTL:
            rows = cached
        else:
            rows = []
    else:
        rows = []

    if not rows:
        path = _resolve_index_path()
        if path:
            rows = _read_index_jsonl(path)
            logger.info("loaded transversal index from file: %s (%s rows)", path, len(rows))
        if not rows:
            rows = _fetch_index_from_storage(dataset)
        if not rows and _DEFAULT_DATA_DIR.is_dir() and any(
            _CROSS_FILE_RE.match(p.name) for p in _DEFAULT_DATA_DIR.iterdir()
        ):
            rows = list(iter_conflictivas_jsonl(excluded=set(), source=src))
            logger.info("computed transversal index from cross JSONL (%s rows)", len(rows))
        _INDEX_CACHE[cache_key] = (now, rows)

    skip = excluded or set()
    if skip:
        return [r for r in rows if r.get("mesa_key") not in skip]
    return rows


def get_transversal_index_row(mesa_key: str, *, source: str | None = None) -> dict | None:
    for row in load_transversal_index_rows(source=source):
        if row.get("mesa_key") == mesa_key:
            return row
    return None


def clear_transversal_index_cache() -> None:
    _INDEX_CACHE.clear()


def iter_conflictivas_jsonl(
    data_dir: Path | None = None,
    *,
    excluded: set[str] | None = None,
    source: str | None = None,
) -> Iterator[dict]:
    """Yield slim index rows for the active dataset's conflictivas from local JSONL."""
    skip = excluded or set()
    src = resolve_primary_source(source)
    for row in iter_cross_rows(data_dir):
        mk = _mesa_key(row)
        if mk in skip:
            continue
        idx = build_index_row(row, src)
        if idx is not None:
            yield idx


def _pending_field_count(
    pkg: dict,
    mesa_decisions: dict[str, dict[str, str]],
) -> int:
    """Human alert fields with at least one undecided source slot."""
    pending = 0
    for h in pkg.get("human") or []:
        field_dec = mesa_decisions.get(h["field"]) or {}
        if any(not field_dec.get(s["src"]) for s in (h.get("sources") or [])):
            pending += 1
    return pending


def _decision_progress(
    pkg: dict,
    mesa_decisions: dict[str, dict[str, str]],
) -> float:
    total_slots = sum(len(h.get("sources") or []) for h in pkg.get("human") or [])
    if total_slots == 0:
        return 1.0
    decided = 0
    for h in pkg.get("human") or []:
        field_dec = mesa_decisions.get(h["field"]) or {}
        for src in h.get("sources") or []:
            if field_dec.get(src["src"]):
                decided += 1
    return decided / total_slots


def build_queue_page(
    rows: list[dict],
    exclusions: set[str],
    *,
    page: int = 1,
    page_size: int = 50,
    dept: str | None = None,
    pending_only: bool = False,
    q: str | None = None,
    decisions: dict[str, dict] | None = None,
    source_available=None,
) -> dict[str, Any]:
    """Filter, sort, and paginate queue rows."""
    decisions = decisions or {}
    items: list[dict] = []

    for row in rows:
        mk = row["mesa_key"]
        if mk in exclusions:
            continue
        if dept and row.get("dept") != dept:
            continue
        if q and q.lower() not in mk.lower():
            continue

        pkg = build_mesa_alert_package(row, mk, source_available=source_available)
        real_blanks = pkg.get("real_blank_count", len(pkg.get("auto") or []))
        if real_blanks == 0:
            continue
        mesa_dec = decisions.get(mk) or {}
        pending = _pending_field_count(pkg, mesa_dec)
        if pending_only and pending == 0:
            continue

        items.append({
            **{k: row[k] for k in ("mesa_key", "dept", "mpio", "zona", "puesto", "mesa")},
            "candidate_votes": row.get("candidate_votes"),
            "blank_fields": row.get("blank_fields") or [],
            "real_blank_count": real_blanks,
            "pending_human_count": pending,
            "decision_progress": _decision_progress(pkg, mesa_dec),
        })

    items.sort(key=lambda r: (r["dept"], r["mesa_key"]))
    total = len(items)
    offset = max(0, (page - 1) * page_size)
    page_items = items[offset: offset + page_size]
    pending_human = sum(1 for r in items if r["pending_human_count"] > 0)

    return {
        "items": page_items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "stats": {"mesas": total, "pending_human": pending_human},
    }