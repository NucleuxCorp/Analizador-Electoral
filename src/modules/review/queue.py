"""Queue builder for transversal conflictivas (compute-on-read, no reprocess)."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterator

from src.modules.review.alerts import build_mesa_alert_package
from src.modules.review.field_audit import build_index_row, mesa_key as _mesa_key, resolve_primary_source

_CROSS_FILE_RE = re.compile(r"^cross_mesa_validation_\d{2}\.jsonl$")
_DEFAULT_DATA_DIR = Path(__file__).resolve().parents[3] / "data"


def iter_cross_rows(data_dir: Path | None = None) -> Iterator[dict]:
    base = data_dir or _DEFAULT_DATA_DIR
    for path in sorted(p for p in base.iterdir() if _CROSS_FILE_RE.match(p.name)):
        with open(path, encoding="utf-8") as fp:
            for line in fp:
                if line.strip():
                    yield json.loads(line)


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
        pending = pkg["pending_human_count"]
        if pending_only and pending == 0:
            continue

        mesa_dec = decisions.get(mk) or {}
        items.append({
            **{k: row[k] for k in ("mesa_key", "dept", "mpio", "zona", "puesto", "mesa")},
            "candidate_votes": row.get("candidate_votes"),
            "blank_fields": row.get("blank_fields") or [],
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