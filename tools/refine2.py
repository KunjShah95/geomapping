"""Refine the AUDA plan georeference against the Sabarmati river only.

The plan sheet draws thousands of small ponds that OSM mostly lacks, which swamps
an all-water IoU objective. Eroding the image mask keeps only the thick, reliable
features (the river and the big reservoirs); the OSM side is reduced to major
rivers. A pattern search then fits scale, rotation and offset.

Forward model (lon/lat -> image px):
    px = ox + ( mx*c - my*s ) * sx
    py = oy - ( mx*s + my*c ) * sy
"""
import json, math, os, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from solve_georef import load_osm, image_mask, merc_y, WORK, FULL_W, FULL_H

MASKS = {}


def thick_mask(div, erode_px, dilate_px):
    key = (div, erode_px, dilate_px)
    if key in MASKS:
        return MASKS[key]
    A = image_mask(div).astype(np.uint8) * 255
    im = Image.fromarray(A)
    if erode_px:
        im = im.filter(ImageFilter.MinFilter(erode_px * 2 + 1))
    if dilate_px:
        im = im.filter(ImageFilter.MaxFilter(dilate_px * 2 + 1))
    out = np.asarray(im) > 127
    MASKS[key] = out
    return out


def raster(sx, sy, c, s, ox, oy, W, H, lines, line_w=3):
    img = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(img)
    for pts in lines:
        d.line([((lo - ox) * c * sx + (merc_y(la) - oy) * s * sx,
                 -(lo - ox) * s * sy - (merc_y(la) - oy) * c * sy) for lo, la in pts],
               fill=255, width=line_w)
    return np.asarray(img) > 127


def prep(lines, div, margin, thicken):
    """Pre-project to unit mercator space so rasterisation is cheap."""
    return lines


def score(p, div, lines, margin=0.12, erode=2, dilate=2, line_w=4):
    sx, sy, c, s, ox, oy = p
    cw, ch = FULL_W // div, FULL_H // div
    sxd, syd = sx / div, sy / div
    W, H = int(cw * (1 + 2 * margin)) + 6, int(ch * (1 + 2 * margin)) + 6
    A = thick_mask(div, erode, dilate)
    Bm = raster(sxd, syd, c, s, ox, oy, W, H, lines, line_w=line_w)
    yo, xo = int(round(ch * margin)), int(round(cw * margin))
    canvas = np.zeros((ch, cw), bool)
    y1, x1 = min(H - yo, ch), min(W - xo, cw)
    if y1 > 0 and x1 > 0:
        canvas[0:y1, 0:x1] = Bm[yo:yo + y1, xo:xo + x1]
    inter = np.logical_and(canvas, A).sum()
    union = np.logical_or(canvas, A).sum()
    return inter / union if union else 0.0


def inverse(p, px, py):
    sx, sy, c, s, ox, oy = p
    a = px / sx
    b = (oy - py) / sy
    return (ox + a * c + b * s, oy - a * s + b * c)


def bounds(p):
    pts = [inverse(p, px, py) for px, py in
           ((0, 0), (FULL_W, 0), (0, FULL_H), (FULL_W, FULL_H))]
    lons = [q[0] for q in pts]
    mys = [q[1] for q in pts]
    lat_of = lambda y: math.degrees(2 * math.atan(math.exp(math.radians(y))) - math.pi / 2)
    return (min(lons), max(lons), lat_of(min(mys)), lat_of(max(mys)))


def search(p0, div, lines, steps, iters=40, **kw):
    p = list(p0)
    best = score(p, div, lines, **kw)
    print(f"  start iou={best:.4f}")
    for it in range(iters):
        improved = False
        for i in range(6):
            for sgn in (1, -1):
                q = list(p)
                q[i] += sgn * steps[i]
                v = score(q, div, lines, **kw)
                if v > best + 1e-6:
                    best, p, improved = v, q, True
        if not improved:
            steps = [x * 0.5 for x in steps]
            if max(steps) < 1e-8:
                break
        if it % 8 == 0:
            print(f"   it{it:3d} iou={best:.4f}")
    return p, best


def overlay(p, div, lines, out, margin=0.12, line_w=4):
    sx, sy, c, s, ox, oy = p
    cw, ch = FULL_W // div, FULL_H // div
    W, H = int(cw * (1 + 2 * margin)) + 6, int(ch * (1 + 2 * margin)) + 6
    A = thick_mask(div, 0, 0)
    Bm = raster(sx / div, sy / div, c, s, ox, oy, W, H, lines, line_w=line_w)
    yo, xo = int(round(ch * margin)), int(round(cw * margin))
    rgb = np.zeros((ch, cw, 3), np.uint8)
    y1, x1 = min(H - yo, ch), min(W - xo, cw)
    rgb[..., 0][0:y1, 0:x1] = (A[:y1, :x1] * 255)
    rgb[..., 1][0:y1, 0:x1] = (Bm[yo:yo + y1, xo:xo + x1] * 255)
    rgb[..., 2][0:y1, 0:x1] = (Bm[yo:yo + y1, xo:xo + x1] * 255)
    Image.fromarray(rgb).save(out)


def main():
    bl_lat, bl_lon = 22.763306, 72.178109
    tr_lat, tr_lon = 23.357406, 72.860675
    ox0, oy0 = bl_lon, merc_y(tr_lat)
    sx0 = FULL_W / (tr_lon - bl_lon)
    sy0 = FULL_H / (oy0 - merc_y(bl_lat))

    polys, lines = load_osm()
    print(f"osm: {len(polys)} polys, {len(lines)} river lines")
    lon_lo, lon_hi = bl_lon - 0.05, tr_lon + 0.05
    y_lo, y_hi = merc_y(bl_lat) - 0.05, oy0 + 0.05
    keep = [l for l in lines if any(lon_lo <= lo <= lon_hi and y_lo <= merc_y(la) <= y_hi for lo, la in l)]
    print(f"rivers overlapping sheet bbox: {len(keep)}")
    # keep only the longest few (the Sabarmati and its main branches)
    keep.sort(key=lambda l: -len(l))
    keep = [l for l in keep if len(l) >= 15][:12]
    print(f"using {len(keep)} major river ways, total {sum(len(l) for l in keep)} vertices")

    p = [sx0, sy0, 1.0, 0.0, ox0, oy0]
    print("\n[div=20]")
    p, b = search(p, 20, keep, [sx0 * .01, sy0 * .01, .003, .003, .003, .003],
                  iters=30, erode=2, dilate=2, line_w=5)
    overlay(p, 20, keep, os.path.join(WORK, "ov2_div20.png"))
    print(f"  iou={b:.4f}")

    print("\n[div=10]")
    p, b = search(p, 10, keep, [sx0 * .002, sy0 * .002, .0006, .0006, .0006, .0006],
                  iters=30, erode=1, dilate=1, line_w=3)
    overlay(p, 10, keep, os.path.join(WORK, "ov2_div10.png"), line_w=3)
    print(f"  iou={b:.4f}")

    sx, sy, c, s, ox, oy = p
    lon_min, lon_max, lat_min, lat_max = bounds(p)
    kpd = 111.32 * math.cos(math.radians((lat_min + lat_max) / 2))
    res = dict(
        model="px = ox + (mercX*cos - mercY*sin)*sx ; py = oy - (mercX*sin + mercY*cos)*sy",
        sx=sx, sy=sy, cos=c, sin=s, ox=ox, oy=oy,
        rotation_deg=math.degrees(math.atan2(s, c)),
        lon_min=lon_min, lon_max=lon_max, lat_min=lat_min, lat_max=lat_max,
        width_km=(lon_max - lon_min) * kpd,
        m_per_px=(lon_max - lon_min) * kpd * 1000 / FULL_W,
        river_iou=b,
    )
    print("\n=== REFINED ===")
    print(json.dumps(res, indent=2))
    with open(os.path.join(WORK, "georef.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)


if __name__ == "__main__":
    main()
