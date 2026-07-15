"""Compatible alert model for transversal visual review (stored data only)."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

TOTAL_FIELDS = ("VOTANTES", "URNA", "SUMA_TOTAL")
FIELD_LABELS = {
    "VOTANTES": "Total votantes",
    "URNA": "Total urna",
    "SUMA_TOTAL": "Suma total votos",
}
HUMAN_CLASSES = frozenset({"partial", "unreadable", "missing"})
AUTO_CLASS = "confirmed_blank"
REAL_BLANK_CLASS = AUTO_CLASS

# Temporary: transversal panel shows only real blanks on critical totals (no partial).
TRANSVERSAL_REAL_BLANK_ONLY = True

SOURCES = ("e14c", "e14d", "e14t")
SOURCE_LABELS = {"e14t": "E14T", "e14d": "E14D", "e14c": "E14C"}
SOURCE_COLORS = {"e14t": "#2563eb", "e14d": "#dc2626", "e14c": "#16a34a"}

CLASS_MSG = {
    "confirmed_blank": "Blanco real en campo crítico (sin tinta detectada en E14C).",
    "partial": "Celda parcial (mezcla de tinta, ? o vacío) — requiere criterio humano.",
    "unreadable": "Tinta presente pero ilegible (?) — requiere criterio humano.",
    "missing": "Sin dato extraído — requiere criterio humano.",
    "has_digits": "Dígitos leídos — no es alerta de vacío.",
}

SourceAvailableFn = Callable[[str, str], bool]


def _default_source_available(_mesa_key: str, _src: str) -> bool:
    return False


def real_blank_fields(field_class: dict) -> list[str]:
    """Critical total fields classified as confirmed blank (real blank on E14C)."""
    return [f for f in TOTAL_FIELDS if field_class.get(f) == REAL_BLANK_CLASS]


def build_mesa_alert_package(
    index_row: dict,
    mesa_key: str | None = None,
    *,
    source_available: SourceAvailableFn | None = None,
) -> dict[str, Any]:
    """Build alert package for one mesa from stored field_class."""
    avail = source_available or _default_source_available
    mk = mesa_key or index_row.get("mesa_key", "")
    field_class = index_row.get("field_class") or {}
    auto: list[dict] = []
    human: list[dict] = []

    if TRANSVERSAL_REAL_BLANK_ONLY:
        for field in TOTAL_FIELDS:
            cls = field_class.get(field, "missing")
            if cls != REAL_BLANK_CLASS:
                continue
            auto.append({
                "field": field,
                "field_label": FIELD_LABELS[field],
                "class": cls,
                "msg": CLASS_MSG.get(cls, cls),
                "stored": (index_row.get("stored_arrays") or {}).get(field),
                "tier": "real_blank",
            })
    else:
        for field in TOTAL_FIELDS:
            cls = field_class.get(field, "missing")
            base = {
                "field": field,
                "field_label": FIELD_LABELS[field],
                "class": cls,
                "msg": CLASS_MSG.get(cls, cls),
                "stored": (index_row.get("stored_arrays") or {}).get(field),
            }
            if cls == AUTO_CLASS:
                auto.append({**base, "tier": "auto"})
                continue
            if cls not in HUMAN_CLASSES:
                continue
            sources = []
            for src in SOURCES:
                if avail(mk, src):
                    sources.append({
                        "src": src,
                        "label": SOURCE_LABELS[src],
                        "color": SOURCE_COLORS[src],
                    })
            human.append({
                **base,
                "tier": "human",
                "sources": sources,
                "decision_key": field,
            })

    real_blank_count = len(auto)
    return {
        "mesa_key": mk,
        "candidate_votes": index_row.get("candidate_votes"),
        "blank_fields": index_row.get("blank_fields") or [],
        "auto": auto,
        "human": human,
        "real_blank_count": real_blank_count,
        "pending_human_count": len(human),
    }


def build_alert_index(
    index_rows: list[dict],
    *,
    source_available: SourceAvailableFn | None = None,
) -> tuple[dict[str, dict], dict[str, int]]:
    """Return (alert_index by mesa_key, aggregate stats)."""
    index: dict[str, dict] = {}
    stats = {
        "mesas": len(index_rows),
        "mesas_human_review": 0,
        "mesas_auto_only": 0,
        "human_alerts_by_field": {f: 0 for f in TOTAL_FIELDS},
        "auto_fields_total": 0,
    }
    for row in index_rows:
        mk = row["mesa_key"]
        pkg = build_mesa_alert_package(row, mk, source_available=source_available)
        index[mk] = pkg
        if pkg["human"]:
            stats["mesas_human_review"] += 1
            for h in pkg["human"]:
                stats["human_alerts_by_field"][h["field"]] += 1
        else:
            stats["mesas_auto_only"] += 1
        stats["auto_fields_total"] += len(pkg["auto"])
    return index, stats