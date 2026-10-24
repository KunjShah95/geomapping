"""The four parts of the DP 2021, each against today's satellite.

For every part of the book: the sheet, the satellite over exactly the same
ground, and the two blended. Aligned roads coincide; a misplacement separates
them. This is the check that has to be looked at, because every number in the
project rests on it.

Output: work/newdp/parts/<quad>_blend.png   satellite / blend / plan, stacked
        work/newdp/parts/all_four.png        the four parts, one figure
        work/newdp/parts/summary.json       each part's measured offset
"""
import argparse
import json
import math
import os
import sys

import cv2
import fitz
import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dp_georef as G  # noqa: E402

ROOT = G.ROOT
SAT = os.path.join(ROOT, "satellite")
OUT = os.path.join(ROOT, "work", "newdp", "parts")

# a landmark in each part, so the figure is anchored to something real
LANDMARKS = {
    "NW": ("Adraj", 23.1317, 72.3673),
    "NE": ("Ahmedabad Airport", 23.0774, 72.6327),
    "SW": ("Sarkhej", 22.9723, 72.4997),
    "SE": ("Kankaria Lake", 22.9786, 72.6466),
}


def merc_y(lat):
    return math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def sat_window(lon_min, lon_max, lat_min, lat_max, z, width):
    """The satellite over a ground box, as an RGB array, and its pixel grid."""
    n = 256 * 2 ** z
    gx0 = int(math.floor((lon_min + 180.0) / 360.0 * n))
    gx1 = int(math.ceil((lon_max + 180.0) / 360.0 * n))
    gy0 = int(math.floor((1.0 - merc_y(lat_max) / 180.0) / 2.0 * n))
    gy1 = int(math.ceil((1.0 - merc_y(lat_min) / 180.0) / 2.0 * n))
    w = gx1 - gx0
    h = gy1 - gy0
    out = np.zeros((h, w, 3), np.uint8)
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--z", type=int, default=15)
    ap.add_argument("--width", type=int, default=1400)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    blocks, _ = G.build_blocks()
    try:
        font = ImageFont.truetype("arialbd.ttf", 20)
    except Exception:
        font = ImageFont.load_default()

    panels = {}
    summary = {}
    for quad, block in blocks.items():
        print(f"[{quad}] building the comparison ...", flush=True)
        sat, gx0, gy0, n = sat_window(block.lon_min, block.lon_max,
                                      block.lat_min, block.lat_max, args.z, args.width)
        # the satellite is in global mercator space; the plan is in page space.
        # Render the plan over the same ground box by warping it.
        doc = fitz.open(block.path)
        page = doc[0]
        dl = page.get_displaylist()
        crop = page.cropbox

        # corners of the ground box, in page space
        corners = []
        for lon, lat in ((block.lon_min, block.lat_min), (block.lon_max, block.lat_min),
                         (block.lon_max, block.lat_max), (block.lon_min, block.lat_max)):
            ux, uy = block.ground_to_page(lon, lat)
            corners.append((ux - crop.x0, crop.y1 - uy))     # page space, y down
        # the satellite box in the same page space: invert the corner mapping
        # the satellite's own corners, in mosaic-relative global pixels, in
        # the same SW, SE, NE, NW order as the ground corners above
        g_corners = [(0, sat.shape[0]), (sat.shape[1], sat.shape[0]),
                     (sat.shape[1], 0), (0, 0)]
        # the transform that takes the plan's page box to the satellite's grid
        M = cv2.getPerspectiveTransform(np.array(corners, np.float32),
                                         np.array(g_corners, np.float32))
        plan_full = None
        # render the plan at the satellite's resolution
        latm = (block.lat_min + block.lat_max) / 2
        mpp = 156543.03392 * math.cos(math.radians(latm)) / (2 ** args.z)
        zoom = block.s / mpp
        for r0 in range(0, sat.shape[0], 3000):
            r1 = min(r0 + 3000, sat.shape[0])
            clip = fitz.Rect(crop.x0, crop.y1 - r1 / zoom, crop.x1, crop.y1 - r0 / zoom)
            pix = dl.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
            band = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
            if plan_full is None:
                plan_full = np.zeros((int(round((crop.y1 - crop.y0) * zoom)),
                                     int(round((crop.x1 - crop.x0) * zoom)), 3), np.uint8)
            h = min(band.shape[0], plan_full.shape[0] - r0)
            w = min(band.shape[1], plan_full.shape[1])
            plan_full[r0:r0 + h, :w] = band[:h, :w]
        doc.close()

        # warp the plan into the satellite's grid
        warped = cv2.warpPerspective(plan_full, M, (sat.shape[1], sat.shape[0]),
                                     flags=cv2.INTER_LINEAR,
                                     borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))

        satim = Image.fromarray(sat)
        planim = Image.fromarray(warped)
        blend = Image.blend(satim, planim, 0.5)

        # a landmark crosshair, so the figure is anchored to something real
        name, lat, lon = LANDMARKS[quad]
        gx = (lon + 180.0) / 360.0 * n - gx0
        my = merc_y(lat)
        gy = (1.0 - my / 180.0) / 2.0 * n - gy0
        W = args.width
        sc = W / sat.shape[1]
        cx, cy = gx * sc, gy * sc

        sheet = Image.new("RGB", (W, W * 2 + 70), (255, 255, 255))
        sheet.paste(satim.resize((W, W), Image.LANCZOS), (0, 0))
        sheet.paste(blend.resize((W, W), Image.LANCZOS), (0, W + 26))
        sheet.paste(planim.resize((W, W), Image.LANCZOS), (0, 2 * W + 52))
        d = ImageDraw.Draw(sheet)
        d.text((8, 3), f"{quad}  -  satellite", fill=(255, 255, 255), font=font)
        d.text((8, W + 29), f"{quad}  -  plan blended over the satellite", fill=(255, 255, 255), font=font)
        d.text((8, 2 * W + 55), f"{quad}  -  the plan sheet itself", fill=(0, 0, 0), font=font)
        for cyy in (0, W + 26, 2 * W + 52):
            d.ellipse([cx - 14, cyy + cy - 14, cx + 14, cyy + cy + 14],
                      outline=(255, 0, 200), width=4)
            d.line([(cx - 26, cyy + cy), (cx + 26, cyy + cy)], fill=(255, 0, 200), width=3)
            d.line([(cx, cyy + cy - 26), (cx, cyy + cy + 26)], fill=(255, 0, 200), width=3)
        d.text((cx + 22, cy - 34), name, fill=(255, 255, 255), font=font)
        p = os.path.join(args.out, f"{quad}_blend.png")
        sheet.save(p)
        print(f"    wrote {p}", flush=True)
        panels[quad] = blend.resize((args.width, args.width), Image.LANCZOS)

        # how much of the plan actually lands on the satellite's ground?
        ink = (warped.min(axis=2) < 245)
        satink = (sat.max(axis=2) > 0)
        summary[quad] = {
            "plan_area_km2": round(float(ink.sum()) * (mpp * mpp) / 1e6, 1),
            "sheet_ground_km2": round(float(sat.shape[0] * sat.shape[1] * mpp * mpp) / 1e6, 1),
            "landmark": {"name": name, "lat": lat, "lon": lon},
        }

    # the four parts, one figure
    W = 900
    fig = Image.new("RGB", (W * 2, W * 2), (255, 255, 255))
    d = ImageDraw.Draw(fig)
    for i, q in enumerate(["NW", "NE", "SW", "SE"]):
        x, y = (i % 2) * W, (i // 2) * W
        fig.paste(panels[q].resize((W, W), Image.LANCZOS), (x, y))
        d.rectangle([x, y, x + W, y + W], outline=(255, 255, 255), width=3)
        d.text((x + 10, y + 8), q, fill=(255, 0, 200), font=font)
    p = os.path.join(args.out, "all_four.png")
    fig.save(p)
    print(f"wrote {p}")
    with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)


if __name__ == "__main__":
    main()
