"""Check grid detection for problem PDFs with X header issues."""
import sys; sys.path.insert(0, '.')
from debug_sv.grid_detector_v2 import *
import fitz, cv2, numpy as np

for pdf in ['data/pdfs_e14c_segunda/60_001_02_03_E14_PRE_60_001_002_00_03_008_6948.pdf',
            'data/pdfs_e14c_segunda/15_169_02_01_E14_PRE_15_169_002_00_01_020_5803.pdf']:
    doc = fitz.open(pdf); p = doc[0]
    pix = p.get_pixmap(matrix=fitz.Matrix(300/72,300/72))
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height,pix.width,pix.n)
    img = cv2.cvtColor(img,cv2.COLOR_RGB2BGR); doc.close()
    gray = cv2.cvtColor(img,cv2.COLOR_BGR2GRAY)
    
    vl = detect_gray_v_lines(gray)
    hl_raw = detect_gray_h_lines(gray)
    hl = process_h_lines(hl_raw, gray.shape[0])
    gr = build_grid(vl, hl)
    lb = label_rows_by_structure(gr, gray.shape[0])
    
    print(pdf.split('/')[-1][:50])
    print('  v_lines:', vl)
    print('  h_lines:', hl)
    print('  rows:', len(lb))
    for r in lb[:10]:
        label = r.get('label','?')
        print('    %s y=%d-%d' % (label, r['top'], r['bot']))
    print()
