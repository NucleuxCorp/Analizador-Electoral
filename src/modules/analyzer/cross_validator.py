"""cross_validator.py — Per-source PDF extractor for the cross-mesa validator.

Provides _extract_source(pdf_abs, acta_type) which routes to the correct
extractor for each acta type and normalises the output into a uniform
SourceResult dict consumed by cross_congruence.compare_mesa().

SourceResult shape:
    {
        "status":      "ok" | "not_available" | "extraction_error",
        "fields":      {field_label: int | None},              # integer value per field
        "digits":      {field_label: [d0, d1, d2]},            # raw OCR chars per subcell
        "confidences": {field_label: [float, float, float]},   # per-subcell confidence
        "error":       str | None,                             # populated on extraction_error
    }

Routing:
    "e14c" → _run_e14c()  — wraps debug_sv.e14_worker.process_pdf_task
    "e14t" → _run_e14t()  — wraps debug_sv.e14t_extractor.extract_fields
    "e14d" → _run_e14t() (E14D uses the same physical form as E14T)
    other  → extraction_error
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import TypedDict

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

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


class SourceResult(TypedDict):
    status: str                          # "ok" | "not_available" | "extraction_error"
    fields: dict                         # {label: int | None}
    digits: dict                         # {label: [str|None, str|None, str|None]}
    confidences: dict                    # {label: [float, float, float]}
    top3_digits: dict                    # {label: [[{digit,prob},...], ...]} — CNN top-3 per subcell
    error: str | None


def _not_available() -> SourceResult:
    return SourceResult(status="not_available", fields={}, digits={}, confidences={}, top3_digits={}, error=None)


def _extraction_error(msg: str) -> SourceResult:
    return SourceResult(status="extraction_error", fields={}, digits={}, confidences={}, top3_digits={}, error=msg)


# ---------------------------------------------------------------------------
# Per-process module cache — avoids reloading CNN on every PDF call
# ---------------------------------------------------------------------------

_MOD_CACHE: dict = {}


def _load_once(name: str, path: Path):
    if name not in _MOD_CACHE:
        import importlib.util
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MOD_CACHE[name] = mod
    return _MOD_CACHE[name]


# ---------------------------------------------------------------------------
# E14T runner
# ---------------------------------------------------------------------------

def _run_e14t(pdf_abs: Path) -> dict:
    """Call e14t_extractor.extract_fields() — isolated so tests can patch it."""
    mod = _load_once("e14t_extractor", ROOT / "debug_sv" / "e14t_extractor.py")
    return mod.extract_fields(pdf_abs)


# ---------------------------------------------------------------------------
# E14C runner
# ---------------------------------------------------------------------------

def _run_e14c(pdf_abs: Path) -> dict:
    """Call e14_worker.process_pdf_task() — isolated so tests can patch it."""
    mod = _load_once("e14_worker", ROOT / "debug_sv" / "e14_worker.py")
    mod.init_worker()
    try:
        rel = str(pdf_abs.relative_to(ROOT))
    except ValueError:
        rel = str(pdf_abs)
    return mod.process_pdf_task(rel, False)


# ---------------------------------------------------------------------------
# Digit + confidence extraction helpers
# ---------------------------------------------------------------------------

def _digits_from_e14t(raw: dict) -> dict[str, list[str | None]]:
    """Extract digits map from e14t_extractor result."""
    return raw.get("digits") or {}


def _confidences_from_e14t(raw: dict) -> dict[str, list[float]]:
    """Extract confidences map from e14t_extractor result.

    Falls back to 1.0 per subcell when the key is absent (legacy data).
    """
    stored = raw.get("confidences") or {}
    if stored:
        return stored
    # Legacy: extractor did not return confidences — assume high confidence
    digits = raw.get("digits") or {}
    return {field: [1.0, 1.0, 1.0] for field in digits}


def _digits_from_e14c(raw: dict) -> dict[str, list[str | None]]:
    """Build digits map from e14_worker rows list."""
    rows_by_label: dict[str, list[str]] = {
        r["label"]: r["digits"]
        for r in raw.get("rows", [])
        if "label" in r and "digits" in r
    }
    result: dict[str, list[str | None]] = {}
    for field in VOTE_FIELDS:
        row_digits = rows_by_label.get(field)
        if row_digits and len(row_digits) >= 3:
            result[field] = list(row_digits[:3])
        else:
            result[field] = [None, None, None]
    return result


def _confidences_from_e14c(raw: dict) -> dict[str, list[float]]:
    """Build confidences map from e14_worker rows list.

    Each row may have a "confidences" key: [float, float, float].
    Falls back to 1.0 per subcell when absent (legacy data).
    """
    result: dict[str, list[float]] = {}
    for r in raw.get("rows", []):
        label = r.get("label")
        if label is None:
            continue
        confs = r.get("confidences")
        if confs and len(confs) >= 3:
            result[label] = [float(c) for c in confs[:3]]
        else:
            result[label] = [1.0, 1.0, 1.0]
    # Fill any missing VOTE_FIELDS with default 1.0
    for field in VOTE_FIELDS:
        if field not in result:
            result[field] = [1.0, 1.0, 1.0]
    return result


def _top3_from_e14c(raw: dict) -> dict[str, list[list[dict]]]:
    """Build top3_digits map from e14_worker rows list.

    Each row may have a "top3_digits" key: [[{digit,prob},...], ...] per subcell.
    Falls back to empty lists per subcell when absent (legacy data or EasyOCR).
    """
    result: dict[str, list[list[dict]]] = {}
    for r in raw.get("rows", []):
        label = r.get("label")
        if label is None:
            continue
        top3 = r.get("top3_digits")
        if top3 and len(top3) >= 3:
            result[label] = [list(t) for t in top3[:3]]
        else:
            result[label] = [[], [], []]
    for field in VOTE_FIELDS:
        if field not in result:
            result[field] = [[], [], []]
    return result


# ---------------------------------------------------------------------------
# T3.1 / T3.2 — public interface
# ---------------------------------------------------------------------------

def _extract_source(pdf_abs: Path | None, acta_type: str) -> SourceResult:
    """Extract fields and digits from one PDF, routing by acta_type.

    Args:
        pdf_abs:   Absolute path to the PDF, or None if source not available.
        acta_type: "e14c", "e14t", or "e14d".

    Returns:
        SourceResult with status, fields, digits, and optional error.
    """
    # T3.2 guard: None or missing file → not_available (never raises)
    if pdf_abs is None or not pdf_abs.exists():
        return _not_available()

    if acta_type not in ("e14c", "e14t", "e14d"):
        return _extraction_error(f"unknown_acta_type:{acta_type}")

    try:
        if acta_type in ("e14t", "e14d"):
            # E14D uses the same physical form as E14T — same extractor applies
            raw = _run_e14t(pdf_abs)
            # T3.2: error key present → extraction_error even without exception
            if raw.get("error"):
                return _extraction_error(raw["error"])
            return SourceResult(
                status="ok",
                fields=raw.get("fields") or {},
                digits=_digits_from_e14t(raw),
                confidences=_confidences_from_e14t(raw),
                top3_digits={},
                error=None,
            )

        else:  # e14c
            raw = _run_e14c(pdf_abs)
            # T3.2: error key present → extraction_error
            if "error" in raw:
                return _extraction_error(str(raw["error"]))
            return SourceResult(
                status="ok",
                fields=raw.get("fields") or {},
                digits=_digits_from_e14c(raw),
                confidences=_confidences_from_e14c(raw),
                top3_digits=_top3_from_e14c(raw),
                error=None,
            )

    except Exception as exc:
        return _extraction_error(str(exc))


# ---------------------------------------------------------------------------
# T4.2 — Confidence filter
# ---------------------------------------------------------------------------

CONFIDENCE_THRESHOLD = 0.70


def apply_confidence_filter(
    result: SourceResult,
    threshold: float = CONFIDENCE_THRESHOLD,
) -> SourceResult:
    """Return a copy of result with low-confidence digits replaced by None.

    Subcells where confidence < threshold are masked to None so that
    compare_mesa() treats them as missing data rather than potential
    discrepancies. This distinguishes OCR noise from genuine fraud.

    The original result is NOT mutated; a shallow copy with new digit lists
    is returned so the raw OCR data is preserved for audit purposes.

    Args:
        result:    A SourceResult dict from _extract_source().
        threshold: Minimum confidence to keep a digit (default 0.70).

    Returns:
        New SourceResult with masked digits; confidences unchanged.
    """
    if result["status"] != "ok":
        return result  # nothing to filter for non-ok sources

    digits = result["digits"]
    confidences = result.get("confidences") or {}

    filtered_digits: dict = {}
    for field, digit_list in digits.items():
        conf_list = confidences.get(field, [1.0, 1.0, 1.0])
        new_digits = []
        for i, digit in enumerate(digit_list):
            conf = conf_list[i] if i < len(conf_list) else 1.0
            new_digits.append(digit if conf >= threshold else None)
        filtered_digits[field] = new_digits

    return SourceResult(
        status=result["status"],
        fields=result["fields"],
        digits=filtered_digits,
        confidences=confidences,
        top3_digits=result.get("top3_digits") or {},
        error=result["error"],
    )


# ---------------------------------------------------------------------------
# T4.2 — Per-acta arithmetic check
# ---------------------------------------------------------------------------

# Fields that should sum to URNA
_VOTE_ADDENDS = ("C1_CEPEDA", "C2_ABELARDO", "BLANCO", "NULOS", "NO_MARCADOS")


def check_arithmetic(fields: dict) -> dict:
    """Check per-acta arithmetic: C1+C2+BLANCO+NULOS+NO_MARCADOS == URNA.

    Args:
        fields: {label: int | None} dict from a SourceResult.

    Returns:
        {
            "ok":        bool | None,  # None when urna or any addend is missing
            "sum_votes": int | None,   # sum of the five addends (None if any missing)
            "urna":      int | None,   # expected total from the URNA field
            "delta":     int | None,   # sum_votes - urna (None if either is None)
        }
    """
    urna = fields.get("URNA")
    addend_values = [fields.get(f) for f in _VOTE_ADDENDS]

    if urna is None or any(v is None for v in addend_values):
        return {"ok": None, "sum_votes": None, "urna": urna, "delta": None}

    sum_votes = sum(addend_values)  # type: ignore[arg-type]
    delta = sum_votes - urna
    ok = delta == 0

    return {"ok": ok, "sum_votes": sum_votes, "urna": urna, "delta": delta}
