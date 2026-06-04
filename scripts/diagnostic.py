"""
Diagnostic script — inspects the real DOM structure after clicking a department.
Run: python diagnostic.py
"""
import asyncio
import json
from playwright.async_api import async_playwright

BASE_URL = "https://divulgacione14presidente.registraduria.gov.co/home"

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        page = await browser.new_page()

        print(f"[1] Navigating to {BASE_URL}")
        await page.goto(BASE_URL, wait_until="networkidle")
        await asyncio.sleep(2)

        print(f"[2] Page title: {await page.title()}")
        print(f"    URL: {page.url}")

        # Get department links/rows in the table
        rows = await page.evaluate("""
            () => {
                // Look for clickable department names
                const candidates = [
                    ...document.querySelectorAll('table td a, table td span, table td'),
                    ...document.querySelectorAll('.mat-cell, .mat-row td'),
                    ...document.querySelectorAll('[class*="department"], [class*="depto"]'),
                ];
                return candidates
                    .filter(el => el.textContent.trim().length > 2 && el.textContent.trim().length < 50)
                    .slice(0, 20)
                    .map(el => ({
                        tag: el.tagName,
                        text: el.textContent.trim(),
                        class: (el.className || '').toString().substring(0, 80),
                        href: el.getAttribute('href') || '',
                        clickable: !!(el.onclick || el.getAttribute('href') ||
                                      el.closest('a') || el.closest('[routerlink]') ||
                                      el.getAttribute('routerlink'))
                    }));
            }
        """)
        print("\n[3] Department rows found:")
        for r in rows:
            print(f"    <{r['tag']}> text='{r['text']}' class='{r['class'][:60]}' href='{r['href']}'")

        # Find AMAZONAS specifically and click it
        print("\n[4] Looking for AMAZONAS link...")
        amazonas = page.locator("text=AMAZONAS").first
        count = await amazonas.count()
        print(f"    Found {count} element(s) with text AMAZONAS")

        if count > 0:
            el_info = await amazonas.evaluate("""
                el => ({
                    tag: el.tagName,
                    class: el.className.toString().substring(0, 100),
                    parent_tag: el.parentElement?.tagName,
                    parent_class: (el.parentElement?.className || '').toString().substring(0, 100),
                    href: el.getAttribute('href') || el.closest('a')?.href || '',
                    routerlink: el.getAttribute('routerlink') || el.closest('[routerlink]')?.getAttribute('routerlink') || ''
                })
            """)
            print(f"    Element: {json.dumps(el_info, indent=4)}")

            print("\n[5] Clicking AMAZONAS...")
            await amazonas.click()
            await asyncio.sleep(3)
            # Angular router — wait for URL change instead of networkidle
            try:
                await page.wait_for_url("**/departamento/**", timeout=10000)
            except Exception:
                await asyncio.sleep(2)

            print(f"    New URL: {page.url}")
            print(f"    New title: {await page.title()}")
            await page.screenshot(path="data/debug_after_amazonas_click.png")
            print("    Screenshot saved: data/debug_after_amazonas_click.png")

            # Now inspect the form
            print("\n[6] Form elements after navigation:")
            all_elements = await page.evaluate("""
                () => {
                    const result = [];
                    // All interactive elements
                    const tags = ['select', 'input', 'button', 'mat-select', 'mat-form-field',
                                  'ng-select', 'textarea', 'mat-radio-group'];
                    for (const tag of tags) {
                        document.querySelectorAll(tag).forEach((el, i) => {
                            result.push({
                                tag,
                                id: el.id,
                                name: el.getAttribute('name') || '',
                                formcontrolname: el.getAttribute('formcontrolname') || '',
                                class: (el.className || '').toString().substring(0, 80),
                                text: el.textContent.trim().substring(0, 60),
                                placeholder: el.getAttribute('placeholder') || '',
                            });
                        });
                    }
                    // Also check comboboxes
                    document.querySelectorAll('[role="combobox"], [role="listbox"]').forEach((el, i) => {
                        result.push({
                            tag: 'role=' + el.getAttribute('role'),
                            id: el.id,
                            name: el.getAttribute('name') || '',
                            formcontrolname: el.getAttribute('formcontrolname') || '',
                            class: (el.className || '').toString().substring(0, 80),
                            text: el.textContent.trim().substring(0, 60),
                            placeholder: el.getAttribute('placeholder') || '',
                        });
                    });
                    return result;
                }
            """)

            print(f"    Found {len(all_elements)} interactive elements:")
            for el in all_elements:
                print(f"      <{el['tag']}> id='{el['id']}' name='{el['name']}' "
                      f"formcontrolname='{el['formcontrolname']}' "
                      f"class='{el['class'][:50]}' text='{el['text'][:40]}'")

            # Get first 3000 chars of page HTML
            body_html = await page.evaluate("document.body.innerHTML.substring(0, 3000)")
            with open("data/debug_page_html.txt", "w", encoding="utf-8") as f:
                f.write(body_html)
            print("\n    Page HTML saved to data/debug_page_html.txt")

        print("\n[7] Done. Press Enter to close browser...")
        input()
        await browser.close()


asyncio.run(main())
