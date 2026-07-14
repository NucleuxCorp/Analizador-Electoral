"""Concordance classification and Jensen-Shannon Divergence computation.

Three public functions:
    classify_concordance(values) -> str
    compute_is_outlier(values, e14_type, concordance_state) -> bool
    compute_jsd(dist_a, dist_b) -> float | None
"""

from __future__ import annotations

import math
from typing import Sequence


# ---------------------------------------------------------------------------
# Concordance states (priority order: highest priority first)
# ---------------------------------------------------------------------------
# discrepante      — ≥2 non-None values, no pair equal
# divergente       — exactly 2 non-None equal, 1 differs (2-vs-1 split)
# concordancia_parcial — ≥2 non-None, ≥1 pair equal, not all equal
#                        (covers [4,4,None] when only 2 sources exist)
# armonico         — all non-None values equal (and ≥2 present)
# otros            — fewer than 2 non-None values


def classify_concordance(values: list[int | None]) -> str:
    """Classify a list of three source values into one of five concordance states.

    Args:
        values: List of three integers or None, ordered [e14c, e14t, e14d].

    Returns:
        One of: "discrepante", "divergente", "concordancia_parcial",
        "armonico", "otros".
    """
    non_none = [v for v in values if v is not None]
    n = len(non_none)

    if n < 2:
        return "otros"

    # Check all equal
    all_equal = len(set(non_none)) == 1

    if n == 2:
        if all_equal:
            # Two sources, both match — partial because third is absent
            return "concordancia_parcial"
        else:
            # Two sources, they differ — no pair equal, so discrepante
            return "discrepante"

    # n == 3 from here
    if all_equal:
        return "armonico"

    # Count pairs
    a, b, c = non_none[0], non_none[1], non_none[2]
    pairs_equal = (a == b) + (b == c) + (a == c)

    if pairs_equal == 0:
        # No pair equal — all three differ
        return "discrepante"

    if pairs_equal >= 1:
        # At least one pair equal, not all equal
        # With 3 values, if exactly 1 pair equals → 2-vs-1 → divergente
        # (pairs_equal can only be 1 here since all_equal is False)
        return "divergente"

    # Unreachable, but keep for safety
    return "otros"  # pragma: no cover


# ---------------------------------------------------------------------------
# Outlier detection
# ---------------------------------------------------------------------------

_TYPE_POS: dict[str, int] = {"e14c": 0, "e14t": 1, "e14d": 2}


def compute_is_outlier(
    values: list[int | None],
    e14_type: str,
    concordance_state: str,
) -> bool:
    """Return True if this e14_type's value is the outlier for the given state.

    Rules by state:
        armonico / otros     → always False
        discrepante          → always True (all sources conflict)
        concordancia_parcial → True for the source whose value is None
        divergente           → True for the minority source (2-vs-1)

    Args:
        values: [e14c_val, e14t_val, e14d_val] — may contain None.
        e14_type: One of "e14c", "e14t", "e14d".
        concordance_state: Output of classify_concordance().

    Returns:
        True if this source is the outlier, False otherwise.
    """
    if concordance_state in ("armonico", "otros"):
        return False

    pos = _TYPE_POS.get(e14_type)
    if pos is None or pos >= len(values):
        return False

    if concordance_state == "discrepante":
        return True

    my_val = values[pos]

    if concordance_state == "concordancia_parcial":
        return my_val is None

    if concordance_state == "divergente":
        non_none = [v for v in values if v is not None]
        if len(non_none) < 2:
            return False
        # Majority value is the one that appears twice
        from collections import Counter
        majority_val = Counter(non_none).most_common(1)[0][0]
        return my_val != majority_val

    return False


# ---------------------------------------------------------------------------
# Jensen-Shannon Divergence
# ---------------------------------------------------------------------------

_N_CLASSES = 10  # digits 0–9
_N_TOP3 = 3
_N_RESIDUAL = _N_CLASSES - _N_TOP3  # 7 remaining classes


def _expand_to_full_distribution(top3: list[float]) -> list[float] | None:
    """Expand a top-3 probability list to a full 10-class distribution.

    The top-3 values are assigned to the first 3 class slots.
    Residual probability (1 - sum(top3)) is distributed equally across
    the remaining 7 class slots.

    Returns None if the input is invalid (negative values, empty, etc.).
    """
    if not top3:
        return None
    if any(p < 0 for p in top3):
        return None

    total = sum(top3)
    residual = max(0.0, 1.0 - total)
    residual_per_class = residual / _N_RESIDUAL

    dist = list(top3[:_N_TOP3])
    # Pad to 3 if fewer than 3 values provided
    while len(dist) < _N_TOP3:
        dist.append(0.0)

    dist.extend([residual_per_class] * _N_RESIDUAL)
    return dist


def _jsd_from_full_dists(p: list[float], q: list[float]) -> float:
    """Compute JSD using log base 2 on two full distributions.

    JSD(P||Q) = (KL(P||M) + KL(Q||M)) / 2  where M = (P+Q)/2.
    With log base 2, result is in [0, 1].
    """
    m = [(pi + qi) / 2.0 for pi, qi in zip(p, q)]

    def kl(src: list[float], ref: list[float]) -> float:
        total = 0.0
        for si, ri in zip(src, ref):
            if si > 0.0 and ri > 0.0:
                total += si * math.log2(si / ri)
            # si == 0 → term is 0 (convention: 0 * log(0) = 0)
            # si > 0, ri == 0 → undefined; JSD with mixture M prevents ri==0
            #   as long as si > 0 somewhere; safe by construction
        return total

    return (kl(p, m) + kl(q, m)) / 2.0


def compute_jsd(dist_a: list[float] | None, dist_b: list[float] | None) -> float | None:
    """Compute Jensen-Shannon Divergence between two top-3 probability lists.

    Each input is a list of up to 3 probabilities (highest first).
    Residual probability is distributed equally across the remaining 7 of
    10 digit classes (0–9), giving a full 10-class distribution before
    computing JSD with log base 2.

    Args:
        dist_a: Top-3 probabilities for distribution A, or None/empty.
        dist_b: Top-3 probabilities for distribution B, or None/empty.

    Returns:
        JSD in [0.0, 1.0], or None if either input is empty, None,
        or contains negative values.
    """
    if not dist_a or not dist_b:
        return None

    full_a = _expand_to_full_distribution(dist_a)
    full_b = _expand_to_full_distribution(dist_b)

    if full_a is None or full_b is None:
        return None

    result = _jsd_from_full_dists(full_a, full_b)
    # Clamp to [0, 1] to guard against floating-point rounding at extremes
    return float(max(0.0, min(1.0, result)))
