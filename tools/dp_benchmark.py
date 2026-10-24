"""Benchmark plan-to-satellite alignment at fixed points (townplanmap comparison).

Input: work/benchmark/ours/<slug>_sat.png and <slug>_plan.png, captured from
the viewer at the same centre and zoom (see work/benchmark/points.json).
For each pair the plan's paper-white road corridors and the satellite's grey
road surface are masked, and the shift that best overlays them is found by
gradient-orientation correlation. The shift is how far the plan sits from
today's ground - plan age plus georeference, not georeference alone.

References: OSM roads (<slug>_osm.png), Esri Wayback Oct 2021 imagery
(<slug>_sat2021.png, the plan's era) and current imagery (<slug>_sat.png).

Output: work/benchmark/ours_offsets.json
"""
import argparse, glob, json, math, os
import numpy as np
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH = os.path.join(ROOT, "work", "benchmark")


def plan_mask(a):
    return a.min(axis=2) > 150


def sat_mask(a):
    mx = a.max(axis=2)
    mn = a.min(axis=2)
    sat = (mx - mn) / np.maximum(mx, 1)
    return (mn > 100) & (sat < 0.22)


def xcorr_shift(moving, fixed, max_shift):
    """Shift (dy, dx) that moves `moving` onto `fixed`, plus peak strength.

    Both are zero-meaned so empty sheet and dense blocks do not dominate.
    The peak is refined to sub-pixel with a parabola on each axis.
    """
    m = moving.astype(np.float64) - moving.mean()
    f = fixed.astype(np.float64) - fixed.mean()
    h, w = m.shape
    F = np.fft.rfft2(f, s=(2 * h, 2 * w))
    M = np.fft.rfft2(m, s=(2 * h, 2 * w))
    cc = np.fft.irfft2(F * np.conj(M), s=(2 * h, 2 * w))
    return _peak(np.fft.fftshift(cc), h, w, max_shift)


def orient(gray, sigma=1.5):
    """Doubled-angle gradient field: edge direction without edge polarity.

    A plan road is a light band on colour; a real road can be darker or
    lighter than what is beside it. Squaring the unit gradient maps both
    polarities of the same edge to the same value, so the two can be
    correlated. Magnitude is kept (square-rooted) so strong edges count more
    than texture noise.
    """
    from scipy import ndimage
    g = ndimage.gaussian_filter(gray.astype(np.float64), sigma)
    gx = ndimage.sobel(g, 1)
    gy = ndimage.sobel(g, 0)
    z = gx + 1j * gy
    mag = np.abs(z)
    mag[mag == 0] = 1
    return z ** 2 / mag ** 1.5


def goc_shift(moving, fixed, max_shift):
    """xcorr_shift for complex orientation fields (real part of the correlation)."""
    h, w = moving.shape
    F = np.fft.fft2(fixed, s=(2 * h, 2 * w))
    M = np.fft.fft2(moving, s=(2 * h, 2 * w))
    cc = np.fft.fftshift(np.real(np.fft.ifft2(F * np.conj(M))))
    return _peak(cc, h, w, max_shift)


def _peak(cc, cy, cx, max_shift):
    win = cc[cy - max_shift:cy + max_shift + 1, cx - max_shift:cx + max_shift + 1]
    iy, ix = np.unravel_index(np.argmax(win), win.shape)

    def refine(v_m, v_0, v_p):
        d = v_m - 2 * v_0 + v_p
        return 0.0 if d == 0 else 0.5 * (v_m - v_p) / d

    sy = sx = 0.0
    if 0 < iy < win.shape[0] - 1:
        sy = refine(win[iy - 1, ix], win[iy, ix], win[iy + 1, ix])
    if 0 < ix < win.shape[1] - 1:
        sx = refine(win[iy, ix - 1], win[iy, ix], win[iy, ix + 1])
    z = (win[iy, ix] - win.mean()) / (win.std() + 1e-9)
    edge = iy in (0, win.shape[0] - 1) or ix in (0, win.shape[1] - 1)
    return iy - max_shift + sy, ix - max_shift + sx, float(z), edge


def self_test():
    rng = np.random.default_rng(0)
    base = rng.random((400, 400)) > 0.97
    base = np.maximum.reduce([np.roll(base, k, 1) for k in range(6)])
    moved = np.roll(np.roll(base, -7, 0), 11, 1)
    dy, dx, _, _ = xcorr_shift(moved, base, 40)
    assert round(dy) == 7 and round(dx) == -11, (dy, dx)


def osm_mask(a):
    """Road fills on the OSM street map: white minor roads, coloured major ones."""
    a = a.astype(np.int16)
    white = a.min(axis=2) >= 250
    coloured = (a[..., 0] > 235) & (a[..., 2] < 205) & (a[..., 0] - a[..., 2] > 40)
    return white | coloured


def gray(a):
    return a.astype(np.float64) @ [0.299, 0.587, 0.114]


def load(slug, kind):
    f = os.path.join(BENCH, "ours", f"{slug}_{kind}.png")
    return np.asarray(Image.open(f).convert("RGB")) if os.path.exists(f) else None


# reference name -> (capture suffix, feature builder for the reference image)
REFS = {
    "osm": ("osm", lambda a: orient(osm_mask(a) * 255.0)),
    "sat2021": ("sat2021", lambda a: orient(gray(a))),
    "sat_now": ("sat", lambda a: orient(gray(a))),
}


def summarise(rows, key, min_z):
    good = [r[key] for r in rows if key in r and r[key]["ok"]]
    d = np.array([g["offset_m"] for g in good])
    if not len(d):
        return {"n": 0}
    return {"n": int(len(d)), "of": sum(1 for r in rows if key in r),
            "median_m": round(float(np.median(d)), 1),
            "p90_m": round(float(np.percentile(d, 90)), 1),
            "mean_east_m": round(float(np.mean([g["east_m"] for g in good])), 1),
            "mean_north_m": round(float(np.mean([g["north_m"] for g in good])), 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zoom", type=float, default=16)
    ap.add_argument("--max-m", type=float, default=100, help="largest offset searched")
    ap.add_argument("--min-z", type=float, default=5.0,
                    help="peak strength below which a match is not trusted")
    a = ap.parse_args()
    self_test()

    with open(os.path.join(BENCH, "points.json"), encoding="utf-8") as f:
        pts = json.load(f)
    rows = []
    for p in pts:
        plan = load(p["slug"], "plan")
        if plan is None:
            print(f"  {p['slug']}: no plan capture, skipped")
            continue
        # MapLibre zoom is 512-px based: z16 in the viewer is z17 in 256-px tile terms
        m_per_px = 156543.03392 * math.cos(math.radians(p["lat"])) / 2 ** (a.zoom + 1)
        ms = int(a.max_m / m_per_px)
        plan_o = orient(plan_mask(plan.astype(np.int16)) * 255.0)
        row = dict(p)
        line = f"  {p['slug']:12s}"

        def measure(mov, fix):
            dy, dx, z, edge = goc_shift(mov, fix, ms)
            e, n = dx * m_per_px, -dy * m_per_px
            return {"east_m": round(e, 1), "north_m": round(n, 1),
                    "offset_m": round(math.hypot(e, n), 1), "peak_z": round(z, 1),
                    "ok": bool(z >= a.min_z and not edge)}

        for name, (suffix, feat) in REFS.items():
            ref = load(p["slug"], suffix)
            if ref is None:
                continue
            row[name] = measure(plan_o, feat(ref))
            r = row[name]
            line += f"  {name} {r['offset_m']:5.1f}m z{r['peak_z']:4.1f}{'' if r['ok'] else '?'}"
        # sanity: the two imagery dates should agree to a few metres
        s21, snow = load(p["slug"], "sat2021"), load(p["slug"], "sat")
        if s21 is not None and snow is not None:
            row["sat2021_vs_now"] = measure(orient(gray(s21)), orient(gray(snow)))
            line += f"  | 2021~now {row['sat2021_vs_now']['offset_m']:4.1f}m"
        rows.append(row)
        print(line)

    summary = {"zoom": a.zoom, "min_peak_z": a.min_z, "max_search_m": a.max_m,
               "method": "gradient-orientation correlation (doubled angle) of the plan's road "
                         "corridors against each reference, 2.2 km window per point",
               "note": "Offsets are plan vs. ground. They include plan age (roads widened and "
                       "rebuilt since the survey); the georeference alone is +/-2.2 m.",
               "summary": {k: summarise(rows, k, a.min_z) for k in [*REFS, "sat2021_vs_now"]},
               "points": rows}
    with open(os.path.join(BENCH, "ours_offsets.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)
    for k, v in summary["summary"].items():
        print(f"{k:15s} {v}")


if __name__ == "__main__":
    main()
