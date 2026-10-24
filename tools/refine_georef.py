"""Refine the plan georeference by pattern-search maximising water-mask IoU.

Full affine model (image px -> WebMercator degrees, and its inverse):
    forward:  px = ox + ( mx*c - my*s ) * sx
              py = oy - ( mx*s + my*c ) * sy
    inverse:  mx = ( (px-ox)/sx * c + (oy-py)/sy * s )
              my = ( -(px-ox)/sx * s + (oy-py)/sy * c )

H0 (site bbox) is the seed. OSM features are first clipped to the H0 footprint so
the Narmada (which the AUDA plan does not cover) does not pollute the objective.
"""
import json, math, os, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from solve_georef import load_osm, image_mask, merc_y, WORK, FULL_W, FULL_H

DIV_LIST = [20, 10]


def clip_to_footprint(polys, lines, ox, oy, sx, sy, margin=0.12):
    """Keep only features mostly inside the sheet footprint (with margin)."""
    # sheet corners in mercator
    corners = [(0, 0), (FULL_W, 0), (0, FULL_H), (FULL_W, FULL_H)]
    mx = []
    my = []
    for px, py in corners:
        mx.append(ox + px / sx)
        my.append(oy - py / sy)
    lon0, lon1 = min(mx) - margin, max(mx) + margin
    y0, y1 = min(my) - margin, max(my) + margin

    def inside(lon, lat):
        return lon0 <= lon <= lon1 and y0 <= merc_y(lat) <= y1

    def frac_ok(pts):
        n = sum(1 for lo, la in pts if inside(lo, la))
        return n / len(pts) >= 0.5

    p2 = [p for p in polys if frac_ok(p)]
    l2 = [p for p in lines if frac_ok(p)]
    return p2, l2


def raster(sx, sy, c, s, ox, oy, W, H, polys, lines, line_w=3):
    img = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(img)

    def P(lon, lat):
        my = merc_y(lat)
        dx, dy = lon - ox, my - oy
        px = (dx * c - dy * s) * sx
        py = -(dx * s + dy * c) * sy
        return (px, py)

    for pts in polys:
        d.polygon([P(lo, la) for lo, la in pts], fill=255)
    for pts in lines:
        d.line([P(lo, la) for lo, la in pts], fill=255, width=line_w)
    return np.asarray(img) > 127


_MASK_CACHE = {}


def cached_mask(div, dilate):
    key = (div, dilate)
    if key not in _MASK_CACHE:
        A = image_mask(div)
        if dilate:
            A = np.asarray(Image.fromarray((A * 255).astype(np.uint8))
                           .filter(ImageFilter.MaxFilter(dilate * 2 + 1))) > 127
        _MASK_CACHE[key] = A
    return _MASK_CACHE[key]


def score(params, div, polys, lines, margin=0.15, dilate=3):
    sx, sy, c, s, ox, oy = params
    cw, ch = FULL_W // div, FULL_H // div
    sxd, syd = sx / div, sy / div
    W, H = int(cw * (1 + 2 * margin)) + 6, int(ch * (1 + 2 * margin)) + 6
    A = cached_mask(div, dilate)
    Bm = raster(sxd, syd, c, s, ox, oy, W, H, polys, lines,
                line_w=max(1, int(round(dilate * sxd / 1000.0))))
    yo, xo = int(round(ch * margin)), int(round(cw * margin))
    canvas = np.zeros((ch, cw), bool)
    y1, x1 = min(H - yo, ch), min(W - xo, cw)
    if y1 > 0 and x1 > 0:
        canvas[0:y1, 0:x1] = Bm[yo:yo + y1, xo:xo + x1]
    inter = np.logical_and(canvas, A).sum()
    union = np.logical_or(canvas, A).sum()
    return inter / union if union else 0.0


def save_overlay(params, div, polys, lines, out, margin=0.15, line_w=3):
    sx, sy, c, s, ox, oy = params
    cw, ch = FULL_W // div, FULL_H // div
    sxd, syd = sx / div, sy / div
    W, H = int(cw * (1 + 2 * margin)) + 6, int(ch * (1 + 2 * margin)) + 6
    A = image_mask(div)
    Bm = raster(sxd, syd, c, s, ox, oy, W, H, polys, lines, line_w=line_w)
    Am = np.zeros((ch, cw), bool)
    Bc = np.zeros((ch, cw), bool)
    yo, xo = int(round(ch * margin)), int(round(cw * margin))
    y1, x1 = min(H - yo, ch), min(W - xo, cw)
    if y1 > 0 and x1 > 0:
        Am[0:y1, 0:x1] = A[:y1, :x1]
        Bc[0:y1, 0:x1] = Bm[yo:yo + y1, xo:xo + x1]
    rgb = np.zeros((ch, cw, 3), np.uint8)
    rgb[..., 0] = Am * 255
    rgb[..., 1] = Bc * 255
    rgb[..., 2] = Bc * 255
    Image.fromarray(rgb).save(out)


def pattern_search(p0, div, polys, lines, steps, iters=60, margin=0.15):
    p = list(p0)
    best = score(p, div, polys, lines, margin)
    print(f"  start iou={best:.4f}  p={[round(v,6) for v in p]}")
    for it in range(iters):
        improved = False
        for i in range(6):
            for sgn in (1, -1):
                q = list(p)
                q[i] += sgn * steps[i]
                v = score(q, div, polys, lines, margin)
                if v > best + 1e-6:
                    best, p, improved = v, q, True
        if not improved:
            steps = [x * 0.5 for x in steps]
            if max(steps) < 1e-7:
                break
        if it % 10 == 0:
            print(f"   it{it:3d} iou={best:.4f}")
    return p, best


def main():
    bl_lat, bl_lon = 22.763306, 72.178109
    tr_lat, tr_lon = 23.357406, 72.860675
    ox0 = bl_lon
    oy0 = merc_y(tr_lat)
    sx0 = FULL_W / (tr_lon - bl_lon)
    sy0 = FULL_H / (oy0 - merc_y(bl_lat))

    polys, lines = load_osm()
    polys, lines = clip_to_footprint(polys, lines, ox0, oy0, sx0, sy0)
    print(f"footprint-clipped: {len(polys)} polygons, {len(lines)} lines")

    p = [sx0, sy0, 1.0, 0.0, ox0, oy0]
    save_overlay(p, 20, polys, lines, os.path.join(WORK, "ov_seed.png"))

    print("\n[div=20] pattern search")
    p, b20 = pattern_search(p, 20, polys, lines,
                            steps=[sx0 * 0.01, sy0 * 0.01, 0.004, 0.004, 0.004, 0.004])
    save_overlay(p, 20, polys, lines, os.path.join(WORK, "ov_div20.png"))
    print(f"div20 final iou={b20:.4f}")

    print("\n[div=10] pattern search")
    p, b10 = pattern_search(p, 10, polys, lines,
                            steps=[sx0 * 0.0015, sy0 * 0.0015, 0.0008, 0.0008, 0.0008, 0.0008])
    save_overlay(p, 10, polys, lines, os.path.join(WORK, "ov_div10.png"), line_w=2)
    print(f"div10 final iou={b10:.4f}")

    sx, sy, c, s, ox, oy = p
    lon_tl = ox
    lon_br = ox + (FULL_W / sx) * c + (-(0 / sy)) * 0
    # recompute corners properly via inverse model
    def inv(px, py):
        a = (px) / sx
        b = (oy - py) / sy
        return (a * c + b * s, -a * s + b * c)
    m = [inv(px, py) for px, py in ((0, 0), (FULL_W, 0), (0, FULL_H), (FULL_W, FULL_H))]
    lons = [q[0] for q in m]
    mys = [q[1] for q in m]
    lon_min, lon_max = min(lons), max(lons)
    y_top, y_bot = max(mys), min(mys)
    lat_max = math.degrees(2 * math.atan(math.exp(math.radians(y_top))) - math.pi / 2)
    lat_min = math.degrees(2 * math.atan(math.exp(math.radians(y_bot))) - math.pi / 2)

    res = dict(
        model="affine mercator, forward px=(lon-ox)*sx*c ... (see tools/refine_georef.py)",
        sx=sx, sy=sy, cos=c, sin=s, ox=ox, oy=oy,
        rotation_deg=math.degrees(math.atan2(s, c)),
        lon_min=lon_min, lon_max=lon_max, lat_min=lat_min, lat_max=lat_max,
        iou=b10,
    )
    kpd = 111.32 * math.cos(math.radians((lat_min + lat_max) / 2))
    res["width_km"] = (lon_max - lon_min) * kpd
    res["m_per_px"] = (lon_max - lon_min) * kpd * 1000 / FULL_W
    print("\n=== REFINED GEOREFERENCE ===")
    print(json.dumps(res, indent=2))
    with open(os.path.join(WORK, "georef.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)


if __name__ == "__main__":
    main()
