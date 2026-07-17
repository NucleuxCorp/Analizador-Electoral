"""Download Temis DIVIPOL JSON snapshots from Registraduría (with retries)."""
from __future__ import annotations

import argparse
import json
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data" / "temis_divipol"

BASES = [
    "https://e14segundavueltapresidente.registraduria.gov.co/assets/temis/divipol_json",
    "https://e14segundavueltapresidentet.registraduria.gov.co/assets/temis/divipol_json",
    "https://divulgacione14presidente.registraduria.gov.co/assets/temis/divipol_json",
]
FILES = (
    "allCorporations.json",
    "allDepartments.json",
    "departmentsTree.json",
)


def fetch_bytes(url: str, timeout: int = 300) -> bytes:
    ctx = ssl._create_unverified_context()
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json,*/*",
        },
    )
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
        return resp.read()


def download_one(name: str, out_dir: Path, timeout: int, retries: int) -> dict:
    last_err = None
    for base in BASES:
        url = f"{base}/{name}"
        for attempt in range(1, retries + 1):
            try:
                print(f"GET {url} (try {attempt}/{retries})")
                raw = fetch_bytes(url, timeout=timeout)
                # validate JSON
                data = json.loads(raw.decode("utf-8"))
                path = out_dir / name
                path.write_bytes(raw)
                print(f"  OK {path} ({len(raw)} bytes)")
                return {
                    "file": name,
                    "url": url,
                    "bytes": len(raw),
                    "ok": True,
                    "top_keys": list(data.keys()) if isinstance(data, dict) else None,
                }
            except Exception as exc:
                last_err = f"{type(exc).__name__}: {exc}"
                print(f"  FAIL {last_err}")
                time.sleep(min(2 * attempt, 8))
    return {"file": name, "ok": False, "error": last_err}


def seed_from_local(out_dir: Path) -> list[dict]:
    """Copy existing local snapshots if remote fails."""
    mapping = {
        "departmentsTree.json": ROOT / "data" / "departmentsTree.json",
        "allCorporations.json": ROOT / "data" / "CorpIndexAndMap.json",
    }
    seeded = []
    for name, src in mapping.items():
        dest = out_dir / name
        if dest.is_file() and dest.stat().st_size > 100:
            continue
        if src.is_file():
            dest.write_bytes(src.read_bytes())
            print(f"SEED {src} -> {dest}")
            seeded.append({"file": name, "seeded_from": str(src), "bytes": dest.stat().st_size})
    return seeded


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument(
        "--allow-seed",
        action="store_true",
        help="If remote fails, seed from existing local departmentsTree/CorpIndexAndMap",
    )
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    results = []
    for name in FILES:
        results.append(download_one(name, args.out, args.timeout, args.retries))

    failed = [r for r in results if not r.get("ok")]
    if failed and args.allow_seed:
        seeded = seed_from_local(args.out)
        results.append({"seeded": seeded})

    meta = {
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "bases": BASES,
        "results": results,
    }
    (args.out / "source_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    ok = sum(1 for r in results if r.get("ok"))
    print(f"Done: ok={ok}/{len(FILES)} -> {args.out}")
    if ok < len(FILES) and not (args.out / "departmentsTree.json").is_file():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
