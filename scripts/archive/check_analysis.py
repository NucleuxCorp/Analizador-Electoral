#!/usr/bin/env python3
import json
from collections import Counter

results = []
with open("data/analysis_segunda_vuelta/analysis_results.jsonl") as f:
    for line in f:
        line = line.strip()
        if line:
            results.append(json.loads(line))

print("Total PDFs:", len(results))
print("Validos:", sum(1 for r in results if r.get("valid_pdf")))
print("Errores:", sum(1 for r in results if r.get("error")))

digits_found = [r.get("digit_cells_found", 0) for r in results if r.get("digit_cells_found")]
if digits_found:
    cd = Counter(digits_found)
    print("\nDigitos encontrados por acta:")
    for k in sorted(cd):
        print(f"  {k}: {cd[k]} actas")
else:
    print("\nNo se encontraron digitos OCR")

print(f"\nTiempo promedio: {431/679:.1f}s por acta")
print("\nPrimeros 5 resultados OCR:")
ocred = [r for r in results if r.get("ocr_digits")]
for r in ocred[:5]:
    print(f"  {r['path']}: {[d['digit'] for d in r['ocr_digits'][:10]]}")
