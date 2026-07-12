import json
from collections import defaultdict
from pathlib import Path

# ── Load manifest for full 500 list ──
manifest = json.loads(Path('data/analysis_segunda_vuelta/tachon_scan_500_manifest.json').read_text())
manifest_pdfs = {e['pdf']: e['dept'] for e in manifest['entries']}

# ── Aggregate per PDF from JSONL ──
pdfs = defaultdict(lambda: {
    'dept': '', 'enmienda': False, 'doble_escritura': False,
    'densidad_alta': False, 'zona_sucia': False,
    'ink_cells': 0, 'tachon_cells': 0, 'enmienda_cells': 0
})

with open('data/analysis_segunda_vuelta/tachon_method_scan_500.jsonl', encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        pdf = row['pdf']
        flags = row.get('flags_by_method', {})
        has_ink = row.get('has_ink', False)

        pdfs[pdf]['dept'] = row.get('dept', '')
        if has_ink:
            pdfs[pdf]['ink_cells'] += 1
        if flags.get('TACHON'):
            pdfs[pdf]['tachon_cells'] += 1
        if flags.get('DOBLE_ESCRITURA'):
            pdfs[pdf]['doble_escritura'] = True
            pdfs[pdf]['enmienda'] = True
            pdfs[pdf]['enmienda_cells'] += 1
        if flags.get('DENSIDAD_ALTA'):
            pdfs[pdf]['densidad_alta'] = True
            pdfs[pdf]['enmienda'] = True
            pdfs[pdf]['enmienda_cells'] += 1
        if flags.get('ZONA_SUCIA'):
            pdfs[pdf]['zona_sucia'] = True
            pdfs[pdf]['enmienda'] = True
            pdfs[pdf]['enmienda_cells'] += 1
        if flags.get('COMBINED'):
            pdfs[pdf]['enmienda'] = True
            pdfs[pdf]['enmienda_cells'] += 1

# ── Classify ──
scanned = set(pdfs.keys())
not_scanned = set(manifest_pdfs.keys()) - scanned

# PDFs with real issues (excluding tachon-only)
issues = []
for pdf, data in pdfs.items():
    flags = []
    if data['doble_escritura']:
        flags.append('DOBLE_ESCRITURA')
    if data['densidad_alta']:
        flags.append('DENSIDAD_ALTA')
    if data['zona_sucia']:
        flags.append('ZONA_SUCIA')
    if not flags and data['ink_cells'] == 0:
        flags.append('SIN_TINTA')
    if flags:
        issues.append({
            'pdf': pdf,
            'dept': data['dept'],
            'flags': flags,
            'ink_cells': data['ink_cells'],
            'tachon_cells': data['tachon_cells'],
            'enmienda_cells': data['enmienda_cells']
        })

# Add PDFs not scanned at all
for pdf in sorted(not_scanned):
    issues.append({
        'pdf': pdf,
        'dept': manifest_pdfs.get(pdf, '??'),
        'flags': ['NO_ESCANEADO'],
        'ink_cells': 0,
        'tachon_cells': 0,
        'enmienda_cells': 0
    })

issues.sort(key=lambda x: (-len(x['flags']), -x['enmienda_cells'], x['pdf']))

# ── Write list ──
out_path = Path('data/analysis_segunda_vuelta/tachon_enmienda_focused_list.json')
out_path.write_text(json.dumps({
    'total_pdfs': len(manifest_pdfs),
    'scanned': len(scanned),
    'not_scanned': len(not_scanned),
    'pdfs_with_issues': len(issues),
    'breakdown': {
        'enmienda_densidad_alta': sum(1 for x in issues if 'DENSIDAD_ALTA' in x['flags']),
        'enmienda_zona_sucia': sum(1 for x in issues if 'ZONA_SUCIA' in x['flags']),
        'doble_escritura': sum(1 for x in issues if 'DOBLE_ESCRITURA' in x['flags']),
        'sin_tinta': sum(1 for x in issues if 'SIN_TINTA' in x['flags']),
        'no_escaneado': sum(1 for x in issues if 'NO_ESCANEADO' in x['flags']),
    },
    'issues': issues
}, indent=2, ensure_ascii=False), encoding='utf-8')

# ── Print summary ──
print(f'Total PDFs en manifiesto: {len(manifest_pdfs)}')
print(f'Escaneados: {len(scanned)}')
print(f'No escaneados: {len(not_scanned)}')
print(f'PDFs con issues reales (excluyendo tachones puros): {len(issues)}')
print()
print('Desglose:')
print(f'  DENSIDAD_ALTA:  {sum(1 for x in issues if "DENSIDAD_ALTA" in x["flags"])}')
print(f'  ZONA_SUCIA:     {sum(1 for x in issues if "ZONA_SUCIA" in x["flags"])}')
print(f'  DOBLE_ESCRITURA:{sum(1 for x in issues if "DOBLE_ESCRITURA" in x["flags"])}')
print(f'  SIN_TINTA:      {sum(1 for x in issues if "SIN_TINTA" in x["flags"])}')
print(f'  NO_ESCANEADO:   {sum(1 for x in issues if "NO_ESCANEADO" in x["flags"])}')
print()
print(f'Archivo: {out_path}')
