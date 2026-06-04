"""
OCR Engine abstraction for E-14C digit recognition.

Provides interchangeable OCR backends behind a common interface so the
form extractor doesn't need to change when we swap engines.

Engines (all kept available — switch via get_engine(name)):
  - SegmentedEngine: per-digit segmentation + EasyOCR — PRIMARY (best balance)
  - EasyOCREngine  : easyocr on full number — FAST but weak on handwriting
  - TrOCREngine    : microsoft/trocr-base-handwritten — trained on English prose,
                     hallucinates words on digits; kept for experiments only
  - VLMEngine      : Qwen2-VL — PENDING (future, highest accuracy on tampered cells)

Why SegmentedEngine wins:
  Each E-14C vote number is written across separate cells (one digit each).
  Segmenting the digits and reading them ONE AT A TIME turns a hard
  handwritten-sequence problem into easy single-digit recognition.
  The segmented digit crops also double as the labeled training set for a
  future custom CNN classifier (the user's own fraud-detection AI).

Usage:
    engine = get_engine("segmented")
    value, raw = engine.read_number(crop_bgr)
"""
import re
from abc import ABC, abstractmethod
from typing import Optional

import cv2
import numpy as np

from src.utils.logger import get_logger

logger = get_logger(__name__)


def _digits_only(text: str, max_digits: int = 3) -> tuple[Optional[int], str]:
    """Extract a numeric value from OCR text. Vote counts are 1-3 digits."""
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None, text
    if len(digits) > max_digits:
        digits = digits[-max_digits:]
    return int(digits), text


class OCREngine(ABC):
    """Common interface for all OCR backends."""

    name: str = "base"

    @abstractmethod
    def read_number(self, crop: np.ndarray) -> tuple[Optional[int], str]:
        """
        Read a 1-3 digit number from a BGR image crop of a vote cell.
        Returns (value, raw_text). value is None if nothing readable.
        """
        ...


# ---------------------------------------------------------------------------
# TrOCR — PRIMARY engine for handwritten digits
# ---------------------------------------------------------------------------

class TrOCREngine(OCREngine):
    """
    Microsoft TrOCR fine-tuned for handwritten text.
    Much stronger than EasyOCR on handwriting, especially handwritten zeros.
    """

    name = "trocr"

    def __init__(self, model_name: str = "microsoft/trocr-base-handwritten"):
        from transformers import TrOCRProcessor, VisionEncoderDecoderModel
        import torch

        self._torch = torch
        logger.info(f"Loading TrOCR model '{model_name}' (first run downloads ~1.3GB)...")
        self.processor = TrOCRProcessor.from_pretrained(model_name)
        self.model = VisionEncoderDecoderModel.from_pretrained(model_name)
        self.model.eval()
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model.to(self.device)
        logger.info(f"TrOCR ready on {self.device}")

    def _preprocess(self, crop: np.ndarray) -> np.ndarray:
        """TrOCR expects RGB. Light upscale helps small cells."""
        if len(crop.shape) == 2:
            crop = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
        h, w = crop.shape[:2]
        if h < 64:
            scale = 64 / h
            crop = cv2.resize(crop, (int(w * scale), 64), interpolation=cv2.INTER_CUBIC)
        return cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)

    def read_number(self, crop: np.ndarray) -> tuple[Optional[int], str]:
        if crop is None or crop.size == 0:
            return None, ""
        rgb = self._preprocess(crop)
        pixel_values = self.processor(images=rgb, return_tensors="pt").pixel_values.to(self.device)
        with self._torch.no_grad():
            generated = self.model.generate(pixel_values, max_new_tokens=8)
        text = self.processor.batch_decode(generated, skip_special_tokens=True)[0]
        return _digits_only(text)

    def read_batch(self, crops: list[np.ndarray]) -> list[tuple[Optional[int], str]]:
        """Process multiple crops in one forward pass (faster on CPU/GPU)."""
        valid = [(i, c) for i, c in enumerate(crops) if c is not None and c.size > 0]
        results: list[tuple[Optional[int], str]] = [(None, "")] * len(crops)
        if not valid:
            return results

        rgbs = [self._preprocess(c) for _, c in valid]
        pixel_values = self.processor(images=rgbs, return_tensors="pt").pixel_values.to(self.device)
        with self._torch.no_grad():
            generated = self.model.generate(pixel_values, max_new_tokens=8)
        texts = self.processor.batch_decode(generated, skip_special_tokens=True)
        for (orig_i, _), text in zip(valid, texts):
            results[orig_i] = _digits_only(text)
        return results


# ---------------------------------------------------------------------------
# EasyOCR — FALLBACK engine
# ---------------------------------------------------------------------------

class EasyOCREngine(OCREngine):
    """EasyOCR with digit allowlist. Fast but weak on handwritten zeros."""

    name = "easyocr"

    def __init__(self):
        import easyocr
        self.reader = easyocr.Reader(["es"], gpu=False, verbose=False)

    def read_number(self, crop: np.ndarray) -> tuple[Optional[int], str]:
        if crop is None or crop.size == 0:
            return None, ""
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
        h, w = gray.shape
        gray = cv2.resize(gray, (int(w * 1.5), int(h * 1.5)), interpolation=cv2.INTER_CUBIC)
        gray = cv2.medianBlur(gray, 3)
        results = self.reader.readtext(
            gray, detail=0, paragraph=False,
            allowlist="0123456789", min_size=10,
            text_threshold=0.5, low_text=0.3,
        )
        return _digits_only("".join(results).strip())


# ---------------------------------------------------------------------------
# Segmented — PRIMARY engine (per-digit segmentation + EasyOCR)
# ---------------------------------------------------------------------------

class _CNNClassifier:
    """
    Thin wrapper around the trained MobileNetV2 digit classifier.
    Loaded lazily — only when models/digit_classifier.pth exists.
    """

    CLASSES = [str(i) for i in range(10)]
    MODEL_PATH = "models/digit_classifier.pth"

    def __init__(self) -> None:
        import torch
        import torch.nn as nn
        from torchvision import models, transforms
        from pathlib import Path

        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        net = models.mobilenet_v2(weights=None)
        net.classifier[1] = nn.Linear(net.last_channel, 10)
        net.load_state_dict(torch.load(self.MODEL_PATH, map_location=device))
        net.eval()
        net.to(device)

        self._net    = net
        self._device = device
        self._tf = transforms.Compose([
            transforms.Resize((64, 64)),
            transforms.Grayscale(num_output_channels=3),
            transforms.ToTensor(),
            transforms.Normalize([0.5]*3, [0.5]*3),
        ])
        logger.info(f"CNN digit classifier loaded from {self.MODEL_PATH} on {device}")

    def predict(self, digit_img: np.ndarray) -> tuple[str, float]:
        """Returns (digit_char, confidence) using the trained CNN."""
        import torch
        import torch.nn.functional as F
        from PIL import Image

        if len(digit_img.shape) == 3:
            pil = Image.fromarray(cv2.cvtColor(digit_img, cv2.COLOR_BGR2RGB))
        else:
            pil = Image.fromarray(digit_img).convert("RGB")

        tensor = self._tf(pil).unsqueeze(0).to(self._device)
        with torch.no_grad():
            logits = self._net(tensor)
            probs  = F.softmax(logits, dim=1)[0]
        idx  = int(probs.argmax())
        conf = float(probs[idx])
        return self.CLASSES[idx], round(conf, 3)


class SegmentedEngine(OCREngine):
    """
    Segments each vote cell into individual digits via connected components,
    then reads each isolated digit.

    Backend priority:
      1. CNN (models/digit_classifier.pth) — trained on labeled E-14C data, ~99% accuracy
      2. EasyOCR fallback — used when CNN model file is not present

    Also exposes segment_digits() so the digit crops can be exported as a
    labeled training dataset for a future custom classifier.
    """

    name = "segmented"

    def __init__(self):
        from pathlib import Path
        self._cnn: Optional[_CNNClassifier] = None
        if Path(_CNNClassifier.MODEL_PATH).exists():
            try:
                self._cnn = _CNNClassifier()
            except Exception as e:
                logger.warning(f"CNN load failed ({e}), falling back to EasyOCR")
        if self._cnn is None:
            import easyocr
            self.reader = easyocr.Reader(["es"], gpu=False, verbose=False)
            logger.info("SegmentedEngine using EasyOCR backend")
        else:
            self.reader = None

    @staticmethod
    def segment_digits(crop: np.ndarray) -> list[np.ndarray]:
        """
        Split a vote-cell crop into individual digit images (left to right).
        Filters out the printed cell frame and noise specks.
        """
        if crop is None or crop.size == 0:
            return []
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
        H, W = gray.shape
        # Isolate dark ink (handwritten digits), ignore faint printed cell lines
        _, bw = cv2.threshold(gray, 120, 255, cv2.THRESH_BINARY_INV)
        bw = cv2.morphologyEx(
            bw, cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
        )
        n, _, stats, _ = cv2.connectedComponentsWithStats(bw)
        boxes = []
        for i in range(1, n):
            x, y, w, h, area = stats[i]
            # Skip frame borders and noise:
            #   - too small (area, w, h)
            #   - too wide or too tall relative to cell (frame lines)
            #   - touches left or right image edge (cell border artifact)
            #   - very thin vertical line: w < 10 px
            #   - extreme aspect ratio: h/w > 8 (thin vertical stroke = border)
            if area < 40:
                continue
            if w < 10 or h < 10:
                continue
            if w > W * 0.65 or h > H * 0.65:
                continue
            if x <= 2 or (x + w) >= W - 2:  # touches left/right border
                continue
            if y <= 2 or (y + h) >= H - 2:  # touches top/bottom border
                continue
            if h > 0 and (w / h) < 0.12:   # very thin vertical line
                continue
            boxes.append((x, y, w, h))
        boxes.sort()  # left to right

        digit_imgs = []
        pad = 12
        src = crop if len(crop.shape) == 3 else cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
        for (x, y, w, h) in boxes:
            d = src[max(0, y - pad): y + h + pad, max(0, x - pad): x + w + pad]
            digit_imgs.append(d)
        return digit_imgs

    def _read_single_digit(self, digit_img: np.ndarray) -> tuple[str, float]:
        """Returns (digit_char, confidence). Uses CNN if available, else EasyOCR."""
        if self._cnn is not None:
            return self._cnn.predict(digit_img)

        # EasyOCR fallback
        gray = cv2.cvtColor(digit_img, cv2.COLOR_BGR2GRAY) if len(digit_img.shape) == 3 else digit_img
        gray = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        r = self.reader.readtext(
            gray, detail=1, allowlist="0123456789",
            text_threshold=0.3, low_text=0.2,
        )
        if r:
            _, text, conf = r[0]
            digit = text.strip()[:1] if text.strip() else "?"
            return (digit or "?"), round(float(conf), 3)
        return "?", 0.0

    def read_single_digit(self, digit_img: np.ndarray) -> tuple[str, float]:
        """Public wrapper — returns (digit_char, confidence)."""
        return self._read_single_digit(digit_img)

    def read_number(self, crop: np.ndarray) -> tuple[Optional[int], str]:
        digits = self.segment_digits(crop)
        if not digits:
            return None, ""
        raw = "".join(self._read_single_digit(d)[0] for d in digits)
        if "?" in raw:
            clean = re.sub(r"\D", "", raw)
            return (int(clean) if clean else None), raw
        return _digits_only(raw)


# ---------------------------------------------------------------------------
# VLM — PENDING (future work, not urgent)
# ---------------------------------------------------------------------------

class VLMEngine(OCREngine):
    """
    PENDING — Qwen2-VL multimodal vision model.

    Reserved for a future high-accuracy mode. A VLM reads the full cell
    like a human and is the most robust against strikethroughs/tampering,
    which is exactly what fraud detection needs. Deferred because of model
    size (~4-7GB) and per-image latency. Not urgent.
    """

    name = "vlm"

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "VLMEngine (Qwen2-VL) is planned future work — not yet implemented. "
            "Use 'trocr' for now."
        )

    def read_number(self, crop: np.ndarray) -> tuple[Optional[int], str]:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_ENGINE_CACHE: dict[str, OCREngine] = {}


def get_engine(name: str = "segmented") -> OCREngine:
    """
    Get an OCR engine by name (cached).
    Options: 'segmented' (default), 'easyocr', 'trocr', 'vlm'.
    """
    name = name.lower()
    if name in _ENGINE_CACHE:
        return _ENGINE_CACHE[name]

    if name == "segmented":
        engine = SegmentedEngine()
    elif name == "easyocr":
        engine = EasyOCREngine()
    elif name == "trocr":
        engine = TrOCREngine()
    elif name == "vlm":
        engine = VLMEngine()
    else:
        raise ValueError(
            f"Unknown OCR engine: {name}. "
            "Use 'segmented', 'easyocr', 'trocr', or 'vlm'."
        )

    _ENGINE_CACHE[name] = engine
    return engine
