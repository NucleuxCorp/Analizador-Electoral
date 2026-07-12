import os
import json
from pathlib import Path

os.chdir(r'D:\Nucleux\tools\Analizador de Elecciones')
for line in open('.env').readlines():
    line = line.strip()
    if not line or line.startswith('#'): continue
    if '=' in line:
        k, v = line.split('=', 1)
        os.environ[k.strip()] = v.strip()

from src.modules.labeler.db import supabase

print('Fetching cloud segunda crop_ids (paginated)...')
cloud = set()
start = 0
page = 1000
while True:
    resp = supabase.table('crops').select('crop_id').eq('vuelta', 'segunda').range(start, start + page - 1).execute()
    rows = resp.data or []
    for r in rows:
        cid = r.get('crop_id')
        if cid:
            cloud.add(cid)
    if len(rows) < page:
        break
    start += page
print('Cloud segunda crop_ids:', len(cloud))

# Local index
local = set()
idx = Path('data/labels_segunda/crops/index.jsonl')
if idx.exists():
    for ln in open(idx, encoding='utf-8'):
        ln = ln.strip()
        if ln:
            try:
                cid = json.loads(ln).get('crop_id')
                if cid:
                    local.add(cid)
            except:
                pass
print('Local segunda index crop_ids:', len(local))

missing_in_cloud = sorted(local - cloud)
missing_in_local = sorted(cloud - local)

print()
print('=== DIFF ===')
print('Local index entries not in cloud (missing in prod):', len(missing_in_cloud))
if missing_in_cloud:
    print('  First few:', missing_in_cloud[:8])

print('Cloud entries not in local index:', len(missing_in_local))
if missing_in_local:
    print('  First few:', missing_in_local[:8])

print()
print('=== Storage URL check for cloud crops ===')
missing_url = 0
for i, cid in enumerate(list(cloud)[:min(20, len(cloud))]):
    try:
        r = supabase.table('crops').select('storage_url').eq('crop_id', cid).single().execute()
        url = (r.data or {}).get('storage_url') if r.data else None
        if not url:
            missing_url += 1
            if missing_url <= 3:
                print(f'  NO URL: {cid}')
    except Exception as e:
        print(f'  ERROR for {cid}: {e}')

print(f'Sampled {min(20, len(cloud))} cloud crops — missing storage_url in sample: {missing_url}')

print()
print('Conclusion for segunda:')
print(f'  Local index: {len(local)}')
print(f'  In production DB: {len(cloud)}')
if len(missing_in_cloud) == 0 and len(missing_in_local) <= 5:
    print('  -> Looks like nearly all (or all intended) segunda crops are uploaded.')
else:
    print('  -> There is a small mismatch. See numbers above.')
