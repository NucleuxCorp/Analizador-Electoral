"""Turbo E14T: Playwright session + route interception (sin base64).

Abre Chrome con tu perfil, mantiene la sesión activa.
Usa fetch desde JS para disparar las requests, route interception captura los bytes directo.
Sin base64: los bytes viajan de Playwright a Python sin codificación.

Usage:
    python scripts/e14/e14t/turbo_e14.py                          # todos los pendientes
    python scripts/e14/e14t/turbo_e14.py --limit 50                # prueba
    python scripts/e14/e14t/turbo_e14.py --batch 6 --stagger 80    # más rápido
    python scripts/e14/e14t/turbo_e14.py --refresh 0 --cooldown 30 # sin pausas
"""
import argparse, json, time
from pathlib import Path
from playwright.sync_api import sync_playwright

_VUELTA_CONFIG = {
    "primera": {
        "e14t": {
            "urls": Path("data/e14t_urls_primera.jsonl"),
            "out":  Path("E:/e14_primera/E14T"),
            "base": "https://divulgacione14presidentet.registraduria.gov.co",
        },
        "e14d": {
            "urls": Path("data/e14d_urls_primera.jsonl"),
            "out":  Path("E:/e14_primera/E14D"),
            "base": "https://divulgacione14presidente.registraduria.gov.co",
        },
    },
    "segunda": {
        "e14t": {
            "urls": Path("data/e14t_sv_urls.jsonl"),
            "out":  Path("E:/e14_segunda/E14T"),
            "base": "https://e14segundavueltapresidentet.registraduria.gov.co",
        },
        "e14d": {
            "urls": Path("data/e14d_sv_urls.jsonl"),
            "out":  Path("E:/e14_segunda/E14D"),
            "base": "https://e14segundavueltapresidente.registraduria.gov.co",
        },
    },
}
PROFILE = "C:/Users/Administrador/AppData/Local/Google/Chrome/User Data"

CONSECUTIVE_FAIL_LIMIT = 5


def _make_js_trigger(stagger_ms):
    """JS que dispara fetch() escalonados y devuelve solo status (sin base64)."""
    return f"""
async (urls) => {{
    const delay = ms => new Promise(r => setTimeout(r, ms));
    return Promise.all(urls.map((url, i) =>
        delay(i * {stagger_ms}).then(() =>
            fetch(url, {{credentials: "include"}})
                .then(r => ({{ok: r.ok, status: r.status, url: url}}))
                .catch(e => ({{ok: false, error: e.message, url: url}}))
        )
    ));
}}
"""


def build_tasks(records, out_dir):
    """Build (url, filename, dest_path) for pending PDFs."""
    existing = {p.name for p in out_dir.glob("*.pdf") if p.stat().st_size > 20000}
    tasks = []
    for r in records:
        url = r["pdf_url"]
        fname = url.split("/")[-1].split("?")[0]
        if fname not in existing:
            tasks.append((url, fname, out_dir / fname))
    return tasks


def _refresh_pages(pages, base_url):
    """Recarga tabs y reemplaza las que fallaron."""
    for i in range(len(pages)):
        try:
            pages[i].goto(f"{base_url}/home", wait_until="domcontentloaded", timeout=20000)
            pages[i].wait_for_timeout(2000)
            pages[i].evaluate("1+1")
        except:
            try:
                ctx = pages[i].context
                new_pg = ctx.new_page()
                new_pg.goto(f"{base_url}/home", wait_until="domcontentloaded", timeout=20000)
                new_pg.wait_for_timeout(2000)
                pages[i] = new_pg
            except:
                pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="e14t", choices=["e14t", "e14d"], help="E14T or E14D")
    parser.add_argument("--vuelta", default="segunda", choices=["primera", "segunda"], help="Election round")
    parser.add_argument("--limit", type=int, default=0, help="Max downloads (0=all)")
    parser.add_argument("--batch", type=int, default=6, help="Concurrent fetches per batch")
    parser.add_argument("--refresh", type=int, default=0, help="Pause after N downloads (0=off)")
    parser.add_argument("--cooldown", type=int, default=30, help="Pause seconds on Cloudflare block")
    parser.add_argument("--stagger", type=int, default=80, help="ms between each fetch (80=fast, 200=safe)")
    args = parser.parse_args()

    global REFRESH_INTERVAL, COOLDOWN
    REFRESH_INTERVAL = args.refresh
    COOLDOWN = args.cooldown

    cfg = _VUELTA_CONFIG[args.vuelta][args.source]
    URLS_FILE = cfg["urls"]
    OUT_DIR = cfg["out"]
    BASE = cfg["base"]

    js_trigger = _make_js_trigger(args.stagger)

    records = [json.loads(l) for l in URLS_FILE.read_text().strip().splitlines() if l.strip()]
    tasks = build_tasks(records, OUT_DIR)
    if args.limit:
        tasks = tasks[:args.limit]

    total = len(tasks)
    if total == 0:
        print("Todo descargado. No hay pendientes.")
        return

    print(f"Turbo E14T (route intercept): {total} PDFs pendientes")
    print(f"  Batch: {args.batch}  Stagger: {args.stagger}ms")
    print(f"  Refresh: {REFRESH_INTERVAL}  Cooldown: {COOLDOWN}s")
    print(f"  Destino: {OUT_DIR}")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE, headless=False, no_viewport=True,
        )
        page = browser.pages[0] if browser.pages else browser.new_page()

        # ── Route interception: captura bytes de cada PDF ──
        captured = {}  # url -> bytes

        def handle_route(route):
            url = route.request.url
            try:
                response = route.fetch()
                body = response.body()
                if body[:4] == b"%PDF" and len(body) > 20000:
                    captured[url] = body
                route.fulfill(response=response)
            except Exception as e:
                print(f"    ROUTE ERROR: {str(e)[:60]}", flush=True)
                route.continue_()

        page.route("**/temis/pdf/**", handle_route)

        # Navegar a la Registraduria para establecer sesión
        page.goto(f"{BASE}/home", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2000)
        print(f"Chrome listo. INICIO: {time.strftime('%H:%M:%S')}", flush=True)

        ok = fail = 0
        t0 = time.time()
        idx = 0
        batch_ok = 0
        consecutive_fails = 0

        while idx < total:
            batch_end = min(idx + args.batch, total)
            batch = tasks[idx:batch_end]
            urls = [t[0] for t in batch]

            captured.clear()

            # Disparar fetch desde el JS (route interception captura los bytes)
            try:
                results = page.evaluate(js_trigger, urls)
            except Exception as e:
                results = [{"ok": False, "error": str(e)[:40]} for _ in batch]

            # Procesar resultados: usar bytes de route interception si están
            for task, js_result in zip(batch, results):
                url, fname, dest = task
                pdf_bytes = captured.get(url)

                if pdf_bytes:
                    dest.write_bytes(pdf_bytes)
                    ok += 1
                    batch_ok += 1
                    consecutive_fails = 0
                else:
                    fail += 1
                    consecutive_fails += 1

            idx = batch_end

            # Progress
            elapsed = time.time() - t0
            rate = (ok + fail) / elapsed if elapsed > 0 else 0
            pct = (ok + fail) * 100 // total if total > 0 else 0
            eta = (total - (ok + fail)) / rate if rate > 0 else 0
            print(f"  [{ok+fail}/{total} {pct}%] OK={ok} FAIL={fail}  {elapsed:.0f}s ETA={eta:.0f}s  {rate:.1f}/s", flush=True)

            # Refresh programado
            if REFRESH_INTERVAL > 0 and batch_ok >= REFRESH_INTERVAL and idx < total:
                print(f"  PAUSA {COOLDOWN}s... ", end="", flush=True)
                for s in range(COOLDOWN, 0, -1):
                    print(f"\r  PAUSA {COOLDOWN}s... {s}s   ", end="", flush=True)
                    time.sleep(1)
                print()
                _refresh_pages([page], BASE)
                batch_ok = 0

            # Bloqueo detectado
            if consecutive_fails >= CONSECUTIVE_FAIL_LIMIT and idx < total:
                print(f"  BLOQUEO! Pausa {COOLDOWN}s... ", end="", flush=True)
                for s in range(COOLDOWN, 0, -1):
                    print(f"\r  BLOQUEO! Pausa {COOLDOWN}s... {s}s   ", end="", flush=True)
                    time.sleep(1)
                print()
                _refresh_pages([page], BASE)
                consecutive_fails = 0

        elapsed = time.time() - t0
        print(f"\nCOMPLETADO: {elapsed:.0f}s")
        print(f"OK={ok} FAIL={fail}  {(ok+fail)/elapsed:.1f} pdf/s")
        print(f"Total en {OUT_DIR}: {len(list(OUT_DIR.glob('*.pdf')))} PDFs")
        browser.close()


if __name__ == "__main__":
    main()
