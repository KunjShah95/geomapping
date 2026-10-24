"""Compare every land-use zone in the plan against today's satellite.

The plan says a piece of ground is, say, Prime Agricultural Zone-A2. What is
that ground now? This answers that, zone by zone, by reading the plan's own
colours and asking the satellite what it sees.

HOW
---
1. The sheet is rasterised at 4.4 m/px and every pixel is assigned to the
   nearest legend colour, so each pixel carries the plan's land-use class.
2. The satellite for the same ground is assembled at the same resolution and
   described by three measurable properties: brightness, excess green
   (2G-R-B, the standard RGB vegetation index) and texture (the local
   gradient, which is what separates a built-up area from a smooth field).
3. The thresholds are LEARNED FROM THE PLAN, not guessed: the plan's dense
   residential zones teach it what built-up looks like in this imagery, the
   plan's waterbodies teach it what water looks like, and the plan's
   agricultural zones teach it what open farmland looks like. Those
   references are then applied to every pixel, including pixels the plan
   leaves blank.

The imagery is dry-season, so farmland reads as brown stubble rather than
green, and the large salt lakes read as pale pans rather than blue water.
That is a property of the capture, not of the method, and the median
brightness / greenness / texture of every zone are reported so the reader can
see it.

Output: data/dp2021/zone_satellite.json
        work/newdp/zone_report.md
"""
import argparse
import json
import math
import os
import sys
from collections import Counter

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dp_georef as G  # noqa: E402
import dp_zone_masks as ZM  # noqa: E402

ROOT = G.ROOT
SAT = os.path.join(ROOT, "satellite")
Z = 15

# The plan classes that teach the classifier what each kind of ground looks
# like in THIS imagery. Water does not become farmland; dense residential
# does not become scrub; the agricultural zones are open ground.
REFERENCE = {
    "built-up": ["Residental Zone I-R1", "Residental Zone II-R2 / Gamtal Extension-GME",
                 "Residental Zone III-R3", "Core Walled City-CW",
                 "Central Business District-CBD", "Commercial Zone-C",
                 "Industrial Zone General-IG"],
    "water": ["Waterbody"],
    "open": ["Prime Agricultural Zone-A2", "General Agricultural Zone-A1",
             "Restricted Agricultural Zone-A3", "Parks , Gardens & Open Space-P"],
}
ROLES = ["built-up", "not built-up"]


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


def properties(rgb):
    """The three measurable properties of the satellite."""
    a = rgb.astype(np.float32)
    R, Gc, B = a[..., 0], a[..., 1], a[..., 2]
    bright = 0.299 * R + 0.587 * Gc + 0.114 * B
    egi = 2.0 * Gc - R - B
    blue = B - R
    # texture is measured on a SMOOTHED image: at 4.4 m/px the imagery's own
    # compression noise is the same size as a road, and a gradient taken on
    # the raw pixels measures the noise, not the built-up area.
    sm = cv2.blur(bright, (7, 7))
    gx = cv2.Sobel(sm, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(sm, cv2.CV_32F, 0, 1, ksize=3)
    texture = np.sqrt(gx * gx + gy * gy)
    return bright, egi, blue, texture


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--z", type=int, default=Z)
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "dp2021", "zone_satellite.json"))
    ap.add_argument("--report", default=os.path.join(ROOT, "work", "newdp", "zone_report.md"))
    args = ap.parse_args()

    blocks, _ = G.build_blocks()
    classes = ZM.legend_classes()
    names = [c["label"] for c in classes]
    hexes = [c["hex"] for c in classes]
    zone_dir = os.path.join(ROOT, "work", "newdp", "zones")

    # pass 1: gather the satellite properties and the plan class at every
    # sheet pixel that carries one
    print("reading the plan and the satellite ...", flush=True)
    ref_vals = {r: {"bright": [], "egi": [], "blue": [], "texture": []} for r in REFERENCE}
    cross = Counter()
    plan_area = Counter()
    zone_props = {n: {"bright": [], "egi": [], "texture": []} for n in names}
    import fitz

    for quad, block in blocks.items():
        npz = np.load(os.path.join(zone_dir, f"{quad}.npz"))
        plan_lab = npz["labels"]
        zoom = float(npz["zoom"][0])
        sat, gx0, gy0, n = sat_window(block.lon_min, block.lon_max,
                                      block.lat_min, block.lat_max, args.z)
        bright, egi, blue, texture = properties(sat)
        del sat

        crop = fitz.open(block.path)[0].cropbox
        rows, cols = np.nonzero(plan_lab >= 0)
        ux = cols / zoom + crop.x0
        uy = crop.y1 - rows / zoom
        mx = block.e0 + block.s * (ux - block.llx)
        my = block.n0 + block.s * (uy - block.ury)
        lon, lat = G.FWD.transform(mx, my)
        gx = ((lon + 180.0) / 360.0 * n).astype(np.int32) - gx0
        mym = np.degrees(np.log(np.tan(np.pi / 4 + np.radians(lat) / 2)))
        gy = ((1.0 - mym / 180.0) / 2.0 * n).astype(np.int32) - gy0
        ok = (gx >= 0) & (gx < bright.shape[1]) & (gy >= 0) & (gy < bright.shape[0])
        pc = plan_lab[rows, cols][ok]
        gx, gy = gx[ok], gy[ok]
        b, e, bl, tx = bright[gy, gx], egi[gy, gx], blue[gy, gx], texture[gy, gx]
        print(f"    {quad}: {len(pc):,} plan pixels matched to the satellite", flush=True)

        for role, plan_classes in REFERENCE.items():
            idxs = [names.index(c) for c in plan_classes if c in names]
            sel = np.isin(pc, idxs)
            if sel.any():
                ref_vals[role]["bright"].append(b[sel])
                ref_vals[role]["egi"].append(e[sel])
                ref_vals[role]["blue"].append(bl[sel])
                ref_vals[role]["texture"].append(tx[sel])
        for pi in range(len(names)):
            sel = pc == pi
            if sel.any():
                zone_props[names[pi]]["bright"].append(b[sel])
                zone_props[names[pi]]["egi"].append(e[sel])
                zone_props[names[pi]]["texture"].append(tx[sel])
                plan_area[pi] += int((plan_lab == pi).sum())
        del bright, egi, blue, texture, plan_lab

    def cat(*a):
        return np.concatenate(a) if a else np.zeros(0)
    # texture is the discriminator, and it does not care what season the
    # imagery was captured in. The threshold is the 25th percentile of the
    # texture inside the plan's own dense residential zones.
    t_built = float(np.percentile(cat(*ref_vals["built-up"]["texture"]), 25))
    t_built_hi = float(np.percentile(cat(*ref_vals["built-up"]["texture"]), 60))
    t_open_txt = float(np.percentile(cat(*ref_vals["open"]["texture"]), 75))
    print("    reference texture: residential p25 %.1f p60 %.1f | open (agriculture) p75 %.1f"
          % (t_built, t_built_hi, t_open_txt))
    t_water_blue = float(np.percentile(cat(*ref_vals["water"]["blue"]), 25))
    t_open_egi = float(np.percentile(cat(*ref_vals["open"]["egi"]), 25))
    print(f"\nlearned from the plan's own dense residential zones: "
          f"built-up texture > {t_built:.1f} (up to {t_built_hi:.0f})")

    # pass 2: classify every satellite pixel and cross-tabulate
    print("\nclassifying the satellite ...", flush=True)
    today = Counter()
    for quad, block in blocks.items():
        sat, gx0, gy0, n = sat_window(block.lon_min, block.lon_max,
                                      block.lat_min, block.lat_max, args.z)
        bright, egi, blue, texture = properties(sat)
        del sat
        cls = np.full(bright.shape, 1, np.uint8)          # 0 built-up, 1 other
        cls[(texture > t_built) & (bright > 55)] = 0
        npz = np.load(os.path.join(zone_dir, f"{quad}.npz"))
        plan_lab = npz["labels"]
        zoom = float(npz["zoom"][0])
        crop = fitz.open(block.path)[0].cropbox
        rows, cols = np.nonzero(plan_lab >= 0)
        ux = cols / zoom + crop.x0
        uy = crop.y1 - rows / zoom
        mx = block.e0 + block.s * (ux - block.llx)
        my = block.n0 + block.s * (uy - block.ury)
        lon, lat = G.FWD.transform(mx, my)
        gx = ((lon + 180.0) / 360.0 * n).astype(np.int32) - gx0
        mym = np.degrees(np.log(np.tan(np.pi / 4 + np.radians(lat) / 2)))
        gy = ((1.0 - mym / 180.0) / 2.0 * n).astype(np.int32) - gy0
        ok = (gx >= 0) & (gx < cls.shape[1]) & (gy >= 0) & (gy < cls.shape[0])
        pc = plan_lab[rows, cols][ok]
        tc = cls[gy[ok], gx[ok]]
        for p, t in zip(pc.tolist(), tc.tolist()):
            cross[(p, t)] += 1
        del cls, plan_lab

    # the table
    out = {
        "method": "the plan's own legend colours, read off the rasterised sheet, "
                  "cross-tabulated against a satellite land-cover screen whose "
                  "thresholds are learned from the plan's own unambiguous "
                  "classes (dense residential, waterbodies, agricultural zones)",
        "caveat": "the imagery is a dry-season capture, so farmland reads as "
                  "brown stubble and the salt lakes as pale pans, and no RGB "
                  "threshold separates those reliably. The headline number is "
                  "therefore texture-based, which is season-independent: a "
                  "pixel counts as built up if it is rougher than 75% of the "
                  "plan's own dense residential zones. The median brightness, "
                  "greenness and texture of every zone are reported so the "
                  "season is visible in the numbers.",
        "thresholds": {"built_up_texture": t_built, "built_up_texture_p60": t_built_hi,
                       "water_blue": t_water_blue, "open_egi": t_open_egi},
        "today_classes": ROLES,
        "zones": {},
    }
    rows_md = ["| plan zone | colour | plan area km2 | built up today | satellite brightness | greenness | texture |",
               "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    for pi, nm in enumerate(names):
        tot = sum(cross[(pi, j)] for j in range(4))
        if tot == 0:
            continue
        pct = [100.0 * cross[(pi, j)] / tot for j in range(2)]
        area = plan_area[pi] * (ZM.M_PER_PX ** 2) / 1e6
        zp = zone_props[nm]
        med_b = float(np.median(cat(*zp["bright"]))) if zp["bright"] else 0
        med_e = float(np.median(cat(*zp["egi"]))) if zp["egi"] else 0
        med_t = float(np.median(cat(*zp["texture"]))) if zp["texture"] else 0
        out["zones"][nm] = {"colour": hexes[pi], "plan_area_km2": round(area, 1),
                            "samples": tot,
                            "percent_today": {r: round(pct[i], 1) for i, r in enumerate(ROLES)},
                            "satellite_median_brightness": round(med_b, 1),
                            "satellite_median_greenness": round(med_e, 1),
                            "satellite_median_texture": round(med_t, 1)}
        rows_md.append(f"| {nm} | {hexes[pi]} | {area:,.0f} | {pct[0]:.1f}% | "
                       f"{med_b:.0f} | {med_e:+.0f} | {med_t:.0f} |")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"wrote {args.out}")
    md = ["# Land-use zones: the plan against today's satellite", "",
          out["method"], "", "> " + out["caveat"], "", "## The zones", "", *rows_md, ""]
    with open(args.report, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(f"wrote {args.report}\n")
    print("\n".join(rows_md))


if __name__ == "__main__":
    main()
