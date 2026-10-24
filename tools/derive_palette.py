"""Derive the plan's land-use palette empirically, and report what fraction of
the sheet each colour covers.

Rather than hunt for the legend block on the sheet (the detector finds the
dense urban area instead, which is full of tiny plots of many colours), the
palette is characterised the way it actually matters: by clustering the
saturated colours the sheet uses, weighted by how much area each covers,
and reporting the hue and the representative colour of each cluster.

That is enough to build a classifier, because the plan uses a SMALL, FIXED
set of fills - a land-use plan has a legend and sticks to it.
"""
import colorsys, json, os, sys
from collections import Counter

import numpy as np
from PIL import Image

sys.stdout.reconfigure(encoding="utf-8")
Image.MAX_IMAGE_PIXELS = None
ROOT = r"C:\ideabot internships\geomapping"
WORK = os.path.join(ROOT, "work")


def main():
    im = Image.open(os.path.join(WORK, "source.jpeg")).convert("RGB")
    a = np.asarray(im.resize((1360, 1280), Image.LANCZOS)).astype(np.int16)
    R, G, B = a[..., 0], a[..., 1], a[..., 2]
    mx = a.max(axis=2)
    mn = a.min(axis=2)
    sat = (mx - mn) / np.maximum(mx, 1)
    d = mx - mn
    hue = np.zeros_like(mx, dtype=np.int16)
    z = d == 0
    rm = (mx == R) & ~z
    gm = (mx == G) & ~z
    bm = (mx == B) & ~z
    hue[rm] = (60 * (G[rm] - B[rm]) / d[rm]) % 360
    hue[gm] = (60 * (B[gm] - R[gm]) / d[gm] + 120) % 360
    hue[bm] = (60 * (R[bm] - G[bm]) / d[bm] + 240) % 360

    sel = (sat > 0.45) & (mx > 100)
    total = sel.sum()
    print(f"saturated pixels: {total} ({100*total/sel.size:.1f}% of the sheet)\n")

    # cluster by (hue bucket, value bucket) and report the biggest
    hb = (hue[sel] // 10 * 10).astype(int)
    vb = (mx[sel] // 40 * 40).astype(int)
    sb = (sat[sel] // 0.2).astype(int)
    keys = Counter(zip(hb.tolist(), vb.tolist(), sb.tolist()))

    # the representative colour of each cluster
    hs = hue[sel]
    vs = mx[sel]
    ss = sat[sel]
    pix = a[sel]

    clusters = []
    for (h, v, s), n in keys.most_common(40):
        if n < 200:
            continue
        m = (hb == h) & (vb == v) & (sb == s)
        if m.sum() == 0:
            continue
        med = np.median(pix[m], axis=0)
        r, g, b = (q / 255 for q in med)
        h2, s2, v2 = colorsys.rgb_to_hsv(r, g, b)
        clusters.append(dict(hue=int(h), rgb=[int(q) for q in med], n=int(n),
                             pct=round(100 * n / total, 1),
                             exact_hue=round(h2 * 360, 1),
                             exact_sat=round(s2, 2), exact_val=round(v2, 2),
                             hex="#%02x%02x%02x" % tuple(int(q) for q in med)))

    print(f"{'pct':>6}  {'hue':>5}  {'hex':>8}  {'sat':>4} {'val':>4}  n")
    for c in clusters:
        print(f"{c['pct']:6.2f}  {c['exact_hue']:5.1f}  {c['hex']}  "
              f"{c['exact_sat']:4.2f} {c['exact_val']:4.2f}  {c['n']}")

    with open(os.path.join(WORK, "palette.json"), "w", encoding="utf-8") as f:
        json.dump(clusters, f, indent=1)
    print(f"\nwrote work/palette.json")


if __name__ == "__main__":
    main()
