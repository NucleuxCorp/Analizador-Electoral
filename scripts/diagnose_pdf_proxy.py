"""Diagnose the /pdf proxy failure.

Picks a sample crop_id from the DB, gets its source_url, then tries to fetch
it the same way the proxy does. Reports whether the URL is reachable from
this machine (if yes, the failure is Railway-network specific).
"""
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
    url = os.environ["SUPABASE_URL"].strip()
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not service_key:
        raise SystemExit("SUPABASE_SERVICE_ROLE_KEY required")
    client = create_client(url, service_key)

    # Get 3 sample crops with non-empty source_url
    res = (
        client.table("crops")
        .select("crop_id, source_url, pdf_path")
        .not_.is_("source_url", "null")
        .neq("source_url", "")
        .limit(3)
        .execute()
    )
    rows = res.data or []
    if not rows:
        print("No crops with source_url found.")
        return

    print(f"Testing {len(rows)} sample source_urls from the DB:\n")
    for r in rows:
        cid = r["crop_id"]
        src = r["source_url"]
        print(f"crop_id={cid}")
        print(f"  source_url={src}")
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
                data = resp.read()
            dt = time.time() - t0
            head = data[:4]
            print(f"  OK status=200 bytes={len(data)} dt={dt:.2f}s head={head!r}")
        except Exception as exc:
            dt = time.time() - t0
            print(f"  FAIL dt={dt:.2f}s exc={type(exc).__name__}: {exc}")
        print()


if __name__ == "__main__":
    main()
