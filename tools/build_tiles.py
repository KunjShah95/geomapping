"""Build a slippy-map (XYZ) tile pyramid from the georeferenced AUDA RDP sheet.

Mirrors how townplanmap.com serves its city plan layers: pre-rendered raster tiles
on a static path, e.g. {z}/{x}/{y}.jpg, draped as a MapLibre raster source.

Only tiles that actually contain plan content are emitted; empty tiles are omitted
(they fall through to the basemap). Output beyond the sheet's native resolution is
optional and upsampled.
"""
import argparse, json, math, os, sys, time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "work")
TILE = 256

_G = {}


def merc_y(lat):
    return math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def load_georef():
    with open(os.path.join(WORK, "georef.json", encoding="utf-8"), encoding="utf-8") as f:
        return json.load(f)


def affine_of(g):
    """Return the forward affine as a 3x3 numpy matrix, or None.

    The matrix maps SOURCE PIXELS -> (lon, mercator_y):
        [lon, merc, 1]^T = A @ [px, py, 1]^T
    It is fitted in Mercator space (tools/affine_fit.py) so that the mapping
    into slippy tile space is itself affine and the tile can be produced by an
    exact pixel-exact warp rather than a resampled approximation.
    """
    af = g.get("affine")
    if not af:
        return None
    A = af["A"]
    return np.array([[A[0][0], A[0][1], A[0][2]],
                      [A[1][0], A[1][1], A[1][2]],
                      [0.0, 0.0, 1.0]], dtype=np.float64)


def inv3(M):
    """Inverse of a 3x3 matrix."""
    det = (M[0, 0] * (M[1, 1] * M[2, 2] - M[1, 2] * M[2, 1])
           - M[0, 1] * (M[1, 0] * M[2, 2] - M[1, 2] * M[2, 0])
           + M[0, 2] * (M[1, 0] * M[2, 1] - M[1, 1] * M[2, 0]))
    if abs(det) < 1e-18:
        raise ValueError("singular matrix")
    c = np.empty((3, 3))
    for i in range(3):
        for j in range(3):
            sub = np.array([[M[r, cc] for cc in range(3) if cc != i]
                            for r in range(3) if r != j])
            c[j, i] = ((-1) ** (i + j)) * np.linalg.det(sub) / det
    return c


def content_mask(g, target=1400):
    """Downsampled 'has plan content' mask: anything not paper-white."""
    im = Image.open(os.path.join(WORK, "source.jpeg")).convert("RGB")
    w = target
    h = int(round(im.size[1] * w / im.size[0]))
    a = np.asarray(im.resize((w, h), Image.BILINEAR)).astype(np.int16)
    mn = a.min(axis=2)
    sat = a.max(axis=2) - a.min(axis=2)
    ink = (mn < 238) | (sat > 16)
    return ink, im.size[0], im.size[1]


def tiles_with_content(g, zmin, zmax):
    """Enumerate (z, x, y) whose sheet footprint contains plan content."""
    ink, W, H = content_mask(g)
    ys, xs = np.nonzero(ink)
    if len(xs) == 0:
        return []
    # sample if huge, to keep the enumeration fast
    if len(xs) > 400000:
        step = len(xs) // 400000 + 1
        xs, ys = xs[::step], ys[::step]
    sx = W / ink.shape[1]
    sy = H / ink.shape[0]
    px = xs * sx
    py = ys * sy

    A = affine_of(g)
    if A is not None:
        # source px -> (lon, merc)
        p = np.column_stack([px, py, np.ones_like(px)])
        geo = p @ A.T
        lon, merc = geo[:, 0], geo[:, 1]
    else:
        lon_min, merc_top, SX, SY = g["lon_min"], g["merc_top"], g["SX"], g["SY"]
        lon = lon_min + px / SX
        merc = merc_top - py / SY

    out = []
    for z in range(zmin, zmax + 1):
        n = 2 ** z
        step_deg = 360.0 / n
        tx = np.floor((lon + 180.0) / step_deg).astype(np.int64)
        ty = np.floor((180.0 - merc) / step_deg).astype(np.int64)
        np.clip(tx, 0, n - 1, out=tx)
        np.clip(ty, 0, n - 1, out=ty)
        pairs = np.unique(np.stack([tx, ty], axis=1), axis=0)
        out.extend((z, int(a), int(b)) for a, b in pairs)
        print(f"  z={z}: {len(pairs)} tiles with content")
    return out


RAW = os.path.join(WORK, "source.rgb")


def ensure_raw():
    """One-off decode of the 323 MB JPEG into a raw RGB file that workers can
    memory-map. Decoding the full image per worker exhausts RAM."""
    need = 27200 * 25600 * 3
    if os.path.exists(RAW) and os.path.getsize(RAW) == need:
        return RAW
    print(f"decoding source -> raw ({need/1024/1024/1024:.2f} GB), one time ...", flush=True)
    im = Image.open(os.path.join(WORK, "source.jpeg")).convert("RGB")
    with open(RAW, "wb") as f:
        for y0 in range(0, im.size[1], 256):
            band = np.asarray(im.crop((0, y0, im.size[0], min(y0 + 256, im.size[1]))))
            f.write(band.tobytes())
    return RAW


def _init(g):
    _G["g"] = g
    _G["mm"] = np.memmap(RAW, dtype=np.uint8, mode="r", shape=(25600, 27200, 3))
    A = affine_of(g)
    _G["A"] = A
    # inverse maps (lon, merc) -> source px, and is what the tile warp needs
    _G["Ai"] = inv3(A) if A is not None else None


def _warp_tile(z, x, y, mm, A, Ai):
    """Render one tile by warping the source with the fitted affine.

    Tile space -> (lon, merc) is a simple scale, and (lon, merc) -> source px
    is the fitted affine, so the whole map from tile pixel to source pixel is
    affine. That means the tile can be produced by an exact resample with
    PIL's AFFINE transform, instead of the axis-aligned resize that only works
    when the sheet is north-up with no rotation.

    Returns a PIL image of TILE x TILE, white where the sheet does not cover.
    """
    n = 2 ** z
    step = 360.0 / n
    lon0 = -180.0 + x * step
    my1 = 180.0 - y * step          # tile top in mercator degrees

    # tile pixel (u,v) in [0,TILE] -> (lon, merc):
    #   lon = lon0 + u*step/TILE ;  merc = my1 - v*step/TILE
    # then source px = Ai @ [lon, merc, 1]. Substituting gives
    #   px = S @ [u, v] + c
    # so S and c ARE the tile-pixel -> source-pixel map.
    #
    # PIL's AFFINE transform takes an OUTPUT -> INPUT mapping, and the
    # output here is the tile while the input is the source. That is
    # exactly S and c, so they are passed straight through. (Getting this
    # wrong is invisible: the tile still renders, it is just displaced.)
    Ai2 = Ai[:2, :2]
    ait = Ai[:2, 2]
    k = step / TILE
    S = np.array([[Ai2[0, 0] * k, -Ai2[0, 1] * k],
                  [Ai2[1, 0] * k, -Ai2[1, 1] * k]])
    c = np.array([Ai2[0, 0] * lon0 + Ai2[0, 1] * my1 + ait[0],
                  Ai2[1, 0] * lon0 + Ai2[1, 1] * my1 + ait[1]])

    coeffs = (S[0, 0], S[0, 1], c[0], S[1, 0], S[1, 1], c[1])

    # Only the part of the source that the tile touches is worth reading.
    corners = np.array([[0.0, 0.0], [TILE, 0.0], [TILE, TILE], [0.0, TILE]])
    src = corners @ S.T + c
    ix0 = max(0, int(math.floor(src[:, 0].min())) - 1)
    iy0 = max(0, int(math.floor(src[:, 1].min())) - 1)
    ix1 = min(27200, int(math.ceil(src[:, 0].max())) + 1)
    iy1 = min(25600, int(math.ceil(src[:, 1].max())) + 1)
    if ix1 <= ix0 or iy1 <= iy0:
        return None

    region = Image.fromarray(np.asarray(mm[iy0:iy1, ix0:ix1]))
    # The crop starts at (ix0, iy0), so the input coordinates must be
    # relative to the crop: input_local = input_full - (ix0, iy0). The
    # linear part is unchanged; only the translation shifts.
    c2 = (coeffs[0], coeffs[1], coeffs[2] - ix0,
          coeffs[3], coeffs[4], coeffs[5] - iy0)
    return region.transform((TILE, TILE), Image.AFFINE, c2, resample=Image.BILINEAR,
                            fillcolor=(255, 255, 255))


def _one(job):
    z, x, y, outdir, fmt, quality, pad = job
    g = _G["g"]
    mm = _G["mm"]
    W, H = 27200, 25600
    n = 2 ** z
    step = 360.0 / n

    if _G["A"] is not None:
        tile = _warp_tile(z, x, y, mm, _G["A"], _G["Ai"])
        if tile is None:
            return None
        d = os.path.join(outdir, str(z), str(x))
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, f"{y}.{fmt}")
        if fmt == "jpg":
            tile.save(path, "JPEG", quality=quality, optimize=True, progressive=True)
        else:
            tile.save(path, "PNG", optimize=True)
        return os.path.getsize(path)

    lon0 = -180.0 + x * step
    lon1 = lon0 + step
    my1 = 180.0 - y * step          # tile top in mercator degrees
    my0 = my1 - step                # tile bottom

    SX, SY = g["SX"], g["SY"]
    lon_min, merc_top = g["lon_min"], g["merc_top"]

    # tile footprint in source pixels (float), and its intersection with the sheet
    fx0 = (lon0 - lon_min) * SX
    fx1 = (lon1 - lon_min) * SX
    fy0 = (merc_top - my1) * SY
    fy1 = (merc_top - my0) * SY

    cx0, cx1 = max(fx0, 0.0), min(fx1, float(W))
    cy0, cy1 = max(fy0, 0.0), min(fy1, float(H))
    if cx1 <= cx0 or cy1 <= cy0:
        return None                      # tile misses the sheet entirely

    tw, th = fx1 - fx0, fy1 - fy0
    dx = max(0, min(TILE - 1, int(round((cx0 - fx0) / tw * TILE))))
    dy = max(0, min(TILE - 1, int(round((cy0 - fy0) / th * TILE))))
    dw = max(1, min(TILE - dx, int(round((cx1 - cx0) / tw * TILE))))
    dh = max(1, min(TILE - dy, int(round((cy1 - cy0) / th * TILE))))

    ix0 = max(0, int(math.floor(cx0)) - 1)
    iy0 = max(0, int(math.floor(cy0)) - 1)
    ix1 = min(W, int(math.ceil(cx1)) + 1)
    iy1 = min(H, int(math.ceil(cy1)) + 1)

    region = Image.fromarray(np.asarray(mm[iy0:iy1, ix0:ix1]))
    box = (cx0 - ix0, cy0 - iy0, cx1 - ix0, cy1 - iy0)   # inside the region, no negatives
    piece = region.resize((dw, dh), Image.LANCZOS, box=box)

    tile = Image.new("RGB", (TILE, TILE), (255, 255, 255))
    tile.paste(piece, (dx, dy))

    d = os.path.join(outdir, str(z), str(x))
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{y}.{fmt}")
    if fmt == "jpg":
        tile.save(path, "JPEG", quality=quality, optimize=True, progressive=True)
    else:
        tile.save(path, "PNG", optimize=True)
    return os.path.getsize(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zmin", type=int, default=11)
    ap.add_argument("--zmax", type=int, default=16)
    ap.add_argument("--out", default=os.path.join(ROOT, "tiles"))
    ap.add_argument("--fmt", default="jpg", choices=["jpg", "png"])
    ap.add_argument("--quality", type=int, default=85)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 1))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    g = load_georef()
    print(f"georeference: lon {g['lon_min']}..{g['lon_max']}  lat {g['lat_min']}..{g['lat_max']}")
    print(f"              {g['m_per_px']:.2f} m/px, native zoom z{g['native_zoom']}")
    print(f"building z{args.zmin}..z{args.zmax} -> {args.out}")

    t0 = time.time()
    jobs_raw = tiles_with_content(g, args.zmin, args.zmax)
    print(f"\n{len(jobs_raw)} candidate tiles in {time.time()-t0:.1f}s")
    total_bytes = sum(1 for _ in jobs_raw) * 0
    if args.dry_run:
        byz = {}
        for z, x, y in jobs_raw:
            byz[z] = byz.get(z, 0) + 1
        for z in sorted(byz):
            print(f"  z={z}: {byz[z]}")
        print(f"  TOTAL {len(jobs_raw)} tiles")
        return

    jobs = [(z, x, y, args.out, args.fmt, args.quality, 2) for z, x, y in jobs_raw]
    # group by zoom then row-band then column so each worker touches few raw rows
    jobs.sort(key=lambda j: (j[0], j[2], j[1]))
    ensure_raw()
    print(f"rendering with {args.workers} workers ...", flush=True)
    done = 0
    t0 = time.time()
    sizes = []
    with ProcessPoolExecutor(max_workers=args.workers, initializer=_init, initargs=(g,)) as ex:
        for sz in ex.map(_one, jobs, chunksize=32):
            if sz:
                sizes.append(sz)
            done += 1
            if done % 2000 == 0:
                el = time.time() - t0
                print(f"  {done}/{len(jobs)}  {el:.0f}s  "
                      f"eta {el/done*(len(jobs)-done):.0f}s  "
                      f"avg {sum(sizes)/len(sizes)/1024:.1f} KB", flush=True)
    el = time.time() - t0
    print(f"\ndone: {len(sizes)} tiles in {el:.0f}s")
    print(f"total {sum(sizes)/1024/1024:.1f} MB  (avg {sum(sizes)/len(sizes)/1024:.1f} KB/tile)")


if __name__ == "__main__":
    main()
