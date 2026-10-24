"""Put a built tile next to a fresh render of the same ground, side by side."""
import os, sys
import fitz, numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dp_georef as G
import dp_build_tiles as B

ROOT = G.ROOT
z, tx, ty = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
blocks, grid = G.build_blocks()

lon_l = tx * 256 / (256 * 2 ** z) * 360.0 - 180.0
lon_r = (tx + 1) * 256 / (256 * 2 ** z) * 360.0 - 180.0
lat_t = B.py2lat(ty * 256, z)
lat_b = B.py2lat((ty + 1) * 256, z)
print(f"tile z{z}/{tx}/{ty}  lon {lon_l:.5f}..{lon_r:.5f}  lat {lat_b:.5f}..{lat_t:.5f}")

built = Image.open(os.path.join(ROOT, "tiles_dp2021", str(z), str(tx), f"{ty}.webp")).convert("RGB")

out = []
for q, b in blocks.items():
    doc = fitz.open(b.path)
    page = doc[0]
    dl = page.get_displaylist()
    crop = page.cropbox
    mx, my = G.INV.transform(np.array([lon_l, lon_r, lon_r, lon_l]),
                             np.array([lat_t, lat_t, lat_b, lat_b]))
    ux = (mx - b.e0) / b.s + b.llx
    uy = (my - b.n0) / b.s + b.ury
    q4 = list(zip(ux, uy))
    w_pt = max(p[0] for p in q4) - min(p[0] for p in q4)
    h_pt = max(p[1] for p in q4) - min(p[1] for p in q4)
    zoom = 256 / max(min(w_pt, h_pt), 1e-6)
    arr = B.render_tile(dl, q4, zoom, crop)
    if arr is None:
        print(f"  block {q}: no coverage")
        continue
    img = Image.fromarray(arr)
    ink = float((arr.min(axis=2) < 245).mean())
    print(f"  block {q}: ink {ink*100:6.2f}%")
    out.append((q, img, ink))
    doc.close()

sheet = Image.new("RGB", (256 * (len(out) + 1), 276), (255, 255, 255))
d = ImageDraw.Draw(sheet)
sheet.paste(built, (0, 20))
d.text((4, 4), "BUILT TILE", fill=(0, 0, 0))
for i, (q, img, ink) in enumerate(out):
    sheet.paste(img, (256 * (i + 1), 20))
    d.text((256 * (i + 1) + 4, 4), f"source {q} ({ink*100:.1f}% ink)", fill=(0, 0, 0))
p = os.path.join(ROOT, "work", "newdp", "tile_diff.png")
sheet.save(p)
print("wrote", p)
