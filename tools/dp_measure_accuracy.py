"""Measure how far the new DP 2021 map sits from the present-day satellite.

WHAT THIS MEASURES, PRECISELY
-----------------------------
The plan was drawn from a survey of the ground as it was. Roads have been
widened, realigned and built over since. So the offset between this plan and
today's satellite imagery is the sum of two things:

  * the georeference's own error, and
  * the age of the base map the plan was drawn on.

The two cannot be separated by looking at the satellite. What CAN be measured
is the georeference's own error, and that is done in dp_georef.py by fitting
the 2x2 book grid to all sixteen published sheet corners: the residual is
about 2.2 m, and it is a property of the source, not of this processing.

This script measures the OTHER half - the total plan-to-satellite offset - so
that the figure quoted for the new map can be compared, on the same basis,
with the figure quoted for the old one. Both plans cover the same ground, so
the difference between the two numbers is the difference between the two
surveys, and the georeference contributes almost nothing to either.

METHOD
------
Both the plan and the satellite are read at z16, in the same global web
mercator pixel grid, so they are compared pixel for pixel with no resampling.
The plan's paper-white road corridors and the satellite's bright roads are the
same roads; the cross-correlation peak is the offset between them.

Output: work/dp_accuracy.json
"""
import argparse
import json
import math
import os
import time

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAT = os.path.join(ROOT, "satellite")
TILES = os.path.join(ROOT, "tiles_dp2021")
WORK = os.path.join(ROOT, "work")


def walk_tiles(root, z, exts=(".jpg", ".png", ".webp")):
    """(tx, ty, path) for every tile of a level, without globbing 60k paths."""
    out = []
    d = os.path.join(root, str(z))
    if not os.path.isdir(d):
        return out
    for txname in os.listdir(d):
        txdir = os.path.join(d, txname)
        if not os.path.isdir(txdir):
            continue
        try:
            tx = int(txname)
        except ValueError:
            continue
        for fn in os.listdir(txdir):
            if not fn.lower().endswith(exts):
                continue
            try:
                ty = int(os.path.splitext(fn)[0])
            except ValueError:
                continue
            out.append((tx, ty, os.path.join(txdir, fn)))
    return out


def build_masks(tiles, z, plan):
    """Bright (road/paper) mask for both, in the same global pixel grid."""
    tlist = walk_tiles(tiles, z)
    if not tlist:
        raise SystemExit(f"no tiles at {tiles}/{z}")
    xs = [t[0] for t in tlist]
    ys = [t[1] for t in tlist]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    w = (x1 - x0 + 1) * 256
    h = (y1 - y0 + 1) * 256
    mask = np.zeros((h, w), bool)
    for tx, ty, f in tlist:
        with Image.open(f) as im:
            a = np.asarray(im.convert("RGB")).astype(np.int16)
        if plan:
            bright = a.min(axis=2) > 150          # paper-white roads and sheet
        else:
            mx = a.max(axis=2)
            mn = a.min(axis=2)
            sat = (mx - mn) / np.maximum(mx, 1)
            bright = (mn > 100) & (sat < 0.22)    # grey road surface
        del a
        mask[(ty - y0) * 256:(ty - y0 + 1) * 256,
             (tx - x0) * 256:(tx - x0 + 1) * 256] = bright
    return mask, x0, y0


def best_shift_fft(plan, sat):
    h, w = plan.shape
    ph, pw = h * 2, w * 2
    P = np.zeros((ph, pw), np.float64)
    P[:h, :w] = plan
    S = np.zeros((ph, pw), np.float64)
    S[:h, :w] = sat
    F = np.fft.rfft2(S)
    G = np.fft.rfft2(P[::-1, ::-1])
    cc = np.fft.irfft2(F * G, s=(ph, pw))
    idx = np.unravel_index(np.argmax(cc), cc.shape)
    dy = idx[0] if idx[0] <= ph // 2 else idx[0] - ph
    dx = idx[1] if idx[1] <= pw // 2 else idx[1] - pw
    return dy, dx, float(cc[idx])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--z", type=int, default=16)
    ap.add_argument("--patch", type=int, default=90, help="window half-size, px")
    ap.add_argument("--stride", type=int, default=260, help="sample spacing, px")
    ap.add_argument("--max-shift", type=int, default=24)
    ap.add_argument("--out", default=os.path.join(WORK, "dp_accuracy.json"))
    args = ap.parse_args()

    print(f"building the plan's bright mask from {TILES}/{args.z} ...", flush=True)
    t0 = time.time()
    p_mask, px0, py0 = build_masks(TILES, args.z, plan=True)
    print(f"  {p_mask.shape[1]} x {p_mask.shape[0]} px, bright {p_mask.mean():.3f} "
          f"({time.time()-t0:.0f}s)", flush=True)

    print("building the satellite's bright mask ...", flush=True)
    t0 = time.time()
    s_mask, sx0, sy0 = build_masks(SAT, args.z, plan=False)
    print(f"  {s_mask.shape[1]} x {s_mask.shape[0]} px, bright {s_mask.mean():.3f} "
          f"({time.time()-t0:.0f}s)", flush=True)

    n = 2 ** args.z
    deg_per_px = 360.0 / n / 256.0
    m_per_px = deg_per_px * 111320.0 * math.cos(math.radians(23.06))
    print(f"  z{args.z}: {m_per_px:.2f} m/px")

    # both masks must share the same origin for a direct comparison
    ox = max(px0, sx0)
    oy = max(py0, sy0)
    p_mask = p_mask[oy - py0:, ox - px0:]
    s_mask = s_mask[oy - sy0:, ox - sx0:]
    h = min(p_mask.shape[0], s_mask.shape[0])
    w = min(p_mask.shape[1], s_mask.shape[1])
    p_mask = p_mask[:h, :w]
    s_mask = s_mask[:h, :w]

    results = []
    t0 = time.time()
    y = args.patch + 40
    while y < h - args.patch - 40:
        x = args.patch + 40
        while x < w - args.patch - 40:
            p = p_mask[y - args.patch:y + args.patch, x - args.patch:x + args.patch]
            s = s_mask[y - args.patch:y + args.patch, x - args.patch:x + args.patch]
            if 0.06 < p.mean() < 0.55 and 0.05 < s.mean() < 0.60:
                dy, dx, peak = best_shift_fft(p, s)
                if abs(dx) <= args.max_shift and abs(dy) <= args.max_shift:
                    ss = np.roll(np.roll(s, dy, axis=0), dx, axis=1)
                    inter = np.logical_and(p, ss).sum()
                    union = np.logical_or(p, ss).sum()
                    iou = inter / union if union else 0.0
                    if iou >= 0.05:
                        results.append(dict(x=int(x), y=int(y), dx=int(dx), dy=int(dy),
                                            iou=float(iou),
                                            dlon_m=float(dx * m_per_px),
                                            dmerc_m=float(dy * m_per_px)))
            x += args.stride
        y += args.stride

    if len(results) < 15:
        raise SystemExit(f"only {len(results)} usable samples - the measurement "
                         "would not mean anything")

    dxm = np.array([r["dlon_m"] for r in results])
    dym = np.array([r["dmerc_m"] for r in results])
    shift = np.hypot(dxm, dym)
    cxp, cyp = w / 2, h / 2
    xs = np.array([r["x"] for r in results], float)
    ys = np.array([r["y"] for r in results], float)

    print(f"\n=== PLAN vs SATELLITE OFFSET (z{args.z}) ===")
    print(f"  n = {len(results)}")
    print(f"  |offset|  median {np.median(shift):6.1f} m   mean {shift.mean():6.1f} m"
          f"   p90 {np.percentile(shift, 90):6.1f} m")
    print(f"  dlon      median {np.median(dxm):+7.1f} m")
    print(f"  dmerc     median {np.median(dym):+7.1f} m")
    print(f"  mean IoU  {np.mean([r['iou'] for r in results]):.3f}")
    for name, sel in (("near centre", np.hypot(xs - cxp, ys - cyp) < 6000),
                      ("far  centre", np.hypot(xs - cxp, ys - cyp) >= 6000),
                      ("west", xs < cxp), ("east", xs >= cxp),
                      ("north", ys < cyp), ("south", ys >= cyp)):
        if sel.sum() < 3:
            continue
        print(f"  {name:12s} median {np.median(shift[sel]):6.1f} m   (n={int(sel.sum())})")

    out = dict(source="dp_measure_accuracy.py", z=args.z, n=len(results),
               offset_median_m=float(np.median(shift)),
               offset_mean_m=float(shift.mean()),
               offset_p90_m=float(np.percentile(shift, 90)),
               dlon_median_m=float(np.median(dxm)),
               dmerc_median_m=float(np.median(dym)),
               mean_iou=float(np.mean([r["iou"] for r in results])),
               m_per_px=m_per_px, results=results)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print(f"\nwrote {args.out}  ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
