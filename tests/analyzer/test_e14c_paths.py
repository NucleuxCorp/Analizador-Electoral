"""Tests for src.modules.analyzer.e14c_paths.

Covers Phase 1 of the e14c-url-builder-folder-source-of-truth change:
- parse_e14c_location() derives zona/puesto from the FOLDER path, never
  the flat filename's field4/field5 positions (the confirmed bug).
- dept/mpio still come from the filename's first two tokens (reliable).
- build_e14c_url() builds the actual Registraduria verification URL.
- Safety: both functions return None rather than ever guessing a URL —
  non-E14 filenames, flat folders (no zona_XX/puesto_XX), and filenames
  missing the flat prefix entirely all fail loudly via None.

No real filesystem access is needed — Path objects are synthetic; only
their string structure (name/parent/parent.parent) is used.
"""
from __future__ import annotations

from pathlib import Path

from src.modules.analyzer.e14c_paths import build_e14c_url, parse_e14c_location

BASE = "https://escrutiniospresidente2026.registraduria.gov.co"


# ---------------------------------------------------------------------------
# parse_e14c_location()
# ---------------------------------------------------------------------------

def test_folder_zona_puesto_override_mismatched_filename_field4():
    """Confirmed AMAZONAS/LETICIA case: filename encodes zona=00, folder
    encodes zona=01 — folder MUST win (spec.md 'known filename/folder
    mismatch' scenario)."""
    path = Path(
        "AMAZONAS/LETICIA/zona_01/puesto_01/"
        "60_001_00_01_E14_PRE_60_001_001_00_01_002_6947.pdf"
    )

    result = parse_e14c_location(path)

    assert result == ("60", "001", "01", "01")


def test_dept_mpio_from_filename_zona_puesto_from_folder_when_they_agree():
    """No-regression case: filename-embedded zona/puesto already agree
    with the folder — result is unchanged either way."""
    path = Path(
        "AMAZONAS/LETICIA/zona_01/puesto_01/"
        "60_001_01_01_E14_PRE_60_001_001_01_01_002_6947.pdf"
    )

    result = parse_e14c_location(path)

    assert result == ("60", "001", "01", "01")


def test_zona_puesto_prefix_is_stripped():
    path = Path(
        "ANTIOQUIA/MEDELLIN/zona_05/puesto_12/"
        "01_050_00_00_E14_PRE_01_050_005_00_12_003_1111.pdf"
    )

    result = parse_e14c_location(path)

    assert result == ("01", "050", "05", "12")


def test_non_e14_filename_returns_none():
    path = Path("AMAZONAS/LETICIA/zona_01/puesto_01/not_an_e14_document.pdf")

    assert parse_e14c_location(path) is None


def test_flat_folder_with_no_zona_prefix_returns_none():
    """No `zona_XX/puesto_XX` folder structure at all — the file sits
    directly in a flat dump folder."""
    path = Path(
        "flat_dump/60_001_00_01_E14_PRE_60_001_001_00_01_002_6947.pdf"
    )

    assert parse_e14c_location(path) is None


def test_puesto_folder_present_but_zona_grandparent_missing_prefix_returns_none():
    path = Path(
        "AMAZONAS/LETICIA/puesto_01/"
        "60_001_00_01_E14_PRE_60_001_001_00_01_002_6947.pdf"
    )

    assert parse_e14c_location(path) is None


def test_filename_missing_flat_prefix_entirely_returns_none():
    """Edge case found during design investigation: a small number of
    files are missing the leading `{dept}_{mpio}_{zona}_{puesto}_` prefix
    entirely (filename starts directly with `E14_...`). Must fail loudly
    (None), never silently emit a garbage URL."""
    path = Path(
        "AMAZONAS/LETICIA/zona_01/puesto_01/E14_PRE_01_196_000_00_01_023_2078.pdf"
    )

    assert parse_e14c_location(path) is None


# ---------------------------------------------------------------------------
# build_e14c_url()
# ---------------------------------------------------------------------------

def test_build_url_uses_folder_derived_zona_puesto():
    path = Path(
        "AMAZONAS/LETICIA/zona_01/puesto_01/"
        "60_001_00_01_E14_PRE_60_001_001_00_01_002_6947.pdf"
    )

    url = build_e14c_url(BASE, path)

    assert url == (
        f"{BASE}/docs/E14/60/001/01/01/E14_PRE_60_001_001_00_01_002_6947.pdf"
    )


def test_build_url_returns_none_for_non_e14_filename():
    path = Path("AMAZONAS/LETICIA/zona_01/puesto_01/not_an_e14_document.pdf")

    assert build_e14c_url(BASE, path) is None


def test_build_url_returns_none_for_flat_folder():
    path = Path(
        "flat_dump/60_001_00_01_E14_PRE_60_001_001_00_01_002_6947.pdf"
    )

    assert build_e14c_url(BASE, path) is None


def test_build_url_returns_none_for_missing_flat_prefix():
    path = Path(
        "AMAZONAS/LETICIA/zona_01/puesto_01/E14_PRE_01_196_000_00_01_023_2078.pdf"
    )

    assert build_e14c_url(BASE, path) is None
