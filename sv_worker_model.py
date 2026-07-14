"""Worker que acepta modelo y chunk. Guarda resultados en JSONL."""
import sys, json, os
from pathlib import Path

chunk = int(sys.argv[1])
total = int(sys.argv[2])
model_path = sys.argv[3]  # "models/digit_classifier.pth" o "model_a"

# Cambiar el modelo que carga SegmentedEngine
os.environ["SV_MODEL_PATH"] = model_path

sys.path.insert(0, ".")
# Forzar recarga del motor
import src.modules.analyzer.ocr_engines as ocre
# Monkey-patch temporal
original_path = ocre._CNNClassifier.MODEL_PATH
ocre._CNNClassifier.MODEL_PATH = model_path

from extract_sv_smart import process_one

pdfs = sorted(Path("data/pdfs_segunda_vuelta").rglob("*.pdf"))
chunk_size = max(1, len(pdfs) // total)
start = chunk * chunk_size
end = min(start + chunk_size, len(pdfs))
my_pdfs = pdfs[start:end]

model_name = Path(model_path).stem
out = Path(f"data/sv_{model_name}_{chunk}.jsonl")
with out.open("w", encoding="utf-8") as f:
    for p in my_pdfs:
        r = process_one(p)
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
print(f"{model_name} chunk {chunk}: {len(my_pdfs)} PDFs -> {out.name}")
