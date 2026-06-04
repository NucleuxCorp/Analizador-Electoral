"""
Diagnostic v2 — capture network requests and inspect the custom select dropdown.
Run: python diagnostic2.py
"""
import asyncio
import json
from playwright.async_api import async_playwright, Request

BASE = "https://divulgacione14presidente.registraduria.gov.co"

captured_requests: list[dict] = []

def on_request(req: Request):
    url = req.url
    # Skip static assets
    if any(ext in url for ext in ['.js', '.css', '.png', '.svg', '.ico', '.woff']):
        return
    captured_requests.append({
        "method": req.method,
        "url": url,
        "post_data": req.post_data,
    })

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        page = await browser.new_page()
        page.on("request", on_request)

        # Step 1: Home page — capture API calls for department list
        print("[1] Loading home page...")
        await page.goto(f"{BASE}/home", wait_until="domcontentloaded")
        await asyncio.sleep(3)

        print("    API calls on home page:")
        for r in captured_requests:
            print(f"      {r['method']} {r['url']}")
            if r['post_data']:
                print(f"           body: {r['post_data'][:200]}")
        captured_requests.clear()

        # Step 2: Navigate to AMAZONAS department page
        print("\n[2] Navigating to Amazonas (/departamento/60)...")
        await page.goto(f"{BASE}/departamento/60", wait_until="domcontentloaded")
        await asyncio.sleep(3)

        print("    API calls on department page:")
        for r in captured_requests:
            print(f"      {r['method']} {r['url']}")
            if r['post_data']:
                print(f"           body: {r['post_data'][:200]}")
        captured_requests.clear()

        # Step 3: Screenshot of the form
        await page.screenshot(path="data/debug_dept_form.png")
        print("\n    Screenshot: data/debug_dept_form.png")

        # Step 4: Find and click Municipio input
        print("\n[3] Clicking Municipio input...")
        inputs = await page.locator("input.custom-input").all()
        print(f"    Found {len(inputs)} custom-input elements:")
        for i, inp in enumerate(inputs):
            ph = await inp.get_attribute("placeholder") or ""
            val = await inp.input_value()
            print(f"      [{i}] placeholder='{ph}' value='{val}'")

        # Click the municipio input (placeholder contains "municipio" or similar)
        mpio_input = None
        for inp in inputs:
            ph = (await inp.get_attribute("placeholder") or "").lower()
            if "municipio" in ph or "mpio" in ph or "seleccione el m" in ph:
                mpio_input = inp
                break

        if not mpio_input and len(inputs) >= 3:
            # Fallback: index 2 (after "Buscar Departamento" and "PRESIDENTE")
            mpio_input = inputs[2]
            print("    Using fallback: inputs[2] as municipio")

        if mpio_input:
            await mpio_input.click()
            await asyncio.sleep(1.5)

            # Capture what appeared in the DOM
            dropdown_html = await page.evaluate("""
                () => {
                    // Look for any newly appeared dropdown-like element
                    const candidates = [
                        document.querySelector('.dropdown-menu'),
                        document.querySelector('.options-list'),
                        document.querySelector('.custom-options'),
                        document.querySelector('.ng-dropdown-panel'),
                        document.querySelector('[class*="option"]'),
                        document.querySelector('[class*="dropdown"]'),
                        document.querySelector('[class*="list"]'),
                        document.querySelector('ul.options'),
                        document.querySelector('.mat-autocomplete-panel'),
                    ].filter(Boolean);
                    if (candidates.length > 0) {
                        return candidates[0].outerHTML.substring(0, 2000);
                    }
                    // Broader search: any visible list
                    const allDivs = Array.from(document.querySelectorAll('div, ul'));
                    for (const d of allDivs) {
                        const style = window.getComputedStyle(d);
                        if (style.display !== 'none' && style.visibility !== 'hidden') {
                            const children = d.children;
                            if (children.length > 3 && children.length < 100) {
                                const texts = Array.from(children).map(c => c.textContent.trim()).filter(Boolean);
                                if (texts.length > 2) {
                                    return d.outerHTML.substring(0, 2000);
                                }
                            }
                        }
                    }
                    return 'NO DROPDOWN FOUND';
                }
            """)
            print(f"\n[4] Dropdown HTML after clicking municipio:")
            print(f"    {dropdown_html[:1000]}")

            # Also save full page HTML
            full_html = await page.evaluate("document.body.innerHTML.substring(0, 10000)")
            with open("data/debug_dropdown_html.txt", "w", encoding="utf-8") as f:
                f.write(full_html)
            print("    Full HTML saved to data/debug_dropdown_html.txt")

            await page.screenshot(path="data/debug_dropdown_open.png")
            print("    Screenshot: data/debug_dropdown_open.png")

            # Capture API calls triggered by clicking the input
            await asyncio.sleep(2)
            print(f"\n    API calls after clicking municipio input:")
            for r in captured_requests:
                print(f"      {r['method']} {r['url']}")
                if r['post_data']:
                    print(f"           body: {r['post_data'][:300]}")

        print("\n[5] Done.")
        await browser.close()

asyncio.run(main())
