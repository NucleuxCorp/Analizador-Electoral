#!/usr/bin/env python3
"""
Download + analyze E-14 second round PDFs in parallel chunks.

Each chunk worker:
1. Downloads a subset of PDFs
2. Runs the form_extractor on each PDF as it arrives
3. Outputs analysis results to JSONL

Usage:
    python run_sv_chunk.py <chunk_idx> <total_chunks> [--dept 01]

The script reads the local allTransmissionCodes JSON (already downloaded),
splits the published mesas into N chunks, and each worker processes its chunk.
"""
import sys, os, json, time, urllib.request, urllib.error
from pathlib import Path
from collections import Counter

ROOT = Path("D:/Nucleux/tools/Analizador de Elecciones")
os.chdir(ROOT)

# Add src to path
sys.path.insert(0, str(ROOT))

BASE = "https://e14segundavueltapresidentt.registraduria.gov.co"
PDF_BASE = f"{BASE}/assets/temis/pdf"
PDF_DIR = ROOT / "data" / "pdfs_segunda_vuelta"
ANALYSIS_DIR = ROOT / "data" / "analysis_segunda_vuelta"
TRANSMISSION_LOCAL = ROOT / "data" / "allTransmissionCodes_segunda.json"

chunk_idx = int(sys.argv[1]) if len(sys.argv) > 1 else 0
total_chunks = int(sys.argv[2]) if len(sys.argv) > 2 else 20
dept_filter = ""
for i, arg in enumerate(sys.argv):
    if arg == "--dept" and i + 1 < len(sys.argv):
        dept_filter = sys.argv[i + 1].zfill(2)

PDF_DIR.mkdir(parents=True, exist_ok=True)
ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)

# 1. Load transmission JSON
if not TRANSMISSION_LOCAL.exists():
    print(f"[chunk {chunk_idx}] ERROR: {TRANSMISSION_LOCAL} not found. Run download_transmission_json.py first.")
    sys.exit(1)

data = json.loads(TRANSMISSION_LOCAL.read_text(encoding="utf-8"))
published = data.get("data", {}).get("status3", {}).get("nodes", [])
print(f"[chunk {chunk_idx}] Total published: {len(published)}", flush=True)

# Filter by dept
if dept_filter:
    published = [m for m in published if str(m.get("idDepartmentCode", "")).zfill(2) == dept_filter]
    print(f"[chunk {chunk_idx}] Filtered by dept {dept_filter}: {len(published)}", flush=True)

# 2. Split into chunks
chunk_size = len(published) // total_chunks + 1
my_start = chunk_idx * chunk_size
my_end = min(my_start + chunk_size, len(published))
my_mesas = published[my_start:my_end]
print(f"[chunk {chunk_idx}] Processing {len(my_mesas)} mesas (offset {my_start}-{my_end})", flush=True)

# 3. Download + analyze each

def build_pdf_url(mesa):
    dept = str(mesa.get("idDepartmentCode", "")).zfill(2)
    mpio = str(mesa.get("municipalityCode", "")).zfill(3)
    zone = str(mesa.get("idZoneCode", "")).zfill(3)
    stand = str(mesa.get("standCode", "")).zfill(2)
    mesa_num = str(mesa.get("numberStand", "")).zfill(3)
    filename = mesa.get("expectedName", "")
    if not filename:
        return ""
    return f"{PDF_BASE}/{dept}/{mpio}/{zone}/{stand}/{mesa_num}/PRE/{filename}"

def download_pdf(url, dest, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = resp.read()
            if data[:4] == b"%PDF":
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                return True
            return False
        except urllib.error.HTTPError as e:
            if e.code == 404 or attempt == retries - 1:
                return False
            time.sleep(2 ** attempt)
        except Exception:
            if attempt == retries - 1:
                return False
            time.sleep(2 ** attempt)
    return False

# Analysis output file for this chunk
analysis_file = ANALYSIS_DIR / f"chunk_{chunk_idx}.jsonl"

# Try to import the analyzer
analyzer_available = False
try:
    from src.modules.analyzer.form_extractor import extract_fields
    from src.modules.analyzer.ocr_engines import get_engine
    analyzer_available = True
    print(f"[chunk {chunk_idx}] Analyzer imported OK", flush=True)
except Exception as e:
    print(f"[chunk {chunk_idx}] Analyzer not available ({e}) — download only", flush=True)

downloaded = 0
analyzed = 0
not_found = 0
failed = 0
t0 = time.time()

with analysis_file.open("w", encoding="utf-8") as af:
    for idx, mesa in enumerate(my_mesas):
        trans_id = mesa.get("idTransmissionCode", "")
        url = build_pdf_url(mesa)

        if not url:
            continue

        dept = str(mesa.get("idDepartmentCode", "")).zfill(2)
        mesa_num = str(mesa.get("numberStand", "")).zfill(3)
        dest = PDF_DIR / dept / f"{trans_id}_M{mesa_num}.pdf"

        # Skip if already downloaded
        if dest.exists():
            downloaded += 1
        else:
            ok = download_pdf(url, dest)
            if ok:
                downloaded += 1
            elif not_found == 0 and failed == 0:
                not_found += 1
                continue
            else:
                failed += 1
                continue

        # Analyze if analyzer is available
        if analyzer_available and dest.exists():
            try:
                # Use the SegmentedEngine (CNN + EasyOCR)
                engine = get_engine("segmented")
                result = extract_fields(dest, engine)

                # Add mesa metadata
                result["mesa_id"] = trans_id
                result["dept"] = dept
                result["mpio"] = str(mesa.get("municipalityCode", "")).zfill(3)
                result["zone"] = str(mesa.get("idZoneCode", "")).zfill(2)
                result["stand"] = str(mesa.get("standCode", "")).zfill(2)
                result["mesa_num"] = mesa_num
                result["pdf_path"] = str(dest.relative_to(ROOT))

                af.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")
                analyzed += 1
            except Exception as e:
                print(f"[chunk {chunk_idx}] Analysis error for {trans_id}: {e}", flush=True)

        if (idx + 1) % 10 == 0:
            elapsed = time.time() - t0
            rate = (idx + 1) / elapsed if elapsed > 0 else 0
            print(f"[chunk {chunk_idx}] {idx+1}/{len(my_mesas)} "
                  f"downloaded={downloaded} analyzed={analyzed} "
                  f"not_found={not_found} failed={failed} ({rate:.1f}/s)", flush=True)

print(f"\n[chunk {chunk_idx}] DONE in {time.time()-t0:.0f}s", flush=True)
print(f"  Downloaded: {downloaded}", flush=True)
print(f"  Analyzed:   {analyzed}", flush=True)
print(f"  Not found:  {not_found}", flush=True)
print(f"  Failed:     {failed}", flush=True)
print(f"  Analysis:   {analysis_file.name}", flush=True)