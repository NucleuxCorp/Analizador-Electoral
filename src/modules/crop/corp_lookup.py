"""Corp lookup — build a (dept, mpio, zona, puesto, mesa) → corp mapping.

Reads id_informacion_mesa_corporacion from URL JSONL files and extracts
the corp code (first 2 chars) for each mesa key.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def build_corp_lookup(url_jsonl_paths: list[Path]) -> dict[tuple, str]:
    """Build a mesa-key → corp-code lookup from one or more URL JSONL files.

    For each record, the key is:
        (dept, mpio, zona, puesto, mesa)
    where dept/mpio/zona/puesto are the raw string values from the record
    and mesa is the raw string value.

    The corp code is id_informacion_mesa_corporacion[:2].

    Records that cannot be parsed (missing required fields) are skipped
    with a warning written to stderr. A running count of failed records
    is printed after processing each file.

    Args:
        url_jsonl_paths: List of JSONL file paths to read.

    Returns:
        Dict mapping (dept, mpio, zona, puesto, mesa) tuples to corp strings.
    """
    lookup: dict[tuple, str] = {}
    total_failed = 0

    for path in url_jsonl_paths:
        failed_in_file = 0
        try:
            with open(path, encoding="utf-8") as fh:
                for lineno, line in enumerate(fh, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        corp_id = record.get("id_informacion_mesa_corporacion")
                        # Field absent entirely — this JSONL doesn't encode corp; skip silently.
                        if corp_id is None:
                            continue
                        # Field present but malformed — warn.
                        if len(corp_id) < 2:
                            raise ValueError(f"short id_informacion_mesa_corporacion at line {lineno}")

                        # Support both primera-vuelta field names (cod_dept/cod_mpio/cod_puesto)
                        # and alternative naming (dept/mpio/puesto).
                        dept  = str(record.get("cod_dept")  or record.get("dept",   ""))
                        mpio  = str(record.get("cod_mpio")  or record.get("mpio",   ""))
                        zona  = str(record.get("zona",  ""))
                        puesto = str(record.get("cod_puesto") or record.get("puesto", ""))
                        mesa  = str(record.get("mesa",  ""))

                        if not (dept and mpio and zona and puesto and mesa):
                            raise ValueError(f"missing geo fields at line {lineno}")

                        key = (dept, mpio, zona, puesto, mesa)
                        lookup[key] = corp_id[:2]

                    except (KeyError, ValueError, TypeError) as exc:
                        failed_in_file += 1
                        total_failed += 1
                        print(
                            f"corp_lookup: warning — skipped parse error in {path.name} "
                            f"line {lineno}: {exc} (running total: {total_failed})",
                            file=sys.stderr,
                        )
        except OSError as exc:
            print(f"corp_lookup: warning — cannot open {path}: {exc}", file=sys.stderr)

    return lookup


def lookup_corp(
    key: tuple,
    lookup: dict[tuple, str],
    override: str | None = None,
) -> str:
    """Look up the corp code for a given mesa key.

    Args:
        key: (dept, mpio, zona, puesto, mesa) tuple.
        lookup: Dict built by build_corp_lookup().
        override: If provided, return this value regardless of lookup result.

    Returns:
        The corp code string. If override is set, returns override.
        If key is not found in lookup, logs a warning and returns "00".
    """
    if override is not None:
        return override

    corp = lookup.get(key)
    if corp is not None:
        return corp

    print(
        f"corp_lookup: warning — key not found in lookup: {key!r}; defaulting to '00'",
        file=sys.stderr,
    )
    return "00"
