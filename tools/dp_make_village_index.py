"""A village index, for the viewer's search box.

The survey numbers are the client's own units, so a client looking for a
record will usually know the village it is in. This is the list of records
by village, with each village's ground extent.
"""
import json, os
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = os.path.join(ROOT, "data", "dp2021", "records.ndjson")
dst = os.path.join(ROOT, "data", "dp2021", "village_index.json")
CAP = 500

by = defaultdict(list)
extent = {}
with open(src, encoding="utf-8") as f:
    for line in f:
        r = json.loads(line)
        v = r.get("village")
        if not v:
            continue
        by[v].append([r["lon"], r["lat"]])
        e = extent.get(v)
        if e is None:
            extent[v] = [r["lon"], r["lat"], r["lon"], r["lat"]]
        else:
            e[0] = min(e[0], r["lon"]); e[1] = min(e[1], r["lat"])
            e[2] = max(e[2], r["lon"]); e[3] = max(e[3], r["lat"])

out = {}
for v, pts in by.items():
    out[v] = {"n": len(pts), "bbox": [round(x, 6) for x in extent[v]],
              "pts": pts[:CAP]}
with open(dst, "w", encoding="utf-8") as f:
    json.dump(out, f, separators=(",", ":"))
print(f"{len(out)} villages -> {dst}  ({os.path.getsize(dst)/1e6:.2f} MB)")
print("largest:", max(v['n'] for v in out.values()), "records")
