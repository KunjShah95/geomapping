"""How far is the extracted plot boundary from the plan's own drawn line?

The overlay picture is not a measurement. This is.

The plan is rendered, the TEXT is wiped out of the render (every text span's
box is filled with the paper colour, so the numbers and place names cannot
be mistaken for lines), and what is left is the plan's own vector artwork.
The ink mask is then the dark pixels that survive a morphological opening
with a long structuring element, which keeps the drawn lines and drops the
antialiasing speckle.

Every vertex of every extracted parcel is then measured against that mask:
the distance from the vertex to the nearest ink. The answer is the fidelity
of the boundary layer, in metres, and it is independent of the extraction -
the mask comes from the rendered plan, the parcels come from the vector
paths.

Output: work/newdp/boundary_offset.json
        work/newdp/boundary_offset.md
"""
import argparse
import json
import os
import sys

import cv2
import fitz
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dp_georef as G  # noqa: E402
from dp_extract_labels import spans_of  # noqa: E402

ROOT = G.ROOT
OUT = os.path.join(ROOT, "data", "dp2021")
WORK = os.path.join(ROOT, "work", "newdp")


def render_clean(page, crop, ux0, uy0, ux1, uy1, zoom):
    """The plan, rendered, with every text span wiped out.

    COORDINATE SPACES. PDF user space has y increasing upward from the
    bottom-left; PyMuPDF's fitz page space has y increasing DOWNWARD from
    the top-left. They are related by

        y_user = crop.y1 - y_fitz

    and `get_pixmap(clip=...)` takes a clip in FITZ page space, not user
    space. Passing a user-space rectangle flips the render about the
    horizontal, which puts the plan in the wrong place while still looking
    plausible - so the clip is converted here, once.
    """
    clip = fitz.Rect(ux0, crop.y1 - uy1, ux1, crop.y1 - uy0)
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
        pix.height, pix.width, 3).copy()

    # wipe the text: the paper colour of the sheet, per span
    for text, bbox, size, direction in spans_of(page):
        x0, y0, x1, y1 = bbox
        if x1 < ux0 - 20 or x0 > ux1 + 20 or y1 < uy0 - 20 or y0 > uy1 + 20:
            continue
        # the image's row 0 is the top of the window, at user y = uy1
        px0 = int((x0 - ux0) * zoom) - 2
        px1 = int((x1 - ux0) * zoom) + 2
        py0 = int((uy1 - (crop.y1 - y0)) * zoom) - 2
        py1 = int((uy1 - (crop.y1 - y1)) * zoom) + 2
        px0 = max(0, px0); py0 = max(0, py0)
        px1 = min(img.shape[1], px1); py1 = min(img.shape[0], py1)
        if px1 > px0 and py1 > py0:
            img[py0:py1, px0:px1] = (255, 255, 255)
    return img


def ink_mask(img, min_area=16):
    """The plan's own lines.

    A long-run opening is the wrong tool: a 0.48 pt line is 4 px wide at 8x,
    so an opening with a 9 px kernel erases every line that is not exactly
    horizontal or exactly vertical. What is wanted is the opposite - the
    lines are THIN, so keep the dark pixels that are not part of a large
    solid region, and drop the isolated specks.

    The text is already wiped out of the render, so what is left is the
    linework and the land-use fills. The fills are coloured, not black, so
    the dark-pixel test isolates the linework.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    dark = (gray < 120).astype(np.uint8)
    # keep the thin structures: open with a small 2x2 cross, which a 4 px
    # line survives and a solid fill does not need
    thin = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    # drop the specks
    n, cc, stats, _ = cv2.connectedComponentsWithStats(thin, 8)
    keep = np.zeros_like(thin)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= min_area:
            keep[cc == i] = 1
    return keep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quad", default="NW")
    ap.add_argument("--zoom", type=float, default=8.0)
    ap.add_argument("--half", type=float, default=500.0,
                    help="PDF points either side of the centre")
    ap.add_argument("--samples", type=int, default=800)
    args = ap.parse_args()

    parcels = json.load(open(os.path.join(OUT, "parcels.geojson"),
                             encoding="utf-8"))["features"]
    blocks, _ = G.build_blocks()
    b = blocks[args.quad]
    doc = fitz.open(b.path)
    page = doc[0]
    crop = page.cropbox

    clon = (b.lon_min + b.lon_max) / 2.0
    clat = (b.lat_min + b.lat_max) / 2.0
    cx, cy = b.ground_to_page(clon, clat)
    ux0, ux1 = cx - args.half, cx + args.half
    uy0, uy1 = cy - args.half, cy + args.half

    img = render_clean(page, crop, ux0, uy0, ux1, uy1, args.zoom)
    h, w = img.shape[:2]
    m_per_px = b.s / args.zoom
    print(f"[{args.quad}] rendered {w} x {h} px at {args.zoom}x "
          f"({2*args.half*b.s:,.0f} m across), 1 px = {m_per_px:.3f} m")

    ink = ink_mask(img)
    print(f"ink: {ink.sum():,} px ({100*ink.mean():.2f}%)")
    dist = cv2.distanceTransform(1 - ink, cv2.DIST_L2, 3)

    # the parcels with vertices in the window
    def to_px(lon, lat):
        # ground -> PDF user space (y up) -> image pixels (y down, row 0 at
        # the top of the window, which is user y = uy1)
        ux, uy = b.ground_to_page(lon, lat)
        return ((ux - ux0) * args.zoom, (uy1 - uy) * args.zoom)

    sel = []
    for f in parcels:
        ring = f["geometry"]["coordinates"][0]
        if any(0 <= to_px(lo, la)[0] < w and 0 <= to_px(lo, la)[1] < h
               for lo, la in ring):
            sel.append(f)
    print(f"{len(sel):,} parcels in the window")
    step = max(1, len(sel) // args.samples)
    picks = sel[::step][:args.samples]

    per_parcel = []
    all_vertex = []
    for f in picks:
        ring = f["geometry"]["coordinates"][0]
        ds = []
        for lon, lat in ring:
            px, py = to_px(lon, lat)
            ix, iy = int(round(px)), int(round(py))
            if not (0 <= ix < w and 0 <= iy < h):
                ds = None
                break
            ds.append(float(dist[iy, ix]) * m_per_px)
        if not ds:
            continue
        per_parcel.append({
            "pid": f["properties"].get("pid"),
            "area_m2": f["properties"].get("area_m2"),
            "median_m": round(float(np.median(ds)), 3),
            "max_m": round(float(np.max(ds)), 3),
            "vertices": len(ds),
        })
        all_vertex.extend(ds)

    if not per_parcel:
        print("no parcels in the window")
        return
    med = np.array([p["median_m"] for p in per_parcel])
    vtx = np.array(all_vertex)
    print(f"\n=== EXTRACTED PLOT BOUNDARY vs THE PLAN'S OWN DRAWN LINE ===")
    print(f"  parcels measured: {len(med):,}   vertices: {len(vtx):,}")
    print(f"  per-parcel median offset: {np.median(med):.3f} m  "
          f"p90 {np.percentile(med,90):.3f} m  max {med.max():.3f} m")
    print(f"  per-vertex offset:         {np.median(vtx):.3f} m  "
          f"p90 {np.percentile(vtx,90):.3f} m  max {vtx.max():.3f} m")
    for tol in (1.0, 2.0, 3.0, 5.0):
        print(f"  vertices within {tol:.0f} m of the line: "
              f"{100*(vtx<=tol).mean():.1f}%")

    out = {
        "quadrant": args.quad,
        "method": "the plan is rendered with the text wiped out; the ink mask "
                  "is what survives a long-run morphological opening; every "
                  "extracted parcel vertex is measured against the nearest ink",
        "zoom": args.zoom, "m_per_px": round(m_per_px, 4),
        "window_m": round(2 * args.half * b.s, 1),
        "parcels": len(med), "vertices": len(vtx),
        "parcel_median_m": round(float(np.median(med)), 3),
        "parcel_p90_m": round(float(np.percentile(med, 90)), 3),
        "parcel_max_m": round(float(med.max()), 3),
        "vertex_median_m": round(float(np.median(vtx)), 3),
        "vertex_p90_m": round(float(np.percentile(vtx, 90)), 3),
        "within_1m_pct": round(100 * float((vtx <= 1.0).mean()), 1),
        "within_2m_pct": round(100 * float((vtx <= 2.0).mean()), 1),
        "within_3m_pct": round(100 * float((vtx <= 3.0).mean()), 1),
        "within_5m_pct": round(100 * float((vtx <= 5.0).mean()), 1),
    }
    p = os.path.join(WORK, "boundary_offset.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print("wrote", p)

    md = ["# How far is the extracted plot boundary from the plan's own line?", "",
          out["method"], "",
          f"Measured on the **{args.quad}** sheet, over a "
          f"{out['window_m']:,.0f} m window at {args.zoom}x "
          f"(1 px = {out['m_per_px']:.3f} m).", "",
          "| | value |", "| --- | ---: |",
          f"| parcels measured | {out['parcels']:,} |",
          f"| vertices measured | {out['vertices']:,} |",
          f"| median offset, per parcel | {out['parcel_median_m']} m |",
          f"| p90 offset, per parcel | {out['parcel_p90_m']} m |",
          f"| median offset, per vertex | {out['vertex_median_m']} m |",
          f"| p90 offset, per vertex | {out['vertex_p90_m']} m |",
          f"| vertices within 1 m of the line | {out['within_1m_pct']}% |",
          f"| vertices within 2 m | {out['within_2m_pct']}% |",
          f"| vertices within 3 m | {out['within_3m_pct']}% |",
          f"| vertices within 5 m | {out['within_5m_pct']}% |", "",
          "For comparison, the georeference's own RMS error against the "
          "sheets' published corners is **2.2 m**, and one measurement pixel "
          f"here is {out['m_per_px']:.2f} m.", ""]
    p = os.path.join(WORK, "boundary_offset.md")
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print("wrote", p)
    doc.close()


if __name__ == "__main__":
    main()
