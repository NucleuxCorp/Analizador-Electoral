"""
Handles interaction with the Angular E-14 form.

Real site structure (confirmed via DOM inspection):
- Home page (/home): table with department links — <a href="/departamento/{id}">
- Department page (/departamento/{id}): form with 5 <input class="custom-input">
    [0] placeholder="Buscar Departamento"  <- sidebar, ignore
    [1] placeholder="PRESIDENTE"           <- pre-filled corporacion, ignore
    [2] placeholder="seleccione el municipio"
    [3] placeholder="seleccione la zona"
    [4] placeholder="seleccione el puesto"
- Clicking an input opens <div class="dropdown-list"><ul><li><p> items
- Option format: "001 — LETICIA (100%)" (code + dash + name + percentage)
- Submit: <button class="custom-button">Consultar</button>
- Results: <div class="open-pdf"> per mesa
"""
import asyncio
import re
from typing import List, Optional

from playwright.async_api import Page, Locator, TimeoutError as PlaywrightTimeoutError

from src.modules.e14.models import FormOption
from src.utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Selectors (confirmed from DOM inspection)
# ---------------------------------------------------------------------------

SEL_DEPT_LINKS   = 'a[href^="/departamento/"]'
SEL_MPIO_INPUT   = 'input.custom-input[placeholder*="municipio"]'
SEL_ZONA_INPUT   = 'input.custom-input[placeholder*="zona"]'
SEL_PUESTO_INPUT = 'input.custom-input[placeholder*="puesto"]'
SEL_DROPDOWN     = 'div.dropdown-list'
SEL_DROPDOWN_LI  = 'div.dropdown-list li p'
SEL_CONSULTAR    = 'button.custom-button'
SEL_MESA_BTN     = 'div.open-pdf'
SEL_NEXT_PAGE    = (
    '.mat-paginator-navigation-next, '
    'button[aria-label*="Next"], '
    'button[aria-label*="Siguiente"], '
    'li.next > a'
)

_FIELD_SELECTORS = {
    'municipio': SEL_MPIO_INPUT,
    'zona':      SEL_ZONA_INPUT,
    'puesto':    SEL_PUESTO_INPUT,
}

# Scope dropdown item searches to the specific app-custom-select parent.
# This prevents mixing items from different dropdowns that are all in the DOM simultaneously.
_FIELD_CONTAINERS = {
    'municipio': 'app-custom-select:has(input[placeholder*="municipio"])',
    'zona':      'app-custom-select:has(input[placeholder*="zona"])',
    'puesto':    'app-custom-select:has(input[placeholder*="puesto"])',
}

# Regex to extract code from dropdown item text: "001 — LETICIA (100%)"
# "001 — LETICIA (85%)" — numeric code + dash + name + percentage
_OPTION_FULL_RE = re.compile(r'^(\d+)\s*[—\-–]\s*(.+?)\s*\((\d+)%?\)\s*$')
# Any "(NN%)" anywhere in the text
_PERCENT_RE = re.compile(r'\((\d+)%?\)')


def _parse_option_text(text: str) -> Optional[FormOption]:
    """
    Parse dropdown option text into a FormOption.

    Handles two formats:
      Full: "001 — LETICIA (85%)"  -> value="001", label="LETICIA", porcentaje=85
      Simple: "ZONA URBANA (100%)" -> value="ZONA URBANA", label="ZONA URBANA", porcentaje=100
      Bare: "1"                    -> value="1", label="1", porcentaje=-1 (unknown)
    """
    text = text.strip()
    if not text:
        return None

    # Try full format with numeric code prefix
    m = _OPTION_FULL_RE.match(text)
    if m:
        return FormOption(
            value=m.group(1),
            label=m.group(2).strip(),
            porcentaje=int(m.group(3)),
        )

    # Extract percentage if present anywhere
    pct_m = _PERCENT_RE.search(text)
    porcentaje = int(pct_m.group(1)) if pct_m else -1  # -1 = unknown

    # Strip the percentage part from the label
    label = _PERCENT_RE.sub("", text).strip().strip("—-–").strip()
    if not label:
        label = text

    return FormOption(value=label, label=label, porcentaje=porcentaje)


# ---------------------------------------------------------------------------
# Department navigation
# ---------------------------------------------------------------------------

async def get_departments_from_home(page: Page) -> List[FormOption]:
    """
    Read all department links from the home page.
    Returns FormOption(value=dept_id, label="AMAZONAS") etc.
    """
    try:
        await page.wait_for_selector(SEL_DEPT_LINKS, timeout=15_000)
    except PlaywrightTimeoutError:
        logger.error("Timeout waiting for department links on home page")
        await take_debug_screenshot(page, "no_dept_links")
        return []

    items: list[dict] = await page.evaluate(
        """
        () => Array.from(document.querySelectorAll('a[href^="/departamento/"]'))
            .map(a => ({
                value: a.getAttribute('href').split('/departamento/')[1],
                label: a.textContent.trim()
            }))
            .filter(o => o.value && o.label)
        """
    )
    depts = [FormOption(value=i["value"], label=i["label"]) for i in items]
    logger.info(f"Found {len(depts)} department(s) on home page")
    return depts


async def navigate_to_department(page: Page, dept_id: str, base_url: str) -> bool:
    """Navigate to /departamento/{dept_id} and wait for the form to be ready."""
    url = f"{base_url.rstrip('/')}/departamento/{dept_id}"
    try:
        await page.goto(url, wait_until="domcontentloaded")
        # Wait for at least one custom-input (form ready)
        await page.wait_for_selector("input.custom-input", timeout=15_000)
        await asyncio.sleep(0.8)  # let Angular finish rendering
        return True
    except Exception as e:
        logger.error(f"Error navigating to {url}: {e}")
        return False


# ---------------------------------------------------------------------------
# Custom select interaction
# ---------------------------------------------------------------------------

async def get_custom_options(page: Page, field: str) -> List[FormOption]:
    """
    Click a custom-input field, read the dropdown list items, then close.
    field: 'municipio' | 'zona' | 'puesto'
    """
    sel = _FIELD_SELECTORS.get(field)
    if not sel:
        logger.error(f"Unknown field: {field}")
        return []

    inp = page.locator(sel).first
    try:
        await inp.wait_for(state="visible", timeout=8_000)
    except PlaywrightTimeoutError:
        logger.warning(f"Field '{field}' not visible yet")
        return []

    try:
        await inp.click()
        await page.wait_for_selector(SEL_DROPDOWN, timeout=5_000)
        await asyncio.sleep(0.3)

        # Scope to this field's specific app-custom-select container
        container_sel = _FIELD_CONTAINERS.get(field, "")
        item_sel = f"{container_sel} div.dropdown-list li p" if container_sel else SEL_DROPDOWN_LI

        raw_items: list[str] = await page.locator(item_sel).evaluate_all(
            "els => els.map(el => el.innerText.trim()).filter(t => t.length > 0)"
        )

        options = [opt for t in raw_items if (opt := _parse_option_text(t)) is not None]

        await page.keyboard.press("Escape")
        await asyncio.sleep(0.3)

        logger.debug(f"  [{field}] {len(options)} option(s)")
        return options

    except PlaywrightTimeoutError:
        logger.warning(f"No dropdown appeared for field '{field}'")
        await page.keyboard.press("Escape")
        return []
    except Exception as e:
        logger.error(f"Error reading options for '{field}': {e}")
        await page.keyboard.press("Escape")
        return []


async def select_custom_option(page: Page, field: str, code: str) -> bool:
    """
    Open the custom dropdown for the given field and click the option with the matching code.
    code: the numeric code prefix (e.g. '001' for LETICIA).
    """
    sel = _FIELD_SELECTORS.get(field)
    if not sel:
        return False

    inp = page.locator(sel).first
    try:
        await inp.wait_for(state="visible", timeout=8_000)
        await inp.click()
        await page.wait_for_selector(SEL_DROPDOWN_LI, timeout=5_000)
        await asyncio.sleep(0.3)

        # Scope to this field's app-custom-select to avoid mixing with other dropdowns
        container_sel = _FIELD_CONTAINERS.get(field, "")
        item_sel = f"{container_sel} div.dropdown-list li p" if container_sel else SEL_DROPDOWN_LI

        items = await page.locator(item_sel).all()
        for item in items:
            text = (await item.inner_text()).strip()
            if text.startswith(code):
                await item.click()
                await asyncio.sleep(0.5)
                return True

        logger.warning(f"  [{field}] option with code '{code}' not found in dropdown")
        await page.keyboard.press("Escape")
        return False

    except PlaywrightTimeoutError:
        logger.warning(f"  [{field}] dropdown did not appear")
        await page.keyboard.press("Escape")
        return False
    except Exception as e:
        logger.error(f"  [{field}] select_custom_option error: {e}")
        await page.keyboard.press("Escape")
        return False


# ---------------------------------------------------------------------------
# Form submission & results
# ---------------------------------------------------------------------------

async def click_consultar(page: Page) -> bool:
    """Click the Consultar button and wait for results or no-data message."""
    try:
        btn = page.locator(SEL_CONSULTAR).first
        await btn.wait_for(state="visible", timeout=8_000)
        await btn.click()
        await page.wait_for_selector(
            f"{SEL_MESA_BTN}, "
            ".no-results, .sin-datos, "
            "td:has-text('No se encontraron'), "
            "td:has-text('Sin resultados'), "
            "p:has-text('No hay')",
            timeout=25_000,
        )
        return await page.locator(SEL_MESA_BTN).count() > 0
    except PlaywrightTimeoutError:
        logger.warning("Timeout waiting for results after Consultar")
        return False
    except Exception as e:
        logger.error(f"Error clicking Consultar: {e}")
        return False


async def get_mesa_buttons(page: Page) -> List[Locator]:
    """Return all download buttons on the current results page."""
    return await page.locator(SEL_MESA_BTN).all()


async def get_mesa_number(page: Page, button_index: int) -> str:
    """Extract the mesa number for the given row index."""
    try:
        rows = await page.locator("tr:has(div.open-pdf)").all()
        if button_index < len(rows):
            text = await rows[button_index].inner_text()
            m = re.search(r"\b(\d{1,4})\b", text)
            if m:
                return m.group(1).zfill(3)
    except Exception:
        pass
    return str(button_index + 1).zfill(3)


async def go_to_next_page(page: Page) -> bool:
    """Navigate to the next results page. Returns False if on the last page."""
    try:
        btn = page.locator(SEL_NEXT_PAGE).first
        if await btn.count() == 0:
            return False
        is_disabled = await btn.get_attribute("disabled")
        aria_disabled = await btn.get_attribute("aria-disabled")
        classes = await btn.get_attribute("class") or ""
        if is_disabled is not None or aria_disabled == "true" or "disabled" in classes:
            return False
        await btn.click()
        try:
            await page.wait_for_load_state("networkidle", timeout=8_000)
        except PlaywrightTimeoutError:
            await asyncio.sleep(0.8)
        return True
    except Exception as e:
        logger.debug(f"go_to_next_page: {e}")
        return False


async def take_debug_screenshot(page: Page, name: str) -> None:
    path = f"data/debug_{name}.png"
    await page.screenshot(path=path)
    logger.info(f"Screenshot: {path}")
