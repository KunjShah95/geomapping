"""Bucket the exact plot numbers into map-tile buckets for the viewer.

The full layer is 211,630 points, about 30 MB of GeoJSON. A browser cannot be
asked to load that. The numbers are also not legible below about z15 - the
type is 5.8 pt on a 2.82 m/pt sheet, which is ~16 m tall on the ground, so
they are unreadable at z13 however they are drawn.

So the layer is split by usefulness:
  plots/15/{x}/{y}.geojson   every number, bucketed by z15 tile
  plots_overview.geojson     a 1-in-N sample for the low zooms

A viewer at z15+ loads only the buckets it can see.
"""
import argparse
import json
import math
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dp_georef as G  # noqa: E402

ROOT = G.ROOT


def lon2px(lon, z):
    return (lon + 180.0) / 360.0 * (256 * 2 ** z)


def lat2py(lat, z):
    la = math.radians(lat)
    return (1.0 - math.log(math.tan(la) + 1.0 / math.cos(la)) / math.pi) / 2.0 * (256 * 2 ** z)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.join(ROOT, "data", "dp2021", "plots.geojson"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "dp2021", "plots"))
    ap.add_argument("--z", type=int, default=15)
    ap.add_argument("--sample", type=int, default=12,
                    help="keep 1 in N for the overview layer")
    args = ap.parse_args()

    with open(args.src, encoding="utf-8") as f:
        g = json.load(f)
    feats = g["features"]
    print(f"{len(feats)} features -> bucketing at z{args.z}")

    buckets = {}
    for ft in feats:
        lon, lat = ft["geometry"]["coordinates"]
        x = int(lon2px(lon, args.z) // 256)
        y = int(lat2py(lat, args.z) // 256)
        buckets.setdefault((x, y), []).append(ft)

    os.makedirs(args.out, exist_ok=True)
    total = 0
    biggest = 0
    for (x, y), items in sorted(buckets.items()):
        d = os.path.join(args.out, str(args.z), str(x))
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, f"{y}.geojson")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"type": "FeatureCollection", "features": items},
                      f, separators=(",", ":"))
        total += os.path.getsize(p)
        biggest = max(biggest, os.path.getsize(p))
    print(f"  {len(buckets)} buckets, {total/1e6:.1f} MB, largest bucket {biggest/1024:.0f} KB")

    # overview: a deterministic sample, so the same plots are always chosen
    step = max(1, args.sample)
    overview = feats[::step]
    p = os.path.join(ROOT, "data", "dp2021", "plots_overview.geojson")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": overview},
                  f, separators=(",", ":"))
    print(f"  overview: {len(overview)} features, {os.path.getsize(p)/1e6:.1f} MB -> {p}")


if __name__ == "__main__":
    main()
