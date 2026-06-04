"""
manifest — Crash-safe ManifestWriter for the labeler portal.

Handles three concerns:
  1. Atomic PNG placement into the ImageFolder tree (digits/ or enmiendas/).
  2. fsync-safe append of one JSON line per label to manifest.jsonl.
  3. Parsing human keypress input into a LabelDecision.

No Flask dependency — safe to import without Flask installed.
"""
import json
import os
import shutil
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.modules.labeler.models import LabelDecision

_MANIFEST_FILENAME = "manifest.jsonl"

# Visual variants of zero — map input char → folder name under digits/
ZERO_VARIANTS: dict[str, str] = {
    "*": "asterisco",
    "-": "guion",
    ".": "punto",
    "+": "asterisco",
}


class ManifestWriter:
    """
    Append-safe writer for manifest.jsonl + ImageFolder PNG placement.

    Thread-safety: a single threading.Lock guards the 3-step write operation
    so the class is correct if Flask's threaded=True is ever enabled.

    Crash-safety contract (ADR-6):
      1. PNG written atomically via os.replace() to final ImageFolder path.
      2. JSON line written + f.flush() + os.fsync() before HTTP 200 is returned.
      3. Cursor rewrite delegated to ValidationQueue.advance() after this returns.

    A trailing partial JSON line (from CTRL+C mid-write) is tolerated on load
    by catching json.JSONDecodeError on the last non-empty line only.
    """

    def __init__(self, manifest_path: Path, labels_dir: Path) -> None:
        """
        Args:
            manifest_path: Path to data/labels/manifest.jsonl.
            labels_dir: Root of data/labels/ — digits/ and enmiendas/ live here.
        """
        self._manifest_path = manifest_path
        self._labels_dir = labels_dir
        self._lock = threading.Lock()
        self.known_crop_ids: set[str] = set()

        # Load existing crop_id set for deduplication / queue skip
        self._manifest_path.parent.mkdir(parents=True, exist_ok=True)
        if self._manifest_path.exists():
            self._load_known_ids()

        # Open manifest in append mode — keep handle for fsync
        self._fh = open(self._manifest_path, "a", encoding="utf-8")  # noqa: WPS515

    # ------------------------------------------------------------------
    # Input parsing
    # ------------------------------------------------------------------

    @staticmethod
    def parse_input(raw: str, label_ocr: str) -> LabelDecision:
        """
        Parse a human's raw keypress string into a LabelDecision.

        Protocol:
          ""  / Enter  → confirm OCR guess; amended=False
          "6"          → clean digit 6; amended=False
          "E6"         → digit 6 with enmienda flag; amended=True
          "E"          → confirm OCR with enmienda; amended=True
          "149"        → multi-digit fallback (digit_index=-1); amended=False

        Raises:
            ValueError: if a single-character input is not a digit 0-9.
        """
        raw = raw.strip()

        # Empty → confirm OCR prediction
        if not raw:
            return LabelDecision(label_human=label_ocr, amended=False, is_fallback=False)

        # Enmienda prefix
        amended = raw.upper().startswith("E")
        value = raw[1:] if amended else raw

        # Bare "E" with no digit → confirm OCR with enmienda
        if amended and not value:
            return LabelDecision(label_human=label_ocr, amended=True, is_fallback=False)

        # Multi-digit fallback (digit_index == -1 items)
        if len(value) > 1:
            if not value.isdigit():
                raise ValueError(f"Multi-digit input must be all digits, got: {value!r}")
            return LabelDecision(label_human=value, amended=amended, is_fallback=True)

        # Zero variant symbols (* - .) — stored in their own folder, train as class 0
        if value in ZERO_VARIANTS:
            return LabelDecision(label_human=value, amended=amended, is_fallback=False)

        # Single digit validation
        if not value.isdigit():
            raise ValueError(
                f"Input must be a digit 0-9, a zero variant (*, -, .), or E-prefixed, got: {raw!r}"
            )

        return LabelDecision(label_human=value, amended=amended, is_fallback=False)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def append(
        self,
        row: dict,
        crop_png_src: Path,
    ) -> Path:
        """
        Atomically write one label to disk.

        Steps (in order):
          1. Determine destination under digits/{label}/ or enmiendas/{label}/.
          2. Write PNG atomically via temp file + os.replace().
          3. Write JSON line to manifest; flush + fsync.

        Args:
            row: Full manifest row dict (caller builds it).
            crop_png_src: Source PNG path in data/labels/crops/.

        Returns:
            Final PNG destination path (already written).
        """
        with self._lock:
            label = str(row["label_human"])
            amended = bool(row.get("amended", False))
            crop_id = row["crop_id"]

            # Determine ImageFolder bucket
            if amended:
                bucket = self._labels_dir / "enmiendas" / label
            elif label in ZERO_VARIANTS:
                bucket = self._labels_dir / "digits" / ZERO_VARIANTS[label]
            elif label == "_fullcell" or row.get("digit_index") == -1:
                bucket = self._labels_dir / "digits" / "_fullcell"
            else:
                bucket = self._labels_dir / "digits" / label

            bucket.mkdir(parents=True, exist_ok=True)
            dst_png = bucket / f"img_{crop_id}.png"

            # Step 1: atomic PNG placement
            if crop_png_src.exists() and not dst_png.exists():
                fd, tmp_png = tempfile.mkstemp(dir=bucket, suffix=".png")
                os.close(fd)
                try:
                    shutil.copy2(str(crop_png_src), tmp_png)
                    os.replace(tmp_png, dst_png)
                except Exception:
                    try:
                        os.unlink(tmp_png)
                    except OSError:
                        pass
                    raise

            # Update row with final img_path
            row["img_path"] = str(dst_png)

            # Step 2: write JSON line + fsync
            line = json.dumps(row, ensure_ascii=False) + "\n"
            self._fh.write(line)
            self._fh.flush()
            os.fsync(self._fh.fileno())

            self.known_crop_ids.add(crop_id)
            return dst_png

    def close(self) -> None:
        """Close the manifest file handle."""
        try:
            self._fh.close()
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_known_ids(self) -> None:
        """Load existing crop_ids from manifest for deduplication."""
        lines = self._manifest_path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                cid = row.get("crop_id")
                if cid:
                    self.known_crop_ids.add(cid)
            except json.JSONDecodeError:
                # Tolerate one trailing partial line (crash mid-write)
                if i < len(lines) - 1:
                    raise
