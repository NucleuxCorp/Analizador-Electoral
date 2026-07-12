#!/usr/bin/env python3
"""Analiza los 4 bloques del E-14 segunda vuelta y extrae coordenadas de celdas."""
import cv2, numpy as np

img = cv2.imread("docs/SV_27_031_M002_8467816_pages-to-jpg-0001.jpg")
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

# Coordenadas de los 4 bloques (del primer analisis, que SI funciono)
blocks = [
    ("SUMA_TOTAL",   75, 496, 967, 202),   # bloque 1
    ("CANDIDATO_1",  63, 1290, 967, 485),  # bloque 4
    ("CANDIDATO_2",  48, 1786, 976, 500),  # bloque 5
    ("BLANCO_NULOS", 40, 2330, 979, 448),  # bloque 6
]

colors = [(255,0,0), (0,255,0), (0,0,255), (255,255,0)]
debug = img.copy()

all_coords = {}

for bidx, (name, bx, by, bw, bh) in enumerate(blocks):
    roi = gray[by:by+bh, bx:bx+bw]
    
    # Probar varios umbrales y quedarse con el que mas celdas detecta
    best_cells = []
    for thresh_val in [80, 100, 120, 140]:
        _, roi_thresh = cv2.threshold(roi, thresh_val, 255, cv2.THRESH_BINARY_INV)
        roi_contours, _ = cv2.findContours(roi_thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        cells = []
        for cnt in roi_contours:
            cx, cy, cw, ch = cv2.boundingRect(cnt)
            area = cw * ch
            if 100 < area < 6000 and 20 < cw < 65 and 20 < ch < 65:
                ratio = cw / ch if ch else 0
                if 0.4 < ratio < 3.5:
                    cells.append((cx+bx, cy+by, cw, ch))
        
        if len(cells) > len(best_cells):
            best_cells = cells
    
    # Agrupar en filas
    best_cells.sort(key=lambda c: (c[1], c[0]))
    rows = []
    cur = []
    ly = -1
    for c in best_cells:
        if ly < 0 or abs(c[1]-ly) < 30:
            cur.append(c)
        else:
            if cur: rows.append(sorted(cur, key=lambda x: x[0]))
            cur = [c]
        ly = c[1]
    if cur: rows.append(sorted(cur, key=lambda x: x[0]))
    
    print(f"\n=== {name} ({len(best_cells)} celdas, {len(rows)} filas) ===")
    
    block_coords = []
    for ri, row in enumerate(rows):
        # Dividir en grupos de 3 (dígitos de un número)
        groups = [row[i:i+3] for i in range(0, len(row), 3)]
        for gi, group in enumerate(groups):
            if len(group) >= 2:
                coords = [(c[0], c[1], c[2], c[3]) for c in group]
                print(f"  Fila{ri} G{gi}: {coords}")
                block_coords.append(coords)
    
    all_coords[name] = block_coords
    
    # Debug
    cv2.rectangle(debug, (bx, by), (bx+bw, by+bh), colors[bidx], 2)
    cv2.putText(debug, f"{name} ({len(best_cells)}c)", (bx, by-5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, colors[bidx], 1)
    for (cx, cy, cw, ch) in best_cells:
        cv2.rectangle(debug, (cx, cy), (cx+cw, cy+ch), colors[bidx], 1)

cv2.imwrite("debug_sv/blocks_final.jpg", debug)
print("\n✅ debug_sv/blocks_final.jpg")

# Guardar coordenadas como JSON para el extractor
import json
with open("debug_sv/block_coords.json", "w") as f:
    json.dump(all_coords, f, indent=2)
print("✅ debug_sv/block_coords.json")
