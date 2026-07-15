"""Field audit helpers for transversal conflictiva queues (stored data only)."""
from __future__ import annotations

from src.modules.review.datasets import dataset_config, normalize_dataset
from src.modules.review.field_array import (
    classify_subcell_array,
    derive_scalar,
    is_confirmed_blank,
)

TOTAL_FIELDS = ("VOTANTES", "URNA", "SUMA_TOTAL")
ARRAY_SOURCE = "e14c"
SCALAR_SOURCES = frozenset({"e14d", "e14t"})


def mesa_key(row: dict) -> str:
    return f"{row['dept']}_{row['mpio']}_{row['zona']}_{row['puesto']}_{row['mesa']}"


def resolve_primary_source(source: str | None = None) -> str:
    if source:
        return source
    return dataset_config()["primary_source"]


def is_scalar_blank(val) -> bool:
    return val is None or val == 0


def classify_scalar_field(val) -> str:
    if val is None:
        return "missing"
    if is_scalar_blank(val):
        return "confirmed_blank"
    if isinstance(val, int) and val > 0:
        return "has_digits"
    return "partial"


def candidate_votes(fields: dict, source: str = ARRAY_SOURCE) -> int:
    c1 = fields.get("C1_CEPEDA")
    c2 = fields.get("C2_ABELARDO")
    if source == ARRAY_SOURCE:
        total = 0
        v1 = derive_scalar(c1)
        v2 = derive_scalar(c2)
        if isinstance(v1, int):
            total += v1
        if isinstance(v2, int):
            total += v2
        return total
    return int(c1 or 0) + int(c2 or 0)


def field_audit(fields: dict, source: str = ARRAY_SOURCE) -> dict:
    """Audit total fields for one source (E14C arrays or E14D/E14T scalars)."""
    if source in SCALAR_SOURCES:
        return _field_audit_scalar(fields)
    return _field_audit_arrays(fields)


def _field_audit_arrays(fields: dict) -> dict:
    out = {}
    for name in TOTAL_FIELDS:
        arr = fields.get(name)
        out[name] = {
            "stored": arr,
            "class": classify_subcell_array(arr),
            "confirmed_blank": is_confirmed_blank(arr),
        }
    return _audit_summary(out)


def _field_audit_scalar(fields: dict) -> dict:
    out = {}
    for name in TOTAL_FIELDS:
        val = fields.get(name)
        out[name] = {
            "stored": val,
            "class": classify_scalar_field(val),
            "confirmed_blank": is_scalar_blank(val),
        }
    return _audit_summary(out)


def _audit_summary(out: dict) -> dict:
    blank_names = [n for n in TOTAL_FIELDS if out[n]["confirmed_blank"]]
    classes = {out[n]["class"] for n in TOTAL_FIELDS}
    return {
        "fields": out,
        "blank_fields": blank_names,
        "all_three_confirmed_blank": len(blank_names) == 3,
        "combo": ",".join(sorted(blank_names)) if blank_names else "none",
        "has_unreadable": "unreadable" in classes,
        "has_partial": "partial" in classes,
        "has_digits": "has_digits" in classes,
    }


def is_conflictiva(row: dict, source: str | None = None) -> bool:
    src = resolve_primary_source(source)
    src_data = row.get("sources", {}).get(src, {})
    if src_data.get("status") != "ok":
        return False
    fields = src_data.get("fields") or {}
    if candidate_votes(fields, src) <= 0:
        return False
    audit = field_audit(fields, src)
    return bool(audit["blank_fields"])


def is_e14c_conflictiva(row: dict) -> bool:
    return is_conflictiva(row, ARRAY_SOURCE)


def build_index_row(raw_data: dict, source: str | None = None) -> dict | None:
    """Build slim queue index row from cross-validation or mesa_results raw_data."""
    src = resolve_primary_source(source)
    if not is_conflictiva(raw_data, src):
        return None
    fields = raw_data["sources"][src]["fields"]
    audit = field_audit(fields, src)
    stored_key = "stored_arrays" if src == ARRAY_SOURCE else "stored_values"
    return {
        "mesa_key": mesa_key(raw_data),
        "dept": raw_data["dept"],
        "mpio": raw_data["mpio"],
        "zona": raw_data["zona"],
        "puesto": raw_data["puesto"],
        "mesa": raw_data["mesa"],
        "primary_source": src,
        "candidate_votes": candidate_votes(fields, src),
        "blank_fields": audit["blank_fields"],
        "all_three_confirmed_blank": audit["all_three_confirmed_blank"],
        "field_class": {n: audit["fields"][n]["class"] for n in TOTAL_FIELDS},
        stored_key: {n: audit["fields"][n]["stored"] for n in TOTAL_FIELDS},
        # Back-compat for E14C consumers
        "stored_arrays": {n: audit["fields"][n]["stored"] for n in TOTAL_FIELDS},
    }