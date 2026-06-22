"""
Batch E-14C segunda vuelta analyzer with candidate fallback.

Pipeline per PDF:
  1. Primary: grid_detector_v2 (full grid + guess_label)
  2. If C1 or C2 missing → candidate_subcells fallback (row-scoped sub-cells)
  3. Flag suspicious actas (missing candidates, arithmetic mismatch, etc.)

Run (PowerShell):
    Set-Location "D:/Nucleux/tools/Analizador de Elecciones"
    python debug_sv/analyze_e14_batch.py --validate
    python debug_sv/analyze_e14_batch.py --concurrent 100 --save-crops
    python debug_sv/analyze_e14_batch.py --dept 01 --concurrent 8
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from debug_sv.e14_worker import init_worker, process_pdf_task, VOTE_FIELDS

PDF_DIR = ROOT / "data" / "pdfs_e14c_segunda"
OUT_DIR = ROOT / "data" / "analysis_segunda_vuelta"

# Default paths (full batch)
RESULTS_JSONL = OUT_DIR / "e14c_grid_results.jsonl"
SUSPICIOUS_JSONL = OUT_DIR / "suspicious.jsonl"
CHECKPOINT = OUT_DIR / "e14c_batch_checkpoint.json"
COMPLETE_MARKER = OUT_DIR / "e14c_batch_complete.json"

# Validation run (first 100 PDFs, isolated from full batch)
VALIDATE_RESULTS = OUT_DIR / "e14c_validate_results.jsonl"
VALIDATE_SUSPICIOUS = OUT_DIR / "e14c_validate_suspicious.jsonl"
VALIDATE_CHECKPOINT = OUT_DIR / "e14c_validate_checkpoint.json"
VALIDATE_COMPLETE = OUT_DIR / "e14c_validate_complete.json"
VALIDATE_LIMIT = 100

_WIN_MAX_PROCESSES = 61
_SAFE_MAX_PROCESSES = 8

console = Console(force_terminal=True)


def _silence_cnn_logs() -> None:
    """Keep Rich progress bar clean — no per-worker CNN load spam."""
    logging.disable(logging.CRITICAL)
    for name in ("src.modules.analyzer.ocr_engines", "src.utils", ""):
        log = logging.getLogger(name)
        log.handlers.clear()
        log.propagate = False
        log.setLevel(logging.CRITICAL)


def load_checkpoint(path: Path) -> tuple[set[str], dict]:
    if not path.exists():
        return set(), {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return set(data.get("done", [])), data.get("stats", {})


def save_checkpoint(path: Path, done: set[str], stats: dict) -> None:
    path.write_text(
        json.dumps({"done": sorted(done), "stats": stats}, indent=2),
        encoding="utf-8",
    )


def append_jsonl(path: Path, record: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def update_stats(stats: dict, record: dict) -> None:
    if record.get("is_suspicious"):
        stats["suspicious"] = stats.get("suspicious", 0) + 1
    else:
        stats["passed"] = stats.get("passed", 0) + 1
    if record.get("fallback_triggered"):
        stats["fallback_used"] = stats.get("fallback_used", 0) + 1
    fields = record.get("fields", {})
    if fields.get("URNA") is not None:
        vote_sum = sum(fields.get(k, 0) or 0 for k in VOTE_FIELDS)
        if vote_sum == fields["URNA"]:
            stats["arith_ok"] = stats.get("arith_ok", 0) + 1


def _fmt_val(fields: dict, key: str) -> str:
    v = fields.get(key)
    return str(v) if v is not None else "—"


def format_result_log(record: dict, index: int, session_total: int) -> str:
    """Colored one-line log per E-14."""
    name = Path(record.get("pdf", "?")).name
    prefix = f"[dim][{index}/{session_total}][/dim]"

    if record.get("error"):
        return (
            f"{prefix} [bold red]ERR[/bold red] 💥 "
            f"[bold red]{name}[/bold red] — {record['error']}"
        )

    fields = record.get("fields", {})
    c1 = _fmt_val(fields, "C1_CEPEDA")
    c2 = _fmt_val(fields, "C2_ABELARDO")
    urna = _fmt_val(fields, "URNA")
    fb = " [dim]↩fallback[/dim]" if record.get("fallback_triggered") else ""

    if record.get("is_suspicious"):
        reasons = record.get("suspicious_reasons", [])
        reason_txt = ", ".join(reasons[:3])
        if len(reasons) > 3:
            reason_txt += "…"
        critical = any(
            r.startswith("missing_") or r.startswith("arithmetic_")
            for r in reasons
        )
        if critical:
            tag, style = "[bold red]SUS![/bold red] 🚨", "bold red"
        else:
            tag, style = "[yellow]SUS[/yellow] ⚠", "yellow"
        return (
            f"{prefix} {tag} [{style}]{name}[/{style}]{fb} "
            f"C1=[{style}]{c1}[/{style}] C2=[{style}]{c2}[/{style}] "
            f"URNA=[{style}]{urna}[/{style}] "
            f"[dim]— {reason_txt}[/dim]"
        )

    return (
        f"{prefix} [bold green] OK [/bold green] ✅ "
        f"[green]{name}[/green]{fb} "
        f"C1=[green]{c1}[/green] C2=[green]{c2}[/green] "
        f"URNA=[green]{urna}[/green]"
    )


def counter_bar(stats: dict, session_done: int, session_total: int, rate: float) -> str:
    passed = stats.get("passed", 0)
    suspicious = stats.get("suspicious", 0)
    fallback = stats.get("fallback_used", 0)
    arith = stats.get("arith_ok", 0)
    total_done = stats.get("processed", 0)
    pct = (session_done / session_total * 100) if session_total else 0
    return (
        f"[cyan]📊 {session_done}/{session_total} ({pct:.1f}%)[/cyan] "
        f"[green]✅ {passed}[/green] "
        f"[yellow]⚠️ {suspicious}[/yellow] "
        f"[blue]↩ {fallback}[/blue] "
        f"[magenta]∑ {arith}[/magenta] "
        f"[dim]total {total_done} · {rate:.1f}/s[/dim]"
    )


def print_header(total: int, pending: int, done_count: int, workers: int,
                 requested: int, ram_cap: int) -> None:
    lines = [
        f"[bold]E-14C Segunda Vuelta — Análisis batch[/bold]",
        f"PDFs: [cyan]{total}[/cyan]  ·  Pendientes: [yellow]{pending}[/yellow]  ·  "
        f"Ya hechos: [green]{done_count}[/green]",
        f"Workers: [cyan]{workers}[/cyan] procesos",
    ]
    if requested > workers:
        lines.append(
            f"[dim]Solicitados {requested} → usando {workers} "
            f"(Windows máx {_WIN_MAX_PROCESSES}, RAM {ram_cap})[/dim]"
        )
    lines.append(
        "[dim][bold green] OK [/bold green]= pasa  ·  "
        "[yellow]SUS[/yellow]= sospechoso  ·  "
        "[bold red]SUS![/bold red]= crítico  ·  "
        "[bold red]ERR[/bold red]= error[/dim]"
    )
    console.print(Panel("\n".join(lines), border_style="cyan"))


def print_summary(stats: dict, elapsed_s: float, results_path: Path,
                  suspicious_path: Path, complete_path: Path) -> None:
    passed = stats.get("passed", 0)
    suspicious = stats.get("suspicious", 0)
    total = stats.get("processed", 0)
    pct_ok = (passed / total * 100) if total else 0
    pct_sus = (suspicious / total * 100) if total else 0

    console.print()
    console.print(Panel(
        f"[bold green]✅ LISTO[/bold green] — {elapsed_s/60:.1f} min\n\n"
        f"Procesados: [cyan]{total}[/cyan]\n"
        f"[green]✅ OK: {passed} ({pct_ok:.1f}%)[/green]\n"
        f"[yellow]⚠️ Sospechosos: {suspicious} ({pct_sus:.1f}%)[/yellow]\n"
        f"[blue]↩ Fallback: {stats.get('fallback_used', 0)}[/blue]\n"
        f"[magenta]∑ Aritmética OK: {stats.get('arith_ok', 0)}[/magenta]\n\n"
        f"Resultados: [dim]{results_path}[/dim]\n"
        f"Sospechosos: [dim]{suspicious_path}[/dim]\n"
        f"Marcador: [dim]{complete_path}[/dim]",
        border_style="green",
    ))


def main():
    parser = argparse.ArgumentParser(description="Batch E-14C SV analyzer")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dept", type=str, default=None, help="Filter by dept code e.g. 01")
    parser.add_argument("--concurrent", type=int, default=100,
                        help="Requested parallel workers (capped by OS/RAM)")
    parser.add_argument("--ram-workers", type=int, default=_SAFE_MAX_PROCESSES,
                        help=f"RAM-safe worker cap (default {_SAFE_MAX_PROCESSES})")
    parser.add_argument("--reset", action="store_true", help="Ignore checkpoint")
    parser.add_argument("--save-crops", action="store_true",
                        help="Save candidate crops when fallback runs")
    parser.add_argument("--quiet", action="store_true",
                        help="Hide per-PDF log lines (only counters + summary)")
    parser.add_argument("--validate", action="store_true",
                        help=f"Validation run: first {VALIDATE_LIMIT} PDFs, separate output files")
    args = parser.parse_args()
    _silence_cnn_logs()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.validate:
        args.limit = VALIDATE_LIMIT
        args.reset = True
        results_path = VALIDATE_RESULTS
        suspicious_path = VALIDATE_SUSPICIOUS
        checkpoint_path = VALIDATE_CHECKPOINT
        complete_path = VALIDATE_COMPLETE
    else:
        results_path = RESULTS_JSONL
        suspicious_path = SUSPICIOUS_JSONL
        checkpoint_path = CHECKPOINT
        complete_path = COMPLETE_MARKER

    pdfs = sorted(PDF_DIR.rglob("*.pdf"))
    if args.dept:
        pdfs = [p for p in pdfs if p.name.startswith(f"{args.dept}_")]
    if args.limit:
        pdfs = pdfs[: args.limit]

    done, prev_stats = (set(), {}) if args.reset else load_checkpoint(checkpoint_path)
    pending_rel = [
        str(p.relative_to(ROOT)).replace("\\", "/")
        for p in pdfs
        if str(p.relative_to(ROOT)).replace("\\", "/") not in done
    ]

    total = len(pdfs)
    worker_count = min(args.concurrent, _WIN_MAX_PROCESSES, args.ram_workers)

    if args.validate:
        console.print(
            f"[bold yellow]Modo validación[/bold yellow] — "
            f"primeros {VALIDATE_LIMIT} PDFs (no toca el batch completo)\n"
        )

    print_header(total, len(pending_rel), len(done), worker_count,
                 args.concurrent, args.ram_workers)

    stats = {
        "processed": len(done),
        "passed": prev_stats.get("passed", max(0, len(done) - prev_stats.get("suspicious", 0))),
        "suspicious": prev_stats.get("suspicious", 0),
        "fallback_used": prev_stats.get("fallback_used", 0),
        "arith_ok": prev_stats.get("arith_ok", 0),
        "concurrent": worker_count,
        "requested_concurrent": args.concurrent,
        "mode": "processes",
    }

    if not pending_rel:
        console.print("[green]Nada pendiente — batch ya completo.[/green]")
        complete_path.write_text(
            json.dumps({"completed_at": time.strftime("%Y-%m-%d %H:%M:%S"), "stats": stats},
                       indent=2),
            encoding="utf-8",
        )
        print_summary(stats, 0, results_path, suspicious_path, complete_path)
        return

    t0 = time.time()
    completed_batch = 0
    checkpoint_every = max(worker_count, 50)
    session_total = len(pending_rel)

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(bar_width=40),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
        expand=False,
    )

    interrupted = False
    session_done = 0

    try:
        with ProcessPoolExecutor(
            max_workers=worker_count,
            initializer=init_worker,
        ) as pool:
            futures = {
                pool.submit(process_pdf_task, rel, args.save_crops): rel
                for rel in pending_rel
            }

            with progress:
                task_id = progress.add_task("Iniciando…", total=session_total)

                for i, future in enumerate(as_completed(futures), 1):
                    rel = futures[future]
                    try:
                        record = future.result()
                    except Exception as e:
                        record = {
                            "pdf": rel.replace("\\", "/"),
                            "error": str(e),
                            "is_suspicious": True,
                            "suspicious_reasons": ["processing_error"],
                        }

                    append_jsonl(results_path, record)
                    if record.get("is_suspicious"):
                        append_jsonl(suspicious_path, record)

                    done.add(rel)
                    update_stats(stats, record)
                    completed_batch += 1
                    session_done = i

                    if completed_batch >= checkpoint_every or i == session_total:
                        stats["processed"] = len(done)
                        save_checkpoint(checkpoint_path, done, stats)
                        completed_batch = 0

                    el = time.time() - t0
                    rate = i / el if el > 0 else 0
                    progress.update(
                        task_id,
                        advance=1,
                        description=counter_bar(stats, i, session_total, rate),
                    )

                    if not args.quiet:
                        progress.console.print(format_result_log(record, i, session_total))

    except KeyboardInterrupt:
        interrupted = True
        console.print("\n[yellow]⏸ Interrumpido por usuario — checkpoint guardado[/yellow]")
    except Exception as e:
        interrupted = True
        stats["processed"] = len(done)
        save_checkpoint(checkpoint_path, done, stats)
        console.print(
            f"\n[bold red]💥 Pool de workers falló[/bold red] "
            f"(sesión {session_done}/{session_total}): {e}\n"
            f"[dim]Causa habitual: RAM insuficiente. "
            f"Baja --ram-workers o reanuda el mismo comando.[/dim]"
        )
        console.print(
            f"[cyan]Checkpoint guardado[/cyan] — {len(done)} PDFs en "
            f"{checkpoint_path.name}"
        )
        sys.exit(1)

    el = time.time() - t0
    stats["elapsed_s"] = round(el, 1)
    stats["processed"] = len(done)
    save_checkpoint(checkpoint_path, done, stats)

    if interrupted:
        sys.exit(130)

    stats["completed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    complete_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print_summary(stats, el, results_path, suspicious_path, complete_path)


if __name__ == "__main__":
    main()