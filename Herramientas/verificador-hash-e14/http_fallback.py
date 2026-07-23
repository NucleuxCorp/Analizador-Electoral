# generated: do not edit — synced from src/modules/analyzer/http_fallback.py
"""Playwright-backed fallback fetcher for blocked E14C downloads.

`PlaywrightFetcher` is used by the online E14C verification transports
(`verify_e14c.py`, `verificador_e14c.py`) once their urllib-primary path hits
the `_RATE["consecutive_fails"] >= 3` threshold and the Registraduria server
starts fingerprint-blocking plain script traffic.

Hard constraint (ADR-1, e14c-verification-dual-mode design): this module
imports NOTHING from the rest of `src/` — stdlib plus a LAZY `playwright`
import only. That is what allows it to be copied verbatim into the
citizen-facing bundle at `Herramientas/verificador-hash-e14/http_fallback.py`
without dragging unrelated project code (torch/OCR/etc.) along with it.

Citizen-safe launch (ADR-2): the browser context is a FRESH, non-persistent
`chromium.launch()` — no `user_data_dir`, no dependency on the maintainer's
installed Chrome profile. E14C's download endpoints do not require an
authenticated session; the browser only needs to present a browser-like
network fingerprint.

Single degradation path (ADR-4): any failure to import Playwright or launch
Chromium (missing display, missing Chromium binary, launch exception) sets
`available = False`, prints exactly one warning, and thereafter `fetch()`
returns `(None, "fallback_unavailable")`. This is treated by callers exactly
like a urllib error — never a crash, never a hang.
"""
from __future__ import annotations

import os
import sys
from typing import Optional

# Route glob for the documented navigate+page.evaluate(fetch)+route escalation
# path, used when the primary `context.request.get()` approach turns out not
# to carry a full browser fingerprint (server returns an HTML challenge page
# instead of a PDF) — see design.md ADR-2 risk log. Implemented in
# `PlaywrightFetcher._fetch_via_page()`.
# NOT E14T's "**/temis/pdf/**" — E14C serves PDFs under a different path.
ROUTE_GLOB = "**/docs/E14/**"

# JS trigger for the escalation path (adapted from the proven
# Debug/1. Descargas de E14/E14T/ensayo_e14t.py rehearsal shape): a real
# in-page `fetch()` so the request travels through Chromium's actual network
# stack (full TLS/JA3 fingerprint), while `page.route()` intercepts and
# captures the response body.
_JS_FETCH_TRIGGER = """
async (url) => {
    try {
        const r = await fetch(url, {credentials: "include"});
        return {ok: r.ok, status: r.status};
    } catch (e) {
        return {ok: false, error: String(e)};
    }
}
"""

_WARNING = (
    "[http_fallback] Playwright/Chromium unavailable — continuing "
    "urllib-only; downloads the server blocks will be reported as errors "
    "and retried on resume"
)


class PlaywrightFetcher:
    """Lazy-launch, fresh-Chromium fetcher for blocked E14C PDF requests.

    Usage:
        fetcher = PlaywrightFetcher(base_url=BASE)
        body, error = fetcher.fetch(url)
        if body is None:
            ...  # treat exactly like a urllib error (error may be
                 # "fallback_unavailable" or a caught exception message)
        fetcher.close()
    """

    def __init__(self, base_url: str, headless: Optional[bool] = None) -> None:
        self.base_url = base_url
        if headless is None:
            headless = bool(os.environ.get("E14C_FALLBACK_HEADLESS"))
        self._headless = headless

        self._available = False
        self._launch_attempted = False
        self._warned = False

        self._playwright = None
        self._browser = None
        self._context = None

        # Escalation path state (navigate+page.evaluate(fetch)+route). Lazily
        # created on first escalation, then reused for subsequent fetches.
        self._page = None
        self._route_captured: Optional[bytes] = None

    @property
    def available(self) -> bool:
        """False until a successful launch; False again after a failed one."""
        return self._available

    def ensure_launched(self) -> bool:
        """Lazily launch a fresh Chromium context. Idempotent — only attempts
        the launch once per instance; subsequent calls return the cached
        availability without retrying (per ADR-4, no retry within a run)."""
        if self._launch_attempted:
            return self._available
        self._launch_attempted = True

        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:  # Playwright not installed — the common
            self._degrade(exc)  # case for citizens without the fallback.
            return False

        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=self._headless)
            self._context = self._browser.new_context()
        except Exception as exc:  # no display, no Chromium binary, etc.
            self._degrade(exc)
            return False

        self._available = True
        return True

    def _degrade(self, exc: Exception) -> None:
        self._available = False
        if not self._warned:
            self._warned = True
            print(f"{_WARNING} ({exc})", file=sys.stderr)

    def fetch(self, url: str, method: str = "GET") -> tuple[Optional[bytes], str]:
        """Fetch `url`, escalating from a lightweight request context to a
        real page-level fetch if needed.

        Two-stage attempt (design.md ADR-2 risk log):
        1. `context.request.get()` — cheap, no page/navigation overhead.
           Works whenever the server isn't currently fingerprint-blocking
           this path.
        2. If (1) doesn't yield real PDF bytes (exception, or a response
           that fails the "%PDF" guard — typically an HTML anti-bot/error
           page), escalate to a real in-page `fetch()` via
           `page.evaluate()` + `page.route()` interception, which travels
           through Chromium's actual network stack and carries a full
           browser TLS/JA3 fingerprint.

        Returns:
            (bytes, "") on success (from either stage).
            (None, "fallback_unavailable") if Playwright/Chromium could not
                be launched (caller treats this exactly like a urllib error).
            (None, "<message>") if both stages failed — the stage-2
                (escalation) error, or both errors combined when they
                differ.

        `method` is accepted for caller symmetry with urllib's HEAD/GET split
        (HEAD is emulated by a GET whose body length gives the size) — the
        fetch mechanism itself is always a single GET, at either stage.
        """
        if not self.ensure_launched():
            return None, "fallback_unavailable"

        body, error1 = self._fetch_via_request_context(url)
        if body is not None:
            return body, ""

        body2, error2 = self._fetch_via_page(url)
        if body2 is not None:
            return body2, ""

        if error1 == error2:
            return None, error2
        return None, f"{error1} (escalation: {error2})"

    def _fetch_via_request_context(self, url: str) -> tuple[Optional[bytes], str]:
        """Stage 1: lightweight `context.request.get()` attempt."""
        try:
            response = self._context.request.get(url)
            body = response.body()
        except Exception as exc:
            return None, str(exc)

        if body[:4] != b"%PDF":
            return None, "invalid_response"

        return body, ""

    def _ensure_escalation_page(self):
        """Lazily create (and cache) the page used for the escalation path.
        Navigates once (`page.goto(base_url)`) and registers the
        `ROUTE_GLOB` route interceptor; reused across subsequent `fetch()`
        calls that need to escalate."""
        if self._page is not None:
            return self._page

        page = self._context.new_page()
        page.route(ROUTE_GLOB, self._handle_route)
        page.goto(self.base_url, wait_until="domcontentloaded", timeout=20000)
        self._page = page
        return page

    def _handle_route(self, route) -> None:
        """`page.route()` interceptor: performs the real network fetch,
        captures the body if it looks like a genuine PDF, and always
        fulfills the route so the in-page `fetch()` resolves normally."""
        try:
            response = route.fetch()
            body = response.body()
            if body[:4] == b"%PDF":
                self._route_captured = body
            route.fulfill(response=response)
        except Exception:
            try:
                route.continue_()
            except Exception:
                pass

    def _fetch_via_page(self, url: str) -> tuple[Optional[bytes], str]:
        """Stage 2: navigate+`page.evaluate(fetch)`+`route` escalation."""
        try:
            page = self._ensure_escalation_page()
        except Exception as exc:
            return None, str(exc)

        self._route_captured = None
        try:
            page.evaluate(_JS_FETCH_TRIGGER, url)
        except Exception as exc:
            return None, str(exc)

        body = self._route_captured
        if body is None:
            return None, "invalid_response"

        return body, ""

    def close(self) -> None:
        """Release the browser/context/playwright handles. Safe to call
        multiple times, and safe to call even if launch never succeeded."""
        if self._page is not None:
            try:
                self._page.close()
            except Exception:
                pass
            self._page = None

        if self._context is not None:
            try:
                self._context.close()
            except Exception:
                pass
            self._context = None

        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
