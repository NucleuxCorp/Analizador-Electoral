#!/usr/bin/env python3
"""
e14d_paths.py — E14D geo-resolved URL builder.

design.md D1: E14D resolves its download URL from the geo fields already
carried by its own local hash index (data/local_hash_index/e14d_e14t_sha256
.jsonl, via e14d_index.py), never from url_index_e14d.json — that cache is
31MB, untracked, and this probe never loads it.

Evidence (design.md D1, read-only sampling of url_index_e14d.json): the URL
path segments are EXACTLY `hash_probe_common.make_mesa_key()`'s padding.
Confirmed against `scripts/e14/build_e14t_e14d_urls.py`, the real script
that generates the geo-key source (`data/e14d_sv_urls.jsonl`) this probe's
adapter reads: `BASE_D = "https://e14segundavueltapresidente.registraduria
.gov.co"`, dept.zfill(2)/mpio.zfill(3)/zona.zfill(3)/puesto.zfill(2)/
mesa.zfill(3), URL shape `{base}/assets/temis/pdf/{dept}/{mpio}/{zona}/
{puesto}/{mesa}/PRE/{expected_name}.pdf`.
"""
from __future__ import annotations

from hash_probe_common import make_mesa_key

BASE_D = "https://e14segundavueltapresidente.registraduria.gov.co"


def build_e14d_url(base: str, dept, mpio, zona, puesto, mesa, expected_name: str) -> str | None:
    """Build the E14D download URL for a resolved geo key.

    Returns None (never a wrong/guessed URL) when any geo field or
    `expected_name` is missing/falsy — the caller must treat that as
    URL_NO_CONSTRUIBLE, same policy as e14c_paths.build_e14c_url().

    Padding is delegated to `hash_probe_common.make_mesa_key()` (PR2 review
    follow-up: this module used to duplicate the zfill widths inline —
    delegation kills that duplication and guarantees this URL's path
    segments can never drift from make_mesa_key's padding scheme, design.md
    D1's core assumption).
    """
    if not all([dept, mpio, zona, puesto, mesa, expected_name]):
        return None
    dept_p, mpio_p, zona_p, puesto_p, mesa_p = make_mesa_key(
        dept, mpio, zona, puesto, mesa
    ).split("_")
    return (
        f"{base}/assets/temis/pdf/"
        f"{dept_p}/{mpio_p}/{zona_p}/{puesto_p}/{mesa_p}/PRE/{expected_name}.pdf"
    )
