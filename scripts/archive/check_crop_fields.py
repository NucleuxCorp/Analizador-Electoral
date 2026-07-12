import os; os.chdir(r'D:\Nucleux\tools\Analizador de Elecciones')
for line in open('.env').readlines():
    line=line.strip()
    if not line or line.startswith('#'): continue
    if '=' in line: k,v=line.split('=',1); os.environ[k.strip()]=v.strip()
from src.modules.labeler.db import supabase

r = supabase.table('crops').select('crop_id,field_name,digit_index,storage_url,full_cell_crop_id,status,label_ocr').limit(3).execute()
for row in (r.data or []):
    print('---')
    for k in ['crop_id','field_name','digit_index','label_ocr','storage_url','full_cell_crop_id','status']:
        v = row.get(k,'?')
        if k == 'storage_url': v = str(v)[:80]
        if k == 'full_cell_crop_id': v = str(v)[:50]
        print('  %s: %s' % (k, v))
