"""Find large water bodies in the plan and match them to OSM to make control points.

WHY WATER
---------
The place-name route (tools/build_gcp.py) needs OCR to read names, which is
slow and error-prone. Water is better: a reservoir like Nalsarovar or Kankaria
is an unmistakable blob in the plan, an exact polygon in OSM, and there are
several of them spread right across the sheet.

THE METHOD
----------
1. Threshold the plan's fill colours to get a water mask (the plan draws
   water in a distinct pale blue).
2. Label the connected components and keep the big ones - a reservoir, not a
   pond.
3. For every OSM water polygon in the sheet's bbox, predict where its centroid
   would land under the current georeference, then find the plan blob whose
   centroid is closest.
4. VERIFY by shape: align the OSM polygon and the plan blob on their centroids
   and compare them by IoU. A real match scores high; a coincidence does not.
   Only matches that pass the IoU test become control points.

Step 4 is what makes this safe. Without it, this is a guess generator; with
it, every accepted point has been confirmed against the actual shape of the
water body in both sources.

Output: work/gcp_water.csv  (px,py,lon,lat,name) - merged with any other GCP
source before the affine fit.
"""
import argparse, json, math, os
import numpy as np
from PIL import Image
from scipy import ndimage

Image.MAX_IMAGE_PIXELS = None
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "work")
FULL_W, FULL_H = 27200, 25600

BL = (22.763306, 72.178109)
TR = (23.357406, 72.860675)
DIV = 4                      # plan downsample for the mask
MIN_AREA_PLAN = 40           # plan px at DIV (40*16 = 640 full px)


def merc_y(lat):
    return math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def inv_merc_y(my):
    return math.degrees(2 * math.atan(math.exp(math.radians(my))) - math.pi / 2)


def h0_px(lat, lon):
    SX = FULL_W / (TR[1] - BL[1])
    SY = FULL_H / (merc_y(TR[0]) - merc_y(BL[0]))
    return ((lon - BL[1]) * SX, (merc_y(TR[0]) - merc_y(lat)) * SY)


def water_mask():
    """Plan water mask at 1/DIV resolution."""
    im = Image.open(os.path.join(WORK, "source.jpeg")).convert("RGB")
    w = FULL_W // DIV
    h = FULL_H // DIV
    a = np.asarray(im.resize((w, h), Image.BILINEAR)).astype(np.int16)
    R, G, B = a[..., 0], a[..., 1], a[..., 2]
    # the plan's water fill is a pale blue: clearly blue-dominant, fairly bright
    m = (B > R + 18) & (B > 150) & (G > R + 5)
    del im, a
    return m


def plan_blobs(mask):
    lab, n = ndimage.label(mask)
    objs = ndimage.find_objects(lab)
    out = []
    for i, sl in enumerate(objs, 1):
        if sl is None:
            continue
        area = int((lab[sl] == i).sum())
        if area < MIN_AREA_PLAN:
            continue
        ys, xs = sl
        sub = (lab[sl] == i)
        cy, cx = ndimage.center_of_mass(sub)
        out.append(dict(label=i, area=area,
                        cx=cx + xs.start, cy=cy + ys.start,
                        bbox=(xs.start, ys.start, xs.stop, ys.stop)))
    return lab, out


def polygon_of(lab, blob):
    ys, xs = np.nonzero(lab == blob["label"])
    return xs, ys


def osm_polygons():
    """OSM water polygons in the bbox, as (lon,lat) rings."""
    with open(os.path.join(WORK, "osm_water.json", encoding="utf-8"), encoding="utf-8") as f:
        data = json.load(f)
    polys = []
    for el in data.get("elements", []):
        geom = el.get("geometry")
        if not geom or len(geom) < 3:
            continue
        ring = [(p["lon"], p["lat"]) for p in geom]
        lons = [p[0] for p in ring]
        lats = [p[1] for p in ring]
        c_lon, c_lat = sum(lons) / len(lons), sum(lats) / len(lats)
        if not (BL[0] <= c_lat <= TR[0] and BL[1] <= c_lon <= TR[1]):
            continue
        area = 0.0
        for i in range(len(ring)):
            x1, y1 = ring[i]
            x2, y2 = ring[(i + 1) % len(ring)]
            area += x1 * y2 - x2 * y1
        polys.append(dict(ring=ring, lon=c_lon, lat=c_lat,
                          area_km2=abs(area) / 2 * 111.32 ** 2 * math.cos(math.radians(c_lat)),
                          name=(el.get("tags") or {}).get("name", "")))
    return polys


def iou_of_shapes(plan_mask_sub, ring_px):
    """IoU between a plan blob (as a boolean array) and a polygon rasterised
    into the same box."""
    from PIL import ImageDraw
    h, w = plan_mask_sub.shape
    img = Image.new("1", (w, h), 0)
    d = ImageDraw.Draw(img)
    d.polygon([(x, y) for x, y in ring_px], fill=1)
    poly = np.asarray(img, dtype=bool)
    inter = np.logical_and(plan_mask_sub, poly).sum()
    union = np.logical_or(plan_mask_sub, poly).sum()
    return inter / union if union else 0.0


def best_alignment(sub, ring_local, max_shift_px):
    """Find the translation that best aligns an OSM polygon to a plan blob.

    Why this is needed: the plan's drawing of a lake and OSM's polygon of the
    same lake are NOT the same outline - different generalisation, different
    survey date, different treatment of islands and the shoreline. So the
    two CENTROIDS do not correspond to the same physical point, and using
    them directly as a control point injects an error of 100-300 m that has
    nothing to do with the georeference.

    The right correspondence is the TRANSLATION that best aligns the shapes.
    That translation is a property of the geometry, not of the generalisation,
    so it is a sound measurement of where the two drawings disagree. Applying
    it and then comparing centroids gives a control point whose residual
    reflects the georeference, not the shoreline.
    """
    from PIL import ImageDraw
    h, w = sub.shape
    best = (0, 0, -1.0)
    for dy in range(-max_shift_px, max_shift_px + 1):
        for dx in range(-max_shift_px, max_shift_px + 1):
            img = Image.new("1", (w, h), 0)
            d = ImageDraw.Draw(img)
            d.polygon([(x + dx, y + dy) for x, y in ring_local], fill=1)
            poly = np.asarray(img, dtype=bool)
            inter = np.logical_and(sub, poly).sum()
            union = np.logical_or(sub, poly).sum()
            iou = inter / union if union else 0.0
            if iou > best[2]:
                best = (dx, dy, iou)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(WORK, "gcp_water.csv"))
    ap.add_argument("--min-iou", type=float, default=0.40)
    ap.add_argument("--max-centroid-shift-m", type=float, default=2500.0)
    ap.add_argument("--search-px", type=int, default=12,
                    help="how far to search for the best shape alignment, in DIV px")
    a = ap.parse_args()

    print("building plan water mask ...", flush=True)
    mask = water_mask()
    print(f"  water px at 1/{DIV}: {int(mask.sum())}")
    lab, blobs = plan_blobs(mask)
    print(f"  {len(blobs)} blobs above {MIN_AREA_PLAN} px")

    polys = osm_polygons()
    print(f"  {len(polys)} OSM water polygons in bbox")

    rows = []
    used = set()
    for p in polys:
        if p["area_km2"] < 0.15:
            continue
        px, py = h0_px(p["lat"], p["lon"])
        bx, by = px / DIV, py / DIV
        # nearest plan blob centroid
        best, bestd = None, 1e18
        for b in blobs:
            d = (b["cx"] - bx) ** 2 + (b["cy"] - by) ** 2
            if d < bestd:
                best, bestd = b, d
        if best is None:
            continue
        shift_m = math.sqrt(bestd) * DIV * 2.57
        if shift_m > a.max_centroid_shift_m:
            continue

        # shape alignment: find the translation that best matches the shapes
        bx0, by0, bx1, by1 = best["bbox"]
        sub = (lab[by0:by1, bx0:bx1] == best["label"])
        ring_local = []
        for lon, lat in p["ring"]:
            qx, qy = h0_px(lat, lon)
            ring_local.append(((qx / DIV) - bx0, (qy / DIV) - by0))
        dx, dy, iou = best_alignment(sub, ring_local, a.search_px)
        status = "OK" if iou >= a.min_iou else "reject (low IoU)"
        print(f"  {p['name'] or 'unnamed':<22} osm_area={p['area_km2']:8.3f} km2  "
              f"plan_blob={best['area'] * 16 / 1e4:8.3f} km2  "
              f"centroid_shift={shift_m:7.0f} m  align=({dx:+d},{dy:+d}) DIVpx  "
              f"IoU={iou:.3f}  {status}")
        if iou < a.min_iou or best["label"] in used:
            continue
        used.add(best["label"])

        # The alignment translation is how far the OSM drawing must move to
        # sit on the plan's drawing. Apply it to the OSM centroid so the two
        # centroids refer to the same point on the SAME outline.
        dlon = -dx * DIV * 2.55 / 102500.0     # plan px -> degrees lon
        dmerc = -dy * DIV * 2.55 / 110574.0    # plan px -> degrees mercator
        rows.append(dict(
            px=int(round(best["cx"] * DIV)), py=int(round(best["cy"] * DIV)),
            lon=p["lon"] + dlon, lat=inv_merc_y(merc_y(p["lat"]) + dmerc),
            name=(p["name"] or f"water_{best['label']}").lower().replace(" ", "_"),
            iou=round(iou, 3)))

    import csv
    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["px", "py", "lon", "lat", "name"])
        for r in rows:
            w.writerow([r["px"], r["py"], f"{r['lon']:.7f}", f"{r['lat']:.7f}", r["name"]])
    print(f"\nwrote {len(rows)} water control points -> {a.out}")


if __name__ == "__main__":
    main()
