"""Fast raw-bytes scan of every PDF in the NEW DP BOOK for georeference structures.

Raw byte search only (no xref walk) -> fast enough for the whole set.
"""
import os, re, json, collections, time

BASE = r"C:\ideabot internships\geomapping\NEW DP BOOK AUDA"
KEYS = [b"/Measure", b"/VP", b"/LGIDict", b"/GPTS", b"/Bounds", b"/SpatialRef", b"/CTM", b"/GCS"]

def walk(sub):
    out = []
    for dp, dn, fn in os.walk(os.path.join(BASE, sub)):
        for f in fn:
            if f.lower().endswith(".pdf"):
                out.append(os.path.join(dp, f))
    return out

t0 = time.time()
rows = []
for sub in ["", "AUDA FULL DP 4 PART", "DP PLAN ALAG ALAG PAGE WITH ORIGINAL NUMBER", "AUDA DP EACH  VILLAGE MAP"]:
    for p in walk(sub):
        with open(p, "rb") as f:
            raw = f.read()
        hits = [k.decode() for k in KEYS if k in raw]
        rows.append({
            "rel": os.path.relpath(p, BASE),
            "size": len(raw),
            "hits": hits,
            "producer": (re.search(rb"/Producer\s*\(([^)]*)\)", raw).group(1).decode("latin1", "replace")
                         if re.search(rb"/Producer\s*\(([^)]*)\)", raw) else ""),
            "creator": (re.search(rb"/Creator\s*\(([^)]*)\)", raw).group(1).decode("latin1", "replace")
                        if re.search(rb"/Creator\s*\(([^)]*)\)", raw) else ""),
        })
        if len(rows) % 40 == 0:
            print(f"  ...{len(rows)} files, {time.time()-t0:.0f}s", flush=True)

with open(os.path.join(BASE, "..", "work", "geo_raw_scan.json"), "w", encoding="utf-8") as f:
    json.dump(rows, f, indent=2)

agg = collections.Counter((tuple(r["hits"]), r["producer"][:40], r["creator"][:40]) for r in rows)
print(f"\n=== {len(rows)} files scanned in {time.time()-t0:.0f}s")
for k, v in agg.most_common():
    print(f"  n={v:4d} hits={k[0]} producer={k[1]!r} creator={k[2]!r}")
