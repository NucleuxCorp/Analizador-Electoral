"""
E-14C Form Field Extractor

Extracts structured data from scanned E-14C PDF forms.

Layout (300 DPI, 1260x3897 per page — calibrated on AMAZONAS/LETICIA forms):

  Page 0 (p0):
    - NIVELACION section: total votantes, total urna, total incinerados
    - Candidates 1-7: vote count per candidate
    - VOTACION column: x=880-1240 (x1=880 captures leading digit)

  Page 1 (p1):
    - Candidates 8-13: table starts at y~950 (after header row at y~800)
    - Totals: votos en blanco, nulos, no marcados, SUMA TOTAL

  Page 2 (p2):
    - Jurado signatures (1-4) + cedulas
    - Recount flag

Fraud checks performed:
  1. SUMA TOTAL == sum(all candidates + blank + null + unmarked)
  2. total_urna == SUMA TOTAL
  3. total_urna <= total_votantes
  4. No negative values
"""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import fitz  # pymupdf
import numpy as np

DPI = 300
SCALE = DPI / 72  # pts → pixels conversion factor

# A single mesa cell holds max 3 digits (0-999). Values above this are OCR garbage.
MAX_MESA_VOTES = 999
# Arithmetic differences above this are OCR noise, not genuine anomalies.
FRAUD_MAX_DIFF = 30

# ---------------------------------------------------------------------------
# Field layout — (page_idx, y1, y2, x1, x2) at 300 DPI
# ---------------------------------------------------------------------------
NIVELACION = {
    "total_votantes":    (0,  950, 1070, 880, 1240),
    "total_urna":        (0, 1070, 1200, 880, 1240),
    "total_incinerados": (0, 1220, 1330, 880, 1240),
}

# Candidates 1-7 on page 0 — x1=880 captures leading digit
CANDIDATE_ROWS_P0 = [
    (0, 1430, 1760, 880, 1240),  # candidate 1
    (0, 1780, 2110, 880, 1240),  # candidate 2
    (0, 2130, 2460, 880, 1240),  # candidate 3
    (0, 2480, 2810, 880, 1240),  # candidate 4
    (0, 2830, 3160, 880, 1240),  # candidate 5
    (0, 3180, 3510, 880, 1240),  # candidate 6
    (0, 3530, 3730, 880, 1240),  # candidate 7 — stop before footer (~y=3780)
]

# Candidates 8-13 on page 1
# Table header (CANDIDATO/AGRUPACION/VOTACION) at y~800-950; candidates start y~950
CANDIDATE_ROWS_P1 = [
    (1,  950, 1280, 880, 1240),  # candidate 8
    (1, 1300, 1630, 880, 1240),  # candidate 9
    (1, 1650, 1980, 880, 1240),  # candidate 10
    (1, 2000, 2330, 880, 1240),  # candidate 11
    (1, 2350, 2680, 880, 1240),  # candidate 12
    (1, 2700, 3000, 880, 1240),  # candidate 13
]

TOTALS = {
    "votos_blanco":      (1, 3010, 3110, 880, 1240),
    "votos_nulos":       (1, 3110, 3210, 880, 1240),
    "votos_no_marcados": (1, 3210, 3310, 880, 1240),
    "suma_total":        (1, 3300, 3480, 880, 1240),
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class FormData:
    pdf_path: str
    total_votantes: Optional[int] = None
    total_urna: Optional[int] = None
    total_incinerados: Optional[int] = None
    votos_candidatos: list[Optional[int]] = field(default_factory=list)
    votos_blanco: Optional[int] = None
    votos_nulos: Optional[int] = None
    votos_no_marcados: Optional[int] = None
    suma_total: Optional[int] = None
    ocr_raw: dict = field(default_factory=dict)

    # Genuine fraud signals (values in plausible range, small discrepancy)
    flags: list[str] = field(default_factory=list)
    # OCR quality issues (implausible values or huge discrepancies → likely misread)
    ocr_flags: list[str] = field(default_factory=list)

    def validate(self) -> None:
        """Run arithmetic fraud checks. Separates genuine anomalies from OCR noise."""
        self.flags.clear()
        self.ocr_flags.clear()

        def _ok(v: Optional[int]) -> bool:
            return v is not None and 0 <= v <= MAX_MESA_VOTES

        cands = [v for v in self.votos_candidatos if v is not None]
        blanco = self.votos_blanco or 0
        nulos = self.votos_nulos or 0
        no_marcados = self.votos_no_marcados or 0
        expected_suma = sum(cands) + blanco + nulos + no_marcados

        if self.suma_total is not None and self.suma_total != expected_suma:
            diff = abs(self.suma_total - expected_suma)
            if _ok(self.suma_total) and _ok(expected_suma) and diff <= FRAUD_MAX_DIFF:
                self.flags.append(
                    f"ARITMETICA_SUMA: suma_total={self.suma_total} != calculado={expected_suma}"
                )
            else:
                self.ocr_flags.append(
                    f"OCR_SUMA_DUDOSA: suma_total={self.suma_total} != calculado={expected_suma}"
                )

        if self.total_urna is not None and self.suma_total is not None:
            if self.total_urna != self.suma_total:
                diff = abs(self.total_urna - self.suma_total)
                if _ok(self.total_urna) and _ok(self.suma_total) and diff <= FRAUD_MAX_DIFF:
                    self.flags.append(
                        f"URNA_VS_SUMA: total_urna={self.total_urna} != suma_total={self.suma_total}"
                    )
                else:
                    self.ocr_flags.append(
                        f"OCR_URNA_DUDOSA: total_urna={self.total_urna} != suma_total={self.suma_total}"
                    )

        if self.total_urna is not None and self.total_votantes is not None:
            if self.total_urna > self.total_votantes:
                excess = self.total_urna - self.total_votantes
                if _ok(self.total_urna) and _ok(self.total_votantes) and excess <= FRAUD_MAX_DIFF:
                    self.flags.append(
                        f"VOTOS_EXCEDEN_VOTANTES: urna={self.total_urna} > votantes={self.total_votantes}"
                    )
                else:
                    self.ocr_flags.append(
                        f"OCR_VOTOS_DUDOSOS: urna={self.total_urna} > votantes={self.total_votantes}"
                    )

        for i, v in enumerate(self.votos_candidatos, 1):
            if v is not None and v < 0:
                self.flags.append(f"VALOR_NEGATIVO: candidato_{i}={v}")

    @property
    def is_suspicious(self) -> bool:
        """True only for genuine fraud signals with plausible OCR values."""
        return len(self.flags) > 0

    @property
    def needs_review(self) -> bool:
        """True when OCR produced implausible values — PDF needs manual inspection."""
        return len(self.ocr_flags) > 0

    def to_dict(self) -> dict:
        return {
            "pdf_path": self.pdf_path,
            "total_votantes": self.total_votantes,
            "total_urna": self.total_urna,
            "total_incinerados": self.total_incinerados,
            "votos_candidatos": self.votos_candidatos,
            "votos_blanco": self.votos_blanco,
            "votos_nulos": self.votos_nulos,
            "votos_no_marcados": self.votos_no_marcados,
            "suma_total": self.suma_total,
            "ocr_raw": self.ocr_raw,
            "flags": self.flags,
            "ocr_flags": self.ocr_flags,
            "is_suspicious": self.is_suspicious,
            "needs_review": self.needs_review,
        }


# ---------------------------------------------------------------------------
# Image rendering
# ---------------------------------------------------------------------------

def render_pdf_pages(pdf_path: Path) -> list[np.ndarray]:
    """Render all PDF pages to numpy BGR arrays at 300 DPI."""
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


# ---------------------------------------------------------------------------
# OCR helpers
# ---------------------------------------------------------------------------

def _preprocess_for_ocr(crop: np.ndarray) -> np.ndarray:
    """
    Minimal preprocessing for digit OCR on scanned forms.
    Heavy binarization destroys digit information — stay grayscale.
    """
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    # Upscale 1.5x — slight boost helps EasyOCR on small cells
    h, w = gray.shape
    gray = cv2.resize(gray, (int(w * 1.5), int(h * 1.5)), interpolation=cv2.INTER_CUBIC)
    # Gentle denoise only — preserve digit shape
    gray = cv2.medianBlur(gray, 3)
    return gray


def _ocr_number(crop: np.ndarray, engine) -> tuple[Optional[int], str]:
    """
    Run OCR on a digit-cell crop and extract a 1-3 digit number.
    Uses allowlist='0123456789' to prevent letter/symbol contamination.
    Returns (int_value, raw_string).
    """
    processed = _preprocess_for_ocr(crop)
    results = engine.readtext(
        processed,
        detail=0,
        paragraph=False,
        allowlist="0123456789",
        min_size=10,
        text_threshold=0.5,
        low_text=0.3,
    )
    raw = "".join(results).strip()
    # Keep only digits (OCR can still produce spaces)
    digits = re.sub(r"\D", "", raw)
    if digits:
        # Enforce max 3 digits (vote counts can't exceed 999)
        digits = digits[-3:] if len(digits) > 3 else digits
        return int(digits), raw
    return None, raw


def _crop(pages: list[np.ndarray], region: tuple) -> Optional[np.ndarray]:
    """Crop a region (page, y1, y2, x1, x2) from pages list."""
    p, y1, y2, x1, x2 = region
    if p >= len(pages):
        return None
    img = pages[p]
    h, w = img.shape[:2]
    y2 = min(y2, h)
    x2 = min(x2, w)
    return img[y1:y2, x1:x2]


# ---------------------------------------------------------------------------
# Main extractor
# ---------------------------------------------------------------------------

def extract_form(pdf_path: Path, engine=None) -> FormData:
    """
    Extract all numeric fields from an E-14C PDF.

    Args:
        pdf_path: An OCREngine instance (see ocr_engines.py).
                  Defaults to TrOCR if None.

    Returns:
        FormData with extracted values and fraud flags.
    """
    if engine is None:
        from src.modules.analyzer.ocr_engines import get_engine
        engine = get_engine("segmented")

    data = FormData(pdf_path=str(pdf_path))
    pages = render_pdf_pages(pdf_path)

    # Build an ordered list of (field_key, region) for ALL fields,
    # then OCR them in a single batch when the engine supports it.
    field_specs: list[tuple[str, tuple]] = []
    field_specs += [(k, r) for k, r in NIVELACION.items()]
    field_specs += [(f"candidato_{i}", r)
                    for i, r in enumerate(CANDIDATE_ROWS_P0 + CANDIDATE_ROWS_P1, 1)]
    field_specs += [(k, r) for k, r in TOTALS.items()]

    crops = [_crop(pages, region) for _, region in field_specs]

    # Batch OCR if available (TrOCR), else per-crop
    if hasattr(engine, "read_batch"):
        ocr_results = engine.read_batch(crops)
    else:
        ocr_results = [engine.read_number(c) for c in crops]

    # Distribute results back to FormData fields
    for (key, _), (val, raw) in zip(field_specs, ocr_results):
        data.ocr_raw[key] = raw
        if key.startswith("candidato_"):
            data.votos_candidatos.append(val)
        else:
            setattr(data, key, val)

    data.validate()
    return data


# ---------------------------------------------------------------------------
# Batch processor
# ---------------------------------------------------------------------------

def process_directory(
    pdf_dir: Path,
    engine=None,
    output_jsonl: Optional[Path] = None,
) -> list[FormData]:
    """
    Process all PDFs in a directory tree.
    Writes results to a JSONL file if output_jsonl is specified.
    """
    import json
    from src.utils.logger import get_logger
    logger = get_logger(__name__)

    if engine is None:
        from src.modules.analyzer.ocr_engines import get_engine
        engine = get_engine("segmented")

    pdfs = sorted(pdf_dir.rglob("*.pdf"))
    logger.info(f"Processing {len(pdfs)} PDFs in {pdf_dir}")

    results = []
    suspicious = 0
    needs_review = 0

    for i, pdf in enumerate(pdfs, 1):
        try:
            data = extract_form(pdf, engine)
            data.validate()
            results.append(data)

            if data.is_suspicious:
                suspicious += 1
                logger.warning(f"[{i}/{len(pdfs)}] SUSPICIOUS {pdf.name}: {data.flags}")
            elif data.needs_review:
                needs_review += 1
                logger.info(f"[{i}/{len(pdfs)}] OCR_REVIEW {pdf.name}: {data.ocr_flags}")
            else:
                logger.debug(f"[{i}/{len(pdfs)}] OK {pdf.name}")

            if output_jsonl:
                with open(output_jsonl, "a", encoding="utf-8") as f:
                    f.write(json.dumps(data.to_dict(), ensure_ascii=False) + "\n")

        except Exception as e:
            logger.error(f"[{i}/{len(pdfs)}] ERROR {pdf.name}: {e}")
            if output_jsonl:
                with open(output_jsonl, "a", encoding="utf-8") as f:
                    f.write(json.dumps({
                        "pdf_path": str(pdf),
                        "error": str(e),
                        "is_suspicious": False,
                        "flags": ["OCR_ERROR"],
                    }, ensure_ascii=False) + "\n")

    logger.info(f"Done. {len(results)} processed — {suspicious} suspicious, {needs_review} OCR review needed.")
    return results
