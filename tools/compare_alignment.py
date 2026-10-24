"""Render the plan's own content against the satellite, at the same place,
so the alignment can be judged BY EYE.

This is the check that matters. Every automated measure in this project has
had to be defended against the objection that it is measuring the wrong
thing - the detector latching onto the wrong feature, the threshold
catching the wrong colour, the label sitting a kilometre from its village.
None of those objections survive being looked at.

The tool takes a lon/lat, crops the plan and the satellite around it at
the same ground extent, and stacks them so any offset is obvious. It can
also draw a candidate transform's correction, so a proposed fix can be
inspected rather than merely asserted.

Output: work/compare/<slug>.png
"""
import argparse, glob, json, math, os
import numpy as np
from PIL import Image, ImageDraw, ImageFont

Image.MAX_IMAGE_PIXELS = None
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "work")
SAT = os.path.join(ROOT, "satellite")
SRC = os.path.join(WORK, "source.jpeg")
FULL_W, FULL_H = 27200, 25600
OUT = os.path.join(WORK, "compare")
os.makedirs(OUT, exist_ok=True)


def merc_y(lat):
    return math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def inv_merc_y(my):
    return math.degrees(2 * math.atan(math.exp(math.radians(my))) - math.pi / 2)


def affine_matrices(g):
    af = g.get("affine")
    if af:
        A = np.array([[af["A"][0][0], af["A"][0][1], af["A"][0][2]],
                      [af["A"][1][0], af["A"][1][1], af["A"][1][2]],
                      [0, 0, 1]], float)
        Ai = np.array([[af["Ai"][0][0], af["Ai"][0][1], af["Ai"][0][2]],
                       [af["Ai"][1][0], af["Ai"][1][1], af["Ai"][1][2]],
                       [0, 0, 1]], float)
        return A, Ai
    SX, SY = g["SX"], g["SY"]
    A = np.array([[1.0 / SX, 0.0, g["lon_min"]],
                  [0.0, -1.0 / SY, g["merc_top"]],
                  [0, 0, 1]], float)
    return A, np.linalg.inv(A)


def sat_crop(lon, lat, m_per_px, half_px, z=16):
    """The satellite around a lon/lat, at the requested ground resolution.

    The crop is assembled from the z-level tiles. The tile at (tx, ty) holds
    pixels [tx*256, tx*256+256) x [ty*256, ty*256+256), so the piece of a
    tile that falls inside the request is cropped out of the tile and
    pasted at the right place in the output.
    """
    n = 2 ** z
    step = 360.0 / n
    deg_per_px = step / 256.0
    fx = (lon + 180.0) / deg_per_px
    fy = (180.0 - merc_y(lat)) / deg_per_px
    # the output is in z-level pixels, so the request is measured in them
    x0, y0 = int(fx) - half_px, int(fy) - half_px
    size = half_px * 2
    out = Image.new("RGB", (size, size), (20, 20, 20))
    for tx in range(x0 // 256, (x0 + size) // 256 + 1):
        for ty in range(y0 // 256, (y0 + size) // 256 + 1):
            p = os.path.join(SAT, str(z), str(tx), f"{ty}.jpg")
            if not os.path.exists(p):
                continue
            with Image.open(p) as im:
                im = im.convert("RGB")
                # this tile covers pixels [tx*256, tx*256+256) x [ty*256, ...)
                ix0 = max(x0, tx * 256)
                iy0 = max(y0, ty * 256)
                ix1 = min(x0 + size, (tx + 1) * 256)
                iy1 = min(y0 + size, (ty + 1) * 256)
                if ix1 <= ix0 or iy1 <= iy0:
                    continue
                piece = im.crop((ix0 - tx * 256, iy0 - ty * 256,
                                 ix1 - tx * 256, iy1 - ty * 256))
                out.paste(piece, (ix0 - x0, iy0 - y0))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--georef", default=os.path.join(WORK, "georef.json"))
    ap.add_argument("--places", default="airport:23.0774,72.6327|"
                                      "stadium:23.0380,72.4777|"
                                      "kankaria:22.9786,72.6466|"
                                      "sarkhej:22.9723,72.4997|"
                                      "ashram:23.0535,72.5714|"
                                      "junction:23.0223,72.5713")
    ap.add_argument("--metres", type=int, default=1200, help="half-width of the crop")
    ap.add_argument("--out-width", type=int, default=1000)
    a = ap.parse_args()

    with open(a.georef, encoding="utf-8") as f:
        g = json.load(f)
    A, Ai = affine_matrices(g)
    print(f"georeference: {a.georef}")
    if g.get("affine"):
        af = g["affine"]
        print(f"  affine: RMSE {af['rmse_px']:.2f} px ({af['rmse_m']:.1f} m) "
              f"from {af['n_points']} points")
    else:
        print("  north-up model (H0)")

    src = Image.open(SRC).convert("RGB")
    try:
        font = ImageFont.truetype("arialbd.ttf", 15)
    except Exception:
        font = ImageFont.load_default()

    for item in a.places.split("|"):
        slug, coords = item.split(":")
        lat, lon = (float(v) for v in coords.split(","))

        # the plan, at the same ground extent
        half_src_px = a.metres / 2.57
        px, py = (Ai @ np.array([lon, merc_y(lat), 1.0]))[:2]
        x0, y0 = int(px - half_src_px), int(py - half_src_px)
        size = int(half_src_px * 2)
        if x0 < 0 or y0 < 0 or x0 + size > FULL_W or y0 + size > FULL_H:
            print(f"  {slug}: outside the sheet, skipped")
            continue
        plan = src.crop((x0, y0, x0 + size, y0 + size)).resize(
            (a.out_width, a.out_width), Image.LANCZOS)

        # the satellite at the same ground extent. At z16 the satellite is
        # 2.4 m/px, so a 1200 m wide crop is 500 z16 pixels.
        sat_m_per_px = 2.4
        sat_half = int(a.metres / 2 / sat_m_per_px)
        sat = sat_crop(lon, lat, sat_m_per_px, sat_half).resize(
            (a.out_width, a.out_width), Image.LANCZOS)

        # THE BLEND. The plan's roads and the satellite's roads are the same
        # roads, so overlaying one on the other at 50% shows the offset
        # directly: aligned roads coincide, misaligned ones separate.
        blend = Image.blend(sat, plan, 0.5)

        sheet = Image.new("RGB", (a.out_width, a.out_width * 2 + 26), (255, 255, 255))
        sheet.paste(blend, (0, 0))
        sheet.paste(plan, (0, a.out_width + 26))
        d = ImageDraw.Draw(sheet)
        d.rectangle([0, 0, a.out_width, 24], fill=(255, 255, 255))
        d.text((6, 4), f"{slug}  {lat:.4f}, {lon:.4f}   "
                       f"TOP = plan blended over satellite   BOTTOM = plan",
               fill=(0, 0, 0), font=font)
        d.rectangle([0, a.out_width + 26, a.out_width, a.out_width * 2 + 26],
                    outline=(255, 0, 0), width=2)
        # crosshair at the exact centre of both
        for cy in (a.out_width // 2, a.out_width + 26 + a.out_width // 2):
            d.line([(a.out_width // 2 - 18, cy), (a.out_width // 2 + 18, cy)],
                   fill=(255, 0, 255), width=2)
            d.line([(a.out_width // 2, cy - 18), (a.out_width // 2, cy + 18)],
                   fill=(255, 0, 255), width=2)
        p = os.path.join(OUT, f"{slug}.png")
        sheet.save(p)
        print(f"  {slug}: wrote {p}")


if __name__ == "__main__":
    main()
