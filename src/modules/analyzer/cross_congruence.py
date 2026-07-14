"""Cross-source congruence engine for mesa-level digit comparison.

Implements position-first null semantics: a null value (source unavailable or
extraction failed) is a genuine disagreement, never transparent agreement.

The 9 tracked fields: VOTANTES, URNA, INCINER, C1_CEPEDA, C2_ABELARDO,
BLANCO, NULOS, NO_MARCADOS, SUMA_TOTAL.

Each field has 3 subcell positions: idx 0 = hundreds, 1 = tens, 2 = units.
"""
from __future__ import annotations

from typing import TypedDict

# ---------------------------------------------------------------------------
# Type aliases (TypedDicts for structured return values)
# ---------------------------------------------------------------------------

VOTE_FIELDS = (
    "VOTANTES",
    "URNA",
    "INCINER",
    "C1_CEPEDA",
    "C2_ABELARDO",
    "BLANCO",
    "NULOS",
    "NO_MARCADOS",
    "SUMA_TOTAL",
)


class SubcellResult(TypedDict):
    """Result of comparing one subcell position across three sources."""

    score: int          # 0–3: count of positions matching the modal value
    agree: bool         # True only when score==3 AND all values are non-null
    values: list        # [e14c_digit, e14t_digit, e14d_digit] (None if missing)
    modal: str | None   # most common non-null value; None when all are null


class FieldResult(TypedDict):
    """Result of comparing one field (3 subcell positions) across three sources."""

    field: str
    score_str: str      # minimum score per subcell expressed as "N/3"
    subcells: list[SubcellResult]


class MesaCongruence(TypedDict):
    """Full congruence result for one mesa."""

    sources_ok: int
    fields: dict[str, FieldResult]
    summary: dict


# ---------------------------------------------------------------------------
# T2.1 — compare_subcell
# ---------------------------------------------------------------------------

def compare_subcell(
    c: str | None,
    t: str | None,
    d: str | None,
) -> SubcellResult:
    """Compare one subcell digit position across E14C, E14T, E14D.

    Null semantics (position-first):
    - None is a distinct token — it NEVER collapses into agreement with any
      other value, including another None.
    - modal_value = the non-null value with the highest count; ties broken by
      e14c > e14t > e14d priority.
    - score = count of positions equal to modal_value when modal is non-null,
      else 0.
    - agree = True only when score == 3 AND ALL three positions are non-null.

    Args:
        c: E14C digit string ("0"–"9") or None.
        t: E14T digit string ("0"–"9") or None.
        d: E14D digit string ("0"–"9") or None.

    Returns:
        SubcellResult dict.
    """
    values: list[str | None] = [c, t, d]

    # Count non-null values per distinct string
    counts: dict[str, int] = {}
    for v in values:
        if v is not None:
            counts[v] = counts.get(v, 0) + 1

    if not counts:
        # All null
        return SubcellResult(score=0, agree=False, values=values, modal=None)

    # Find the modal: highest count; tiebreak by order e14c > e14t > e14d
    max_count = max(counts.values())
    # candidates with the same max count, preserve e14c > e14t > e14d order
    modal: str | None = None
    for v in values:
        if v is not None and counts[v] == max_count:
            modal = v
            break

    score = sum(1 for v in values if v == modal)
    agree = score == 3 and all(v is not None for v in values)

    return SubcellResult(score=score, agree=agree, values=values, modal=modal)


# ---------------------------------------------------------------------------
# T2.2 — compare_field
# ---------------------------------------------------------------------------

def compare_field(
    field_name: str,
    c_digits: list[str | None] | None,
    t_digits: list[str | None] | None,
    d_digits: list[str | None] | None,
) -> FieldResult:
    """Compare one field (3 subcell positions) across three sources.

    Args:
        field_name: VOTANTES, URNA, etc.
        c_digits: List of 3 digit strings from E14C, or None if source missing.
        t_digits: List of 3 digit strings from E14T, or None if source missing.
        d_digits: List of 3 digit strings from E14D, or None if source missing.

    Returns:
        FieldResult dict. score_str reflects the MINIMUM subcell score as "N/3".
    """
    # Expand None source to [None, None, None]
    c = c_digits if c_digits is not None else [None, None, None]
    t = t_digits if t_digits is not None else [None, None, None]
    d = d_digits if d_digits is not None else [None, None, None]

    subcells: list[SubcellResult] = []
    for idx in range(3):
        subcells.append(compare_subcell(c[idx], t[idx], d[idx]))

    min_score = min(sc["score"] for sc in subcells)
    score_str = f"{min_score}/3"

    return FieldResult(field=field_name, score_str=score_str, subcells=subcells)


# ---------------------------------------------------------------------------
# T2.3 — compare_mesa
# ---------------------------------------------------------------------------

def compare_mesa(
    e14c_result: dict,
    e14t_result: dict,
    e14d_result: dict,
) -> MesaCongruence:
    """Compute full congruence across all 9 fields for one mesa.

    Each source result has the shape:
        {
            "status": "ok" | "not_available" | "extraction_error",
            "digits": {field_label: [str|None, str|None, str|None]} | {}
        }

    When status != "ok", all digits for that source are treated as None.

    Returns:
        MesaCongruence with fields for each of the 9 VOTE_FIELDS and a summary.
    """

    def _get_digits(result: dict, field: str) -> list[str | None] | None:
        """Return digit list for a field, or None if source is not ok."""
        if result.get("status") != "ok":
            return None
        digits_map = result.get("digits") or {}
        raw = digits_map.get(field)
        if raw is None:
            return [None, None, None]
        # Normalize: map empty string "" to None
        return [v if v != "" else None for v in raw]

    sources_ok = sum(
        1 for r in (e14c_result, e14t_result, e14d_result)
        if r.get("status") == "ok"
    )

    fields: dict[str, FieldResult] = {}
    discrepant_fields: list[str] = []
    fields_3_3 = 0

    cross_discrepant_fields: list[str] = []

    for field in VOTE_FIELDS:
        c_d = _get_digits(e14c_result, field)
        t_d = _get_digits(e14t_result, field)
        d_d = _get_digits(e14d_result, field)

        field_result = compare_field(field, c_d, t_d, d_d)
        fields[field] = field_result

        # A field is 3/3 only when ALL subcells score 3
        all_agree = all(sc["agree"] for sc in field_result["subcells"])
        if all_agree:
            fields_3_3 += 1
        else:
            discrepant_fields.append(field)

        # Cross-source conflict: ≥2 available sources with differing values in any subcell
        if sources_ok >= 2:
            for sc in field_result["subcells"]:
                non_null = [v for v in sc["values"] if v is not None]
                if len(non_null) >= 2 and len(set(non_null)) > 1:
                    cross_discrepant_fields.append(field)
                    break

    any_discrepancy = len(discrepant_fields) > 0
    # True only when ≥2 sources are available and they actually report different digits
    cross_discrepancy = len(cross_discrepant_fields) > 0

    summary = {
        "sources_ok": sources_ok,
        "fields_3_3": fields_3_3,
        "any_discrepancy": any_discrepancy,
        "discrepant_fields": discrepant_fields,
        "cross_discrepancy": cross_discrepancy,
        "cross_discrepant_fields": cross_discrepant_fields,
    }

    return MesaCongruence(sources_ok=sources_ok, fields=fields, summary=summary)


# ---------------------------------------------------------------------------
# Quick manual test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Smoke test: 3 identical sources
    e14c = {"status": "ok", "digits": {"VOTANTES": ["2", "5", "0"]}}
    e14t = {"status": "ok", "digits": {"VOTANTES": ["2", "5", "0"]}}
    e14d = {"status": "ok", "digits": {"VOTANTES": ["2", "5", "0"]}}
    result = compare_mesa(e14c, e14t, e14d)
    print("sources_ok:", result["summary"]["sources_ok"])
    print("fields_3_3:", result["summary"]["fields_3_3"])
    print("any_discrepancy:", result["summary"]["any_discrepancy"])

    # Null disagreement
    r = compare_subcell("3", "3", None)
    print("score (3,3,None):", r["score"], "agree:", r["agree"])

    # All null
    r2 = compare_subcell(None, None, None)
    print("score (None,None,None):", r2["score"], "modal:", r2["modal"])
