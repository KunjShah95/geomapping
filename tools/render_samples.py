"""Render sample pages from the NEW DP book so we can see what we are working with."""
import fitz, os, sys

BASE = r"C:\ideabot internships\geomapping\NEW DP BOOK AUDA"
OUT = r"C:\ideabot internships\geomapping\work\newdp"
os.makedirs(OUT, exist_ok=True)

JOBS = [
    ("Key Map-New.pdf", "keymap.png", 2200),
    ("DP PLAN ALAG ALAG PAGE WITH ORIGINAL NUMBER\\A Key Map.pdf", "sheets_keymap.png", 2200),
    ("DP PLAN ALAG ALAG PAGE WITH ORIGINAL NUMBER\\D-13.pdf", "sheet_D13.png", 2600),
    ("DP PLAN ALAG ALAG PAGE WITH ORIGINAL NUMBER\\H-05.pdf", "sheet_H05.png", 2600),
    ("AUDA DP EACH  VILLAGE MAP\\ADRAJ.pdf", "village_ADRAJ.png", 2600),
    ("AUDA DP EACH  VILLAGE MAP\\THALTEJ.pdf", "village_THALTEJ.png", 2600),
    ("AUDA FULL DP 4 PART\\DP 2021 AUDA Final-1.pdf", "block1_full.png", 2600),
    ("BOOK TITLE.pdf", "booktitle.png", 1400),
]

for rel, name, maxw in JOBS:
    p = os.path.join(BASE, rel)
    if not os.path.exists(p):
        print("MISSING", rel)
        continue
    doc = fitz.open(p)
    pg = doc[0]
    zoom = maxw / max(pg.rect.width, 1)
    zoom = min(zoom, 4.0)
    pix = pg.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    dest = os.path.join(OUT, name)
    pix.save(dest)
    print(f"{name:22s} {pix.width}x{pix.height}  page={pg.rect}  crop={pg.cropbox}  mediabox={pg.mediabox}")
    doc.close()
