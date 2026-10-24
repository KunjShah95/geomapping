"""Solve the sheet's georeference from control points, properly.

WHY THIS EXISTS
---------------
The current georeference (H0) anchors the sheet's four edges to a bounding box
that townplanmap.com publishes by hand:

    lon 72.178109 .. 72.860675   lat 22.763306 .. 23.357406

That box is internally anisotropic by 0.5% - its Mercator aspect does not match
the scan's pixel aspect - so H0 has to stretch one axis to fit. The error
accumulates away from the centre and reaches ~365 m at the north/south edges
of a 70 km sheet. H0 is also a 4-parameter model (origin + 2 scales, north-up),
so it cannot absorb rotation or shear, both of which a real scan has.

WHAT THIS DOES
--------------
Fits a full 6-parameter affine (rotation + shear + independent x/y scale +
translation) from control points, by least squares, and reports the residual in
metres and in source pixels.

WHY MERCATOR, NOT LATITUDE
---------------------------
The fit is done in (lon, mercator_y) space, not (lon, lat). That matters for
two reasons:

  1. Web Mercator tiles are laid out in Mercator. A transform that is affine
     in Mercator is affine in tile space, so a fitted tile can be rendered
     with an exact pixel-exact warp instead of a resampled approximation.

  2. latitude is a non-linear function of Mercator y, so a fit that is affine
     in latitude is NOT affine in tile space, and the tile builder would have
     to resample rather than warp.

Residuals are reported in metres, using the local scale of each axis.

The fit is only worth anything if the residuals are small, so this script
refuses to write a georeference whose RMSE is worse than the H0 it replaces.
"""
import argparse, csv, json, math, os

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "work")
FULL_W, FULL_H = 27200, 25600

# H0, for comparison
H0 = dict(lon_min=72.178109, lon_max=72.860675,
          lat_min=22.763306, lat_max=23.357406)

# source pixels per degree of longitude at this latitude's scale - used to
# turn a degree residual into a pixel residual for outlier rejection
PX_PER_DEG_LON = 27200 / (H0["lon_max"] - H0["lon_min"])


def merc_y(lat):
    return math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def inv_merc_y(my):
    return math.degrees(2 * math.atan(math.exp(math.radians(my))) - math.pi / 2)


def load_gcp_csv(path):
    pts = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(l for l in f if not l.lstrip().startswith("#")):
            pts.append(dict(px=float(row["px"]), py=float(row["py"]),
                            lon=float(row["lon"]), lat=float(row["lat"]),
                            name=(row.get("name") or "").strip()))
    return pts


def fit_affine(P, F):
    """Least squares: F = [1 P] @ M.  P is (n,2) source px, F is (n,2)."""
    A = np.column_stack([np.ones(len(P)), P])
    M, *_ = np.linalg.lstsq(A, F, rcond=None)
    return M


def scales(lat_c):
    """Metres per degree of lon and per degree of Mercator y, at a latitude."""
    m_lon = 111320.0 * math.cos(math.radians(lat_c))
    m_merc = 110574.0 * math.cos(math.radians(lat_c))
    return m_lon, m_merc


def h0_residuals(pts):
    """Residual of each point under H0, for comparison."""
    m_lon, m_merc = scales((H0["lat_min"] + H0["lat_max"]) / 2)
    SX = FULL_W / (H0["lon_max"] - H0["lon_min"])
    SY = FULL_H / (merc_y(H0["lat_max"]) - merc_y(H0["lat_min"]))
    out = []
    for p in pts:
        lon = H0["lon_min"] + p["px"] / SX
        my = merc_y(H0["lat_max"]) - p["py"] / SY
        dx = (lon - p["lon"]) * m_lon
        dy = (my - merc_y(p["lat"])) * m_merc
        out.append((p["name"], math.hypot(dx, dy), dx, dy))
    rmse = math.sqrt(sum(r[1] ** 2 for r in out) / len(out))
    print("\n=== H0 (current, for comparison) ===")
    print(f"  n={len(pts)}  RMSE {rmse:.1f} m")
    for name, d, dx, dy in sorted(out, key=lambda r: -r[1]):
        print(f"    {name[:26]:<26} {d:8.1f} m  (dlon {dx:+8.1f} m, dmerc {dy:+8.1f} m)")
    return rmse, out


def robust_fit(P, F, n_points, max_res_px, max_iters=12):
    """Least squares with iterative outlier rejection.

    A least-squares fit over control points is only as good as its worst point.
    One bad match - a plan blob that is really a different lake, an OSM polygon
    that is really a canal - drags the whole fit.

    So: fit, drop the points that are further than max_res_px from the fit, and
    refit. Repeat until the set is stable. The number of points kept is
    reported, and the dropped ones are listed, because "we used 14 of 19
    points" is information the reader needs.
    """
    keep = np.ones(len(P), dtype=bool)
    dropped = []
    M = None
    for _ in range(max_iters):
        A = np.column_stack([np.ones(int(keep.sum())), P[keep]])
        M, *_ = np.linalg.lstsq(A, F[keep], rcond=None)
        # residual in source PIXELS: fit the inverse on the kept points and
        # measure how far every point lands from its true pixel.
        Mi = fit_affine(F[keep], P[keep])
        back = np.column_stack([np.ones(len(P)), F]) @ Mi
        px = np.linalg.norm(back - P, axis=1)
        bad = keep & (px > max_res_px)
        if not bad.any():
            break
        for i in np.nonzero(bad)[0]:
            dropped.append(int(i))
        keep = keep & ~bad
        if keep.sum() < 4:
            raise SystemExit("outlier rejection left too few points")
    return M, keep, dropped


def ransac_fit(P, F, n_iter=2000, thresh_px=25.0, rng=None):
    """RANSAC: find the affine that the most control points agree with.

    Why this and not plain least squares: the control points come from
    matching a NAME on the scan to a PLACE in OSM. Most of those matches are
    right, but a few are wrong in a way that is not obvious - a name that
    exists in three villages, a label that is drawn well away from the
    settlement it names, an OCR misread that happens to be a real place
    name elsewhere. A bad point is not a small perturbation, it is a
    kilometre out, and one kilometre out in a least-squares fit drags the
    whole surface with it.

    So: sample three points (the minimum that determines an affine), fit,
    count the inliers, keep the best consensus, then refit on the inliers
    only. The consensus is what identifies the true transform; the refit is
    what makes it precise.
    """
    import random
    rng = rng or random.Random(20221020)
    n = len(P)
    if n < 3:
        raise SystemExit("need 3+ points for RANSAC")
    best_inliers = None
    best_count = 0
    for _ in range(n_iter):
        idx = rng.sample(range(n), 3)
        Pa, Fa = P[idx], F[idx]
        if np.linalg.matrix_rank(np.column_stack([np.ones(3), Pa])) < 3:
            continue
        M = fit_affine(Pa, Fa)
        pred = np.column_stack([np.ones(n), P]) @ M
        # residual in source pixels along the lon axis (the dominant term)
        px = np.abs(pred[:, 0] - F[:, 0]) * PX_PER_DEG_LON
        inl = px < thresh_px
        c = int(inl.sum())
        if c > best_count:
            best_count = c
            best_inliers = inl
    if best_inliers is None:
        raise SystemExit("RANSAC found no consensus")
    # refit on the consensus
    A = np.column_stack([np.ones(int(best_inliers.sum())), P[best_inliers]])
    M, *_ = np.linalg.lstsq(A, F[best_inliers], rcond=None)
    # one more rejection pass on the refit, to drop the marginal inliers
    pred = np.column_stack([np.ones(n), P]) @ M
    px = np.abs(pred[:, 0] - F[:, 0]) * PX_PER_DEG_LON
    inl = best_inliers & (px < thresh_px * 1.6)
    A = np.column_stack([np.ones(int(inl.sum())), P[inl]])
    M, *_ = np.linalg.lstsq(A, F[inl], rcond=None)
    return M, inl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gcp", default=os.path.join(WORK, "gcp.csv"))
    ap.add_argument("--out", default=os.path.join(WORK, "affine_fit.json"))
    ap.add_argument("--max-rmse-m", type=float, default=150.0,
                    help="refuse to write a fit worse than this (metres)")
    ap.add_argument("--max-res-px", type=float, default=40.0,
                    help="outlier threshold: control points further than this from "
                         "the fit are dropped and the fit is repeated")
    ap.add_argument("--ransac", action="store_true",
                    help="use RANSAC to find the consensus transform first")
    ap.add_argument("--ransac-iters", type=int, default=4000)
    ap.add_argument("--ransac-thresh", type=float, default=22.0,
                    help="inlier threshold for RANSAC, in source pixels")
    a = ap.parse_args()

    if not os.path.exists(a.gcp):
        raise SystemExit(f"no {a.gcp}")
    pts = load_gcp_csv(a.gcp)
    if len(pts) < 4:
        raise SystemExit(f"need 4+ control points for a 6-parameter affine, got {len(pts)}")
    print(f"{len(pts)} control points")

    P = np.array([[p["px"], p["py"]] for p in pts], float)
    # target: lon and Mercator y. Mercator keeps the transform affine in
    # tile space, which is what the tile builder needs.
    L = np.array([[p["lon"], merc_y(p["lat"])] for p in pts], float)

    if a.ransac:
        M, keep = ransac_fit(P, L, a.ransac_iters, a.ransac_thresh)
        print(f"RANSAC: consensus of {int(keep.sum())} of {len(pts)} points "
              f"at {a.ransac_thresh} px")
        dropped = [i for i in range(len(pts)) if not keep[i]]
        if dropped:
            print("  outliers: " + ", ".join(pts[i]["name"] for i in dropped))
    else:
        M, keep, dropped = robust_fit(P, L, len(pts), a.max_res_px)
        if dropped:
            print(f"outlier rejection dropped {len(dropped)} point(s): "
                  + ", ".join(pts[i]["name"] for i in dropped))
            print(f"  using {int(keep.sum())} of {len(pts)} points")

    A = np.column_stack([np.ones(len(P)), P])
    pred = A @ M
    res = L - pred
    lat_c = float(np.mean([p["lat"] for p in pts]))
    m_lon, m_merc = scales(lat_c)
    res_m = np.column_stack([res[:, 0] * m_lon, res[:, 1] * m_merc])

    # inverse: (lon, merc) -> px
    Mi = fit_affine(L[keep], P[keep])
    back = np.column_stack([np.ones(len(P)), L]) @ Mi
    res_px = np.linalg.norm(back - P, axis=1)

    rmse_px = float(np.sqrt((res_px ** 2).mean()))
    rmse_m = float(np.sqrt((res_m ** 2).sum(axis=1).mean()))
    worst = int(np.argmax(res_px))

    # implied geometry, in metres.
    # M rows are [const, d/dpx, d/dpy]; column 0 of M is the lon row, column 1
    # is the merc row. So the 2x2 that maps source px -> (east, north) is:
    #   [dlon/dpx   dlon/dpy  ]
    #   [dmerc/dpx  dmerc/dpy ]
    amat = np.array([[M[1][0], M[2][0]],
                     [M[1][1], M[2][1]]]) * np.array([[m_lon], [m_merc]])
    sx = math.hypot(amat[0, 0], amat[1, 0])
    sy = math.hypot(amat[0, 1], amat[1, 1])
    # rotation of the east axis away from screen-right (0 for north-up)
    rot = math.degrees(math.atan2(amat[1, 0], amat[0, 0]))
    # shear: how far the north axis is from being perpendicular to the east
    # axis, wrapped into (-90, 90] so a north-up sheet reads ~0
    north_ang = math.degrees(math.atan2(amat[1, 1], amat[0, 1]))
    shear = (north_ang - (rot + 90.0) + 90.0) % 180.0 - 90.0

    print("\n=== FIT (6-parameter affine, Mercator space) ===")
    print(f"  n={len(pts)}  RMSE {rmse_px:.2f} px  ({rmse_m:.1f} m)")
    print(f"  scale x {sx:.4f} m/px   y {sy:.4f} m/px   anisotropy {sx/sy:.5f}")
    print(f"  rotation {rot:+.4f} deg   shear {shear:+.4f} deg")
    for p, rp, rm in zip(pts, res_px, res_m):
        flag = "  <== CHECK" if rp > 20 else ""
        print(f"    {p['name'][:26]:<26} ({p['px']:8.1f},{p['py']:8.1f}) "
              f"res {rp:6.2f}px ({rm[0]:+7.1f},{rm[1]:+7.1f})m{flag}")

    h0_rmse, h0_detail = h0_residuals(pts)

    print()
    if rmse_m >= h0_rmse:
        print(f"REFUSING: affine RMSE {rmse_m:.1f} m is not better than "
              f"H0's {h0_rmse:.1f} m on the same points.")
        raise SystemExit(1)
    if rmse_m > a.max_rmse_m:
        print(f"REFUSING: affine RMSE {rmse_m:.1f} m exceeds the "
              f"{a.max_rmse_m:.0f} m threshold - check the control points.")
        raise SystemExit(1)

    # sheet extent under the fitted transform
    corners = np.array([[0, 0], [FULL_W, 0], [FULL_W, FULL_H], [0, FULL_H]], float)
    Ac = np.column_stack([np.ones(4), corners])
    geo = Ac @ M
    lon_min, lon_max = geo[:, 0].min(), geo[:, 0].max()
    my_min, my_max = geo[:, 1].min(), geo[:, 1].max()
    lat_min, lat_max = inv_merc_y(my_min), inv_merc_y(my_max)
    print("\nsheet extent under the fit:")
    print(f"  lon {lon_min:.6f} .. {lon_max:.6f}   (H0 {H0['lon_min']} .. {H0['lon_max']})")
    print(f"  lat {lat_min:.6f} .. {lat_max:.6f}   (H0 {H0['lat_min']} .. {H0['lat_max']})")
    print(f"  shift vs H0: lon {(lon_min - H0['lon_min'])*1000:+.1f} m, "
          f"lat {(lat_min - H0['lat_min'])*1000:+.1f} m")

    out = dict(
        source="affine_fit.py",
        n_points=len(pts),
        rmse_px=rmse_px, rmse_m=rmse_m,
        h0_rmse_m=h0_rmse,
        m_per_px_x=float(sx), m_per_px_y=float(sy),
        rotation_deg=float(rot), shear_deg=float(shear),
        # M is 3x2 in MERCATOR space: [lon, merc] = [1, px, py] @ M
        M=M.tolist(),
        M_inverse=Mi.tolist(),
        extent=dict(lon_min=lon_min, lon_max=lon_max, lat_min=lat_min, lat_max=lat_max),
        gcps=pts,
    )
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"\nwrote {a.out}")
    print(f"RMSE improved {h0_rmse:.1f} m -> {rmse_m:.1f} m "
          f"({100*(h0_rmse-rmse_m)/h0_rmse:.0f}% better)")


if __name__ == "__main__":
    main()
