"""Spot-check the extracted survey numbers against the rendered map.

A sample of numbers is taken at random, the map is rendered at that spot from
the built tile pyramid, and the crop is put on a contact sheet next to the
number the extractor claims is there. If the extraction were fragmenting or
misplacing labels, it would show up immediately.
"""
import json
import math
import os
import random
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TILES = os.path.join(ROOT, "tiles_dp2021")
PLOTS = os.path.join(ROOT, "data", "dp2021", "plots.geojson")
OUT = os.path.join(ROOT, "work", "newdp", "spotcheck.png")

Z = 17
TILE = 256


def lon2px(lon, z):
    return (lon + 180.0) / 360.0 * (256 * 2 ** z)


def lat2py(lat, z):
    la = math.radians(lat)
    return (1.0 - math.log(math.tan(la) + 1.0 / math.cos(la)) / math.pi) / 2.0 * (256 * 2 ** z)


def fetch(lon, lat, half=90):
    """The map around a lon/lat, assembled from the z-level tiles."""
    px = lon2px(lon, Z)
    py = lat2py(lat, Z)
    x0, y0 = int(px) - half, int(py) - half
    size = half * 2
    out = Image.new("RGB", (size, size), (250, 250, 250))
    for tx in range(x0 // TILE, (x0 + size) // TILE + 1):
        for ty in range(y0 // TILE, (y0 + size) // TILE + 1):
            p = os.path.join(TILES, str(Z), str(tx), f"{ty}.webp")
            if not os.path.exists(p):
                continue
            with Image.open(p) as im:
                im = im.convert("RGB")
                ix0, iy0 = max(x0, tx * TILE), max(y0, ty * TILE)
                ix1, iy1 = min(x0 + size, (tx + 1) * TILE), min(y0 + size, (ty + 1) * TILE)
                if ix1 <= ix0 or iy1 <= iy0:
                    continue
                out.paste(im.crop((ix0 - tx * TILE, iy0 - ty * TILE,
                                   ix1 - tx * TILE, iy1 - ty * TILE)), (ix0 - x0, iy0 - y0))
    return out


def main():
    random.seed(7)
    with open(PLOTS, encoding="utf-8") as f:
        feats = json.load(f)["features"]
    # take numbers that are long enough to be unambiguous
    pool = [f for f in feats if len(f["properties"]["t"]) >= 3]
    picks = random.sample(pool, 9)

    cell = 300
    sheet = Image.new("RGB", (cell * 3, (cell + 22) * 3), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("arialbd.ttf", 15)
    except Exception:
        font = ImageFont.load_default()

    for i, f in enumerate(picks):
        lon, lat = f["geometry"]["coordinates"]
        t = f["properties"]["t"]
        img = fetch(lon, lat, half=90).resize((cell, cell), Image.LANCZOS)
        col, row = i % 3, i // 3
        ox, oy = col * cell, row * (cell + 22)
        sheet.paste(img, (ox, oy + 22))
        d.rectangle([ox, oy, ox + cell, oy + cell + 22], outline=(200, 200, 200))
        d.text((ox + 4, oy + 4), f"claimed: {t}", fill=(0, 0, 0), font=font)
    sheet.save(OUT)
    print(f"wrote {OUT}")
    for f in picks:
        print("  ", f["properties"]["t"], f["geometry"]["coordinates"])


if __name__ == "__main__":
    main()
