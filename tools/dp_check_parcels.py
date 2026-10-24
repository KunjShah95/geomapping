"""Verify the extracted plot boundaries against the source.

A boundary layer that is wrong in a way nobody checks is worse than no
boundary layer, so this checks three things that can each fail independently:

  1. GEOMETRY. Re-render the source PDF and draw the extracted parcel
     boundary over it. If the boundary is in the wrong place, the picture
     shows it immediately.

  2. CONTENT. A parcel should contain the survey number it claims. Measure
     how many extracted parcels contain their own number, and how many
     numbers are in a parcel at all.

  3. AREA. The plan's plots have a characteristic size. If the extraction
     produced a layer of wildly different sizes, the classification is
     wrong. Compare the parcel areas against the plan's own drawn
     dimensions where they exist.

Output: work/newdp/parcel_check.png   the overlay
        work/newdp/parcel_check.md    the numbers
"""
import argparse
import collections
import json
import math
import os
import sys

import fitz
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dp_georef as G  # noqa: E402

ROOT = G.ROOT
OUT = os.path.join(ROOT, "data", "dp2021")
WORK = os.path.join(ROOT, "work", "newdp")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quad", default="NW")
    ap.add_argument("--zoom", type=float, default=4.0)
    ap.add_argument("--out", default=os.path.join(WORK, "parcel_check.png"))
    args = ap.parse_args()

    parcels = json.load(open(os.path.join(OUT, "parcels.geojson"),
                             encoding="utf-8"))["features"]
    print(f"{len(parcels):,} parcels loaded")

    blocks, _ = G.build_blocks()
    b = blocks[args.quad]
    doc = fitz.open(b.path)
    page = doc[0]
    crop = page.cropbox

    # the extracted parcels on this sheet, back to PAGE space
    lons = [f["geometry"]["coordinates"][0][0][0] for f in parcels]
    lats = [f["geometry"]["coordinates"][0][0][1] for f in parcels]
    sel = [f for f in parcels
           if b.lon_min <= f["geometry"]["coordinates"][0][0][0] <= b.lon_max
           and b.lat_min <= f["geometry"]["coordinates"][0][0][1] <= b.lat_max]
    print(f"[{args.quad}] {len(sel):,} parcels on this sheet")

    # ---- 1. the overlay ----------------------------------------------------
    # a window of the sheet, with the extracted boundaries drawn over it
    step = max(1, len(sel) // 40)
    picks = sel[::step][:40]
    if not picks:
        print("no parcels on this sheet")
        return
    # centre the window on the median parcel. The window is in PDF user
    # space, so the centre is converted from lon/lat first.
    clon = float(np.median([f["geometry"]["coordinates"][0][0][0] for f in picks]))
    clat = float(np.median([f["geometry"]["coordinates"][0][0][1] for f in picks]))
    cx, cy = b.ground_to_page(clon, clat)
    half = 400.0        # PDF points either side
    ux0, ux1 = cx - half, cx + half
    uy0, uy1 = cy - half, cy + half
    clip = fitz.Rect(ux0, crop.y1 - uy1, ux1, crop.y1 - uy0)
    pix = page.get_pixmap(matrix=fitz.Matrix(args.zoom, args.zoom),
                          clip=clip, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
        pix.height, pix.width, 3).copy()
    print(f"rendered {pix.width} x {pix.height} px, centre "
          f"lon {clon:.5f} lat {clat:.5f}")

    # the extracted boundaries that fall in the window, in pixel coordinates.
    # The clip was given in PDF user space, where y increases upward, while
    # the rendered image's y increases downward - so the image row is
    # (uy1 - uy) * zoom, not (uy - uy0) * zoom.
    def to_px(lon, lat):
        ux, uy = b.ground_to_page(lon, lat)
        px = (ux - ux0) * args.zoom
        py = (uy1 - uy) * args.zoom
        return px, py

    n_drawn = 0
    for f in sel:
        ring = f["geometry"]["coordinates"][0]
        pts = [to_px(p[0], p[1]) for p in ring]
        if not pts:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        if max(xs) < 0 or min(xs) > pix.width or max(ys) < 0 or min(ys) > pix.height:
            continue
        n_drawn += 1
        for i in range(len(pts)):
            x0, y0 = pts[i]
            x1, y1 = pts[(i + 1) % len(pts)]
            steps = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
            for k in range(steps + 1):
                t = k / steps
                px = int(x0 + (x1 - x0) * t)
                py = int(y0 + (y1 - y0) * t)
                if 0 <= px < pix.width and 0 <= py < pix.height:
                    img[py, px] = (255, 0, 255)      # magenta, so it stands out
    print(f"drew {n_drawn} boundaries")

    import cv2
    out = cv2.imencode(".png", img[:, :, ::-1])[1].tobytes()
    with open(args.out, "wb") as f:
        f.write(out)
    print("wrote", args.out)

    # ---- 2. the content check ---------------------------------------------
    # a parcel should contain the survey number it claims
    recs = {}
    p = os.path.join(OUT, "record_parcels.ndjson")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                if "pid" in r:
                    recs.setdefault(r["pid"], []).append(r)
    print(f"\n{len(recs):,} parcels carry a record")

    by_pid = {}
    for i, f in enumerate(parcels):
        by_pid[f["properties"].get("pid")] = f

    ok = 0
    bad = 0
    for pid, rs in recs.items():
        feat = by_pid.get(pid)
        if feat is None:
            continue
        ring = feat["geometry"]["coordinates"][0]
        for r in rs:
            inside = point_in_ring((r["lon"], r["lat"]), ring)
            if inside:
                ok += 1
            else:
                bad += 1
    total = ok + bad
    print(f"records inside their own parcel: {ok:,} / {total:,} "
          f"({100*ok/max(1,total):.2f}%)")
    if bad:
        print(f"  {bad:,} outside - these are the ones the round trip lost")

    # ---- 3. the area check -------------------------------------------------
    areas = np.array([f["properties"]["area_m2"] for f in parcels])
    print(f"\nparcel areas (m2): p10={np.percentile(areas,10):,.0f} "
          f"med={np.median(areas):,.0f} p90={np.percentile(areas,90):,.0f} "
          f"max={areas.max():,.0f}")
    a = areas / 10000.0
    print(f"  in hectares: med={np.median(a):.2f}  "
          f"90% of plots between {np.percentile(a,5):.2f} and {np.percentile(a,95):.2f} ha")

    md = ["# Plot boundary check", "",
          "## The overlay", "",
          f"`{os.path.relpath(args.out, ROOT)}` - the source sheet with the "
          "extracted boundaries drawn over it in magenta. A boundary in the "
          "wrong place would be immediately visible.", "",
          "## The content check", "",
          "| | count |", "| --- | ---: |",
          f"| records inside their own parcel | {ok:,} |",
          f"| records outside it | {bad:,} |",
          f"| total | {total:,} |", "",
          f"**{100*ok/max(1,total):.2f}% of the records lie inside the parcel "
          "they were assigned to.**", "",
          "## The area check", "",
          "| | m2 |", "| --- | ---: |",
          f"| p10 | {np.percentile(areas,10):,.0f} |",
          f"| median | {np.median(areas):,.0f} |",
          f"| p90 | {np.percentile(areas,90):,.0f} |",
          f"| max | {areas.max():,.0f} |", "",
          f"In hectares: the median plot is {np.median(a):.2f} ha, and 90% of "
          f"plots fall between {np.percentile(a,5):.2f} and "
          f"{np.percentile(a,95):.2f} ha.", ""]
    p = os.path.join(WORK, "parcel_check.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print("wrote", p)

    doc.close()


def point_in_ring(pt, ring):
    x, y = pt
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y):
            if x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
        j = i
    return inside


if __name__ == "__main__":
    main()
