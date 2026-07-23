# generated: do not edit — synced from src/modules/analyzer/e14c_paths.py
"""Canonical E14C dept/mpio/zona/puesto extraction and URL builder.

The bug this module fixes: every one of the 8 duplicated `build_url()`
copies across the E14C tooling derives `zona`/`puesto` from the flat local
filename's underscore split (`stem.split("_", 5)`, positions 2/3). That
split reads an unresolved `field4` placeholder in the slot that should hold
the real `zona`/`puesto`, producing a non-existent server path for 22,967
of 118,543 PDFs (19.4%) — see
`openspec/changes/e14c-url-builder-folder-source-of-truth/design.md`.

Fix (ADR-1): `dept`/`mpio` stay sourced from the filename (`stem.split("_",
5)[0:2]`) — those two positions are reliable, unaffected by the `field4`
bug, and echoed in the server URL tail. `zona`/`puesto` are instead derived
from the **folder path** (`.../{DEPT}/{MPIO}/zona_XX/puesto_XX/file.pdf`),
which is the confirmed, index-driven download layout written by
`src/modules/e14c/downloader.py::_dest_path`.

Hard constraint (mirrors the `http_fallback.py` precedent from
`e14c-verification-dual-mode`): this module imports NOTHING from the rest
of `src/` — stdlib only. That is what allows it to be copied verbatim into
the citizen-facing bundle at
`Herramientas/verificador-hash-e14/e14c_paths.py` without dragging
unrelated project code (torch/OCR/etc.) along with it.

Safety principle: both functions return `None` rather than ever emitting a
wrong or partially-guessed URL — a filename that isn't a recognizable E14
document, or a folder that lacks the `zona_XX/puesto_XX` structure (a
"flat" folder), fails loudly (via `None`) instead of silently falling back
to the buggy filename-position parsing.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

_ZONA_PREFIX = "zona_"
_PUESTO_PREFIX = "puesto_"


def parse_e14c_location(pdf_path: Path) -> Optional[tuple[str, str, str, str]]:
    """Derive `(dept, mpio, zona, puesto)` for an E14C PDF path.

    `dept`/`mpio` come from the filename's first two underscore-separated
    tokens (`stem.split("_", 5)[0:2]`) — confirmed reliable. `zona`/`puesto`
    come from the folder path: `zona` from `pdf_path.parent.parent.name`
    (stripped of the `zona_` prefix), `puesto` from `pdf_path.parent.name`
    (stripped of the `puesto_` prefix).

    Returns `None` when:
    - the filename isn't a recognizable E14 document (fewer than 6
      underscore-separated tokens, or token index 4 isn't `"E14"`), or
    - the folder path doesn't carry the expected `zona_XX/puesto_XX`
      structure (a "flat" folder with no folder-derivable zona/puesto).
    """
    stem = pdf_path.stem
    parts = stem.split("_", 5)
    if len(parts) <= 5 or parts[4] != "E14":
        return None

    dept, mpio = parts[0], parts[1]

    puesto_folder = pdf_path.parent.name
    zona_folder = pdf_path.parent.parent.name

    if not puesto_folder.startswith(_PUESTO_PREFIX):
        return None
    if not zona_folder.startswith(_ZONA_PREFIX):
        return None

    zona = zona_folder[len(_ZONA_PREFIX):]
    puesto = puesto_folder[len(_PUESTO_PREFIX):]
    if not zona or not puesto:
        return None

    return dept, mpio, zona, puesto


def build_e14c_url(base: str, pdf_path: Path) -> Optional[str]:
    """Build the Registraduria verification URL for an E14C PDF path.

    Uses `parse_e14c_location` for `dept/mpio/zona/puesto` (folder as
    source of truth) and the filename's `E14_...` tail (token index 4
    onward) for the server-side document name. Returns `None` under the
    same conditions as `parse_e14c_location` (never emits a wrong URL).
    """
    location = parse_e14c_location(pdf_path)
    if location is None:
        return None

    dept, mpio, zona, puesto = location
    parts = pdf_path.stem.split("_", 5)
    url_fn = "_".join(parts[4:]) + ".pdf"

    return f"{base}/docs/E14/{dept}/{mpio}/{zona}/{puesto}/{url_fn}"
