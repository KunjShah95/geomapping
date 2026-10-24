"""The reference for the survey set-out sheet.

The set-out geometry lives in web/markout.js. The viewer no longer shows the
sheet, so the library is now used from the command line. This script computes
the same thing the slow, authoritative way - with pyproj, in the same
projected CRS the plan's sheets were drawn in - and writes the answer down.
tools/dp_check_markout.js then re-computes it with the library's code and
compares the two.

Why it matters: a set-out sheet is a list of numbers that somebody will
peg a peg in the ground from. The numbers have to be right, and "right"
has to mean the same thing in both implementations.

The algorithm, in both places:

    1. project the drawn ring into the plan's own metres
    2. drop points that are the same point
    3. drop points that sit within 5 cm of the straight line through
       their neighbours - the plan draws a straight edge as a polyline
       with a vertex every few centimetres, and those are not corners
    4. wind the ring counter-clockwise, so the bearings mean the same
       thing every time
    5. start at the south-west corner, so the same plot always prints
       the same table
    6. measure: azimuth, length, cumulative distance, area, perimeter

Usage:
    python tools/dp_markout_reference.py            # writes work/markout_reference.json
    python tools/dp_markout_reference.py --count 40
"""
import argparse
import glob
import json
import math
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The projected CRS the sheets were drawn in, as published in the files.
# This is the same WKT tools/dp_georef.py uses - one source of truth.
WKT = ('PROJCS["WGS_1984_Transverse_Mercator",GEOGCS["GCS_WGS_1984",'
       'DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],'
       'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],'
       'PROJECTION["Transverse_Mercator"],PARAMETER["False_Easting",400000.0],'
       'PARAMETER["False_Northing",400000.0],PARAMETER["Central_Meridian",71.5],'
       'PARAMETER["Scale_Factor",0.999738],PARAMETER["Latitude_Of_Origin",22.5],'
       'UNIT["Meter",1.0]]')

TOL_M = 0.05          # how far a point may sit off the chord
DUP_M = 0.01          # two points closer than this are one point
MIN_CORNERS = 3


def make_transformer():
    from pyproj import CRS, Transformer
    crs = CRS.from_wkt(WKT)
    return Transformer.from_crs(4326, crs, always_xy=True), \
        Transformer.from_crs(crs, 4326, always_xy=True)


def signed_area(pts):
    a = 0.0
    n = len(pts)
    for i in range(n):
        j = (i + 1) % n
        a += pts[i][0] * pts[j][1] - pts[j][0] * pts[i][1]
    return a / 2.0


def deviation(pts, i):
    n = len(pts)
    p0, p1, p2 = pts[(i - 1) % n], pts[i], pts[(i + 1) % n]
    ax, ay = p1[0] - p0[0], p1[1] - p0[1]
    bx, by = p2[0] - p0[0], p2[1] - p0[1]
    length = math.hypot(bx, by)
    if length < 1e-9:
        return 0.0
    return abs(ax * by - ay * bx) / length


def reduce_corners(metres):
    out = []
    for p in metres:
        if not out or math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) >= DUP_M:
            out.append(p)
    pts = out
    while len(pts) > 1 and math.hypot(pts[0][0] - pts[-1][0],
                                      pts[0][1] - pts[-1][1]) < DUP_M:
        pts.pop()
    changed = True
    while changed and len(pts) > MIN_CORNERS:
        changed = False
        worst, worst_dev = -1, float("inf")
        for i in range(len(pts)):
            dev = deviation(pts, i)
            if dev < TOL_M and dev < worst_dev:
                worst, worst_dev = i, dev
        if worst >= 0:
            pts.pop(worst)
            changed = True
    return pts


def set_out(ring, tr):
    """The set-out of one [lon, lat] ring, computed with pyproj."""
    back = tr[1]
    metres = [tr[0].transform(lon, lat) for lon, lat in ring]
    pts = reduce_corners(metres)
    if signed_area(pts) < 0:
        pts = list(reversed(pts))
    start, best_key = 0, float("inf")
    for i, p in enumerate(pts):
        key = p[1] * 1e7 + p[0]
        if key < best_key:
            best_key, start = key, i
    pts = pts[start:] + pts[:start]

    corners, perimeter = [], 0.0
    for i, p in enumerate(pts):
        j = (i + 1) % len(pts)
        de, dn = pts[j][0] - p[0], pts[j][1] - p[1]
        length = math.hypot(de, dn)
        az = math.degrees(math.atan2(de, dn))
        if az < 0:
            az += 360.0
        # the corner's own geographic position, straight out of pyproj's
        # inverse - the browser's set-out has to agree with this to the
        # millimetre, because these are the numbers on the sheet
        lon, lat = back.transform(p[0], p[1])
        corners.append({"i": i + 1, "e": p[0], "n": p[1],
                        "len": length, "az": az,
                        "lon": lon, "lat": lat})
        perimeter += length
    run = 0.0
    for c in corners:
        c["cum"] = run
        run += c["len"]

    return {
        "corners": corners,
        "n": len(corners),
        "area_m2": abs(signed_area(pts)),
        "perimeter_m": perimeter,
        "vertices_drawn": len(ring),
    }


def sample_parcels(count, style=None):
    """A spread of real parcels out of the bucketed layer.

    style=None takes whatever is there; a style name takes only the plots
    of that kind - "polygonized" for the ones assembled from the plan's
    shared strokes, which are a different animal from the closed rings.
    """
    files = sorted(glob.glob(os.path.join(ROOT, "data", "dp2021", "parcels", "15", "*", "*.geojson")))
    if not files:
        raise SystemExit("no parcel buckets found - run tools/bucket_parcels.py first")
    rng = random.Random(20211022)
    picked = []
    seen = set()
    # spread the sample over the book rather than over one tile
    step = max(1, len(files) // (count * 3))
    for f in files[::step]:
        try:
            with open(f, encoding="utf-8") as fh:
                g = json.load(fh)
        except (OSError, ValueError):
            continue
        for feat in g.get("features", []):
            props = feat.get("properties") or {}
            pid = props.get("pid")
            if pid in seen:
                continue
            if style is not None and props.get("style") != style:
                continue
            seen.add(pid)
            geom = feat.get("geometry") or {}
            if geom.get("type") != "Polygon":
                continue
            ring = geom["coordinates"][0]
            if len(ring) < 3:
                continue
            picked.append({"file": os.path.relpath(f, ROOT), "ring": ring,
                           "props": props})
            break
        if len(picked) >= count:
            break
    if not picked:
        rng.shuffle(files)
        for f in files:
            try:
                with open(f, encoding="utf-8") as fh:
                    g = json.load(fh)
            except (OSError, ValueError):
                continue
            for feat in g.get("features", []):
                props = feat.get("properties") or {}
                if style is not None and props.get("style") != style:
                    continue
                geom = feat.get("geometry") or {}
                if geom.get("type") == "Polygon":
                    picked.append({"file": os.path.relpath(f, ROOT),
                                   "ring": geom["coordinates"][0],
                                   "props": props})
                    break
            if len(picked) >= count:
                break
    return picked


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", type=int, default=30,
                    help="how many parcels to reference")
    ap.add_argument("--out", default=os.path.join(ROOT, "work", "markout_reference.json"))
    ap.add_argument("--style", default=None,
                    help="only sample plots of this style, e.g. polygonized")
    args = ap.parse_args()

    tr = make_transformer()
    parcels = sample_parcels(args.count, args.style)
    if not parcels:
        raise SystemExit("no parcels to reference")

    out = []
    for p in parcels:
        so = set_out(p["ring"], tr)
        out.append({
            "file": p["file"],
            "props": p["props"],
            "ring": p["ring"],
            "reference": so,
        })
        print(f"  pid {p['props'].get('pid')!s:>8}  "
              f"{so['vertices_drawn']:>3} drawn -> {so['n']:>2} corners  "
              f"{so['area_m2']:>10.2f} m2  {so['perimeter_m']:>9.3f} m")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"crs": WKT, "tolerance_m": TOL_M, "parcels": out}, fh, indent=1)
    print(f"\n{len(out)} parcels referenced -> {os.path.relpath(args.out, ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
