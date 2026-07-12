#!/usr/bin/env python3
"""Analyze second round PDFs locally - counts, OCR, fraud detection."""
import sys, os, json, time
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent
PDF_DIR = ROOT / "data" / "pdfs_segunda_vuelta"
OUTPUT_DIR = ROOT / "data" / "analysis_segunda_vuelta"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Import OCR engine
sys.path.insert(0, str(ROOT))
try:
    from src.modules.analyzer.ocr_engines import SegmentedEngine
    engine = SegmentedEngine()
    print(f"OCR Engine: SegmentedEngine (CNN={'yes' if engine._cnn else 'no'})")
    HAVE_OCR = True
except Exception as e:
    print(f"OCR Engine not available: {e}")
    HAVE_OCR = False

def analyze_pdf(pdf_path):
    """Extract fields from a second-round PDF.
    
    Second round layout: 2 candidates + blank vote.
    Uses the same OCR engine but different coordinates.
    For now, we just OCR the digit cells and count.
    """
    result = {
        "path": str(pdf_path.relative_to(ROOT)),
        "size_bytes": pdf_path.stat().st_size,
        "valid_pdf": pdf_path.read_bytes()[:4] == b"%PDF",
    }
    
    if not result["valid_pdf"]:
        return result
    
    # Read PDF with PyMuPDF
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
        result["pages"] = len(doc)
        result["page_dims"] = [f"{p.rect.width:.0f}x{p.rect.height:.0f}" for p in doc]
        
        if HAVE_OCR:
            # OCR first page - extract all digit crops
            page = doc[0]
            pix = page.get_pixmap(dpi=300)
            img_data = pix.tobytes("png")
            
            import cv2
            import numpy as np
            nparr = np.frombuffer(img_data, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            if img is not None:
                h, w = img.shape[:2]
                result["img_dims"] = f"{w}x{h}"
                
                # Use SegmentedEngine to find and read digit cells
                # This uses the existing segment_digits method
                digits = SegmentedEngine.segment_digits(img)
                result["digit_cells_found"] = len(digits)
                
                ocr_results = []
                for d in digits[:15]:  # max 15 cells
                    digit, conf = engine._read_single_digit(d)
                    ocr_results.append({"digit": digit, "conf": conf})
                result["ocr_digits"] = ocr_results
        
        doc.close()
    except Exception as e:
        result["error"] = str(e)
    
    return result

def main():
    pdfs = sorted(PDF_DIR.rglob("*.pdf"))
    print(f"PDFs found: {len(pdfs)}")
    
    results_file = OUTPUT_DIR / "analysis_results.jsonl"
    count = 0
    errors = 0
    
    t0 = time.time()
    for pdf_path in pdfs:
        result = analyze_pdf(pdf_path)
        with open(results_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")
        
        if result.get("valid_pdf"):
            count += 1
        else:
            errors += 1
        
        if (count + errors) % 50 == 0:
            el = time.time() - t0
            print(f"  {count+errors}/{len(pdfs)} ({el:.0f}s)")
    
    el = time.time() - t0
    print(f"\nAnalysis complete in {el:.0f}s")
    print(f"  Valid PDFs: {count}")
    print(f"  Errors: {errors}")
    print(f"  Results: {results_file}")

if __name__ == "__main__":
    main()