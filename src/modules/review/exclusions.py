"""Merge human decisiones files to exclude mesas from transversal queue."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from src.modules.review.field_audit import resolve_primary_source

TOTAL_FIELDS = ("VOTANTES", "URNA", "SUMA_TOTAL")

DECISIONS_FILENAMES = (
    "decisiones_E14C_3_totales_blancos.json",
    "decisiones_E14D_3_totales_blancos.json",
    "decisiones_E14T_3_totales_blancos.json",
)

_DECISIONS_CACHE: dict[str, tuple[float, dict[str, dict]]] = {}
_DECISIONS_CACHE_TTL = 300.0


def _default_decisions_dir() -> Path:
    env = os.environ.get("TRANSVERSAL_DECISIONS_DIR")
    if env:
        return Path(env)
    return Path(r"E:\Nucleux\tools\Analizador de Elecciones\Data")


def _source_confirmed(entry: dict, src: str = "e14c") -> bool:
    reports = entry.get("_reports") or []
    if any(r.get("source") in (src, None) for r in reports):
        return True
    dec = entry.get(src)
    return isinstance(dec, dict) and any(dec.get(f) is True for f in TOTAL_FIELDS)


def _read_and_merge(base: Path) -> dict[str, dict]:
    """Read and merge all decisiones JSON files into one mesa_key → entry map."""
    merged: dict[str, dict] = {}
    for fname in DECISIONS_FILENAMES:
        path = base / fname
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for mk, entry in data.get("decisions", {}).items():
            if mk.startswith("_"):
                continue
            merged.setdefault(mk, {})
            for key, val in entry.items():
                if key == "_reports":
                    merged[mk].setdefault("_reports", [])
                    if isinstance(val, list):
                        merged[mk]["_reports"].extend(val)
                else:
                    merged[mk][key] = val
    return merged


def merge_decisions(
    decisions_dir: Path | None = None,
    *,
    force_reload: bool = False,
) -> dict[str, dict]:
    """Merge all decisiones JSON files into one mesa_key → entry map.

    Cached per `decisions_dir` for `_DECISIONS_CACHE_TTL` seconds so repeated
    calls within the TTL window don't re-read/re-parse the JSON files.
    """
    base = decisions_dir or _default_decisions_dir()
    key = str(base)
    now = time.time()
    if not force_reload and key in _DECISIONS_CACHE:
        ts, cached = _DECISIONS_CACHE[key]
        if now - ts < _DECISIONS_CACHE_TTL:
            return cached
    merged = _read_and_merge(base)
    _DECISIONS_CACHE[key] = (now, merged)
    return merged


def clear_exclusions_cache() -> None:
    _DECISIONS_CACHE.clear()


def load_excluded_keys(
    mode: str = "confirmed",
    *,
    decisions_dir: Path | None = None,
    source: str | None = None,
) -> set[str]:
    """Return mesa_keys to exclude from queue.

    mode:
      confirmed — human marked primary-source suspicious (lab build-index default)
      all       — any key present in merged decisiones
    """
    src = resolve_primary_source(source)
    decisions = merge_decisions(decisions_dir)
    if not decisions:
        return set()
    if mode == "all":
        return set(decisions)
    return {k for k, v in decisions.items() if _source_confirmed(v, src)}