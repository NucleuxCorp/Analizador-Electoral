"""
Diagnostic for E-14C (escrutinio oficial) — captures all XHR calls and
finds puestos that already have digitalizado > 0 / nombre_archivo set.
"""
import asyncio, json, ssl, urllib.request
from playwright.async_api import async_playwright, Request, Response

BASE = "https://escrutiniospresidente2026.registraduria.gov.co"
TIMESTAMP = "20260526_141209_555"   # discovered timestamp — may change

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

captured_json: list[dict] = []


def fetch_json(url: str):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
            body = r.read()
            if body.lstrip().startswith(b"[") or body.lstrip().startswith(b"{"):
                return json.loads(body)
    except Exception as e:
        print(f"  FETCH ERR {url}: {e}")
    return None


def mesas_url(dept, mpio, zona, puesto):
    ts = TIMESTAMP
    return (
        f"{BASE}/data/esc/v1/actas-documentos/001/{dept}/{mpio}/{zona}/{puesto}/mesas/"
        f"actas_documentos_001_{dept}_{mpio}_{zona}_{puesto}_mesas_{ts}.json"
    )


async def main():
    # -----------------------------------------------------------------------
    # Step 1: Navigate the E-14C app and capture all XHR requests
    # -----------------------------------------------------------------------
    print(f"\n[1] Loading {BASE}/actas-e14 — capturing XHR calls...")
    xhr_calls: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        page = await browser.new_page()

        def on_response(response: Response):
            url = response.url
            ct = response.headers.get("content-type", "")
            if "json" in ct and "/data/" in url:
                xhr_calls.append({"url": url, "status": response.status})

        page.on("response", on_response)

        await page.goto(f"{BASE}/actas-e14", wait_until="domcontentloaded")
        await asyncio.sleep(4)

        print(f"  Page title: {await page.title()}")
        print(f"  URL: {page.url}")
        await page.screenshot(path="data/debug_e14c_home.png")

        # Inspect page elements
        elements = await page.evaluate("""
            () => {
                const res = [];
                ['select','mat-select','app-custom-select','ng-select','input'].forEach(tag => {
                    document.querySelectorAll(tag).forEach(el => {
                        const ph = el.getAttribute('placeholder') || '';
                        const fc = el.getAttribute('formcontrolname') || '';
                        res.push({ tag, id: el.id, placeholder: ph, formcontrolname: fc,
                                   class: (el.className||'').toString().substring(0,60) });
                    });
                });
                return res;
            }
        """)
        print(f"\n  Form elements ({len(elements)}):")
        for e in elements:
            print(f"    <{e['tag']}> id={e['id']} ph='{e['placeholder']}' fc='{e['formcontrolname']}' cls={e['class'][:40]}")

        # Save page HTML for inspection
        html = await page.evaluate("document.body.innerHTML.substring(0, 5000)")
        with open("data/debug_e14c_html.txt", "w", encoding="utf-8") as f:
            f.write(html)

        print(f"\n  XHR calls on load ({len(xhr_calls)}):")
        for x in xhr_calls:
            print(f"    [{x['status']}] {x['url']}")
        xhr_calls.clear()

        await browser.close()

    # -----------------------------------------------------------------------
    # Step 2: Brute-force find a puesto with digitalizado > 0
    # Test known depts from departamentos.json — try small zone/puesto ranges
    # -----------------------------------------------------------------------
    import json as _json
    depts = _json.loads(open("data/departamentos.json", encoding="utf-8").read())

    print(f"\n[2] Scanning for puestos with digitalizado > 0...")
    found_digital = []

    for dept in depts[:10]:  # first 10 depts
        d = dept["id"]
        for mpio in ["001", "002", "003"]:
            for zona in ["01", "02", "03", "04", "05"]:
                for puesto in ["01", "02", "03", "04", "05"]:
                    url = mesas_url(d, mpio, zona, puesto)
                    data = fetch_json(url)
                    if data and isinstance(data, list):
                        total = len(data)
                        digital = [m for m in data if m.get("digitalizado", 0) > 0]
                        con_archivo = [m for m in data if m.get("nombre_archivo", "")]
                        if digital or con_archivo:
                            print(f"  FOUND! dept={d} mpio={mpio} zona={zona} puesto={puesto} — "
                                  f"{len(digital)} digitalizados, {len(con_archivo)} con archivo")
                            print(f"  Sample: {json.dumps(data[0], ensure_ascii=False)}")
                            if con_archivo:
                                print(f"  nombre_archivo: {con_archivo[0]['nombre_archivo']}")
                            found_digital.append({"dept":d,"mpio":mpio,"zona":zona,"puesto":puesto,"url":url})
                        else:
                            print(f"  dept={d} mpio={mpio} zona={zona} puesto={puesto} — {total} mesas, todos digitalizado=0")

    if not found_digital:
        print("\n  No puestos con digitalizado > 0 encontrados en el rango explorado.")
        print("  El timestamp podría haber cambiado o los PDFs aún no están subidos para estos dptos.")

    # -----------------------------------------------------------------------
    # Step 3: Check if there's a newer timestamp by trying variations
    # -----------------------------------------------------------------------
    print("\n[3] Checking for newer timestamps on a known valid puesto...")
    known_dept, known_mpio, known_zona, known_puesto = "05", "001", "21", "04"
    for ts_variant in [
        "20260527_000000_000",
        "20260528_000000_000",
        "20260529_000000_000",
        "20260530_000000_000",
        "20260531_000000_000",
    ]:
        url = (f"{BASE}/data/esc/v1/actas-documentos/001/{known_dept}/{known_mpio}/"
               f"{known_zona}/{known_puesto}/mesas/"
               f"actas_documentos_001_{known_dept}_{known_mpio}_{known_zona}_{known_puesto}_mesas_{ts_variant}.json")
        data = fetch_json(url)
        if data and isinstance(data, list):
            digital = sum(1 for m in data if m.get("digitalizado", 0) > 0)
            print(f"  VALID timestamp: {ts_variant} — {len(data)} mesas, {digital} digitalizados")
        else:
            print(f"  {ts_variant}: not found")


asyncio.run(main())
