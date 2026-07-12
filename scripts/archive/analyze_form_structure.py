#!/usr/bin/env python3
"""Analiza la primera pagina de un PDF y detecta automaticamente las celdas de digitos."""
import sys, json
from pathlib import Path
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

def analyze_pdf_structure(pdf_path: str):
    import fitz
    
    doc = fitz.open(pdf_path)
    page = doc[0]
    
    # Renderizar a 300 DPI como el extractor
    mat = fitz.Matrix(300/72, 300/72)
    pix = page.get_pixmap(matrix=mat)
    img = np.frombuffer(pix.tobytes("png"), np.uint8)
    page_img = cv2.imdecode(img, cv2.IMREAD_COLOR)
    gray = cv2.cvtColor(page_img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    
    print(f"Dimensiones: {w}x{h} px a 300 DPI")
    
    # 1. Detectar lineas verticales y horizontales (tabla del formulario)
    # Umbral adaptativo para aislar el contenido
    thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                    cv2.THRESH_BINARY_INV, 21, 4)
    
    # Detectar componentes conectados
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(thresh, 8)
    
    # Filtrar componentes que parecen celdas de digitos (pequenos rectangulos)
    cells = []
    for i in range(1, num_labels):
        x = stats[i, cv2.CC_STAT_LEFT]
        y = stats[i, cv2.CC_STAT_TOP]
        cw = stats[i, cv2.CC_STAT_WIDTH]
        ch = stats[i, cv2.CC_STAT_HEIGHT]
        area = stats[i, cv2.CC_STAT_AREA]
        
        # Una celda de digito tipicamente tiene area entre 200 y 2000 px
        # y proporcion entre 1:1 y 2:1
        if 200 < area < 4000 and 20 < cw < 80 and 20 < ch < 60:
            ratio = cw / ch if ch > 0 else 0
            if 0.5 < ratio < 3.0:
                cells.append((x, y, cw, ch))
    
    # Agrupar por fila (similar y)
    cells.sort(key=lambda c: (c[1], c[0]))
    
    # Agrupar en filas
    rows = []
    current_row = []
    last_y = -1
    for cell in cells:
        if last_y < 0 or abs(cell[1] - last_y) < 20:
            current_row.append(cell)
        else:
            if current_row:
                rows.append(sorted(current_row, key=lambda c: c[0]))
            current_row = [cell]
        last_y = cell[1]
    if current_row:
        rows.append(sorted(current_row, key=lambda c: c[0]))
    
    print(f"\nTotal celdas de digitos detectadas: {len(cells)}")
    print(f"Filas detectadas: {len(rows)}")
    print()
    
    # Mostrar las primeras 20 filas con sus coordenadas
    for idx, row in enumerate(rows[:30]):
        avg_y = int(np.mean([c[1] for c in row]))
        row_str = "; ".join([f"({c[0]},{c[1]},{c[2]},{c[3]})" for c in row[:6]])
        if len(row) > 6:
            row_str += f" ... +{len(row)-6} mas"
        print(f"  Fila {idx}: y~{avg_y} | {row_str}")
    
    # Guardar imagen debug con celdas marcadas
    debug = page_img.copy()
    for (x, y, cw, ch) in cells:
        cv2.rectangle(debug, (x, y), (x+cw, y+ch), (0, 255, 0), 1)
    
    out_dir = Path("debug_sv")
    out_dir.mkdir(exist_ok=True)
    cv2.imwrite(str(out_dir / "cells_detected.png"), debug)
    print(f"\nImagen debug guardada: {out_dir / 'cells_detected.png'}")
    
    doc.close()
    
    return cells, rows


if __name__ == "__main__":
    pdf = "data/pdfs_segunda_vuelta/27/3862819_M012.pdf"
    if len(sys.argv) > 1:
        pdf = sys.argv[1]
    analyze_pdf_structure(pdf)
