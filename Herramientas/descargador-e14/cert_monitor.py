#!/usr/bin/env python3
"""Certificate tripwire for Registraduría hosts.

Minimal monitor that performs socket-level TLS handshakes (verify disabled)
and records leaf certificate SHA-256 fingerprints. Logs incidents to a
Spanish-language markdown log.
"""
from __future__ import annotations
import hashlib
import json
import socket
import ssl
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

HOSTS = [
    "escrutiniospresidente2026.registraduria.gov.co",
    "e14segundavueltapresidente.registraduria.gov.co",
    "e14segundavueltapresidentet.registraduria.gov.co",
]

OUT_FILE = Path("Herramientas") / "descargador-e14" / "cert_fingerprints.json"
LOG_FILE = Path("Herramientas") / "descargador-e14" / "cert_monitor_log.md"

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


def get_cert_info(host: str, port: int = 443, timeout: int = 10):
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with _ctx.wrap_socket(sock, server_hostname=host) as ssock:
            der = ssock.getpeercert(binary_form=True)
            info = ssock.getpeercert()
    fp = hashlib.sha256(der).hexdigest()
    subject = info.get('subject')
    issuer = info.get('issuer')
    notAfter = info.get('notAfter')
    return fp, subject, issuer, notAfter


def load_db() -> Dict[str, Dict]:
    if OUT_FILE.exists():
        return json.loads(OUT_FILE.read_text(encoding='utf-8'))
    return {}


def save_db(db: Dict[str, Dict]):
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding='utf-8')


def append_log(host, old, new, subject, issuer, notAfter):
    ts = datetime.now(timezone.utc).isoformat()
    lines = []
    lines.append(f"# Cambio detectado: {ts}")
    lines.append("")
    lines.append(f"Host: {host}")
    lines.append(f"Antes: {old}")
    lines.append(f"Ahora: {new}")
    lines.append(f"Subject: {subject}")
    lines.append(f"Issuer: {issuer}")
    lines.append(f"NotAfter: {notAfter}")
    lines.append("")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n\n")


def run_once():
    db = load_db()
    changed = False
    for host in HOSTS:
        try:
            fp, subject, issuer, notAfter = get_cert_info(host)
        except Exception as e:
            print(f"{host}: error fetching cert: {e}")
            continue
        rec = db.get(host)
        if not rec:
            db[host] = {
                "fingerprint": fp,
                "first_seen": datetime.now(timezone.utc).isoformat(),
                "last_seen": datetime.now(timezone.utc).isoformat(),
                "subject": subject,
                "issuer": issuer,
                "notAfter": notAfter,
            }
            print(f"{host}: new fingerprint {fp}")
            changed = True
        else:
            if rec.get("fingerprint") != fp:
                old = rec.get("fingerprint")
                print(f"ALERT {host}: fingerprint changed\n  old={old}\n  new={fp}")
                append_log(host, old, fp, subject, issuer, notAfter)
                rec.update({
                    "fingerprint": fp,
                    "last_seen": datetime.now(timezone.utc).isoformat(),
                    "subject": subject,
                    "issuer": issuer,
                    "notAfter": notAfter,
                })
                changed = True
            else:
                rec["last_seen"] = datetime.now(timezone.utc).isoformat()
                print(f"{host}: fingerprint {fp} (unchanged)")
    if changed:
        save_db(db)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--loop", type=int, default=0, help="Loop interval seconds (0=off)")
    args = parser.parse_args()
    if args.once:
        run_once()
    elif args.loop and args.loop > 0:
        import time
        while True:
            run_once()
            time.sleep(args.loop)
    else:
        parser.print_help()
