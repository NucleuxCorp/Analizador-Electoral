"""
Auditor de consola — Registraduría E14C
Captura todo el output de consola del browser durante N minutos y lo guarda en CSV.

Uso:
    python audit_console.py                          # 60 min, URL por defecto
    python audit_console.py --url <URL> --minutes 30
    python audit_console.py --url <URL> --minutes 60 --output audit_log.csv
"""
import asyncio
import csv
import argparse
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright

DEFAULT_URL = "https://escrutinios2vueltapresidente2026.registraduria.gov.co"
DEFAULT_OUTPUT = Path("data/audit_console_log.csv")
FIELDNAMES = ["timestamp_utc", "elapsed_s", "level", "message", "source_url", "line", "col"]


async def run(url: str, minutes: int, output: Path):
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    start = datetime.now(timezone.utc)

    print(f"Iniciando auditoría de consola")
    print(f"URL:      {url}")
    print(f"Duración: {minutes} minutos")
    print(f"Output:   {output}")
    print(f"Inicio:   {start.isoformat()}")
    print("-" * 60)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        def on_console(msg):
            now = datetime.now(timezone.utc)
            elapsed = (now - start).total_seconds()
            location = msg.location or {}
            row = {
                "timestamp_utc": now.isoformat(),
                "elapsed_s": round(elapsed, 3),
                "level": msg.type,
                "message": msg.text,
                "source_url": location.get("url", ""),
                "line": location.get("lineNumber", ""),
                "col": location.get("columnNumber", ""),
            }
            rows.append(row)
            level_tag = msg.type.upper().ljust(7)
            print(f"[{elapsed:7.1f}s] [{level_tag}] {msg.text[:120]}")

        def on_page_error(err):
            now = datetime.now(timezone.utc)
            elapsed = (now - start).total_seconds()
            row = {
                "timestamp_utc": now.isoformat(),
                "elapsed_s": round(elapsed, 3),
                "level": "pageerror",
                "message": str(err),
                "source_url": page.url,
                "line": "",
                "col": "",
            }
            rows.append(row)
            print(f"[{elapsed:7.1f}s] [PAGERROR] {str(err)[:120]}")

        page.on("console", on_console)
        page.on("pageerror", on_page_error)

        print(f"Navegando a {url} ...")
        await page.goto(url, wait_until="networkidle", timeout=60_000)
        print(f"Página cargada. Capturando por {minutes} minutos...")
        print("(Podés navegar el browser — todo lo que salga en consola queda registrado)")
        print()

        # Wait for the full audit duration
        await asyncio.sleep(minutes * 60)

        await browser.close()

    # Write CSV
    with open(output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    end = datetime.now(timezone.utc)
    print()
    print("=" * 60)
    print(f"Auditoría completada: {end.isoformat()}")
    print(f"Total entradas capturadas: {len(rows):,}")

    # Summary by level
    from collections import Counter
    by_level = Counter(r["level"] for r in rows)
    for level, count in sorted(by_level.items()):
        print(f"  {level.ljust(12)}: {count:,}")

    print(f"Archivo guardado: {output}")


def main():
    parser = argparse.ArgumentParser(description="Auditor de consola del browser — Registraduría")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--minutes", type=int, default=60)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    asyncio.run(run(args.url, args.minutes, args.output))


if __name__ == "__main__":
    main()
