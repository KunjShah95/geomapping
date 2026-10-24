"""Mark particular survey records on the map.

Given one or more survey numbers, find every place the plan draws that
number, ring it on the map, and write it out as a marked-records layer the
viewer can load.

Survey numbers repeat across the district - "461" is on 115 different plots,
because the numbers restart in each village - so a record is identified by
its number AND its position, and every occurrence is marked and numbered so
the reader can see which village they are in.

Output: work/newdp/marked/<slug>.png   the records ringed on the map
        data/dp2021/marked/<slug>.geojson  the records, for the viewer
        data/dp2021/marked/index.json     the list of marking jobs
"""
import argparse
import json
import math
import os
import sys
from collections import defaultdict

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dp_georef as G  # noqa: E402

ROOT = G.ROOT
TILES = os.path.join(ROOT, "tiles_dp2021")
PLOTS = os.path.join(ROOT, "data", "dp2021", "plots.geojson")
OUT_IMG = os.path.join(ROOT, "work", "newdp", "marked")
OUT_GEO = os.path.join(ROOT, "data", "dp2021", "marked")


def lon2px(lon, z):
    return (lon + 180.0) / 360.0 * (256 * 2 ** z)


def lat2py(lat, z):
    la = math.radians(lat)
    return (1.0 - math.log(math.tan(la) + 1.0 / math.cos(la)) / math.pi) / 2.0 * (256 * 2 ** z)


def fetch_map(lon, lat, half_px, z):
    """The map around a lon/lat, assembled from the built tiles."""
    px = lon2px(lon, z)
    py = lat2py(lat, z)
    x0, y0 = int(px) - half_px, int(py) - half_px
    out = Image.new("RGB", (half_px * 2, half_px * 2), (245, 245, 245))
    for tx in range(x0 // 256, (x0 + half_px * 2) // 256 + 1):
        for ty in range(y0 // 256, (y0 + half_px * 2) // 256 + 1):
            p = os.path.join(TILES, str(z), str(tx), f"{ty}.webp")
            if not os.path.exists(p):
                continue
            with Image.open(p) as im:
                im = im.convert("RGB")
                ix0, iy0 = max(x0, tx * 256), max(y0, ty * 256)
                ix1, iy1 = min(x0 + half_px * 2, (tx + 1) * 256), min(y0 + half_px * 2, (ty + 1) * 256)
                if ix1 <= ix0 or iy1 <= iy0:
                    continue
                out.paste(im.crop((ix0 - tx * 256, iy0 - ty * 256,
                                   ix1 - tx * 256, iy1 - ty * 256)), (ix0 - x0, iy0 - y0))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("numbers", nargs="+", help="survey numbers to mark")
    ap.add_argument("--z", type=int, default=17)
    ap.add_argument("--radius", type=int, default=140, help="half-width of the crop, px")
    ap.add_argument("--out-width", type=int, default=1100)
    ap.add_argument("--place", default=None,
                    help="lon,lat to centre on; the default is the first record")
    ap.add_argument("--all", action="store_true",
                    help="mark every occurrence of the number, not just the first")
    args = ap.parse_args()

    os.makedirs(OUT_IMG, exist_ok=True)
    os.makedirs(OUT_GEO, exist_ok=True)

    with open(PLOTS, encoding="utf-8") as f:
        feats = json.load(f)["features"]
    by_text = defaultdict(list)
    for ft in feats:
        by_text[ft["properties"]["t"]].append(ft)

    wanted = []
    for n in args.numbers:
        if n in by_text:
            wanted.append(n)
        else:
            print(f"  {n}: not found in the survey numbers")
    if not wanted:
        raise SystemExit("nothing to mark")

    marks = []
    for n in wanted:
        for ft in by_text[n]:
            lon, lat = ft["geometry"]["coordinates"]
            marks.append({"t": n, "lon": lon, "lat": lat})
    print(f"marking {len(marks)} occurrences of {len(wanted)} numbers")

    if args.place:
        clon, clat = (float(v) for v in args.place.split(","))
    else:
        clon, clat = marks[0]["lon"], marks[0]["lat"]
    if not args.all:
        # keep only the marks within 1.5 km of the centre
        keep = []
        for m in marks:
            d = math.hypot((m["lon"] - clon) * 102000, (m["lat"] - clat) * 111000)
            if d < 1500:
                keep.append(m)
        marks = keep

    z = args.z
    m_per_px = 156543.03392 * math.cos(math.radians(clat)) / (2 ** z)
    half_m = args.radius * m_per_px
    img = fetch_map(clon, clat, args.radius, z).resize((args.out_width, args.out_width), Image.LANCZOS)
    sc = args.out_width / (args.radius * 2)

    try:
        font = ImageFont.truetype("arialbd.ttf", 20)
    except Exception:
        font = ImageFont.load_default()
    d = ImageDraw.Draw(img)
    for i, m in enumerate(marks):
        x = (lon2px(m["lon"], z) - (lon2px(clon, z) - args.radius)) * sc
        y = (lat2py(m["lat"], z) - (lat2py(clat, z) - args.radius)) * sc
        d.ellipse([x - 26, y - 26, x + 26, y + 26], outline=(255, 0, 60), width=5)
        d.ellipse([x - 5, y - 5, x + 5, y + 5], fill=(255, 0, 60))
        d.text((x + 30, y - 12), f"{i+1}. {m['t']}", fill=(122, 0, 22), font=font)
    d.rectangle([0, 0, args.out_width, 30], fill=(255, 255, 255))
    d.text((8, 5), f"survey records: {', '.join(wanted)}   -   {len(marks)} occurrence(s)",
           fill=(0, 0, 0), font=font)
    d.text((8, args.out_width - 26), f"{clat:.5f}, {clon:.5f}   z{z}", fill=(0, 0, 0), font=font)

    slug = "_".join(wanted).replace("/", "-").replace("+", "plus")
    p = os.path.join(OUT_IMG, slug + ".png")
    img.save(p)
    print(f"wrote {p}")

    g = {
        "type": "FeatureCollection",
        "name": "auda_dp2021_marked",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3/CRS84"}},
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [m["lon"], m["lat"]]},
             "properties": {"t": m["t"], "n": i + 1,
                            "note": "survey number read from the DP 2021 sheet"}}
            for i, m in enumerate(marks)
        ],
    }
    gp = os.path.join(OUT_GEO, slug + ".geojson")
    with open(gp, "w", encoding="utf-8") as f:
        json.dump(g, f, separators=(",", ":"))
    print(f"wrote {gp}")

    idx_path = os.path.join(OUT_GEO, "index.json")
    index = {}
    if os.path.exists(idx_path):
        with open(idx_path, encoding="utf-8") as f:
            index = json.load(f)
    index[slug] = {"numbers": wanted, "marks": len(marks), "centre": [clon, clat],
                   "geojson": f"/data/dp2021/marked/{slug}.geojson",
                   "image": f"work/newdp/marked/{slug}.png"}
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)
    print(f"wrote {idx_path}")


if __name__ == "__main__":
    main()
