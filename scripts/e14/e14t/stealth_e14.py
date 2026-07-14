"""Download E14T with stealth Playwright (avoids Cloudflare detection)."""
import sys, json, time, random, base64
from pathlib import Path
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

CODES = Path('debug_sv/allTransmissionCodes.json')
OUT = Path('E:/e14_segunda/E14T')
BASE = 'https://e14segundavueltapresidentet.registraduria.gov.co'
DELAY = (0.5, 1)

data = json.loads(CODES.read_text(encoding='utf-8'))
nodes = data['data']['status11']['nodes']
OUT.mkdir(parents=True, exist_ok=True)

def build_url(n):
    dept=n['idDepartmentCode']; mpio=n['municipalityCode'].zfill(3)
    zone=n['idZoneCode'].zfill(3); stand=n['standCode'].zfill(2)
    mesa=n['numberStand'].zfill(3); h=n['expectedName']
    return f'{BASE}/assets/temis/pdf/{dept}/{mpio}/{zone}/{stand}/{mesa}/PRE/{h}'

print(f'E14T stealth download: {len(nodes)} PDFs')
print(f'Delay: {DELAY}s  Destino: {OUT}')
print(f'INICIO: {time.strftime("%H:%M:%S")}', flush=True)

with sync_playwright() as p:
    browser = p.chromium.launch_persistent_context(
        user_data_dir='C:/Users/Administrador/AppData/Local/Temp/e14t_chrome_profile',
        headless=False, no_viewport=True,
        args=['--disable-blink-features=AutomationControlled']
    )
    page = browser.pages[0] if browser.pages else browser.new_page()
    Stealth().apply_stealth_sync(page)
    page.goto(f'{BASE}/home', wait_until='domcontentloaded', timeout=20000)
    page.wait_for_timeout(2000)
    
    # Second tab
    page2 = browser.new_page()
    Stealth().apply_stealth_sync(page2)
    page2.goto(f'{BASE}/home', wait_until='domcontentloaded', timeout=20000)
    page2.wait_for_timeout(2000)

    ok = skip = fail = 0; t0 = time.time(); pages = [page, page2]

    for idx, n in enumerate(nodes):
        url = build_url(n); name = n['expectedName']; dest = OUT / name
        if dest.exists():
            skip += 1
            if (ok+skip+fail) % 500 == 0:
                print(f'  OK={ok} SKIP={skip} FAIL={fail}', flush=True)
            continue

        pg = pages[idx % 2]
        try:
            result = pg.evaluate('async(u)=>{const r=await fetch(u,{credentials:"include"});const b=await r.blob();const fr=new FileReader();return await new Promise(r2=>{fr.onload=()=>r2(fr.result);fr.readAsDataURL(b)})}', url)
            if result and 'data:' in result:
                d = base64.b64decode(result.split(',')[1])
                if d[:4] == b'%PDF' and len(d) > 20000:
                    dest.write_bytes(d); ok += 1
                else:
                    fail += 1
            else: fail += 1
        except:
            fail += 1

        # If 10+ consecutive failures -> Cloudflare blocked -> reload
        if ok > 0 and fail > ok * 3 and fail >= 10:
            print(f'  Posible bloqueo ({fail} fails). Recargando pagina en 60s...', flush=True)
            time.sleep(60)
            try:
                page.goto(f'{BASE}/home', wait_until='domcontentloaded', timeout=15000)
                page.wait_for_timeout(3000)
            except: pass
            fail = 0  # reset counter

        time.sleep(random.uniform(*DELAY))

        if (ok+skip+fail) % 100 == 0:
            elapsed = time.time()-t0
            rate = (ok+skip+fail)/elapsed if elapsed > 0 else 0
            print(f'  OK={ok} SKIP={skip} FAIL={fail}  {elapsed:.0f}s  {rate:.1f}pdf/s', flush=True)

    elapsed = time.time()-t0
    print(f'\nCOMPLETADO: {elapsed:.0f}s')
    print(f'OK={ok} SKIP={skip} FAIL={fail}')
    browser.close()
