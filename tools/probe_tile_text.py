"""Probe: OCR one plan tile at high zoom and report every detection with its
glyph height, so we can separate big place names from tiny plot numbers."""
import os, sys, math
os.environ.setdefault("OMP_NUM_THREADS", "1")
import numpy as np
from PIL import Image
Image.MAX_IMAGE_PIXELS = None
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "work")
RAW = os.path.join(WORK, "source.rgb")
FULL_W, FULL_H = 27200, 25600

tx, ty = int(sys.argv[1]), int(sys.argv[2])
SCALE = float(sys.argv[3]) if len(sys.argv) > 3 else 4.0
TILE = 1024

from rapidocr import RapidOCR
ocr = RapidOCR()

mm = np.memmap(RAW, dtype=np.uint8, mode="r", shape=(FULL_H, FULL_W, 3))
x0, y0 = tx * TILE, ty * TILE
x1, y1 = min(x0 + TILE, FULL_W), min(y0 + TILE, FULL_H)
crop = Image.fromarray(np.asarray(mm[y0:y1, x0:x1]))
big = np.asarray(crop.resize((int((x1 - x0) * SCALE), int((y1 - y0) * SCALE)), Image.LANCZOS))
out = ocr(big, box_thresh=0.30, unclip_ratio=2.4, text_score=0.30)
r = out[0] if isinstance(out, tuple) else out
if r is None or r.txts is None:
    print("no detections")
    sys.exit()
rows = []
for box, txt, sc in zip(r.boxes, r.txts, r.scores):
    h = (max(p[1] for p in box) - min(p[1] for p in box)) / SCALE
    w = (max(p[0] for p in box) - min(p[0] for p in box)) / SCALE
    cx = sum(p[0] for p in box) / len(box) / SCALE + x0
    cy = sum(p[1] for p in box) / len(box) / SCALE + y0
    rows.append((h, w, txt, sc, cx, cy))
rows.sort(reverse=True)
print(f"tile {tx},{ty}  region=({x0},{y0})-({x1},{y1})  scale={SCALE}  dets={len(rows)}")
for h, w, txt, sc, cx, cy in rows:
    print(f"  h={h:6.1f} w={w:6.1f} s={sc:.2f}  ({cx:7.0f},{cy:7.0f})  {txt!r}")
