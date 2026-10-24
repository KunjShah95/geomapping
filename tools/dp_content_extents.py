"""Where does each part sheet's land-use content actually lie?

The /VP says the whole canvas maps to the sheet's quadrant. But the drawings
do not fill the canvas. This measures the ground extent of each sheet's own
land-use polygons (the big filled areas, ignoring the paper background, the
legend swatches and the stray off-sheet marks), and compares it with what
the /VP claims.

Output: work/newdp/content_extents.json
"""
import json, os, sys
import fitz, numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dp_georef as G

ROOT = G.ROOT
out = {}
blocks, grid = G.build_blocks()
for q, b in blocks.items():
    doc = fitz.open(b.path); page = doc[0]
    mb = page.mediabox
    areas = []
    for d in page.get_drawings():
        if d["type"] != "f" or not d.get("fill"): continue
        r = d["rect"]
        a = r.width * r.height
        if a < 4000 or a > mb.width*mb.height*0.5: continue   # skip background + legend
        areas.append(r)
    if not areas:
        print(q, "no polygons"); doc.close(); continue
    x0 = min(r.x0 for r in areas); x1 = max(r.x1 for r in areas)
    y0 = min(r.y0 for r in areas); y1 = max(r.y1 for r in areas)
    ux0, uy0 = x0, mb.y1 - y1
    ux1, uy1 = x1, mb.y1 - y0
    g = b.ground_of_rect(ux0, uy0, ux1, uy1)
    # how much of the canvas do the land-use polygons cover?
    cov = sum(r.width*r.height for r in areas)/(mb.width*mb.height)
    print(f"{q}: {len(areas):6d} polygons, canvas cover {cov*100:5.1f}%")
    print(f"     canvas x {x0:8.0f}..{x1:8.0f}  y {y0:8.0f}..{y1:8.0f}  "
          f"(canvas is {mb.width:.0f} x {mb.height:.0f})")
    print(f"     ground  lon {g[0]:.5f}..{g[2]:.5f}  lat {g[1]:.5f}..{g[3]:.5f}")
    print(f"     /VP     lon {b.lon_min:.5f}..{b.lon_max:.5f}  lat {b.lat_min:.5f}..{b.lat_max:.5f}")
    out[q] = {"n_polygons": len(areas), "canvas_cover": cov,
              "canvas_x": [x0, x1], "canvas_y": [y0, y1],
              "ground": list(g), "vp": [b.lon_min, b.lat_min, b.lon_max, b.lat_max]}
    doc.close()
json.dump(out, open(os.path.join(ROOT, "work", "newdp", "content_extents.json"), "w", encoding="utf-8"), indent=1)
print("wrote work/newdp/content_extents.json")
