"""Download E14T PDFs using Playwright with batches of 3 concurrent fetches."""
import json, sys, time, base64
from pathlib import Path
from playwright.sync_api import sync_playwright

CODES = Path('debug_sv/allTransmissionCodes.json')
OUT = Path('E:/e14_segunda/E14T')
BASE = 'https://e14segundavueltapresidentet.registraduria.gov.co'
BATCH = 3  # concurrent fetches per batch

def build_url(n):
    dept=n['idDepartmentCode']; mpio=n['municipalityCode'].zfill(3)
    zone=n['idZoneCode'].zfill(3); stand=n['standCode'].zfill(2)
    mesa=n['numberStand'].zfill(3); h=n['expectedName']
    return f'{BASE}/assets/temis/pdf/{dept}/{mpio}/{zone}/{stand}/{mesa}/PRE/{h}'

data = json.loads(CODES.read_text(encoding='utf-8'))
nodes = data['data']['status11']['nodes']
total = len(nodes)
OUT.mkdir(parents=True, exist_ok=True)

JS_FETCH = """
async (urls) => {
    const results = await Promise.all(urls.map(async (url) => {
        try {
            const resp = await fetch(url);
            if (!resp.ok) return {ok: false, error: 'status ' + resp.status};
            const blob = await resp.blob();
            const reader = new FileReader();
            const b64 = await new Promise((resolve) => {
                reader.onload = () => resolve(reader.result.split(',')[1]);
                reader.readAsDataURL(blob);
            });
            return {ok: true, data: b64, size: blob.size};
        } catch (e) {
            return {ok: false, error: e.message};
        }
    }));
    return results;
}
"""

print(f"Descargando {total} PDFs en lotes de {BATCH}...")
print(f"INICIO: {time.strftime('%H:%M:%S')}", flush=True)

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        user_data_dir='C:/Users/Administrador/AppData/Local/Google/Chrome/User Data',
        headless=False, no_viewport=True
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(f'{BASE}/home', wait_until='domcontentloaded', timeout=15000)
    page.wait_for_timeout(1000)

    ok = fail = 0
    t0 = time.time()
    batch_urls = []

    for i, n in enumerate(nodes):
        url = build_url(n)
        name = n['expectedName']
        dest = OUT / name
        if dest.exists():
            ok += 1
            continue
        batch_urls.append((url, name, dest))

        if len(batch_urls) >= BATCH or i == total - 1:
            if not batch_urls:
                continue
            # Process this batch
            urls = [b[0] for b in batch_urls]
            try:
                results = page.evaluate(JS_FETCH, urls)
                for j, (url, name, dest) in enumerate(batch_urls):
                    r = results[j] if j < len(results) else None
                    if r and r.get('ok') and r.get('size', 0) > 100:
                        dest.write_bytes(base64.b64decode(r['data']))
                        ok += 1
                    else:
                        fail += 1
            except Exception as e:
                fail += len(batch_urls)

            batch_urls = []

            if (ok + fail) % 200 == 0:
                elapsed = time.time()-t0; rate = (ok+fail)/elapsed
                print(f"  [{ok+fail}/{total}] OK={ok} FAIL={fail}  {elapsed:.0f}s  {rate:.1f}pdf/s", flush=True)

    elapsed = time.time()-t0
    print(f"\nCOMPLETADO: {time.strftime('%H:%M:%S')}  {elapsed:.0f}s")
    print(f"OK={ok} FAIL={fail}")
    ctx.close()
