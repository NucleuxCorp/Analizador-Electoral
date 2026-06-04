"""
setup_local_portal.py — One-shot local setup for the multi-user labeling portal.

Run ONCE after creating your Supabase project:
    python setup_local_portal.py

What this does:
  1. Creates .env with your credentials
  2. Verifies connection to Supabase
  3. Checks that SQL migrations were already applied (schema + RPC)
  4. Prints next steps

Prerequisites:
  - Supabase project created at https://supabase.com
  - SQL schemas applied via Supabase SQL Editor:
      scripts/supabase_schema.sql   (tables: crops, labels, assignments)
      scripts/assign_next_crop.sql  (PL/pgSQL function)
  - data/labels_v2/crops/index.jsonl exists (run: python prepare_v2_export.py)
"""
import os
import sys
from pathlib import Path


def prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{label}{suffix}: ").strip()
    return val or default


def check_supabase_connection(url: str, anon_key: str) -> bool:
    try:
        from supabase import create_client
        client = create_client(url, anon_key)
        # Try a simple query
        resp = client.table("crops").select("crop_id", count="exact").limit(1).execute()
        print(f"  Connection OK — crops table found ({resp.count} rows)")
        return True
    except Exception as exc:
        err = str(exc)
        if "relation" in err.lower() or "does not exist" in err.lower():
            print(f"  ERROR: Tables not found. Run supabase_schema.sql first.")
        else:
            print(f"  ERROR: {exc}")
        return False


def check_rpc(url: str, anon_key: str) -> bool:
    try:
        from supabase import create_client
        client = create_client(url, anon_key)
        # The RPC should exist even with an empty crops table
        client.rpc("assign_next_crop", {"p_annotator_id": "00000000-0000-0000-0000-000000000000"}).execute()
        print("  RPC assign_next_crop OK")
        return True
    except Exception as exc:
        err = str(exc)
        if "function" in err.lower() or "does not exist" in err.lower():
            print("  ERROR: RPC not found. Run assign_next_crop.sql first.")
            return False
        # Other errors (e.g., no rows) are fine — function exists
        print("  RPC assign_next_crop OK (no rows is expected on empty table)")
        return True


def write_env(url: str, anon_key: str, secret_key: str, labels_dir: str) -> None:
    env_path = Path(".env")
    content = f"""# Analizador de Elecciones — Local dev portal config
SUPABASE_URL={url}
SUPABASE_ANON_KEY={anon_key}
SECRET_KEY={secret_key}
LABELS_DIR={labels_dir}
USE_SUPABASE_STORAGE=false
FAKE_USER_ID=dev-user
"""
    env_path.write_text(content, encoding="utf-8")
    print(f"  .env written: {env_path.resolve()}")


def main():
    print("=" * 60)
    print("  Analizador de Elecciones — Local Portal Setup")
    print("=" * 60)
    print()
    print("You need the following from your Supabase project dashboard:")
    print("  Settings > API > Project URL")
    print("  Settings > API > anon (public) key")
    print()

    url        = prompt("Supabase Project URL (https://xxx.supabase.co)")
    anon_key   = prompt("Supabase anon key")
    secret_key = prompt("Flask SECRET_KEY (any random string)", default="change-me-in-prod")
    labels_dir = prompt("LABELS_DIR", default="data/labels_v2")

    if not url.startswith("https://"):
        print("ERROR: URL must start with https://")
        sys.exit(1)

    print()
    print("[1] Writing .env ...")
    write_env(url, anon_key, secret_key, labels_dir)

    print()
    print("[2] Checking Supabase connection ...")
    ok = check_supabase_connection(url, anon_key)
    if not ok:
        print()
        print("ACTION NEEDED: Apply the SQL migrations in Supabase SQL Editor:")
        print("  1. Open: https://supabase.com/dashboard/project/YOUR_PROJECT/sql")
        print("  2. Paste and run: scripts/supabase_schema.sql")
        print("  3. Paste and run: scripts/assign_next_crop.sql")
        print("  4. Re-run this script.")
        sys.exit(1)

    print()
    print("[3] Checking assign_next_crop RPC ...")
    check_rpc(url, anon_key)

    print()
    index = Path(labels_dir) / "crops" / "index.jsonl"
    if index.exists():
        count = sum(1 for _ in open(index, encoding="utf-8") if _.strip())
        print(f"[4] Crops index found: {count} crops in {index}")
        print()
        print("Ready to seed the database. Run:")
        print(f"  python scripts/migrate_to_supabase.py")
    else:
        print(f"[4] Crops index NOT found: {index}")
        print("  Run first: python prepare_v2_export.py")

    print()
    print("=" * 60)
    print("  Setup complete! Start the portal with:")
    print("  python main.py label")
    print()
    print("  Or with hot-reload:")
    print("  flask --app src.modules.labeler.wsgi run --port 5000 --debug")
    print("=" * 60)


if __name__ == "__main__":
    main()
