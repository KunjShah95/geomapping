"""Read the plan's own land-use legend off the sheet.

The legend is drawn on the sheet as a column of colour swatches, each with a
label beside it. Reading it from the drawing objects means the legend in the
viewer is the plan's legend, with the plan's exact colours, rather than a
list someone typed out by eye.

The legend block sits in the top-left corner of the north-west sheet.

Output: data/dp2021/legend.json
"""
import json
import os
import sys
from collections import Counter

import fitz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dp_georef as G  # noqa: E402

ROOT = G.ROOT
OUT = os.path.join(ROOT, "data", "dp2021", "legend.json")

# the legend block on the north-west sheet, in page coordinates (y down)
LEGEND_BOX = (60, 60, 1200, 3400)


def clean(t):
    """The sheets carry a few unprintable glyphs; they are not worth rendering."""
    t = "".join(ch for ch in t if ch.isprintable() and ch not in "\ufffd\ufffe\uffff")
    return " ".join(t.split())


def is_swatch(d):
    r = d["rect"]
    if r.is_empty or r.is_infinite:
        return False
    w, h = r.width, r.height
    # the legend's swatches are 114.7 x 57.6 pt rectangles in a single column
    if not (60.0 <= w <= 200.0 and 30.0 <= h <= 100.0):
        return False
    if d.get("fill_opacity", 1) in (0, None):
        return False
    if not d.get("fill"):
        return False
    return True


def main():
    blocks, _ = G.build_blocks()
    block = blocks["NW"]
    doc = fitz.open(block.path)
    page = doc[0]
    crop = page.cropbox
    print("scanning drawings for legend swatches ...")

    swatches = []
    for d in page.get_drawings():
        r = d["rect"]
        if r.x1 < LEGEND_BOX[0] or r.x0 > LEGEND_BOX[2]:
            continue
        if r.y1 < LEGEND_BOX[1] or r.y0 > LEGEND_BOX[3]:
            continue
        if not is_swatch(d):
            continue
        fill = d.get("fill")
        if not fill:
            continue
        swatches.append({
            "x": (r.x0 + r.x1) / 2, "y": (r.y0 + r.y1) / 2,
            "w": r.width, "h": r.height,
            "rgb": "#%02x%02x%02x" % tuple(int(round(c * 255)) for c in fill),
        })
    # keep one per position
    uniq = {}
    for s in swatches:
        uniq[(round(s["x"]), round(s["y"]))] = s
    swatches = sorted(uniq.values(), key=lambda s: (s["x"], s["y"]))
    print(f"  {len(swatches)} candidate swatches")

    # the labels: characters, not spans. Esri emits the glyphs of a legend
    # label as separate runs in an order that is not reading order, so a span
    # reads "Insttuuonal" where the sheet says "Institutional". Sorting the
    # characters by x puts them back.
    labels = []
    d = page.get_text("rawdict")
    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        for line in b.get("lines", []):
            for sp in line.get("spans", []):
                for ch in sp.get("chars", []):
                    cx, cy = ch["bbox"][0], ch["bbox"][1]
                    if cx < LEGEND_BOX[0] or cx > LEGEND_BOX[2]:
                        continue
                    if cy < LEGEND_BOX[1] or cy > LEGEND_BOX[3]:
                        continue
                    labels.append({"c": ch["c"], "x0": cx, "x1": ch["bbox"][2], "y": cy,
                                   "x": (ch["bbox"][0] + ch["bbox"][2]) / 2,
                                   "y": (ch["bbox"][1] + ch["bbox"][3]) / 2,
                                   "sz": sp.get("size", 5.0)})

    entries = []
    for s in swatches:
        # the label sits to the right of the swatch, on the same line
        cands = [l for l in labels
                 if l["x0"] >= s["x"] + s["w"] / 2 - 6 and abs(l["y"] - s["y"]) < 12]
        if not cands:
            continue
        cands.sort(key=lambda l: l["x0"])
        # a space is a real gap between glyphs, not every gap: the sheets run
        # some words together with wide tracking, which reads as "A ffordabl e".
        out = []
        prev = None
        for l in cands:
            if prev is not None and l["x0"] - prev["x1"] > 0.22 * l.get("sz", 5.0):
                out.append(" ")
            out.append(l["c"])
            prev = l
        text = clean("".join(out))
        if not text:
            continue
        entries.append({"color": s["rgb"], "label": text,
                        "x": round(s["x"], 1), "y": round(s["y"], 1)})

    # a few swatches sit on the same line as a label that is drawn in two
    # pieces; merge entries that share a label
    merged = []
    for e in entries:
        if merged and merged[-1]["label"] == e["label"] and \
                abs(merged[-1]["y"] - e["y"]) < 9:
            continue
        merged.append(e)

    print(f"  {len(merged)} legend entries")
    for e in merged:
        print(f"    {e['color']}  {e['label']}")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"source": "read from the legend block of DP 2021 AUDA Final-1.pdf",
                   "entries": merged}, f, indent=2, ensure_ascii=False)
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
