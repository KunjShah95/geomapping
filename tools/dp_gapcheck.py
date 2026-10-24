"""Look at one place where the plan is white, and judge it against the satellite.

Renders a wide crop from the SOURCE PDF and the same crop from the built
tiles, and puts them over the satellite. If the source sheet really is white
there, the tiles are faithful and there is nothing to fix; if the source has
content and the tiles do not, that is a bug in the builder.
"""
import math
import os
import sys

import fitz
import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dp_georef as G  # noqa: E402
import dp_build_tiles as B  # noqa: E402

ROOT = G.ROOT
SAT = os.path.join(ROOT, "satellite")
OUT = os.path.join(ROOT, "work", "newdp", "gapcheck.png")

LAT, LON = float(sys.argv[1]), float(sys.argv[2])
SPAN_M = float(sys.argv[3]) if len(sys.argv) > 3 else 4000.0


def sat_crop(lon, lat, half_px, z=16):
    n = 2 ** z
    dpp = 360.0 / n / 256.0
    fx = (lon + 180.0) / dpp
    fy = (180.0 - math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))) / dpp
    x0, y0 = int(fx) - half_px, int(fy) - half_px
    size = half_px * 2
    out = Image.new("RGB", (size, size), (30, 30, 30))
    for tx in range(x0 // 256, (x0 + size) // 256 + 1):
        for ty in range(y0 // 256, (y0 + size) // 256 + 1):
            p = os.path.join(SAT, str(z), str(tx), f"{ty}.jpg")
            if not os.path.exists(p):
                continue
            with Image.open(p) as im:
                im = im.convert("RGB")
                ix0, iy0 = max(x0, tx * 256), max(y0, ty * 256)
                ix1, iy1 = min(x0 + size, (tx + 1) * 256), min(y0 + size, (ty + 1) * 256)
                if ix1 <= ix0 or iy1 <= iy0:
                    continue
                out.paste(im.crop((ix0 - tx * 256, iy0 - ty * 256,
                                   ix1 - tx * 256, iy1 - ty * 256)), (ix0 - x0, iy0 - y0))
    return out


def main():
    blocks, grid = G.build_blocks()
    W = 900
    sat_m_per_px = 156543.03392 * math.cos(math.radians(LAT)) / (2 ** 16)
    half_sat = int(SPAN_M / 2 / sat_m_per_px)
    sat = sat_crop(LON, LAT, half_sat).resize((W, W), Image.LANCZOS)

    # the source PDF, rendered over the same ground extent
    best = None
    for q, b in blocks.items():
        doc = fitz.open(b.path)
        page = doc[0]
        crop = page.cropbox
        ux, uy = b.ground_to_page(LON, LAT)
        half_pt = (SPAN_M / 2) / b.s
        x0, x1 = ux - half_pt, ux + half_pt
        y0, y1 = uy - half_pt, uy + half_pt
        if x1 < crop.x0 or x0 > crop.x1 or y1 < crop.y0 or y0 > crop.y1:
            doc.close()
            continue
        clip = fitz.Rect(x0 - crop.x0, crop.y1 - y1, x1 - crop.x0, crop.y1 - y0)
        zoom = W / (clip.width * b.s) * (SPAN_M / 2) / (SPAN_M / 2)
        zoom = (W / 2) / ((SPAN_M / 2) / b.s)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).resize((W, W), Image.LANCZOS)
        best = (q, img)
        doc.close()
        break

    # the built tiles, over the same extent
    Z = 15
    px = B.lon2px(LON, Z)
    py = B.lat2py(LAT, Z)
    half_px = int(SPAN_M / 2 / (156543.03392 * math.cos(math.radians(LAT)) / (2 ** Z)))
    tx0, ty0 = int(px) - half_px, int(py) - half_px
    tile = Image.new("RGB", (half_px * 2, half_px * 2), (255, 255, 255))
    for tx in range(tx0 // 256, (tx0 + half_px * 2) // 256 + 1):
        for ty in range(ty0 // 256, (ty0 + half_px * 2) // 256 + 1):
            p = os.path.join(ROOT, "tiles_dp2021", str(Z), str(tx), f"{ty}.webp")
            if not os.path.exists(p):
                continue
            with Image.open(p) as im:
                im = im.convert("RGB")
                ix0, iy0 = max(tx0, tx * 256), max(ty0, ty * 256)
                ix1, iy1 = min(tx0 + half_px * 2, (tx + 1) * 256), min(ty0 + half_px * 2, (ty + 1) * 256)
                if ix1 <= ix0 or iy1 <= iy0:
                    continue
                tile.paste(im.crop((ix0 - tx * 256, iy0 - ty * 256,
                                    ix1 - tx * 256, iy1 - ty * 256)), (ix0 - tx0, iy0 - ty0))
    tile = tile.resize((W, W), Image.LANCZOS)

    sheet = Image.new("RGB", (W, W * 3 + 40), (255, 255, 255))
    sheet.paste(sat, (0, 0))
    sheet.paste(Image.blend(sat, best[1], 0.5), (0, W + 20))
    sheet.paste(best[1], (0, 2 * W + 40))
    d = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("arialbd.ttf", 15)
    except Exception:
        font = ImageFont.load_default()
    d.text((6, 4), f"{LAT:.5f}, {LON:.5f}  block {best[0]}   TOP satellite", fill=(255, 255, 255), font=font)
    d.text((6, W + 24), "MIDDLE source sheet blended over the satellite", fill=(255, 255, 255), font=font)
    d.text((6, 2 * W + 44), "BOTTOM the source sheet alone", fill=(0, 0, 0), font=font)
    sheet.save(OUT)
    print(f"wrote {OUT}")
    print(f"source block: {best[0]}")


if __name__ == "__main__":
    main()
