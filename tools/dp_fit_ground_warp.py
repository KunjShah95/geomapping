"""Fit the local ground warp: the smooth residual the affine correction leaves.

Input:  work/georef_osm/field.json, measured on tiles built with the affine
        ground correction ONLY (built_with == the affine's id).
Output: work/dp_ground_warp.json, read by tools/dp_georef.py.

tools/dp_fit_local_warp.py showed the affine's residuals are spatially
coherent (neighbour correlation +0.42 at 1 km, fading by ~10 km) and that a
Gaussian-kernel average at 3 km predicts held-out 5 km blocks best:
7.7 -> 5.8 m median with a 2 km leakage buffer. This turns that model into a
correction:

    R(g) = sum_i w_i(g) d_i / (sum_i w_i(g) + s0),   w_i = exp(-|g - p_i|^2 / 2 sigma^2)

where p_i are window centres in corrected-ground TM metres, d_i the residual
shift each window measured, and s0 a shrink term (one virtual window with zero
residual): where windows are dense R is their local average; where they are
sparse (sheet edges, gamtals with no roads) R fades to zero instead of
extrapolating a neighbour's shift.

R is sampled on a regular grid in TM metres; dp_georef bilinearly
interpolates it, applies it after the affine (forward), and inverts it by
fixed-point iteration.
"""
import argparse, json, math, os, sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dp_georef as G  # noqa: E402

FIELD = os.path.join(G.ROOT, "work", "georef_osm", "field.json")


def gauss_field(P, D, sigma, s0):
    def f(Q):
        out = np.zeros((len(Q), 2))
        for i in range(0, len(Q), 4000):          # chunked: grid x windows is large
            q = Q[i:i + 4000]
            d2 = ((q[:, None, :] - P[None, :, :]) ** 2).sum(axis=2)
            w = np.exp(-d2 / (2 * sigma ** 2))
            out[i:i + 4000] = (w @ D) / (w.sum(axis=1, keepdims=True) + s0)
        return out
    return f


def cross_validate(P, D, sigma, s0, block_m=5000.0, buffer_m=2000.0):
    blocks = np.floor(P / block_m).astype(int)
    keys = [tuple(b) for b in blocks]
    res = np.zeros(len(P))
    for k in set(keys):
        test = np.array([kk == k for kk in keys])
        lo = np.array(k) * block_m - buffer_m
        hi = (np.array(k) + 1) * block_m + buffer_m
        near = np.all((P >= lo) & (P < hi), axis=1)
        train = ~test & ~near
        f = gauss_field(P[train], D[train], sigma, s0)
        res[test] = np.hypot(*(D[test] - f(P[test])).T)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--field", default=FIELD)
    ap.add_argument("--out", default=G.WARP_JSON)
    ap.add_argument("--sigma-m", type=float, default=3000.0)
    ap.add_argument("--shrink", type=float, default=1.0,
                    help="weight of the virtual zero-residual window")
    ap.add_argument("--grid-m", type=float, default=250.0)
    a = ap.parse_args()

    corr = G.CORRECTION
    if not corr:
        raise SystemExit("no affine ground correction active; fit that first")
    with open(a.field, encoding="utf-8") as f:
        field = json.load(f)
    if field.get("built_with") != corr["id"]:
        raise SystemExit(f"field was measured on tiles built with {field.get('built_with')!r}; "
                         f"the warp must be fitted on tiles built with the affine "
                         f"{corr['id']!r} alone")

    pts = [p for p in field["points"] if p["ok"]]
    lon = np.array([p["lon"] for p in pts]); lat = np.array([p["lat"] for p in pts])
    dE = np.array([p["east_m"] for p in pts]); dN = np.array([p["north_m"] for p in pts])
    # same outlier rule as dp_osm_offsets / dp_fit_local_warp
    D0 = np.c_[dE, dN]
    dist = np.hypot(*(D0 - np.median(D0, axis=0)).T)
    keep = dist < max(3 * 1.4826 * np.median(dist), 30)
    lon, lat, dE, dN = lon[keep], lat[keep], dE[keep], dN[keep]

    # window centre and its target, both in corrected-ground TM metres
    k_lat = 1 / 110574.0
    k_lon = 1 / (111320.0 * np.cos(np.radians(lat)))
    P = np.array(G.INV_RAW.transform(lon, lat)).T
    T = np.array(G.INV_RAW.transform(lon + dE * k_lon, lat + dN * k_lat)).T
    D = T - P

    cv = cross_validate(P, D, a.sigma_m, a.shrink)
    base = np.hypot(*D.T)
    print(f"{len(P)} windows; held-out (5 km blocks, 2 km buffer): "
          f"none {np.median(base):.2f}/{np.percentile(base, 90):.2f} m -> "
          f"warp {np.median(cv):.2f}/{np.percentile(cv, 90):.2f} m (median/p90)")

    # the grid: the plan's extent in corrected-ground TM, padded by 2 sigma
    blocks, _ = G.build_blocks()
    xs, ys = [], []
    for b in blocks.values():
        for x, y in b.page_corners:
            lo_, la_ = b.page_to_ground(x, y)
            mx, my = G.INV_RAW.transform(lo_, la_)
            xs.append(mx); ys.append(my)
    pad = 2 * a.sigma_m
    x0, y0 = math.floor((min(xs) - pad) / a.grid_m) * a.grid_m, math.floor((min(ys) - pad) / a.grid_m) * a.grid_m
    nx = int(math.ceil((max(xs) + pad - x0) / a.grid_m)) + 1
    ny = int(math.ceil((max(ys) + pad - y0) / a.grid_m)) + 1
    gx, gy = np.meshgrid(x0 + np.arange(nx) * a.grid_m, y0 + np.arange(ny) * a.grid_m)
    R = gauss_field(P, D, a.sigma_m, a.shrink)(np.c_[gx.ravel(), gy.ravel()]).reshape(ny, nx, 2)

    out = {
        "id": f"gauss{a.sigma_m / 1000:g}km-s{a.shrink:g}-{len(P)}w",
        "affine": corr["id"],
        "model": "corrected_ground += bilinear(R, corrected_ground), TM metres, after the affine",
        "sigma_m": a.sigma_m, "shrink": a.shrink,
        "grid": {"x0": x0, "y0": y0, "step": a.grid_m, "nx": nx, "ny": ny},
        "dE": [[round(float(v), 3) for v in row] for row in R[..., 0]],
        "dN": [[round(float(v), 3) for v in row] for row in R[..., 1]],
        "fit": {"windows": int(len(P)),
                "before_median_m": round(float(np.median(base)), 2),
                "before_p90_m": round(float(np.percentile(base, 90)), 2),
                "held_out_median_m": round(float(np.median(cv)), 2),
                "held_out_p90_m": round(float(np.percentile(cv, 90)), 2),
                "cv": "5 km blocks, 2 km buffer"},
        "max_shift_m": round(float(np.hypot(R[..., 0], R[..., 1]).max()), 2),
        "reference": "OSM roads, tools/dp_osm_offsets.py",
    }
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"grid {nx}x{ny} at {a.grid_m:g} m, largest shift {out['max_shift_m']} m; wrote {a.out}")


if __name__ == "__main__":
    main()
