"""
Unified grid detector: corner marks → frame → fixed fallback.

This module provides a single public entry point that tries the most robust
detector first (corner registration marks) and only falls back to frame-based
or full-page fixed grids when corner confidence is low.

This is the E-14D shim preserved from the original grid_detector.py.
For the canonical E-14C segunda vuelta gray-line detector, see grid_detector.py.
"""

from __future__ import annotations

import cv2
import numpy as np

from .grid_detector_corners import (
    GridResult,
    detect_grid as _corners_detect_grid,
    project_grid_from_corners,
)
from .grid_detector_frame import build_grid_from_frame


_CONFIDENCE_THRESHOLD = 0.5


def detect_grid(page_image: np.ndarray) -> GridResult:
    """
    Detect the voting grid using a fallback chain.

    1. Try corner marks first (most robust when present).
    2. If corner detection has low confidence (< 0.5) or method is "fixed",
       try frame-based detection.
    3. If frame detection also fails or has low confidence, fall back to the
       fixed full-page grid.

    Accepts a grayscale or BGR page image.
    """
    corner_result = _corners_detect_grid(page_image)
    if corner_result.method == "corners" and corner_result.confidence >= _CONFIDENCE_THRESHOLD:
        return corner_result

    if page_image.ndim == 3:
        gray = cv2.cvtColor(page_image, cv2.COLOR_BGR2GRAY)
    else:
        gray = page_image

    page_h, page_w = gray.shape[:2]
    frame_result = build_grid_from_frame(gray, page_w, page_h)
    if frame_result.method == "frame" and frame_result.confidence >= _CONFIDENCE_THRESHOLD:
        return frame_result

    return project_grid_from_corners([], page_w, page_h)
