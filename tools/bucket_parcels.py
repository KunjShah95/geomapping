"""Bucket the parcel polygons into per-tile GeoJSON for lazy loading.

Mirrors tools/bucket_zones.py: each parcel is written to every z15 tile its
bounding box intersects, so a parcel that straddles a tile edge still renders
when either side is in view.

The parcel layer is large - a quarter of a million polygons - so it cannot be
loaded as one file. The viewer fetches only the buckets it can see.
"""
import argparse
import json
import math
import os
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def deg2tile(lon, lat, z):
    n = 2.0 ** z
    x = (lon + 180.0) / 360.0 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return int(math.floor(x)), int(math.floor(y))


def tile_range_for_bbox(minx, miny, maxx, maxy, z):
    x0, y0 = deg2tile(minx, maxy, z)
    x1, y1 = deg2tile(maxx, miny, z)
    return range(min(x0, x1), max(x0, x1) + 1), range(min(y0, y1), max(y0, y1) + 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src",
                    default=os.path.join(ROOT, "data", "dp2021", "parcels.geojson"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "dp2021", "parcels"))
    ap.add_argument("--zoom", type=int, default=15)
    a = ap.parse_args()

    data = json.load(open(a.src, encoding="utf-8"))
    feats = data["features"]
    print(f"{len(feats):,} parcels loaded")

    z = a.zoom
    buckets = defaultdict(list)
    for f in feats:
        g = f["geometry"]
        if g["type"] != "Polygon":
            continue
        ring = g["coordinates"][0]
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        xr, yr = tile_range_for_bbox(min(xs), min(ys), max(xs), max(ys), z)
        for x in xr:
            for y in yr:
                buckets[(x, y)].append(f)

    total = 0
    biggest = 0
    for (x, y), items in sorted(buckets.items()):
        d = os.path.join(a.out, str(z), str(x))
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, f"{y}.geojson")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"type": "FeatureCollection", "features": items}, fh,
                      ensure_ascii=False, separators=(",", ":"))
        sz = os.path.getsize(p)
        total += sz
        biggest = max(biggest, sz)
    print(f"z{z}: {len(buckets):,} tiles, {total/1e6:.1f} MB, "
          f"largest bucket {biggest/1024:.0f} KB")
    print(f"output: {a.out}")


if __name__ == "__main__":
    main()
