"""A compact index of the survey numbers, for the viewer's search box.

The full layer is 211,630 positions, but only 28,807 distinct numbers, so
"number -> where is it" fits in about a megabyte. That is what a search box
needs; the geometry is then fetched from the z15 buckets as the map moves.

The positions are kept to seven decimals, which is the precision the detail
buckets carry. At five decimals the index rounds a position by up to a
metre, and the viewer then cannot tell which of two records with the same
number it is looking at - which is the whole difficulty with these numbers,
since they restart in every village. The extra 0.85 MB is worth it.
"""
import json, os, sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = os.path.join(ROOT, "data", "dp2021", "plots.geojson")
dst = os.path.join(ROOT, "data", "dp2021", "number_index.json")

g = json.load(open(src, encoding="utf-8"))
by = defaultdict(list)
for f in g["features"]:
    t = f["properties"]["t"]
    lon, lat = f["geometry"]["coordinates"]
    by[t].append([round(lon, 7), round(lat, 7)])

out = {t: v for t, v in sorted(by.items())}
with open(dst, "w", encoding="utf-8") as fh:
    json.dump(out, fh, separators=(",", ":"))
print(f"{len(out)} distinct numbers -> {dst}  ({os.path.getsize(dst)/1e6:.2f} MB)")
