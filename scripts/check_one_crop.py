"""Check a specific crop's source_url and fetch it locally."""
from __future__ import annotations

import os
import ssl
import sys
import time
import urllib.request
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
try:
    from dotenv import load_dotenv
    load_dotenv(_REPO / ".env")
except ImportError:
    pass

from supabase import create_client


def main() -> None:
    crop_id = sys.argv[1] if len(sys.argv) > 1 else "e6f23308accbcfe8"
    url = os.environ["SUPABASE_URL"].strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    client = create_client(url, key)

    res = (
        client.table("crops")
        .select("crop_id, source_url, pdf_path, full_cell_crop_id")
        .eq("crop_id", crop_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        print(f"NOT FOUND: {crop_id}")
        return
    r = res.data[0]
    print(f"crop_id: {r['crop_id']}")
    print(f"source_url: {r.get('source_url')!r}")
    print(f"pdf_path: {r.get('pdf_path')!r}")
    print(f"full_cell_crop_id: {r.get('full_cell_crop_id')!r}")

    src = (r.get("source_url") or "").strip()
    if not src:
        print("\nsource_url is EMPTY → proxy would fall through to disk path.")
        return

    print(f"\nFetching from THIS machine (mimics what /pdf does on Railway)...")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
            data = resp.read()
        print(f"  OK status=200 bytes={len(data)} dt={time.time()-t0:.2f}s head={data[:4]!r}")
    except Exception as exc:
        print(f"  FAIL dt={time.time()-t0:.2f}s type={type(exc).__name__} repr={exc!r} str={str(exc)!r}")


if __name__ == "__main__":
    main()
