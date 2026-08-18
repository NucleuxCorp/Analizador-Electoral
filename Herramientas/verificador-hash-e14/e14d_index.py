#!/usr/bin/env python3
"""
e14d_index.py — E14D local hash index adapter for the live hash probe.

Reads data/local_hash_index/e14d_e14t_sha256.jsonl (produced by
scripts/build_local_hash_index_e14d_e14t.py), filters rows to
source == "E14D", and builds a {expected_name: row} lookup keyed by the
server-assigned expectedName filename stem (design.md D2). No new index
artifact is created — this is an in-memory adapter over the existing file.

Design D2: duplicate expected_name keys keep the FIRST row (deterministic,
first-wins) and are counted as collisions — same convention as
scripts/analyze/ibg_triangulate.py's _load_our_sha_index().

Coverage reason tracked here (design.md D1):
  - SIN_GEO_EN_INDICE: the row exists (matched by expected_name) but its
    geo fields are null — build_local_hash_index_e14d_e14t.py found no
    match in data/e14d_sv_urls.jsonl for this file.

SIN_FILA_EN_INDICE (a local PDF on disk with NO row at all in this index)
is NOT tracked here — the adapter only knows what IS in the index; the
caller (prueba_hash_e14d.py) computes that reason while iterating the
on-disk corpus against this adapter.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from hash_probe_common import CorruptIndexError

SIN_GEO_EN_INDICE = "SIN_GEO_EN_INDICE"

# PR2 review follow-up 2 (obs 1560): default step-1 command, now a single
# source of truth — prueba_hash_e14d.py imports this instead of duplicating
# the same string as its own STEP1_CMD constant.
DEFAULT_STEP1_CMD = "python scripts/build_local_hash_index_e14d_e14t.py --sources E14D"


class E14DIndex:
    """Adapter satisfying hash_probe_common.LocalHashIndex for E14D."""

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
    """Print + raise the ONE actionable shape shared with
    hash_probe_common.guard_index_load()'s generic message (PR2 review
    follow-up 2, obs 1560): same opening/closing lines, line-specific
    detail folded into the middle line instead of a second custom shape.
    `guard_index_load()`'s own step1_cmd argument stays meaningful for this
    path too — it flows through here from the caller (previously this
    module raised CorruptIndexError with its own hardcoded command BEFORE
    guard_index_load's wrapper ever saw it, making its step1_cmd dead for
    the E14D path)."""
    print(
        f"ERROR: hash index is corrupt or truncated: {jsonl_path}\n"
        f"  line {lineno}: {detail}\n"
        f"  Regenerate it: {step1_cmd}",
        file=sys.stderr,
    )
    raise CorruptIndexError(f"{jsonl_path}:{lineno}")


def load_e14d_index(jsonl_path: Path, step1_cmd: str = DEFAULT_STEP1_CMD) -> E14DIndex:
    """Load and filter `jsonl_path` to source == "E14D" rows.

    Raises hash_probe_common.CorruptIndexError with an actionable
    file+line message (never a raw traceback) on:
      - a malformed JSON line, or
      - a syntactically valid but non-object line (bare `42`, `null`,
        `[1, 2, 3]`) — `row.get(...)` below would otherwise raise a raw
        AttributeError, contradicting this docstring (PR2 review follow-up
        3, obs 1560).
    Same shape as hash_probe_common.guard_index_load()'s generic message —
    see `_abort_corrupt_line()`.
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
            if row.get("source") != "E14D":
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

    return E14DIndex(rows, collisions, sin_geo_en_indice)
