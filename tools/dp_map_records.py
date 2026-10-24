"""Attribute every survey record to the client's own units, and check it
against Google Earth.

A record on its own - a number and a position - is not much use. What makes
it useful is knowing WHICH village it is in, which part sheet it is on, what
the plan zones that ground as, and what the ground under it is like now.

So this joins the three things the client gave us:

  * the survey numbers, read from the sheets
  * the place names, read from the same sheets - the nearest one is the
    village the record is in
  * the land-use zone, read from the sheet's own legend colours

and then checks each record against the Google Earth imagery: the ground
under the record is measured for texture, and the threshold for "built up"
is learned from the plan's own dense residential zones. A record whose
ground is now built up although the plan zones it agricultural is a finding,
and the count of those is the honest measure of how much the plan still
describes the city.

Output: data/dp2021/records.ndjson      every record, with its village, its
                                         zone, its sheet and the check
        data/dp2021/records_summary.json  the counts, by village and by zone
"""
import argparse
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict

import cv2
import fitz
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dp_georef as G  # noqa: E402
import dp_zone_masks as ZM  # noqa: E402
from dp_zone_satellite import sat_window, properties  # noqa: E402

ROOT = G.ROOT
OUT = os.path.join(ROOT, "data", "dp2021")
ZONE_DIR = os.path.join(ROOT, "work", "newdp", "zones")

# Words on the sheets that are annotations, not place names.
NOT_PLACE = re.compile(r"^(BL\.?NO|SUR\.?NO|MT\.?|M|MT|NO|NUM|TO|FROM)$", re.IGNORECASE)
# sheet furniture that is set in capitals on the sheet but is not a place
FURNITURE = re.compile(
    r"DTPS|\bTP\b|REVISED|DRAFT|DEVELOPMENT|\bPLAN\b|AUDA|SCALE|LEGEND|\bNOTE|"
    r"REFER|\bREF\.|\bGT\b|\bSHEET\b|MINUTE|\bDIST\b|SURVEY|\bVILLAGE\b|"
    r"\bVIBHAG\b|\bTALUKA\b|\bDISTRICT\b|\bBLOCK\b|\bPART\b|\bPAGE\b|"
    r"\(\w+\)|\d", re.IGNORECASE)
WIDTH_RE = re.compile(r"^\d+(\.\d+)?\s*(M|MT)\.?$", re.IGNORECASE)


def place_names(page):
    """(name, x, y) for the settlement names on a sheet, in PDF user space."""
    d = page.get_text("dict")
    out = []
    for blk in d.get("blocks", []):
        if blk.get("type") != 0:
            continue
        for line in blk.get("lines", []):
            for sp in line.get("spans", []):
                t = sp["text"].strip()
                sz = sp.get("size", 0)
                if sz < 12 or len(t) < 4 or not t.isupper():
                    continue
                if NOT_PLACE.match(t) or WIDTH_RE.match(t) or FURNITURE.search(t):
                    continue
                bb = sp["bbox"]
                out.append((t, (bb[0] + bb[2]) / 2.0, (bb[1] + bb[3]) / 2.0))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plots", default=os.path.join(OUT, "plots.geojson"))
    ap.add_argument("--out", default=os.path.join(OUT, "records.ndjson"))
    ap.add_argument("--max-village-km", type=float, default=4.0)
    args = ap.parse_args()

    blocks, grid = G.build_blocks()
    classes = ZM.legend_classes()
    zone_names = [c["label"] for c in classes]

    sheets = {}
    for q, b in blocks.items():
        doc = fitz.open(b.path)
        page = doc[0]
        names = place_names(page)
        npz = np.load(os.path.join(ZONE_DIR, f"{q}.npz"))
        sheets[q] = {"names": names, "zones": npz["labels"],
                     "zoom": float(npz["zoom"][0]), "crop": page.cropbox}
        doc.close()
        print(f"[{q}] {len(names):,} place names", flush=True)

    # the satellite, and the texture that says "built up"
    print("\nreading the satellite ...", flush=True)
    satdata = {}
    for q, b in blocks.items():
        sat, gx0, gy0, n = sat_window(b.lon_min, b.lon_max,
                                      b.lat_min, b.lat_max, 15)
        bright, egi, blue, texture = properties(sat)
        satdata[q] = (bright, egi, blue, texture, gx0, gy0, n)
        del sat
        print(f"    {q}: {bright.shape[1]} x {bright.shape[0]} px", flush=True)

    with open(args.plots, encoding="utf-8") as f:
        feats = json.load(f)["features"]
    print(f"{len(feats):,} records to attribute")

    # the "built up" threshold, learned from the plan's own dense residential
    t_built = None
    for q, b in blocks.items():
        zl = sheets[q]["zones"]
        idx = [i for i, c in enumerate(classes)
               if c["label"].startswith("Residental Zone I")]
        if not idx:
            continue
        m = np.isin(zl, idx)
        if not m.any():
            continue
        bright, egi, blue, texture, gx0, gy0, n = satdata[q]
        rows, cols = np.nonzero(m)
        step = max(1, len(rows) // 60000)
        rows, cols = rows[::step], cols[::step]
        ux = cols / sheets[q]["zoom"] + sheets[q]["crop"].x0
        uy = sheets[q]["crop"].y1 - rows / sheets[q]["zoom"]
        mx = b.e0 + b.s * (ux - b.llx)
        my = b.n0 + b.s * (uy - b.ury)
        lon, lat = G.FWD.transform(mx, my)
        gx = ((lon + 180.0) / 360.0 * n).astype(int) - gx0
        mym = np.degrees(np.log(np.tan(np.pi / 4 + np.radians(lat) / 2)))
        gy = ((1.0 - mym / 180.0) / 2.0 * n).astype(int) - gy0
        ok = (gx >= 0) & (gx < texture.shape[1]) & (gy >= 0) & (gy < texture.shape[0])
        if ok.any():
            # the MEDIAN of the plan's dense residential: half of the
            # ground the plan calls built-up is at least this rough
            t = float(np.median(texture[gy[ok], gx[ok]]))
            t_built = t if t_built is None else min(t_built, t)
    print(f"    'built up' threshold: texture > {t_built:.1f}")

    # the names, bucketed
    cell = 400.0
    grids = {}
    for q, s in sheets.items():
        g = defaultdict(list)
        for t, x, y in s["names"]:
            g[(int(x // cell), int(y // cell))].append((t, x, y))
        grids[q] = g

    out = []
    counts_zone = Counter()
    counts_village = Counter()
    counts_block = Counter()
    built_by_zone = defaultdict(int)
    zone_total = Counter()
    unmatched = 0
    with open(args.out, "w", encoding="utf-8") as f:
        for ft in feats:
            lon, lat = ft["geometry"]["coordinates"]
            t = ft["properties"]["t"]
            q = None
            for qq, b in blocks.items():
                if b.lon_min <= lon <= b.lon_max and b.lat_min <= lat <= b.lat_max:
                    q = qq
                    break
            if q is None:
                continue
            s = sheets[q]
            b = blocks[q]
            ux, uy = b.ground_to_page(lon, lat)
            col = (ux - s["crop"].x0) * s["zoom"]
            row = (s["crop"].y1 - uy) * s["zoom"]
            # the zone, as the majority in a small window: the record's own
            # glyphs are drawn over the zone colour and would defeat a
            # single-pixel lookup
            zone = ""
            zl = s["zones"]
            r0, r1 = max(0, int(row) - 5), min(zl.shape[0], int(row) + 6)
            c0, c1 = max(0, int(col) - 5), min(zl.shape[1], int(col) + 6)
            if r1 > r0 and c1 > c0:
                win = zl[r0:r1, c0:c1].ravel()
                win = win[win >= 0]
                if len(win) >= 6:
                    zone = zone_names[int(np.bincount(win).argmax())]

            # the nearest place name
            gx, gy = int(ux // cell), int(uy // cell)
            best = None
            bestd = 1e18
            ring = int(args.max_village_km * 1000 / b.s / cell) + 1
            for dx in range(-ring, ring + 1):
                for dy in range(-ring, ring + 1):
                    for nm, nx, ny in grids[q].get((gx + dx, gy + dy), ()):
                        d = (nx - ux) ** 2 + (ny - uy) ** 2
                        if d < bestd:
                            bestd, best = d, nm
            village = ""
            dist_km = None
            if best is not None:
                dist_km = math.sqrt(bestd) * b.s / 1000.0
                if dist_km <= args.max_village_km:
                    village = best
                else:
                    unmatched += 1

            # the Google Earth check
            bright, egi, blue, texture, gx0, gy0, n = satdata[q]
            n_z = 256 * 2 ** 15
            myv = math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))
            sx = int((lon + 180.0) / 360.0 * n_z) - gx0
            sy = int((1.0 - myv / 180.0) / 2.0 * n_z) - gy0
            built = None
            if 0 <= sy < texture.shape[0] and 0 <= sx < texture.shape[1]:
                built = bool(texture[sy, sx] > t_built and bright[sy, sx] > 55)

            rec = {"t": t, "lon": lon, "lat": lat, "block": q,
                   "village": village, "zone": zone, "built_up_today": built}
            if dist_km is not None:
                rec["village_km"] = round(dist_km, 3)
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
            out.append(rec)
            if zone:
                counts_zone[zone] += 1
                zone_total[zone] += 1
                if built:
                    built_by_zone[zone] += 1
            if village:
                counts_village[village] += 1
            counts_block[q] += 1

    print(f"\nwrote {args.out}  ({len(out):,} records)")
    print(f"  with a village within {args.max_village_km} km: "
          f"{len(out) - unmatched:,}  ({100*(len(out)-unmatched)/len(out):.1f}%)")
    print(f"  with a land-use zone: {sum(counts_zone.values()):,}")
    print(f"  distinct villages: {len(counts_village):,}")
    print("  by sheet:", dict(counts_block))
    print("\n  top villages by record count:")
    for v, c in counts_village.most_common(12):
        print(f"    {v:28s} {c:6d}")
    print("\n  by land-use zone, and how much of it is built up now:")
    for z, c in counts_zone.most_common(12):
        pct = 100.0 * built_by_zone[z] / max(1, zone_total[z])
        print(f"    {z:44s} {c:6d} records  {pct:5.1f}% built up today")

    summary = {
        "records": len(out),
        "with_village": len(out) - unmatched,
        "with_zone": sum(counts_zone.values()),
        "distinct_villages": len(counts_village),
        "built_up_threshold_texture": round(t_built, 2),
        "by_block": dict(counts_block),
        "by_zone": dict(counts_zone),
        "by_village": dict(counts_village),
        "built_up_today_by_zone": {z: {"records": zone_total[z],
                                       "built_up": built_by_zone[z],
                                       "percent": round(100.0 * built_by_zone[z] / max(1, zone_total[z]), 1)}
                                   for z in zone_total},
    }
    p = os.path.join(OUT, "records_summary.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"wrote {p}")


if __name__ == "__main__":
    main()
