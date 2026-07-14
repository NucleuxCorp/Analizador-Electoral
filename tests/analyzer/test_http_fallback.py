"""Tests for src.modules.analyzer.http_fallback.PlaywrightFetcher.

Covers Phase 1 of the e14c-verification-dual-mode change:
- ensure_launched() success/failure (import missing, launch exception)
- fetch() success / "fallback_unavailable" degradation
- one-time warning on degradation
- "%PDF" transient-response guard
- no `user_data_dir` / persistent-profile usage (citizen-safe launch, ADR-2)
- headless default False + E14C_FALLBACK_HEADLESS escape hatch
- close() idempotency

None of these tests touch a real network or a real Chrome install — Playwright's
`sync_playwright` entry point is stubbed via `sys.modules["playwright.sync_api"]`.
"""
from __future__ import annotations

import sys
import types

import pytest

from src.modules.analyzer.http_fallback import PlaywrightFetcher

PDF_BYTES = b"%PDF-1.4 fake pdf body"
NON_PDF_BYTES = b"<html>blocked</html>"


# ---------------------------------------------------------------------------
# Fake Playwright sync API
# ---------------------------------------------------------------------------

class _FakeAPIResponse:
    def __init__(self, body: bytes):
        self._body = body

    def body(self) -> bytes:
        return self._body


class _FakeRequestContext:
    def __init__(self, body: bytes = PDF_BYTES, raise_on_get: Exception | None = None):
        self._body = body
        self._raise_on_get = raise_on_get
        self.get_calls: list[str] = []

    def get(self, url: str):
        self.get_calls.append(url)
        if self._raise_on_get is not None:
            raise self._raise_on_get
        return _FakeAPIResponse(self._body)


class _FakeRouteRequest:
    def __init__(self, url: str):
        self.url = url


class _FakeRoute:
    """Fake `playwright.sync_api.Route` used by the page-level escalation path."""

    def __init__(self, url: str, fetch_body: bytes | None = None, fetch_raises: Exception | None = None):
        self.request = _FakeRouteRequest(url)
        self._fetch_body = fetch_body
        self._fetch_raises = fetch_raises
        self.fulfilled = False
        self.continued = False

    def fetch(self):
        if self._fetch_raises is not None:
            raise self._fetch_raises
        return _FakeAPIResponse(self._fetch_body)

    def fulfill(self, response=None):
        self.fulfilled = True

    def continue_(self):
        self.continued = True


class _FakePage:
    """Fake `playwright.sync_api.Page` for the navigate+evaluate(fetch)+route
    escalation path (design.md ADR-2 risk log)."""

    def __init__(
        self,
        route_body: bytes | None = None,
        route_raises: Exception | None = None,
        evaluate_raises: Exception | None = None,
    ):
        self._route_glob = None
        self._route_handler = None
        self._route_body = route_body
        self._route_raises = route_raises
        self._evaluate_raises = evaluate_raises
        self.goto_calls: list[str] = []
        self.evaluate_calls: list[str] = []
        self.closed = False

    def route(self, glob, handler):
        self._route_glob = glob
        self._route_handler = handler

    def goto(self, url: str, **kwargs):
        self.goto_calls.append(url)

    def evaluate(self, script: str, arg):
        self.evaluate_calls.append(arg)
        if self._evaluate_raises is not None:
            raise self._evaluate_raises
        if self._route_handler is not None:
            route = _FakeRoute(arg, fetch_body=self._route_body, fetch_raises=self._route_raises)
            self._route_handler(route)
        return {"ok": True, "status": 200}

    def close(self):
        self.closed = True


class _FakeContext:
    def __init__(
        self,
        body: bytes = PDF_BYTES,
        raise_on_get: Exception | None = None,
        page: _FakePage | None = None,
    ):
        self.request = _FakeRequestContext(body=body, raise_on_get=raise_on_get)
        # Default page has no captured escalation body — tests that rely on
        # a successful escalation must pass an explicit `page=` fixture.
        self._page = page if page is not None else _FakePage(route_body=None)
        self.new_page_calls = 0
        self.closed = False

    def new_page(self):
        self.new_page_calls += 1
        return self._page

    def close(self):
        self.closed = True


class _FakeBrowser:
    def __init__(self, context: _FakeContext):
        self._context = context
        self.closed = False

    def new_context(self):
        return self._context

    def close(self):
        self.closed = True


class _FakeChromium:
    """Deliberately has NO `launch_persistent_context` — if production code
    ever calls it, this raises AttributeError, which the degradation path
    must catch (proving no persistent-profile pattern is used, ADR-2)."""

    def __init__(self, browser: _FakeBrowser | None = None, raise_on_launch: Exception | None = None):
        self._browser = browser
        self._raise_on_launch = raise_on_launch
        self.launch_calls: list[dict] = []

    def launch(self, **kwargs):
        self.launch_calls.append(kwargs)
        if self._raise_on_launch is not None:
            raise self._raise_on_launch
        return self._browser


class _FakePlaywright:
    def __init__(self, chromium: _FakeChromium):
        self.chromium = chromium
        self.stopped = False

    def stop(self):
        self.stopped = True


class _FakeSyncPlaywrightCM:
    def __init__(self, playwright_obj: _FakePlaywright):
        self._playwright_obj = playwright_obj

    def start(self):
        return self._playwright_obj


def _install_fake_playwright(monkeypatch, chromium: _FakeChromium):
    """Install a fake `playwright.sync_api` module in sys.modules so
    `from playwright.sync_api import sync_playwright` resolves to our stub."""
    playwright_obj = _FakePlaywright(chromium=chromium)
    fake_module = types.SimpleNamespace(
        sync_playwright=lambda: _FakeSyncPlaywrightCM(playwright_obj)
    )
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_module)
    return playwright_obj


def _working_chromium(
    body: bytes = PDF_BYTES,
    raise_on_get: Exception | None = None,
    page: _FakePage | None = None,
) -> _FakeChromium:
    context = _FakeContext(body=body, raise_on_get=raise_on_get, page=page)
    browser = _FakeBrowser(context=context)
    return _FakeChromium(browser=browser)


# ---------------------------------------------------------------------------
# ensure_launched()
# ---------------------------------------------------------------------------

def test_ensure_launched_success_marks_fetcher_available(monkeypatch):
    chromium = _working_chromium()
    _install_fake_playwright(monkeypatch, chromium)

    fetcher = PlaywrightFetcher(base_url="https://example.test")
    result = fetcher.ensure_launched()

    assert result is True
    assert fetcher.available is True


def test_ensure_launched_import_error_degrades_gracefully(monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)

    fetcher = PlaywrightFetcher(base_url="https://example.test")
    result = fetcher.ensure_launched()

    assert result is False
    assert fetcher.available is False


def test_ensure_launched_browser_launch_exception_degrades_gracefully(monkeypatch):
    chromium = _FakeChromium(raise_on_launch=RuntimeError("no display available"))
    _install_fake_playwright(monkeypatch, chromium)

    fetcher = PlaywrightFetcher(base_url="https://example.test")
    result = fetcher.ensure_launched()

    assert result is False
    assert fetcher.available is False


def test_ensure_launched_prints_warning_exactly_once(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)

    fetcher = PlaywrightFetcher(base_url="https://example.test")
    fetcher.ensure_launched()
    fetcher.ensure_launched()
    fetcher.ensure_launched()

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert combined.count("Playwright/Chromium unavailable") == 1


# ---------------------------------------------------------------------------
# fetch()
# ---------------------------------------------------------------------------

def test_fetch_returns_bytes_on_success(monkeypatch):
    chromium = _working_chromium(body=PDF_BYTES)
    _install_fake_playwright(monkeypatch, chromium)

    fetcher = PlaywrightFetcher(base_url="https://example.test")
    body, error = fetcher.fetch("https://example.test/docs/E14/foo.pdf")

    assert body == PDF_BYTES
    assert error == ""


def test_fetch_returns_fallback_unavailable_when_launch_fails(monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)

    fetcher = PlaywrightFetcher(base_url="https://example.test")
    body, error = fetcher.fetch("https://example.test/docs/E14/foo.pdf")

    assert body is None
    assert error == "fallback_unavailable"


def test_fetch_rejects_non_pdf_body_as_transient_guard(monkeypatch):
    chromium = _working_chromium(body=NON_PDF_BYTES)
    _install_fake_playwright(monkeypatch, chromium)

    fetcher = PlaywrightFetcher(base_url="https://example.test")
    body, error = fetcher.fetch("https://example.test/docs/E14/foo.pdf")

    assert body is None
    assert error == "invalid_response"


def test_fetch_returns_error_string_when_request_raises(monkeypatch):
    chromium = _working_chromium(raise_on_get=ConnectionError("blocked"))
    _install_fake_playwright(monkeypatch, chromium)

    fetcher = PlaywrightFetcher(base_url="https://example.test")
    body, error = fetcher.fetch("https://example.test/docs/E14/foo.pdf")

    assert body is None
    assert "blocked" in error


# ---------------------------------------------------------------------------
# fetch() escalation: navigate+page.evaluate(fetch)+route (design.md ADR-2
# risk log, ROUTE_GLOB) — used only when `context.request.get()` does not
# yield real PDF bytes (fingerprint blocked -> HTML challenge page).
# ---------------------------------------------------------------------------

def test_fetch_escalates_to_page_route_when_request_context_returns_non_pdf(monkeypatch):
    page = _FakePage(route_body=PDF_BYTES)
    chromium = _working_chromium(body=NON_PDF_BYTES, page=page)
    _install_fake_playwright(monkeypatch, chromium)

    fetcher = PlaywrightFetcher(base_url="https://example.test")
    body, error = fetcher.fetch("https://example.test/docs/E14/foo.pdf")

    assert body == PDF_BYTES
    assert error == ""
    assert page.evaluate_calls == ["https://example.test/docs/E14/foo.pdf"]


def test_fetch_escalates_to_page_route_when_request_context_raises(monkeypatch):
    page = _FakePage(route_body=PDF_BYTES)
    chromium = _working_chromium(raise_on_get=ConnectionError("blocked"), page=page)
    _install_fake_playwright(monkeypatch, chromium)

    fetcher = PlaywrightFetcher(base_url="https://example.test")
    body, error = fetcher.fetch("https://example.test/docs/E14/foo.pdf")

    assert body == PDF_BYTES
    assert error == ""


def test_fetch_does_not_escalate_when_request_context_already_returns_pdf(monkeypatch):
    page = _FakePage(route_body=PDF_BYTES)
    chromium = _working_chromium(body=PDF_BYTES, page=page)
    _install_fake_playwright(monkeypatch, chromium)

    fetcher = PlaywrightFetcher(base_url="https://example.test")
    body, error = fetcher.fetch("https://example.test/docs/E14/foo.pdf")

    assert body == PDF_BYTES
    assert error == ""
    assert page.evaluate_calls == []


def test_fetch_escalation_returns_invalid_response_when_route_never_captures_pdf(monkeypatch):
    page = _FakePage(route_body=NON_PDF_BYTES)
    chromium = _working_chromium(body=NON_PDF_BYTES, page=page)
    _install_fake_playwright(monkeypatch, chromium)

    fetcher = PlaywrightFetcher(base_url="https://example.test")
    body, error = fetcher.fetch("https://example.test/docs/E14/foo.pdf")

    assert body is None
    assert error == "invalid_response"


def test_fetch_escalation_returns_error_when_page_evaluate_raises(monkeypatch):
    page = _FakePage(evaluate_raises=RuntimeError("navigation timeout"))
    chromium = _working_chromium(body=NON_PDF_BYTES, page=page)
    _install_fake_playwright(monkeypatch, chromium)

    fetcher = PlaywrightFetcher(base_url="https://example.test")
    body, error = fetcher.fetch("https://example.test/docs/E14/foo.pdf")

    assert body is None
    assert "navigation timeout" in error


def test_fetch_escalation_reuses_same_page_across_calls(monkeypatch):
    page = _FakePage(route_body=PDF_BYTES)
    chromium = _working_chromium(body=NON_PDF_BYTES, page=page)
    _install_fake_playwright(monkeypatch, chromium)

    fetcher = PlaywrightFetcher(base_url="https://example.test")
    fetcher.fetch("https://example.test/docs/E14/foo.pdf")
    fetcher.fetch("https://example.test/docs/E14/bar.pdf")

    assert fetcher._context.new_page_calls == 1
    assert page.goto_calls == ["https://example.test"]
    assert page.evaluate_calls == [
        "https://example.test/docs/E14/foo.pdf",
        "https://example.test/docs/E14/bar.pdf",
    ]


def test_fetch_escalation_uses_route_glob_matching_e14c_docs_path(monkeypatch):
    page = _FakePage(route_body=PDF_BYTES)
    chromium = _working_chromium(body=NON_PDF_BYTES, page=page)
    _install_fake_playwright(monkeypatch, chromium)

    fetcher = PlaywrightFetcher(base_url="https://example.test")
    fetcher.fetch("https://example.test/docs/E14/foo.pdf")

    assert page._route_glob == "**/docs/E14/**"


def test_close_closes_escalation_page_if_created(monkeypatch):
    page = _FakePage(route_body=PDF_BYTES)
    chromium = _working_chromium(body=NON_PDF_BYTES, page=page)
    _install_fake_playwright(monkeypatch, chromium)

    fetcher = PlaywrightFetcher(base_url="https://example.test")
    fetcher.fetch("https://example.test/docs/E14/foo.pdf")
    fetcher.close()

    assert page.closed is True


def test_close_is_idempotent_when_escalation_page_was_created(monkeypatch):
    page = _FakePage(route_body=PDF_BYTES)
    chromium = _working_chromium(body=NON_PDF_BYTES, page=page)
    _install_fake_playwright(monkeypatch, chromium)

    fetcher = PlaywrightFetcher(base_url="https://example.test")
    fetcher.fetch("https://example.test/docs/E14/foo.pdf")

    fetcher.close()
    fetcher.close()  # must not raise


# ---------------------------------------------------------------------------
# Citizen-safe launch (ADR-2): no user_data_dir / persistent profile
# ---------------------------------------------------------------------------

def test_launch_never_uses_user_data_dir(monkeypatch):
    chromium = _working_chromium()
    _install_fake_playwright(monkeypatch, chromium)

    fetcher = PlaywrightFetcher(base_url="https://example.test")
    fetcher.ensure_launched()

    assert len(chromium.launch_calls) == 1
    assert "user_data_dir" not in chromium.launch_calls[0]


# ---------------------------------------------------------------------------
# headless default + escape hatch (ADR-6)
# ---------------------------------------------------------------------------

def test_headless_defaults_to_false(monkeypatch):
    monkeypatch.delenv("E14C_FALLBACK_HEADLESS", raising=False)
    chromium = _working_chromium()
    _install_fake_playwright(monkeypatch, chromium)

    fetcher = PlaywrightFetcher(base_url="https://example.test")
    fetcher.ensure_launched()

    assert chromium.launch_calls[0]["headless"] is False


def test_headless_env_override_enables_headless(monkeypatch):
    monkeypatch.setenv("E14C_FALLBACK_HEADLESS", "1")
    chromium = _working_chromium()
    _install_fake_playwright(monkeypatch, chromium)

    fetcher = PlaywrightFetcher(base_url="https://example.test")
    fetcher.ensure_launched()

    assert chromium.launch_calls[0]["headless"] is True


def test_explicit_headless_argument_overrides_env(monkeypatch):
    monkeypatch.setenv("E14C_FALLBACK_HEADLESS", "1")
    chromium = _working_chromium()
    _install_fake_playwright(monkeypatch, chromium)

    fetcher = PlaywrightFetcher(base_url="https://example.test", headless=False)
    fetcher.ensure_launched()

    assert chromium.launch_calls[0]["headless"] is False


# ---------------------------------------------------------------------------
# close()
# ---------------------------------------------------------------------------

def test_close_is_idempotent_after_successful_launch(monkeypatch):
    chromium = _working_chromium()
    _install_fake_playwright(monkeypatch, chromium)

    fetcher = PlaywrightFetcher(base_url="https://example.test")
    fetcher.ensure_launched()

    fetcher.close()
    fetcher.close()  # must not raise


def test_close_is_idempotent_when_never_launched():
    fetcher = PlaywrightFetcher(base_url="https://example.test")

    fetcher.close()
    fetcher.close()  # must not raise
