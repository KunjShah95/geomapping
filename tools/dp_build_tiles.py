"""Build the slippy-map tile pyramid for the NEW AUDA DP 2021 source.

The old pyramid was cut from a 323 MB scan of one printed sheet, at 2.57 m/px.
This one is rendered from the vector GeoPDFs, so there is no scan resolution
ceiling: the plan is drawn at 2.8222 m per PDF point and every tile is
rasterised straight from the vector art at the zoom it is served at.

How a tile is made
------------------
For each tile:
  1. the tile's four corners are taken in lon/lat from the web-mercator grid;
  2. they are pushed through the exact model (lon/lat -> Transverse Mercator
     metres -> PDF user space), so the tile is placed by the survey grid and
     not by eye;
  3. the PDF page is rasterised over that rectangle with MuPDF's own
     antialiasing, at a resolution at or above the tile's own;
  4. the rendered rectangle is warped onto the 256x256 tile with the four
     corner correspondences, which removes the ~0.4 deg rotation the sheets
     carry in geographic space;
  5. a tile with no ink in it is not written, so the basemap shows through
     outside the plan instead of a sheet of white paper.

THE FOUR SHEETS OVERLAP
-----------------------
The four part sheets are the four quadrants of one plan, and the printed
sheets overlap by about 1.8 km, so a strip of every tile boundary is covered
by two sheets. That overlap is the whole difficulty: a tile on the boundary
is covered in full by one sheet and only in part by its neighbour, and a
builder that lets each sheet write its own tiles keeps whichever wrote last.
The map then tears - a band of missing plan along every sheet seam.

So a tile is rendered from EVERY sheet that covers it, and the renders are
merged: the fullest one first, and any part of it that is blank paper is
filled in from the next. The sheets agree where they overlap, so the result
is the union of the plan, seamless.

Output: <out>/{z}/{x}/{y}.webp
"""
import argparse
import json
import math
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dp_georef as G  # noqa: E402

TILE = 256


def lon2px(lon, z):
    return (lon + 180.0) / 360.0 * (256 * 2 ** z)


def lat2py(lat, z):
    la = math.radians(lat)
    return (1.0 - math.log(math.tan(la) + 1.0 / math.cos(la)) / math.pi) / 2.0 * (256 * 2 ** z)


def py2lat(py, z):
    n = math.pi * (1.0 - 2.0 * py / (256 * 2 ** z))
    return math.degrees(math.atan(math.sinh(n)))


def block_tile_range(block, z):
    """The tile x/y range that covers the block's ground extent."""
    xs, ys = [], []
    for lon, lat in ((block.lon_min, block.lat_min), (block.lon_max, block.lat_min),
                     (block.lon_max, block.lat_max), (block.lon_min, block.lat_max)):
        xs.append(lon2px(lon, z))
        ys.append(lat2py(lat, z))
    return (int(math.floor(min(xs) / TILE)), int(math.ceil(max(xs) / TILE)),
            int(math.floor(min(ys) / TILE)), int(math.ceil(max(ys) / TILE)))


def render_tile(dl, quad_user, zoom, cropbox):
    """Rasterise one tile.

    quad_user: 4 (x, y) PDF *user space* corners in the order
    top-left, top-right, bottom-right, bottom-left, where "top" means
    geographically north (larger user-space y).
    Returns a 256x256 RGB numpy array, or None if nothing was drawn.
    """
    import fitz

    q = [(x - cropbox[0], cropbox[3] - y) for x, y in quad_user]
    xs = [p[0] for p in q]
    ys = [p[1] for p in q]
    x0, x1 = min(xs) - 1.0, max(xs) + 1.0
    y0, y1 = min(ys) - 1.0, max(ys) + 1.0
    if x1 <= cropbox[0] or x0 >= cropbox[2] or y1 <= cropbox[1] or y0 >= cropbox[3]:
        return None

    pix = dl.get_pixmap(matrix=fitz.Matrix(zoom, zoom),
                        clip=fitz.Rect(x0, y0, x1, y1), alpha=False)
    if pix.width < 2 or pix.height < 2:
        return None
    src = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)

    src_q = np.array([[(p[0] - x0) * zoom, (p[1] - y0) * zoom] for p in q], dtype=np.float32)
    dst_q = np.array([[0, 0], [TILE, 0], [TILE, TILE], [0, TILE]], dtype=np.float32)
    m = cv2.getPerspectiveTransform(src_q, dst_q)
    out = cv2.warpPerspective(src, m, (TILE, TILE), flags=cv2.INTER_AREA,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
    return out


def ink_fraction(arr):
    return float((arr.min(axis=2) < 245).mean())


def merge_covers(renders):
    """Merge the sheets that cover one tile into one seamless tile.

    renders: list of (ink_fraction, array), any order. The fullest render
    leads; any part of it that is blank paper is taken from the next, and
    so on. The sheets agree where they overlap, so this is a union.
    """
    renders = sorted(renders, key=lambda r: -r[0])
    result = renders[0][1].copy()
    blank = result.min(axis=2) >= 245
    for frac, arr in renders[1:]:
        if not blank.any():
            break
        take = blank & (arr.min(axis=2) < 245)
        if take.any():
            result[take] = arr[take]
            blank = blank & ~take
    return result, blank


def build_zoom(z, out_dir, fmt, quality, overwrite, blocks_spec):
    """Render every tile of one zoom, merging the sheets that cover each."""
    import fitz

    blocks, _grid = G.build_blocks()
    sheets = {}
    for q in blocks_spec:
        b = blocks[q]
        doc = fitz.open(b.path)
        page = doc[0]
        sheets[q] = (doc, page.get_displaylist(), b, page.cropbox)

    stats = {"z": z, "tiles": 0, "bytes": 0, "blank": 0, "multi_sheet": 0}
    t_start = time.time()

    # the tile range of the whole book
    tx0 = min(block_tile_range(sheets[q][2], z)[0] for q in sheets)
    tx1 = max(block_tile_range(sheets[q][2], z)[1] for q in sheets)
    ty0 = min(block_tile_range(sheets[q][2], z)[2] for q in sheets)
    ty1 = max(block_tile_range(sheets[q][2], z)[3] for q in sheets)

    n = 0
    zbytes = 0
    for ty in range(ty0, ty1 + 1):
        lat_top = py2lat(ty * TILE, z)
        lat_bot = py2lat((ty + 1) * TILE, z)
        lon_left = tx0 * TILE / (256 * 2 ** z) * 360.0 - 180.0
        lon_right = (tx1 + 1) * TILE / (256 * 2 ** z) * 360.0 - 180.0
        step = (lon_right - lon_left) / (tx1 - tx0 + 1)
        lons = np.array([lon_left + step * i for i in range(tx1 - tx0 + 2)])
        ll = lons[:-1]
        lr = lons[1:]
        ntiles = tx1 - tx0 + 1
        lats = np.array([lat_top, lat_top, lat_bot, lat_bot] * ntiles)
        lons4 = np.empty_like(lats)
        lons4[0::4] = ll
        lons4[1::4] = lr
        lons4[2::4] = lr
        lons4[3::4] = ll
        mx, my = G.INV.transform(lons4, lats)

        for i in range(ntiles):
            tx = tx0 + i
            lon_l = ll[i]
            lon_r = lr[i]
            covers = []
            for q, (doc, dl, b, cropbox) in sheets.items():
                if not (b.lon_min - 0.2 <= lon_l and lon_r <= b.lon_max + 0.2):
                    continue
                if not (b.lat_min - 0.2 <= lat_bot and lat_top <= b.lat_max + 0.2):
                    continue
                ux = (mx[4 * i:4 * i + 4] - b.e0) / b.s + b.llx
                uy = (my[4 * i:4 * i + 4] - b.n0) / b.s + b.ury
                q4 = list(zip(ux, uy))
                w_pt = max(p[0] for p in q4) - min(p[0] for p in q4)
                h_pt = max(p[1] for p in q4) - min(p[1] for p in q4)
                if w_pt <= 0 or h_pt <= 0:
                    continue
                zoom = TILE / min(w_pt, h_pt)
                if zoom <= 0 or zoom > 64:
                    continue
                arr = render_tile(dl, q4, zoom, cropbox)
                if arr is None:
                    continue
                covers.append((ink_fraction(arr), arr))
            if not covers:
                stats["blank"] += 1
                continue
            if len(covers) > 1:
                stats["multi_sheet"] += 1
            out, blank = merge_covers(covers)
            if blank.all():
                stats["blank"] += 1
                continue
            path = os.path.join(out_dir, str(z), str(tx), f"{ty}.{ext_of(fmt)}")
            if not overwrite and os.path.exists(path):
                n += 1
                zbytes += os.path.getsize(path)
                stats["bytes"] += os.path.getsize(path)
                continue
            os.makedirs(os.path.dirname(path), exist_ok=True)
            img = Image.fromarray(out)
            if fmt == "jpg":
                img.save(path, quality=quality, optimize=True, subsampling=0)
            elif fmt == "webp":
                img.save(path, "WEBP", quality=quality, method=6)
            else:
                img.save(path, optimize=True)
            sz = os.path.getsize(path)
            zbytes += sz
            stats["bytes"] += sz
            n += 1

    for doc, dl, b, cropbox in sheets.values():
        doc.close()
    stats["tiles"] = n
    stats["seconds"] = round(time.time() - t_start, 1)
    print(f"  z{z}: {n} tiles, {zbytes/1e6:.1f} MB, "
          f"{stats['multi_sheet']} covered by more than one sheet, {stats['seconds']}s",
          flush=True)
    return stats


def ext_of(fmt):
    return {"png": "png", "jpg": "jpg", "webp": "webp"}[fmt]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(G.ROOT, "tiles_dp2021"))
    ap.add_argument("--zmin", type=int, default=11)
    ap.add_argument("--zmax", type=int, default=17)
    ap.add_argument("--fmt", choices=["png", "jpg", "webp"], default="webp")
    ap.add_argument("--quality", type=int, default=90)
    ap.add_argument("--blocks", default="NW,NE,SW,SE")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--workers", type=int, default=7)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    quads = [q.strip() for q in args.blocks.split(",") if q.strip()]

    print(f"building {args.fmt} tiles z{args.zmin}-{args.zmax} into {args.out}")
    jobs = [(z, args.out, args.fmt, args.quality, args.overwrite, quads)
            for z in range(args.zmin, args.zmax + 1)]
    allstats = []
    t0 = time.time()
    if args.workers > 1 and len(jobs) > 1:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(build_zoom, *j): j[0] for j in jobs}
            for fut in as_completed(futs):
                allstats.append(fut.result())
    else:
        for j in jobs:
            allstats.append(build_zoom(*j))
    allstats.sort(key=lambda s: s["z"])

    total = sum(s["tiles"] for s in allstats)
    totalb = sum(s["bytes"] for s in allstats)
    print(f"\nDONE in {time.time()-t0:.0f}s: {total} tiles, {totalb/1e6:.1f} MB")
    with open(os.path.join(G.ROOT, "work", "tile_build_stats.json"), "w", encoding="utf-8") as f:
        json.dump({"total_tiles": total, "total_bytes": totalb,
                   "zmin": args.zmin, "zmax": args.zmax, "fmt": args.fmt,
                   "out": args.out, "per_zoom": allstats}, f, indent=2)


if __name__ == "__main__":
    main()
