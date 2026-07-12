import requests, os
from dotenv import load_dotenv
from collections import Counter

load_dotenv('.env.primera')
url = os.getenv('SUPABASE_URL')
key = os.getenv('SUPABASE_ANON_KEY') or os.getenv('SUPABASE_KEY')

r = requests.get(
    f'{url}/rest/v1/labels?select=digit&limit=10000',
    headers={'apikey': key, 'Authorization': f'Bearer {key}'},
    timeout=15
)
data = r.json()
print(f'Total: {len(data)}')
c = Counter(row['digit'] for row in data)
for k, v in sorted(c.items(), key=lambda x: str(x[0])):
    print(f'  {k}: {v}')
