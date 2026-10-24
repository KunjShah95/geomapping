"""Are the white gaps in the pyramid real, or did the builder drop tiles it
should have kept?

The builder omits a tile when nothing in it is darker than 245 - that is the
"let the basemap through" rule. But the sheets carry a fine layer of road
lines and labels over white paper, and a tile that holds only a hairline can
sit just above that threshold and be dropped.

This takes the gaps in the built pyramid and renders the same ground from the
source PDF, then reports how much ink is really there.
"""
import math
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dp_georef as G  # noqa: E402
import dp_build_tiles as B  # noqa: E402

ROOT = G.ROOT
TILES = os.path.join(ROOT, "tiles_dp2021")
Z = 12


def main():
    blocks, grid = G.build_blocks()
    import fitz
    dls = {}
    for q, b in blocks.items():
        doc = fitz.open(b.path)
        dls[q] = (doc, doc[0].get_displaylist(), b, doc[0].cropbox)

    # every z12 tile that is MISSING from the pyramid but inside the book
    lon_min = min(b.lon_min for b in blocks.values())
    lon_max = max(b.lon_max for b in blocks.values())
    lat_min = min(b.lat_min for b in blocks.values())
    lat_max = max(b.lat_max for b in blocks.values())
    tx0 = int(B.lon2px(lon_min, Z) // 256)
    tx1 = int(B.lon2px(lon_max, Z) // 256)
    ty0 = int(B.lat2py(lat_max, Z) // 256)
    ty1 = int(B.lat2py(lat_min, Z) // 256)

    missing = []
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            p = os.path.join(TILES, str(Z), str(tx), f"{ty}.webp")
            if not os.path.exists(p):
                missing.append((tx, ty))
    print(f"{len(missing)} z12 tiles inside the book are missing from the pyramid")

    # render each missing tile from the source and measure the ink
    worst = []
    for tx, ty in missing:
        lon_l = tx * 256 / (256 * 2 ** Z) * 360.0 - 180.0
        lon_r = (tx + 1) * 256 / (256 * 2 ** Z) * 360.0 - 180.0
        lat_t = B.py2lat(ty * 256, Z)
        lat_b = B.py2lat((ty + 1) * 256, Z)
        best = None
        for q, (doc, dl, b, cropbox) in dls.items():
            if not (b.lon_min - 0.1 <= lon_l and lon_r <= b.lon_max + 0.1):
                continue
            if not (b.lat_min - 0.1 <= lat_b and lat_t <= b.lat_max + 0.1):
                continue
            mx, my = G.INV.transform(np.array([lon_l, lon_r, lon_r, lon_l]),
                                     np.array([lat_t, lat_t, lat_b, lat_b]))
            ux = (mx - b.e0) / b.s + b.llx
            uy = (my - b.n0) / b.s + b.ury
            q4 = list(zip(ux, uy))
            w_pt = max(p[0] for p in q4) - min(p[0] for p in q4)
            h_pt = max(p[1] for p in q4) - min(p[1] for p in q4)
            zoom = 256 / max(min(w_pt, h_pt), 1e-6)
            arr = B.render_tile(dl, q4, zoom, cropbox)
            if arr is None:
                continue
            frac = float((arr.min(axis=2) < 245).mean())
            if best is None or frac > best[0]:
                best = (frac, q)
        if best:
            worst.append((best[0], tx, ty, best[1]))

    worst.sort(reverse=True)
    inked = [w for w in worst if w[0] > 0.0005]
    print(f"{len(inked)} of those tiles DO have ink in the source "
          f"({len(inked)/max(1,len(worst)):.0%})")
    print("the inkiest of them:")
    for frac, tx, ty, q in worst[:15]:
        print(f"   z{Z}/{tx}/{ty}  ink {frac*100:6.3f}%  from block {q}")
    if inked:
        print("\n=> the blank-tile rule is dropping tiles that carry content.")


if __name__ == "__main__":
    main()
