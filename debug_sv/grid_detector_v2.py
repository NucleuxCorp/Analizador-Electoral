"""
Grid-based E-14C segunda vuelta cell detector — v2.

Uses BOTH vertical AND horizontal gray lines to form COMPLETE cell rectangles.
Empty cells (e.g. SUMA TOTAL) are still marked as valid cells with value 0.

Architecture:
  1. Detect gray vertical lines (sub-cell separators: X≈905/998/1091/1187)
  2. Detect gray horizontal lines (row boundaries)
  3. Form a COMPLETE GRID: every (vertical × horizontal) intersection = one cell
     - Each row has 3 cells (3 gaps between 4 vertical lines)
     - Each cell = (x_left, y_top, x_right, y_bot) bounded on ALL 4 sides
  4. For each cell: crop with INNER PADDING to exclude gray border lines
     - This prevents CNN from reading border fragments as digits
  5. Check ink presence; cells without ink = empty (value 0, still marked)
  6. Filter non-data rows (header text like "VOTACION") by checking
     if ANY cell in the row has digit-like ink

Run:
    python debug_sv/grid_detector_v2.py
"""
import json
import logging
import sys
from pathlib import Path

import cv2
import fitz
import numpy as np

sys.path.insert(0, ".")
from src.modules.analyzer.ocr_engines import SegmentedEngine

logger = logging.getLogger(__name__)

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

# Row height bounds for pairing horizontal lines
ROW_MIN_H = 80
ROW_MAX_H = 150

# Header zone: gaps 20–50 px valid below this Y (reference page height 3500 px @ 300 DPI)
HEADER_Y_MAX_REF = 900
PAGE_HEIGHT_REF = 3500
HEADER_Y_MAX_RATIO = HEADER_Y_MAX_REF / PAGE_HEIGHT_REF


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
    """Check if a cell (with inner padding) contains digit-like ink."""
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
    bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE,
                          cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(bw)
    H, W = crop.shape
    for k in range(1, n):
        x, y, w, h, area = stats[k]
        if area >= 40 and 6 < w < W * 0.9 and 8 < h < H * 0.9:
            # Edge filter: reject only if the blob EXCEEDS the crop bounds
            # (was too strict before: >= W-1 rejected digits that touched the last pixel,
            #  e.g. a "4" at x=38 w=38 with W=77)
            if x > 0 and (x + w) <= W:
                return True
    return False


def ocr_cell(engine, img: np.ndarray, x1: int, y1: int, x2: int, y2: int,
             pad: int = CELL_PAD) -> tuple[str, float]:
    """OCR a single cell with inner padding (excludes gray borders)."""
    cx1 = x1 + pad
    cy1 = y1 + pad
    cx2 = x2 - pad
    cy2 = y2 - pad
    if cx2 <= cx1 or cy2 <= cy1:
        return ("0", 0.0)

    crop = img[cy1:cy2, cx1:cx2]
    if crop.size == 0:
        return ("0", 0.0)

    ch, conf = engine._read_single_digit(crop)
    if ch is None or ch in {"*", "-", ".", "+", "o", "O", "null", "?", ""}:
        ch = "0"
    return (ch, float(conf))


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


def label_rows_by_structure(
    rows: list[dict],
    page_h: int | None = None,
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
# Main
# ---------------------------------------------------------------------------
def main():
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
    print(f"Horizontal gray lines raw ({len(h_lines_raw)}) → processed ({len(h_lines)}):")
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

        # Check ink in each cell
        inks = []
        for cell in row["cells"]:
            has_ink = cell_has_ink(gray, cell["x1"], cell["y1"], cell["x2"], cell["y2"])
            inks.append(has_ink)

        ink_count = sum(inks)

        # Filter: skip rows with 0 ink cells (pure empty rows between sections)
        # BUT keep rows with at least 1 ink OR known labels (e.g. SUMA may be empty)
        known_label = is_known_label(label)

        if ink_count == 0 and not known_label:
            continue

        # OCR each cell (with padding to avoid reading borders)
        digits = []
        confs = []
        for j, cell in enumerate(row["cells"]):
            # If ink check says NO digit ink, skip OCR and assign 0 directly.
            # This avoids the CNN misreading border line fragments as digits
            # (e.g. SUMA TOTAL cells showed "331" despite being empty).
            if not inks[j]:
                digits.append("0")
                confs.append(0.0)
                continue
            ch, conf = ocr_cell(engine, img, cell["x1"], cell["y1"], cell["x2"], cell["y2"])
            digits.append(ch)
            confs.append(round(conf, 3))

        raw = "".join(digits)
        try:
            value = int(raw) if raw.isdigit() else None
        except ValueError:
            value = None

        results.append({
            "index": len(results) + 1,
            "label": label,
            "y_top": row["top"],
            "y_bot": row["bot"],
            "y_mid": y_mid,
            "height": row["height"],
            "digits": digits,
            "confidences": confs,
            "value": value,
            "ink": inks,
        })

        ink_str = "".join("1" if i else "0" for i in inks)
        print(f"  Row {len(results):2d} Y={row['top']}-{row['bot']} mid={y_mid}: "
              f"{label:15s} digits={raw} value={value} ink=[{ink_str}]")

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

    out_img = OUT_DIR / "grid_cells_v2_detected.png"
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
    out_json = OUT_DIR / "grid_cells_v2_detected.json"
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