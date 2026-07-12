import json
from collections import defaultdict

rows = []
with open('data/analysis_segunda_vuelta/tachon_method_scan_500.jsonl', encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line:
            rows.append(json.loads(line))

# Per PDF: does it have at least one flagged subcell?
pdfs = defaultdict(lambda: {'tachon': False, 'doble': False, 'densidad': False, 'zona': False, 'combined': False, 'ink_cells': 0, 'flagged_cells': 0})
for r in rows:
    pdf_id = r.get('pdf', 'unknown')
    flags = r.get('flags_by_method', {})
    has_ink = r.get('has_ink', False)
    if has_ink:
        pdfs[pdf_id]['ink_cells'] += 1
    if flags.get('TACHON', False):
        pdfs[pdf_id]['tachon'] = True
        pdfs[pdf_id]['flagged_cells'] += 1
    if flags.get('DOBLE_ESCRITURA', False):
        pdfs[pdf_id]['doble'] = True
    if flags.get('DENSIDAD_ALTA', False):
        pdfs[pdf_id]['densidad'] = True
    if flags.get('ZONA_SUCIA', False):
        pdfs[pdf_id]['zona'] = True
    if flags.get('COMBINED', False):
        pdfs[pdf_id]['combined'] = True

tp = len(pdfs)
tachon = sum(1 for p in pdfs.values() if p['tachon'])
doble = sum(1 for p in pdfs.values() if p['doble'])
dens = sum(1 for p in pdfs.values() if p['densidad'])
zona = sum(1 for p in pdfs.values() if p['zona'])
comb = sum(1 for p in pdfs.values() if p['combined'])
any_flag = sum(1 for p in pdfs.values() if p['tachon'] or p['doble'] or p['densidad'] or p['zona'] or p['combined'])
any_enmienda = sum(1 for p in pdfs.values() if p['doble'] or p['densidad'] or p['zona'] or p['combined'])
total_ink = sum(p['ink_cells'] for p in pdfs.values())
total_flagged = sum(p['flagged_cells'] for p in pdfs.values())

print(f'PDFs procesados: {tp}')
print()
print(f'PDFs con TACHONES (al menos 1):     {tachon:5d}  ({tachon/tp*100:.1f}%)')
print(f'PDFs con DOBLE ESCRITURA:           {doble:5d}  ({doble/tp*100:.1f}%)')
print(f'PDFs con DENSIDAD ALTA:             {dens:5d}  ({dens/tp*100:.1f}%)')
print(f'PDFs con ZONA SUCIA:                {zona:5d}  ({zona/tp*100:.1f}%)')
print(f'PDFs con COMBINED:                  {comb:5d}  ({comb/tp*100:.1f}%)')
print(f'PDFs con ENMIENDA (DOBLE+DENS+ZONA): {any_enmienda:5d}  ({any_enmienda/tp*100:.1f}%)')
print(f'PDFs con AL MENOS 1 FLAG:           {any_flag:5d}  ({any_flag/tp*100:.1f}%)')
print()
print(f'Total subceldas con tinta: {total_ink}')
print(f'Total subceldas flagged TACHON: {total_flagged}')
