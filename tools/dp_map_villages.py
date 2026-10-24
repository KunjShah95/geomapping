"""Place every per-village extract on the part sheet it belongs to.

Why the crop box cannot be used
-------------------------------
All 145 per-village PDFs quote the same viewport as the part sheet they came
from, and their crop boxes do not sit where their content is: measured
against the sheets, the crop boxes are a median of 19.7 km out. They are
bookkeeping, not position.

What works
----------
The extracts and the part sheets are the same plan drawn twice, so they carry
the same words. The place names in a village extract are the same words, at
known positions, as the place names on the sheet it was cut from. Matching
the words gives the offset between the two pages, and the offset puts the
village on the sheet - and therefore on the Earth, through the sheet's own
surveyed transform.

This also confirms the mapping is right: a village that matches a sheet on
its place names is on that sheet.

Output: data/dp2021/villages/villages.json   village -> sheet, true extent
        data/dp2021/villages/index.json      the list, by sheet
        data/dp2021/villages/<NAME>.json     one file per village
"""
import argparse
import json
import math
import os
import sys
from collections import Counter, defaultdict

import fitz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dp_georef as G  # noqa: E402

ROOT = G.ROOT
BASE = os.path.join(ROOT, "NEW DP BOOK AUDA")
VILL_DIR = os.path.join(BASE, "AUDA DP EACH  VILLAGE MAP")
OUT = os.path.join(ROOT, "data", "dp2021", "villages")

def spans_of(page):
    """(text, centre x, centre y) for every text span, in PDF user space."""
    d = page.get_text("dict")
    crop = page.cropbox
    out = []
    for blk in d.get("blocks", []):
        if blk.get("type") != 0:
            continue
        for line in blk.get("lines", []):
            for sp in line.get("spans", []):
                t = sp["text"].strip()
                if len(t) < 4:
                    continue
                bb = sp["bbox"]
                out.append((t, (bb[0] + bb[2]) / 2.0, (bb[1] + bb[3]) / 2.0))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    blocks, grid = G.build_blocks()

    # the sheets' place names, and their positions
    sheet_words = {}
    for q, b in blocks.items():
        doc = fitz.open(b.path)
        page = doc[0]
        d = defaultdict(list)
        for t, x, y in spans_of(page):
            if t.isupper() and len(t) >= 4 and " " not in t:
                d[t].append((x, y))
        sheet_words[q] = d
        print(f"[{q}] {len(d):,} distinct place names", flush=True)
        doc.close()

    files = sorted(f for f in os.listdir(VILL_DIR) if f.lower().endswith(".pdf"))
    print(f"\n{len(files)} per-village extracts")
    out = {}
    by_block = defaultdict(list)
    weak = []

    for fn in files:
        name = os.path.splitext(fn)[0]
        doc = fitz.open(os.path.join(VILL_DIR, fn))
        page = doc[0]
        crop = page.cropbox
        words = [w for w in spans_of(page) if w[0].isupper() and len(w[0]) >= 4 and " " not in w[0]]
        doc.close()
        if len(words) < 4:
            weak.append((name, "no place names to match on"))
            continue

        best = None
        for q, table in sheet_words.items():
            votes = Counter()
            for t, vx, vy in words:
                for px, py in table.get(t, ()):
                    votes[(round(px - vx, 1), round(py - vy, 1))] += 1
            if not votes:
                continue
            off, n = votes.most_common(1)[0]
            # the offset must be the only one with a real number of votes
            second = votes.most_common(2)[1][1] if len(votes) > 1 else 0
            if best is None or n > best[2]:
                best = (q, off, n, second)
        if best is None or best[2] < 3:
            weak.append((name, "no sheet matched its place names"))
            continue
        q, (dx, dy), n, second = best
        b = blocks[q]
        # the village's content, placed on the sheet
        ux0 = crop.x0 + dx
        uy0 = crop.y0 + dy
        ux1 = ux0 + (crop.x1 - crop.x0)
        uy1 = uy0 + (crop.y1 - crop.y0)
        g = b.ground_of_rect(ux0, uy0, ux1, uy1)
        rec = {
            "name": name, "file": fn, "block": q,
            "matched_place_names": n,
            "runner_up_votes": second,
            "offset_points": [dx, dy],
            "lon_min": round(g[0], 7), "lon_max": round(g[2], 7),
            "lat_min": round(g[1], 7), "lat_max": round(g[3], 7),
            "width_km": round((g[2] - g[0]) * 111320.0
                              * math.cos(math.radians((g[1] + g[3]) / 2)) / 1000, 2),
            "height_km": round((g[3] - g[1]) * 110990.0 / 1000, 2),
            "source": "NEW DP BOOK AUDA/AUDA DP EACH VILLAGE MAP/" + fn,
            "note": "placed by matching the extract's place names against the "
                    "part sheets; the extract's own crop box carries no usable "
                    "position",
        }
        out[name] = rec
        by_block[q].append(name)
        if n < 5 or (second and second > n * 0.5):
            weak.append((name, f"only {n} names matched (runner-up {second})"))

    print("\nassigned by part sheet:")
    for q in ("NW", "NE", "SW", "SE"):
        print(f"  {q}: {len(by_block[q])} villages")
    if weak:
        print(f"\n{len(weak)} with a weak match:")
        for n, why in weak[:15]:
            print(f"  {n}: {why}")

    with open(os.path.join(args.out, "villages.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    index = {
        "note": "each per-village extract is a piece of one of the four part "
                "sheets. All of them quote the same viewport and their crop "
                "boxes are out by a median of 19.7 km, so each is placed by "
                "matching its place names against the sheets.",
        "by_block": {q: sorted(by_block[q]) for q in ("NW", "NE", "SW", "SE")},
        "count": len(out),
    }
    with open(os.path.join(args.out, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
    for name, rec in out.items():
        with open(os.path.join(args.out, name.replace("/", "-") + ".json"), "w",
                  encoding="utf-8") as f:
            json.dump(rec, f, indent=2, ensure_ascii=False)
    print(f"\nwrote {args.out}/villages.json, index.json and one file per village")


if __name__ == "__main__":
    main()
