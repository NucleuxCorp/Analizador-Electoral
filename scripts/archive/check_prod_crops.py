import os; os.chdir(r'D:\Nucleux\tools\Analizador de Elecciones')
for line in open('.env').readlines():
    line=line.strip()
    if not line or line.startswith('#'): continue
    if '=' in line:
        k,v=line.split('=',1)
        os.environ[k.strip()]=v.strip()
from src.modules.labeler.db import supabase

r = supabase.table('crops').select('crop_id,created_at').order('created_at', desc=True).limit(5).execute()
print('Ultimos 5 crops en produccion:')
for row in (r.data or []):
    print(' ', row.get('crop_id','?')[:20], row.get('created_at','?'))

r2 = supabase.table('crops').select('crop_id', count='exact').limit(1).execute()
print('Total crops en Supabase:', r2.count)
