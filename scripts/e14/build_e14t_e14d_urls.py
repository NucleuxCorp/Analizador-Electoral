"""Rebuild E14T + E14D URLs from the REAL server transmission codes."""
import json, uuid
from pathlib import Path

INDEX = Path("data/allTransmissionCodes_segunda_index.json")
OUTPUT_T = Path("data/e14t_sv_urls.jsonl")
OUTPUT_D = Path("data/e14d_sv_urls.jsonl")
BASE_T = "https://e14segundavueltapresidentet.registraduria.gov.co"
BASE_D = "https://e14segundavueltapresidente.registraduria.gov.co"

data = json.loads(INDEX.read_text())
nodes = []
for sk in ["status3", "status11"]:
    ns = data.get("data", {}).get(sk, {}).get("nodes", [])
    if isinstance(ns, list):
        nodes.extend(ns)

print(f"Total nodes: {len(nodes)}")

# Filter: dedup by (dept,mpio,zone,stand,mesa) — keep first node per mesa
seen_keys = set()
filtered = []
for n in nodes:
    if not n.get("expectedName"):
        continue
    key = f"{n.get('idDepartmentCode','')}.{n.get('municipalityCode','')}.{n.get('idZoneCode','')}.{n.get('standCode','')}.{n.get('numberStand','')}"
    if key in seen_keys:
        continue
    seen_keys.add(key)
    filtered.append(n)

print(f"Filtered (corp=001, status 3/11): {len(filtered)}")

for label, base, output in [("E14T", BASE_T, OUTPUT_T), ("E14D", BASE_D, OUTPUT_D)]:
    seen = set()
    with open(output, "w", encoding="utf-8") as f:
        for n in filtered:
            dept = str(n.get("idDepartmentCode", "")).zfill(2)
            mpio = str(n.get("municipalityCode", "")).zfill(3)
            zone = str(n.get("idZoneCode", "")).zfill(3)
            stand = str(n.get("standCode", "")).zfill(2)
            mesa_num = str(n.get("numberStand", "")).zfill(3)
            expected = n.get("expectedName", "")

            # Dedup: skip if same (dept,mpio,zone,stand,mesa) already processed
            key = f"{dept}.{mpio}.{zone}.{stand}.{mesa_num}"
            if key in seen:
                continue
            seen.add(key)

            url = f"{base}/assets/temis/pdf/{dept}/{mpio}/{zone}/{stand}/{mesa_num}/PRE/{expected}?uuid={uuid.uuid4()}"
            f.write(json.dumps({
                "cod_dept": dept, "cod_mpio": mpio,
                "zona": zone, "puesto": stand, "mesa": mesa_num,
                "pdf_url": url,
            }, ensure_ascii=False) + "\n")

    with open(output) as f:
        lines = sum(1 for _ in f)
    print(f"{label}: {lines} URLs -> {output}")

print("\nSample URLs (proba con uuid=11111111-2222-3333-4444-555555555555):")
with open(OUTPUT_T) as f:
    for i, line in enumerate(f):
        if i >= 3: break
        print(f"  {json.loads(line)['pdf_url'][:120]}")
