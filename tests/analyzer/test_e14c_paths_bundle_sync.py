"""Drift guard: the citizen-bundle copy of `e14c_paths.py` must stay
byte-identical to the canonical module (per
e14c-url-builder-folder-source-of-truth design.md ADR-1, mirroring the
`http_fallback.py` precedent from e14c-verification-dual-mode).

`Herramientas/verificador-hash-e14/e14c_paths.py` is a verbatim copy of
`src/modules/analyzer/e14c_paths.py`, prefixed with exactly one
"# generated: do not edit" header line so the bundle stays a mechanically
reproducible sync rather than a hand-maintained duplicate. This test fails
the build the moment the two files diverge.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL = REPO_ROOT / "src" / "modules" / "analyzer" / "e14c_paths.py"
BUNDLE = REPO_ROOT / "Herramientas" / "verificador-hash-e14" / "e14c_paths.py"
GENERATED_HEADER_PREFIX = "# generated: do not edit"


def test_bundle_copy_exists():
    assert BUNDLE.exists(), (
        f"{BUNDLE} is missing — the citizen bundle must ship its own copy "
        f"of parse_e14c_location/build_e14c_url (ADR-1)."
    )


def test_bundle_copy_has_generated_header():
    first_line = BUNDLE.read_text(encoding="utf-8").splitlines()[0]
    assert first_line.startswith(GENERATED_HEADER_PREFIX), (
        "Bundle copy must start with a '# generated: do not edit' marker "
        "so maintainers know not to hand-edit it."
    )


def test_bundle_copy_is_byte_identical_to_canonical_module():
    canonical_content = CANONICAL.read_text(encoding="utf-8")
    bundle_content = BUNDLE.read_text(encoding="utf-8")

    # The bundle copy is the canonical content with exactly one generated
    # header line prepended — everything after that first line must match
    # the canonical module verbatim (no manual edits, no drift).
    bundle_lines = bundle_content.splitlines(keepends=True)
    assert bundle_lines[0].startswith(GENERATED_HEADER_PREFIX)
    bundle_body = "".join(bundle_lines[1:])

    assert bundle_body == canonical_content, (
        "Herramientas/verificador-hash-e14/e14c_paths.py has drifted from "
        "src/modules/analyzer/e14c_paths.py. Re-sync the bundle copy "
        "(verbatim copy + generated header) before merging."
    )
