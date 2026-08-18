#!/usr/bin/env python3
"""
Non-destructive hash probe: local E14C PDFs vs live Registraduría.

Policy (hard constraints):
  - NEVER modify or delete local PDFs.
  - Download server copy into memory only.
  - If SHA-256 differs: keep server bytes under <folder>/dif_evidence/{stem}_SERVER.pdf
  - If SHA-256 matches: discard server bytes (nothing written).
  - Local hashes come from the PRECOMPUTED index (hash_index_e14c.json):
    step 1 of the flow generates the index once; this probe never re-hashes
    a file that is already indexed (on-the-fly hashing only for files
    missing from the index, disclosed per file).
  - No third-party comparison here (colombiaelige / IBG are separate,
    later tests against their own indexes).

Usage:
  python prueba_hash_registraduria.py <folder> [hash_index.json]
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

# Reuse citizen verifier transport + URL builder (no menu / no input).
import verificador_e14c as v

# Canonical folder-as-source-of-truth URL builder, imported directly (not
# only transitively via v.build_url — see resolve_url()'s docstring for why
# this explicit wiring matters and where it still can't replace the
# bare-filename fallback below; design.md D6 synced-copy convention).
from e14c_paths import build_e14c_url

# Type-agnostic shared logic (checkpoint/resume, evidence policy, report
# renderer, index guard) — see hash_probe_common.py (design.md D4).
from hash_probe_common import (
    CHECKPOINT_EVERY,
    CorruptIndexError,
    MissingIndexError,
    compare_hashes,
    guard_index_load,
    load_checkpoint,
    make_result_row,
    require_index,
    save_checkpoint,
    save_server_evidence,
    sha256_file,
    write_report,
)

REPO = Path(__file__).resolve().parents[2]

REPORT_NAME = "informe_prueba_hash.md"


def folder_zona_puesto(pdf: Path) -> tuple[str, str] | None:
    """Read zona/puesto from .../zona_XX/puesto_XX/file.pdf ancestors."""
    puesto_folder = pdf.parent.name
    zona_folder = pdf.parent.parent.name
    if not puesto_folder.startswith("puesto_") or not zona_folder.startswith("zona_"):
        return None
    zona = zona_folder[len("zona_") :]
    puesto = puesto_folder[len("puesto_") :]
    if not zona or not puesto:
        return None
    return zona, puesto


def remote_stem_parts(pdf: Path) -> list[str] | None:
    """
    Normalize to the server-side stem tokens starting at E14.

    Accepts:
      - 01_001_01_01_E14_PRE_01_001_001_01_01_001_5002
      - E14_PRE_01_001_001_01_01_001_5002
    """
    parts = pdf.stem.split("_")
    if "E14" not in parts:
        return None
    i = parts.index("E14")
    remote = parts[i:]
    # E14_PRE_dept_mpio_xxx_zona_puesto_mesa_code → at least 9 tokens
    if len(remote) < 9:
        return None
    return remote


def geo_from_remote_or_folder(pdf: Path) -> tuple[str, str, str, str, str] | None:
    """
    (dep, mun, zona, puesto, mesa_norm).

    Prefer folder zona/puesto; dep/mpio/mesa from the E14_PRE remote tail.
    Remote layout: E14 PRE dept mpio field zona puesto mesa code
    """
    remote = remote_stem_parts(pdf)
    if not remote:
        return None
    # remote: [E14, PRE, dept, mpio, xxx, zona, puesto, mesa, code, ...]
    dept, mpio = remote[2], remote[3]
    mesa_tok = remote[7]
    if not mesa_tok.isdigit():
        return None
    mesa = str(int(mesa_tok))

    zp = folder_zona_puesto(pdf)
    if zp:
        zona, puesto = zp
    else:
        zona, puesto = remote[5], remote[6]
    return dept, mpio, zona, puesto, mesa


def resolve_indexed_sha(entry) -> str:
    """Normalize a `by_filename` index entry to its sha256 string.

    The REAL `hash_index_e14c.json` schema stores `by_filename` values as
    `{"sha256": "<hex>", "vuelta": "segunda"}` dicts — `verificador_e14c.
    load_index()`'s `dict[str, str]` type hint is stale/wrong. Passing the
    raw dict straight through as `local_sha` crashed `classify()`'s
    `actual_sha256 in by_hash` with `TypeError: unhashable type: 'dict'`
    (found live via operator task 1.8).

    Returns "" (not-indexed — safe fallback to on-the-fly hashing,
    disclosed via hash_origen) for a missing entry, a dict without
    "sha256", or any other unexpected shape. Passes plain strings through
    unchanged (defensive back-compat with any index snapshot that used the
    old flat-string shape).
    """
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return entry.get("sha256") or ""
    return ""


def resolve_url(pdf: Path, by_location: dict) -> str | None:
    """Build Registraduría URL; support prefixed and bare E14_PRE_ filenames.

    Resolution order (PR1 review follow-up 4, obs 1551):
      1. `e14c_paths.build_e14c_url()` — the canonical folder-as-truth
         builder (design.md D6), called DIRECTLY here so the dependency is
         explicit in this probe's own code, not merely inherited via
         `v.build_url()`'s internal call to the same function.
      2. `v.build_url()`'s `by_location` bridge — flat-folder fallback
         keyed by filename (design.md ADR-5). Internally re-tries step 1,
         which is a cheap no-op re-check once step 1 has already failed.
      3. Bare `E14_PRE_...` filename fallback (no local dept_mpio_xx_xx
         prefix before the E14 tail). `e14c_paths.build_e14c_url()` CANNOT
         resolve this shape by design — it requires the 4-token prefix —
         so this step derives dept/mpio/mesa from the remote tail and
         zona/puesto from folder ancestors when available. NOT replaceable
         by re-wiring e14c_paths: by the time this step runs, step 1 has
         already failed for this exact pdf, so calling it again here would
         always return None (a provably dead call).
    """
    url = build_e14c_url(v.BASE_URL, pdf)
    if url:
        return url

    url = v.build_url(pdf, by_location)
    if url:
        return url

    remote = remote_stem_parts(pdf)
    geo = geo_from_remote_or_folder(pdf)
    if not remote or not geo:
        return None
    dept, mpio, zona, puesto, _mesa = geo
    url_fn = "_".join(remote) + ".pdf"
    return f"{v.BASE_URL}/docs/E14/{dept}/{mpio}/{zona}/{puesto}/{url_fn}"


class E14CTransport:
    """Transport adapter satisfying hash_probe_common.Transport, delegating
    to verificador_e14c's dual-mode downloader (urllib primary, Playwright
    fallback once blocked). `_transport_request` itself stays in
    verificador_e14c.py — it is NOT moved into the shared helper (D4)."""

    def fetch_and_hash(self, url: str) -> tuple[str, bytes, str]:
        return v.download_and_hash(url)


def main() -> int:
    if len(sys.argv) < 2:
        print(
            "Usage: python prueba_hash_registraduria.py <folder> [hash_index.json]",
            file=sys.stderr,
        )
        return 2

    folder = Path(sys.argv[1]).resolve()
    if not folder.is_dir():
        print(f"ERROR: folder not found: {folder}", file=sys.stderr)
        return 1

    # Local hash index (REQUIRED) — step 1 generates it once; this probe
    # never re-hashes indexed files and never writes the index.
    index_path = (
        Path(sys.argv[2]).resolve()
        if len(sys.argv) > 2
        else Path(__file__).resolve().parent / "hash_index_e14c.json"
    )
    step1_cmd = "generate it (build_hash_index.py) and retry."
    try:
        require_index(index_path, step1_cmd)
    except MissingIndexError:
        return 1
    # Guard the load call site (not v.load_index itself, per PR1 review
    # follow-up 1, obs 1551): a truncated/malformed index must abort with
    # an actionable message, never a raw traceback.
    try:
        by_hash, by_filename, known_dif, _meta = guard_index_load(
            index_path, step1_cmd, lambda: v.load_index(index_path)
        )
    except CorruptIndexError:
        return 1
    by_location = v.load_by_location(index_path)
    print(f"Local hash index: {len(by_filename):,} entries (read-only)", flush=True)

    pdfs = [
        p
        for p in v.scan_folder(folder)
        if "dif_evidence" not in p.parts and not p.name.endswith("_SERVER.pdf")
    ]
    print(f"Local PDFs (read-only): {len(pdfs):,}", flush=True)
    if not pdfs:
        print("No PDFs found.")
        return 0

    evidence_dir = folder / "dif_evidence"
    checkpoint_path = folder / ".prueba_hash_checkpoint.json"
    results: list[dict] = []
    done_paths: set[str] = set()
    if checkpoint_path.exists():
        try:
            results, done_paths = load_checkpoint(checkpoint_path)
            print(
                f"Resume: {len(done_paths):,} already done "
                f"(retrying previous download errors)",
                flush=True,
            )
        except Exception as exc:
            print(f"Checkpoint unreadable, starting fresh: {exc}", flush=True)
            results = []
            done_paths = set()

    pdfs = [p for p in pdfs if str(p.resolve()) not in done_paths]
    print(f"Pending downloads: {len(pdfs):,}", flush=True)

    transport = E14CTransport()

    for i, pdf in enumerate(pdfs, 1):
        print(f"[{i}/{len(pdfs)}] {pdf.name}", flush=True)
        row: dict = make_result_row(pdf.name, str(pdf.resolve()))

        local_sha = resolve_indexed_sha(by_filename.get(pdf.name))
        if local_sha:
            row["hash_origen"] = "indice"
        else:
            try:
                local_sha = sha256_file(pdf)
            except Exception as exc:
                row["status"] = "ERROR_LOCAL"
                row["note"] = str(exc)
                results.append(row)
                continue
            row["hash_origen"] = "calculado (no estaba en el indice)"
        row["local_sha256"] = local_sha

        if by_hash or by_filename:
            row["indice_local"] = v.classify(
                pdf, local_sha, by_hash, by_filename, known_dif
            )["status"]

        url = resolve_url(pdf, by_location)
        row["url"] = url or ""
        if not url:
            row["status"] = "URL_NO_CONSTRUIBLE"
            row["note"] = "Cannot build Registraduría URL from folder/filename"
            results.append(row)
            continue

        server_sha, server_data, err = transport.fetch_and_hash(url)
        if err:
            row["status"] = "ERROR_DESCARGA"
            row["note"] = err
            results.append(row)
            print(f"  ERROR_DESCARGA: {err}", flush=True)
            continue

        row["server_sha256"] = server_sha
        if compare_hashes(local_sha, server_sha):
            # Equal → discard server bytes; local untouched.
            row["status"] = "IGUAL_SERVIDOR"
            del server_data
            print("  IGUAL_SERVIDOR (server copy discarded)", flush=True)
        else:
            # Different → keep ONLY server evidence copy; local untouched.
            evidence_path = save_server_evidence(evidence_dir, pdf.stem, server_data)
            row["status"] = "DIFERENTE_SERVIDOR"
            row["evidence"] = str(evidence_path)
            if row["indice_local"] == "VERIFICADA":
                row["status"] = "MANIPULADA_EN_SERVIDOR"
            print(f"  {row['status']} → evidence {evidence_path.name}", flush=True)

        results.append(row)

        # Checkpoint every CHECKPOINT_EVERY files (resume-safe; never writes
        # local actas).
        if i % CHECKPOINT_EVERY == 0 or i == len(pdfs):
            save_checkpoint(checkpoint_path, folder, results)

    # Report next to this tool (not inside PDF tree).
    report_path = Path(__file__).resolve().parent / REPORT_NAME
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
    ts_path = Path(__file__).resolve().parent / f"informe_prueba_hash_{ts}.md"
    write_report(results, folder, report_path, ts_path)
    print(f"\nReport: {report_path}")
    print(f"Timestamped: {ts_path}")

    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("Summary:", counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
