"""Fit the plan-to-ground correction from the OSM displacement field.

Input:  work/georef_osm/field.json (tools/dp_osm_offsets.py), measured on the
        served tiles.
Output: work/dp_ground_correction.json, read by tools/dp_georef.py.

The sheets' own corner coordinates place the plan ~85 m east and ~20 m north
of the ground, with a tilt that grows across the book. That is a systematic
error in the published georeference, so it is corrected where the
georeference lives: an affine map in the plan's own TM metres,

    corrected = A @ plan_metres + t

applied before the projection to lon/lat. Every tool that goes through
dp_georef.FWD / INV picks it up.

The field was measured on tiles built with whatever correction was active
then, so each measurement is first taken back to raw plan metres through that
correction; refitting after a rebuild therefore refines the correction rather
than stacking a second one on top.

The fit is robust (iteratively reweighted, Huber) and scored by spatial-block
cross-validation, the same score dp_osm_offsets.py reports.
"""
import argparse, json, math, os, sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dp_georef as G  # noqa: E402

ROOT = G.ROOT
FIELD = os.path.join(ROOT, "work", "georef_osm", "field.json")


def fit_affine(X, Y, w=None):
    """Y ~ X @ M.T + t, weighted least squares. X, Y: (n, 2)."""
    w = np.ones(len(X)) if w is None else w
    A = np.c_[X, np.ones(len(X))] * np.sqrt(w)[:, None]
    cx, *_ = np.linalg.lstsq(A, Y[:, 0] * np.sqrt(w), rcond=None)
    cy, *_ = np.linalg.lstsq(A, Y[:, 1] * np.sqrt(w), rcond=None)
    return np.array([[cx[0], cx[1]], [cy[0], cy[1]]]), np.array([cx[2], cy[2]])


def robust_fit(X, Y, k=15.0, iters=20):
    w = np.ones(len(X))
    for _ in range(iters):
        M, t = fit_affine(X, Y, w)
        r = np.hypot(*(Y - (X @ M.T + t)).T)
        w = np.where(r <= k, 1.0, k / np.maximum(r, 1e-9))
    return M, t, r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--field", default=FIELD)
    ap.add_argument("--out", default=G.CORRECTION_JSON)
    ap.add_argument("--min-z", type=float, default=5.0)
    a = ap.parse_args()

    with open(a.field, encoding="utf-8") as f:
        field = json.load(f)
    pts = [p for p in field["points"] if p["ok"] and p["peak_z"] >= a.min_z]
    lon = np.array([p["lon"] for p in pts])
    lat = np.array([p["lat"] for p in pts])
    dE = np.array([p["east_m"] for p in pts])
    dN = np.array([p["north_m"] for p in pts])

    # where the feature is on the served map, and where OSM says it belongs
    k_lat = 1 / 110574.0
    k_lon = 1 / (111320.0 * np.cos(np.radians(lat)))
    served = np.array(G.INV_RAW.transform(lon, lat)).T
    target = np.array(G.INV_RAW.transform(lon + dE * k_lon, lat + dN * k_lat)).T
    # back through the correction the tiles were built with -> raw plan metres
    built = field.get("built_with")
    old = G.load_correction() if built else None
    if built and (not old or old["id"] != built):
        raise SystemExit(f"the field was measured on tiles built with correction {built!r}, "
                         f"but work/dp_ground_correction.json is {old and old['id']!r}; "
                         "re-measure the current tiles first")
    raw = G.uncorrect(served, old) if old else served
    print(f"{len(pts)} trusted windows; tiles built with "
          f"{'correction ' + old['id'] if old else 'no correction'}")

    centre = raw.mean(axis=0)
    X, Y = raw - centre, target - centre
    M, t, resid = robust_fit(X, Y)

    # spatial-block cross-validation, 5 km blocks
    blocks = [tuple(b) for b in np.floor(X / 5000.0).astype(int)]
    cv = np.zeros(len(X))
    for b in set(blocks):
        test = np.array([bb == b for bb in blocks])
        Mb, tb, _ = robust_fit(X[~test], Y[~test])
        cv[test] = np.hypot(*(Y[test] - (X[test] @ Mb.T + tb)).T)
    before = np.hypot(*(Y - X).T)

    # decompose for the record: shift at the centre, rotation, scale
    rot = math.degrees(math.atan2(M[1, 0] - M[0, 1], M[0, 0] + M[1, 1]))
    sx, sy = np.hypot(M[0, 0], M[1, 0]), np.hypot(M[0, 1], M[1, 1])
    out = {
        "id": f"osm-affine-{field.get('trusted', len(pts))}w",
        "model": "affine in plan TM metres: corrected = M @ (m - centre) + centre + t",
        "centre_m": [round(float(c), 3) for c in centre],
        "M": [[float(v) for v in row] for row in M],
        "t_m": [float(v) for v in t],
        "summary": {
            "shift_at_centre_m": [round(float(t[0]), 1), round(float(t[1]), 1)],
            "rotation_deg": round(rot, 4),
            "scale_x": round(float(sx), 6), "scale_y": round(float(sy), 6),
        },
        "fit": {
            "windows": len(pts),
            "before_median_m": round(float(np.median(before)), 1),
            "before_p90_m": round(float(np.percentile(before, 90)), 1),
            "after_median_m": round(float(np.median(resid)), 1),
            "after_p90_m": round(float(np.percentile(resid, 90)), 1),
            "held_out_median_m": round(float(np.median(cv)), 1),
            "held_out_p90_m": round(float(np.percentile(cv, 90)), 1),
        },
        "reference": "OSM roads (ODbL) via Overpass, measured by tools/dp_osm_offsets.py",
        "note": "Residual after correction is OSM's own error plus the plan's local "
                "drawing differences; it is not georeference error alone.",
    }
    if old:
        out["previous"] = old["id"]
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    s, fit = out["summary"], out["fit"]
    print(f"shift at centre E {s['shift_at_centre_m'][0]} m, N {s['shift_at_centre_m'][1]} m; "
          f"rotation {s['rotation_deg']} deg; scale {s['scale_x']:.5f} x {s['scale_y']:.5f}")
    print(f"error before {fit['before_median_m']} m (p90 {fit['before_p90_m']}), "
          f"after {fit['after_median_m']} m (p90 {fit['after_p90_m']}), "
          f"held-out {fit['held_out_median_m']} m (p90 {fit['held_out_p90_m']})")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
