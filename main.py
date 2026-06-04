#!/usr/bin/env python3
"""
Analizador de Elecciones — CLI entry point

Commands:
  collect-urls   Recolectar URLs de PDFs E-14 de Delegados
  status         Ver estado actual del scraping
  discover       Abrir el browser y mostrar los elementos del formulario (debug)
  reset          Borrar checkpoint y datos (requiere confirmacion)

Usage examples:
  python main.py collect-urls --departamento AMAZONAS --visible
  python main.py collect-urls --delay 2000
  python main.py status
  python main.py discover
  python main.py reset
"""
import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def cmd_collect_urls(args: argparse.Namespace) -> None:
    from src.modules.e14.scraper import collect_urls

    asyncio.run(
        collect_urls(
            headless=not args.visible,
            delay_ms=args.delay,
            target_dept=args.departamento,
            reset=args.reset,
        )
    )


def cmd_status(args: argparse.Namespace) -> None:
    from src.utils import storage
    from rich.console import Console
    from rich.table import Table

    console = Console()

    async def _show() -> None:
        stats = await storage.get_stats()
        urls_file = Path("data/urls/e14_urls.jsonl")

        table = Table(title="Estado del Scraping E-14", show_header=True)
        table.add_column("Metrica", style="cyan")
        table.add_column("Valor", style="green")

        table.add_row("Puestos registrados", str(stats["total_puestos"]))
        table.add_row("Completados", str(stats["completed_puestos"]))
        table.add_row("Pendientes (0% publicado)", str(stats["pending_puestos"]))
        table.add_row("URLs recolectadas", str(stats["total_urls"]))

        if urls_file.exists():
            size_mb = urls_file.stat().st_size / (1024 * 1024)
            table.add_row("Archivo JSONL", f"{urls_file} ({size_mb:.2f} MB)")
        else:
            table.add_row("Archivo JSONL", "(aun no creado)")

        console.print(table)

        if args.pending:
            pending = await storage.get_pending_summary()
            if pending:
                pt = Table(title=f"Pendientes ({len(pending)} entradas)", show_header=True)
                pt.add_column("Dpto")
                pt.add_column("Mpio")
                pt.add_column("Zona")
                pt.add_column("Puesto")
                pt.add_column("%")
                for p in pending[:50]:
                    pt.add_row(p["cod_dpto"], p["cod_mpio"], p["zona"], p["cod_puesto"], str(p["porcentaje"]))
                console.print(pt)
                if len(pending) > 50:
                    console.print(f"... y {len(pending)-50} mas")
            else:
                console.print("No hay entradas pendientes.")

    asyncio.run(_show())


def cmd_discover(args: argparse.Namespace) -> None:
    """Open the browser visibly and list all form elements found on the E-14 page."""
    import os
    from playwright.sync_api import sync_playwright

    base_url = os.getenv("E14_BASE_URL", "https://divulgacione14presidente.registraduria.gov.co/home")

    print(f"\nAbriendo {base_url} en modo visible...\n")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(base_url, wait_until="networkidle")

        # Discover selects
        selects = page.evaluate("""
            () => {
                function getLabel(el) {
                    if (el.id) {
                        const l = document.querySelector('label[for="' + el.id + '"]');
                        if (l) return l.textContent.trim();
                    }
                    let p = el.parentElement;
                    for (let i = 0; i < 6; i++) {
                        if (!p) break;
                        const l = p.querySelector('label, .label, span');
                        if (l && l.textContent.trim()) return l.textContent.trim();
                        p = p.parentElement;
                    }
                    return '?';
                }
                return Array.from(document.querySelectorAll('select')).map((s, i) => ({
                    index: i,
                    id: s.id,
                    name: s.name,
                    label: getLabel(s),
                    options: s.options.length,
                    selector: s.id ? '#' + s.id : (s.name ? 'select[name="' + s.name + '"]' : 'select:nth-of-type(' + (i+1) + ')')
                }));
            }
        """)

        print("=== SELECT ELEMENTS FOUND ===")
        for s in selects:
            print(f"  [{s['index']}] label='{s['label']}' | id='{s['id']}' | name='{s['name']}' | options={s['options']}")
            print(f"       selector: {s['selector']}")
            print()

        # Discover buttons
        btns = page.evaluate("""
            () => Array.from(document.querySelectorAll('button, input[type="submit"]')).map(b => ({
                tag: b.tagName,
                text: b.textContent.trim().substring(0, 60),
                type: b.type || '',
                id: b.id,
                classes: b.className.substring(0, 80)
            }))
        """)

        print("=== BUTTONS FOUND ===")
        for b in btns:
            print(f"  [{b['tag']}] text='{b['text']}' | id='{b['id']}' | classes='{b['classes']}'")

        print("\nPresiona Enter para cerrar el browser...")
        input()
        browser.close()


def cmd_analyze_e14c(args: argparse.Namespace) -> None:
    from pathlib import Path
    from src.modules.analyzer.form_extractor import process_directory
    from src.modules.analyzer.ocr_engines import get_engine

    pdf_dir = Path(args.dir)
    if not pdf_dir.exists():
        print(f"Directorio no encontrado: {pdf_dir}")
        return

    engine = get_engine(args.engine)
    out = Path(args.output) if args.output else pdf_dir / "analisis_resultados.jsonl"
    process_directory(pdf_dir, engine=engine, output_jsonl=out)
    print(f"\nResultados en: {out}")


def cmd_extract_notes(args: argparse.Namespace) -> None:
    from pathlib import Path
    from src.modules.analyzer.notes_extractor import batch_extract_notes, calibrate_region

    pdf_dir = Path(args.dir)
    if not pdf_dir.exists():
        print(f"Directorio no encontrado: {pdf_dir}")
        return

    if args.calibrate:
        pdfs = list(pdf_dir.rglob("*.pdf"))
        if not pdfs:
            print("No PDFs found for calibration.")
            return
        sample = pdfs[0]
        print(f"Calibrating on: {sample}")
        calibrate_region(sample, out_dir=Path("debug"))
        return

    out = Path(args.output) if args.output else pdf_dir.parent / f"{pdf_dir.name.lower()}_notes.jsonl"
    batch_extract_notes(
        pdf_dir,
        output_jsonl=out,
        skip_empty=args.skip_empty,
    )
    print(f"\nNotas en: {out}")


def cmd_download_e14c(args: argparse.Namespace) -> None:
    from src.modules.e14c.downloader import download_pdfs
    asyncio.run(download_pdfs(
        departamento=args.departamento,
        max_concurrent=args.concurrent,
    ))


def cmd_collect_e14c(args: argparse.Namespace) -> None:
    from src.modules.e14c.scraper import collect_e14c_urls
    asyncio.run(collect_e14c_urls(
        max_concurrent=args.concurrent,
        resume=not args.reset,
    ))


def cmd_export_crops(args: argparse.Namespace) -> None:
    from pathlib import Path
    from src.modules.labeler.exporter import run_export

    run_export(
        pdfs_root=Path("data/pdfs"),
        labels_dir=Path("data/labels"),
        limit=args.limit,
        dept_filter=args.dept,
    )


def cmd_label(args: argparse.Namespace) -> None:
    from pathlib import Path
    from src.modules.labeler.server import create_app

    index_path = Path("data/labels/crops/index.jsonl")
    labels_dir = Path("data/labels")
    app = create_app(index_path, labels_dir)
    print(f"Starting labeling portal on http://127.0.0.1:{args.port}")
    app.run(host="127.0.0.1", port=args.port, threaded=False)


def cmd_reset(args: argparse.Namespace) -> None:
    from src.utils import storage

    confirm = input("Esto borrara el checkpoint y el JSONL de URLs. Confirmar? [s/N]: ").strip().lower()
    if confirm in ("s", "si", "y", "yes"):
        asyncio.run(storage.reset_all())
        print("Listo. Checkpoint y datos reseteados.")
    else:
        print("Cancelado.")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Analizador de Elecciones — Modulo E-14 URL Collector",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # collect-urls
    p = sub.add_parser("collect-urls", help="Recolectar URLs de PDFs E-14")
    p.add_argument("--departamento", metavar="NOMBRE",
                   help="Procesar solo este departamento (nombre exacto, ej: AMAZONAS)")
    p.add_argument("--visible", action="store_true",
                   help="Mostrar ventana del browser (util para debug)")
    p.add_argument("--delay", type=int, default=1500, metavar="MS",
                   help="Delay minimo entre requests en ms (default: 1500)")
    p.add_argument("--reset", action="store_true",
                   help="Resetear checkpoint antes de iniciar")
    p.set_defaults(func=cmd_collect_urls)

    # analyze-e14c
    p = sub.add_parser("analyze-e14c", help="Analizar PDFs E-14C: OCR + validacion aritmetica + deteccion de tachones")
    p.add_argument("--dir", default="data/pdfs", metavar="DIR",
                   help="Directorio raiz de PDFs (default: data/pdfs)")
    p.add_argument("--output", metavar="FILE",
                   help="Archivo JSONL de resultados (default: {dir}/analisis_resultados.jsonl)")
    p.add_argument("--engine", default="segmented",
                   choices=["segmented", "easyocr", "trocr", "vlm"],
                   help="Motor OCR (default: segmented)")
    p.set_defaults(func=cmd_analyze_e14c)

    # extract-notes
    p = sub.add_parser("extract-notes", help="Extraer notas manuscritas de jurados de PDFs E-14C (seccion Notas/Constancias)")
    p.add_argument("--dir", default="data/pdfs", metavar="DIR",
                   help="Directorio raiz de PDFs (default: data/pdfs)")
    p.add_argument("--output", metavar="FILE",
                   help="Archivo JSONL de salida (default: data/{dept}_notes.jsonl)")
    p.add_argument("--skip-empty", action="store_true",
                   help="Omitir actas sin texto en la seccion de notas")
    p.add_argument("--calibrate", action="store_true",
                   help="Guardar imagenes de debug para verificar la region de notas")
    p.set_defaults(func=cmd_extract_notes)

    # download-e14c
    p = sub.add_parser("download-e14c", help="Descargar PDFs E-14C con estructura jerarquica de directorios")
    p.add_argument("--departamento", metavar="NOMBRE",
                   help="Solo descargar este departamento (ej: AMAZONAS, ANTIOQUIA)")
    p.add_argument("--concurrent", type=int, default=10, metavar="N",
                   help="Descargas simultaneas (default: 10)")
    p.set_defaults(func=cmd_download_e14c)

    # collect-e14c
    p = sub.add_parser("collect-e14c", help="Recolectar URLs de PDFs E-14C (escrutinio oficial) — sin browser")
    p.add_argument("--concurrent", type=int, default=30, metavar="N",
                   help="Requests simultaneos (default: 30)")
    p.add_argument("--reset", action="store_true",
                   help="Ignorar progreso previo y empezar de cero")
    p.set_defaults(func=cmd_collect_e14c)

    # export-crops
    p = sub.add_parser(
        "export-crops",
        help="Batch-export digit crops from PDFs into data/labels/crops/",
    )
    p.add_argument("--dept", metavar="DEPT",
                   help="Restrict to this department (e.g. AMAZONAS)")
    p.add_argument("--limit", type=int, default=2000, metavar="N",
                   help="Maximum number of PDFs to process (default: 2000)")
    p.set_defaults(func=cmd_export_crops)

    # label
    p = sub.add_parser(
        "label",
        help="Start the local human validation portal (Flask)",
    )
    p.add_argument("--port", type=int, default=5000, metavar="PORT",
                   help="Port to listen on (default: 5000)")
    p.add_argument("--dept", metavar="DEPT",
                   help="(reserved for future queue filtering by department)")
    p.set_defaults(func=cmd_label)

    # status
    p = sub.add_parser("status", help="Ver estado del scraping")
    p.add_argument("--pending", action="store_true", help="Mostrar lista de puestos pendientes (0%)")
    p.set_defaults(func=cmd_status)

    # discover
    p = sub.add_parser("discover", help="Inspeccionar elementos del formulario (abre browser)")
    p.set_defaults(func=cmd_discover)

    # reset
    p = sub.add_parser("reset", help="Resetear checkpoint y datos")
    p.set_defaults(func=cmd_reset)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
