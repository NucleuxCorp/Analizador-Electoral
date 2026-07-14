"""cross_validator_cli.py — CLI for the cross-mesa validator.

Two subcommands:
    build-index   Build cross_mesa_index.jsonl from E14C/E14T/E14D URL files.
    validate      Run per-source extraction + congruence on all indexed mesas.

Usage (from project root):
    python -m src.modules.analyzer.cross_validator_cli build-index [--output PATH]
    python -m src.modules.analyzer.cross_validator_cli validate [options]

    python debug_sv/cross_validator_cli.py build-index
    python debug_sv/cross_validator_cli.py validate --workers 8 --dept 05
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm

# ---------------------------------------------------------------------------
# Project root on sys.path so imports work from any cwd
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Default data paths
# ---------------------------------------------------------------------------

DEFAULT_E14C_JSONL = ROOT / "data" / "e14c_sv_urls.jsonl"
DEFAULT_E14T_JSON = ROOT / "data" / "allTransmissionCodes_segunda_index.json"
DEFAULT_E14D_JSONL = ROOT / "data" / "e14d_sv_urls.jsonl"

DEFAULT_E14C_ROOT = Path("E:/Nucleux/tools/Analizador de Elecciones/Data/e14_segunda/E14C")
DEFAULT_E14T_ROOT = Path("E:/Nucleux/tools/Analizador de Elecciones/Data/e14_segunda/E14T")
DEFAULT_E14D_ROOT = Path("E:/Nucleux/tools/Analizador de Elecciones/Data/e14_segunda/E14D")

DEFAULT_INDEX_OUTPUT = ROOT / "data" / "cross_mesa_index.jsonl"
DEFAULT_VALIDATE_OUTPUT = ROOT / "data" / "cross_mesa_validation.jsonl"
DEFAULT_CHECKPOINT = None  # auto: {output}.checkpoint.json

# ---------------------------------------------------------------------------
# Tachon scan helper
# ---------------------------------------------------------------------------

_TACHON_MOD = None


def _run_tachon_scan(pdf_path: "Path | None") -> "dict | None":
    """Run tachon scan on a PDF and return aggregated summary, or None on failure."""
    if pdf_path is None:
        return None

    global _TACHON_MOD
    import importlib.util as _ilu
    from pathlib import Path as _Path

    _root = _Path(__file__).resolve().parents[3]
    _mod_path = _root / "debug_sv" / "tachon_method_scan.py"

    try:
        if _TACHON_MOD is None:
            _spec = _ilu.spec_from_file_location("tachon_method_scan", _mod_path)
            _mod = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            _TACHON_MOD = _mod

        _fn = getattr(_TACHON_MOD, "process_pdf_for_scan", None)
        if _fn is None:
            return None

        records = _fn(str(pdf_path))
    except Exception:
        return None

    if not records:
        return {
            "suspicious": False,
            "max_score": 0.0,
            "suspicious_fields": [],
            "density_high": False,
            "doble_escritura": False,
            "tachon": False,
        }

    suspicious_fields = [r["label"] for r in records if r.get("is_suspicious_combined")]
    max_score = max((r.get("scores", {}).get("combined_score", 0.0) for r in records), default=0.0)

    density_high = any(r.get("flags_by_method", {}).get("DENSIDAD_ALTA") for r in records)
    doble_escritura = any(r.get("flags_by_method", {}).get("DOBLE_ESCRITURA") for r in records)
    tachon = any(r.get("flags_by_method", {}).get("TACHON") for r in records)

    return {
        "suspicious": bool(suspicious_fields),
        "max_score": float(max_score),
        "suspicious_fields": suspicious_fields,
        "density_high": density_high,
        "doble_escritura": doble_escritura,
        "tachon": tachon,
    }


# ---------------------------------------------------------------------------
# Worker function — must be top-level for ProcessPoolExecutor pickling
# ---------------------------------------------------------------------------

def _worker_extract(entry: dict) -> dict:
    """Extract all three sources for one index entry.

    Runs in a subprocess (Windows spawn). Imports are deferred inside the
    function to avoid module-level initialization issues on spawn.

    Args:
        entry: One record from cross_mesa_index.jsonl with keys:
               dept, mpio, zona, puesto, mesa, e14c_path, e14t_path, e14d_path.

    Returns:
        dict with keys: dept, mpio, zona, puesto, mesa, sources, congruencia,
        aritmetica, tachones.
    """
    import logging as _logging
    import sys as _sys
    from pathlib import Path as _Path

    _logging.getLogger().setLevel(_logging.WARNING)

    _root = _Path(__file__).resolve().parents[3]
    _sys.path.insert(0, str(_root))

    # Lazy imports inside the worker
    from src.modules.analyzer.cross_validator import (
        _extract_source,
        apply_confidence_filter,
        check_arithmetic,
    )
    from src.modules.analyzer.cross_congruence import compare_mesa

    dept = entry["dept"]
    mpio = entry["mpio"]
    zona = entry["zona"]
    puesto = entry["puesto"]
    mesa = entry["mesa"]

    def _resolve(path_str: str | None) -> _Path | None:
        return _Path(path_str) if path_str else None

    e14c_pdf = _resolve(entry.get("e14c_path"))
    e14t_pdf = _resolve(entry.get("e14t_path"))
    e14d_pdf = _resolve(entry.get("e14d_path"))

    # Extract
    raw_e14c = _extract_source(e14c_pdf, "e14c")
    raw_e14t = _extract_source(e14t_pdf, "e14t")
    raw_e14d = _extract_source(e14d_pdf, "e14d")

    # Apply confidence filter before cross-source comparison
    filt_e14c = apply_confidence_filter(raw_e14c)
    filt_e14t = apply_confidence_filter(raw_e14t)
    filt_e14d = apply_confidence_filter(raw_e14d)

    # Cross-source congruence (uses filtered digits)
    congruencia = compare_mesa(filt_e14c, filt_e14t, filt_e14d)

    # Per-source arithmetic check (uses raw fields — not filtered)
    def _arithmetic(src_result: dict) -> dict:
        if src_result["status"] == "ok":
            return check_arithmetic(src_result.get("fields") or {})
        return {"ok": None, "sum_votes": None, "urna": None, "delta": None}

    aritmetica = {
        "e14c": _arithmetic(raw_e14c),
        "e14t": _arithmetic(raw_e14t),
        "e14d": _arithmetic(raw_e14d),
    }

    # Summarise source status (only status + fields, omit raw digit arrays to
    # keep output readable; congruencia already carries the digit comparison)
    def _source_summary(src: dict) -> dict:
        return {
            "status": src["status"],
            "fields": src.get("fields") or {},
        }

    return {
        "dept": dept,
        "mpio": mpio,
        "zona": zona,
        "puesto": puesto,
        "mesa": mesa,
        "sources": {
            "e14c": _source_summary(raw_e14c),
            "e14t": _source_summary(raw_e14t),
            "e14d": _source_summary(raw_e14d),
        },
        "congruencia": dict(congruencia),
        "aritmetica": aritmetica,
        "tachones": {
            "e14c": _run_tachon_scan(e14c_pdf),
            "e14t": None,
            "e14d": None,
        },
    }


# ---------------------------------------------------------------------------
# build-index subcommand
# ---------------------------------------------------------------------------

def cmd_build_index(args: argparse.Namespace) -> int:
    """Build the cross-mesa index JSONL."""
    from src.modules.analyzer.cross_mesa_index import build_index

    e14c_jsonl = Path(args.e14c_jsonl)
    e14t_json = Path(args.e14t_json)
    e14d_jsonl = Path(args.e14d_jsonl)
    output = Path(args.output)

    if not e14c_jsonl.exists():
        print(f"[ERROR] E14C JSONL not found: {e14c_jsonl}", file=sys.stderr)
        return 1
    if not e14t_json.exists():
        print(f"[ERROR] E14T JSON not found: {e14t_json}", file=sys.stderr)
        return 1

    # E14D JSONL is optional — pass a sentinel that build_index gracefully handles
    # by scanning an empty source.  If the file is missing, use /dev/null equivalent.
    if not e14d_jsonl.exists():
        print(f"[WARN] E14D JSONL not found: {e14d_jsonl} — E14D paths will be None")
        # Provide an empty source by writing to a temp file
        import tempfile, os
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".jsonl")
        os.close(tmp_fd)
        e14d_jsonl = Path(tmp_path)
        _e14d_tmp = e14d_jsonl
    else:
        _e14d_tmp = None

    try:
        count = build_index(
            e14c_jsonl=e14c_jsonl,
            e14t_json=e14t_json,
            e14d_jsonl=e14d_jsonl,
            e14c_root=args.e14c_root,
            e14t_root=args.e14t_root,
            e14d_root=args.e14d_root,
            output_path=output,
        )
    finally:
        if _e14d_tmp is not None:
            try:
                _e14d_tmp.unlink()
            except OSError:
                pass

    print(f"[build-index] Done — {count} records written to {output}")
    return 0


# ---------------------------------------------------------------------------
# validate subcommand
# ---------------------------------------------------------------------------

def cmd_validate(args: argparse.Namespace) -> int:
    """Run cross-source validation on all indexed mesas."""
    index_path = Path(args.index)
    workers = args.workers
    dept_filter: str | None = args.dept
    base_output = Path(args.output)
    output_path = base_output.with_stem(base_output.stem + f"_{dept_filter}") if dept_filter else base_output
    if args.checkpoint:
        checkpoint_path = Path(args.checkpoint)
    elif dept_filter:
        checkpoint_path = output_path.with_suffix(f".checkpoint_{dept_filter}.json")
    else:
        checkpoint_path = output_path.with_suffix(".checkpoint.json")
    limit: int | None = args.limit

    if not index_path.exists():
        print(f"[ERROR] Index not found: {index_path}", file=sys.stderr)
        print("  Run 'build-index' first.", file=sys.stderr)
        return 1

    # Read checkpoint (last processed line index)
    checkpoint_line = 0
    if checkpoint_path.exists():
        try:
            with open(checkpoint_path, encoding="utf-8") as f:
                chk = json.load(f)
                checkpoint_line = int(chk.get("last_line", 0))
            print(f"[validate] Resuming from line {checkpoint_line}")
        except Exception as exc:
            print(f"[WARN] Could not read checkpoint ({exc}), starting from 0")

    # Load all index entries
    entries: list[dict] = []
    with open(index_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))

    # Apply dept filter
    if dept_filter:
        entries = [e for e in entries if e.get("dept") == dept_filter]
        print(f"[validate] Dept filter '{dept_filter}': {len(entries)} entries")

    # Apply limit before skipping checkpoint (limit is absolute, not relative)
    if limit is not None:
        entries = entries[:limit]

    # Skip already-processed entries
    to_process = entries[checkpoint_line:]
    total = len(entries)
    remaining = len(to_process)
    print(f"[validate] Total: {total}, already done: {checkpoint_line}, to process: {remaining}")

    if remaining == 0:
        print("[validate] Nothing to do.")
        return 0

    # Prepare output file (append mode so we don't lose prior results)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if checkpoint_line > 0 else "w"

    start = time.monotonic()
    done_this_run = 0

    pbar = tqdm(
        total=total,
        initial=checkpoint_line,
        desc="Validando",
        unit="mesa",
        bar_format="{desc}: {percentage:3.0f}%|{bar}| {n}/{total} [{elapsed}<{remaining}, {rate_fmt}]",
    )

    with open(output_path, mode, encoding="utf-8") as out_f:
        if workers > 1:
            # ProcessPoolExecutor — submit in batches to maintain ordering
            pool = ProcessPoolExecutor(max_workers=workers)
            try:
                # Submit all futures in order; collect in order
                futures = {pool.submit(_worker_extract, entry): i
                           for i, entry in enumerate(to_process)}

                # We want ordered output — use a results buffer
                results_buf: dict[int, dict] = {}
                next_write = 0

                for future in as_completed(futures):
                    idx = futures[future]
                    try:
                        result = future.result()
                    except Exception as exc:
                        entry = to_process[idx]
                        result = {
                            "dept": entry.get("dept"), "mpio": entry.get("mpio"),
                            "zona": entry.get("zona"), "puesto": entry.get("puesto"),
                            "mesa": entry.get("mesa"),
                            "sources": {
                                "e14c": {"status": "extraction_error", "fields": {}},
                                "e14t": {"status": "extraction_error", "fields": {}},
                                "e14d": {"status": "extraction_error", "fields": {}},
                            },
                            "congruencia": {},
                            "aritmetica": {
                                "e14c": {"ok": None, "sum_votes": None, "urna": None, "delta": None},
                                "e14t": {"ok": None, "sum_votes": None, "urna": None, "delta": None},
                                "e14d": {"ok": None, "sum_votes": None, "urna": None, "delta": None},
                            },
                            "tachones": None,
                            "_worker_error": str(exc),
                        }

                    results_buf[idx] = result

                    # Flush contiguous results in order
                    while next_write in results_buf:
                        out_f.write(json.dumps(results_buf.pop(next_write), ensure_ascii=False) + "\n")
                        next_write += 1
                        done_this_run += 1
                        pbar.update(1)

                        abs_done = checkpoint_line + done_this_run
                        if done_this_run % 500 == 0:
                            out_f.flush()
                            _write_checkpoint(checkpoint_path, abs_done)

            except KeyboardInterrupt:
                pool.shutdown(wait=False, cancel_futures=True)
                out_f.flush()
                _write_checkpoint(checkpoint_path, checkpoint_line + done_this_run)
                pbar.close()
                print("\n[validate] Cancelled — checkpoint saved.")
                return 0
            finally:
                pool.shutdown(wait=False, cancel_futures=True)

        else:
            # Single-process mode
            for i, entry in enumerate(to_process):
                try:
                    result = _worker_extract(entry)
                except Exception as exc:
                    result = {
                        "dept": entry.get("dept"), "mpio": entry.get("mpio"),
                        "zona": entry.get("zona"), "puesto": entry.get("puesto"),
                        "mesa": entry.get("mesa"),
                        "sources": {
                            "e14c": {"status": "extraction_error", "fields": {}},
                            "e14t": {"status": "extraction_error", "fields": {}},
                            "e14d": {"status": "extraction_error", "fields": {}},
                        },
                        "congruencia": {},
                        "aritmetica": {
                            "e14c": {"ok": None, "sum_votes": None, "urna": None, "delta": None},
                            "e14t": {"ok": None, "sum_votes": None, "urna": None, "delta": None},
                            "e14d": {"ok": None, "sum_votes": None, "urna": None, "delta": None},
                        },
                        "tachones": None,
                        "_worker_error": str(exc),
                    }

                out_f.write(json.dumps(result, ensure_ascii=False) + "\n")
                done_this_run += 1
                pbar.update(1)
                abs_done = checkpoint_line + done_this_run

                if done_this_run % 500 == 0:
                    out_f.flush()
                    _write_checkpoint(checkpoint_path, abs_done)

    pbar.close()

    # Final checkpoint
    final_done = checkpoint_line + done_this_run
    _write_checkpoint(checkpoint_path, final_done)
    elapsed = time.monotonic() - start
    print(f"[validate] Complete — {done_this_run} processed in {elapsed:.1f}s "
          f"(total done: {final_done}/{total})")
    print(f"[validate] Output: {output_path}")
    return 0


def _write_checkpoint(path: Path, last_line: int) -> None:
    """Atomically write checkpoint JSON."""
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"last_line": last_line, "ts": time.time()}, f)
    tmp.replace(path)


# ---------------------------------------------------------------------------
# report subcommand — Phase 5
# ---------------------------------------------------------------------------

def generate_summary_report(records: list[dict]) -> dict:
    """Compute summary statistics from a list of validation records.

    Args:
        records: List of dicts, each matching the output schema of _worker_extract.

    Returns:
        Summary dict following the documented JSON schema.
    """
    from datetime import datetime, timezone

    total = len(records)

    # --- sources distribution ---
    src_dist: dict[str, int] = {"0": 0, "1": 0, "2": 0, "3": 0}
    e14c_ok_count = 0
    e14t_ok_count = 0
    e14d_ok_count = 0

    # --- arithmetic ---
    arith: dict[str, dict[str, int]] = {
        "e14c": {"ok": 0, "fail": 0, "skip": 0},
        "e14t": {"ok": 0, "fail": 0, "skip": 0},
        "e14d": {"ok": 0, "fail": 0, "skip": 0},
    }

    # --- congruence ---
    cross_discrepancy_count = 0
    both_arith_ok_count = 0
    both_arith_ok_cross_disc = 0
    discrepant_fields_freq: dict[str, int] = {}

    # --- top arithmetic deltas ---
    delta_candidates: list[dict] = []

    for rec in records:
        sources = rec.get("sources") or {}
        aritmetica = rec.get("aritmetica") or {}
        congruencia = rec.get("congruencia") or {}
        summary = congruencia.get("summary") or {}

        # sources_ok: count sources with status == "ok"
        sources_ok = sum(
            1 for src in ("e14c", "e14t", "e14d")
            if (sources.get(src) or {}).get("status") == "ok"
        )
        src_dist[str(min(sources_ok, 3))] = src_dist.get(str(sources_ok), 0) + 1

        if (sources.get("e14c") or {}).get("status") == "ok":
            e14c_ok_count += 1
        if (sources.get("e14t") or {}).get("status") == "ok":
            e14t_ok_count += 1
        if (sources.get("e14d") or {}).get("status") == "ok":
            e14d_ok_count += 1

        # arithmetic per source
        arith_ok_sources = 0
        for src_key in ("e14c", "e14t", "e14d"):
            src_arith = aritmetica.get(src_key) or {}
            ok_val = src_arith.get("ok")
            delta_val = src_arith.get("delta")
            if ok_val is True:
                arith[src_key]["ok"] += 1
                arith_ok_sources += 1
            elif ok_val is False:
                arith[src_key]["fail"] += 1
                # Track delta candidate for top-10
                if delta_val is not None:
                    delta_candidates.append({
                        "dept": rec.get("dept", ""),
                        "mpio": rec.get("mpio", ""),
                        "zona": rec.get("zona", ""),
                        "puesto": rec.get("puesto", ""),
                        "mesa": rec.get("mesa", ""),
                        "source": src_key,
                        "delta": delta_val,
                        "urna": src_arith.get("urna"),
                    })
            else:
                arith[src_key]["skip"] += 1

        # cross discrepancy
        cross_disc = summary.get("cross_discrepancy", False)
        if cross_disc:
            cross_discrepancy_count += 1

        # both-arithmetic-ok: at least 2 sources with ok=True
        if arith_ok_sources >= 2:
            both_arith_ok_count += 1
            if cross_disc:
                both_arith_ok_cross_disc += 1

        # discrepant fields frequency
        for field in summary.get("cross_discrepant_fields") or []:
            discrepant_fields_freq[field] = discrepant_fields_freq.get(field, 0) + 1

    # Sort discrepant fields by frequency descending
    discrepant_fields_freq = dict(
        sorted(discrepant_fields_freq.items(), key=lambda kv: kv[1], reverse=True)
    )

    # Top-10 arithmetic failures by abs(delta)
    delta_candidates.sort(key=lambda x: abs(x["delta"]), reverse=True)
    top_deltas = delta_candidates[:10]

    cross_disc_rate = cross_discrepancy_count / total if total > 0 else 0.0

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_mesas": total,
        "sources": {
            "distribution": src_dist,
            "e14c_ok": e14c_ok_count,
            "e14t_ok": e14t_ok_count,
            "e14d_ok": e14d_ok_count,
        },
        "arithmetic": arith,
        "congruencia": {
            "cross_discrepancy_count": cross_discrepancy_count,
            "cross_discrepancy_rate": round(cross_disc_rate, 6),
            "both_arithmetic_ok_count": both_arith_ok_count,
            "both_arithmetic_ok_cross_discrepancy": both_arith_ok_cross_disc,
            "discrepant_fields_frequency": discrepant_fields_freq,
        },
        "top_arithmetic_deltas": top_deltas,
    }


def _render_markdown(report: dict, input_file: str) -> str:
    """Render the summary report dict as a Markdown string."""
    lines: list[str] = []

    lines.append("# Cross-Mesa Validation Report")
    lines.append("")
    lines.append(f"Generated: {report['generated_at']}")
    lines.append(f"Input: {input_file}")
    lines.append(f"Total mesas: {report['total_mesas']}")
    lines.append("")

    # --- Sources Available ---
    lines.append("## Sources Available")
    lines.append("")
    lines.append("| sources_ok | count | % |")
    lines.append("|---|---|---|")
    total = report["total_mesas"]
    for key in ("0", "1", "2", "3"):
        count = report["sources"]["distribution"].get(key, 0)
        pct = f"{count / total * 100:.1f}" if total > 0 else "0.0"
        lines.append(f"| {key} | {count} | {pct} |")
    lines.append("")

    # --- Arithmetic Check ---
    lines.append("## Arithmetic Check")
    lines.append("")
    lines.append("| Source | OK | FAIL | SKIP |")
    lines.append("|---|---|---|---|")
    for src_key, label in (("e14c", "E14C"), ("e14t", "E14T"), ("e14d", "E14D")):
        a = report["arithmetic"][src_key]
        lines.append(f"| {label} | {a['ok']} | {a['fail']} | {a['skip']} |")
    lines.append("")

    # --- Cross-Source Congruence ---
    lines.append("## Cross-Source Congruence")
    lines.append("")
    cong = report["congruencia"]
    disc_count = cong["cross_discrepancy_count"]
    rate_pct = f"{cong['cross_discrepancy_rate'] * 100:.1f}"
    lines.append(f"- Cross-discrepancy rate: {disc_count}/{total} ({rate_pct}%)")
    both_ok = cong["both_arithmetic_ok_count"]
    both_ok_disc = cong["both_arithmetic_ok_cross_discrepancy"]
    both_ok_disc_pct = f"{both_ok_disc / both_ok * 100:.1f}" if both_ok > 0 else "0.0"
    lines.append(f"- Mesas with ≥2 arithmetic-OK sources: {both_ok}")
    lines.append(f"  - Of those, with cross-discrepancy: {both_ok_disc} ({both_ok_disc_pct}%)")
    lines.append("")

    if cong["discrepant_fields_frequency"]:
        lines.append("### Most Discrepant Fields")
        lines.append("")
        lines.append("| Field | Count |")
        lines.append("|---|---|")
        for field, count in cong["discrepant_fields_frequency"].items():
            lines.append(f"| {field} | {count} |")
        lines.append("")

    # --- Top Arithmetic Failures ---
    lines.append("## Top Arithmetic Failures (by |delta|)")
    lines.append("")
    if report["top_arithmetic_deltas"]:
        lines.append("| Dept | Mpio | Zona | Puesto | Mesa | Source | Delta | URNA |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for entry in report["top_arithmetic_deltas"]:
            lines.append(
                f"| {entry['dept']} | {entry['mpio']} | {entry['zona']} "
                f"| {entry['puesto']} | {entry['mesa']} | {entry['source']} "
                f"| {entry['delta']} | {entry.get('urna', '')} |"
            )
    else:
        lines.append("_No arithmetic failures found._")
    lines.append("")

    return "\n".join(lines)


def cmd_report(args: argparse.Namespace) -> int:
    """Read a completed validation JSONL and produce a summary report."""
    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"[ERROR] Input JSONL not found: {input_path}", file=sys.stderr)
        return 1

    # Read all records
    records: list[dict] = []
    with open(input_path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"[WARN] Skipping malformed line {line_no}: {exc}", file=sys.stderr)

    print(f"[report] Loaded {len(records)} records from {input_path}")

    report = generate_summary_report(records)
    report["input_file"] = str(input_path)

    # Write JSON output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"[report] JSON written to {output_path}")

    # Write Markdown output
    md_path = output_path.with_suffix(".md")
    md_content = _render_markdown(report, str(input_path))
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"[report] Markdown written to {md_path}")

    # Print markdown to stdout for quick preview (UTF-8 safe on all platforms)
    sys.stdout.buffer.write(("\n" + md_content).encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()

    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cross_validator_cli",
        description="Cross-mesa validator for E14C/E14T/E14D actas.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --- build-index ---
    bi = sub.add_parser("build-index", help="Build cross_mesa_index.jsonl")
    bi.add_argument("--e14c-jsonl", default=str(DEFAULT_E14C_JSONL),
                    help="Path to e14c_sv_urls.jsonl")
    bi.add_argument("--e14t-json", default=str(DEFAULT_E14T_JSON),
                    help="Path to allTransmissionCodes_segunda_index.json")
    bi.add_argument("--e14d-jsonl", default=str(DEFAULT_E14D_JSONL),
                    help="Path to e14d_sv_urls.jsonl")
    bi.add_argument("--e14c-root", default=str(DEFAULT_E14C_ROOT),
                    help="Root directory of E14C PDFs")
    bi.add_argument("--e14t-root", default=str(DEFAULT_E14T_ROOT),
                    help="Root directory of E14T PDFs")
    bi.add_argument("--e14d-root", default=str(DEFAULT_E14D_ROOT),
                    help="Root directory of E14D PDFs")
    bi.add_argument("--output", default=str(DEFAULT_INDEX_OUTPUT),
                    help="Output JSONL path (default: data/cross_mesa_index.jsonl)")

    # --- validate ---
    va = sub.add_parser("validate", help="Run cross-source validation")
    va.add_argument("--index", default=str(DEFAULT_INDEX_OUTPUT),
                    help="Path to cross_mesa_index.jsonl")
    va.add_argument("--output", default=str(DEFAULT_VALIDATE_OUTPUT),
                    help="Output JSONL path (default: data/cross_mesa_validation.jsonl)")
    va.add_argument("--workers", type=int, default=4,
                    help="Number of parallel worker processes (default: 4)")
    va.add_argument("--dept", default=None,
                    help="Only validate mesas for this dept code (e.g. '05')")
    va.add_argument("--limit", type=int, default=None,
                    help="Stop after N mesas (useful for smoke tests)")
    va.add_argument("--checkpoint", default=None,
                    help="Checkpoint file path (default: {output}.checkpoint.json)")

    # --- report ---
    rp = sub.add_parser("report", help="Generate summary report from validation JSONL")
    rp.add_argument("--input", required=True,
                    help="Path to completed validation JSONL")
    rp.add_argument("--output", required=True,
                    help="Output JSON path (Markdown is written alongside with .md extension)")

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "build-index":
        return cmd_build_index(args)
    elif args.command == "validate":
        return cmd_validate(args)
    elif args.command == "report":
        return cmd_report(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
