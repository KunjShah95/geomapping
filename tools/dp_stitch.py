"""Stitch the built pyramid back into one georeferenced overview.

This is the honest way to see what the pyramid actually contains: it is
assembled from the same tiles the map serves, so any gap, blank patch or
misplaced sheet shows up here exactly as it would on the map.

Output: work/newdp/overview_z<N>.png (+ a .pgw world file)
"""
import argparse
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dp_georef as G  # noqa: E402

ROOT = G.ROOT


def lon2px(lon, z):
    return (lon + 180.0) / 360.0 * (256 * 2 ** z)


def lat2py(lat, z):
    la = math.radians(lat)
    return (1.0 - math.log(math.tan(la) + 1.0 / math.cos(la)) / math.pi) / 2.0 * (256 * 2 ** z)


import math  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tiles", default=os.path.join(ROOT, "tiles_dp2021"))
    ap.add_argument("--z", type=int, default=12)
    ap.add_argument("--out", default=os.path.join(ROOT, "work", "newdp", f"overview_z{12}.png"))
    ap.add_argument("--max-px", type=int, default=3000)
    a = ap.parse_args()

    blocks, grid = G.build_blocks()
    lon_min = min(b.lon_min for b in blocks.values())
    lon_max = max(b.lon_max for b in blocks.values())
    lat_min = min(b.lat_min for b in blocks.values())
    lat_max = max(b.lat_max for b in blocks.values())

    px0 = int(lon2px(lon_min, a.z))
    px1 = int(lon2px(lon_max, a.z))
    py0 = int(lat2py(lat_max, a.z))
    py1 = int(lat2py(lat_min, a.z))
    w = px1 - px0
    h = py1 - py0
    if w > a.max_px or h > a.max_px:
        s = a.max_px / max(w, h)
        w, h = int(w * s), int(h * s)
    print(f"overview {w} x {h} px at z{a.z}")

    canvas = Image.new("RGB", (w, h), (255, 255, 255))
    scale = w / (px1 - px0)
    n = 0
    for tx in range(px0 // 256, (px1 + 255) // 256 + 1):
        for ty in range(py0 // 256, (py1 + 255) // 256 + 1):
            p = os.path.join(a.tiles, str(a.z), str(tx), f"{ty}.webp")
            if not os.path.exists(p):
                continue
            with Image.open(p) as im:
                im = im.convert("RGB")
                dx = int((tx * 256 - px0) * scale)
                dy = int((ty * 256 - py0) * scale)
                canvas.paste(im.resize((int(256 * scale), int(256 * scale)), Image.LANCZOS), (dx, dy))
                n += 1
    print(f"  pasted {n} tiles")
    canvas.save(a.out)
    print(f"wrote {a.out}")

    # world file
    deg = 360.0 / (256 * 2 ** a.z)
    with open(os.path.splitext(a.out)[0] + ".pgw", "w", encoding="utf-8") as f:
        f.write(f"{deg/scale:.12f}\n0.0\n{lon_min:.10f}\n")
        f.write(f"0.0\n{-deg/scale:.12f}\n{lat_max:.10f}\n")
    print("wrote the .pgw alongside it")


if __name__ == "__main__":
    main()
