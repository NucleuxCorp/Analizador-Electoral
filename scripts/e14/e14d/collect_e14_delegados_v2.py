"""
Collect E-14 Delegados PDF URLs from the segunda vuelta Angular SPA.

Domain: https://e14segundavueltapresidente.registraduria.gov.co

Usage:
    python collect_e14_delegados_v2.py                          # all depts
    python collect_e14_delegados_v2.py --dept AMAZONAS          # single dept
    python collect_e14_delegados_v2.py --visible                 # show browser
    python collect_e14_delegados_v2.py --dept AMAZONAS --visible
"""
import asyncio, json, os, sys, re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from playwright.async_api import async_playwright, Page, Browser, BrowserContext, Request, TimeoutError as PWTimeout

# ── Config ──
BASE_URL = "https://e14segundavueltapresidente.registraduria.gov.co"
OUTPUT = REPO / "data" / "urls" / "e14_delegados_v2_urls.jsonl"
OUTPUT.parent.mkdir(parents=True, exist_ok=True)

# Reuse selectors from the existing form_handler
SEL_DEPT_LINKS   = 'a[href^="/departamento/"]'
SEL_MPIO_INPUT   = 'input.custom-input[placeholder*="municipio"]'
SEL_ZONA_INPUT   = 'input.custom-input[placeholder*="zona"]'
SEL_PUESTO_INPUT = 'input.custom-input[placeholder*="puesto"]'
SEL_DROPDOWN     = 'div.dropdown-list'
SEL_DROPDOWN_LI  = 'div.dropdown-list li p'
SEL_CONSULTAR    = 'button.custom-button'
SEL_MESA_BTN     = 'div.open-pdf'
SEL_NEXT_PAGE    = '.mat-paginator-navigation-next, button[aria-label*="Next"], button[aria-label*="Siguiente"], li.next > a'

_FIELD_CONTAINERS = {
    'municipio': 'app-custom-select:has(input[placeholder*="municipio"])',
    'zona':      'app-custom-select:has(input[placeholder*="zona"])',
    'puesto':    'app-custom-select:has(input[placeholder*="puesto"])',
}

_OPTION_RE = re.compile(r'^(\d+)\s*[—\-–]\s*(.+?)\s*\((\d+)%?\)\s*$')
_PERCENT_RE = re.compile(r'\((\d+)%?\)')

_PDF_KEYWORDS = ["pdf", "acta", "e14", "temis", "download"]


def parse_option(text: str) -> Optional[dict]:
    text = text.strip()
    if not text:
        return None
    m = _OPTION_RE.match(text)
    if m:
        return {"value": m.group(1), "label": m.group(2).strip(), "pct": int(m.group(3))}
    pct_m = _PERCENT_RE.search(text)
    pct = int(pct_m.group(1)) if pct_m else -1
    label = _PERCENT_RE.sub("", text).strip().strip("—-–").strip() or text
    return {"value": label, "label": label, "pct": pct}


def _looks_like_pdf(url: str) -> bool:
    if not url or url == "about:blank":
        return False
    low = url.lower()
    return any(kw in low for kw in _PDF_KEYWORDS)


# ── Helpers ──

async def select_option(page: Page, field: str, value: str) -> bool:
    """Click the input to open dropdown, then click the matching option."""
    container_sel = _FIELD_CONTAINERS[field]
    container = page.locator(container_sel)
    inp = container.locator("input")
    try:
        await inp.click(timeout=5000)
    except PWTimeout:
        return False
    await asyncio.sleep(0.8)

    # Iterate through all li items to find matching text
    items = container.locator(SEL_DROPDOWN_LI)
    count = await items.count()
    found = None
    for i in range(count):
        text = await items.nth(i).inner_text()
        # Match by value (code) anywhere in the text
        if value in text:
            found = items.nth(i)
            break
    if not found:
        return False
    try:
        await found.click(timeout=3000)
        await asyncio.sleep(0.5)
        return True
    except PWTimeout:
        return False


async def get_options(page: Page, field: str) -> list[dict]:
    """Open dropdown and return parsed options."""
    container_sel = _FIELD_CONTAINERS[field]
    container = page.locator(container_sel)
    inp = container.locator("input")
    try:
        await inp.click(timeout=5000)
    except PWTimeout:
        return []
    await asyncio.sleep(0.8)

    items = container.locator(SEL_DROPDOWN_LI)
    count = await items.count()
    results = []
    for i in range(count):
        text = await items.nth(i).inner_text()
        opt = parse_option(text)
        if opt:
            results.append(opt)
    # Close dropdown by clicking elsewhere
    await page.locator("body").click(position={"x": 5, "y": 5})
    await asyncio.sleep(0.3)
    return results


async def get_departments(page: Page) -> list[dict]:
    """Scrape home page or department listing for department links."""
    await page.goto(f"{BASE_URL}/home", wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(2)

    links = page.locator(SEL_DEPT_LINKS)
    count = await links.count()
    if count:
        depts = []
        for i in range(count):
            href = await links.nth(i).get_attribute("href")
            text = await links.nth(i).inner_text()
            depts.append({"id": href.split("/")[-1], "name": text.strip()})
        return depts

    # Fallback: load from departamentos.json
    dept_file = REPO / "data" / "departamentos.json"
    if dept_file.exists():
        raw = json.loads(dept_file.read_text(encoding="utf-8"))
        return [{"id": d["id"], "name": d["nombre"]} for d in raw]
    return []


async def go_next_page(page: Page) -> bool:
    try:
        nxt = page.locator(SEL_NEXT_PAGE)
        if await nxt.count() == 0:
            return False
        is_disabled = await nxt.get_attribute("disabled")
        if is_disabled:
            return False
        cls = await nxt.get_attribute("class") or ""
        if "disabled" in cls:
            return False
        await nxt.click(timeout=3000)
        await asyncio.sleep(1.5)
        return True
    except PWTimeout:
        return False


async def get_mesa_buttons(page: Page) -> list:
    return await page.locator(SEL_MESA_BTN).all()


def append_url(record: dict) -> None:
    with open(OUTPUT, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ── Main ──

async def collect(target_dept: str = "", headless: bool = True):
    total = 0

    async with async_playwright() as pw:
        browser: Browser = await pw.chromium.launch(headless=headless)
        context: BrowserContext = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
        )
        # Install window.open hook
        await context.add_init_script("""
        (function() {
            if (window.__pdfCapture) return;
            window.__pdfCapture = { urls: [] };
            const _open = window.open.bind(window);
            window.open = function(url) {
                if (url) window.__pdfCapture.urls.push(String(url));
                return _open.apply(this, arguments);
            };
        })();
        """)
        page: Page = await context.new_page()
        page.set_default_timeout(30000)

        depts = await get_departments(page)
        print(f"Departments: {len(depts)}")

        for dept in depts:
            if target_dept and dept["name"].upper() != target_dept.upper():
                continue
            print(f"\n{'='*60}")
            print(f"DEPT: {dept['name']} (id={dept['id']})")
            print(f"{'='*60}")

            await page.goto(f"{BASE_URL}/departamento/{dept['id']}", wait_until="domcontentloaded")
            await asyncio.sleep(2)

            # Municipios
            municipios = await get_options(page, "municipio")
            active = [m for m in municipios if m["pct"] > 0]
            print(f"  Municipios: {len(municipios)} ({len(active)} active)")

            for mpio in (active if len(active) > 0 else municipios):
                print(f"\n  MPIO: {mpio['label']} ({mpio['pct']}%)")
                await page.goto(f"{BASE_URL}/departamento/{dept['id']}", wait_until="domcontentloaded")
                await asyncio.sleep(1.5)

                ok = await select_option(page, "municipio", mpio["value"])
                if not ok:
                    print(f"    Failed to select municipio {mpio['value']}")
                    continue
                await asyncio.sleep(1)

                zonas = await get_options(page, "zona")
                if not zonas:
                    zonas = [{"value": "0", "label": "UNICA", "pct": 100}]

                for zona in zonas:
                    if zona["value"] != "0":
                        ok = await select_option(page, "zona", zona["value"])
                        if not ok:
                            continue
                        await asyncio.sleep(0.8)

                    puestos = await get_options(page, "puesto")

                    for puesto in puestos:
                        if puesto["pct"] == 0:
                            print(f"    [0%] {puesto['label']} — skip")
                            continue

                        print(f"    PUESTO: {puesto['label']} ({puesto['pct']}%)")
                        ok = await select_option(page, "puesto", puesto["value"])
                        if not ok:
                            print(f"      Failed to select puesto")
                            continue
                        await asyncio.sleep(0.5)

                        # Click Consultar
                        btn = page.locator(SEL_CONSULTAR)
                        if await btn.count() == 0:
                            continue
                        await btn.click()
                        await asyncio.sleep(2)

                        # Paginate
                        page_num = 1
                        while True:
                            buttons = await get_mesa_buttons(page)
                            print(f"      page {page_num}: {len(buttons)} mesas")

                            for i, btn_mesa in enumerate(buttons):
                                # Get mesa number from inner text
                                mesa_text = (await btn_mesa.inner_text()).strip()
                                mesa_num = mesa_text.split()[-1] if mesa_text else str(i + 1)

                                # Capture PDF URL
                                pdf_url = None
                                captured = []
                                request_seen = asyncio.Event()

                                def _on_req(req: Request):
                                    url = req.url
                                    if "/temis/pdf" in url or ".pdf" in url:
                                        captured.append(url)
                                        request_seen.set()

                                # Attach listener BEFORE click
                                page.on("request", _on_req)

                                # Strategy 1: popup
                                try:
                                    async with context.expect_page(timeout=3000) as popup_info:
                                        await btn_mesa.click()
                                    popup = await popup_info.value
                                    try:
                                        await popup.wait_for_load_state("domcontentloaded", timeout=4000)
                                    except PWTimeout:
                                        pass
                                    popup_url = popup.url
                                    if "/temis/pdf" in popup_url or ".pdf" in popup_url:
                                        pdf_url = popup_url
                                    await popup.close()
                                except PWTimeout:
                                    pass

                                # Strategy 2: network request
                                if not pdf_url:
                                    try:
                                        await asyncio.wait_for(request_seen.wait(), timeout=6)
                                        if captured:
                                            pdf_url = captured[-1]
                                    except asyncio.TimeoutError:
                                        pass

                                # Strategy 3: window.open hook
                                if not pdf_url:
                                    try:
                                        hook_urls = await page.evaluate("() => window.__pdfCapture ? [...window.__pdfCapture.urls] : []")
                                        candidates = [u for u in hook_urls if "/temis/pdf" in u or ".pdf" in u]
                                        pdf_url = candidates[-1] if candidates else None
                                    except Exception:
                                        pass

                                page.remove_listener("request", _on_req)
                                try:
                                    await page.evaluate("() => { if (window.__pdfCapture) window.__pdfCapture.urls = []; }")
                                except Exception:
                                    pass

                                if pdf_url:
                                    record = {
                                        "departamento": dept["name"], "cod_dept": dept["id"],
                                        "municipio": mpio["label"], "cod_mpio": mpio["value"],
                                        "zona": zona["label"], "puesto": puesto["label"],
                                        "mesa": mesa_num,
                                        "pdf_url": pdf_url,
                                        "scraped_at": datetime.now(timezone.utc).isoformat(),
                                    }
                                    append_url(record)
                                    total += 1
                                    if total % 50 == 0:
                                        print(f"        [{total} URLs collected]")

                            has_next = await go_next_page(page)
                            if not has_next:
                                break
                            page_num += 1

        await browser.close()

    print(f"\n{'='*60}")
    print(f"DONE — {total} URLs saved to {OUTPUT}")
    print(f"{'='*60}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Collect E-14 Delegados v2 PDF URLs")
    parser.add_argument("--dept", default="", help="Department name to process (e.g. AMAZONAS)")
    parser.add_argument("--visible", action="store_true", help="Show browser (not headless)")
    args = parser.parse_args()
    asyncio.run(collect(target_dept=args.dept, headless=not args.visible))
