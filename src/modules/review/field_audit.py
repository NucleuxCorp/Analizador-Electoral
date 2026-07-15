"""Field audit helpers for E14C conflictiva queue (stored data only, no reprocess)."""
from __future__ import annotations

from src.modules.review.field_array import (
    classify_subcell_array,
    derive_scalar,
    is_confirmed_blank,
)

TOTAL_FIELDS = ("VOTANTES", "URNA", "SUMA_TOTAL")


def mesa_key(row: dict) -> str:
    return f"{row['dept']}_{row['mpio']}_{row['zona']}_{row['puesto']}_{row['mesa']}"


def candidate_votes(fields: dict) -> int:
    c1 = derive_scalar(fields.get("C1_CEPEDA"))
    c2 = derive_scalar(fields.get("C2_ABELARDO"))
    total = 0
    if isinstance(c1, int):
        total += c1
    if isinstance(c2, int):
        total += c2
    return total


def is_e14c_conflictiva(row: dict) -> bool:
    e14c = row.get("sources", {}).get("e14c", {})
    if e14c.get("status") != "ok":
        return False
    fields = e14c.get("fields", {})
    if candidate_votes(fields) <= 0:
        return False
    return any(is_confirmed_blank(fields.get(f)) for f in TOTAL_FIELDS)


def field_audit(fields: dict) -> dict:
    out = {}
    for name in TOTAL_FIELDS:
        arr = fields.get(name)
        out[name] = {
            "stored": arr,
            "class": classify_subcell_array(arr),
            "confirmed_blank": is_confirmed_blank(arr),
        }
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


def build_index_row(raw_data: dict) -> dict | None:
    """Build slim queue index row from cross-validation or mesa_results raw_data."""
    if not is_e14c_conflictiva(raw_data):
        return None
    fields = raw_data["sources"]["e14c"]["fields"]
    audit = field_audit(fields)
    return {
        "mesa_key": mesa_key(raw_data),
        "dept": raw_data["dept"],
        "mpio": raw_data["mpio"],
        "zona": raw_data["zona"],
        "puesto": raw_data["puesto"],
        "mesa": raw_data["mesa"],
        "candidate_votes": candidate_votes(fields),
        "blank_fields": audit["blank_fields"],
        "all_three_confirmed_blank": audit["all_three_confirmed_blank"],
        "field_class": {n: audit["fields"][n]["class"] for n in TOTAL_FIELDS},
        "stored_arrays": {n: audit["fields"][n]["stored"] for n in TOTAL_FIELDS},
    }