"""Verify the NEW DP 2021 georeference against satellite imagery.

For a set of lon/lat points it:
  1. asks the fitted transform for the page rectangle covering that point,
  2. renders that rectangle from the vector PDF,
  3. crops the satellite at the identical ground extent,
  4. blends them 50/50 so any offset is visible as separated roads.

A correct georeference makes the two road networks coincide.
"""
import math
import os
import sys

import fitz
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dp_georef as G  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(ROOT, "NEW DP BOOK AUDA")
SAT = os.path.join(ROOT, "satellite")
OUT = os.path.join(ROOT, "work", "newdp", "verify")
os.makedirs(OUT, exist_ok=True)

# landmarks that are unambiguous in both the plan and the satellite
POINTS = [
    ("kankaria_lake", 22.9786, 72.6466),
    ("airport", 23.0774, 72.6327),
    ("sabarmati_ashram", 23.0535, 72.5714),
    ("sarkhej", 22.9723, 72.4997),
    ("stadium", 23.0380, 72.4777),
    ("adraj", 23.1317, 72.3673),
    ("sanand", 22.9833, 72.3833),
    ("gandhinagar", 23.2156, 72.6369),
]


def merc_y(lat):
    return math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def sat_crop(lon, lat, half_px, z=16):
    n = 2 ** z
    deg_per_px = 360.0 / n / 256.0
    fx = (lon + 180.0) / deg_per_px
    fy = (180.0 - merc_y(lat)) / deg_per_px
    x0, y0 = int(fx) - half_px, int(fy) - half_px
    size = half_px * 2
    out = Image.new("RGB", (size, size), (25, 25, 25))
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
    print(f"scale {grid['scale_m_per_pt']:.4f} m/pt   rms "
          f"{grid['east']['rms_m']:.2f}/{grid['north']['rms_m']:.2f} m")

    docs = {}
    try:
        font = ImageFont.truetype("arialbd.ttf", 16)
    except Exception:
        font = ImageFont.load_default()

    for slug, lat, lon in POINTS:
        blk = None
        for q, b in blocks.items():
            if b.lat_min <= lat <= b.lat_max and b.lon_min <= lon <= b.lon_max:
                blk = (q, b)
                break
        if blk is None:
            print(f"{slug}: outside every block")
            continue
        q, b = blk
        if b.path not in docs:
            docs[b.path] = fitz.open(b.path)
        doc = docs[b.path]
        page = doc[0]
        mb = page.mediabox

        ux, uy = b.ground_to_page(lon, lat)
        metres = 1200.0
        m_per_deg_lon = 111320.0 * math.cos(math.radians(lat))
        m_per_deg_lat = 110990.0
        # points per metre, from the block's own scale
        half_pt_x = (metres / 2) / b.s
        half_pt_y = (metres / 2) / b.s

        x0 = max(ux - half_pt_x, mb.x0)
        x1 = min(ux + half_pt_x, mb.x1)
        y0 = max(uy - half_pt_y, mb.y0)
        y1 = min(uy + half_pt_y, mb.y1)
        # user space (y up) -> page space (y down) for get_pixmap(clip=)
        crop = page.cropbox
        clip = fitz.Rect(x0 - crop.x0, crop.y1 - y1, x1 - crop.x0, crop.y1 - y0)

        # render at the same ground resolution the satellite crop uses:
        # pixels per PDF point = (metres per point) / (metres per pixel)
        sat_z = 16
        sat_m_per_px = 156543.03392 * math.cos(math.radians(lat)) / (2 ** sat_z)
        zoom = b.s / sat_m_per_px
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
        plan = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        plan = plan.resize((900, 900), Image.LANCZOS)

        sat = sat_crop(lon, lat, int(metres / 2 / sat_m_per_px), z=sat_z)
        sat = sat.resize((900, 900), Image.LANCZOS)

        blend = Image.blend(sat, plan, 0.5)
        sheet = Image.new("RGB", (900, 900 * 2 + 26), (255, 255, 255))
        sheet.paste(blend, (0, 0))
        sheet.paste(plan, (0, 926))
        d = ImageDraw.Draw(sheet)
        d.text((6, 4), f"{slug}  {lat:.5f}, {lon:.5f}   block {q}   TOP=blend  BOTTOM=plan",
               fill=(0, 0, 0), font=font)
        p = os.path.join(OUT, f"{slug}.png")
        sheet.save(p)
        print(f"{slug}: block {q}  wrote {p}")

    for d in docs.values():
        d.close()


if __name__ == "__main__":
    main()
