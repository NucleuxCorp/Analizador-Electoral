"""Rasterize a PDF page-by-page into a new image-only PDF (anti copy/paste)."""
import sys
import os
import fitz  # PyMuPDF


def rasterize(src: str, dst: str, dpi: int = 200) -> None:
    doc = fitz.open(src)
    out = fitz.open()
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)

    for page in doc:
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img_bytes = pix.tobytes("png")

        # Create a new PDF page matching the original page size in points.
        rect = page.rect
        new_page = out.new_page(width=rect.width, height=rect.height)
        new_page.insert_image(rect, stream=img_bytes)

    out.save(dst, deflate=True, garbage=4)
    n_pages = out.page_count
    out.close()
    doc.close()

    size_kb = os.path.getsize(dst) / 1024
    print(f"FINAL PDF: {size_kb:.1f} KB, {n_pages} pages -> {dst}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python rasterize_pdf.py <input.pdf> <output.pdf> [dpi]")
        sys.exit(1)
    dpi = int(sys.argv[3]) if len(sys.argv) > 3 else 200
    rasterize(sys.argv[1], sys.argv[2], dpi)