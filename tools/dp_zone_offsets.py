"""Measure the plan's offset from the satellite, zone by zone.

The global accuracy figure says the plan sits a median of 19.8 m from
today's satellite. That is one number for the whole book, and it hides
anything different about one zone - a village whose roads were realigned, a
canal that was straightened, a stretch where the plan is simply out of date.

So the same measurement is run again and every sample is attributed to the
land-use zone it falls in. This is a measurement, not a land-cover
classification: the imagery is a dry-season capture, so reading water and
vegetation out of RGB alone is not defensible, but measuring how far the
plan's own road corridors are from the satellite's is.

Both rasters are read at 4.4 m/px and every window is centred on the same
ground point, so the correlation compares like with like.

Output: data/dp2021/zone_offsets.json
        work/newdp/zone_offsets.md
"""
import argparse
import json
import math
import os
import sys
import time
from collections import Counter, defaultdict

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dp_georef as G  # noqa: E402
import dp_zone_masks as ZM  # noqa: E402

ROOT = G.ROOT
SAT = os.path.join(ROOT, "satellite")
Z = 15
M_PER_PX = ZM.M_PER_PX


def merc_y(lat):
    return math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def sat_window(lon_min, lon_max, lat_min, lat_max, z):
    n = 256 * 2 ** z
    gx0 = int(math.floor((lon_min + 180.0) / 360.0 * n))
    gx1 = int(math.ceil((lon_max + 180.0) / 360.0 * n))
    gy0 = int(math.floor((1.0 - merc_y(lat_max) / 180.0) / 2.0 * n))
    gy1 = int(math.ceil((1.0 - merc_y(lat_min) / 180.0) / 2.0 * n))
    out = np.zeros((gy1 - gy0, gx1 - gx0, 3), np.uint8)
    for tx in range(gx0 // 256, (gx1 + 255) // 256 + 1):
        for ty in range(gy0 // 256, (gy1 + 255) // 256 + 1):
            p = os.path.join(SAT, str(z), str(tx), f"{ty}.jpg")
            if not os.path.exists(p):
                continue
            with Image.open(p) as im:
                tile = np.asarray(im.convert("RGB"))
            ix0, iy0 = max(gx0, tx * 256), max(gy0, ty * 256)
            ix1, iy1 = min(gx1, (tx + 1) * 256), min(gy1, (ty + 1) * 256)
            if ix1 <= ix0 or iy1 <= iy0:
                continue
            out[iy0 - gy0:iy1 - gy0, ix0 - gx0:ix1 - gx0] = \
                tile[iy0 - ty * 256:iy1 - ty * 256, ix0 - tx * 256:ix1 - tx * 256]
    return out, gx0, gy0, n


def bright_mask(rgb):
    """The plan's white paper corridors; the satellite's grey road surface."""
    mx = rgb.max(axis=2).astype(np.int16)
    mn = rgb.min(axis=2).astype(np.int16)
    sat = (mx - mn) / np.maximum(mx, 1)
    return (mn > 100) & (sat < 0.22)


def best_shift_fft(plan, sat):
    h, w = plan.shape
    ph, pw = h * 2, w * 2
    P = np.zeros((ph, pw))
    P[:h, :w] = plan
    S = np.zeros((ph, pw))
    S[:h, :w] = sat
    cc = np.fft.irfft2(np.fft.rfft2(S) * np.fft.rfft2(P[::-1, ::-1]), s=(ph, pw))
    idx = np.unravel_index(np.argmax(cc), cc.shape)
    dy = idx[0] if idx[0] <= ph // 2 else idx[0] - ph
    dx = idx[1] if idx[1] <= pw // 2 else idx[1] - pw
    return dy, dx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--patch", type=int, default=40, help="window half-size, px")
    ap.add_argument("--stride", type=int, default=120, help="sample spacing, px")
    ap.add_argument("--max-shift", type=int, default=20)
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "dp2021", "zone_offsets.json"))
    ap.add_argument("--report", default=os.path.join(ROOT, "work", "newdp", "zone_offsets.md"))
    args = ap.parse_args()

    blocks, _ = G.build_blocks()
    classes = ZM.legend_classes()
    names = [c["label"] for c in classes]
    hexes = [c["hex"] for c in classes]
    zone_dir = os.path.join(ROOT, "work", "newdp", "zones")
    m_per_px = 156543.03392 * math.cos(math.radians(23.06)) / (2 ** Z)

    per_zone = defaultdict(list)
    per_block = defaultdict(list)
    t_all = time.time()
    for quad, block in blocks.items():
        print(f"[{quad}] reading the plan and the satellite ...", flush=True)
        npz = np.load(os.path.join(zone_dir, f"{quad}.npz"))
        zone_lab = npz["labels"]
        zoom = float(npz["zoom"][0])
        plan_rgb, _ = ZM.render_sheet(block)
        plan_bright = plan_rgb.min(axis=2) > 150
        sat, gx0, gy0, n = sat_window(block.lon_min, block.lon_max,
                                      block.lat_min, block.lat_max, Z)
        sat_bright = bright_mask(sat)
        del sat
        crop = __import__("fitz").open(block.path)[0].cropbox

        half = args.patch
        rows, cols = np.nonzero(zone_lab >= 0)
        step = max(1, len(rows) // 40000)
        rows, cols = rows[::step], cols[::step]
        t0 = time.time()
        used = 0
        for r, c in zip(rows.tolist(), cols.tolist()):
            pi = int(zone_lab[r, c])
            # the same ground point in the satellite's grid
            ux = c / zoom + crop.x0
            uy = crop.y1 - r / zoom
            mx = block.e0 + block.s * (ux - block.llx)
            my = block.n0 + block.s * (uy - block.ury)
            lon, lat = G.FWD.transform(mx, my)
            gx = int((lon + 180.0) / 360.0 * n) - gx0
            gy = int((1.0 - merc_y(lat) / 180.0) / 2.0 * n) - gy0
            r0, r1 = r - half, r + half
            c0, c1 = c - half, c + half
            g0, g1 = gy - half, gy + half
            if r0 < 0 or c0 < 0 or r1 > plan_bright.shape[0] or c1 > plan_bright.shape[1]:
                continue
            if g0 < 0 or g1 < 0 or g1 > sat_bright.shape[0] or g1 > sat_bright.shape[1]:
                continue
            pp = plan_bright[r0:r1, c0:c1]
            sp = sat_bright[g0:g1, g0:g1]
            if pp.mean() < 0.06 or pp.mean() > 0.60:
                continue
            if sp.mean() < 0.05 or sp.mean() > 0.60:
                continue
            dy, dx = best_shift_fft(pp, sp)
            if abs(dx) > args.max_shift or abs(dy) > args.max_shift:
                continue
            s = np.roll(np.roll(sp, dy, axis=0), dx, axis=1)
            inter = np.logical_and(pp, s).sum()
            union = np.logical_or(pp, s).sum()
            iou = inter / union if union else 0
            if iou < 0.05:
                continue
            dlon = dx * m_per_px
            dmerc = dy * m_per_px
            per_zone[pi].append((dlon, dmerc, float(iou)))
            per_block[quad].append((dlon, dmerc, float(iou)))
            used += 1
        print(f"    {used:,} windows measured ({time.time()-t0:.0f}s)", flush=True)
        del plan_rgb, plan_bright, sat_bright, zone_lab

    # the table
    out = {"method": "the plan's road corridors against the satellite's, in "
                     "windows centred on points of each land-use zone, at 4.4 m/px; "
                     "the offset is the FFT cross-correlation peak",
           "zones": {}, "blocks": {}}
    rows_md = ["| plan zone | colour | plan area km2 | samples | offset median m | "
               "p90 m | mean IoU |",
               "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    area_by_zone = {}
    for quad, block in blocks.items():
        npz = np.load(os.path.join(zone_dir, f"{quad}.npz"))
        lab = npz["labels"]
        vals, counts = np.unique(lab[lab >= 0], return_counts=True)
        for v, c in zip(vals.tolist(), counts.tolist()):
            area_by_zone[v] = area_by_zone.get(v, 0) + c * M_PER_PX * M_PER_PX / 1e6
    for pi in range(len(names)):
        v = per_zone.get(pi)
        if not v or len(v) < 5:
            continue
        arr = np.array(v)
        d = np.hypot(arr[:, 0], arr[:, 1])
        out["zones"][names[pi]] = {
            "colour": hexes[pi],
            "plan_area_km2": round(area_by_zone.get(pi, 0), 1),
            "samples": len(v),
            "offset_median_m": round(float(np.median(d)), 1),
            "offset_p90_m": round(float(np.percentile(d, 90)), 1),
            "dlon_median_m": round(float(np.median(arr[:, 0])), 1),
            "dmerc_median_m": round(float(np.median(arr[:, 1])), 1),
            "mean_iou": round(float(arr[:, 2].mean()), 3),
        }
        rows_md.append(f"| {names[pi]} | {hexes[pi]} | {area_by_zone.get(pi,0):,.0f} | "
                       f"{len(v):,} | {np.median(d):.1f} | {np.percentile(d,90):.1f} | {arr[:,2].mean():.3f} |")
    for quad, v in per_block.items():
        arr = np.array(v)
        d = np.hypot(arr[:, 0], arr[:, 1])
        out["blocks"][quad] = {"samples": len(v),
                               "offset_median_m": round(float(np.median(d)), 1),
                               "offset_p90_m": round(float(np.percentile(d, 90)), 1),
                               "mean_iou": round(float(arr[:, 2].mean()), 3)}
    allv = np.array([x for v in per_zone.values() for x in v])
    d = np.hypot(allv[:, 0], allv[:, 1])
    out["overall"] = {"samples": len(allv), "offset_median_m": round(float(np.median(d)), 1),
                      "offset_p90_m": round(float(np.percentile(d, 90)), 1)}

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"\nwrote {args.out}")
    md = ["# The plan against today's satellite, zone by zone", "",
          out["method"], "",
          "## The four parts", "",
          "| part | samples | offset median m | p90 m | mean IoU |", "| --- | ---: | ---: | ---: | ---: |"]
    for quad in ("NW", "NE", "SW", "SE"):
        b = out["blocks"].get(quad)
        if b:
            md.append(f"| {quad} | {b['samples']:,} | {b['offset_median_m']} | "
                      f"{b['offset_p90_m']} | {b['mean_iou']} |")
    md += ["", "## The zones", "", *rows_md, "",
           f"**Overall: {out['overall']['offset_median_m']} m median, "
           f"{out['overall']['offset_p90_m']} m p90, over "
           f"{out['overall']['samples']:,} windows.**", ""]
    with open(args.report, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"wrote {args.report}\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
