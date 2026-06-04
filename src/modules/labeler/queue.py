"""
queue — ValidationQueue for the labeler portal.

Reads the crop index (index.jsonl), sorts items by priority, and manages
a persistent cursor so labeling can be resumed after a server restart.

No Flask dependency — this module is safe to import without Flask installed.
"""
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from src.modules.labeler.models import CropRecord, QueueItem

_PROGRESS_FILENAME = "queue_progress.json"


class ValidationQueue:
    """
    Priority-ordered, cursor-persisted validation queue.

    Priority order: 0 (needs_review) < 1 (is_suspicious) < 2 (normal).
    Items whose crop_id is already in `known_crop_ids` are skipped on load.
    Cursor is atomically written to `data/labels/queue_progress.json`.
    """

    def __init__(self) -> None:
        self._items: list[QueueItem] = []
        self._cursor: int = 0
        self._labeled: int = 0
        self._skipped: int = 0
        self._progress_path: Optional[Path] = None

    # ------------------------------------------------------------------
    # Factory / loader
    # ------------------------------------------------------------------

    @classmethod
    def load(
        cls,
        index_path: Path,
        known_crop_ids: Optional[set] = None,
    ) -> "ValidationQueue":
        """
        Load the queue from index.jsonl, restore cursor from progress file.

        Args:
            index_path: Path to data/labels/crops/index.jsonl.
            known_crop_ids: Set of crop_id strings already labeled (from
                ManifestWriter.known_crop_ids). Items in this set are
                excluded from the queue so they are never re-presented.
        """
        known = known_crop_ids or set()
        q = cls()
        q._progress_path = index_path.parent.parent / _PROGRESS_FILENAME

        # Parse index.jsonl
        items: list[QueueItem] = []
        if index_path.exists():
            with open(index_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if row.get("crop_id") in known:
                        continue
                    items.append(CropRecord(
                        crop_id=row["crop_id"],
                        pdf_path=row["pdf_path"],
                        field_name=row["field_name"],
                        digit_index=row.get("digit_index", 0),
                        label_ocr=row.get("label_ocr", "?"),
                        confidence=row.get("confidence", 0.0),
                        priority=row.get("priority", 2),
                        full_cell_crop_id=row.get("full_cell_crop_id", ""),
                        page_index=row.get("page_index", 0),
                    ))

        # Stable sort: (priority, pdf_path, field_name, digit_index)
        items.sort(key=lambda r: (r.priority, r.pdf_path, r.field_name, r.digit_index))
        q._items = items

        # Restore cursor from progress file
        if q._progress_path.exists():
            try:
                with open(q._progress_path, encoding="utf-8") as f:
                    progress = json.load(f)
                q._cursor = min(int(progress.get("cursor", 0)), len(items))
                q._labeled = int(progress.get("labeled", 0))
                q._skipped = int(progress.get("skipped", 0))
            except (json.JSONDecodeError, KeyError, ValueError):
                q._cursor = 0

        return q

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def current(self) -> Optional[QueueItem]:
        """Return the item at the current cursor position, or None if exhausted."""
        if self._cursor < len(self._items):
            return self._items[self._cursor]
        return None

    def advance(self, *, labeled: bool = True) -> None:
        """
        Move cursor forward by one.

        Args:
            labeled: True when the item was labeled; False when skipped.
        """
        if self._cursor < len(self._items):
            if labeled:
                self._labeled += 1
            else:
                self._skipped += 1
            self._cursor += 1
            self._persist_cursor()

    def back(self) -> bool:
        """Move cursor back one position. Returns True if moved, False if already at start."""
        if self._cursor <= 0:
            return False
        self._cursor -= 1
        self._labeled = max(0, self._labeled - 1)
        self._persist_cursor()
        return True

    def seek(self, n: int) -> None:
        """Move cursor to absolute position n (clamped to valid range)."""
        self._cursor = max(0, min(n, len(self._items)))
        self._persist_cursor()

    def remaining(self) -> int:
        """Number of items not yet processed (labeled + skipped)."""
        return max(0, len(self._items) - self._cursor)

    @property
    def total(self) -> int:
        """Total items in the queue (after deduplication against manifest)."""
        return len(self._items)

    @property
    def labeled(self) -> int:
        """Number of items labeled so far this session (+ restored from progress)."""
        return self._labeled

    @property
    def cursor(self) -> int:
        return self._cursor

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _persist_cursor(self) -> None:
        """Atomically write progress to disk using temp file + os.replace()."""
        if self._progress_path is None:
            return
        data = json.dumps({
            "cursor": self._cursor,
            "total": len(self._items),
            "labeled": self._labeled,
            "skipped": self._skipped,
        })
        self._progress_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=self._progress_path.parent,
            prefix=".queue_progress_",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp, self._progress_path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
