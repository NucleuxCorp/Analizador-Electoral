#!/usr/bin/env python3
"""
e14t_paths.py — E14T geo-resolved URL builder.

Mirrors e14d_paths.py exactly (design.md D1's URL-resolution rule applies
identically to E14T): resolves the download URL from the geo fields already
carried by the shared local hash index (data/local_hash_index/e14d_e14t_sha256
.jsonl, via e14t_index.py), never from url_index_e14t.json.

Evidence (verified against a real url_index_e14t.json sample, 2026-08-16):
E14T serves under the SAME path shape as E14D (`/assets/temis/pdf/...`), but
a DIFFERENT domain — the server-side subdomain carries a trailing `t`:

  BASE_D (E14D) = https://e14segundavueltapresidente.registraduria.gov.co
  BASE_T (E14T) = https://e14segundavueltapresidentet.registraduria.gov.co

URL shape: {base}/assets/temis/pdf/{dept}/{mpio}/{zona}/{puesto}/{mesa}/PRE/
{expected_name}.pdf — identical padding to E14D (delegated to
hash_probe_common.make_mesa_key()).
"""
from __future__ import annotations

from hash_probe_common import make_mesa_key

BASE_T = "https://e14segundavueltapresidentet.registraduria.gov.co"


def build_e14t_url(base: str, dept, mpio, zona, puesto, mesa, expected_name: str) -> str | None:
    """Build the E14T download URL for a resolved geo key.

    Returns None (never a wrong/guessed URL) when any geo field or
    `expected_name` is missing/falsy — the caller must treat that as
    URL_NO_CONSTRUIBLE, same policy as build_e14d_url()/build_e14c_url().
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
