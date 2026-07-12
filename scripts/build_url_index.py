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

# SOURCE: Herramientas/verificador-hash-e14/verificador_e14c.py::build_url()
# Keep BASE_URL and build_url() in sync with that file.
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


def build_url(filename: str) -> str | None:
    """
    Reconstruct Registraduría download URL from an E14C filename.
    SOURCE: Herramientas/verificador-hash-e14/verificador_e14c.py::build_url()
    Keep this function in sync with that copy.
    """
    stem = Path(filename).stem
    parts = stem.split("_", 5)
    if len(parts) > 5 and parts[4] == "E14":
        dept, mpio, zona, puesto = parts[0], parts[1], parts[2], parts[3]
        url_fn = "_".join(parts[4:]) + ".pdf"
        return f"{BASE_URL}/docs/E14/{dept}/{mpio}/{zona}/{puesto}/{url_fn}"
    return None


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
    filenames = sorted(by_filename.keys())
    print(f"  {len(filenames):,} filenames found in by_filename")

    index: dict[str, str | dict] = {}
    skipped = 0
    for fn in filenames:
        url = build_url(fn)
        if url:
            index[fn] = url
        else:
            skipped += 1

    entries = len(index)
    index["_meta"] = {
        "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M"),
        "entries": entries,
        "skipped": skipped,
        "source": str(HASH_INDEX),
    }

    OUT_FILE.write_text(
        json.dumps(index, sort_keys=True, ensure_ascii=False, indent=None),
        encoding="utf-8",
    )
    size_mb = OUT_FILE.stat().st_size / 1_048_576
    print(f"Written: {OUT_FILE} ({size_mb:.1f} MB, {entries:,} entries, {skipped} skipped)")


if __name__ == "__main__":
    main()
