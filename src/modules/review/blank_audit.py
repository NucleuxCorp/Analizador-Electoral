"""Classify transversal blank alerts for audit and queue filtering."""
from __future__ import annotations

from typing import Any

from src.modules.review.alerts import AUTO_CLASS, TOTAL_FIELDS
from src.modules.review.field_audit import is_scalar_blank

FIELD_BUCKET_LABELS = {
    "auto_false_positive": "AUTO falso (E14C blanco, T/D con dígitos)",
    "auto_consistent": "AUTO consistente (blanco en E14C y T/D)",
    "partial_digit": "Partial dígito (hueco OCR, no blanco)",
    "blank_uncertainty": "Incertidumbre blanco (missing/unreadable)",
    "has_digits": "E14C con dígitos (sin alerta blanco)",
    "other": "Otro",
}

MESA_BUCKET_LABELS = {
    "queue_eligible": "Cola blancos (incertidumbre real)",
    "excluded_partial_only": "Excluida: solo partial dígito",
    "excluded_auto_only": "Excluida: solo AUTO consistente",
    "excluded_no_blank_work": "Excluida: sin trabajo de blanco",
}


def _scalar_positive(val: Any) -> bool:
    return isinstance(val, int) and val > 0


def classify_total_field(
    field: str,
    *,
    e14c_class: str,
    e14t_fields: dict | None,
    e14d_fields: dict | None,
) -> str:
    """Per-field bucket for one of VOTANTES / URNA / SUMA_TOTAL."""
    t_fields = e14t_fields or {}
    d_fields = e14d_fields or {}
    t_pos = _scalar_positive(t_fields.get(field))
    d_pos = _scalar_positive(d_fields.get(field))

    if e14c_class == AUTO_CLASS:
        if t_pos or d_pos:
            return "auto_false_positive"
        return "auto_consistent"
    if e14c_class == "partial":
        return "partial_digit"
    if e14c_class in ("missing", "unreadable"):
        return "blank_uncertainty"
    if e14c_class == "has_digits":
        return "has_digits"
    return "other"


def classify_mesa(
    index_row: dict,
    cross_row: dict | None,
) -> dict[str, Any]:
    """Classify one index row; cross_row optional (skips T/D checks if missing)."""
    mk = index_row["mesa_key"]
    field_class = index_row.get("field_class") or {}
    blank_fields = index_row.get("blank_fields") or []

    sources = (cross_row or {}).get("sources") or {}
    e14t = (sources.get("e14t") or {}).get("fields") or {}
    e14d = (sources.get("e14d") or {}).get("fields") or {}

    per_field: dict[str, str] = {}
    for field in TOTAL_FIELDS:
        per_field[field] = classify_total_field(
            field,
            e14c_class=field_class.get(field, "missing"),
            e14t_fields=e14t,
            e14d_fields=e14d,
        )

    has_auto_fp = any(per_field[f] == "auto_false_positive" for f in TOTAL_FIELDS)
    has_uncertainty = any(per_field[f] == "blank_uncertainty" for f in TOTAL_FIELDS)
    has_partial_digit = any(per_field[f] == "partial_digit" for f in TOTAL_FIELDS)
    has_auto_consistent = any(per_field[f] == "auto_consistent" for f in TOTAL_FIELDS)

    # Current panel human = partial + missing + unreadable (all human classes)
    current_human_fields = [
        f for f in TOTAL_FIELDS
        if field_class.get(f) in ("partial", "missing", "unreadable")
    ]
    human_only_partial = bool(current_human_fields) and all(
        field_class.get(f) == "partial" for f in current_human_fields
    )

    queue_eligible = has_auto_fp or has_uncertainty

    if queue_eligible:
        mesa_bucket = "queue_eligible"
    elif human_only_partial and not has_auto_fp and not has_uncertainty:
        mesa_bucket = "excluded_partial_only"
    elif has_auto_consistent and not has_partial_digit and not has_uncertainty and not has_auto_fp:
        mesa_bucket = "excluded_auto_only"
    else:
        mesa_bucket = "excluded_no_blank_work"

    return {
        "mesa_key": mk,
        "dept": index_row.get("dept"),
        "zona": index_row.get("zona"),
        "puesto": index_row.get("puesto"),
        "mesa": index_row.get("mesa"),
        "candidate_votes": index_row.get("candidate_votes"),
        "blank_fields": blank_fields,
        "field_class": field_class,
        "per_field": per_field,
        "current_human_fields": current_human_fields,
        "human_only_partial": human_only_partial,
        "mesa_bucket": mesa_bucket,
        "flags": {
            "auto_false_positive": has_auto_fp,
            "blank_uncertainty": has_uncertainty,
            "partial_digit": has_partial_digit,
            "auto_consistent": has_auto_consistent,
            "queue_eligible": queue_eligible,
        },
    }


def aggregate_audit(rows: list[dict]) -> dict[str, Any]:
    """Summarize classified mesa rows."""
    n = len(rows)
    field_counts = {k: 0 for k in FIELD_BUCKET_LABELS}
    mesa_bucket_counts = {k: 0 for k in MESA_BUCKET_LABELS}
    flags = {
        "auto_false_positive": 0,
        "blank_uncertainty": 0,
        "partial_digit": 0,
        "human_only_partial": 0,
        "queue_eligible": 0,
        "current_panel_pending": 0,
    }
    auto_fp_fields = {f: 0 for f in TOTAL_FIELDS}
    partial_fields = {f: 0 for f in TOTAL_FIELDS}

    for row in rows:
        mesa_bucket_counts[row["mesa_bucket"]] = mesa_bucket_counts.get(row["mesa_bucket"], 0) + 1
        fc = row["flags"]
        if fc["auto_false_positive"]:
            flags["auto_false_positive"] += 1
        if fc["blank_uncertainty"]:
            flags["blank_uncertainty"] += 1
        if fc["partial_digit"]:
            flags["partial_digit"] += 1
        if row["human_only_partial"]:
            flags["human_only_partial"] += 1
        if fc["queue_eligible"]:
            flags["queue_eligible"] += 1
        if row["current_human_fields"]:
            flags["current_panel_pending"] += 1

        for field, bucket in row["per_field"].items():
            field_counts[bucket] = field_counts.get(bucket, 0) + 1
            if bucket == "auto_false_positive":
                auto_fp_fields[field] += 1
            if bucket == "partial_digit":
                partial_fields[field] += 1

    return {
        "mesas_total": n,
        "mesa_buckets": mesa_bucket_counts,
        "mesa_flags": flags,
        "field_slot_counts": field_counts,
        "auto_false_positive_by_field": auto_fp_fields,
        "partial_digit_by_field": partial_fields,
    }