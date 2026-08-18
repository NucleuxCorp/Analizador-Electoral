#!/usr/bin/env python3
"""
prueba_hash_e14d.py — Non-destructive per-type hash probe for E14D.

Policy (hard constraints, shared with the E14C probe — hash_probe_common.py):
  - NEVER modify or delete local PDFs.
  - Index-first: local hashes come from the PRECOMPUTED index
    (data/local_hash_index/e14d_e14t_sha256.jsonl); step 1 of the flow
    generates it once, this probe never re-hashes an indexed file.
  - Geo-resolve the Registraduría URL from the index row's own geo fields
    (design.md D1) — never from url_index_e14d.json.

`--dry-run`: URL-resolution + coverage accounting ONLY, ZERO network calls.
This is what gates the >99% corpus-coverage requirement (spec: "Corpus dry
run gates delivery") — task 2.11/3.11, operator-run, before/alongside the
live acceptance run below.

Live mode (default, no `--dry-run`): fetches each PDF's current server copy
via `PlaywrightTransport` (Playwright-primary, plain HTTP proved non-viable
— design.md D5) and compares SHA-256. Aborts BEFORE touching the corpus if
Playwright/Chromium is unavailable (never a silent all-error run).

Usage:
  python prueba_hash_e14d.py <folder> --dry-run [e14d_e14t_sha256.jsonl]
  python prueba_hash_e14d.py <folder> [e14d_e14t_sha256.jsonl]
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time
from datetime import datetime
from pathlib import Path

from e14d_index import DEFAULT_STEP1_CMD, SIN_GEO_EN_INDICE, load_e14d_index
from e14d_paths import BASE_D, build_e14d_url
from hash_probe_common import (
    CHECKPOINT_EVERY,
    DEFAULT_POLICY_NOTE,
    CorruptIndexError,
    MissingIndexError,
    compare_hashes,
    guard_index_load,
    load_checkpoint,
    make_result_row,
    require_index,
    resolve_coverage,
    save_checkpoint,
    save_server_evidence,
    sha256_file,
    write_report,
)
from http_fallback import PlaywrightFetcher

SIN_FILA_EN_INDICE = "SIN_FILA_EN_INDICE"
# Single source of truth (PR2 review follow-up 2, obs 1560): e14d_index.py
# owns the default; this module no longer duplicates the literal string.
STEP1_CMD = DEFAULT_STEP1_CMD

REPO = Path(__file__).resolve().parents[2]
DEFAULT_INDEX_PATH = REPO / "data" / "local_hash_index" / "e14d_e14t_sha256.jsonl"

# Live-transport constants (design.md D5, justified from the authorized WAF
# spike): E14D serves PDFs under a different path than E14C's ROUTE_GLOB, so
# the escalation route interceptor needs its own glob or it would silently
# never match. Rate limit is slower than E14C's 0.3s (verificador_e14c.py) —
# the spike found this WAF tarpits harder. goto timeout stays at
# PlaywrightFetcher's proven 20000ms default (unchanged, not overridden).
E14D_ROUTE_GLOB = "**/assets/temis/pdf/**"
RATE_LIMIT_SECONDS = 1.0
PLAYWRIGHT_INSTALL_CMD = "pip install playwright && playwright install chromium"
CHECKPOINT_NAME = ".prueba_hash_e14d_checkpoint.json"
REPORT_NAME = "informe_prueba_hash_e14d.md"
E14D_TITLE = "# Informe de prueba de hash E-14D (local vs Registraduría en vivo)"
# PR3 review follow-up 1 (reliability WARNING): write_report()'s footer
# defaults to the E14C script's own name — every other per-type probe must
# pass its own, or informe_prueba_hash_e14d.md (an evidentiary artifact for
# auditors) misattributes itself to prueba_hash_registraduria.py.
E14D_FOOTER_NOTE = "*prueba_hash_e14d.py — no reescribe PDF locales*"


def scan_pdfs(folder: Path) -> list[Path]:
    """Local PDFs (read-only), excluding dif_evidence/ and *_SERVER.pdf —
    same exclusion policy as the E14C probe."""
    return sorted(
        p
        for p in folder.rglob("*.[Pp][Dd][Ff]")
        if "dif_evidence" not in p.parts and not p.name.endswith("_SERVER.pdf")
    )


def resolve_geo_and_url(pdf: Path, index) -> tuple[str | None, str | None]:
    """Resolve `(url, reason)` for one local PDF via the E14D index
    adapter. Pure resolution — zero network. `reason` is set only when
    `url` is None. Shared by dry-run's `resolve_pdf_urls()` and live mode's
    per-file loop so URL resolution has exactly one implementation."""
    expected_name = pdf.stem
    geo = index.geo(expected_name)
    if geo is None:
        reason = SIN_GEO_EN_INDICE if expected_name in index else SIN_FILA_EN_INDICE
        return None, reason
    url = build_e14d_url(
        BASE_D, geo["dept"], geo["mpio"], geo["zona"], geo["puesto"], geo["mesa"], expected_name
    )
    return url, (None if url else "URL_NO_CONSTRUIBLE")


def resolve_pdf_urls(pdfs: list[Path], index) -> list[dict]:
    """Resolve a Registraduría URL for each local PDF via the E14D index
    adapter. Pure resolution — zero network. One row per pdf:
    `{"pdf": Path, "url": str | None, "reason": str | None}`."""
    rows: list[dict] = []
    for pdf in pdfs:
        url, reason = resolve_geo_and_url(pdf, index)
        rows.append({"pdf": pdf, "url": url, "reason": reason})
    return rows


def load_index_guarded(index_path: Path):
    """require_index + guard_index_load, wired with the SAME step1_cmd for
    both the missing-index and corrupt-index cases (dry-run and live share
    this — PR2 review follow-up 2, obs 1560: previously the corrupt-index
    message for E14D used a hardcoded string instead of this module's
    STEP1_CMD, making guard_index_load's own step1_cmd argument dead for
    this path)."""
    require_index(index_path, STEP1_CMD)
    return guard_index_load(
        index_path, STEP1_CMD, lambda: load_e14d_index(index_path, step1_cmd=STEP1_CMD)
    )


def run_dry_run(folder: Path, index_path: Path, transport=None) -> dict:
    """URL-resolution + coverage accounting, ZERO network calls.

    `transport` is an unused forward-compat seam for PR3's live-mode
    wiring point (hash_probe_common.Transport protocol) — dry-run must
    structurally never call it; kept explicit here so a test can inject a
    Transport stub that raises on any use and prove it.
    """
    index = load_index_guarded(index_path)
    pdfs = scan_pdfs(folder)
    rows = resolve_pdf_urls(pdfs, index)
    coverage = resolve_coverage(rows)
    coverage["collisions"] = index.collisions
    return coverage


class TransportUnavailableError(Exception):
    """Raised by run_live() when the Playwright transport cannot be
    launched. Aborts BEFORE iterating the corpus (design.md D5: never a
    silent all-error run)."""


class PlaywrightTransport:
    """Transport adapter satisfying hash_probe_common.Transport for E14D.

    Playwright is the PRIMARY (only) transport for this domain — plain HTTP
    proved non-viable in the authorized WAF spike (design.md D5: 12/12
    read-timeouts at 30s on both E14D/E14T domains). Delegates to
    `http_fallback.PlaywrightFetcher` (the same class E14C's fallback uses),
    scoped to E14D's own `route_glob` via the ctor arg added in this slice.
    """

    def __init__(self, base_url: str = BASE_D, fetcher=None, fetcher_factory=None):
        factory = fetcher_factory or PlaywrightFetcher
        self._fetcher = fetcher or factory(base_url, route_glob=E14D_ROUTE_GLOB)

    def ensure_available(self) -> bool:
        return self._fetcher.ensure_launched()

    def fetch_and_hash(self, url: str) -> tuple[str, bytes, str]:
        """Download `url` and compute its SHA-256 (design.md D5's rate
        limit applies to every request, not just successful ones — the
        WAF tarpits regardless of outcome)."""
        time.sleep(RATE_LIMIT_SECONDS)
        body, err = self._fetcher.fetch(url)
        if err:
            return "", b"", err
        return hashlib.sha256(body).hexdigest(), body, ""


def _print_coverage(coverage: dict) -> None:
    total = coverage["total"]
    resueltas = coverage["resueltas"]
    pct = (100 * resueltas / total) if total else 0.0
    print(f"Resolved: {resueltas:,}/{total:,} ({pct:.2f}%)")
    for reason, n in sorted(coverage["por_motivo"].items()):
        print(f"  {reason}: {n:,}")
    if coverage["collisions"]:
        print(f"Index collisions (first-wins): {coverage['collisions']:,}")


def run_live(
    folder: Path,
    index_path: Path,
    transport,
    evidence_dir: Path | None = None,
    checkpoint_path: Path | None = None,
    report_path: Path | None = None,
    ts_path: Path | None = None,
    now: datetime | None = None,
) -> dict:
    """Live (non-dry-run) probe: index-first local hash -> geo-resolved URL
    -> `transport.fetch_and_hash()` -> compare -> evidence-on-mismatch ->
    checkpoint/report (spec: "Local files never mutated", "On-the-fly
    hashing disclosed"). No `MANIPULADA_EN_SERVIDOR` escalation for E14D
    (design.md D3 — that status requires a canonical corpus index E14D
    doesn't have). Hard-aborts BEFORE iterating any row if the transport
    can't be launched (design.md D5).
    """
    index = load_index_guarded(index_path)

    if not transport.ensure_available():
        print(
            "ERROR: Playwright/Chromium unavailable for the E14D live transport.\n"
            f"  Install it: {PLAYWRIGHT_INSTALL_CMD}",
            file=sys.stderr,
        )
        raise TransportUnavailableError(PLAYWRIGHT_INSTALL_CMD)

    pdfs = scan_pdfs(folder)
    evidence_dir = evidence_dir or (folder / "dif_evidence")
    checkpoint_path = checkpoint_path or (folder / CHECKPOINT_NAME)
    script_dir = Path(__file__).resolve().parent
    report_path = report_path or (script_dir / REPORT_NAME)
    ts = (now or datetime.now()).strftime("%Y-%m-%d_%H-%M")
    ts_path = ts_path or (script_dir / f"informe_prueba_hash_e14d_{ts}.md")

    results: list[dict] = []
    done_paths: set[str] = set()
    if checkpoint_path.exists():
        try:
            results, done_paths = load_checkpoint(checkpoint_path)
        except Exception as exc:
            print(f"Checkpoint unreadable, starting fresh: {exc}", flush=True)
            results, done_paths = [], set()
    pending = [p for p in pdfs if str(p.resolve()) not in done_paths]

    coverage_rows: list[dict] = []
    for i, pdf in enumerate(pending, 1):
        row = make_result_row(pdf.name, str(pdf.resolve()))

        indexed_sha = index.lookup(pdf.name)
        if indexed_sha:
            local_sha = indexed_sha
            row["hash_origen"] = "indice"
        else:
            try:
                local_sha = sha256_file(pdf)
            except Exception as exc:
                row["status"] = "ERROR_LOCAL"
                row["note"] = str(exc)
                results.append(row)
                print(f"[{i}/{len(pending)}] {row['filename']}: {row['status']}", flush=True)
                continue
            row["hash_origen"] = "calculado (no estaba en el indice)"
        row["local_sha256"] = local_sha

        url, reason = resolve_geo_and_url(pdf, index)
        coverage_rows.append({"url": url, "reason": reason})
        row["url"] = url or ""
        if not url:
            row["status"] = "URL_NO_CONSTRUIBLE"
            row["note"] = reason
            results.append(row)
            print(f"[{i}/{len(pending)}] {row['filename']}: {row['status']}", flush=True)
            continue

        server_sha, server_data, err = transport.fetch_and_hash(url)
        if err:
            row["status"] = "ERROR_DESCARGA"
            row["note"] = err
            results.append(row)
            print(f"[{i}/{len(pending)}] {row['filename']}: {row['status']}", flush=True)
            continue

        row["server_sha256"] = server_sha
        if compare_hashes(local_sha, server_sha):
            # Equal -> discard server bytes; local untouched.
            row["status"] = "IGUAL_SERVIDOR"
        else:
            # Different -> keep ONLY server evidence copy; local untouched.
            evidence_path = save_server_evidence(evidence_dir, pdf.stem, server_data)
            row["status"] = "DIFERENTE_SERVIDOR"
            row["evidence"] = str(evidence_path)
        results.append(row)
        print(f"[{i}/{len(pending)}] {row['filename']}: {row['status']}", flush=True)

        if i % CHECKPOINT_EVERY == 0 or i == len(pending):
            save_checkpoint(checkpoint_path, folder, results, now=now)

    write_report(
        results,
        str(folder),
        report_path,
        ts_path,
        now=now,
        title=E14D_TITLE,
        policy_note=DEFAULT_POLICY_NOTE,
        footer_note=E14D_FOOTER_NOTE,
    )
    coverage = resolve_coverage(coverage_rows)
    coverage["collisions"] = index.collisions
    return {"results": results, "coverage": coverage}


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint.

    Exit codes:
      0 — success (dry-run coverage report, or live run completed).
      1 — folder not found, index missing, or index corrupt/unreadable.
      3 — live mode only: Playwright/Chromium unavailable (see
          PLAYWRIGHT_INSTALL_CMD); aborted before touching the corpus.
      2 — unused (was PR2's "live mode not implemented" placeholder rc,
          kept reserved rather than reassigned to avoid confusing any
          external caller/script that may already branch on it).
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("folder")
    parser.add_argument("index", nargs="?", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    folder = Path(args.folder).resolve()
    if not folder.is_dir():
        print(f"ERROR: folder not found: {folder}", file=sys.stderr)
        return 1

    index_path = Path(args.index).resolve() if args.index else DEFAULT_INDEX_PATH

    if args.dry_run:
        try:
            coverage = run_dry_run(folder, index_path)
        except MissingIndexError:
            return 1
        except CorruptIndexError:
            return 1
        _print_coverage(coverage)
        return 0

    transport = PlaywrightTransport(BASE_D)
    try:
        result = run_live(folder, index_path, transport)
    except MissingIndexError:
        return 1
    except CorruptIndexError:
        return 1
    except TransportUnavailableError:
        return 3

    _print_coverage(result["coverage"])
    counts: dict[str, int] = {}
    for r in result["results"]:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("Summary:", counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
