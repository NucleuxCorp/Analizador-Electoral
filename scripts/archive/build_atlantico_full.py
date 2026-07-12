import json, hashlib
from pathlib import Path

pdfs = sorted(Path('E:/e14c_segunda/03').glob('03_*.pdf'))
entries = [{'pdf': str(p), 'dept': '03', 'stratum': 'atlantico_full'} for p in pdfs]

m = {
    'seed': 0, 'total_pdfs': len(pdfs), 'total_corpus': len(pdfs),
    'departments': 1, 'allocation_formula': 'atlantico_03_full',
    'entries': entries,
}
m['manifest_hash'] = 'sha256:' + hashlib.sha256(json.dumps(entries, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

out = Path(r'D:\Nucleux\tools\Analizador de Elecciones\data\analysis_segunda_vuelta\tachon_scan_atlantico_full_manifest.json')
out.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding='utf-8')
print(f'Manifest completo Atlantico: {len(pdfs)} PDFs')
print(f'Archivo: {out}')
