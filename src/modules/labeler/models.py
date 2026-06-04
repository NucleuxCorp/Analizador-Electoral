"""
models — Shared dataclasses for the labeler module.

Defines the vocabulary used across exporter, queue, manifest, and server
so that each component agrees on field names and types without circular
imports.
"""
from dataclasses import dataclass


@dataclass
class CropRecord:
    """One entry in data/labels/crops/index.jsonl — represents a single digit crop."""
    crop_id: str          # sha1(pdf_path|field_name|digit_index)[:16]
    pdf_path: str         # relative path, e.g. data/pdfs/AMAZONAS/...pdf
    field_name: str       # e.g. "candidato_3" or "TOTALS.suma_total"
    digit_index: int      # position within the cell; -1 for full-cell fallback
    label_ocr: str        # digit predicted by OCR engine, e.g. "8"
    confidence: float     # OCR confidence (0.0 if not available)
    priority: int         # 0 = needs_review, 1 = is_suspicious, 2 = normal
    full_cell_crop_id: str  # crop_id of the parent cell crop (context panel)
    page_index: int       # PDF page where the cell appears (0-based)


# QueueItem is the same shape as CropRecord — alias for semantic clarity
QueueItem = CropRecord


@dataclass
class LabelDecision:
    """Result of parsing a human's keypress or text input."""
    label_human: str   # the digit(s) the human assigned, e.g. "6" or "149"
    amended: bool      # True if the human indicated an enmienda (E-prefix)
    is_fallback: bool  # True when digit_index == -1 (full-cell multi-digit)
