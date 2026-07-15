"""Export envelope builder for transversal review decisions."""
from __future__ import annotations

import time
from typing import Any

DEFAULT_DATASET = "transversal_review_E14C_conflictivas"


def build_export_envelope(
    decisions_nested: dict[str, Any],
    dataset: str = DEFAULT_DATASET,
    *,
    generated: str | None = None,
) -> dict[str, Any]:
    """Build normative export JSON (spec §10)."""
    return {
        "generated": generated or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "project": dataset,
        "decisions": decisions_nested,
    }