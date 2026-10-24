"""Bucket the legacy OCR plot numbers into per-tile GeoJSON for lazy loading."""
import argparse, json, math, os
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def deg2tile(lon, lat, z):
    n = 2.0 ** z
    x = (lon + 180.0) / 360.0 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return int(math.floor(x)), int(math.floor(y))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", default=os.path.join(ROOT, "data", "plots.geojson"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "plots_tiles"))
    ap.add_argument("--zooms", default="13,14,15,16")
    ap.add_argument("--min-conf", type=float, default=0.0)
    a = ap.parse_args()

    data = json.load(open(a.src, encoding="utf-8"))
    rows = [f for f in data["features"] if f["properties"].get("s", 1.0) >= a.min_conf]
    print(f"{len(rows)} numbers (min conf {a.min_conf})")

    zooms = [int(z) for z in a.zooms.split(",") if z.strip()]
    total_files = 0
    for z in zooms:
        buckets = defaultdict(list)
        for f in rows:
            c = f["geometry"]["coordinates"]
            x, y = deg2tile(c[0], c[1], z)
            buckets[(x, y)].append(f)
        for (x, y), items in buckets.items():
            d = os.path.join(a.out, str(z), str(x))
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, f"{y}.geojson"), "w", encoding="utf-8") as fh:
                json.dump({"type": "FeatureCollection", "features": items},
                          fh, ensure_ascii=False, separators=(",", ":"))
            total_files += 1
        print(f"  z{z}: {len(buckets)} tiles, {sum(len(v) for v in buckets.values())} labels")
    print(f"wrote {total_files} GeoJSON files -> {a.out}")


if __name__ == "__main__":
    main()