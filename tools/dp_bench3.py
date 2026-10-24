"""Benchmark per-tile rendering from a cached DisplayList at z17 scale."""
import math
import os
import sys
import time

import fitz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dp_georef import load_views, build_index  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(ROOT, "NEW DP BOOK AUDA")
REL = "AUDA FULL DP 4 PART\\DP 2021 AUDA Final-1.pdf"

views = load_views()
idx = build_index(views)
t = idx[REL][0]["transform"]
doc = fitz.open(os.path.join(BASE, REL))
page = doc[0]
mb = page.mediabox
dl = page.get_displaylist()
print("displaylist", dl.rect)

latm = (t.lat_min + t.lat_max) / 2
mpp_x = (t.lon_max - t.lon_min) * 111320.0 * math.cos(math.radians(latm)) / (t.urx - t.llx)
mpp_y = (t.lat_max - t.lat_min) * 110990.0 / abs(t.ury - t.lly)
z = 17
mpp_px = 156543.03392 * math.cos(math.radians(latm)) / (2 ** z)
zoom = ((mpp_x + mpp_y) / 2) / mpp_px
print(f"zoom {zoom:.4f}  tile covers {256/zoom:.1f} pt")

for label, lon0, lat0 in [("dense_city", 72.58, 23.03), ("farmland", 72.30, 23.20)]:
    ux, uy = t.ground_to_page(lon0, lat0)
    n = 120
    t0 = time.time()
    for i in range(n):
        cx = ux + (i % 20) * (256 / zoom)
        cy = uy + (i // 20) * (256 / zoom)
        r = fitz.Rect(cx, cy, cx + 256 / zoom, cy + 256 / zoom)
        pix = dl.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=r, alpha=False)
        _ = pix.samples
    dt = time.time() - t0
    print(f"  {label}: {n} tiles in {dt:.2f}s -> {dt/n*1000:.1f} ms/tile  "
          f"(60k tiles would take {dt/n*60800/60:.1f} min)")
doc.close()
