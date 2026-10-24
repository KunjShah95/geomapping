"""Bucket OCR plot numbers into per-tile GeoJSON so MapLibre can load them lazily.

A single GeoJSON for the whole sheet would be hundreds of thousands of points and
tens of megabytes. Instead we write one small FeatureCollection per slippy tile at
each distribution zoom, which is exactly what a GeoJSON vector source expects:
    numbers/{z}/{x}/{y}.json

Emitting text as a vector layer (rather than relying on the numbers baked into the
raster) is what makes them legible at every zoom: the raster is downsampled ~13x at
z11, so its own labels are unreadable there, while these stay crisp.
"""
import argparse, json, math, os
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "work")


def deg2tile(lon, lat, z):
    n = 2.0 ** z
    x = (lon + 180.0) / 360.0 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return int(math.floor(x)), int(math.floor(y))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", default=os.path.join(WORK, "plots.ndjson"))
    ap.add_argument("--out", default=os.path.join(ROOT, "numbers"))
    ap.add_argument("--zooms", default="13,14,15,16")
    ap.add_argument("--min-conf", type=float, default=0.0)
    a = ap.parse_args()

    zooms = [int(z) for z in a.zooms.split(",") if z.strip()]
    rows = []
    with open(a.src, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r["s"] < a.min_conf:
                continue
            rows.append(r)
    print(f"{len(rows)} numbers (min conf {a.min_conf})")

    total_files = 0
    for z in zooms:
        buckets = defaultdict(list)
        for r in rows:
            x, y = deg2tile(r["lon"], r["lat"], z)
            buckets[(x, y)].append(r)

        # only touch tiles that hold data; MapLibre treats a 404 as "nothing here"
        for (x, y), items in buckets.items():
            feats = [{
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                "properties": {"t": r["t"], "s": r["s"], "r": r.get("rot", 0)},
            } for r in items]
            d = os.path.join(a.out, str(z), str(x))
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, f"{y}.json"), "w", encoding="utf-8") as fh:
                json.dump({"type": "FeatureCollection", "features": feats},
                          fh, ensure_ascii=False, separators=(",", ":"))
            total_files += 1

        size = sum(len(v) for v in buckets.values())
        print(f"  z{z}: {len(buckets)} tiles, {size} labels")

    print(f"wrote {total_files} GeoJSON files -> {a.out}")

    # a compact index for the search box
    idx = os.path.join(WORK, "numbers_index.json")
    uniq = {}
    for r in rows:
        uniq.setdefault(r["t"], 0)
        uniq[r["t"]] += 1
    top = sorted(uniq.items(), key=lambda kv: -kv[1])[:5000]
    with open(idx, "w", encoding="utf-8") as fh:
        json.dump({"total": len(rows), "distinct": len(uniq), "common": top}, fh)
    print(f"wrote {idx}: {len(uniq)} distinct numbers")


if __name__ == "__main__":
    main()
