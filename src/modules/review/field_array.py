"""
field_array.py

Canonical helpers for working with E14C 3-subcell digit arrays.

SCOPE: E14C fields only. E14T fields are scalars (a different pipeline) —
applying derive_scalar or is_confirmed_blank to E14T values would be a defect.

Array semantics (authoritative definition per user decision #3):
  [None, None, None]   → cell_has_ink() returned False for all subcells (truly blank)
  ["?", "?", "?"]      → ink detected but OCR could not read any subcell
  ["9", "?", None]     → partial: one read, one ink-unreadable, one no-ink
  ["0", None, None]    → digit "0" in first subcell (distinguishable from blank)
  ["9", "1", "2"]      → full read; downstream derives integer 912

The integer 0 is NEVER stored in the array. String "0" is the correct
representation for a written zero digit.
"""
from __future__ import annotations

from typing import Union


def derive_scalar(arr) -> Union[int, list, None]:
    """Derive an arithmetic scalar from a 3-subcell digits array."""
    if not isinstance(arr, list) or not arr:
        return None
    if all(isinstance(d, str) and d.isdigit() for d in arr):
        return int("".join(arr))
    return arr


def is_confirmed_blank(arr) -> bool:
    """Return True only when every subcell has no ink (all None)."""
    return isinstance(arr, list) and len(arr) > 0 and all(d is None for d in arr)


def classify_subcell_array(arr) -> str:
    """Classify an E14C 3-subcell array for audit/revalidation."""
    if not isinstance(arr, list) or not arr:
        return "missing"
    if is_confirmed_blank(arr):
        return "confirmed_blank"
    if all(d == "?" for d in arr):
        return "unreadable"
    if all(isinstance(d, str) and d.isdigit() for d in arr):
        return "has_digits"
    return "partial"