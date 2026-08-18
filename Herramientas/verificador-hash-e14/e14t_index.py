#!/usr/bin/env python3
"""
e14t_index.py — E14T local hash index adapter for the live hash probe.

Mirrors e14d_index.py exactly, filtering the SAME shared file
(data/local_hash_index/e14d_e14t_sha256.jsonl) to source == "E14T" instead
of "E14D" — the two document types share one index artifact, built by
scripts/build_local_hash_index_e14d_e14t.py --sources E14D E14T.

Design D2: duplicate expected_name keys keep the FIRST row (deterministic,
first-wins) and are counted as collisions — same convention as e14d_index.py
and scripts/analyze/ibg_triangulate.py's _load_our_sha_index().
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from hash_probe_common import CorruptIndexError

SIN_GEO_EN_INDICE = "SIN_GEO_EN_INDICE"

DEFAULT_STEP1_CMD = "python scripts/build_local_hash_index_e14d_e14t.py --sources E14T"


class E14TIndex:
    """Adapter satisfying hash_probe_common.LocalHashIndex for E14T."""

    def __init__(self, rows: dict[str, dict], collisions: int, sin_geo_en_indice: int):
        self._rows = rows
        self.collisions = collisions
        self.sin_geo_en_indice = sin_geo_en_indice

    def __contains__(self, expected_name: str) -> bool:
        return expected_name in self._rows

    def lookup(self, filename: str) -> str | None:
        """Indexed SHA-256 for `filename`, matched by its stem (the
        server-assigned expectedName). None if absent from the index."""
        row = self._rows.get(Path(filename).stem)
        return row["sha256"] if row else None

    def geo(self, expected_name: str) -> dict | None:
        """Row (with dept/mpio/zona/puesto/mesa) for `expected_name`, or
        None when the row is absent OR its geo is null (SIN_GEO_EN_INDICE
        — caller distinguishes the two via `expected_name in self`)."""
        row = self._rows.get(expected_name)
        if not row or row.get("key") is None:
            return None
        return row


def _abort_corrupt_line(jsonl_path: Path, lineno: int, detail: str, step1_cmd: str) -> None:
    """Same actionable shape as e14d_index.py's _abort_corrupt_line()."""
    print(
        f"ERROR: hash index is corrupt or truncated: {jsonl_path}\n"
        f"  line {lineno}: {detail}\n"
        f"  Regenerate it: {step1_cmd}",
        file=sys.stderr,
    )
    raise CorruptIndexError(f"{jsonl_path}:{lineno}")


def load_e14t_index(jsonl_path: Path, step1_cmd: str = DEFAULT_STEP1_CMD) -> E14TIndex:
    """Load and filter `jsonl_path` to source == "E14T" rows.

    Raises hash_probe_common.CorruptIndexError with an actionable
    file+line message (never a raw traceback) on a malformed JSON line or
    a syntactically valid but non-object line — same guards as
    load_e14d_index().
    """
    rows: dict[str, dict] = {}
    collisions = 0
    sin_geo_en_indice = 0
    with open(jsonl_path, encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as exc:
                preview = raw[:120]
                _abort_corrupt_line(
                    jsonl_path,
                    lineno,
                    f"{exc} (offending line: {preview!r})",
                    step1_cmd,
                )
            if not isinstance(row, dict):
                _abort_corrupt_line(
                    jsonl_path,
                    lineno,
                    f"expected a JSON object, got {type(row).__name__}: {raw[:120]!r}",
                    step1_cmd,
                )
            if row.get("source") != "E14T":
                continue
            expected_name = row.get("expected_name")
            if not expected_name:
                continue
            if expected_name in rows:
                collisions += 1
                continue
            rows[expected_name] = row
            if row.get("key") is None:
                sin_geo_en_indice += 1

    return E14TIndex(rows, collisions, sin_geo_en_indice)
