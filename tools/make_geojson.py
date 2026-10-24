"""Turn cleaned OCR numbers into the GeoJSON label layer, with the land-use
zone read from the plan's own fill.

WHY THIS REPLACES tools/make_geojson.py
----------------------------------------
The old version had three problems, all of which showed up in the shipped
data/plots.geojson:

  1. IT FED ON RAW OCR.  The detector splits one printed number into two
     boxes ("285" + "/2"), and both halves were emitted. The result was
     42,281 features with only 7,355 distinct texts, the most common being
     "2", "22" and "7" - fragments, not numbers. Now it feeds on
     work/numbers_clean.ndjson, which merges the split boxes and rejects
     the fragments (tools/clean_numbers.py).

  2. THE ZONE CLASSIFIER WAS WRONG. It classified by hue bands, and its
     bands put RED - which the plan uses for INDUSTRIAL - inside the orange
     "commercial" range, and lumped the plan's TEAL-GREEN open land in with
     the darker agricultural green. The shipped layer had 26,631 features
     labelled "Open / agricultural" on an urban plan, which is a symptom,
     not a fact.

     The palette, measured from the sheet itself:
       yellow   hue  40-85   residential
       orange   hue  25-40   commercial / mixed
       red      hue   0-22   industrial
       purple   hue 255-300 institutional / public
       green    hue  88-132  agricultural
       teal     hue 135-178  open / vacant
       light blue hue 185-205 water

  3. THE ZONE WAS SAMPLED AT THE NUMBER'S CENTRE, where the red glyph of
     the number itself sits. The median over a 9x9 patch is usually the
     fill, but the number is drawn ON a boundary often enough to matter.
     The fill is now sampled from a ring AROUND the number, and the modal
     colour wins, so the glyph itself is excluded.

Output: data/plots.geojson
"""
import argparse, colorsys, json, math, os
from collections import Counter

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "work")
FULL_W, FULL_H = 27200, 25600

# --- the plan's palette, as measured -------------------------------------
# Derived by tools/derive_palette.py, which clusters the saturated colours
# of the sheet and reports each cluster's hue, saturation and value along
# with the share of the sheet it covers. The AUDA RDP 2022 legend uses a
# small, fixed set of fills, and the plan sticks to it.
#
#   hue   swatch   what the plan paints it
#   ----  -------  --------------------------------------------
#    14   #fd3f01  industrial / red
#    44   #f7b704  commercial / orange
#    57   #f8ee08  residential / yellow
#    75   #a3bb5f  a lighter yellow-olive, also residential-family
#   100   #3ba905  agricultural / green
#   135   #4a975e  darker green
#   150   #66cc9a  open / vacant, the teal fringe around the city
#   195   #7edfff  water, the pale blue of the Sabarmati
#   213   #0071fe  mixed land use, the saturated blue near Chakkarpur
#   270   #9966cb  institutional / public, violet
#
# The 213 blue is a land-use fill, NOT water: it has plot numbers in it.
# The old classifier had no band for it at all, so those plots fell through
# to "Other".
PALETTE = [
    (0, 22, "Industrial", "#d9342b"),
    (24, 40, "Commercial / mixed", "#ef8a1f"),
    (40, 86, "Residential", "#f5e93a"),
    (88, 120, "Agricultural", "#2fa84f"),
    (120, 145, "Green / undeveloped", "#4a975e"),
    (145, 178, "Open / vacant", "#7fc9a8"),
    (185, 206, "Water", "#8fd4f0"),
    (206, 232, "Mixed land use", "#0071fe"),
    (250, 302, "Institutional / public", "#a86fd6"),
]


def classify_zone(rgb):
    r, g, b = (v / 255.0 for v in rgb)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    deg = h * 360.0
    if s < 0.16:
        return "Unzoned / paper", "#f2f2f2"
    for lo, hi, name, swatch in PALETTE:
        if lo <= deg <= hi:
            return name, swatch
    return "Other", "#888888"


def modal_fill(mm, cx, cy, r_in=7, r_out=15):
    """The fill colour around a number, excluding the glyph itself.

    A ring is sampled rather than a square, because the number is drawn
    INSIDE its plot, so a square centred on it would still be mostly fill -
    but a ring avoids the case where the number straddles a boundary, where
    the fill on one side and the neighbouring fill on the other would
    average into a colour that is in neither zone.
    """
    ys = []
    xs = []
    for r in range(r_in, r_out + 1, 2):
        for a in range(0, 360, 30):
            t = math.radians(a)
            ys.append(int(cy + r * math.sin(t)))
            xs.append(int(cx + r * math.cos(t)))
    ys = np.clip(ys, 0, FULL_H - 1)
    xs = np.clip(xs, 0, FULL_W - 1)
    patch = mm[ys, xs]
    # quantise and take the mode
    q = (patch // 8) * 8
    keys = q[:, 0].astype(np.int32) * 65536 + q[:, 1].astype(np.int32) * 256 + q[:, 2]
    vals, counts = np.unique(keys, return_counts=True)
    k = vals[counts.argmax()]
    return (int(k) >> 16) & 255, (int(k) >> 8) & 255, int(k) & 255


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", default=os.path.join(WORK, "numbers_clean.ndjson"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "plots.geojson"))
    ap.add_argument("--min-conf", type=float, default=0.45)
    a = ap.parse_args()

    rows, seen = [], set()
    dupes = 0
    with open(a.src, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("s", 0) < a.min_conf:
                continue
            key = (r["t"], round(r["lon"], 6), round(r["lat"], 6))
            if key in seen:
                dupes += 1
                continue
            seen.add(key)
            rows.append(r)
    print(f"{len(rows)} numbers at conf >= {a.min_conf}  ({dupes} duplicates dropped)")

    mm = np.memmap(os.path.join(WORK, "source.rgb"), dtype=np.uint8, mode="r",
                   shape=(FULL_H, FULL_W, 3))

    feats = []
    zones = Counter()
    for r in rows:
        cx, cy = int(r["px"]), int(r["py"])
        rgb = modal_fill(mm, cx, cy)
        zone, swatch = classify_zone(rgb)
        zones[zone] += 1
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
            "properties": {
                "t": r["t"], "s": r["s"], "z": zone,
                "c": "#%02x%02x%02x" % rgb,
                "px": cx, "py": cy,
            },
        })
    mm._mmap.close()

    fc = {"type": "FeatureCollection", "features": feats}
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(fc, fh, ensure_ascii=False, separators=(",", ":"))

    kb = os.path.getsize(a.out) / 1024
    print(f"wrote {a.out}: {len(feats)} features, {kb:.0f} KB")
    print("zone mix:")
    for z, n in zones.most_common():
        print(f"  {n:7d}  {z}")

    uniq = Counter(f["properties"]["t"] for f in feats)
    with open(os.path.join(WORK, "numbers_index.json"), "w", encoding="utf-8") as fh:
        json.dump({"total": len(feats), "distinct": len(uniq),
                   "common": [[k, v] for k, v in uniq.most_common(4000)]}, fh)
    print(f"wrote work/numbers_index.json: {len(uniq)} distinct numbers")


if __name__ == "__main__":
    main()
