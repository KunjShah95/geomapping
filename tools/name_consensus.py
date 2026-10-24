"""RANSAC over the place-name control points, reporting the consensus set's
spatial spread and its quality.

The question this answers: is there a subset of the name matches that agrees
on a single affine, and does that subset span the sheet? If yes, the
consensus IS the georeference and the rest of the matches are name
ambiguity, not transform error.
"""
import csv, json, math, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from affine_fit import load_gcp_csv, fit_affine, merc_y, h0_residuals

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "work")
FULL_W, FULL_H = 27200, 25600


def main():
    path = os.path.join(WORK, "gcp_names.csv")
    pts = load_gcp_csv(path)
    P = np.array([[p["px"], p["py"]] for p in pts], float)
    L = np.array([[p["lon"], merc_y(p["lat"])] for p in pts], float)
    n = len(pts)
    print(f"{n} control points")

    PXLON = FULL_W / (72.860675 - 72.178109)

    import random
    rng = random.Random(20221020)
    best = None
    for it in range(20000):
        idx = rng.sample(range(n), 3)
        if np.linalg.matrix_rank(np.column_stack([np.ones(3), P[idx]])) < 3:
            continue
        M = fit_affine(P[idx], L[idx])
        pred = np.column_stack([np.ones(n), P]) @ M
        px = np.abs(pred[:, 0] - L[:, 0]) * PXLON
        inl = px < 25
        c = int(inl.sum())
        if best is None or c > best[0]:
            best = (c, inl.copy())
    c, inl = best
    print(f"best consensus: {c}/{n} points at 25 px")

    # refit
    M = fit_affine(P[inl], L[inl])
    pred = np.column_stack([np.ones(n), P]) @ M
    px = np.abs(pred[:, 0] - L[:, 0]) * PXLON
    inl2 = inl & (px < 40)
    M = fit_affine(P[inl2], L[inl2])
    print(f"after refit: {int(inl2.sum())}/{n} inliers at 40 px")

    cons = [pts[i] for i in range(n) if inl2[i]]
    xs = [p["px"] for p in cons]
    ys = [p["py"] for p in cons]
    print(f"\nconsensus spread: px {min(xs)}..{max(xs)} (of {FULL_W})")
    print(f"                   py {min(ys)}..{max(ys)} (of {FULL_H})")
    # coverage: how many 4x4 grid cells are hit
    cells = set((p["px"] // (FULL_W // 4), p["py"] // (FULL_H // 4)) for p in cons)
    print(f"                   covers {len(cells)} of 16 quadrants")
    for p in cons:
        print(f"  {p['name']:16s} ({p['px']:7.0f},{p['py']:7.0f})  "
              f"{p['lon']:.5f},{p['lat']:.5f}")

    # residuals of the consensus
    Mi = fit_affine(L[inl2], P[inl2])
    back = np.column_stack([np.ones(n), L]) @ Mi
    rp = np.linalg.norm(back - P, axis=1)
    rmse = float(np.sqrt((rp[inl2] ** 2).mean()))
    print(f"\nconsensus RMSE: {rmse:.1f} px ({rmse*2.57:.0f} m)")
    print(f"  max residual: {rp[inl2].max():.1f} px ({rp[inl2].max()*2.57:.0f} m)")

    # geometry
    lat_c = float(np.mean([p["lat"] for p in pts]))
    m_lon = 111320.0 * math.cos(math.radians(lat_c))
    m_merc = 110574.0 * math.cos(math.radians(lat_c))
    amat = np.array([[M[1][0], M[2][0]], [M[1][1], M[2][1]]]) * np.array([[m_lon], [m_merc]])
    sx = math.hypot(amat[0, 0], amat[1, 0])
    sy = math.hypot(amat[0, 1], amat[1, 1])
    rot = math.degrees(math.atan2(amat[1, 0], amat[0, 0]))
    print(f"  scale x {sx:.4f} m/px  y {sy:.4f} m/px  anisotropy {sx/sy:.5f}")
    print(f"  rotation {rot:+.4f} deg")

    # where does it put the sheet corners?
    corners = np.array([[0, 0], [FULL_W, 0], [FULL_W, FULL_H], [0, FULL_H]], float)
    geo = np.column_stack([np.ones(4), corners]) @ M
    lons = geo[:, 0]
    mys = geo[:, 1]
    print(f"  sheet extent lon {lons.min():.6f}..{lons.max():.6f} "
          f"(H0 72.178109..72.860675)")
    print(f"  sheet extent lat {min(math.degrees(2*math.atan(math.exp(math.radians(m)))-math.pi/2) for m in mys):.6f}"
          f"..{max(math.degrees(2*math.atan(math.exp(math.radians(m)))-math.pi/2) for m in mys):.6f}"
          f"  (H0 22.763306..23.357406)")

    out = dict(consensus=[p["name"] for p in cons],
               rmse_px=rmse, rmse_m=rmse * 2.57,
               M=M.tolist(), M_inverse=fit_affine(L[inl2], P[inl2]).tolist(),
               n_consensus=int(inl2.sum()), n_total=n,
               scale_x_m_px=float(sx), scale_y_m_px=float(sy),
               rotation_deg=float(rot))
    with open(os.path.join(WORK, "name_consensus.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"\nwrote work/name_consensus.json")


if __name__ == "__main__":
    main()
