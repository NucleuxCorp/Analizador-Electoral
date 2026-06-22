"""Shared tachon enrichment for primary and fallback digit subcells."""
from __future__ import annotations

import numpy as np

from debug_sv.candidate_subcells import crop_cell
from debug_sv.grid_detector_v2 import CELL_PAD
from src.modules.analyzer.tachon_detector import analyze_cell

NEUTRAL_TACHON: dict = {
    "score": 0.0,
    "tachon_score": 0.0,
    "density_score": 0.0,
    "noise_score": 0.0,
    "flags": [],
    "is_suspicious": False,
}


def _json_safe_tachon(tachon: dict) -> dict:
    return {
        "score": float(tachon["score"]),
        "tachon_score": float(tachon["tachon_score"]),
        "density_score": float(tachon["density_score"]),
        "noise_score": float(tachon["noise_score"]),
        "flags": [str(f) for f in tachon.get("flags", [])],
        "is_suspicious": bool(tachon["is_suspicious"]),
    }


def enrich_subcell_tachon(
    img: np.ndarray,
    cell: dict,
    *,
    has_ink: bool,
    pad: int = CELL_PAD,
) -> dict:
    if not has_ink:
        return dict(NEUTRAL_TACHON)
    crop = crop_cell(img, cell, pad)
    if crop.size == 0:
        return dict(NEUTRAL_TACHON)
    return _json_safe_tachon(analyze_cell(crop).to_dict())


def build_subcell_payload(
    cell: dict,
    *,
    digit: str,
    confidence: float,
    has_ink: bool,
    img: np.ndarray,
    pad: int = CELL_PAD,
) -> dict:
    return {
        "idx": int(cell["idx"]),
        "x1": int(cell["x1"]),
        "y1": int(cell["y1"]),
        "x2": int(cell["x2"]),
        "y2": int(cell["y2"]),
        "digit": str(digit),
        "confidence": float(confidence),
        "has_ink": bool(has_ink),
        "tachon": enrich_subcell_tachon(img, cell, has_ink=has_ink, pad=pad),
    }