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
  python convertidor.py --input DIR  # Usa DIR como carpeta de entrada (default: pdfs/)
  python convertidor.py --output DIR # Usa DIR como carpeta de salida (default: md/)
"""

import argparse
import sys
import time
from pathlib import Path

# --- Constants ---

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = SCRIPT_DIR / "pdfs"
DEFAULT_OUTPUT = SCRIPT_DIR / "md"


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


def convert_one(md_converter, pdf_path: Path, md_path: Path) -> tuple[bool, str]:
    """Convert a single PDF to Markdown.

    Returns (success, message).
    """
    try:
        result = md_converter.convert(str(pdf_path))
        content = get_markdown(result)
        if not content.strip():
            return False, "Sin contenido extraido (PDF podria ser escaneado/sin texto)"
        md_path.write_text(content, encoding="utf-8")
        return True, f"{len(content)} caracteres"
    except Exception as exc:
        return False, f"Error: {exc}"


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
    t0 = time.time()

    for i, (pdf_path, md_path) in enumerate(to_process, 1):
        prefix = f"  [{i}/{len(to_process)}] {pdf_path.name}"
        print(prefix, end="", flush=True)

        success, msg = convert_one(converter, pdf_path, md_path)

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
    print(f"  Tiempo       : {elapsed:.1f}s")
    print(f"  Carpeta MD   : {args.output}")
    print()
    print("  Presiona Enter para salir...")
    pause()


if __name__ == "__main__":
    main()