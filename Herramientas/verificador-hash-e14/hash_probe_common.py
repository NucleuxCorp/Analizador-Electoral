#!/usr/bin/env python3
"""
hash_probe_common.py — type-agnostic shared helper for the live hash probes.

Extracted from `prueba_hash_registraduria.py` (the E14C probe) so future
per-type probes (E14D, E14T, ...) reuse the same checkpoint/resume, report
rendering, dif_evidence policy, and index-guard logic instead of
re-implementing it (design.md D4).

Stays OUT of this module (kept type-specific, per D4):
  - The actual network transport (`_transport_request`, urllib/Playwright
    dual-mode routing) — every probe injects its own `Transport`
    implementation satisfying the `Transport` protocol below.
  - Index format/lookup (`load_index`, `classify`, `scan_folder`,
    `build_url`) — E14C keeps using `verificador_e14c` directly; other
    types will bring their own adapters.
  - Any status-vocabulary escalation logic (e.g. E14C's
    MANIPULADA_EN_SERVIDOR) — each probe decides its own vocabulary.

Policy (hard constraints, shared by every probe built on this module):
  - NEVER modify or delete local PDFs.
  - Download server copy into memory only.
  - If SHA-256 differs: keep server bytes under <folder>/dif_evidence/{stem}_SERVER.pdf
  - If SHA-256 matches: discard server bytes (nothing written).
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Protocol

CHUNK = 64 * 1024

# Checkpoint statuses that get retried on resume instead of kept as-is.
RETRY_STATUSES = ("ERROR_DESCARGA",)
CHECKPOINT_EVERY = 25


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def compare_hashes(local_sha256: str, server_sha256: str) -> bool:
    """True when the local and server hashes match."""
    return local_sha256 == server_sha256


# ---------------------------------------------------------------------------
# Result row factory
# ---------------------------------------------------------------------------
def make_result_row(filename: str, path: str) -> dict:
    """Default result-row shape shared by every per-type hash probe."""
    return {
        "filename": filename,
        "path": path,
        "local_sha256": "",
        "server_sha256": "",
        "status": "",
        "indice_local": "",
        "hash_origen": "",
        "evidence": "",
        "url": "",
        "note": "",
    }


# ---------------------------------------------------------------------------
# dif_evidence policy
# ---------------------------------------------------------------------------
def save_server_evidence(evidence_dir: Path, stem: str, data: bytes) -> Path:
    """Persist the server copy under `{evidence_dir}/{stem}_SERVER.pdf`.
    Never touches the local PDF — this is the ONLY file this function writes."""
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = evidence_dir / f"{stem}_SERVER.pdf"
    evidence_path.write_bytes(data)
    return evidence_path


# ---------------------------------------------------------------------------
# Checkpoint (resume-safe)
# ---------------------------------------------------------------------------
def load_checkpoint(checkpoint_path: Path) -> tuple[list[dict], set[str]]:
    """Load a checkpoint file already known to exist.

    Drops any row whose status is in RETRY_STATUSES so it gets retried on
    resume. Returns (results, done_paths). Raises whatever exception
    reading/parsing the file raises — callers decide how to report/recover
    (matches the original try/except boundary in the E14C probe's main()).
    """
    prev = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    results = [r for r in prev.get("results", []) if r.get("status") not in RETRY_STATUSES]
    done_paths = {r["path"] for r in results}
    return results, done_paths


def save_checkpoint(
    checkpoint_path: Path,
    folder,
    results: list[dict],
    now: datetime | None = None,
) -> None:
    """Persist `results` (never writes to any PDF path — checkpoint file only)."""
    now = now or datetime.now()
    checkpoint_path.write_text(
        json.dumps(
            {
                "folder": str(folder),
                "updated_at": now.isoformat(timespec="seconds"),
                "results": results,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Index guard — index-first, index required (spec: "Index-first, index required")
# ---------------------------------------------------------------------------
class MissingIndexError(Exception):
    """Raised by require_index() when the local hash index file is absent."""


def require_index(path: Path, step1_cmd: str) -> None:
    """Abort BEFORE any network call when `path` doesn't exist.

    Prints an actionable error (including the step-1 rebuild command) to
    stderr and raises MissingIndexError. Callers translate this into a
    process exit code at the CLI boundary. No-op (returns None) when the
    index is present.
    """
    if not path.exists():
        print(
            f"ERROR: hash index not found: {path}\n"
            f"  Step 1 of the flow: {step1_cmd}",
            file=sys.stderr,
        )
        raise MissingIndexError(str(path))


class CorruptIndexError(Exception):
    """Raised by guard_index_load() when an index file EXISTS but fails to
    parse (truncated/malformed JSON or JSONL). Complements require_index(),
    which only guards the missing-file case."""


def guard_index_load(path: Path, step1_cmd: str, loader):
    """Call the zero-arg `loader()` and convert any parse failure into an
    actionable CorruptIndexError instead of a raw traceback.

    Every probe's index-loading call MUST go through this wrapper (spec:
    "index-first, index required" extends to "index must be readable, not
    just present"). Does NOT modify the individual per-type loader
    functions themselves (e.g. verificador_e14c.load_index) — it wraps the
    call site, so a truncated/malformed index surfaces the same actionable
    message regardless of which type-specific loader raised it.

    Also catches OSError (PermissionError/IsADirectoryError/a delete race
    between require_index()'s existence check and the actual read) — an
    index that EXISTS but can't be opened is the same actionable situation
    as one that fails to parse, not a raw traceback (PR2 review follow-up).
    """
    try:
        return loader()
    except (json.JSONDecodeError, ValueError, KeyError, OSError) as exc:
        print(
            f"ERROR: hash index is corrupt or truncated: {path}\n"
            f"  {exc}\n"
            f"  Regenerate it: {step1_cmd}",
            file=sys.stderr,
        )
        raise CorruptIndexError(str(path)) from exc


# ---------------------------------------------------------------------------
# Transport protocol — every probe injects its own implementation
# ---------------------------------------------------------------------------
class Transport(Protocol):
    def fetch_and_hash(self, url: str) -> tuple[str, bytes, str]:
        """Download `url` and return (sha256_hex, content_bytes,
        error_message). error_message == "" on success."""
        ...


# ---------------------------------------------------------------------------
# Report renderer + JSON sidecar
# ---------------------------------------------------------------------------
DEFAULT_TITLE = "# Informe de prueba de hash E-14C (local vs Registraduría en vivo)"
DEFAULT_POLICY_NOTE = (
    "> **Política:** los PDF locales **no se modifican ni se borran**. "
    "Se descarga la copia del servidor en memoria. "
    "Si es **igual**, se descarta. Si es **diferente**, se guarda la copia del "
    "servidor en `dif_evidence/*_SERVER.pdf` SIN descartar la local."
)


DEFAULT_FOOTER_NOTE = "*prueba_hash_registraduria.py — no reescribe PDF locales*"


def write_report(
    results: list[dict],
    folder: str | Path,
    report_path: Path,
    ts_path: Path,
    now: datetime | None = None,
    title: str = DEFAULT_TITLE,
    policy_note: str = DEFAULT_POLICY_NOTE,
    footer_note: str = DEFAULT_FOOTER_NOTE,
) -> None:
    """Render the informe .md (+ JSON sidecar) shared by every per-type probe.

    `results` row-key schema (per row; produced by `make_result_row()` and
    filled in by the calling probe's main loop) — required: `filename`,
    `status`. Optional (rendered as `—` when absent/falsy): `indice_local`,
    `hash_origen`, `evidence` (a path-like whose `.name` is shown),
    `local_sha256`, `server_sha256`, `note` (only surfaced when truthy, in
    a dedicated "Notas / errores" section).

    `folder` accepts either a `str` or a `Path` — it is used ONLY for
    string formatting into the report header (`f"...{folder}..."`), never
    for filesystem I/O, so any object with a stable `__str__` works.
    Golden-diff tests fix it to a plain `str` (not a `Path`) specifically
    to keep the golden output byte-identical across platforms — a `Path`
    renders with OS-specific separators (`\\` on Windows vs `/` elsewhere),
    which would make the same fixture produce different golden bytes per
    OS.

    `footer_note` defaults to the E14C attribution line (byte-identical to
    the pre-existing golden output) — every OTHER per-type probe MUST pass
    its own footer_note (e.g. E14D's run_live does), or the rendered
    report misattributes itself to prueba_hash_registraduria.py.
    """
    now = (now or datetime.now()).strftime("%Y-%m-%d %H:%M")
    total = len(results)
    lines = [
        title,
        "",
        f"**Fecha:** {now}  ",
        f"**Carpeta local (solo lectura):** `{folder}`  ",
        f"**PDFs analizados:** {total}  ",
        "**Hash local:** tomado del índice precalculado; solo se calcula en marcha si el archivo no está en el índice.  ",
        "",
        "---",
        "",
        policy_note,
        "",
        "## Resumen",
        "",
        "| Estado | Cantidad |",
        "|--------|--------:|",
    ]
    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    for status, n in sorted(counts.items()):
        lines.append(f"| {status} | {n} |")
    lines += ["", f"| **Total** | **{total}** |", "", "## Detalle", ""]
    lines += [
        "| Archivo | vs servidor | índice local | hash origen | evidencia |",
        "|---------|-------------|--------------|-------------|-----------|",
    ]
    for r in results:
        ev = Path(r["evidence"]).name if r.get("evidence") else "—"
        lines.append(
            f"| {r['filename']} | {r['status']} | {r.get('indice_local') or '—'} | "
            f"{r.get('hash_origen') or '—'} | {ev} |"
        )
    lines += [
        "",
        "## SHA-256",
        "",
        "| Archivo | local | servidor |",
        "|---------|-------|----------|",
    ]
    for r in results:
        lines.append(
            f"| {r['filename']} | `{r.get('local_sha256','')}` | "
            f"`{r.get('server_sha256','')}` |"
        )
    if any(r.get("note") for r in results):
        lines += ["", "## Notas / errores", ""]
        for r in results:
            if r.get("note"):
                lines.append(f"- **{r['filename']}**: {r['note']}")
    lines += [
        "",
        "---",
        "",
        footer_note,
        "",
    ]
    content = "\n".join(lines)
    report_path.write_text(content, encoding="utf-8")
    ts_path.write_text(content, encoding="utf-8")
    # JSON sidecar for machine use (also non-destructive of PDFs)
    json_path = report_path.with_suffix(".json")
    json_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Local hash index lookup interface — consumed starting with the E14D probe
# (Phase 2). Declared here now so Phase 2's adapter has a stable contract to
# satisfy without touching this module again.
# ---------------------------------------------------------------------------
class LocalHashIndex(Protocol):
    def lookup(self, filename: str) -> str | None:
        """Return the indexed SHA-256 hex digest for `filename`, or None if
        the file has no index entry (on-the-fly hashing required)."""
        ...


# ---------------------------------------------------------------------------
# mesa_key normalization — verbatim copy of
# scripts/build_consolidated_hash_index.py::make_key() (design.md D6 synced-
# copy convention: runtime import from scripts/ is rejected because the
# citizen-facing bundle must stay dependency-free of the rest of the repo).
# ---------------------------------------------------------------------------
# Manual verbatim copy of scripts/build_consolidated_hash_index.py::make_key().
# Keep in sync by hand; test_make_mesa_key_parity guards drift. Do not edit
# independently.
def make_mesa_key(dept, mpio, zona, puesto, mesa) -> str:
    """Zero-padded mesa_key: dept(2)_mpio(3)_zona(3)_puesto(2)_mesa(3).

    Same padding scheme every per-type geo URL is built from (design.md
    D1) — E14D/E14T's server URL path segments are exactly this padding.
    """
    return (
        f"{str(dept).zfill(2)}_{str(mpio).zfill(3)}_{str(zona).zfill(3)}_"
        f"{str(puesto).zfill(2)}_{str(mesa).zfill(3)}"
    )


# ---------------------------------------------------------------------------
# URL-resolution coverage accounting (design.md D1/spec: "URL-resolution
# coverage accounting" — mandatory output, gates the >99% corpus dry-run
# threshold before PR3 wires the live transport).
# ---------------------------------------------------------------------------
def resolve_coverage(rows: list[dict]) -> dict:
    """Summarize URL-resolution rows into `{resueltas, total, por_motivo}`.

    Each row is `{"url": str | None, "reason": str | None}` — `reason` is
    read only for rows whose `url` is falsy (unresolved), and defaults to
    `"DESCONOCIDO"` when a row omits it (defensive; every real caller sets
    a reason for every unresolved row).
    """
    total = len(rows)
    resueltas = sum(1 for r in rows if r.get("url"))
    por_motivo: dict[str, int] = {}
    for r in rows:
        if not r.get("url"):
            reason = r.get("reason") or "DESCONOCIDO"
            por_motivo[reason] = por_motivo.get(reason, 0) + 1
    return {"resueltas": resueltas, "total": total, "por_motivo": por_motivo}
