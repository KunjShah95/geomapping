"""Bucket land-use zone polygons into per-tile GeoJSON for lazy loading.

Mirrors tools/bucket_plots.py but for polygons: each zone is written to every
z15 tile its bounding box intersects, so a polygon that straddles a tile edge
still renders when either side is in view.
"""
import argparse, json, math, os
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
    xs = range(min(x0, x1), max(x0, x1) + 1)
    ys = range(min(y0, y1), max(y0, y1) + 1)
    return xs, ys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", default=os.path.join(ROOT, "data", "dp2021", "zones.geojson"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "dp2021", "zones"))
    ap.add_argument("--zoom", type=int, default=15)
    a = ap.parse_args()

    data = json.load(open(a.src, encoding="utf-8"))
    feats = data["features"]
    print(f"{len(feats)} zones loaded")

    z = a.zoom
    buckets = defaultdict(list)
    for i, f in enumerate(feats):
        g = f["geometry"]
        if g["type"] != "Polygon":
            continue
        ring = g["coordinates"][0]
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        minx, maxx = min(xs), max(xs)
        miny, maxy = min(ys), max(ys)
        xr, yr = tile_range_for_bbox(minx, miny, maxx, maxy, z)
        props = dict(f["properties"])
        props["zid"] = i
        feat = {"type": "Feature", "geometry": g, "properties": props}
        for x in xr:
            for y in yr:
                buckets[(x, y)].append(feat)

    # every tile inside the plan gets a file, even with no zones in it, so the
    # viewer's bucket fetches never 404 (an empty collection, not a missing file)
    allx = [p[0] for f in feats if f["geometry"]["type"] == "Polygon" for p in f["geometry"]["coordinates"][0]]
    ally = [p[1] for f in feats if f["geometry"]["type"] == "Polygon" for p in f["geometry"]["coordinates"][0]]
    if allx:
        xr, yr = tile_range_for_bbox(min(allx), min(ally), max(allx), max(ally), z)
        for x in xr:
            for y in yr:
                buckets.setdefault((x, y), [])

    total_files = 0
    for (x, y), items in buckets.items():
        d = os.path.join(a.out, str(z), str(x))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"{y}.geojson"), "w", encoding="utf-8") as fh:
            json.dump({"type": "FeatureCollection", "features": items}, fh,
                      ensure_ascii=False, separators=(",", ":"))
        total_files += 1

    print(f"z{z}: {len(buckets)} tiles, {sum(len(v) for v in buckets.values())} feature-refs -> {total_files} files")
    print(f"output: {a.out}")


if __name__ == "__main__":
    main()