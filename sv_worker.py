import sys, json
sys.path.insert(0,'.')
from extract_sv_smart import process_one
from pathlib import Path

chunk = int(sys.argv[1])
total = int(sys.argv[2])
pdfs = sorted(Path("data/pdfs_segunda_vuelta").rglob("*.pdf"))
my_pdfs = pdfs[chunk::total]  # every Nth pdf

out = Path(f"data/sv_chunk_{chunk}.jsonl")
with out.open("w", encoding="utf-8") as f:
    for p in my_pdfs:
        r = process_one(p)
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
print(f"Chunk {chunk}: {len(my_pdfs)} PDFs -> {out.name}")
