"""
Tests for cross-source congruence engine (cross_congruence.py).

TDD cycle: these tests were written FIRST (RED phase) before any production code exists.
"""

from __future__ import annotations

import pytest

from src.modules.analyzer.cross_congruence import (
    compare_subcell,
    compare_field,
    compare_mesa,
    SubcellResult,
    FieldResult,
    MesaCongruence,
)


# ---------------------------------------------------------------------------
# T2.1 — compare_subcell
# ---------------------------------------------------------------------------

class TestCompareSubcell:
    """Position-first null semantics: None is never transparent."""

    def test_all_three_agree_returns_score_3(self):
        """3/3 when all three non-null values are identical."""
        result = compare_subcell("3", "3", "3")
        assert result["score"] == 3
        assert result["agree"] is True
        assert result["modal"] == "3"
        assert result["values"] == ["3", "3", "3"]

    def test_all_three_differ_returns_score_1(self):
        """1/3 when all three are distinct non-null values (modal wins with count=1)."""
        result = compare_subcell("1", "2", "3")
        assert result["score"] == 1
        assert result["agree"] is False
        # modal is e14c value (tiebreak: e14c > e14t > e14d) when all count=1
        assert result["modal"] == "1"

    def test_two_agree_one_differs_returns_score_2(self):
        """2/3 when two sources agree and one differs."""
        result = compare_subcell("5", "5", "7")
        assert result["score"] == 2
        assert result["agree"] is False
        assert result["modal"] == "5"

    def test_null_disagrees_with_non_null(self):
        """null source always disagrees — score cannot be 3 with any null."""
        result = compare_subcell("3", "3", None)
        assert result["score"] == 2
        assert result["agree"] is False
        assert result["modal"] == "3"

    def test_null_disagrees_with_null(self):
        """null == null is still disagreement, not agreement."""
        result = compare_subcell(None, None, "5")
        # None appears 2 times, "5" appears 1 time
        # But None is NOT a valid modal (we only count non-null for agreement)
        # score = count of positions equal to modal when modal non-null
        # modal_non_null = "5" (count=1), so score=1
        assert result["score"] == 1
        assert result["agree"] is False

    def test_all_null_returns_score_0(self):
        """When all three are null, score=0, agree=False, modal=None."""
        result = compare_subcell(None, None, None)
        assert result["score"] == 0
        assert result["agree"] is False
        assert result["modal"] is None

    def test_one_non_null_two_null(self):
        """Only one source has a value: score=1, agree=False."""
        result = compare_subcell("7", None, None)
        assert result["score"] == 1
        assert result["agree"] is False
        assert result["modal"] == "7"

    def test_tiebreak_prefers_e14c(self):
        """When e14c and e14t each appear once, e14c wins tiebreak."""
        result = compare_subcell("2", "9", None)
        # "2" count=1, "9" count=1, None=1 -> tiebreak among non-null: e14c wins
        assert result["modal"] == "2"
        assert result["score"] == 1  # only e14c matches modal

    def test_values_list_preserves_order(self):
        """values list must be [e14c, e14t, e14d] in that order."""
        result = compare_subcell("1", "2", "3")
        assert result["values"] == ["1", "2", "3"]

    def test_values_list_preserves_none(self):
        """None values appear as None in the values list."""
        result = compare_subcell("5", None, "5")
        assert result["values"] == ["5", None, "5"]


# ---------------------------------------------------------------------------
# T2.2 — compare_field
# ---------------------------------------------------------------------------

class TestCompareField:
    """Field-level comparison across 3 subcell positions."""

    def test_all_subcells_agree_returns_3_3(self):
        """score_str='3/3' when all 3 positions agree for all 3 sources."""
        c_digits = ["2", "5", "0"]
        t_digits = ["2", "5", "0"]
        d_digits = ["2", "5", "0"]
        result = compare_field("VOTANTES", c_digits, t_digits, d_digits)
        assert result["score_str"] == "3/3"
        assert result["field"] == "VOTANTES"
        assert len(result["subcells"]) == 3
        assert all(sc["agree"] for sc in result["subcells"])

    def test_one_subcell_disagrees_returns_2_3(self):
        """score_str='2/3' when at least one position has score < 3."""
        c_digits = ["2", "5", "0"]
        t_digits = ["2", "5", "0"]
        d_digits = ["2", "5", "1"]  # units digit differs
        result = compare_field("VOTANTES", c_digits, t_digits, d_digits)
        assert result["score_str"] == "2/3"
        assert result["subcells"][2]["agree"] is False
        assert result["subcells"][0]["agree"] is True

    def test_none_source_propagates_to_subcells(self):
        """None source means all its digits are None."""
        c_digits = ["1", "0", "0"]
        t_digits = None  # E14T unavailable
        d_digits = ["1", "0", "0"]
        result = compare_field("C1_CEPEDA", c_digits, t_digits, d_digits)
        # Each subcell: values = [c_digit, None, d_digit]
        for sc in result["subcells"]:
            assert sc["values"][1] is None
        # E14C and E14D agree on all 3 -> but t is None -> score=2 per subcell
        assert result["score_str"] == "2/3"

    def test_field_name_preserved(self):
        """field name must be preserved in result."""
        result = compare_field("NULOS", ["0", "0", "0"], ["0", "0", "0"], ["0", "0", "0"])
        assert result["field"] == "NULOS"

    def test_all_null_source_returns_0_3(self):
        """When all sources are None, score_str='0/3'."""
        result = compare_field("BLANCO", None, None, None)
        assert result["score_str"] == "0/3"


# ---------------------------------------------------------------------------
# T2.3 — compare_mesa
# ---------------------------------------------------------------------------

class TestCompareMesa:
    """Mesa-level congruence with all 9 fields."""

    FIELDS = [
        "VOTANTES", "URNA", "INCINER",
        "C1_CEPEDA", "C2_ABELARDO", "BLANCO",
        "NULOS", "NO_MARCADOS", "SUMA_TOTAL",
    ]

    def _make_source(self, status: str, digits: dict | None = None) -> dict:
        return {"status": status, "digits": digits or {}}

    def _make_digits_all(self, value: str) -> dict:
        """Build a digits dict where all 9 fields have the same 3-digit value."""
        return {field: [value, value, value] for field in self.FIELDS}

    def test_all_sources_ok_all_agree(self):
        """3 sources OK, all identical digits -> no discrepancy."""
        digits = self._make_digits_all("5")
        e14c = self._make_source("ok", digits)
        e14t = self._make_source("ok", digits)
        e14d = self._make_source("ok", digits)

        result = compare_mesa(e14c, e14t, e14d)

        assert result["summary"]["sources_ok"] == 3
        assert result["summary"]["any_discrepancy"] is False
        assert result["summary"]["fields_3_3"] == 9
        assert result["summary"]["discrepant_fields"] == []

    def test_one_source_not_available(self):
        """E14T not available -> sources_ok=2, null digits for all E14T fields."""
        digits = self._make_digits_all("3")
        e14c = self._make_source("ok", digits)
        e14t = self._make_source("not_available", None)
        e14d = self._make_source("ok", digits)

        result = compare_mesa(e14c, e14t, e14d)

        assert result["summary"]["sources_ok"] == 2
        # E14C and E14D agree, E14T is null -> each subcell score=2, not 3
        assert result["summary"]["any_discrepancy"] is True
        assert result["summary"]["fields_3_3"] == 0

    def test_single_field_discrepancy(self):
        """Only C1_CEPEDA hundreds differs -> any_discrepancy=True."""
        digits_c = self._make_digits_all("1")
        digits_c["C1_CEPEDA"] = ["3", "0", "0"]  # differs in hundreds

        digits_t = self._make_digits_all("1")
        digits_t["C1_CEPEDA"] = ["2", "0", "0"]  # also differs

        digits_d = self._make_digits_all("1")
        digits_d["C1_CEPEDA"] = ["3", "0", "0"]  # same as e14c

        e14c = self._make_source("ok", digits_c)
        e14t = self._make_source("ok", digits_t)
        e14d = self._make_source("ok", digits_d)

        result = compare_mesa(e14c, e14t, e14d)

        assert result["summary"]["any_discrepancy"] is True
        assert "C1_CEPEDA" in result["summary"]["discrepant_fields"]
        # All other fields should be 3/3
        for field in self.FIELDS:
            if field != "C1_CEPEDA":
                assert result["fields"][field]["score_str"] == "3/3"

    def test_extraction_error_source(self):
        """Extraction error -> treated like null (status != ok)."""
        digits = self._make_digits_all("7")
        e14c = self._make_source("ok", digits)
        e14t = self._make_source("extraction_error", None)
        e14d = self._make_source("ok", digits)

        result = compare_mesa(e14c, e14t, e14d)

        assert result["summary"]["sources_ok"] == 2
        assert result["summary"]["any_discrepancy"] is True

    def test_all_sources_not_available(self):
        """No sources available -> sources_ok=0, all discrepant."""
        e14c = self._make_source("not_available")
        e14t = self._make_source("not_available")
        e14d = self._make_source("not_available")

        result = compare_mesa(e14c, e14t, e14d)

        assert result["summary"]["sources_ok"] == 0
        assert result["summary"]["any_discrepancy"] is True
        assert result["summary"]["fields_3_3"] == 0

    def test_result_has_all_9_fields(self):
        """Result must contain entries for all 9 required fields."""
        digits = self._make_digits_all("0")
        e14c = self._make_source("ok", digits)
        e14t = self._make_source("ok", digits)
        e14d = self._make_source("ok", digits)

        result = compare_mesa(e14c, e14t, e14d)

        for field in self.FIELDS:
            assert field in result["fields"], f"Missing field: {field}"

    def test_discrepant_fields_list_populated(self):
        """discrepant_fields lists only fields with at least one subcell disagreement."""
        digits_c = self._make_digits_all("5")
        digits_t = self._make_digits_all("5")
        digits_d = self._make_digits_all("5")
        # Make NULOS differ
        digits_c["NULOS"] = ["9", "9", "9"]
        digits_t["NULOS"] = ["0", "0", "0"]
        digits_d["NULOS"] = ["9", "9", "9"]

        result = compare_mesa(
            self._make_source("ok", digits_c),
            self._make_source("ok", digits_t),
            self._make_source("ok", digits_d),
        )

        assert result["summary"]["discrepant_fields"] == ["NULOS"]

    def test_single_source_no_cross_discrepancy(self):
        """sources_ok=1: cross_discrepancy must be False (nothing to compare)."""
        digits = self._make_digits_all("3")
        e14c = self._make_source("not_available")
        e14t = self._make_source("ok", digits)
        e14d = self._make_source("not_available")

        result = compare_mesa(e14c, e14t, e14d)

        assert result["summary"]["sources_ok"] == 1
        assert result["summary"]["cross_discrepancy"] is False
        assert result["summary"]["cross_discrepant_fields"] == []
        # any_discrepancy stays True because not all 3 sources agree
        assert result["summary"]["any_discrepancy"] is True

    def test_two_sources_agree_no_cross_discrepancy(self):
        """sources_ok=2, both agree: cross_discrepancy=False even though one is null."""
        digits = self._make_digits_all("7")
        e14c = self._make_source("ok", digits)
        e14t = self._make_source("ok", digits)
        e14d = self._make_source("not_available")

        result = compare_mesa(e14c, e14t, e14d)

        assert result["summary"]["sources_ok"] == 2
        assert result["summary"]["cross_discrepancy"] is False
        assert result["summary"]["cross_discrepant_fields"] == []

    def test_two_sources_disagree_cross_discrepancy(self):
        """sources_ok=2, values differ on C2_ABELARDO: cross_discrepancy=True."""
        digits_c = self._make_digits_all("5")
        digits_t = self._make_digits_all("5")
        digits_c["C2_ABELARDO"] = ["1", "2", "3"]
        digits_t["C2_ABELARDO"] = ["1", "9", "3"]  # tens digit differs

        result = compare_mesa(
            self._make_source("ok", digits_c),
            self._make_source("ok", digits_t),
            self._make_source("not_available"),
        )

        assert result["summary"]["sources_ok"] == 2
        assert result["summary"]["cross_discrepancy"] is True
        assert "C2_ABELARDO" in result["summary"]["cross_discrepant_fields"]

    def test_no_sources_no_cross_discrepancy(self):
        """sources_ok=0: cross_discrepancy=False (no data at all)."""
        result = compare_mesa(
            self._make_source("not_available"),
            self._make_source("not_available"),
            self._make_source("not_available"),
        )
        assert result["summary"]["cross_discrepancy"] is False
