"""Extract the plan's labels from the vector PDF - exactly, not by OCR.

The previous layer had 29,175 OCR'd "plot numbers" of which 25,968 were
fragments, and it misread real numbers: 718/4 came back as 728/4 and
431+432NC as 451+432NC. It was reading pixels off a scan.

These PDFs are vector, and the survey numbers are *text objects* in the file,
so they are read, not guessed. The sheets use a fixed set of type sizes, and
each size means one kind of label:

    5.8 / 6.7   the survey number inside a plot      <- the plot numbers
    8.6 / 9.6 / 12.0   road widths: "12.00 Mt.", "30 M"
    9.6 / 13.9 / 15.8  settlement and feature names: MOTERA, Stadium
    4.8         the "!" TP-boundary marker glyph

Classifying on the type size, not on the shape of the text, is what keeps a
village called "5" or a road numbered "30" out of the plot-number layer.

Output: data/dp2021/plots.geojson  (the survey numbers, clickable)
        data/dp2021/labels.geojson (places, road widths, sheet refs)
        data/dp2021/legend.json    (the land-use legend, read off the sheet)
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

import fitz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dp_georef as G  # noqa: E402

ROOT = G.ROOT
OUT_DIR = os.path.join(ROOT, "data", "dp2021")

# Type sizes, as used on the sheets.
SIZE_PLOT = (5.5, 7.0)
SIZE_ROAD = (8.0, 9.2)
SIZE_ROAD_BIG = (11.5, 12.5)
SIZE_PLACE = (9.3, 10.2)
SIZE_PLACE_BIG = (13.0, 16.5)

ROAD_RE = re.compile(r"^(?:\d{1,3}(?:\.\d{1,2})?)\s*(?:Mt|M)\.?$", re.IGNORECASE)
SHEET_RE = re.compile(r"^[A-Q]-?\d{1,2}$")
PLOTISH_RE = re.compile(r"^\d{1,5}(?:[/+][A-Za-z0-9]{1,6})*$")
NUMERIC_RE = re.compile(r"[\d]")
# words that are sheet furniture, not plan content
NOT_PLOT = re.compile(r"^(?:TO\s*\d+|BRIDGE|RAILWAY|CIRCLE|SUR\.?NO|OVERBRIDGE)$", re.IGNORECASE)


def kind_of(text, size):
    """Classify one text span by its type size first, then its content."""
    t = text.strip()
    if not t or t == "!":
        return None
    if SIZE_PLOT[0] <= size <= SIZE_PLOT[1]:
        if not NUMERIC_RE.search(t) or NOT_PLOT.match(t):
            return None
        # some road widths are printed at plot size ("12 M"): a width, not a survey number
        if ROAD_RE.match(t.replace(" ", "")):
            return "road"
        # several crowded numbers run together by the text order ("959493929")
        # are no one number; their position is not any one plot's
        if re.match(r"^\d{6,}$", t):
            return None
        # a survey number glued to a road width ("1037.5 M" = 103 + 7.5 M)
        if re.match(r"^\d+(?:\.\d+)?\s*M\.?$", t, re.IGNORECASE):
            return None
        if not PLOTISH_RE.match(t) and not re.match(r"^[\d\s/+\-A-Za-z.]+$", t):
            return None
        return "plot"
    if SIZE_ROAD[0] <= size <= SIZE_ROAD[1]:
        if ROAD_RE.match(t.replace(" ", "")):
            return "road"
        if t.isupper() and len(t) >= 4:
            return "place"
        return None
    if SIZE_ROAD_BIG[0] <= size <= SIZE_ROAD_BIG[1]:
        if ROAD_RE.match(t.replace(" ", "")):
            return "road"
        if re.match(r"^[\d\s.]+$", t):
            return "road_frag"
        return None
    if SIZE_PLACE[0] <= size <= SIZE_PLACE[1]:
        if re.match(r"^[\d\s.]+$", t):
            return "road_frag"
        return "place"
    if SIZE_PLACE_BIG[0] <= size <= SIZE_PLACE_BIG[1]:
        if t.upper() in ("SUR.NO", "SUR. NO"):
            return "sur"
        return "place"
    return None


def spans_of(page):
    d = page.get_text("dict")
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                t = span.get("text", "")
                if not t.strip():
                    continue
                yield t, span["bbox"], span.get("size", 0.0), line.get("dir", (1, 0))


def extract_labels():
    blocks, _grid = G.build_blocks()
    out = []
    counts = Counter()
    for quad, block in blocks.items():
        doc = fitz.open(block.path)
        page = doc[0]
        crop = page.cropbox
        print(f"[{quad}] {os.path.basename(block.path)}", flush=True)
        n = 0
        for text, bbox, size, direction in spans_of(page):
            n += 1
            t = text.strip()
            kind = kind_of(t, size)
            if kind is None:
                continue
            px = (bbox[0] + bbox[2]) / 2.0
            py = (bbox[1] + bbox[3]) / 2.0
            ux = px + crop.x0
            uy = crop.y1 - py
            lon, lat = block.page_to_ground(ux, uy)
            out.append({
                "t": t, "k": kind, "b": quad,
                "lon": round(lon, 7), "lat": round(lat, 7),
                "sz": round(size, 2),
                "rot": round(math.degrees(math.atan2(direction[1], direction[0])), 1),
            })
            counts[kind] += 1
        print(f"    {n} text spans", flush=True)
        doc.close()
    return out, counts


import math  # noqa: E402  (used above)


def dedupe(rows, metres=2.0):
    """The four parts overlap by ~1.8 km, so labels in the seams appear twice."""
    seen = {}
    keep = []
    # bucket by a coarse grid so the comparison is local
    for r in rows:
        key = (round(r["lon"], 5), round(r["lat"], 5), r["t"])
        k = (int(r["lon"] * 1000), int(r["lat"] * 1000))
        hit = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for t2 in seen.get((k[0] + dx, k[1] + dy), ()):
                    if t2 == r["t"]:
                        hit = t2
                        break
                if hit:
                    break
            if hit:
                break
        if hit is None:
            seen.setdefault(k, []).append(r["t"])
            keep.append(r)
    return keep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT_DIR)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    rows, counts = extract_labels()
    print("\nraw classification:", dict(counts))
    rows = dedupe(rows)
    print(f"after de-duplicating the block seams: {len(rows)} labels")
    counts = Counter(r["k"] for r in rows)
    print("final:", dict(counts))

    plots = [r for r in rows if r["k"] == "plot"]
    others = [r for r in rows if r["k"] != "plot"]

    def fc(name, items, props):
        return {
            "type": "FeatureCollection",
            "name": name,
            "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3/CRS84"}},
            "features": [
                {"type": "Feature",
                 "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                 "properties": {**{k: r[k] for k in props},
                                "lo": r["lon"], "la": r["lat"]}}
                for r in items
            ],
        }

    p = os.path.join(args.out, "plots.geojson")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(fc("auda_dp2021_plots", plots, ("t", "k", "sz", "rot")), f,
                  separators=(",", ":"))
    print(f"\nwrote {p}  ({len(plots)} features, {os.path.getsize(p)/1e6:.1f} MB)")

    p = os.path.join(args.out, "labels.geojson")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(fc("auda_dp2021_labels", others, ("t", "k", "sz", "rot")), f,
                  separators=(",", ":"))
    print(f"wrote {p}  ({len(others)} features, {os.path.getsize(p)/1e6:.1f} MB)")

    with open(os.path.join(args.out, "label_stats.json"), "w", encoding="utf-8") as f:
        json.dump({"counts": dict(counts),
                   "sample": [r["t"] for r in plots[:40]]}, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
