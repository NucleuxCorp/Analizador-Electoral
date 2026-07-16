"""
convertidor_pdf_md.py
=====================

Convierte archivos PDF a Markdown usando MarkItDown (Microsoft).

Flujo:
  1. Coloca tus PDFs en la carpeta  pdfs/
  2. Ejecuta este script (o ejecutar.bat)
  3. Los Markdown generados aparecen en  md/

Uso:
  python convertidor.py              # Convierte todos los PDFs pendientes
  python convertidor.py --force      # Re-convierte incluso los ya procesados
  python convertidor.py --clean      # Borra los .md anteriores antes de convertir
  python convertidor.py --embed-csv  # Detecta tablas y las embebe como CSV en el .md
  python convertidor.py --csv-files  # Genera ademas archivos .csv de respaldo por tabla
  python convertidor.py --input DIR  # Usa DIR como carpeta de entrada (default: pdfs/)
  python convertidor.py --output DIR # Usa DIR como carpeta de salida (default: md/)
  python convertidor.py --csv-dir DIR # Carpeta para .csv de respaldo (default: csv/)
"""

import argparse
import csv
import io
import sys
import time
from pathlib import Path

# --- Constants ---

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = SCRIPT_DIR / "pdfs"
DEFAULT_OUTPUT = SCRIPT_DIR / "md"
DEFAULT_CSV_DIR = SCRIPT_DIR / "csv"


# --- Helpers ---

def pause() -> None:
    """Wait for Enter key, or exit silently when stdin is non-interactive (piped)."""
    try:
        input()
    except EOFError:
        pass


def ensure_dir(path: Path, label: str) -> None:
    """Create directory if it does not exist."""
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
        print(f"  Carpeta {label} creada: {path}")
    elif not path.is_dir():
        print(f"  ERROR: {label} existe pero no es una carpeta: {path}")
        sys.exit(1)


def find_pdfs(input_dir: Path) -> list[Path]:
    """Return sorted list of .pdf files in the input directory (case-insensitive)."""
    return sorted(
        p for p in input_dir.iterdir()
        if p.is_file() and p.suffix.lower() == ".pdf"
    )


def get_markdown(result) -> str:
    """Extract markdown text from a MarkItDown result object.

    MarkItDown versions differ: newer use ``result.markdown``, older use
    ``result.text_content``.  Handle both transparently.
    """
    for attr in ("markdown", "text_content"):
        val = getattr(result, attr, None)
        if val:
            return val
    return ""


def table_to_csv(rows: list[list[str | None]]) -> str:
    """Render a 2D table (list of rows) as CSV plain text.

    None cells become empty strings; newlines inside cells are collapsed to
    spaces so they don't break CSV row boundaries.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    for row in rows:
        cleaned = [(c or "").replace("\n", " ").replace("\r", " ").strip() for c in row]
        writer.writerow(cleaned)
    return buf.getvalue().rstrip("\n")


def clean_table(rows: list[list[str | None]]) -> list[list[str | None]]:
    """Drop rows that are empty (no cell has any content)."""
    return [row for row in rows if any((c or "").strip() for c in row)]


def extract_tables(pdf_path: Path) -> list[tuple[int, list[list[str | None]]]]:
    """Detect tables on every page with pdfplumber.

    Returns a list of (page_number, cleaned_rows).  Empty list means no
    tables were found or pdfplumber is not available.
    """
    try:
        import pdfplumber
    except ImportError:
        return []

    result: list[tuple[int, list[list[str | None]]]] = []
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page_idx, page in enumerate(pdf.pages, 1):
                for table in page.extract_tables():
                    if not table:
                        continue
                    meaningful = clean_table(table)
                    if meaningful:
                        result.append((page_idx, meaningful))
    except Exception:
        return []
    return result


def render_embedded_tables(
    tables: list[tuple[int, list[list[str | None]]]],
) -> str:
    """Build the Markdown section with fenced ```csv blocks from extracted tables."""
    if not tables:
        return ""

    blocks: list[str] = []
    for idx, (page_idx, rows) in enumerate(tables, 1):
        blocks.append(
            f"\n\n### Tabla {idx} (pagina {page_idx})\n\n"
            f"```csv\n{table_to_csv(rows)}\n```\n"
        )

    header = (
        "\n\n---\n\n"
        "## Tablas detectadas (CSV embebido)\n\n"
        f"_{len(tables)} tabla(s) extraida(s) con pdfplumber._\n"
    )
    return header + "".join(blocks)


def write_csv_files(
    tables: list[tuple[int, list[list[str | None]]]],
    pdf_path: Path,
    csv_dir: Path,
) -> int:
    """Write each table as a standalone .csv file inside a per-PDF subfolder.

    Layout:  csv_dir/{pdf_stem}/tabla{N}.csv
    Each PDF gets its own folder so tables from different PDFs never collide.
    Only meaningful (non-empty) tables are written.  Returns count written.
    """
    if not tables:
        return 0

    # Per-PDF subfolder
    pdf_subdir = csv_dir / pdf_path.stem
    pdf_subdir.mkdir(parents=True, exist_ok=True)

    written = 0
    for idx, (_page_idx, rows) in enumerate(tables, 1):
        out_path = pdf_subdir / f"tabla{idx}.csv"
        out_path.write_text(table_to_csv(rows) + "\n", encoding="utf-8")
        written += 1
    return written


def convert_one(
    md_converter,
    pdf_path: Path,
    md_path: Path,
    embed_csv: bool = False,
    csv_dir: Path | None = None,
) -> tuple[bool, str, int, int]:
    """Convert a single PDF to Markdown.

    Returns (success, message, embedded_table_count, csv_file_count).
    """
    try:
        result = md_converter.convert(str(pdf_path))
        content = get_markdown(result)
        if not content.strip():
            return False, "Sin contenido extraido (PDF podria ser escaneado/sin texto)", 0, 0

        # Single extraction pass shared by embed + file modes
        tables = []
        if embed_csv or csv_dir is not None:
            tables = extract_tables(pdf_path)

        embedded_count = 0
        csv_file_count = 0
        table_section = ""

        if embed_csv and tables:
            table_section = render_embedded_tables(tables)
            embedded_count = len(tables)

        if csv_dir is not None and tables:
            csv_file_count = write_csv_files(tables, pdf_path, csv_dir)

        final_content = content + table_section
        md_path.write_text(final_content, encoding="utf-8")

        msg = f"{len(final_content)} caracteres"
        if embedded_count:
            msg += f", {embedded_count} tabla(s) embebida(s)"
        if csv_file_count:
            msg += f", {csv_file_count} CSV file(s)"
        return True, msg, embedded_count, csv_file_count
    except Exception as exc:
        return False, f"Error: {exc}", 0, 0
    """Convert a single PDF to Markdown.

    Returns (success, message, table_count).
    """
    try:
        result = md_converter.convert(str(pdf_path))
        content = get_markdown(result)
        if not content.strip():
            return False, "Sin contenido extraido (PDF podria ser escaneado/sin texto)", 0

        table_section = ""
        table_count = 0
        if embed_csv:
            table_section = extract_embedded_tables(pdf_path)
            # Count embedded tables by counting ```csv fences
            table_count = table_section.count("```csv")

        final_content = content + table_section
        md_path.write_text(final_content, encoding="utf-8")
        msg = f"{len(final_content)} caracteres"
        if table_count:
            msg += f", {table_count} tabla(s) CSV"
        return True, msg, table_count
    except Exception as exc:
        return False, f"Error: {exc}", 0


# --- Main ---

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convierte PDFs a Markdown con MarkItDown.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-convierte incluso los PDFs que ya tienen un .md generado.",
    )
    parser.add_argument(
        "--clean", action="store_true",
        help="Borra todos los .md de la carpeta de salida antes de convertir.",
    )
    parser.add_argument(
        "--input", type=Path, default=DEFAULT_INPUT,
        help=f"Carpeta de entrada con PDFs (default: {DEFAULT_INPUT.name}/).",
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT,
        help=f"Carpeta de salida para Markdown (default: {DEFAULT_OUTPUT.name}/).",
    )
    parser.add_argument(
        "--embed-csv", action="store_true",
        help="Detecta tablas con pdfplumber y las embebe dentro del .md como CSV en texto plano.",
    )
    parser.add_argument(
        "--csv-files", action="store_true",
        help="Genera ademas archivos .csv de respaldo, uno por tabla, en csv_dir/{pdf_stem}/.",
    )
    parser.add_argument(
        "--csv-dir", type=Path, default=DEFAULT_CSV_DIR,
        help=f"Carpeta base para .csv de respaldo (default: {DEFAULT_CSV_DIR.name}/).",
    )
    args = parser.parse_args()

    # --- Banner ---
    print()
    print("=" * 50)
    print("  Convertidor PDF -> Markdown  (MarkItDown)")
    print("=" * 50)
    print()

    # --- Validate directories ---
    ensure_dir(args.input, "entrada (PDFs)")
    ensure_dir(args.output, "salida (Markdown)")

    # --- Clean mode ---
    if args.clean:
        deleted = 0
        for f in args.output.glob("*.md"):
            f.unlink()
            deleted += 1
        print(f"  Limpiados {deleted} archivo(s) .md de {args.output}")
        print()

    # --- Find PDFs ---
    pdfs = find_pdfs(args.input)
    if not pdfs:
        print(f"  No se encontraron PDFs en: {args.input}")
        print()
        print("  Coloca tus archivos .pdf en esa carpeta y vuelve a ejecutar.")
        print()
        print("  Presiona Enter para salir...")
        pause()
        return

    # --- Determine which to process ---
    to_process = []
    skipped = 0
    for pdf in pdfs:
        md_path = args.output / (pdf.stem + ".md")
        if md_path.exists() and not args.force:
            skipped += 1
            continue
        to_process.append((pdf, md_path))

    print(f"  PDFs encontrados : {len(pdfs)}")
    print(f"  Ya convertidos  : {skipped} (usa --force para re-convertir)")
    print(f"  A procesar      : {len(to_process)}")
    if args.embed_csv:
        print(f"  Modo CSV embebido: ACTIVO (pdfplumber)")
    print()

    if not to_process:
        print("  Nada que procesar. Todo esta al dia.")
        print()
        print("  Presiona Enter para salir...")
        pause()
        return

    # --- Import MarkItDown ---
    try:
        from markitdown import MarkItDown
    except ImportError:
        print("  ERROR: markitdown no esta instalado.")
        print()
        print("  Instala las dependencias:")
        print("    pip install -r requirements.txt")
        print()
        print("  Presiona Enter para salir...")
        pause()
        sys.exit(1)

    # --- Initialize converter ---
    try:
        converter = MarkItDown()
    except Exception as exc:
        print(f"  ERROR al inicializar MarkItDown: {exc}")
        print()
        print("  Presiona Enter para salir...")
        pause()
        sys.exit(1)

    print("  Inicializando MarkItDown ... listo")
    print()

    # --- Convert ---
    ok = 0
    fail = 0
    empty = 0
    total_embedded = 0
    total_csv_files = 0
    t0 = time.time()

    csv_dir = args.csv_dir if args.csv_files else None

    for i, (pdf_path, md_path) in enumerate(to_process, 1):
        prefix = f"  [{i}/{len(to_process)}] {pdf_path.name}"
        print(prefix, end="", flush=True)

        success, msg, embedded, csv_files = convert_one(
            converter,
            pdf_path,
            md_path,
            embed_csv=args.embed_csv,
            csv_dir=csv_dir,
        )
        total_embedded += embedded
        total_csv_files += csv_files

        if success:
            ok += 1
            print(f"  -> OK ({msg})")
        else:
            if "Sin contenido" in msg:
                empty += 1
            else:
                fail += 1
            md_path.unlink(missing_ok=True)
            print(f"  -> FALLA ({msg})")

    elapsed = time.time() - t0

    # --- Summary ---
    print()
    print("=" * 50)
    print("  RESUMEN")
    print("=" * 50)
    print(f"  Procesados  : {len(to_process)}")
    print(f"  Exitosos    : {ok}")
    print(f"  Sin contenido: {empty} (PDFs escaneados/sin texto)")
    print(f"  Fallidos     : {fail}")
    if args.embed_csv:
        print(f"  Tablas embebidas: {total_embedded}")
    if args.csv_files:
        print(f"  Archivos CSV    : {total_csv_files}")
        print(f"  Carpeta CSV     : {args.csv_dir}")
    print(f"  Tiempo       : {elapsed:.1f}s")
    print(f"  Carpeta MD   : {args.output}")
    print()
    print("  Presiona Enter para salir...")
    pause()


if __name__ == "__main__":
    main()