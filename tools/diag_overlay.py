"""Diagnostic: evaluate the site-bbox hypothesis and a neighbourhood around it,
and save visual overlays so the result can be checked by eye."""
import math, os, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from solve_georef import load_osm, raster_osm, image_mask, merc_y, WORK, SRC, FULL_W, FULL_H

DIV = 20
cw, ch = FULL_W // DIV, FULL_H // DIV


def iou_at(A, polys, lines, s, ox, oy, margin=0.25):
    """IoU of OSM water (rasterised over the sheet window + margin) vs image mask."""
    w = int(cw * (1 + 2 * margin)) + 4
    h = int(ch * (1 + 2 * margin)) + 4
    ox0 = ox - cw * margin / s
    oy0 = oy + ch * margin / s
    B = raster_osm(s, ox0, oy0, w, h, polys, lines, line_w=3)
    canvas = np.zeros_like(A)
    y_off = int(round(ch * margin))
    x_off = int(round(cw * margin))
    yy0, xx0 = y_off, x_off
    yy1, xx1 = min(h, y_off + ch), min(w, x_off + cw)
    canvas[0:yy1 - yy0, 0:xx1 - xx0] = B[yy0:yy1, xx0:xx1]
    inter = np.logical_and(canvas, A).sum()
    union = np.logical_or(canvas, A).sum()
    return (inter / union if union else 0.0)


def overlay(A, polys, lines, s, ox, oy, out, margin=0.25, tag=""):
    w = int(cw * (1 + 2 * margin)) + 4
    h = int(ch * (1 + 2 * margin)) + 4
    ox0 = ox - cw * margin / s
    oy0 = oy + ch * margin / s
    B = raster_osm(s, ox0, oy0, w, h, polys, lines, line_w=3)
    rgb = np.zeros((h, w, 3), np.uint8)
    # plan water = red, osm water = cyan, overlap = white
    Bm = np.zeros((h, w), bool)
    y_off, x_off = int(round(ch * margin)), int(round(cw * margin))
    Bm[y_off:y_off + min(h - y_off, ch), x_off:x_off + min(w - x_off, cw)] = \
        B[y_off:y_off + min(h - y_off, ch), x_off:x_off + min(w - x_off, cw)]
    Am = np.zeros((h, w), bool)
    Am[y_off:y_off + ch, x_off:x_off + cw] = A
    rgb[..., 0] = Am * 255
    rgb[..., 1] = Bm * 255
    rgb[..., 2] = Bm * 255
    Image.fromarray(rgb).save(out)
    return out


def main():
    polys, lines = load_osm()
    A = image_mask(DIV)
    print(f"canvas {cw}x{ch}  image water px {A.sum()}")

    # H0: sheet edge <-> site bbox edge, north-up
    bl_lat, bl_lon = 22.763306, 72.178109
    tr_lat, tr_lon = 23.357406, 72.860675
    s_h0 = FULL_W / (tr_lon - bl_lon) / DIV
    ox_h0 = bl_lon
    oy_h0 = merc_y(tr_lat)
    print(f"H0: s={s_h0:.1f} px/deg(div{DIV})  ox={ox_h0:.5f}  oy={oy_h0:.5f}")

    results = []
    for s_mult in np.arange(0.80, 1.35, 0.04):
        s = s_h0 * s_mult
        for dlon in np.arange(-0.10, 0.101, 0.025):
            for dlat in np.arange(-0.10, 0.101, 0.025):
                ox = ox_h0 + dlon
                oy = oy_h0 + dlat
                v = iou_at(A, polys, lines, s, ox, oy)
                results.append((v, s, ox, oy))
    results.sort(reverse=True)
    print("\ntop candidates (iou, s, ox, oy):")
    for v, s, ox, oy in results[:10]:
        print(f"  iou={v:.4f} s={s:8.2f} ox={ox:.5f} oy={oy:.5f}")

    v, s, ox, oy = results[0]
    print(f"\nBEST iou={v:.4f}")
    print(overlay(A, polys, lines, s, ox, oy, os.path.join(WORK, "overlay_best.png"), tag="best"))
    print(overlay(A, polys, lines, s_h0, ox_h0, oy_h0, os.path.join(WORK, "overlay_h0.png"), tag="h0"))


if __name__ == "__main__":
    main()
