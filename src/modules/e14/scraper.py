"""
Main orchestrator for E-14 URL collection.

Navigation flow:
  1. Home page (/home) -> get all department links
  2. For each department -> navigate to /departamento/{id}
  3. On dept page: municipio -> zona -> puesto (custom Angular select dropdowns)
  4. Click Consultar -> paginated mesa list -> extract PDF URL per mesa

Skips options with 0% published mesas (no PDFs available).
Records skipped options as 'pending' in checkpoint DB.
"""
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, Page, Browser, BrowserContext

from src.modules.e14 import form_handler, url_extractor
from src.modules.e14.models import FormOption
from src.utils import storage
from src.utils.logger import get_logger
from src.utils.rate_limiter import AsyncRateLimiter

logger = get_logger(__name__)

BASE_URL = os.getenv("E14_BASE_URL", "https://divulgacione14presidente.registraduria.gov.co")

_sample_urls: list[str] = []


async def _process_puesto(
    page: Page,
    dept: FormOption,
    mpio: FormOption,
    zona: FormOption,
    puesto: FormOption,
    rate_limiter: AsyncRateLimiter,
) -> int:
    """Process all mesas for one puesto. Returns URLs collected."""
    already_done = await storage.is_completed(dept.value, mpio.value, zona.value, puesto.value)
    if already_done:
        logger.info(f"  [skip] {mpio.label} / zona {zona.label} / {puesto.label}")
        return 0

    if puesto.porcentaje == 0:
        logger.info(f"  [0%] {mpio.label} / zona {zona.label} / {puesto.label} — sin mesas publicadas, marcado como pendiente")
        await storage.save_checkpoint(dept.value, mpio.value, zona.value, puesto.value,
                                      0, 0, completed=False, pending=True, porcentaje=0)
        return 0

    logger.info(f"  [query {puesto.porcentaje}%] {mpio.label} / zona {zona.label} / {puesto.label}")

    # Select puesto and query
    ok = await form_handler.select_custom_option(page, "puesto", puesto.value)
    if not ok:
        return 0

    await rate_limiter.wait()
    has_results = await form_handler.click_consultar(page)
    if not has_results:
        logger.info(f"    No mesas returned")
        await storage.save_checkpoint(dept.value, mpio.value, zona.value, puesto.value,
                                      0, 0, completed=True, porcentaje=puesto.porcentaje)
        return 0

    urls_collected = 0
    page_num = 1

    while True:
        buttons = await form_handler.get_mesa_buttons(page)
        logger.info(f"    page {page_num}: {len(buttons)} mesa(s)")

        for i, button in enumerate(buttons):
            mesa_num = await form_handler.get_mesa_number(page, i)
            pdf_url = await url_extractor.extract_pdf_url(page, button)

            if pdf_url:
                record = {
                    "departamento": dept.label,
                    "cod_dpto": dept.value,
                    "municipio": mpio.label,
                    "cod_mpio": mpio.value,
                    "zona": zona.label,
                    "puesto": puesto.label,
                    "cod_puesto": puesto.value,
                    "mesa": mesa_num,
                    "pdf_url": pdf_url,
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                }
                storage.append_url(record)
                urls_collected += 1

                if len(_sample_urls) < 10:
                    _sample_urls.append(pdf_url)
                    if len(_sample_urls) == 5:
                        pattern = await url_extractor.detect_url_pattern(_sample_urls)
                        if pattern:
                            logger.info(f"[pattern detected] {pattern}")

            await rate_limiter.wait()

        has_next = await form_handler.go_to_next_page(page)
        if not has_next:
            break
        page_num += 1

    await storage.save_checkpoint(dept.value, mpio.value, zona.value, puesto.value,
                                  page_num, urls_collected, completed=True,
                                  porcentaje=puesto.porcentaje)
    logger.info(f"    [done] {urls_collected} URL(s)")
    return urls_collected


async def collect_urls(
    headless: bool = True,
    delay_ms: int = 1500,
    target_dept: Optional[str] = None,
    reset: bool = False,
    skip_zero_percent: bool = True,
) -> None:
    """
    Entry point for URL collection.

    Args:
        headless: Run without visible browser window.
        delay_ms: Min ms between requests.
        target_dept: If set, process only this department name (case-insensitive).
        reset: Wipe checkpoint and start fresh.
        skip_zero_percent: Skip municipalities/zones/puestos with 0% published.
    """
    await storage.init_storage()
    if reset:
        await storage.reset_all()

    rate_limiter = AsyncRateLimiter(delay_ms=delay_ms)
    total_urls = 0
    home_url = f"{BASE_URL}/home"

    async with async_playwright() as pw:
        browser: Browser = await pw.chromium.launch(headless=headless)
        context: BrowserContext = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        await url_extractor.install_hook(context)
        page: Page = await context.new_page()
        page.set_default_timeout(30_000)

        # Load departments from pre-saved JSON (faster and avoids re-scraping home page)
        dept_file = Path("data/departamentos.json")
        if dept_file.exists():
            import json
            raw = json.loads(dept_file.read_text(encoding="utf-8"))
            departamentos = [FormOption(value=d["id"], label=d["nombre"]) for d in raw]
            logger.info(f"Loaded {len(departamentos)} department(s) from {dept_file}")
        else:
            # Fallback: scrape home page
            logger.info(f"Loading home: {home_url}")
            await page.goto(home_url, wait_until="domcontentloaded")
            await asyncio.sleep(2)
            departamentos = await form_handler.get_departments_from_home(page)
            if not departamentos:
                logger.error("No departments found. Run the home scrape first or check the site.")
                await form_handler.take_debug_screenshot(page, "no_departamentos")
                await browser.close()
                return

        for dept in departamentos:
            if target_dept and dept.label.upper() != target_dept.upper():
                continue

            logger.info(f"\n{'='*60}")
            logger.info(f"DEPARTAMENTO: {dept.label} (id={dept.value})")
            logger.info(f"{'='*60}")

            ok = await form_handler.navigate_to_department(page, dept.value, BASE_URL)
            if not ok:
                continue

            # --- Municipios ---
            municipios = await form_handler.get_custom_options(page, "municipio")
            logger.info(f"  {len(municipios)} municipio(s)")

            zero_mpios = [m for m in municipios if m.porcentaje == 0]
            active_mpios = [m for m in municipios if m.porcentaje > 0]
            if zero_mpios:
                logger.info(f"  Skipping {len(zero_mpios)} municipio(s) with 0%: "
                            f"{', '.join(m.label for m in zero_mpios)}")
                for m in zero_mpios:
                    await storage.save_checkpoint(dept.value, m.value, "0", "0",
                                                  0, 0, completed=False, pending=True,
                                                  porcentaje=0)

            for mpio in (active_mpios if skip_zero_percent else municipios):
                logger.info(f"\n  MUNICIPIO: {mpio.label} ({mpio.porcentaje}%)")

                # Re-navigate to reset form state before each municipio
                await form_handler.navigate_to_department(page, dept.value, BASE_URL)

                ok = await form_handler.select_custom_option(page, "municipio", mpio.value)
                if not ok:
                    # One retry after a fresh wait
                    await asyncio.sleep(1.5)
                    ok = await form_handler.select_custom_option(page, "municipio", mpio.value)
                if not ok:
                    continue
                await asyncio.sleep(1.0)

                # --- Zonas ---
                zonas = await form_handler.get_custom_options(page, "zona")
                if not zonas:
                    zonas = [FormOption(value="0", label="UNICA", porcentaje=100)]

                # Don't skip zonas by % — zona format may differ and % may be unreliable
                for zona in zonas:
                    if zona.value != "0":
                        ok = await form_handler.select_custom_option(page, "zona", zona.value)
                        if not ok:
                            continue
                        await asyncio.sleep(0.8)

                    # --- Puestos ---
                    puestos = await form_handler.get_custom_options(page, "puesto")

                    for puesto in puestos:
                        n = await _process_puesto(page, dept, mpio, zona, puesto, rate_limiter)
                        total_urls += n
                        await rate_limiter.wait()

        await browser.close()

    stats = await storage.get_stats()
    logger.info(f"\n{'='*60}")
    logger.info(f"COLLECTION COMPLETE")
    logger.info(f"URLs recolectadas    : {stats['total_urls']}")
    logger.info(f"Puestos completados  : {stats['completed_puestos']}/{stats['total_puestos']}")
    logger.info(f"Pendientes (0%)      : {stats.get('pending_puestos', 0)}")
    logger.info(f"Output               : data/urls/e14_urls.jsonl")
    logger.info(f"{'='*60}")
