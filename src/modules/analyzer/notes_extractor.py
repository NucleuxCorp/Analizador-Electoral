"""
notes_extractor.py — Extract handwritten jurado notes from E-14C actas.

The Colombian E-14C form has a "Notas o Constancias" section where jurados
write freeform observations, challenges, or annotations. These contain
fraud-relevant information and must be extracted as plain text.

Layout (estimated at 300 DPI — run calibrate_region() on a sample PDF
to adjust if the coordinates don't match your department's print template):

  Page 1 bottom (y > 3480): Notas / Constancias free text area
  Page 2 (full page):       Continuation notes + signatures + cedulas

Approach:
  EasyOCR with ['es', 'en'] (handles Spanish+numbers well).
  Full-page extraction on designated regions; no digit allowlist.

Usage:
    from src.modules.analyzer.notes_extractor import extract_notes, batch_extract_notes

    result = extract_notes(Path("data/pdfs/AMAZONAS/.../E14_...pdf"))
    print(result.notes_text)

    # Calibration (save debug images to inspect region coverage):
    calibrate_region(Path("data/pdfs/AMAZONAS/.../E14_...pdf"), out_dir=Path("debug"))
"""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

DPI = 300
SCALE = DPI / 72

# ---------------------------------------------------------------------------
# Region config — (page_idx, y1, y2, x1, x2) at 300 DPI
# All measurements calibrated on AMAZONAS/LETICIA template.
# Adjust if the printed form differs for another department.
# ---------------------------------------------------------------------------

# Notes section: bottom of page 1, below the totals row
NOTES_REGION_P1 = (1, 3480, 3897, 0, 1260)

# Page 2: full page covers signatures, cedulas, notas continuation, recount checkbox
NOTES_REGION_P2 = (2, 0, 3897, 0, 1260)

# Which regions to extract (order matters — text is read top-to-bottom)
NOTES_REGIONS = [NOTES_REGION_P1, NOTES_REGION_P2]

# Confidence threshold for EasyOCR — discard low-confidence fragments
MIN_CONFIDENCE = 0.25


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class NotesResult:
    pdf_path: str
    notes_text: str = ""
    fragments: list[dict] = field(default_factory=list)  # raw EasyOCR output
    pages_found: list[int] = field(default_factory=list)
    # Structured fields extracted from page 2
    recount: Optional[bool] = None       # True = hubo recuento, False = no hubo
    recount_requested_by: str = ""       # "SOLICITADO POR:" value
    recount_represented_by: str = ""     # "EN REPRESENTACIÓN DE:" value
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "pdf_path": self.pdf_path,
            "notes_text": self.notes_text,
            "fragments": self.fragments,
            "pages_found": self.pages_found,
            "recount": self.recount,
            "recount_requested_by": self.recount_requested_by,
            "recount_represented_by": self.recount_represented_by,
            "error": self.error,
            "has_notes": bool(self.notes_text.strip()),
        }


# ---------------------------------------------------------------------------
# EasyOCR reader (module-level singleton, lazy init)
# ---------------------------------------------------------------------------

_reader = None


def _get_reader():
    global _reader
    if _reader is None:
        import easyocr
        _reader = easyocr.Reader(["es", "en"], gpu=False, verbose=False)
    return _reader


# ---------------------------------------------------------------------------
# Core extractor
# ---------------------------------------------------------------------------

def _render_pages(pdf_path: Path) -> list[np.ndarray]:
    """Render all PDF pages to BGR numpy arrays at 300 DPI."""
    import fitz
    mat = fitz.Matrix(SCALE, SCALE)
    doc = fitz.open(str(pdf_path))
    pages = []
    for page in doc:
        pix = page.get_pixmap(matrix=mat)
        data = np.frombuffer(pix.samples, dtype=np.uint8)
        img = data.reshape(pix.height, pix.width, pix.n)
        if pix.n == 4:
            img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
        elif pix.n == 1:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        pages.append(img)
    doc.close()
    return pages


def _crop(pages: list[np.ndarray], region: tuple) -> Optional[np.ndarray]:
    p, y1, y2, x1, x2 = region
    if p >= len(pages):
        return None
    img = pages[p]
    h, w = img.shape[:2]
    return img[min(y1, h):min(y2, h), min(x1, w):min(x2, w)]


def _preprocess_for_text_ocr(crop: np.ndarray) -> np.ndarray:
    """
    Preprocessing optimized for full handwritten text (not digits).
    Light contrast enhancement + gentle denoise.
    """
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    # CLAHE: improves contrast on low-quality scans
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)
    gray = cv2.medianBlur(gray, 3)
    return gray


def extract_notes(
    pdf_path: Path,
    regions: Optional[list[tuple]] = None,
    min_confidence: float = MIN_CONFIDENCE,
) -> NotesResult:
    """
    Extract handwritten notes from the Notas/Constancias section of an E-14C PDF.

    Args:
        pdf_path: Path to the E-14C PDF.
        regions: List of (page, y1, y2, x1, x2) regions to scan.
                 Defaults to NOTES_REGIONS (page 1 bottom + full page 2).
        min_confidence: Discard EasyOCR fragments below this confidence.

    Returns:
        NotesResult with combined text and per-fragment details.
    """
    if regions is None:
        regions = NOTES_REGIONS

    result = NotesResult(pdf_path=str(pdf_path))
    reader = _get_reader()

    try:
        pages = _render_pages(pdf_path)
    except Exception as exc:
        result.error = f"render_failed: {exc}"
        return result

    all_fragments = []
    pages_with_content = set()

    for region in regions:
        page_idx = region[0]
        crop = _crop(pages, region)
        if crop is None or crop.size == 0:
            continue

        processed = _preprocess_for_text_ocr(crop)

        try:
            ocr_out = reader.readtext(
                processed,
                detail=1,
                paragraph=False,
                text_threshold=0.4,
                low_text=0.3,
            )
        except Exception as exc:
            result.error = (result.error or "") + f" ocr_page{page_idx}: {exc}"
            continue

        for bbox, text, conf in ocr_out:
            text = text.strip()
            if not text or conf < min_confidence:
                continue
            # Rough y-offset for ordering (bbox is [[x1,y1],[x2,y1],[x2,y2],[x1,y2]])
            y_center = (bbox[0][1] + bbox[2][1]) / 2
            all_fragments.append({
                "page": page_idx,
                "y": round(y_center),
                "text": text,
                "conf": round(conf, 3),
                "region": region,
            })
            pages_with_content.add(page_idx)

    # Sort top-to-bottom, left-to-right within each page
    all_fragments.sort(key=lambda f: (f["page"], f["y"]))

    result.fragments = [
        {"page": f["page"], "text": f["text"], "conf": f["conf"]}
        for f in all_fragments
    ]
    result.notes_text = " ".join(f["text"] for f in all_fragments).strip()
    result.pages_found = sorted(pages_with_content)

    # Parse structured fields from fragment texts
    _parse_structured_fields(result, all_fragments)

    return result


def _parse_structured_fields(result: NotesResult, fragments: list[dict]) -> None:
    """
    Extract structured data from the constancias page fragments.

    Detects:
      - recount: True/False based on X marks near "SÍ"/"NO" after "HUBO RECUENTO"
      - recount_requested_by / recount_represented_by
    """
    texts = [f["text"].upper().strip() for f in fragments]
    full = " ".join(texts)

    # Recount detection — look for "SI" or "NO" near "RECUENTO" or "RECUENTO DE VOTOS"
    # The form has two checkboxes: one X marks either "SÍ" or "NO"
    # OCR reads the X + nearby label; heuristic: first X-adjacent label wins
    if "RECUENTO" in full:
        # Find the X marks (OCR often reads as "X" or "x")
        # Look for patterns: X near SI → recount=True, X near NO → recount=False
        for i, t in enumerate(texts):
            if t in ("X", "x", "X.", "x."):
                # Check surrounding fragments for SI/NO
                window = texts[max(0, i-3): i+4]
                window_str = " ".join(window)
                if any(s in window_str for s in ("SI", "SÍ", "S1", "S|")):
                    result.recount = True
                    break
                if "NO" in window_str:
                    result.recount = False
                    break

    _SKIP_LABELS = {
        "POR", "EN", "FIRMA", "DE", "JURADO", "JURADOS", "CONSTANCIAS",
        "SOLICITADO", "REPRESENTACI", "VOTACI", "RECUENTO", "VOTOS",
    }

    # Extract "SOLICITADO POR:" value — skip if next fragment is another label
    for i, t in enumerate(texts):
        if "SOLICITADO" in t:
            for j in range(i + 1, min(i + 4, len(texts))):
                cand = fragments[j]["text"].strip()
                cand_up = cand.upper()
                if (cand and len(cand) > 2
                        and not any(lbl in cand_up for lbl in _SKIP_LABELS)
                        and not cand_up.startswith("EN REPR")):
                    result.recount_requested_by = cand
                    break
            break

    # Extract "EN REPRESENTACIÓN DE:" value
    for i, t in enumerate(texts):
        if "REPRESENTACI" in t:
            for j in range(i + 1, min(i + 4, len(texts))):
                cand = fragments[j]["text"].strip()
                cand_up = cand.upper()
                if (cand and len(cand) > 2
                        and not any(lbl in cand_up for lbl in _SKIP_LABELS)):
                    result.recount_represented_by = cand
                    break
            break


# ---------------------------------------------------------------------------
# Batch processor
# ---------------------------------------------------------------------------

def batch_extract_notes(
    pdf_dir: Path,
    output_jsonl: Optional[Path] = None,
    regions: Optional[list[tuple]] = None,
    skip_empty: bool = False,
) -> list[NotesResult]:
    """
    Extract notes from all PDFs in a directory tree.

    Args:
        pdf_dir: Root directory to scan recursively for *.pdf.
        output_jsonl: Optional JSONL output path (appended, not overwritten).
        regions: Override region coordinates (default: NOTES_REGIONS).
        skip_empty: If True, omit actas with no detected notes text.

    Returns:
        List of NotesResult objects.
    """
    from src.utils.logger import get_logger
    logger = get_logger(__name__)

    pdfs = sorted(pdf_dir.rglob("*.pdf"))
    logger.info(f"Extracting notes from {len(pdfs)} PDFs in {pdf_dir}")

    results = []
    with_notes = 0

    for i, pdf in enumerate(pdfs, 1):
        try:
            r = extract_notes(pdf, regions=regions)
            if r.error:
                logger.warning(f"[{i}/{len(pdfs)}] ERROR {pdf.name}: {r.error}")
            elif r.has_notes:
                with_notes += 1
                logger.info(f"[{i}/{len(pdfs)}] NOTES {pdf.name}: {r.notes_text[:80]!r}")
            else:
                logger.debug(f"[{i}/{len(pdfs)}] EMPTY {pdf.name}")

            if skip_empty and not r.has_notes and not r.error:
                continue

            results.append(r)

            if output_jsonl:
                with open(output_jsonl, "a", encoding="utf-8") as f:
                    f.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")

        except Exception as exc:
            logger.error(f"[{i}/{len(pdfs)}] UNHANDLED {pdf.name}: {exc}")

    logger.info(f"Done. {len(results)} processed — {with_notes} with notes text.")
    return results


# ---------------------------------------------------------------------------
# Calibration helper
# ---------------------------------------------------------------------------

def calibrate_region(
    pdf_path: Path,
    out_dir: Path = Path("debug"),
    regions: Optional[list[tuple]] = None,
) -> None:
    """
    Save annotated debug images showing the configured note regions.

    Run this on a representative PDF to verify that NOTES_REGIONS
    correctly covers the Notas/Constancias section for your form template.

    Output:
        debug/notes_region_p{N}.png  — region crop with EasyOCR bboxes drawn
        debug/notes_region_p{N}_full.png  — full page with region highlighted
    """
    if regions is None:
        regions = NOTES_REGIONS

    out_dir.mkdir(parents=True, exist_ok=True)
    pages = _render_pages(pdf_path)
    reader = _get_reader()

    for region in regions:
        page_idx, y1, y2, x1, x2 = region
        if page_idx >= len(pages):
            print(f"  Page {page_idx} not found in PDF (only {len(pages)} pages)")
            continue

        page_img = pages[page_idx].copy()
        h, w = page_img.shape[:2]
        y2c, x2c = min(y2, h), min(x2, w)

        # Highlight region on full page
        cv2.rectangle(page_img, (x1, y1), (x2c, y2c), (0, 0, 255), 8)
        scale = 1200 / max(w, 1)
        thumb = cv2.resize(page_img, (int(w * scale), int(h * scale)))
        full_out = out_dir / f"notes_region_p{page_idx}_full.png"
        cv2.imwrite(str(full_out), thumb)
        print(f"  Saved full-page debug: {full_out}")

        # Crop and run OCR
        crop = page_img[y1:y2c, x1:x2c]
        processed = _preprocess_for_text_ocr(crop)
        ocr_out = reader.readtext(processed, detail=1, paragraph=False)

        annotated = crop.copy()
        for bbox, text, conf in ocr_out:
            if conf < MIN_CONFIDENCE:
                continue
            pts = np.array(bbox, dtype=np.int32)
            cv2.polylines(annotated, [pts], True, (0, 255, 0), 2)
            cv2.putText(
                annotated, f"{text[:20]} ({conf:.2f})",
                (pts[0][0], max(pts[0][1] - 5, 0)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 0), 1,
            )
            print(f"    [{conf:.2f}] {text!r}")

        crop_out = out_dir / f"notes_region_p{page_idx}.png"
        cv2.imwrite(str(crop_out), annotated)
        print(f"  Saved region crop with OCR: {crop_out}")
