"""Measure where the plan sits on the ground, everywhere, against OSM roads.

The georeference fits the sheets to their own printed corners to +/-2.2 m,
which says nothing about whether those corners are right on the ground. The
benchmark (tools/dp_benchmark.py) found the plan ~150-180 m east of both OSM
and 2021 imagery at most of 20 points. This tool turns that into a full
displacement field and asks which correction explains it.

Method
  * Ground truth: OSM roads (ODbL), drawn at z15 with a width per road class.
    OSM is used rather than imagery because roads are drawn cleanly (no
    rooftops) and its licence allows deriving corrections from it.
  * Plan: the served tile pyramid at z15, paper-white road corridors.
  * A window every --step metres; gradient-orientation correlation gives the
    shift that moves the plan onto OSM at that window. Weak or edge peaks
    are dropped.
  * Models fitted to the field: shift, similarity, affine. Each is scored by
    spatial-block cross-validation (fit without a block, test on it), so a
    model only wins if it predicts places it has not seen.

Ground-truth roads (work/osm/roads.json, ~53 MB, not in git) - re-download with:
  curl -A "auda-dp2021-georef-check/1.0" -X POST https://overpass.kumi.systems/api/interpreter \
    --data-urlencode 'data=[out:json][timeout:500];way["highway"~"^(motorway|trunk|primary|
    secondary|tertiary|motorway_link|trunk_link|primary_link|secondary_link|tertiary_link|
    residential|unclassified)$"](22.757,72.173,23.364,72.870);out geom qt;' -o work/osm/roads.json
  (one line, no breaks inside the regex; overpass-api.de works too when not at capacity)

Output: work/georef_osm/field.json, work/georef_osm/field.png
"""
import argparse, json, math, os, sys
from collections import defaultdict

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dp_benchmark import orient, goc_shift, plan_mask  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TILES = os.path.join(ROOT, "tiles_dp2021")
OSM = os.path.join(ROOT, "work", "osm", "roads.json")
OUT = os.path.join(ROOT, "work", "georef_osm")
Z = 15
# drawn width in z15 px (4.4 m/px at Ahmedabad), roughly the real carriageway
WIDTH = {"motorway": 7, "trunk": 7, "primary": 6, "secondary": 5, "tertiary": 4,
         "motorway_link": 3, "trunk_link": 3, "primary_link": 3, "secondary_link": 3,
         "tertiary_link": 3, "residential": 2, "unclassified": 2}


def lonlat_to_px(lon, lat, z=Z):
    n = 2 ** z * 256
    x = (lon + 180.0) / 360.0 * n
    y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
    return x, y


def px_to_lonlat(x, y, z=Z):
    n = 2 ** z * 256
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lon, lat


def load_roads(cell):
    with open(OSM, encoding="utf-8") as f:
        ways = json.load(f)["elements"]
    grid = defaultdict(list)
    for w in ways:
        g = w.get("geometry")
        if not g or len(g) < 2:
            continue
        width = WIDTH.get(w.get("tags", {}).get("highway"), 2)
        pts = [lonlat_to_px(p["lon"], p["lat"]) for p in g]
        for a, b in zip(pts, pts[1:]):
            for cx in {int(a[0] // cell), int(b[0] // cell)}:
                for cy in {int(a[1] // cell), int(b[1] // cell)}:
                    grid[(cx, cy)].append((a, b, width))
    return grid


def osm_window(grid, cell, x0, y0, size):
    im = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(im)
    seen = set()
    for cx in range(int(x0 // cell) - 1, int((x0 + size) // cell) + 2):
        for cy in range(int(y0 // cell) - 1, int((y0 + size) // cell) + 2):
            for seg in grid.get((cx, cy), ()):
                if id(seg) in seen:
                    continue
                seen.add(id(seg))
                (ax, ay), (bx, by), w = seg
                d.line([(ax - x0, ay - y0), (bx - x0, by - y0)], fill=255, width=w)
    return np.asarray(im) > 0


def plan_window(x0, y0, size):
    """Plan z15 pixels for [x0, x0+size) x [y0, y0+size); None where no tile."""
    out = np.zeros((size, size, 3), np.int16)
    have = np.zeros((size, size), bool)
    for tx in range(x0 // 256, (x0 + size - 1) // 256 + 1):
        for ty in range(y0 // 256, (y0 + size - 1) // 256 + 1):
            p = os.path.join(TILES, str(Z), str(tx), f"{ty}.webp")
            if not os.path.exists(p):
                continue
            with Image.open(p) as im:
                rgba = np.asarray(im.convert("RGBA"))
            ix0, iy0 = max(x0, tx * 256), max(y0, ty * 256)
            ix1, iy1 = min(x0 + size, tx * 256 + 256), min(y0 + size, ty * 256 + 256)
            sub = rgba[iy0 - ty * 256:iy1 - ty * 256, ix0 - tx * 256:ix1 - tx * 256]
            out[iy0 - y0:iy1 - y0, ix0 - x0:ix1 - x0] = sub[..., :3]
            have[iy0 - y0:iy1 - y0, ix0 - x0:ix1 - x0] = sub[..., 3] > 0
    return out, have


def fit(model, P, D):
    """Least squares: D (plan->ground shift, m) as a function of P (position, km)."""
    if model == "shift":
        return D.mean(axis=0), lambda Q: np.repeat(D.mean(axis=0)[None], len(Q), 0)
    if model == "similarity":
        # d = t + s*p + r*perp(p): 4 parameters
        A = np.zeros((2 * len(P), 4))
        A[0::2] = np.c_[np.ones(len(P)), np.zeros(len(P)), P[:, 0], -P[:, 1]]
        A[1::2] = np.c_[np.zeros(len(P)), np.ones(len(P)), P[:, 1], P[:, 0]]
        c, *_ = np.linalg.lstsq(A, D.reshape(-1), rcond=None)

        def pred(Q):
            return np.c_[c[0] + c[2] * Q[:, 0] - c[3] * Q[:, 1], c[1] + c[2] * Q[:, 1] + c[3] * Q[:, 0]]
        return c, pred
    if model == "affine":
        A = np.c_[np.ones(len(P)), P]
        cx, *_ = np.linalg.lstsq(A, D[:, 0], rcond=None)
        cy, *_ = np.linalg.lstsq(A, D[:, 1], rcond=None)
        return np.r_[cx, cy], lambda Q: np.c_[np.c_[np.ones(len(Q)), Q] @ cx, np.c_[np.ones(len(Q)), Q] @ cy]
    raise ValueError(model)


def main():
    global TILES, OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=float, default=1000, help="grid spacing, m")
    ap.add_argument("--window", type=int, default=384, help="window size, z15 px")
    ap.add_argument("--max-m", type=float, default=320, help="largest shift searched, m")
    ap.add_argument("--min-z", type=float, default=5.0)
    ap.add_argument("--tiles", default=TILES, help="tile pyramid to measure")
    ap.add_argument("--built-with", default=None,
                    help="id of the ground correction those tiles were built with "
                         "(omit for tiles built without one)")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    TILES, OUT = a.tiles, a.out
    os.makedirs(OUT, exist_ok=True)

    with open(os.path.join(ROOT, "data", "dp2021", "bounds.json"), encoding="utf-8") as f:
        b = json.load(f)
    lat0 = (b["lat_min"] + b["lat_max"]) / 2
    mpp = 156543.03392 * math.cos(math.radians(lat0)) / 2 ** Z
    step = int(a.step / mpp)
    ms = int(a.max_m / mpp)
    x_min, y_max = lonlat_to_px(b["lon_min"], b["lat_min"])
    x_max, y_min = lonlat_to_px(b["lon_max"], b["lat_max"])
    cell = 512
    print(f"loading OSM roads ...", flush=True)
    grid = load_roads(cell)
    print(f"  {sum(len(v) for v in grid.values())} segment-cells; window {a.window}px "
          f"({a.window * mpp:.0f} m), step {a.step:.0f} m, search +/-{a.max_m:.0f} m", flush=True)

    rows = []
    half = a.window // 2
    ys = range(int(y_min) + half, int(y_max) - half, step)
    xs = range(int(x_min) + half, int(x_max) - half, step)
    total = len(ys) * len(xs)
    for i, cy in enumerate(ys):
        for cx in xs:
            x0, y0 = cx - half, cy - half
            plan, have = plan_window(x0, y0, a.window)
            if have.mean() < 0.9:
                continue
            pm = plan_mask(plan)
            om = osm_window(grid, cell, x0, y0, a.window)
            if pm.mean() < 0.02 or om.mean() < 0.02:
                continue
            dy, dx, z, edge = goc_shift(orient(pm * 255.0), orient(om * 255.0), ms)
            lon, lat = px_to_lonlat(cx, cy)
            rows.append({"lon": round(lon, 6), "lat": round(lat, 6),
                         "east_m": round(dx * mpp, 1), "north_m": round(-dy * mpp, 1),
                         "peak_z": round(z, 2), "edge": bool(edge),
                         "ok": bool(z >= a.min_z and not edge),
                         "osm_density": round(float(om.mean()), 3)})
        print(f"  row {i + 1}/{len(ys)}: {len(rows)} windows, "
              f"{sum(r['ok'] for r in rows)} trusted", flush=True)

    good = [r for r in rows if r["ok"]]
    P = np.array([[(r["lon"] - 72.52) * 111.32 * math.cos(math.radians(lat0)),
                   (r["lat"] - 23.06) * 110.57] for r in good])
    D = np.array([[r["east_m"], r["north_m"]] for r in good])
    # robust: drop gross outliers (> 3 MAD from the median shift) before fitting
    med = np.median(D, axis=0)
    dist = np.hypot(*(D - med).T)
    keep = dist < max(3 * 1.4826 * np.median(dist), 30)
    P, D = P[keep], D[keep]

    # spatial-block cross validation on a 5 km grid
    blocks = np.floor(P / 5.0).astype(int)
    keys = [tuple(k) for k in blocks]
    models = {}
    for model in ("none", "shift", "similarity", "affine"):
        res = np.zeros_like(D)
        for k in set(keys):
            test = np.array([kk == k for kk in keys])
            if model == "none":
                res[test] = D[test]
                continue
            _, pred = fit(model, P[~test], D[~test])
            res[test] = D[test] - pred(P[test])
        r = np.hypot(*res.T)
        coef, _ = fit(model, P, D) if model != "none" else (None, None)
        models[model] = {"cv_median_m": round(float(np.median(r)), 1),
                         "cv_p90_m": round(float(np.percentile(r, 90)), 1),
                         "coef": None if coef is None else [round(float(c), 4) for c in np.ravel(coef)]}
        print(f"  {model:10s} held-out residual median {models[model]['cv_median_m']:6.1f} m, "
              f"p90 {models[model]['cv_p90_m']:6.1f} m")

    out = {"tiles": os.path.relpath(TILES, ROOT), "built_with": a.built_with, "z": Z, "m_per_px": round(mpp, 3), "step_m": a.step, "window_m": round(a.window * mpp),
           "windows": len(rows), "trusted": len(good), "used_after_outliers": int(keep.sum()),
           "median_shift_east_m": round(float(np.median(D[:, 0])), 1),
           "median_shift_north_m": round(float(np.median(D[:, 1])), 1),
           "shift_meaning": "metres to move the plan to land on OSM (east, north)",
           "position_origin": "km from 72.52E, 23.06N",
           "models": models, "points": rows}
    with open(os.path.join(OUT, "field.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print(f"median shift needed: E {out['median_shift_east_m']} m, N {out['median_shift_north_m']} m "
          f"({len(good)} trusted of {len(rows)} windows)")

    # quiver plot: arrows = where the plan must move, exaggerated x10
    W = 900
    sx = W / (x_max - x_min)
    H = int((y_max - y_min) * sx)
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    for r in rows:
        x, y = lonlat_to_px(r["lon"], r["lat"])
        px, py = (x - x_min) * sx, (y - y_min) * sx
        col = (20, 110, 200) if r["ok"] else (210, 210, 210)
        ex = r["east_m"] / mpp * sx * 10
        ey = -r["north_m"] / mpp * sx * 10
        d.line([(px, py), (px + ex, py + ey)], fill=col, width=1)
        d.ellipse([px - 1, py - 1, px + 1, py + 1], fill=col)
    d.text((6, 6), "arrow = shift that moves the plan onto OSM, x10. grey = untrusted", fill="black")
    im.save(os.path.join(OUT, "field.png"))


if __name__ == "__main__":
    main()
