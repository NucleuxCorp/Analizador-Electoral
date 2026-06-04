"""
Extracts the PDF URL that fires when a .open-pdf button is clicked.

Three strategies are tried in order:
  1. Popup / new tab  — Angular opens the PDF in a new browser tab
  2. Network request  — Playwright intercepts the outgoing PDF request
  3. window.open hook — JS hook captures calls to window.open()

The first strategy that yields a URL wins.
"""
import asyncio
from typing import Optional
from urllib.parse import urlparse

from playwright.async_api import (
    Page,
    Locator,
    BrowserContext,
    Request,
    TimeoutError as PlaywrightTimeoutError,
)

from src.utils.logger import get_logger

logger = get_logger(__name__)

# Keywords that indicate a URL points to the E-14 PDF
_PDF_KEYWORDS = ["pdf", "acta", "e14", "delegado", "download", "descarga", "e-14"]

# JS snippet injected into every page to intercept window.open() calls.
# Must be installed via context.add_init_script() before navigation.
WINDOW_OPEN_HOOK = """
(function () {
    if (window.__e14PdfCapture) return;
    window.__e14PdfCapture = { urls: [] };
    const _orig = window.open.bind(window);
    window.open = function (url) {
        if (url) window.__e14PdfCapture.urls.push(String(url));
        return _orig.apply(this, arguments);
    };
})();
"""


def _looks_like_pdf(url: str) -> bool:
    if not url or url == "about:blank":
        return False
    low = url.lower()
    return any(kw in low for kw in _PDF_KEYWORDS)


async def install_hook(context: BrowserContext) -> None:
    """Install the window.open interceptor for all pages opened in this context."""
    await context.add_init_script(WINDOW_OPEN_HOOK)


async def _clear_hook(page: Page) -> None:
    try:
        await page.evaluate("() => { if (window.__e14PdfCapture) window.__e14PdfCapture.urls = []; }")
    except Exception:
        pass


async def _read_hook(page: Page) -> Optional[str]:
    try:
        urls: list[str] = await page.evaluate(
            "() => window.__e14PdfCapture ? [...window.__e14PdfCapture.urls] : []"
        )
        candidates = [u for u in urls if _looks_like_pdf(u)]
        return candidates[-1] if candidates else None
    except Exception:
        return None


async def extract_pdf_url(page: Page, button: Locator, timeout: float = 8.0) -> Optional[str]:
    """
    Click the given button and capture the PDF URL that results from that click.

    Returns the URL string on success, None if all strategies fail.
    """
    captured_requests: list[str] = []
    request_seen = asyncio.Event()

    def _on_request(request: Request) -> None:
        url = request.url
        if _looks_like_pdf(url):
            captured_requests.append(url)
            request_seen.set()

    await _clear_hook(page)
    page.on("request", _on_request)

    pdf_url: Optional[str] = None

    try:
        # ----------------------------------------------------------------
        # Strategy 1: PDF opens in a new tab / popup
        # ----------------------------------------------------------------
        try:
            async with page.context.expect_page(timeout=3500) as popup_info:
                await button.click()

            new_page = await popup_info.value
            try:
                await new_page.wait_for_load_state("domcontentloaded", timeout=6000)
            except PlaywrightTimeoutError:
                pass

            candidate = new_page.url
            if _looks_like_pdf(candidate):
                pdf_url = candidate
            elif candidate and candidate != "about:blank":
                # Sometimes the tab redirects — check final URL
                pdf_url = candidate

            await new_page.close()

        except PlaywrightTimeoutError:
            # No popup opened — fall through to next strategies
            pass
        except Exception as e:
            logger.debug(f"Strategy 1 (popup) failed: {e}")

        # ----------------------------------------------------------------
        # Strategy 2: Network request interception
        # ----------------------------------------------------------------
        if not pdf_url:
            try:
                await asyncio.wait_for(request_seen.wait(), timeout=timeout)
                if captured_requests:
                    pdf_url = captured_requests[-1]
            except asyncio.TimeoutError:
                pass

        # ----------------------------------------------------------------
        # Strategy 3: window.open() hook
        # ----------------------------------------------------------------
        if not pdf_url:
            pdf_url = await _read_hook(page)

    finally:
        page.remove_listener("request", _on_request)

    if pdf_url:
        logger.debug(f"PDF URL captured: {pdf_url}")
    else:
        logger.warning("All strategies failed — could not capture PDF URL for this mesa")

    return pdf_url


async def detect_url_pattern(sample_urls: list[str]) -> Optional[str]:
    """
    Analyze a list of captured URLs to detect if they follow a predictable pattern.
    If a pattern is found, returns a template string (informational only).
    """
    if len(sample_urls) < 3:
        return None

    parsed = [urlparse(u) for u in sample_urls]
    hosts = {p.netloc for p in parsed}
    paths = [p.path for p in parsed]

    if len(hosts) != 1:
        return None  # different hosts — no single pattern

    # Find longest common prefix of paths
    from os.path import commonprefix
    prefix = commonprefix(paths)
    suffixes = [p[len(prefix):] for p in paths]

    # Pattern: suffix is a number or number.pdf
    clean = [re.sub(r"\.pdf$", "", s) for s in suffixes]
    if all(s.isdigit() for s in clean if s):
        pattern = f"{list(hosts)[0]}{prefix}{{mesa_id}}"
        logger.info(f"URL pattern detected: {pattern}")
        return pattern

    return None


import re
