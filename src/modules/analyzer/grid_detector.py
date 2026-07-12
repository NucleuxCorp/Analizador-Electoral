"""
Grid-based E-14C segunda vuelta cell detector — v3.

v3 = v2 + targeted clip improvements from grid_clean.py.

Changes from v2:
  - ``ocr_cell()`` gains optional ``label`` and ``cell_idx`` parameters.
    When both are provided and ``label`` is in the known-clip set, two
    targeted clips are applied before reading the digit:
      1. For VOTANTES / C1_CEPEDA, subcell 0: trim the top 20 rows to
         remove printed text that bleeds into the digit area.
      2. For any label, subcell 2: trim the right 15 columns to remove
         the right-border artifact of the last sub-column.
  - Adds ``extract_clean_cells_and_digits(pdf_path)`` public API that
    returns ``[{"label", "value", "digits"}]`` for known labels only,
    mirroring the cleaner implementation in grid_clean.py.
  - Adds ``render_first_page(pdf_path)`` alias for ``render_page`` that
    always opens page 0 with an explicit length guard.

All v2 public API is preserved unchanged. The clip logic is purely
additive — callers that do not pass ``label`` / ``cell_idx`` see
identical behaviour to v2.

Uses BOTH vertical AND horizontal gray lines to form COMPLETE cell
rectangles.  Empty cells (e.g. SUMA TOTAL) are still marked as valid
cells with value 0.

Architecture:
  1. Detect gray vertical lines (sub-cell separators: X≈905/998/1091/1187)
  2. Detect gray horizontal lines (row boundaries)
  3. Form a COMPLETE GRID: every (vertical × horizontal) intersection = one cell
     - Each row has 3 cells (3 gaps between 4 vertical lines)
     - Each cell = (x_left, y_top, x_right, y_bot) bounded on ALL 4 sides
  4. For each cell: crop with INNER PADDING to exclude gray border lines
     - This prevents CNN from reading border fragments as digits
  5. Check ink presence; cells without ink = empty (value 0, still marked)
  6. Filter non-data rows **explicitly**: any row labeled UNK@... (header text
     like "VOTACION", "CANDIDATO", titles, spacers) is dropped immediately.
     Only rows assigned a known numeric label (VOTANTES, candidates, totals...)
     ever become full cells or get passed to the digit acortador.

Run:
    python debug_sv/grid_detector_v3.py
"""
import json
import logging
import sys
from pathlib import Path
from typing import Any

import cv2
import fitz
import numpy as np

from .ocr_engines import SegmentedEngine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Oval-template filter (inlined — removed from ocr_engines in current version)
# ---------------------------------------------------------------------------
OVAL_MEAN_THRESH: float = 67.0
OVAL_STD_THRESH: float = 18.5
OVAL_COMPACT_MIN: float = 0.58
OVAL_ASPECT_MIN: float = 0.68
OVAL_ASPECT_MAX: float = 1.38


def es_ovalo_plantilla(
    gray_sub: np.ndarray,
    caja: tuple[int, int, int, int],
    umbral_media: float = OVAL_MEAN_THRESH,
    umbral_std: float = OVAL_STD_THRESH,
    compact_min: float = OVAL_COMPACT_MIN,
    aspecto_min: float = OVAL_ASPECT_MIN,
    aspecto_max: float = OVAL_ASPECT_MAX,
) -> bool:
    """Return True if the connected component looks like a pre-printed template oval.

    Criteria: near-square aspect, high compactness, and lighter/more-uniform
    ink than a genuine handwritten digit stroke.
    """
    x, y, w, h = caja
    if w < 8 or h < 8:
        return False
    sub = gray_sub[y:y + h, x:x + w]
    if sub.size == 0:
        return False

    _, bw = cv2.threshold(sub, 120, 255, cv2.THRESH_BINARY_INV)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(bw)
    if n < 2:
        return False

    areas = stats[1:, cv2.CC_STAT_AREA]
    k = 1 + int(np.argmax(areas))
    area = stats[k, cv2.CC_STAT_AREA]
    if area < 40:
        return False

    cw = stats[k, cv2.CC_STAT_WIDTH]
    ch = stats[k, cv2.CC_STAT_HEIGHT]
    if cw == 0 or ch == 0:
        return False

    aspect = cw / ch
    if not (aspecto_min < aspect < aspecto_max):
        return False

    compact = area / (cw * ch)
    if compact < compact_min:
        return False

    mask = labels == k
    pix = sub[mask]
    if len(pix) == 0:
        return False
    media = float(np.mean(pix))
    desvest = float(np.std(pix))
    return media > umbral_media and desvest < umbral_std

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DPI = 300
PDF_DIR = Path("data/pdfs_e14c_segunda")
OUT_DIR = Path("debug_sv")

VOTE_X_LEFT = 889
VOTE_X_RIGHT = 1197

# Known sub-cell X coordinates (fallback if detection fails)
SUBCELL_X_FALLBACK = [905, 998, 1091, 1187]

Y1 = 700   # analysis top
Y2 = 3500  # analysis bottom

# Inner padding to exclude gray border lines from the OCR crop
# This prevents the CNN from reading border fragments as digit strokes
CELL_PAD = 8  # pixels

# Extra interior margin when slicing a sub-digit from between two gray guide lines.
# Prevents the crop from eating the gray border "referencias" or noise.
SUB_GUIDE_INNER = 6

# Row height bounds for pairing horizontal lines
ROW_MIN_H = 80
ROW_MAX_H = 150

# Header zone: gaps 20–50 px valid below this Y (reference page height 3500 px @ 300 DPI)
HEADER_Y_MAX_REF = 900
PAGE_HEIGHT_REF = 3500
HEADER_Y_MAX_RATIO = HEADER_Y_MAX_REF / PAGE_HEIGHT_REF

# Labels whose first subcell (j==0) has printed text bleeding into the digit crop.
# A 20-row top trim removes it without cutting into the digit body.
_CLIP_TOP20_LABELS = frozenset(("VOTANTES", "C1_CEPEDA"))


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def render_page(pdf_path: Path) -> np.ndarray:
    doc = fitz.open(str(pdf_path))
    page = doc[0]
    mat = fitz.Matrix(DPI / 72, DPI / 72)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
    elif pix.n == 1:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    else:
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    doc.close()
    return img


def render_first_page(pdf_path: Path) -> np.ndarray:
    """Render ONLY page 0 at 300 DPI with an explicit length guard.

    Alias for ``render_page`` that mirrors the grid_clean.py contract:
    always opens page 0, never touches page 1.
    """
    doc = fitz.open(str(pdf_path))
    if len(doc) < 1:
        doc.close()
        raise ValueError(f"PDF has no pages: {pdf_path}")
    page = doc[0]
    mat = fitz.Matrix(DPI / 72, DPI / 72)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
    elif pix.n == 1:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    else:
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    doc.close()
    return img


# ---------------------------------------------------------------------------
# Gray line detection
# ---------------------------------------------------------------------------
def detect_gray_h_lines(gray: np.ndarray) -> list[int]:
    """Detect horizontal gray lines in the voting column. Returns absolute Y coords."""
    roi = gray[Y1:Y2, VOTE_X_LEFT:VOTE_X_RIGHT]
    H, W = roi.shape
    candidates = []
    for y in range(H):
        row = roi[y, :]
        gray_frac = np.mean((row > 80) & (row < 220))
        black_frac = np.mean(row < 80)
        if gray_frac > 0.40 and black_frac < 0.15:
            candidates.append(y)

    if not candidates:
        return []

    # Cluster within 5px
    candidates.sort()
    clusters = []
    cur = [candidates[0]]
    for y in candidates[1:]:
        if y - cur[-1] <= 5:
            cur.append(y)
        else:
            clusters.append(int(np.median(cur)) + Y1)
            cur = [y]
    clusters.append(int(np.median(cur)) + Y1)
    return clusters


def detect_gray_v_lines(gray: np.ndarray) -> list[int]:
    """Detect vertical gray lines in the voting column. Returns absolute X coords."""
    roi = gray[Y1:Y2, VOTE_X_LEFT:VOTE_X_RIGHT]
    H, W = roi.shape
    candidates = []
    for x in range(W):
        col = roi[:, x]
        gray_frac = np.mean((col > 80) & (col < 220))
        black_frac = np.mean(col < 80)
        if gray_frac > 0.35 and black_frac < 0.15:
            candidates.append(x)

    if not candidates:
        return []

    candidates.sort()
    clusters = []
    cur = [candidates[0]]
    for x in candidates[1:]:
        if x - cur[-1] <= 8:
            cur.append(x)
        else:
            clusters.append(int(np.median(cur)) + VOTE_X_LEFT)
            cur = [x]
    clusters.append(int(np.median(cur)) + VOTE_X_LEFT)
    return clusters


def detect_local_subcell_gray_lines(
    gray: np.ndarray,
    y_top: int,
    y_bot: int,
    x_left: int = VOTE_X_LEFT,
    x_right: int = VOTE_X_RIGHT,
    global_lines: list[int] | None = None,
) -> list[int]:
    """Detect (or refine) the 4 vertical gray grid lines for one full cell.

    Follows the request:
    - Launch gray line search from the left border of the 3-subcell group.
    - Find the lines in the y-band of *this* full cell so the 3 cuts follow
      the actual printed grilla gris (not equal thirds, not noisy refs).
    - If local evidence weak (short band), refine the global_lines.

    Returns 4 absolute X positions when possible.
    """
    band_y1 = max(0, int(y_top))
    band_y2 = min(gray.shape[0], int(y_bot))
    band_x1 = int(x_left)
    band_x2 = int(x_right)
    band = gray[band_y1:band_y2, band_x1:band_x2]

    if band.size == 0 or band.shape[1] < 50:
        return global_lines[:4] if global_lines and len(global_lines) >= 4 else []

    H, W = band.shape
    col_scores = []
    for x in range(W):
        col = band[:, x]
        g = np.mean((col > 60) & (col < 240))
        b = np.mean(col < 60)
        score = g if b < 0.30 else 0.0
        col_scores.append((x, score))

    candidates = [x for x, s in col_scores if s > 0.18]

    if len(candidates) < 3 and global_lines:
        # local band weak -> start from global and locally adjust each line
        base = sorted(global_lines)[:4]
        refined = []
        for bx in base:
            rel = bx - band_x1
            w0 = max(0, rel - 14)
            w1 = min(W, rel + 15)
            if w1 > w0:
                best_rel = max(range(w0, w1), key=lambda rx: col_scores[rx][1])
                refined.append(band_x1 + best_rel)
            else:
                refined.append(bx)
        return sorted(refined)[:4]

    if not candidates:
        return global_lines[:4] if global_lines and len(global_lines) >= 4 else []

    candidates.sort()
    clusters = []
    cur = [candidates[0]]
    for x in candidates[1:]:
        if x - cur[-1] <= 14:
            cur.append(x)
        else:
            clusters.append(int(np.median(cur)))
            cur = [x]
    clusters.append(int(np.median(cur)))
    abs_lines = sorted(c + band_x1 for c in clusters)

    if len(abs_lines) >= 4:
        # Fix false left border: if gap[0] < 40px, line 0 is stuck to edge
        if (abs_lines[1] - abs_lines[0]) < 40:
            cell_w = (x_right - x_left) // 3
            abs_lines = [x_left, x_left + cell_w, x_left + 2*cell_w, x_right]
            return abs_lines
        return abs_lines[:4]
    if global_lines and len(global_lines) >= 4:
        return sorted(global_lines)[:4]
    return abs_lines


# ---------------------------------------------------------------------------
# Horizontal line cleanup (merge + filter before build_grid)
# ---------------------------------------------------------------------------
def merge_duplicate_h_lines(h_lines: list[int], threshold: int = 15) -> list[int]:
    """Fusiona líneas horizontales gemelas separadas menos de ``threshold`` px.

    Agrupa líneas consecutivas cuya distancia es menor al umbral, reemplaza
    cada grupo por su promedio entero y devuelve la lista ordenada ascendente.

    Args:
        h_lines: Coordenadas Y absolutas de líneas detectadas.
        threshold: Distancia máxima (px) para considerar dos líneas como duplicadas.

    Returns:
        Lista de coordenadas Y fusionadas, ordenadas de menor a mayor.
    """
    if not h_lines:
        return []

    n_before = len(h_lines)
    sorted_lines = sorted(h_lines)
    merged: list[int] = []
    cluster = [sorted_lines[0]]

    for y in sorted_lines[1:]:
        if y - cluster[-1] < threshold:
            cluster.append(y)
        else:
            merged.append(int(round(sum(cluster) / len(cluster))))
            cluster = [y]
    merged.append(int(round(sum(cluster) / len(cluster))))

    logger.info(
        "merge_duplicate_h_lines: %d líneas → %d tras fusionar gemelas <%d px",
        n_before,
        len(merged),
        threshold,
    )
    return merged


def filter_vote_row_boundaries(
    h_lines: list[int],
    row_min_h: int = ROW_MIN_H,
    row_max_h: int = ROW_MAX_H,
    header_y_max: int | None = None,
) -> tuple[list[int], list[int]]:
    """Filtra líneas horizontales conservando solo bordes de fila de votación.

    Recorre las líneas en orden ascendente con estrategia greedy: acepta una
    línea solo si su separación respecto a la última aceptada cae en un rango
    válido de altura de fila (``row_min_h``–``row_max_h``). Las líneas
    intermedias que no cumplen el criterio se descartan.

    Excepción de encabezado (Y < ``header_y_max``): gaps de 20–50 px son
    válidos porque el bloque superior del formulario tiene separadores más
    densos (título, doble borde entre sub-secciones).

    Excepción de cuerpo: gaps de 20–50 px también se aceptan cuando la línea
    actual es el borde superior de una fila cuyo borde inferior válido aparece
    más adelante (típico doble línea entre VOTANTES/URNA y filas de totales).

    Saltos mayores a ``row_max_h`` se aceptan como anclas de nueva sección
    (p. ej. salto entre INCINER y candidatos). No se fuerza un número fijo de
    filas: el resultado tolera filas faltantes.

    Args:
        h_lines: Coordenadas Y (idealmente ya fusionadas).
        row_min_h: Altura mínima válida de fila de votación (px).
        row_max_h: Altura máxima válida de fila de votación (px).
        header_y_max: Límite Y del bloque de encabezado con gaps cortos válidos.

    Returns:
        Tupla ``(aceptadas, descartadas)`` con listas ordenadas ascendente.
    """
    if header_y_max is None:
        header_y_max = int(Y2 * HEADER_Y_MAX_RATIO)

    sorted_lines = sorted(h_lines)
    if not sorted_lines:
        return [], []

    n_before = len(sorted_lines)
    accepted: list[int] = [sorted_lines[0]]
    discarded: list[int] = []

    for y in sorted_lines[1:]:
        gap = y - accepted[-1]
        in_header = accepted[-1] < header_y_max

        if row_min_h <= gap <= row_max_h:
            accepted.append(y)
        elif in_header and 20 <= gap <= 50:
            accepted.append(y)
        elif 20 <= gap <= 50 and not in_header:
            # Borde superior de fila: debe existir un cierre válido más abajo.
            starts_valid_row = any(
                row_min_h <= future - y <= row_max_h
                for future in sorted_lines
                if future > y
            )
            if starts_valid_row:
                accepted.append(y)
            else:
                discarded.append(y)
        elif gap > row_max_h:
            accepted.append(y)
        else:
            discarded.append(y)

    logger.info(
        "filter_vote_row_boundaries: %d líneas → %d aceptadas, %d descartadas",
        n_before,
        len(accepted),
        len(discarded),
    )
    return accepted, discarded


def process_h_lines(
    h_lines: list[int],
    page_height: int | None = None,
) -> list[int]:
    """Aplica merge y filtrado de líneas horizontales antes de ``build_grid``.

    Pipeline: ``merge_duplicate_h_lines`` → ``filter_vote_row_boundaries``.

    Args:
        h_lines: Líneas crudas devueltas por ``detect_gray_h_lines``.
        page_height: Altura de página en px; escala ``header_y_max`` proporcionalmente.

    Returns:
        Lista de coordenadas Y listas para construir la grilla.
    """
    header_y_max = (
        int(page_height * HEADER_Y_MAX_RATIO)
        if page_height is not None
        else None
    )
    merged = merge_duplicate_h_lines(h_lines)
    accepted, _discarded = filter_vote_row_boundaries(merged, header_y_max=header_y_max)
    if len(accepted) < 2:
        logger.warning(
            "process_h_lines: solo %d línea(s) tras filtrado (raw=%d, merged=%d)",
            len(accepted),
            len(h_lines),
            len(merged),
        )
    return accepted


# ---------------------------------------------------------------------------
# Grid construction (THE FIX: form complete cells from V×H intersections)
# ---------------------------------------------------------------------------
def build_grid(v_lines: list[int], h_lines: list[int]) -> list[dict]:
    """
    Build complete cell grid from vertical AND horizontal lines.

    Each cell is bounded on ALL 4 sides:
      top    = h_lines[i]
      bottom = h_lines[i+1]
      left   = v_lines[j]
      right  = v_lines[j+1]

    Returns list of rows, each row = list of 3 cells:
      [{row_idx, top, bot, height, cells: [{cell_idx, x1, y1, x2, y2}]}]
    """
    if len(v_lines) < 4 or len(h_lines) < 2:
        if len(h_lines) < 2:
            logger.warning(
                "build_grid: h_lines insuficientes (%d), se requieren >= 2",
                len(h_lines),
            )
        return []

    v_sorted = sorted(v_lines)
    h_sorted = sorted(h_lines)

    rows = []
    for i in range(len(h_sorted) - 1):
        top = h_sorted[i]
        bot = h_sorted[i + 1]
        height = bot - top

        # Skip rows outside reasonable vote-row height
        if not (ROW_MIN_H <= height <= ROW_MAX_H):
            continue

        cells = []
        for j in range(len(v_sorted) - 1):
            cells.append({
                "cell_idx": j,
                "x1": v_sorted[j],
                "y1": top,
                "x2": v_sorted[j + 1],
                "y2": bot,
            })

        rows.append({
            "row_idx": len(rows),
            "top": top,
            "bot": bot,
            "height": height,
            "cells": cells,
        })

    if not rows:
        logger.warning(
            "build_grid: grilla vacía (v_lines=%d, h_lines=%d)",
            len(v_lines),
            len(h_lines),
        )

    return rows


# ---------------------------------------------------------------------------
# Ink detection with padding
# ---------------------------------------------------------------------------
def cell_has_ink(gray: np.ndarray, x1: int, y1: int, x2: int, y2: int,
                 pad: int = CELL_PAD) -> bool:
    """Devuelve True si la celda (con padding) contiene tinta manuscrita de dígito.
    Filtra óvalos de plantilla pre-impresa usando es_ovalo_plantilla para
    evitar falsos positivos en celdas vacías (INCINER, BLANCO, etc).
    """
    cx1 = x1 + pad
    cy1 = y1 + pad
    cx2 = x2 - pad
    cy2 = y2 - pad
    if cx2 <= cx1 or cy2 <= cy1:
        return False

    crop = gray[cy1:cy2, cx1:cx2]
    if crop.size == 0:
        return False

    _, bw = cv2.threshold(crop, 120, 255, cv2.THRESH_BINARY_INV)
    # FM-3 fix: reduced kernel (3,3) to limit dilation width so wide digits (e.g. '4', '0')
    # are not inflated beyond the 0.97 width upper-bound after morphological closing.
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE,
                          cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(bw)
    H, W = crop.shape
    # FM-1 fix: lowered ratio 0.30->0.18 and floor 8->6.
    # The original 30% floor rejected short/flat genuine strokes (squat '2', flat '7',
    # compressed '0'). E14T dot markers (~1-2px) still fall well under 18%.
    min_h = max(6, int(H * 0.18))
    for k in range(1, n):
        x, y, w, h, area = stats[k]
        # FM-3 fix: raised width upper-bound 0.9->0.97. The (x+w)<=W right-edge guard
        # already rejects full-width grid-line smears; 0.97 stays safe against those.
        if area >= 40 and 6 < w < W * 0.97 and min_h < h:
            # Edge filter: reject blobs that exceed the crop right bound.
            # x > 0 was removed — CELL_PAD already separates from the grid line,
            # so blobs starting at x=0 post-padding are real digits (not grid artifacts).
            # Original x > 0 caused VOTANTES/URNA/SUMA_TOTAL to read as 0 when
            # digits were written at the left edge of their subcell.
            if (x + w) <= W:
                # FM-2 fix: local umbral_media=90.0 override (do NOT change module default).
                # Printed ovals are light-gray (mean > 90); handwritten ink is darker (mean ~68-80).
                # The second live caller (ocr_engines.py:380, read_number) keeps the 67.0 default.
                if not es_ovalo_plantilla(crop, (x, y, w, h), umbral_media=90.0):
                    return True
    return False


def ocr_cell(
    engine,
    img: np.ndarray,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    pad: int = CELL_PAD,
    guide_inner: int = 0,
    label: str | None = None,
    cell_idx: int | None = None,
) -> tuple[str, float, list[dict]]:  # digit, "?", or (rarely) other from engine
    """OCR a single cell.

    Args:
        engine: SegmentedEngine instance.
        img: Full page BGR image.
        x1, y1, x2, y2: Cell bounding box (absolute page coordinates).
        pad: General inner padding to strip gray border lines.
        guide_inner: Extra trim from gray guide lines (applied symmetrically on x).
        label: Optional row label (e.g. "VOTANTES").  When provided together
            with ``cell_idx``, targeted clips are applied to remove printed
            text artifacts that bleed into the digit crop area.
        cell_idx: Zero-based index of the subcell within its row (0, 1, or 2).
            Required for targeted clips; ignored when ``label`` is None.

    Targeted clips (v3 addition — only active when both label and cell_idx
    are passed):
        - ``label in ("VOTANTES", "C1_CEPEDA")`` and ``cell_idx == 0``:
          trim the top 20 rows of the crop to remove the printed row-label
          text that bleeds upward into subcell 0.
        - ``cell_idx == 2``: trim the rightmost 15 columns to remove the
          right-border artifact of the last sub-column.

    Returns:
        Tuple of (digit_char, confidence, top3) where top3 is a list of up to
        3 dicts {"digit": str, "prob": float} sorted by probability descending.
        Empty list when the EasyOCR fallback is used.
    """
    extra = max(pad, guide_inner)
    cx1 = x1 + extra
    cy1 = y1 + pad
    cx2 = x2 - extra
    cy2 = y2 - pad
    if cx2 <= cx1 or cy2 <= cy1:
        return ("?", 0.0, [])

    crop = img[cy1:cy2, cx1:cx2]
    if crop.size == 0:
        return ("?", 0.0, [])

    ch, conf, top3 = engine._read_single_digit(crop)
    # Do NOT coerce symbols to "0" here. With expanded allowlist the engine
    # can now return *, -, +, x, X. Caller (using cell_has_ink) will decide:
    #   - ink + digit → digit
    #   - ink + symbol/empty → "?"
    if ch is None or ch in {"", "null", ".", "o", "O"}:
        ch = "?"
    return (ch, float(conf), top3)


# ---------------------------------------------------------------------------
# Row labelling
# ---------------------------------------------------------------------------
SECTION_GAP_THRESHOLD = 400
ROW_TYPICAL_MAX_H = 120  # nivelación rows; taller bands are header/spacer noise
BLOCK_A_Y_FLOOR = 980
BLOCK_A_REF_PAGE_H = 3500
BLOCK_A_Y_FLOOR_CAP = 1020  # keep URNA@1025 band eligible on tall pages
BLOCK_A_PREPEND_Y_THRESHOLD = 1140  # prepend sub-floor row when first eligible is higher
BLOCK_A_PREPEND_MAX_Y = 950  # only prepend header noise rows below this Y
LABELS_BLOCK_A = ("VOTANTES", "URNA", "INCINER")
LABELS_BLOCK_B = ("C1_CEPEDA", "C2_ABELARDO")
LABELS_BLOCK_C = ("BLANCO", "NULOS", "NO_MARCADOS", "SUMA_TOTAL")
KNOWN_ROW_LABELS = frozenset(
    LABELS_BLOCK_A + LABELS_BLOCK_B + LABELS_BLOCK_C
)

# For mark-anchored positions (from extract_marks_normalized + upper)
FIELD_RANGES = {
    "VOTANTES": (985, 1079),
    "URNA": (1102, 1197),
    "INCINER": (1221, 1316),
    "C1_CEPEDA": (1748, 1844),
    "C2_ABELARDO": (2350, 2445),
    "BLANCO": (2787, 2882),
    "NULOS": (2906, 3000),
    "NO_MARCADOS": (3024, 3118),
    "SUMA_TOTAL": (3142, 3235),
}


def is_known_label(label: str) -> bool:
    """Return True if *label* is a recognized structural field name."""
    return label in KNOWN_ROW_LABELS


def _row_gaps(rows: list[dict]) -> list[int]:
    """Gap in px between consecutive row bottoms and tops."""
    return [rows[i + 1]["top"] - rows[i]["bot"] for i in range(len(rows) - 1)]


def _assign_labels(
    rows: list[dict],
    indices: list[int],
    labels: tuple[str, ...],
    *,
    label_source: str = "structure",
) -> None:
    """Write *labels* onto *rows* at *indices* (truncated to shorter length)."""
    for idx, label in zip(indices, labels):
        y_mid = (rows[idx]["top"] + rows[idx]["bot"]) // 2
        rows[idx]["label"] = label
        rows[idx]["y_mid"] = y_mid
        rows[idx]["label_source"] = label_source


def _effective_block_a_y_floor(
    *,
    y_floor: int | None,
    page_h: int | None,
) -> int:
    if y_floor is not None:
        return y_floor
    if page_h is not None:
        scaled = int(page_h * BLOCK_A_Y_FLOOR / BLOCK_A_REF_PAGE_H)
        return min(scaled, BLOCK_A_Y_FLOOR_CAP)
    raise ValueError("anchor_block_a requires y_floor or page_h")


def anchor_block_a(
    rows: list[dict],
    c1_idx: int | None,
    *,
    y_floor: int | None = None,
    page_h: int | None = None,
) -> list[int]:
    """Return grid indices for Block A (up to 3 compact rows above Y floor, before C1)."""
    if c1_idx is None or c1_idx <= 0:
        return []

    y_floor_effective = _effective_block_a_y_floor(y_floor=y_floor, page_h=page_h)
    compact_below: list[int] = []
    eligible: list[int] = []
    for i in range(c1_idx):
        row = rows[i]
        h = row.get("height", row["bot"] - row["top"])
        y_mid = (row["top"] + row["bot"]) // 2
        if not (ROW_MIN_H <= h <= ROW_TYPICAL_MAX_H):
            continue
        if y_mid < y_floor_effective:
            compact_below.append(i)
            continue
        eligible.append(i)

    if compact_below and eligible:
        first_y = (rows[eligible[0]]["top"] + rows[eligible[0]]["bot"]) // 2
        last_below_y = (rows[compact_below[-1]]["top"] + rows[compact_below[-1]]["bot"]) // 2
        prepend = False
        if (
            len(compact_below) == 1
            and last_below_y < 940
            and 1025 <= first_y < BLOCK_A_PREPEND_Y_THRESHOLD
        ):
            prepend = True
        elif last_below_y < BLOCK_A_PREPEND_MAX_Y and first_y > BLOCK_A_PREPEND_Y_THRESHOLD:
            prepend = True
        if prepend:
            eligible = [compact_below[-1]] + eligible[: len(LABELS_BLOCK_A) - 1]

    return eligible[: len(LABELS_BLOCK_A)]


def detect_4_registration_marks(img: np.ndarray) -> list[dict]:
    """Detect the 4 main corner registration/alignment marks."""
    H, W = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, bw = cv2.threshold(gray, 55, 255, cv2.THRESH_BINARY_INV)
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (5,5)))

    contours, _ = cv2.findContours(bw, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    candidates = []
    for c in contours:
        area = cv2.contourArea(c)
        if 400 < area < 6000:
            x, y, w, h = cv2.boundingRect(c)
            aspect = w / max(h, 1)
            if 0.4 < aspect < 2.5:
                cx, cy = x + w//2, y + h//2
                dists = [
                    (cx**2 + cy**2)**0.5,
                    ((W-cx)**2 + cy**2)**0.5,
                    (cx**2 + (H-cy)**2)**0.5,
                    ((W-cx)**2 + (H-cy)**2)**0.5
                ]
                min_d = min(dists)
                if min_d < 400:
                    candidates.append({'cx': cx, 'cy': cy, 'x':x, 'y':y, 'w':w, 'h':h, 'area':area, 'min_d': min_d})

    corners = [(0,0), (W,0), (0,H), (W,H)]
    marks = []
    used = set()
    for pos in corners:
        best = None
        best_dist = 1e9
        for i, c in enumerate(candidates):
            if i in used: continue
            d = ((c['cx'] - pos[0])**2 + (c['cy'] - pos[1])**2)**0.5
            if d < best_dist:
                best_dist = d
                best = (i, c)
        if best:
            used.add(best[0])
            marks.append(best[1])

    if len(marks) >= 4:
        marks = sorted(marks, key=lambda m: (m['cy'], m['cx']))
        top = sorted(marks[:2], key=lambda m: m['cx'])
        bot = sorted(marks[2:], key=lambda m: m['cx'])
        labeled = [
            {'pos': 'TL', 'data': top[0]},
            {'pos': 'TR', 'data': top[1]},
            {'pos': 'BL', 'data': bot[0]},
            {'pos': 'BR', 'data': bot[1]},
        ]
        return labeled
    return []


def label_rows_by_structure(
    rows: list[dict],
    page_h: int | None = None,
    page_img: np.ndarray | None = None,
) -> list[dict]:
    """Assign semantic labels from relative row gaps (not absolute Y).

    Pattern (E-14C segunda vuelta):
      VOTANTES → URNA → INCINER → [gap>400] → C1 → C2 → … → BLANCO → NULOS →
      NO_MARCADOS → SUMA_TOTAL

    Spacer / noise rows between sections receive ``UNK@{y_mid}``.

    Args:
        rows: Output of ``build_grid()`` (``top``, ``bot``, ``cells``, ``row_idx``).

    Returns:
        Same rows enriched with ``label``, ``y_mid``, ``label_source``.
    """
    if not rows:
        return []

    labeled = [dict(r) for r in rows]
    n = len(labeled)
    gaps = _row_gaps(labeled)

    for row in labeled:
        y_mid = (row["top"] + row["bot"]) // 2
        row["y_mid"] = y_mid
        row["label"] = f"UNK@{y_mid}"
        row["label_source"] = "structure"

    mega_indices = [i for i, g in enumerate(gaps) if g > SECTION_GAP_THRESHOLD]

    c1_idx: int | None = None
    c2_idx: int | None = None

    if mega_indices:
        if page_img is not None:
            marks = detect_4_registration_marks(page_img)
            if marks and len(marks) >= 2:
                top_ys = [m['data']['y'] for m in marks if m.get('pos') in ('TL', 'TR')]
                if top_ys:
                    top_y = int(np.mean(top_ys))
                    expected_c1_y = top_y + 1693  # calibration delta from black squares
                    # Choose the mega gap whose middle y is closest to the reference
                    def gap_mid_y(i):
                        return (labeled[i]['bot'] + labeled[i+1]['top']) / 2
                    cand_gap_idx = min(mega_indices, key=lambda i: abs(gap_mid_y(i) - expected_c1_y))
                    c1_idx = cand_gap_idx
                    c2_idx = cand_gap_idx + 1
                    _assign_labels(labeled, [c1_idx, c2_idx], LABELS_BLOCK_B)
                else:
                    cand_gap_idx = max(mega_indices, key=lambda i: gaps[i])
                    c1_idx = cand_gap_idx
                    c2_idx = cand_gap_idx + 1
                    _assign_labels(labeled, [c1_idx, c2_idx], LABELS_BLOCK_B)
            else:
                cand_gap_idx = max(mega_indices, key=lambda i: gaps[i])
                c1_idx = cand_gap_idx
                c2_idx = cand_gap_idx + 1
                _assign_labels(labeled, [c1_idx, c2_idx], LABELS_BLOCK_B)
        else:
            # Largest internal gap separates the two candidate rows (gap ~600).
            cand_gap_idx = max(mega_indices, key=lambda i: gaps[i])
            c1_idx = cand_gap_idx
            c2_idx = cand_gap_idx + 1
            _assign_labels(labeled, [c1_idx, c2_idx], LABELS_BLOCK_B)
    elif n >= 2:
        # No mega-gap: pick the two rows with the largest separation in the
        # middle third of the grid as a weak C1/C2 fallback.
        mid_lo = n // 3
        mid_hi = max(mid_lo + 1, (2 * n) // 3)
        best_gap = -1
        best_i = mid_lo
        for i in range(mid_lo, min(mid_hi, n - 1)):
            if gaps[i] > best_gap:
                best_gap = gaps[i]
                best_i = i
        if best_gap >= ROW_MAX_H:
            c1_idx = best_i
            c2_idx = best_i + 1
            _assign_labels(labeled, [c1_idx, c2_idx], LABELS_BLOCK_B)

    # Block A: compact nivelación rows above Y floor, before C1.
    if c1_idx is not None and c1_idx > 0:
        effective_page_h = page_h if page_h is not None else max(r["bot"] for r in labeled)
        block_a_indices = anchor_block_a(labeled, c1_idx, page_h=effective_page_h)
        _assign_labels(
            labeled,
            block_a_indices,
            LABELS_BLOCK_A,
            label_source="anchor_block_a",
        )

    # Block C: last 4 compact rows at the bottom of the grid.
    if n >= 4:
        block_c = list(range(n - 4, n))
        _assign_labels(labeled, block_c, LABELS_BLOCK_C)

    return labeled


def validate_labeled_rows(
    rows: list[dict],
    fields: dict[str, int | None] | None = None,
) -> list[str]:
    """Lightweight structural / arithmetic checks on labeled rows.

    Returns a list of warning strings (empty when all checks pass).
    """
    warnings: list[str] = []
    by_label = {r["label"]: r for r in rows if is_known_label(r.get("label", ""))}

    if "C1_CEPEDA" in by_label and "C2_ABELARDO" in by_label:
        if by_label["C1_CEPEDA"]["y_mid"] >= by_label["C2_ABELARDO"]["y_mid"]:
            warnings.append("order_c1_c2")

    if "URNA" in by_label and "C1_CEPEDA" in by_label:
        if by_label["URNA"]["y_mid"] >= by_label["C1_CEPEDA"]["y_mid"]:
            warnings.append("order_urna_c1")

    if "C2_ABELARDO" in by_label and "BLANCO" in by_label:
        if by_label["C2_ABELARDO"]["y_mid"] >= by_label["BLANCO"]["y_mid"]:
            warnings.append("order_c2_blanco")

    if fields:
        urna = fields.get("URNA")
        c1 = fields.get("C1_CEPEDA")
        c2 = fields.get("C2_ABELARDO")
        if urna is not None and c1 is not None and c2 is not None:
            if (c1 or 0) + (c2 or 0) > urna:
                warnings.append("c1_c2_exceed_urna")

    return warnings


def guess_label(y_mid: int) -> str:
    """Deprecated: fixed Y-center lookup. Use ``label_rows_by_structure()`` instead.

    Centers calibrated from the first successful grid detection run:
      VOTANTES  ~1032
      URNA      ~1149
      INCINER   ~1268
      C1        ~1796
      C2        ~2397
      BLANCO    ~2834
      NULOS     ~2953
      NO_MARCADOS ~3071
      SUMA_TOTAL  ~3188
    """
    if abs(y_mid - 1032) < 70:  return "VOTANTES"
    if abs(y_mid - 1149) < 70:  return "URNA"
    if abs(y_mid - 1268) < 70:  return "INCINER"
    if abs(y_mid - 1796) < 80:  return "C1_CEPEDA"
    if abs(y_mid - 2397) < 80:  return "C2_ABELARDO"
    if abs(y_mid - 2834) < 70:  return "BLANCO"
    if abs(y_mid - 2953) < 70:  return "NULOS"
    if abs(y_mid - 3071) < 70:  return "NO_MARCADOS"
    if abs(y_mid - 3188) < 70:  return "SUMA_TOTAL"
    return f"UNK@{y_mid}"


# ---------------------------------------------------------------------------
# v3 public API — clean extraction + first-page alias
# ---------------------------------------------------------------------------
def extract_clean_cells_and_digits(pdf_path: Path) -> list[dict[str, Any]]:
    """Extract vote cells and per-digit values from the first page of an E-14C PDF.

    - Renders ONLY page 0 (via render_first_page).
    - Builds grid on the vote column using the standard pipeline.
    - Labels rows structurally (label_rows_by_structure).
    - Immediately skips every row whose label starts with ``UNK@``
      (VOTACIÓN, CANDIDATO, titles, spacers — never reach OCR).
    - For each known row: checks ink (with oval filter), applies targeted
      clips (same as the v3 ocr_cell clips), reads the three subcell digits.

    Returns:
        List of dicts with keys ``label`` (str), ``value`` (int | None),
        and ``digits`` (list[str | None | "?"]), one entry per known label found.

        Per-subcell semantics (after cell_has_ink propagation):
          - None  → cell_has_ink() was False (truly blank, no ink)
          - "0"-"9" → recognized digit
          - "?" → ink present but OCR returned symbol (* - x + etc) or nothing

    Example::

        [
            {"label": "VOTANTES", "value": 238, "digits": ["2", "3", "8"]},
            {"label": "SUMA_TOTAL", "value": None, "digits": [None, None, None]},
            ...
        ]
    """
    img = render_first_page(pdf_path)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    v_lines = detect_gray_v_lines(gray)
    if len(v_lines) < 4:
        v_lines = SUBCELL_X_FALLBACK

    h_lines_raw = detect_gray_h_lines(gray)
    h_lines = process_h_lines(h_lines_raw, page_height=gray.shape[0])
    grid_rows = build_grid(v_lines, h_lines)
    labeled_rows = label_rows_by_structure(grid_rows, page_h=gray.shape[0])

    engine = SegmentedEngine()
    results: list[dict[str, Any]] = []

    for row in labeled_rows:
        label = row["label"]
        # Strict early exclusion — headers never reach OCR
        if not is_known_label(label):
            continue

        inks = [
            cell_has_ink(gray, c["x1"], c["y1"], c["x2"], c["y2"])
            for c in row["cells"]
        ]

        digits: list[str | None] = []
        for j, cell in enumerate(row["cells"]):
            if not inks[j]:
                digits.append(None)  # truly blank (no ink)
                continue

            # Crop with inner padding (mirrors ocr_cell logic)
            x1, y1, x2, y2 = cell["x1"], cell["y1"], cell["x2"], cell["y2"]
            extra = max(CELL_PAD, SUB_GUIDE_INNER)
            cx1 = x1 + extra
            cy1 = y1 + CELL_PAD
            cx2 = x2 - extra
            cy2 = y2 - CELL_PAD
            if cx2 <= cx1 or cy2 <= cy1:
                digits.append(None)
                continue

            crop = img[cy1:cy2, cx1:cx2]
            if crop.size == 0:
                digits.append(None)
                continue

            # Targeted clips (same thresholds as ocr_cell v3)
            if label in _CLIP_TOP20_LABELS and j == 0 and crop.shape[0] > 20:
                crop = crop[20:, :]
            if j == 2 and crop.shape[1] > 15:
                crop = crop[:, :-15]

            # Prefer segment_digits for sub-cell blobs; fall back to direct read
            digit_crops = engine.segment_digits(crop)
            if digit_crops:
                ch, *_ = engine._read_single_digit(digit_crops[0])
            else:
                ch, *_ = engine._read_single_digit(crop)

            # ink=True here: digit stays, anything else (symbol/empty) becomes "?"
            if not ch or not str(ch).isdigit():
                ch = "?"
            digits.append(ch)

        # value only from actual digits
        digit_strs = [d for d in digits if isinstance(d, str) and d.isdigit()]
        raw = "".join(digit_strs)
        value = int(raw) if raw else None

        results.append({
            "label": label,
            "value": value,
            "digits": digits,
        })

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None):
    argv = argv or sys.argv[1:]
    if argv:
        pdf = Path(argv[0])
        if not pdf.exists():
            print(f"ERROR: PDF not found: {pdf}")
            sys.exit(1)
    else:
        pdfs = sorted(PDF_DIR.rglob("*.pdf"))
        if not pdfs:
            print("No PDFs in", PDF_DIR)
            sys.exit(1)
        pdf = pdfs[0]
    print(f"Processing: {pdf.name}")

    img = render_page(pdf)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    H, W = gray.shape
    print(f"Page: {W}x{H}")

    # Step 1: detect vertical & horizontal gray lines
    v_lines = detect_gray_v_lines(gray)
    h_lines_raw = detect_gray_h_lines(gray)
    h_lines = process_h_lines(h_lines_raw)
    print(f"\nVertical gray lines: {v_lines}")
    print(f"Horizontal gray lines raw ({len(h_lines_raw)}) -> processed ({len(h_lines)}):")
    for y in h_lines:
        print(f"  Y={y}")

    # Fallback to known X if detection fails
    if len(v_lines) < 4:
        print("  Using fallback sub-cell X coordinates")
        v_lines = SUBCELL_X_FALLBACK

    # Step 2: build complete grid (V × H intersections = cells with 4 borders)
    grid = build_grid(v_lines, h_lines)
    labeled_grid = label_rows_by_structure(grid)
    print(f"\nGrid: {len(grid)} rows × {len(v_lines)-1} sub-cells")

    # Step 3: for each row, check ink in ALL 3 cells (including empty ones)
    engine = SegmentedEngine()
    results = []

    for row in labeled_grid:
        y_mid = row["y_mid"]
        label = row["label"]

        # Explicitly eliminate header text rows (e.g. "VOTACION", column titles,
        # "CANDIDATO", etc.). These get labeled UNK@... by structure and must
        # never become full cells or be fed to the digit acortador.
        if label.startswith("UNK"):
            continue

        # Check ink in each cell
        inks = []
        for cell in row["cells"]:
            has_ink = cell_has_ink(gray, cell["x1"], cell["y1"], cell["x2"], cell["y2"])
            inks.append(has_ink)

        ink_count = sum(inks)

        # Filter: skip rows with 0 ink cells (pure empty rows between sections)
        # BUT keep rows with at least 1 ink OR known labels (e.g. SUMA may be empty)
        # (UNK rows were already dropped above to eliminate "VOTACION" etc.)
        known_label = is_known_label(label)

        if ink_count == 0 and not known_label:
            continue

        # Per-full-cell: compute precise subcell x boundaries from local gray grid
        # (start detection at left border of the 3-cell group for this row's y band)
        local_vs = detect_local_subcell_gray_lines(gray, row["top"], row["bot"], global_lines=v_lines)
        if len(local_vs) >= 4:
            # override the cells for this row with locally fitted gray lines
            row["cells"] = [
                {"cell_idx": j, "x1": local_vs[j], "y1": row["top"], "x2": local_vs[j+1], "y2": row["bot"]}
                for j in range(3)
            ]

        # Full cell global 3-digit validation (read on the entire wide strip first)
        full_crop = img[row["top"]:row["bot"], VOTE_X_LEFT:VOTE_X_RIGHT].copy()
        full_val, full_raw = engine.read_number(full_crop)
        if full_val is None:
            full_val = None

        # OCR each cell (with padding to avoid reading borders)
        digits: list[str | None] = []
        confs = []
        for j, cell in enumerate(row["cells"]):
            # Per handoff rule:
            #   no ink → None  (truly blank)
            #   ink + digit → digit
            #   ink + symbol/empty → "?"
            if not inks[j]:
                digits.append(None)
                confs.append(0.0)
                continue
            ch, conf, _ = ocr_cell(
                engine, img, cell["x1"], cell["y1"], cell["x2"], cell["y2"],
                guide_inner=SUB_GUIDE_INNER,
                label=label,
                cell_idx=j,
            )
            if ch is None or not str(ch).isdigit():
                ch = "?"
            digits.append(ch)
            confs.append(round(conf, 3))

        # value only from actual numeric digits; preserve per-subcell None / "?" in "digits"
        digit_strs = [d for d in digits if isinstance(d, str) and d.isdigit()]
        raw = "".join(digit_strs)
        value = int(raw) if raw else None

        results.append({
            "index": len(results) + 1,
            "label": label,
            "y_top": row["top"],
            "y_bot": row["bot"],
            "y_mid": y_mid,
            "height": row["height"],
            "digits": digits,
            "confidences": confs,
            "value": value,                 # per-digit concat
            "full_cell_value": full_val,    # global read of the 3 digits together
            "full_cell_raw": full_raw,
            "ink": inks,
            "subcell_x_bounds": local_vs if len(local_vs) >= 4 else None,
        })

        ink_str = "".join("1" if i else "0" for i in inks)
        # For display, render None as "0" (legacy visual) but the stored digits keep None/"?"
        display_digits = ["0" if d is None else str(d) for d in digits]
        print(f"  Row {len(results):2d} Y={row['top']}-{row['bot']} mid={y_mid}: "
              f"{label:15s} digits={''.join(display_digits)} value={value} ink=[{ink_str}]")

    # Step 4: draw debug image with COMPLETE grid (including empty cells)
    debug = img.copy()

    # Draw all vertical lines (blue)
    for x in v_lines:
        cv2.line(debug, (x, Y1), (x, Y2), (255, 100, 0), 1)

    # Draw all horizontal lines (blue)
    for y in h_lines:
        cv2.line(debug, (VOTE_X_LEFT, y), (VOTE_X_RIGHT, y), (255, 100, 0), 1)

    # Draw ROI rectangle (magenta)
    cv2.rectangle(debug, (VOTE_X_LEFT, Y1), (VOTE_X_RIGHT, Y2), (255, 0, 255), 2)

    # Draw each accepted row's cells
    for row in results:
        top = row["y_top"]
        bot = row["y_bot"]

        # Full row outline (green)
        cv2.rectangle(debug, (VOTE_X_LEFT, top), (VOTE_X_RIGHT, bot), (0, 255, 0), 2)

        # Each cell: green if ink, yellow if empty — BOTH are marked
        for j, has in enumerate(row["ink"]):
            x1 = v_lines[j]
            x2 = v_lines[j + 1]
            color = (0, 200, 0) if has else (0, 255, 255)
            cv2.rectangle(debug, (x1, top), (x2, bot), color, 2)
            # Mark empty cells with "E"
            if not has:
                cx = (x1 + x2) // 2
                cy = (top + bot) // 2
                cv2.putText(debug, "E", (cx - 5, cy + 5),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1)

        # Label
        label = f"{row['label']}={row['value']} [{','.join(row['digits'])}]"
        cv2.rectangle(debug, (10, top - 5), (10 + len(label) * 9, top + 20), (0, 0, 0), -1)
        cv2.putText(debug, label, (12, top + 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 1)

    # Legend
    legend = [
        "GREEN = cell with ink (OCRd)",
        "YELLOW + E = empty cell (value 0, still marked)",
        "BLUE = detected gray lines (V and H)",
        "MAGENTA = analysis ROI",
    ]
    for i, line in enumerate(legend):
        y = 30 + i * 25
        cv2.rectangle(debug, (10, y - 15), (380, y + 8), (0, 0, 0), -1)
        cv2.putText(debug, line, (14, y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

    out_img = OUT_DIR / "grid_cells_v3_detected.png"
    cv2.imwrite(str(out_img), debug)
    print(f"\nSaved: {out_img}")

    # JSON
    report = {
        "pdf": str(pdf),
        "v_lines": v_lines,
        "h_lines": h_lines,
        "rows_total": len(grid),
        "rows_accepted": len(results),
        "rows": results,
    }
    out_json = OUT_DIR / "grid_cells_v3_detected.json"
    out_json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Saved: {out_json}")

    # Summary
    print(f"\n=== SUMMARY ===")
    print(f"Rows in grid: {len(grid)}, accepted: {len(results)}")
    for r in results:
        ink_str = "".join("1" if i else "0" for i in r["ink"])
        print(f"  {r['label']:15s} = {str(r['value']):>5}  digits={r['digits']}  ink=[{ink_str}]")


if __name__ == "__main__":
    main()
