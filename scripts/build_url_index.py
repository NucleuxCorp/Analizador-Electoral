"""
build_url_index.py — Maintainer tool. NOT distributed with the citizen verifier.

Reads hash_index_e14c.json from Herramientas/verificador-hash-e14/ and emits
url_index_e14c.json to the same folder mapping filename → download URL.

Run this whenever hash_index_e14c.json is updated (new snapshot) to regenerate
the URL index used by the online size-check and SHA256 runners.

Usage:
    python build_url_index.py

Output:
    Herramientas/verificador-hash-e14/url_index_e14c.json

URL construction (ADR-3, openspec/changes/e14c-url-builder-folder-source-of-
truth): this script only ever sees `by_filename` keys — bare filenames with
no folder context in its own data flow, so it cannot folder-derive
zona/puesto itself. Instead it consumes the `by_location` map that
`build_hash_index.py` now emits into `hash_index_e14c.json` (ADR-2), which
was computed there from the full checkpoint paths (folder as source of
truth). Filenames without a `by_location` entry are skipped rather than
falling back to the buggy filename-position parsing.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Config — must stay in sync with verificador_e14c.py
# ---------------------------------------------------------------------------
BASE_URL   = "https://escrutinios2vueltapresidente2026.registraduria.gov.co"
HASH_INDEX = Path("Herramientas/verificador-hash-e14/hash_index_e14c.json")
OUT_FILE   = Path("Herramientas/verificador-hash-e14/url_index_e14c.json")


def build_url(filename: str, location: str) -> str | None:
    """
    Reconstruct the Registraduría download URL for `filename` using its
    folder-derived `location` string (`"dept/mpio/zona/puesto"`, from the
    hash index's `by_location` map — see ADR-2/ADR-3). Does NOT parse
    zona/puesto from the filename itself; the flat filename's field4/field5
    positions are the confirmed source of the original bug.
    """
    stem = Path(filename).stem
    parts = stem.split("_", 5)
    if len(parts) <= 5 or parts[4] != "E14":
        return None

    segments = location.split("/")
    if len(segments) != 4:
        return None
    dept, mpio, zona, puesto = segments

    url_fn = "_".join(parts[4:]) + ".pdf"
    return f"{BASE_URL}/docs/E14/{dept}/{mpio}/{zona}/{puesto}/{url_fn}"


def main() -> None:
    if not HASH_INDEX.exists():
        print(
            f"ERROR: Index file not found: {HASH_INDEX}\n"
            f"  Place hash_index_e14c.json at {HASH_INDEX} and retry.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Loading {HASH_INDEX}...")
    data = json.loads(HASH_INDEX.read_text(encoding="utf-8"))

    by_filename: dict = data.get("by_filename", {})
    by_location: dict = data.get("by_location", {})
    filenames = sorted(by_filename.keys())
    print(f"  {len(filenames):,} filenames found in by_filename")
    print(f"  {len(by_location):,} filenames have a by_location entry")

    index: dict[str, str | dict] = {}
    skipped = 0
    skipped_no_location = 0
    for fn in filenames:
        location = by_location.get(fn)
        if location is None:
            skipped_no_location += 1
            continue
        url = build_url(fn, location)
        if url:
            index[fn] = url
        else:
            skipped += 1

    entries = len(index)
    index["_meta"] = {
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M"),
        "entries": entries,
        "skipped": skipped,
        "skipped_no_location": skipped_no_location,
        "source": str(HASH_INDEX),
    }

    OUT_FILE.write_text(
        json.dumps(index, sort_keys=True, ensure_ascii=False, indent=None),
        encoding="utf-8",
    )
    size_mb = OUT_FILE.stat().st_size / 1_048_576
    print(
        f"Written: {OUT_FILE} ({size_mb:.1f} MB, {entries:,} entries, "
        f"{skipped} skipped, {skipped_no_location} skipped for missing "
        f"by_location entry)"
    )


if __name__ == "__main__":
    main()
