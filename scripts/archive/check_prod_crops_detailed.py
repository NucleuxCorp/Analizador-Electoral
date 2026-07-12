import os
os.chdir(r'D:\Nucleux\tools\Analizador de Elecciones')
for line in open('.env').readlines():
    line=line.strip()
    if not line or line.startswith('#'): continue
    if '=' in line:
        k,v=line.split('=',1)
        os.environ[k.strip()]=v.strip()
from src.modules.labeler.db import supabase
from pathlib import Path

print("=== SUPABASE PRODUCTION CROPS CHECK ===\n")

# 1. Crops table counts
print("--- Crops table (DB rows) ---")
total = supabase.table('crops').select('crop_id', count='exact').execute().count or 0
print(f"Total rows: {total}")

for vuelta in ['primera', 'segunda', None]:
    if vuelta:
        cnt = supabase.table('crops').select('crop_id', count='exact').eq('vuelta', vuelta).execute().count or 0
        print(f"  vuelta={vuelta}: {cnt}")
    else:
        cnt = supabase.table('crops').select('crop_id', count='exact').is_('vuelta', 'null').execute().count or 0
        if cnt:
            print(f"  sin vuelta (null): {cnt}")

# 2. storage_url populated
print("\n--- Storage URLs populated ---")
with_url = supabase.table('crops').select('crop_id', count='exact').not_.is_('storage_url', 'null').execute().count or 0
print(f"With storage_url: {with_url} / {total} ({100*with_url/total:.1f}% if total>0)" if total else "N/A")

for vuelta in ['primera', 'segunda']:
    w = supabase.table('crops').select('crop_id', count='exact').eq('vuelta', vuelta).not_.is_('storage_url', 'null').execute().count or 0
    t = supabase.table('crops').select('crop_id', count='exact').eq('vuelta', vuelta).execute().count or 0
    print(f"  {vuelta} with url: {w} / {t}")

# 3. Try to sample storage objects count (note: list() is limited, usually 100 by default)
print("\n--- Storage bucket sample ---")
try:
    objs = supabase.storage.from_('crops').list()
    print(f"Storage list() returned: {len(objs)} objects (this is likely only 1st page, max ~100-1000)")
    if objs:
        print(f"  Sample names: {[o.get('name','') for o in objs[:5]]}...")
except Exception as e:
    print(f"  Storage list error: {e}")

# 4. Local comparison for main dirs
print("\n--- LOCAL CROPS ---")
labels_root = Path('data/labels')
sources = [
    ('data/labels/crops', 'primera (main)'),
    ('data/labels_v2/crops', 'primera (v2)'),
    ('data/labels_segunda/crops', 'segunda'),
]

for dpath, label in sources:
    d = Path(dpath)
    if not d.exists():
        print(f"{label}: dir not found")
        continue
    pngs = [p for p in d.iterdir() if p.suffix == '.png']
    idx = d / 'index.jsonl'
    idx_lines = 0
    if idx.exists():
        with open(idx, encoding='utf-8') as fh:
            idx_lines = sum(1 for ln in fh if ln.strip())
    print(f"{label}: {len(pngs)} PNGs, index.jsonl={idx_lines} entries")

print("\n--- RECOMMENDATION ---")
print("Compare the DB total + 'with url' numbers vs the local PNG counts above.")
print("If you want a full audit (set diff), run: python scripts/audit_cloud_to_local.py")
print("Note: audit also checks labels and does local-vs-cloud set comparison.")
