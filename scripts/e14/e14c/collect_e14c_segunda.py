#!/usr/bin/env python3
"""Descarga PDFs E-14C segunda vuelta desde API de escrutinio."""
import sys, json, time, urllib.request
from pathlib import Path

BASE = "https://escrutinios2vueltapresidente2026.registraduria.gov.co"
INDEX = Path("data/index_e14c_segunda.json")
PDF_DIR = Path("data/pdfs_e14c_segunda")
CHECKPOINT = Path("data/e14c_segunda_progress.json")

def fetch(url):
    for a in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except:
            if a == 2: return None
            time.sleep(2**a)

def download(url, dest):
    if dest.exists(): return True
    for a in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                d = r.read()
            if d[:4] == b"%PDF":
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(d)
                return True
        except:
            if a == 2: return False
            time.sleep(2**a)

def main():
    data = json.loads(INDEX.read_text("utf-8"))
    done = set(json.loads(CHECKPOINT.read_text("utf-8"))) if CHECKPOINT.exists() else set()
    dept = sys.argv[1] if len(sys.argv) > 1 else None
    
    keys = sorted(k for k in data if "001/" in k and k.endswith("/mesas/") and
                  (not dept or f"/{dept}/" in k))
    
    print(f"Puestos: {len(keys)}")
    downloaded = 0
    
    for i, k in enumerate(keys):
        if k in done: continue
        
        mesa_data = fetch(BASE + "/" + k + data[k])
        if not mesa_data: continue
        
        for m in mesa_data:
            if m.get("digitalizado") == 1 and m.get("escrutado"):
                u = BASE + m["nombre_archivo"]
                d = PDF_DIR / m["nombre_archivo"].replace("/docs/E14/", "").replace("/", "_")
                if download(u, d): downloaded += 1
        
        done.add(k)
        if (i+1) % 10 == 0 or i == len(keys)-1:
            CHECKPOINT.write_text(json.dumps(list(done)))
            print(f"  {i+1}/{len(keys)} | {downloaded} PDFs")
    
    print(f"Total: {downloaded} PDFs en {PDF_DIR}")

if __name__ == "__main__":
    main()
