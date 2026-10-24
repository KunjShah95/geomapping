"""Is the residual in the fidelity check a sub-pixel shift, or a real difference?

Cross-correlates the built tile against a fresh render of the same ground and
reports where the peak sits. A peak at (0,0) means the two are the same
drawing to within a fraction of a pixel, and the pixel-value differences are
antialiasing, not content.
"""
import os, sys
import fitz, numpy as np
from PIL import Image
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dp_georef as G
import dp_build_tiles as B

ROOT = G.ROOT
z = int(sys.argv[1]); picks = [(int(a), int(b)) for a, b in (x.split(",") for x in sys.argv[2:])]
blocks, grid = G.build_blocks()
dls = {}
for q, b in blocks.items():
    doc = fitz.open(b.path); dls[q] = (doc, doc[0].get_displaylist(), b, doc[0].cropbox)

for tx, ty in picks:
    with Image.open(os.path.join(ROOT, "tiles_dp2021", str(z), str(tx), f"{ty}.webp")) as im:
        built = np.asarray(im.convert("L")).astype(np.float64)
    lon_l = tx*256/(256*2**z)*360.0-180.0; lon_r = (tx+1)*256/(256*2**z)*360.0-180.0
    lat_t = B.py2lat(ty*256, z); lat_b = B.py2lat((ty+1)*256, z)
    covers = []
    for q, (doc, dl, b, cropbox) in dls.items():
        if not (b.lon_min-0.2 <= lon_l and lon_r <= b.lon_max+0.2): continue
        if not (b.lat_min-0.2 <= lat_b and lat_t <= b.lat_max+0.2): continue
        mx, my = G.INV.transform(np.array([lon_l, lon_r, lon_r, lon_l]),
                                 np.array([lat_t, lat_t, lat_b, lat_b]))
        ux = (mx-b.e0)/b.s+b.llx; uy = (my-b.n0)/b.s+b.ury
        q4 = list(zip(ux, uy))
        w = max(p[0] for p in q4)-min(p[0] for p in q4); h = max(p[1] for p in q4)-min(p[1] for p in q4)
        arr = B.render_tile(dl, q4, 256/max(min(w, h), 1e-6), cropbox)
        if arr is not None: covers.append((B.ink_fraction(arr), arr))
    merged, _ = B.merge_covers(covers)
    fresh = merged.astype(np.float64).mean(axis=2)
    P = np.zeros((512, 512)); P[:256, :256] = built
    S = np.zeros((512, 512)); S[:256, :256] = fresh
    cc = np.fft.irfft2(np.fft.rfft2(S)*np.fft.rfft2(P[::-1, ::-1]), s=(512, 512))
    idx = np.unravel_index(np.argmax(cc), cc.shape)
    dy = idx[0] if idx[0] <= 256 else idx[0]-512
    dx = idx[1] if idx[1] <= 256 else idx[1]-512
    # also the correlation at zero shift, as a fraction of the peak
    z0 = cc[0, 0]/cc[idx]
    print(f"z{z}/{tx}/{ty}: peak offset dy={dy} dx={dx} px   corr at zero shift = {z0:.4f} of peak")
