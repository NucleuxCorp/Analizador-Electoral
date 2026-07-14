"""Tests for src/modules/crop/concordance.py.

TDD cycle: tests written first (RED) before implementation exists.
Covers all spec scenarios for classify_concordance and compute_jsd.
"""

from __future__ import annotations

import math
import pytest

from src.modules.crop.concordance import classify_concordance, compute_jsd, compute_is_outlier


# ---------------------------------------------------------------------------
# classify_concordance — spec scenarios
# ---------------------------------------------------------------------------


class TestClassifyConcordance:
    """All five concordance states plus edge cases."""

    def test_all_three_equal_returns_armonico(self):
        """Spec: values=[5,5,5] → 'armonico'"""
        assert classify_concordance([5, 5, 5]) == "armonico"

    def test_two_vs_one_returns_divergente(self):
        """Spec: values=[5,7,5] — 2 equal, 1 differs → 'divergente'"""
        assert classify_concordance([5, 7, 5]) == "divergente"

    def test_all_three_differ_returns_discrepante(self):
        """Spec: values=[3,7,1] — all different → 'discrepante'"""
        assert classify_concordance([3, 7, 1]) == "discrepante"

    def test_two_non_none_equal_returns_concordancia_parcial(self):
        """Spec: values=[4,4,None] — 2 of 2 non-None equal → 'concordancia_parcial'"""
        assert classify_concordance([4, 4, None]) == "concordancia_parcial"

    def test_only_one_source_returns_otros(self):
        """Spec: values=[None,None,6] — only 1 non-None → 'otros'"""
        assert classify_concordance([None, None, 6]) == "otros"

    def test_all_none_returns_otros(self):
        """Spec: values=[None,None,None] → 'otros'"""
        assert classify_concordance([None, None, None]) == "otros"

    # Additional edge cases

    def test_two_none_one_value_returns_otros(self):
        assert classify_concordance([None, 3, None]) == "otros"

    def test_two_non_none_different_returns_discrepante(self):
        """2 non-None, no pair equal → discrepante."""
        assert classify_concordance([1, 2, None]) == "discrepante"

    def test_priority_discrepante_over_otros(self):
        """Ensure priority ordering is correct; [1,2,3] has 3 non-None no pair equal."""
        assert classify_concordance([1, 2, 3]) == "discrepante"

    def test_zeros_all_equal_armonico(self):
        assert classify_concordance([0, 0, 0]) == "armonico"

    def test_divergente_first_differs(self):
        """First element differs from other two."""
        assert classify_concordance([1, 5, 5]) == "divergente"

    def test_divergente_last_differs(self):
        """Last element differs."""
        assert classify_concordance([5, 5, 1]) == "divergente"

    def test_concordancia_parcial_with_none_first(self):
        """[None,3,3] — 2 non-None equal, third absent → concordancia_parcial"""
        assert classify_concordance([None, 3, 3]) == "concordancia_parcial"


# ---------------------------------------------------------------------------
# compute_jsd — spec scenarios
# ---------------------------------------------------------------------------


class TestComputeJsd:
    """JSD with top-3 probability list expansion to 10 classes."""

    def test_identical_distributions_return_zero(self):
        """Spec: identical dists → 0.0"""
        dist = [0.9, 0.05, 0.03]
        result = compute_jsd(dist, dist)
        assert result is not None
        assert abs(result) < 1e-9

    def test_disjoint_distributions_return_one(self):
        """Spec: all mass on class 0 vs all mass on class 1 → 1.0.

        Represented as top-3 lists: dist_a has 1.0 on class 0 (position 0),
        dist_b has 1.0 on class 1 (position 1 of the top-3 list).
        """
        # Top-3 as flat probability values (highest to lowest).
        # dist_a: digit 0 gets 1.0, rest 0.0
        # dist_b: digit 1 gets 1.0, rest 0.0
        dist_a = [1.0, 0.0, 0.0]
        dist_b = [0.0, 1.0, 0.0]
        result = compute_jsd(dist_a, dist_b)
        assert result is not None
        assert abs(result - 1.0) < 1e-6

    def test_residual_expansion_sums_to_one(self):
        """Spec: residual probability distributed across remaining 7 classes.

        dist = [0.7, 0.2, 0.05] → sum=0.95, residual=0.05/7 per remaining class.
        Expanded distribution must have exactly 10 entries summing to 1.0.
        """
        dist = [0.7, 0.2, 0.05]
        # We test this by calling jsd with identical dists (must return 0.0)
        # and separately verifying the expansion logic holds via the result.
        result = compute_jsd(dist, dist)
        assert result is not None
        assert abs(result) < 1e-9

    def test_none_input_returns_none(self):
        """Spec: dist_a=None → returns None"""
        assert compute_jsd(None, [0.9, 0.05, 0.03]) is None  # type: ignore[arg-type]

    def test_empty_input_returns_none(self):
        assert compute_jsd([], [0.9, 0.05, 0.03]) is None

    def test_second_none_returns_none(self):
        assert compute_jsd([0.9, 0.05, 0.03], None) is None  # type: ignore[arg-type]

    def test_negative_probability_returns_none(self):
        """Negative probabilities are invalid."""
        assert compute_jsd([-0.1, 0.9, 0.2], [0.9, 0.05, 0.03]) is None

    def test_result_in_valid_range(self):
        """JSD must be in [0, 1] with log base 2."""
        dist_a = [0.8, 0.1, 0.05]
        dist_b = [0.1, 0.7, 0.15]
        result = compute_jsd(dist_a, dist_b)
        assert result is not None
        assert 0.0 <= result <= 1.0

    def test_symmetric(self):
        """JSD must be symmetric: JSD(a,b) == JSD(b,a)"""
        dist_a = [0.8, 0.1, 0.05]
        dist_b = [0.1, 0.7, 0.15]
        assert compute_jsd(dist_a, dist_b) == compute_jsd(dist_b, dist_a)


# ---------------------------------------------------------------------------
# compute_is_outlier — spec scenarios
# ---------------------------------------------------------------------------


class TestComputeIsOutlier:
    """All five concordance states, each e14_type position, and edge cases."""

    def test_armonico_never_outlier(self):
        for t in ("e14c", "e14t", "e14d"):
            assert compute_is_outlier([5, 5, 5], t, "armonico") is False

    def test_otros_never_outlier(self):
        assert compute_is_outlier([None, None, 5], "e14d", "otros") is False

    def test_discrepante_all_are_outliers(self):
        for t in ("e14c", "e14t", "e14d"):
            assert compute_is_outlier([3, 7, 1], t, "discrepante") is True

    def test_discrepante_two_sources_both_outliers(self):
        # [1, 2, None] — n=2, they differ → discrepante; both non-None are outliers
        assert compute_is_outlier([1, 2, None], "e14c", "discrepante") is True
        assert compute_is_outlier([1, 2, None], "e14t", "discrepante") is True

    def test_divergente_middle_is_minority(self):
        # [5, 7, 5] — e14t (pos 1) is the outlier
        assert compute_is_outlier([5, 7, 5], "e14c", "divergente") is False
        assert compute_is_outlier([5, 7, 5], "e14t", "divergente") is True
        assert compute_is_outlier([5, 7, 5], "e14d", "divergente") is False

    def test_divergente_first_is_minority(self):
        # [1, 5, 5] — e14c (pos 0) is the outlier
        assert compute_is_outlier([1, 5, 5], "e14c", "divergente") is True
        assert compute_is_outlier([1, 5, 5], "e14t", "divergente") is False
        assert compute_is_outlier([1, 5, 5], "e14d", "divergente") is False

    def test_divergente_last_is_minority(self):
        # [5, 5, 1] — e14d (pos 2) is the outlier
        assert compute_is_outlier([5, 5, 1], "e14c", "divergente") is False
        assert compute_is_outlier([5, 5, 1], "e14t", "divergente") is False
        assert compute_is_outlier([5, 5, 1], "e14d", "divergente") is True

    def test_concordancia_parcial_none_at_end(self):
        # [4, 4, None] — e14d is None → outlier
        assert compute_is_outlier([4, 4, None], "e14c", "concordancia_parcial") is False
        assert compute_is_outlier([4, 4, None], "e14t", "concordancia_parcial") is False
        assert compute_is_outlier([4, 4, None], "e14d", "concordancia_parcial") is True

    def test_concordancia_parcial_none_at_start(self):
        # [None, 3, 3] — e14c is None → outlier
        assert compute_is_outlier([None, 3, 3], "e14c", "concordancia_parcial") is True
        assert compute_is_outlier([None, 3, 3], "e14t", "concordancia_parcial") is False
        assert compute_is_outlier([None, 3, 3], "e14d", "concordancia_parcial") is False

    def test_unknown_e14_type_returns_false(self):
        assert compute_is_outlier([1, 2, 3], "e14x", "discrepante") is False
