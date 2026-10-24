"""Solve image->WebMercator transform by matching the plan's water mask to OSM water.

Model (north-up, isotropic scale s, no rotation):
    px = (lon - ox) * s
    py = (oy - mercY(lat)) * s

For each candidate scale we rasterise OSM water into a window centred on the OSM
extent and 1.5x the size of the image canvas, then FFT-correlate to find the best
translation. Multi-resolution: coarse at 1/40, refine at 1/20 and 1/10.
"""
import json, math, os, sys
import numpy as np
from PIL import Image, ImageDraw

Image.MAX_IMAGE_PIXELS = None
WORK = r"C:\ideabot internships\geomapping\work"
SRC = os.path.join(WORK, "source.jpeg")
FULL_W, FULL_H = 27200, 25600
EXPAND = 1.6


def merc_y(lat):
    return math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def load_osm(min_area_deg2=2e-5):
    BBOX = (22.70, 72.10, 23.42, 72.92)
    with open(os.path.join(WORK, "osm_water.json", encoding="utf-8"), encoding="utf-8") as f:
        d = json.load(f)
    polys, lines = [], []
    dropped = 0
    for e in d["elements"]:
        g = e.get("geometry")
        if not g:
            continue
        pts = [(p["lon"], p["lat"]) for p in g if "lon" in p]
        if len(pts) < 2:
            continue
        # clip stray geometry that extends far outside the query window
        clipped = [p for p in pts if BBOX[0] <= p[1] <= BBOX[2] and BBOX[1] <= p[0] <= BBOX[3]]
        if len(clipped) < 2:
            dropped += 1
            continue
        if len(clipped) < len(pts):
            dropped += 1
        pts = clipped
        tags = e.get("tags", {})
        wt = tags.get("waterway")
        if wt in ("river", "riverbank"):
            lines.append(pts)
        elif tags.get("natural") == "water" or tags.get("landuse") == "reservoir":
            lons = [p[0] for p in pts]
            lats = [p[1] for p in pts]
            w = (max(lons) - min(lons)) * math.cos(math.radians(np.mean(lats)))
            h = max(lats) - min(lats)
            if len(pts) >= 3 and w * h >= min_area_deg2:
                polys.append(pts)
    print(f"   dropped {dropped} features extending outside the query window")
    return polys, lines


def raster_osm(s, ox, oy, W, H, polys, lines, line_w=2):
    img = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(img)

    def P(lon, lat):
        return ((lon - ox) * s, (oy - merc_y(lat)) * s)

    for pts in polys:
        d.polygon([P(lo, la) for lo, la in pts], fill=255)
    for pts in lines:
        d.line([P(lo, la) for lo, la in pts], fill=255, width=line_w)
    return np.asarray(img) > 127


def best_iou(A, Bm):
    """Best translation overlap of Bm onto A via FFT. Returns (iou, ay0, ax0)."""
    A = A.astype(np.float32)
    B = Bm.astype(np.float32)
    fh, fw = A.shape
    fh2, fw2 = fh * 2, fw * 2
    corr = np.fft.irfft2(np.fft.rfft2(A, (fh2, fw2)) *
                         np.fft.rfft2(B[::-1, ::-1], (fh2, fw2)), (fh2, fw2))
    dy = int(np.argmax(corr.max(axis=1)))
    dx = int(np.argmax(corr[dy]))
    sh, sw = B.shape
    ay0, ax0 = dy - sh + 1, dx - sw + 1
    canvas = np.zeros_like(A)
    y0, x0 = max(0, ay0), max(0, ax0)
    y1, x1 = min(fh, ay0 + sh), min(fw, ax0 + sw)
    if y1 > y0 and x1 > x0:
        canvas[y0:y1, x0:x1] = B[y0 - ay0:y1 - ay0, x0 - ax0:x1 - ax0]
    inter = np.logical_and(canvas, A).sum()
    union = np.logical_or(canvas, A).sum()
    return (inter / union if union else 0.0), ay0, ax0


def image_mask(div):
    im = Image.open(SRC).convert("RGB")
    w, h = FULL_W // div, FULL_H // div
    a = np.asarray(im.resize((w, h), Image.BILINEAR)).astype(np.int16)
    R, G, B = a[..., 0], a[..., 1], a[..., 2]
    return (B > R + 12) & (B > 170) & (G > R)


def search(div, s_lo, s_hi, s_step, polys, lines, pad=3, verbose=True):
    A = image_mask(div)
    # thicken to tolerate small misalignment
    from PIL import ImageFilter
    Ap = np.asarray(Image.fromarray((A * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(5))) > 127
    cw, ch = A.shape[1], A.shape[0]
    W, H = int(cw * EXPAND), int(ch * EXPAND)

    lons = [p[0] for pl in polys + lines for p in pl]
    lats = [p[1] for pl in polys + lines for p in pl]
    x0, x1 = min(lons), max(lons)
    ytop, ybot = merc_y(max(lats)), merc_y(min(lats))
    mx, my = (x0 + x1) / 2, (ytop + ybot) / 2
    if verbose:
        print(f"   osm extent: lon {x0:.4f}..{x1:.4f}  mercY {ybot:.4f}..{ytop:.4f}"
              f"  span {(x1-x0):.4f} x {(ytop-ybot):.4f} deg")

    best = None
    for s in np.arange(s_lo, s_hi, s_step):
        sw = (x1 - x0) * s
        sh = (ytop - ybot) * s
        if sw > W * 0.98 or sh > H * 0.98 or sw < cw * 0.15 or sh < ch * 0.15:
            continue
        ox0, oy0 = mx - (W / 2) / s, my + (H / 2) / s
        Bm = raster_osm(s, ox0, oy0, W, H, polys, lines, line_w=max(1, int(pad * s / 900)))
        if Bm.sum() < 100:
            continue
        iou, ay, ax = best_iou(Ap, Bm)
        ox = ox0 + ax / s
        oy = oy0 - ay / s
        if best is None or iou > best["iou"]:
            best = dict(iou=iou, s=float(s), ox=ox, oy=oy)
            if verbose:
                print(f"   div={div} s={s:7.1f} iou={iou:.4f} ox={ox:.5f} oy={oy:.5f}  <== best")
    return best


def main():
    polys, lines = load_osm()
    print(f"osm: {len(polys)} water polygons, {len(lines)} river lines")

    print("\n[stage 1] coarse")
    b = search(40, 1100, 3400, 40, polys, lines)
    print("stage1:", b)
    if not b:
        sys.exit("no solution")

    print("\n[stage 2] refine")
    b2 = search(20, b["s"] * 0.93, b["s"] * 1.07, 8, polys, lines)
    print("stage2:", b2)

    print("\n[stage 3] fine")
    b3 = search(10, b2["s"] * 0.985, b2["s"] * 1.015, 3, polys, lines)
    print("stage3:", b3)

    best = b3 or b2 or b
    print("\n=== SOLUTION ===")
    print(json.dumps(best, indent=2))

    s = best["s"] * 10          # convert px/deg at div=10 -> px/deg at full res
    ox, oy = best["ox"], best["oy"]
    lon_tl, lon_br = ox, ox + FULL_W / s
    lat_br = math.degrees(2 * math.atan(math.exp(math.radians(oy))) - math.pi / 2)
    lat_tl = math.degrees(2 * math.atan(math.exp(math.radians(oy - FULL_H / s))) - math.pi / 2)
    res = dict(s_px_per_deg=s, lon_min=lon_tl, lon_max=lon_br,
               lat_max=lat_tl, lat_min=lat_br,
               width_deg=lon_br - lon_tl, height_deg=oy - (oy - FULL_H / s))
    print("\nfull-sheet bounds:", json.dumps(res, indent=2))
    km_per_deg_lon = 111.32 * math.cos(math.radians((lat_tl + lat_br) / 2))
    print(f"\nsheet covers ~{(lon_br-lon_tl)*km_per_deg_lon:.1f} km E-W")
    print(f"ground resolution ~{(lon_br-lon_tl)*km_per_deg_lon*1000/FULL_W:.2f} m/px")
    with open(os.path.join(WORK, "georef.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)


if __name__ == "__main__":
    main()
