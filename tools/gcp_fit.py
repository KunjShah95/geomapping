"""Fit an image->map affine transform from hand-picked control points.

Scale for this sheet is already established (~2.5 m/px, see
work/georef_solved.json); this script only solves POSITION, which the
mask-IoU search could not do reliably.

Input  work/gcp.csv   header: px,py,lon,lat[,name]
Output work/gcp_fit.json  +  work/gcp.gcpinfo  (GDAL-readable)

Usage:
    python tools/gcp_fit.py                 # fit and report
    python tools/gcp_fit.py --show-residual # list per-point residuals

Conventions
-----------
px, py   pixel coords in the SOURCE image (27200 x 25600), origin top-left.
lon, lat WGS84 degrees. This is column order in the CSV - lat/lon swap is
         the most likely mistake, and --show-residual will expose it.

The fit is the standard 6-parameter affine, solved independently per axis
(GDAL "polynomial order 1"). Both the forward map (px,py -> lon,lat) and the
inverse map (lon,lat -> px,py) are fitted so residuals can be reported in
both degrees/metres and in source pixels.
"""
import argparse, csv, json, math, os, sys
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WORK = os.path.join(ROOT, "work")
FULL_W, FULL_H = 27200, 25600
GCP_CSV = os.path.join(WORK, "gcp.csv")

# An affine cannot exactly represent the mercator projection: latitude is
# non-linear, so a perfect control-point set still leaves a residual that grows
# toward the top and bottom edges of the sheet. Measured on an exact synthetic
# set spanning the whole sheet: RMSE ~5px (~13m), all of it north-south.
# Thresholds are set above that projection floor so they flag real mistakes
# (mispicked points, swapped columns) rather than the projection itself.
WARN_PX = 12.0    # RMSE above this means at least one point is probably wrong
BAD_PX = 25.0     # per-point residual above this is a likely mispick


def load_gcps(path):
    pts = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(l for l in f if not l.lstrip().startswith("#")):
            try:
                pts.append(dict(
                    px=float(row["px"]), py=float(row["py"]),
                    lon=float(row["lon"]), lat=float(row["lat"]),
                    name=(row.get("name") or "").strip(),
                ))
            except (KeyError, TypeError, ValueError) as e:
                raise SystemExit(f"bad row {row!r}: {e}")
    return pts


def fit(P, F):
    """Least-squares affine: F[:, 0] = m0 + m1*P[:,0] + m2*P[:,1]."""
    A = np.column_stack([np.ones(len(P)), P])
    if np.linalg.matrix_rank(A) < 3:
        return None
    m, *_ = np.linalg.lstsq(A, F, rcond=None)
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=GCP_CSV)
    ap.add_argument("--show-residual", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.csv):
        raise SystemExit(f"no {args.csv} - copy work/gcp_template.csv and fill it in")

    pts = load_gcps(args.csv)
    n = len(pts)
    print(f"{n} control point(s)")
    if n < 3:
        raise SystemExit("need at least 3 non-collinear points for an affine fit")
    if n == 3:
        print("WARNING: 3 points gives zero redundancy - every fit is exact. "
              "Use 5+ so residuals mean something.")

    P = np.array([[p["px"], p["py"]] for p in pts], float)
    L = np.array([[p["lon"], p["lat"]] for p in pts], float)

    if np.any(P[:, 0] < 0) or np.any(P[:, 0] >= FULL_W) or \
       np.any(P[:, 1] < 0) or np.any(P[:, 1] >= FULL_H):
        print("WARNING: some px/py fall outside the 27200x25600 source image")

    lon_c, lat_c = L[:, 0].mean(), L[:, 1].mean()

    # Domain check against the anchor box. A lat/lon column swap is the most
    # likely input mistake and it is otherwise easy to miss: the fit still
    # converges, it just puts the sheet somewhere absurd.
    A_LON, A_LAT = (72.178109, 72.860675), (22.763306, 23.357406)
    m = 0.5  # degrees of slack, generous but far smaller than a swap
    if not (A_LON[0] - m <= L[:, 0].min() and L[:, 0].max() <= A_LON[1] + m):
        print(f"WARNING: lon values span {L[:, 0].min():.3f}..{L[:, 0].max():.3f}, "
              f"outside the sheet's expected {A_LON[0]:.3f}..{A_LON[1]:.3f}. "
              f"Check the column order in the CSV.")
    if not (A_LAT[0] - m <= L[:, 1].min() and L[:, 1].max() <= A_LAT[1] + m):
        print(f"WARNING: lat values span {L[:, 1].min():.3f}..{L[:, 1].max():.3f}, "
              f"outside the sheet's expected {A_LAT[0]:.3f}..{A_LAT[1]:.3f}. "
              f"Check the column order in the CSV.")

    mf = fit(P, L)          # forward: px,py -> lon,lat
    mi = fit(L, P)          # inverse: lon,lat -> px,py
    if mf is None or mi is None:
        raise SystemExit("control points are collinear or coincident - "
                         "spread them across the sheet")

    A = np.column_stack([np.ones(n), P])
    pred = A @ mf
    res_deg = L - pred
    m_lat = 110574.0
    m_lon = 111320.0 * math.cos(math.radians(lat_c))
    res_m = np.column_stack([res_deg[:, 0] * m_lon, res_deg[:, 1] * m_lat])

    Ai = np.column_stack([np.ones(n), L])
    back = Ai @ mi
    res_px = np.linalg.norm(back - P, axis=1)

    rmse_px = float(np.sqrt((res_px ** 2).mean()))
    rmse_m = float(np.sqrt((res_m ** 2).sum(axis=1).mean()))
    worst = int(np.argmax(res_px))

    # mf is (3, 2): column 0 = lon coefficients, column 1 = lat coefficients.
    #   lon = mf[0,0] + mf[1,0]*px + mf[2,0]*py
    #   lat = mf[0,1] + mf[1,1]*px + mf[2,1]*py
    lon0, lon_px, lon_py = mf[0, 0], mf[1, 0], mf[2, 0]
    lat0, lat_px, lat_py = mf[0, 1], mf[1, 1], mf[2, 1]

    # implied scale and rotation from the forward linear block
    a = np.array([[lon_px, lon_py], [lat_px, lat_py]]) * np.array([[m_lon], [m_lat]])
    sx = math.hypot(a[0, 0], a[1, 0])
    sy = math.hypot(a[0, 1], a[1, 1])
    rot = math.degrees(math.atan2(a[0, 1], a[0, 0]))

    lon_tl, lat_tl = lon0, lat0
    lon_br = lon0 + lon_px * FULL_W + lon_py * FULL_H
    lat_br = lat0 + lat_px * FULL_W + lat_py * FULL_H
    lon_min, lon_max = min(lon_tl, lon_br), max(lon_tl, lon_br)
    lat_min, lat_max = min(lat_tl, lat_br), max(lat_tl, lat_br)

    kpd = 111.32 * math.cos(math.radians((lat_min + lat_max) / 2))
    m_per_px = (lon_max - lon_min) * kpd * 1000 / FULL_W

    print()
    print("=== FIT ===")
    print(f"  lon = {lon0:.6f} + {lon_px:.9f}*px + {lon_py:.9f}*py")
    print(f"  lat = {lat0:.6f} + {lat_px:.9f}*px + {lat_py:.9f}*py")
    print(f"  RMSE   {rmse_px:.2f} px  ({rmse_m:.1f} m)")
    print(f"  scale  {m_per_px:.2f} m/px  (x/y ratio {sx / sy:.4f})")
    print(f"  rotation {rot:+.3f} deg")
    print(f"  sheet extent lon {lon_min:.4f}..{lon_max:.4f}  lat {lat_min:.4f}..{lat_max:.4f}")

    solved = os.path.join(WORK, "georef_solved.json")
    if os.path.exists(solved):
        g = json.load(open(solved, encoding="utf-8"))
        print(f"\n  vs georef_solved.json : s_full={g['s_px_per_merc_deg']:.0f} "
              f"m/px={g['m_per_px']:.2f}  (scale delta {100 * (m_per_px - g['m_per_px']) / g['m_per_px']:+.1f}%)")
        if g.get("anchor"):
            a_ = g["anchor"]
            dx = (lon_min - a_["lon_min"]) * kpd
            dy = (lat_min - a_["lat_min"]) * m_lat / 1000.0
            print(f"  vs anchor lower-left : lon {dx:+.1f} km  lat {dy:+.1f} km")

    if args.show_residual or rmse_px > WARN_PX:
        print("\n=== PER-POINT ===")
        for p, r, rm in zip(pts, res_px, res_m):
            flag = "  <== CHECK" if r > BAD_PX else ""
            print(f"  {p['name'][:22]:<22} px=({p['px']:8.1f},{p['py']:8.1f}) "
                  f"res={r:6.2f}px ({rm[0]:+7.1f},{rm[1]:+7.1f}) m{flag}")

    if rmse_px > WARN_PX:
        print(f"\nRMSE {rmse_px:.2f}px exceeds {WARN_PX}px. Worst point is "
              f"'{pts[worst]['name'] or pts[worst]['px']}' - re-check it, or add points.")
    else:
        print(f"\nRMSE {rmse_px:.2f}px is a clean fit.")

    out = dict(
        affine=dict(lon0=lon0, lon_px=lon_px, lon_py=lon_py,
                    lat0=lat0, lat_px=lat_px, lat_py=lat_py),
        rmse_px=rmse_px, rmse_m=rmse_m, m_per_px=m_per_px, rotation_deg=rot,
        extent=dict(lon_min=lon_min, lon_max=lon_max, lat_min=lat_min, lat_max=lat_max),
        gcps=pts,
    )
    with open(os.path.join(WORK, "gcp_fit.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    gpath = os.path.join(WORK, "gcp.gcpinfo")
    with open(gpath, "w", encoding="utf-8") as f:
        f.write("# image to map affine control points\n")
        f.write("# units: px,py = source pixels (27200x25600); lon,lat = WGS84 deg\n")
        f.write("mapProjections = +proj=longlat +datum=WGS84 +no_defs\n")
        f.write("# GCPs\n")
        for i, p in enumerate(pts, 1):
            f.write(f"gcp.no = {i}\n")
            f.write(f"gcp.name = {p['name'] or f'gcp{i}'}\n")
            f.write("gcp.color = 255 0 0\n")
            f.write(f"gcp.pixel = {p['px']:.3f} {p['py']:.3f}\n")
            f.write("gcp.line = 0.0 0.0\n")
            f.write(f"gcp.geo = {p['lon']:.6f} {p['lat']:.6f} 0.0\n")
    print(f"wrote work/gcp_fit.json and work/gcp.gcpinfo")


if __name__ == "__main__":
    main()