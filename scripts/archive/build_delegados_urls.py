"""Rebuild Delegados URLs using presidente domain + transmission hashes."""
import json
from pathlib import Path
import urllib.request, ssl

BASE = "https://e14segundavueltapresidente.registraduria.gov.co"
OUTPUT = Path("data/delegados_v2_urls.jsonl")

data = json.loads(Path("data/allTransmissionCodes_segunda.json").read_text())
nodes = []
for sk in ["status3", "status11"]:
    nodes.extend(data.get("data", {}).get(sk, {}).get("nodes", []))

print(f"Total nodes: {len(nodes)}")

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

ok = fail = 0
with open(OUTPUT, "w", encoding="utf-8") as f:
    for i, n in enumerate(nodes):
        dept = n["idDepartmentCode"].zfill(2)
        mpio = n["municipalityCode"].zfill(3)
        zone = n["idZoneCode"].zfill(2)
        stand = n["standCode"].zfill(2)
        mesa = n["idTransmissionCode"]
        expected = n["expectedName"]
        
        url = f"{BASE}/assets/temis/pdf/{dept}/{mpio}/{zone}/{stand}/{mesa}/PRE/{expected}"
        
        record = {
            "cod_dept": dept, "cod_mpio": mpio,
            "zona": zone, "puesto": stand, "mesa": mesa,
            "pdf_url": url,
        }
        f.write(json.dumps(record, ensure_ascii=False) + "\n")

        # Test first 5
        if i < 5:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                r = urllib.request.urlopen(req, timeout=8, context=ctx)
                d = r.read()
                if d[:4] == b"%PDF":
                    print(f"  OK  {i+1}: {url[-60:]}")
                    ok += 1
                else:
                    print(f"  NOT {i+1}: {url[-60:]}")
                    fail += 1
            except Exception as e:
                print(f"  ERR {i+1}: {url[-60:]}")
                fail += 1

print(f"\nGenerated: {len(nodes)} -> {OUTPUT}")
if ok + fail > 0:
    print(f"Tested {ok+fail}: OK={ok} FAIL={fail}")
