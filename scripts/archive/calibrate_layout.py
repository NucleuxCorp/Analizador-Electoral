#!/usr/bin/env python3
"""Interpretar las coordenadas detectadas y generar layout SV."""
import json
import cv2
import numpy as np
from pathlib import Path
from collections import defaultdict

import fitz

pdf_path = "data/pdfs_segunda_vuelta/27/3862819_M012.pdf"

doc = fitz.open(pdf_path)
page = doc[0]
mat = fitz.Matrix(300/72, 300/72)
pix = page.get_pixmap(matrix=mat)
img = np.frombuffer(pix.tobytes("png"), np.uint8)
page_img = cv2.imdecode(img, cv2.IMREAD_COLOR)
gray = cv2.cvtColor(page_img, cv2.COLOR_BGR2GRAY)
h, w = gray.shape

print(f"Imagen: {w}x{h}")

# Detectar celdas
thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                cv2.THRESH_BINARY_INV, 21, 4)
num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(thresh, 8)

cells = []
for i in range(1, num_labels):
    x = stats[i, cv2.CC_STAT_LEFT]
    y = stats[i, cv2.CC_STAT_TOP]
    cw = stats[i, cv2.CC_STAT_WIDTH]
    ch = stats[i, cv2.CC_STAT_HEIGHT]
    area = stats[i, cv2.CC_STAT_AREA]
    if 200 < area < 4000 and 20 < cw < 80 and 20 < ch < 60:
        ratio = cw / ch if ch > 0 else 0
        if 0.5 < ratio < 3.0:
            cells.append((x, y, cw, ch, area))

# Agrupar en filas
cells.sort(key=lambda c: (c[1], c[0]))
rows = []
current_row = []
last_y = -1
for cell in cells:
    if last_y < 0 or abs(cell[1] - last_y) < 25:
        current_row.append(cell)
    else:
        if current_row:
            rows.append(sorted(current_row, key=lambda c: c[0]))
        current_row = [cell]
    last_y = cell[1]
if current_row:
    rows.append(sorted(current_row, key=lambda c: c[0]))

# Filtrar filas con al menos 3 celdas (probablemente una fila de datos)
valid_rows = [r for r in rows if len(r) >= 4]
print(f"\nFilas con 4+ celdas: {len(valid_rows)}")

# Buscar el bloque de votos (varias filas cercanas con 6+ celdas cada una)
# En segunda vuelta: 2 candidatos + blanco = ~3 filas de datos
# Cada fila tiene: 3 celdas de votos + 3 de total = 6 celdas tipicamente
print("\nFilas con 6+ celdas (probables filas de votacion):")
vote_rows = []
for idx, row in enumerate(valid_rows):
    n_cells = len(row)
    if n_cells >= 6:
        avg_y = int(np.mean([c[1] for c in row]))
        x_min = min(c[0] for c in row)
        x_max = max(c[0] for c in row)
        print(f"  Fila {idx}: y~{avg_y} | {n_cells} celdas | x={x_min}-{x_max}")
        vote_rows.append((idx, avg_y, row))

# Analizar las primeras 6 filas de votacion (probablemente el bloque principal)
if len(vote_rows) >= 6:
    print(f"\n--- Coordenadas para layout_sv.py ---")
    print()
    print("from layout import FIELD_MAP, SCALE")
    print()
    
    # Tomar las primeras filas como candidatos
    labels = ["candidato_1", "candidato_2", "blanco", "nulos", "no_marcados", "total_mesa"]
    
    for i, (idx, avg_y, row) in enumerate(vote_rows[:6]):
        label = labels[i] if i < len(labels) else f"campo_{i}"
        
        # Dividir las celdas en grupos de 3 (3 votos + 3 total)
        # Asumiendo que las primeras 3 celdas son votos y las siguientes 3 son total
        votos_cells = row[:3]
        total_cells = row[3:6] if len(row) >= 6 else row[3:]
        
        print(f"# {label} (y~{avg_y})")
        print(f"{label.upper()}_VOTOS = [")
        for c in votos_cells[:3]:
            print(f"    ({c[0]}, {c[1]}, {c[2]}, {c[3]}),")
        print("]")
        print()
        
        if total_cells:
            print(f"{label.upper()}_TOTAL = [")
            for c in total_cells[:3]:
                print(f"    ({c[0]}, {c[1]}, {c[2]}, {c[3]}),")
            print("]")
            print()
    
    print("FIELD_MAP = {")
    for label in labels:
        print(f'    "{label}_votos": {label.upper()}_VOTOS,')
        if label != "no_marcados":
            print(f'    "{label}_total": {label.upper()}_TOTAL,')
    print("}")

# Guardar debug
debug = page_img.copy()
for idx, row in enumerate(valid_rows):
    color = (0, 255, 0) if len(row) >= 6 else (100, 100, 100)
    for (x, y, cw, ch, _) in row:
        cv2.rectangle(debug, (x, y), (x+cw, y+ch), color, 1)
    if len(row) >= 6:
        cv2.putText(debug, str(idx), (min(c[0] for c in row), min(c[1] for c in row)-5),
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)

cv2.imwrite("debug_sv/vote_rows.png", debug)
print(f"\nDebug: debug_sv/vote_rows.png")

doc.close()
