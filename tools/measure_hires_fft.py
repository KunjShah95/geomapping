"""Measure the georeference's error by FFT cross-correlation of the plan
against satellite imagery at z16.

THE PROBLEM THIS SOLVES
-----------------------
The brute-force version of this measurement (tools/measure_hires.py, the
inner loop) slides one patch against another over an 81x81 grid of
offsets. That is 6561 IoU evaluations per sample point, each over a
200x200 patch - about 17 seconds per sample, and 783 samples, so four
hours for one pass. That is not a measurement, it is a commitment.

FFT cross-correlation computes the correlation for EVERY offset at once, in
O(N log N). The same 783 samples then take a couple of minutes, which
means the measurement can be re-run to VERIFY a correction instead of
being a one-shot.

THE MEASUREMENT
---------------
At z16 the satellite is 2.4 m/px and the plan is 2.57 m/px - essentially
the same scale, so the two can be compared almost pixel for pixel. The
plan's white road corridors and the satellite's bright roads are the same
roads, so the correlation peak is the offset between them, in satellite
pixels, and therefore in metres.

The offset is reported in FOUR quadrants (west/east/north/south of the
sheet centre) as well as overall. A scale error shows up as the offsets
growing with distance from the centre, and the quadrants make that
visible directly.

Output: work/hires_error.json
"""
import argparse, glob, json, math, os, time
import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "work")
SAT = os.path.join(ROOT, "satellite")
FULL_W, FULL_H = 27200, 25600
MP_PER_PX = 2.57


def merc_y(lat):
    return math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def inv_merc_y(my):
    return math.degrees(2 * math.atan(math.exp(math.radians(my))) - math.pi / 2)


def affine_matrices(g):
    af = g.get("affine")
    if af:
        A = np.array([[af["A"][0][0], af["A"][0][1], af["A"][0][2]],
                      [af["A"][1][0], af["A"][1][1], af["A"][1][2]],
                      [0, 0, 1]], float)
        Ai = np.array([[af["Ai"][0][0], af["Ai"][0][1], af["Ai"][0][2]],
                       [af["Ai"][1][0], af["Ai"][1][1], af["Ai"][1][2]],
                       [0, 0, 1]], float)
        return A, Ai
    SX, SY = g["SX"], g["SY"]
    A = np.array([[1.0 / SX, 0.0, g["lon_min"]],
                  [0.0, -1.0 / SY, g["merc_top"]],
                  [0, 0, 1]], float)
    return A, np.linalg.inv(A)


def sat_bright_mask(z):
    """Satellite bright mask at zoom z, plus its lon/merc origin.

    Only the mask is kept - at z16 the full mosaic is 12 GB of RGB, but the
    correlation runs on one bit per pixel.

    The tile list is built by walking the directory tree rather than by
    glob: glob over 15,000 files leaves enough handles open that Windows
    starts refusing new ones partway through.
    """
    import os
    root = os.path.join(SAT, str(z))
    tiles = []
    for dirpath, dirnames, filenames in os.walk(root):
        # the layout is satellite/{z}/{x}/{y}.jpg
        try:
            tx = int(os.path.basename(dirpath))
        except ValueError:
            continue
        for fn in filenames:
            if not fn.lower().endswith(".jpg"):
                continue
            try:
                ty = int(os.path.splitext(fn)[0])
            except ValueError:
                continue
            tiles.append((tx, ty, os.path.join(dirpath, fn)))
    if not tiles:
        raise SystemExit(f"no satellite tiles at z{z}")
    xs = [t[0] for t in tiles]
    ys = [t[1] for t in tiles]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    w = (x1 - x0 + 1) * 256
    h = (y1 - y0 + 1) * 256
    mask = np.zeros((h, w), bool)
    for tx, ty, f in tiles:
        with Image.open(f) as im:
            a = np.asarray(im.convert("RGB")).astype(np.int16)
        mx = a.max(axis=2)
        mn = a.min(axis=2)
        sat = (mx - mn) / np.maximum(mx, 1)
        del a
        mask[(ty - y0) * 256:(ty - y0 + 1) * 256,
             (tx - x0) * 256:(tx - x0 + 1) * 256] = (mn > 100) & (sat < 0.22)
    n = 2 ** z
    step = 360.0 / n
    lon0 = -180.0 + x0 * step
    my0 = 180.0 - y0 * step
    return mask, lon0, my0, step / 256.0


def plan_bright_mask():
    """The plan's bright (road / paper) mask, read in bands to bound memory."""
    pm = np.memmap(os.path.join(WORK, "source.rgb"), dtype=np.uint8, mode="r",
                   shape=(FULL_H, FULL_W, 3))
    out = np.zeros((FULL_H, FULL_W), bool)
    BAND = 1024
    for y0 in range(0, FULL_H, BAND):
        y1 = min(y0 + BAND, FULL_H)
        band = np.asarray(pm[y0:y1])
        out[y0:y1] = band.min(axis=2) > 110
    del pm
    return out


def best_shift_fft(plan, sat):
    """The (dy, dx) that maximises the overlap of the two patches, via FFT.

    The plan patch is the template. The satellite patch is the search
    image. Cross-correlation of the two, via FFT, gives the overlap for
    every offset at once; the peak is the best offset.
    """
    h, w = plan.shape
    # pad both to the same size, at least 2x, so every offset is representable
    ph, pw = h * 2, w * 2
    P = np.zeros((ph, pw), np.float64)
    P[:h, :w] = plan
    S = np.zeros((ph, pw), np.float64)
    S[:h, :w] = sat
    # correlate: sum(plan * sat_shifted) for all shifts
    F = np.fft.rfft2(S)
    G = np.fft.rfft2(P[::-1, ::-1])
    cc = np.fft.irfft2(F * G, s=(ph, pw))
    # the peak of cc at (dy,dx) means sat shifted by (dy,dx) matches plan
    idx = np.unravel_index(np.argmax(cc), cc.shape)
    dy = idx[0] if idx[0] <= ph // 2 else idx[0] - ph
    dx = idx[1] if idx[1] <= pw // 2 else idx[1] - pw
    return dy, dx, float(cc[idx])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--georef", default=os.path.join(WORK, "georef.json"))
    ap.add_argument("--out", default=os.path.join(WORK, "hires_error.json"))
    ap.add_argument("--patch", type=int, default=200, help="patch half-size, source px")
    ap.add_argument("--stride", type=int, default=700, help="sample spacing, source px")
    ap.add_argument("--z", type=int, default=16)
    ap.add_argument("--max-shift", type=int, default=60, help="reject offsets beyond this")
    a = ap.parse_args()

    with open(a.georef, encoding="utf-8") as f:
        g = json.load(f)
    A, Ai = affine_matrices(g)
    print(f"georeference: {a.georef}")
    if g.get("affine"):
        af = g["affine"]
        print(f"  affine: RMSE {af['rmse_px']:.2f} px ({af['rmse_m']:.1f} m) "
              f"from {af['n_points']} points, rotation {af['rotation_deg']:+.4f} deg")
    else:
        print("  north-up model")

    print(f"loading satellite z{a.z} ...", flush=True)
    t0 = time.time()
    s_bright, slon0, smy0, deg_per_px = sat_bright_mask(a.z)
    print(f"  {s_bright.shape[1]} x {s_bright.shape[0]} px  ({time.time()-t0:.0f}s)")
    print(f"  bright fraction {s_bright.mean():.3f}")

    print("building the plan's mask ...", flush=True)
    t0 = time.time()
    p_bright = plan_bright_mask()
    print(f"  done ({time.time()-t0:.0f}s)  bright fraction {p_bright.mean():.3f}")

    m_lon = 111320.0 * math.cos(math.radians((g["lat_min"] + g["lat_max"]) / 2))
    m_merc = 110574.0 * math.cos(math.radians((g["lat_min"] + g["lat_max"]) / 2))
    sat_m_per_px = deg_per_px * m_lon

    pts = []
    y = a.patch + 300
    while y < FULL_H - a.patch - 300:
        x = a.patch + 300
        while x < FULL_W - a.patch - 300:
            pts.append((x, y))
            x += a.stride
        y += a.stride
    print(f"{len(pts)} sample points")

    results = []
    t0 = time.time()
    for i, (px, py) in enumerate(pts):
        x0, y0 = max(0, px - a.patch), max(0, py - a.patch)
        x1, y1 = min(FULL_W, px + a.patch), min(FULL_H, py + a.patch)
        if x1 - x0 < a.patch or y1 - y0 < a.patch:
            continue
        plan_p = p_bright[y0:y1, x0:x1]
        pf = plan_p.mean()
        if pf < 0.08 or pf > 0.55:
            continue

        lon, my = (A @ np.array([px, py, 1.0]))[:2]
        sx = int((lon - slon0) / deg_per_px)
        sy = int((smy0 - my) / deg_per_px)
        r = int(a.patch * MP_PER_PX / sat_m_per_px)
        if r < 32:
            continue
        sx0, sy0 = sx - r, sy - r
        sx1, sy1 = sx + r, sy + r
        if sx0 < 2 or sy0 < 2 or sx1 >= s_bright.shape[1] or sy1 >= s_bright.shape[0]:
            continue
        sat_p = s_bright[sy0:sy1, sx0:sx1]
        if sat_p.mean() < 0.05 or sat_p.mean() > 0.60:
            continue

        # the plan patch is in source px, the satellite in satellite px;
        # resample the plan to the satellite's grid before correlating
        plan_r = np.asarray(Image.fromarray(plan_p.astype(np.uint8) * 255)
                            .resize((sat_p.shape[1], sat_p.shape[0]),
                                    Image.BILINEAR)) > 127

        dy, dx, peak = best_shift_fft(plan_r, sat_p)
        if abs(dx) > a.max_shift or abs(dy) > a.max_shift:
            continue
        # score the correlation properly at the found offset
        s = np.roll(np.roll(sat_p, dy, axis=0), dx, axis=1)
        inter = np.logical_and(plan_r, s).sum()
        union = np.logical_or(plan_r, s).sum()
        iou = inter / union if union else 0.0
        if iou < 0.04:
            continue
        results.append(dict(px=px, py=py, lon=lon, lat=inv_merc_y(my),
                            dx=int(dx), dy=int(dy), iou=float(iou),
                            dlon_m=float(dx * sat_m_per_px),
                            dmerc_m=float(dy * sat_m_per_px)))
        if (i + 1) % 100 == 0:
            print(f"  {len(results)} measured  ({time.time()-t0:.0f}s)", flush=True)

    if len(results) < 20:
        raise SystemExit(f"only {len(results)} usable samples")

    dxm = np.array([r["dlon_m"] for r in results])
    dym = np.array([r["dmerc_m"] for r in results])
    shift = np.hypot(dxm, dym)
    print(f"\n=== HIGH-ZOOM ERROR MEASUREMENT (z{a.z}) ===")
    print(f"  n={len(results)}")
    print(f"  |shift| median {np.median(shift):6.1f} m   "
          f"mean {shift.mean():6.1f} m   p90 {np.percentile(shift, 90):6.1f} m")
    print(f"  dlon  median {np.median(dxm):+7.1f} m")
    print(f"  dmerc median {np.median(dym):+7.1f} m")
    print(f"  mean IoU {np.mean([r['iou'] for r in results]):.3f}")

    cxp, cyp = FULL_W / 2, FULL_H / 2
    rad = np.hypot([p["px"] - cxp for p in results], [p["py"] - cyp for p in results])
    for name, sel in (("near centre", rad < 7000), ("far  centre", rad >= 7000),
                      ("west", np.array([p["px"] < cxp for p in results])),
                      ("east", np.array([p["px"] >= cxp for p in results])),
                      ("north", np.array([p["py"] < cyp for p in results])),
                      ("south", np.array([p["py"] >= cyp for p in results]))):
        if sel.sum() < 3:
            continue
        print(f"  {name:12s}: |shift| {np.median(shift[sel]):6.1f} m  "
              f"dlon {np.median(dxm[sel]):+7.1f}  dmerc {np.median(dym[sel]):+7.1f}  "
              f"(n={int(sel.sum())})")

    out = dict(source="measure_hires_fft.py", n=len(results), z=a.z,
               shift_median_m=float(np.median(shift)),
               shift_mean_m=float(shift.mean()),
               dlon_median_m=float(np.median(dxm)),
               dmerc_median_m=float(np.median(dym)),
               mean_iou=float(np.mean([r["iou"] for r in results])),
               results=results)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
