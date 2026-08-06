#!/usr/bin/env python3
"""E-14 downloader CLI (single-file)

Minimal, self-contained downloader for E14 indexes.
Only standard library + optional Playwright fallback for Cloudflare-protected hosts.
"""
from __future__ import annotations
import argparse
import json
import random
import ssl
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Tuple

SCRIPT_DIR = Path(__file__).parent
DEFAULT_OUT = Path("Herramientas") / "descargador-e14" / "pdfs"

INDEX_NAMES = {
    "e14c": "url_index_e14c.json",
    "e14d": "url_index_e14d.json",
    "e14t": "url_index_e14t.json",
}

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE


def find_index_for_type(kind: str, explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.exists():
            raise FileNotFoundError(p)
        return p
    name = INDEX_NAMES.get(kind)
    candidates = [SCRIPT_DIR / name, Path.cwd() / name]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError(f"Index for {kind} not found. Looked: {candidates}")


def load_index(index_path: Path) -> List[Tuple[str, str]]:
    with open(index_path, "r", encoding="utf-8") as f:
        j = json.load(f)
    items = [(k, v) for k, v in j.items() if not k.startswith("_") and isinstance(v, str)]
    return items


def safe_write(dest: Path, data: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(dest)


def download_e14c(url: str, dest: Path, timeout: int = 30):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=_ssl_ctx, timeout=timeout) as resp:
            data = resp.read()
        if not data.startswith(b"%PDF"):
            return False, "Not a PDF"
        safe_write(dest, data)
        return True, ""
    except Exception as e:
        return False, str(e)


def browser_fetch(url: str, profile_dir: str | None = None, timeout: int = 30):
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return False, None, "Playwright not available"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx_args = {}
            if profile_dir:
                ctx_args["user_data_dir"] = profile_dir
            context = browser.new_context(**ctx_args)
            page = context.new_page()
            resp = page.goto(url, timeout=timeout * 1000)
            if not resp:
                browser.close()
                return False, None, "No response"
            body = resp.body()
            browser.close()
            return True, body, ""
    except Exception as e:
        return False, None, str(e)


def attempt_download(kind: str, filename: str, url: str, out_dir: Path, profile: str | None, resume: bool):
    dest = out_dir / filename
    if resume and dest.exists() and dest.stat().st_size > 0:
        return "SKIPPED", "exists"
    retries = 3
    backoff = 1
    for attempt in range(1, retries + 1):
        try:
            if kind == "e14c":
                ok, err = download_e14c(url, dest)
                if ok:
                    return "OK", ""
                else:
                    raise RuntimeError(err)
            else:
                ok, body, err = browser_fetch(url, profile)
                if not ok:
                    raise RuntimeError(err)
                if not body or not body.startswith(b"%PDF"):
                    raise RuntimeError("Browser fetch did not return a valid PDF")
                safe_write(dest, body)
                return "OK", ""
        except Exception as e:
            msg = str(e)
            if attempt < retries:
                time.sleep(backoff + random.random())
                backoff *= 2
                continue
            return "FAILED", msg


def write_summary(out_dir: Path, results: List[Tuple[str, str, str]]):
    ok = sum(1 for _, s, _ in results if s == "OK")
    skipped = sum(1 for _, s, _ in results if s == "SKIPPED")
    failed = [(fn, m) for fn, s, m in results if s == "FAILED"]
    md = []
    md.append("# Resumen de descarga")
    md.append("")
    md.append(f"Total procesados: {len(results)}")
    md.append(f"Descargados: {ok}")
    md.append(f"Saltados: {skipped}")
    md.append(f"Fallidos: {len(failed)}")
    md.append("")
    if failed:
        md.append("## Fallidos (archivo, motivo)")
        for fn, m in failed:
            md.append(f"- {fn} : {m}")
    p = out_dir / "descarga_resumen.md"
    p.write_text("\n".join(md), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description="E-14 downloader (citizen bundle)")
    parser.add_argument("--type", choices=["e14c", "e14d", "e14t", "all"], default=None)
    parser.add_argument("--index", help="Path to index JSON")
    parser.add_argument("--out", help="Output dir", default=str(DEFAULT_OUT))
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--checkpoint", default=None)
    parser.add_argument("--profile", default=None, help="Playwright user profile dir (optional)")
    args = parser.parse_args(argv)
    kind = args.type
    if not kind:
        print("Choose type to download: e14c, e14d, e14t or all")
        kind = input("Type: ").strip() or "e14c"
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    kinds = [kind] if kind != "all" else ["e14c", "e14d", "e14t"]
    all_items = []
    for k in kinds:
        try:
            idx = find_index_for_type(k, args.index)
        except FileNotFoundError:
            print(f"Index for {k} not found; skipping.")
            continue
        try:
            items = load_index(idx)
        except Exception as e:
            print(f"Failed to load index {idx}: {e}")
            continue
        for filename, url in items:
            all_items.append((k, filename, url))
    if args.limit and args.limit > 0:
        all_items = all_items[: args.limit]
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {}
        for k, filename, url in all_items:
            futures[ex.submit(attempt_download, k, filename, url, out_dir, args.profile, args.resume)] = (k, filename, url)
        for fut in as_completed(futures):
            k, fname, url = futures[fut]
            try:
                status, msg = fut.result()
            except Exception as e:
                status, msg = "FAILED", str(e)
            results.append((fname, status, msg))
            print(f"{fname} : {status} {msg}")
    write_summary(out_dir, results)
    print("Summary written to", out_dir / "descarga_resumen.md")


if __name__ == '__main__':
    main()
