"""Verify the records against Google Earth, and list the ones that disagree.

The measurement is the one the whole project rests on: the plan's road
corridors against the satellite's, by FFT cross-correlation. What is added
here is attribution - every measurement window is tied to the survey record
nearest its centre, and therefore to that record's village and its
land-use zone.

That gives three things the client asked for:

  * how far the plan sits from Google Earth, record by record and village
    by village;
  * the same, for each land-use zone;
  * a ranked list of the records where the plan and Google Earth disagree
    most - the ones worth a human looking at.

Output: data/dp2021/record_checks.json
        data/dp2021/record_disagreements.json
        work/newdp/record_check.md
"""
import argparse
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dp_georef as G  # noqa: E402
from dp_measure_accuracy import build_masks, best_shift_fft  # noqa: E402

ROOT = G.ROOT
TILES = os.path.join(ROOT, "tiles_dp2021")
RECORDS = os.path.join(ROOT, "data", "dp2021", "records.ndjson")


def load_records():
    recs = []
    with open(RECORDS, encoding="utf-8") as f:
        for line in f:
            recs.append(json.loads(line))
    return recs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--z", type=int, default=16)
    ap.add_argument("--patch", type=int, default=90)
    ap.add_argument("--stride", type=int, default=260)
    ap.add_argument("--max-shift", type=int, default=24)
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "dp2021", "record_checks.json"))
    args = ap.parse_args()

    recs = load_records()
    print(f"{len(recs):,} records")
    # a coarse grid over the records, for the nearest-record lookup
    cell = 0.01          # degrees, about 1 km
    grid = defaultdict(list)
    for i, r in enumerate(recs):
        grid[(int(r["lon"] / cell), int(r["lat"] / cell))].append(i)

    def nearest(lon, lat):
        gx, gy = int(lon / cell), int(lat / cell)
        best, bd = -1, 1e18
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i in grid.get((gx + dx, gy + dy), ()):
                    r = recs[i]
                    d = (r["lon"] - lon) ** 2 + (r["lat"] - lat) ** 2
                    if d < bd:
                        bd, best = d, i
        return best, math.sqrt(bd) * 111320.0 * math.cos(math.radians(lat))

    print("building the masks ...", flush=True)
    t0 = time.time()
    p_mask, px0, py0 = build_masks(TILES, args.z, plan=True)
    s_mask, sx0, sy0 = build_masks(os.path.join(ROOT, "satellite"), args.z, plan=False)
    print(f"  {time.time()-t0:.0f}s", flush=True)

    # build_masks returns TILE indices; the masks start at tile x 256 px
    ox = max(px0, sx0) * 256
    oy = max(py0, sy0) * 256
    p_mask = p_mask[oy - py0 * 256:, ox - px0 * 256:]
    s_mask = s_mask[oy - sy0 * 256:, ox - sx0 * 256:]
    h = min(p_mask.shape[0], s_mask.shape[0])
    w = min(p_mask.shape[1], s_mask.shape[1])
    p_mask = p_mask[:h, :w]
    s_mask = s_mask[:h, :w]

    n = 2 ** args.z
    deg = 360.0 / n / 256.0
    m_per_px = deg * 111320.0 * math.cos(math.radians(23.06))
    print(f"  z{args.z}: {m_per_px:.2f} m/px")

    results = []
    by_village = defaultdict(list)
    by_zone = defaultdict(list)
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
                    iou = inter / union if union else 0
                    if iou >= 0.05:
                        lon = (x + ox) * deg - 180.0
                        my = 180.0 - (y + oy) * deg
                        lat = math.degrees(2 * math.atan(math.exp(math.radians(my))) - math.pi / 2)
                        dlon = dx * m_per_px
                        dmerc = dy * m_per_px
                        shift = math.hypot(dlon, dmerc)
                        i, dist_m = nearest(lon, lat)
                        r = recs[i] if i >= 0 else {}
                        rec = {"lon": lon, "lat": lat,
                               "offset_m": round(shift, 1),
                               "dlon_m": round(dlon, 1), "dmerc_m": round(dmerc, 1),
                               "iou": round(float(iou), 3),
                               "record": r.get("t"), "village": r.get("village"),
                               "zone": r.get("zone"), "block": r.get("block"),
                               "record_lon": r.get("lon"), "record_lat": r.get("lat"),
                               "record_m": round(dist_m, 1)}
                        results.append(rec)
                        if r.get("village"):
                            by_village[r["village"]].append(shift)
                        if r.get("zone"):
                            by_zone[r["zone"]].append(shift)
            x += args.stride
        y += args.stride

    if len(results) < 15:
        raise SystemExit("too few usable samples")
    d = np.array([r["offset_m"] for r in results])
    print(f"\n=== RECORDS vs GOOGLE EARTH ===")
    print(f"  {len(results):,} windows, each tied to the nearest survey record")
    print(f"  offset median {np.median(d):.1f} m   p90 {np.percentile(d,90):.1f} m")

    out = {"method": "the plan's road corridors against the Google Earth "
                     "imagery by FFT cross-correlation, every window tied to the "
                     "nearest survey record",
           "windows": len(results),
           "offset_median_m": round(float(np.median(d)), 1),
           "offset_p90_m": round(float(np.percentile(d, 90)), 1),
           "by_village": {}, "by_zone": {}}
    for v, arr in by_village.items():
        if len(arr) >= 3:
            out["by_village"][v] = {"windows": len(arr),
                                    "median_m": round(float(np.median(arr)), 1),
                                    "p90_m": round(float(np.percentile(arr, 90)), 1)}
    for v, arr in by_zone.items():
        if len(arr) >= 3:
            out["by_zone"][v] = {"windows": len(arr),
                                 "median_m": round(float(np.median(arr)), 1),
                                 "p90_m": round(float(np.percentile(arr, 90)), 1)}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"wrote {args.out}")

    # the disagreements
    worst = sorted(results, key=lambda r: -r["offset_m"])[:400]
    dis = {"note": "the measurements where the plan and Google Earth disagree "
                   "most - the records worth a human looking at",
           "records": worst}
    p = os.path.join(ROOT, "data", "dp2021", "record_disagreements.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(dis, f, indent=2, ensure_ascii=False)
    print(f"wrote {p}")

    md = ["# The records against Google Earth", "", out["method"], "",
          f"**{len(results):,} measurements. Offset from Google Earth: "
          f"{out['offset_median_m']} m median, {out['offset_p90_m']} m p90.**", "",
          "## By village", "",
          "| village | windows | offset median m | p90 m |", "| --- | ---: | ---: | ---: |"]
    for v, a in sorted(out["by_village"].items(), key=lambda kv: -kv[1]["median_m"])[:30]:
        md.append(f"| {v} | {a['windows']:,} | {a['median_m']} | {a['p90_m']} |")
    md += ["", "## By land-use zone", "",
           "| zone | windows | offset median m | p90 m |", "| --- | ---: | ---: | ---: |"]
    for v, a in sorted(out["by_zone"].items(), key=lambda kv: -kv[1]["median_m"]):
        md.append(f"| {v} | {a['windows']:,} | {a['median_m']} | {a['p90_m']} |")
    md += ["", "## The largest disagreements", "",
           "| record | village | zone | offset m | at |", "| --- | --- | --- | ---: | --- |"]
    for r in worst[:25]:
        md.append(f"| {r['record']} | {r['village'] or '-'} | {r['zone'] or '-'} | "
                  f"{r['offset_m']} | {r['lat']:.5f}, {r['lon']:.5f} |")
    md.append("")
    p = os.path.join(ROOT, "work", "newdp", "record_check.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"wrote {p}")
    print(f"({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
