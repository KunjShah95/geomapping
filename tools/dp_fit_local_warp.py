"""Would a local warp beat the affine ground correction? Measure, don't apply.

Input:  work/georef_osm/field.json - plan-vs-OSM shifts on a 1 km grid,
        measured on tiles already built with the affine correction, so each
        shift is the residual the affine left behind.
Output: work/georef_osm/warp/report.json, work/georef_osm/warp/residuals.png

A local warp can only help if the residuals are spatially coherent: if the
window 1 km east of a window has a similar residual, a smooth field can
predict it; if neighbours are uncorrelated, the residual is local drawing
difference / OSM error and any warp just fits noise.

So this does two things:
  1. the spatial autocorrelation of the residuals (binned correlation of
     residual vectors against separation), and
  2. candidate smooth fields - thin-plate splines at several smoothings,
     Gaussian-kernel averages at 3/5/8 km - each scored by spatial-block
     cross-validation (fit without a block, predict it), at 5 km and 3 km
     blocks, against the "none" baseline.

Verdict rule: recommend a warp only if the held-out median improves by at
least 1.5 m AND the p90 improves.
"""
import json, math, os

import numpy as np
from PIL import Image, ImageDraw
from scipy.interpolate import RBFInterpolator

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIELD = os.path.join(ROOT, "work", "georef_osm", "field.json")
OUT = os.path.join(ROOT, "work", "georef_osm", "warp")
LAT0, LON0 = 23.06, 72.52


def load():
    with open(FIELD, encoding="utf-8") as f:
        field = json.load(f)
    good = [p for p in field["points"] if p["ok"]]
    P = np.array([[(p["lon"] - LON0) * 111.32 * math.cos(math.radians(LAT0)),
                   (p["lat"] - LAT0) * 110.57] for p in good])          # km
    D = np.array([[p["east_m"], p["north_m"]] for p in good])            # m
    # the same outlier rule dp_osm_offsets.py uses, so "none" matches its figure
    med = np.median(D, axis=0)
    dist = np.hypot(*(D - med).T)
    keep = dist < max(3 * 1.4826 * np.median(dist), 30)
    return field, P[keep], D[keep]


def autocorrelation(P, D, bins=(0, 1.5, 2.5, 3.5, 5, 7, 10, 15, 25)):
    """Correlation of residual vectors (each component, pooled) vs separation."""
    R = D - D.mean(axis=0)
    dx = P[:, None, :] - P[None, :, :]
    sep = np.hypot(dx[..., 0], dx[..., 1])
    iu = np.triu_indices(len(P), 1)
    sep = sep[iu]
    var = (R ** 2).sum(axis=1).mean()
    prod = (R[:, None, :] * R[None, :, :]).sum(axis=2)[iu]
    out = []
    for lo, hi in zip(bins, bins[1:]):
        m = (sep >= lo) & (sep < hi)
        if m.sum() < 20:
            continue
        out.append({"from_km": lo, "to_km": hi, "pairs": int(m.sum()),
                    "correlation": round(float(prod[m].mean() / var), 3)})
    return out


def model_tps(smoothing):
    def fit(Ptr, Dtr):
        f = RBFInterpolator(Ptr, Dtr, kernel="thin_plate_spline",
                            smoothing=smoothing, degree=1)
        return f
    return fit


def model_gauss(scale_km):
    def fit(Ptr, Dtr):
        def f(Q):
            d2 = ((Q[:, None, :] - Ptr[None, :, :]) ** 2).sum(axis=2)
            w = np.exp(-d2 / (2 * scale_km ** 2))
            s = w.sum(axis=1, keepdims=True)
            return np.where(s > 1e-9, (w @ Dtr) / np.maximum(s, 1e-9), 0.0)
        return f
    return fit


def model_none():
    return lambda Ptr, Dtr: (lambda Q: np.zeros((len(Q), 2)))


def cross_validate(P, D, fit, block_km, buffer_km=0.0):
    """Held-out residuals. buffer_km also drops training windows within that
    distance of the held-out block: neighbouring windows overlap (1.7 km
    windows every 1 km), so without a buffer a test window can borrow from
    a training window that saw the same roads."""
    keys = [tuple(k) for k in np.floor(P / block_km).astype(int)]
    res = np.zeros(len(P))
    for k in set(keys):
        test = np.array([kk == k for kk in keys])
        train = ~test
        if buffer_km > 0:
            lo = np.array(k) * block_km - buffer_km
            hi = (np.array(k) + 1) * block_km + buffer_km
            near = np.all((P >= lo) & (P < hi), axis=1)
            train &= ~near
        f = fit(P[train], D[train])
        res[test] = np.hypot(*(D[test] - f(P[test])).T)
    return round(float(np.median(res)), 2), round(float(np.percentile(res, 90)), 2)


def draw(P, D, path):
    W = 900
    x0, y0 = P.min(axis=0) - 1
    x1, y1 = P.max(axis=0) + 1
    s = W / (x1 - x0)
    H = int((y1 - y0) * s)
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    for (x, y), (e, n) in zip(P, D):
        px, py = (x - x0) * s, (y1 - y) * s
        mag = math.hypot(e, n)
        col = (20, 110, 200) if mag < 10 else (230, 140, 20) if mag < 20 else (200, 30, 30)
        d.line([(px, py), (px + e * s / 1000 * 40, py - n * s / 1000 * 40)], fill=col, width=1)
        d.ellipse([px - 1, py - 1, px + 1, py + 1], fill=col)
    d.text((6, 6), "residual after affine correction, x40. blue <10 m, orange <20 m, red >=20 m",
           fill="black")
    im.save(path)


def main():
    os.makedirs(OUT, exist_ok=True)
    field, P, D = load()
    print(f"{len(P)} trusted windows (field built with {field.get('built_with')})")

    ac = autocorrelation(P, D)
    print("residual autocorrelation vs separation:")
    for a in ac:
        print(f"  {a['from_km']:>4}-{a['to_km']:<4} km  r = {a['correlation']:+.3f}  ({a['pairs']} pairs)")

    models = {"none": model_none()}
    for sm in (10, 100, 1000, 5000):
        models[f"tps_smooth_{sm}"] = model_tps(sm)
    for sc in (3, 5, 8):
        models[f"gauss_{sc}km"] = model_gauss(sc)

    rows = {}
    for name, fit in models.items():
        r5 = cross_validate(P, D, fit, 5.0)
        r3 = cross_validate(P, D, fit, 3.0)
        rb = cross_validate(P, D, fit, 5.0, buffer_km=2.0)
        rows[name] = {"block5km_median_m": r5[0], "block5km_p90_m": r5[1],
                      "block3km_median_m": r3[0], "block3km_p90_m": r3[1],
                      "block5km_buffer2km_median_m": rb[0], "block5km_buffer2km_p90_m": rb[1]}
        print(f"  {name:16s} 5km {r5[0]:5.2f}/{r5[1]:5.2f}   3km {r3[0]:5.2f}/{r3[1]:5.2f}   "
              f"5km+2km buffer {rb[0]:5.2f}/{rb[1]:5.2f}")

    base = rows["none"]
    key_m, key_p = "block5km_buffer2km_median_m", "block5km_buffer2km_p90_m"
    best = min((k for k in rows if k != "none"), key=lambda k: rows[k][key_m])
    b = rows[best]
    gain_med = base[key_m] - b[key_m]
    gain_p90 = base[key_p] - b[key_p]
    recommend = gain_med >= 1.5 and gain_p90 > 0
    verdict = {
        "best_model": best,
        "gain_median_m": round(gain_med, 2), "gain_p90_m": round(gain_p90, 2),
        "recommend_warp": bool(recommend),
        "summary": ("local warp recommended" if recommend else
                    "keep the affine: no smooth field predicts held-out residuals "
                    "well enough to clear the 1.5 m median bar"),
    }
    draw(P, D, os.path.join(OUT, "residuals.png"))
    report = {"input": os.path.relpath(FIELD, ROOT), "built_with": field.get("built_with"),
              "windows": int(len(P)),
              "residual_mean_m": [round(float(v), 2) for v in D.mean(axis=0)],
              "autocorrelation": ac, "models": rows, "verdict": verdict,
              "rule": "recommend only if held-out median (5 km blocks, training windows within 2 km of the block dropped) improves >= 1.5 m and p90 improves"}
    with open(os.path.join(OUT, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    print(f"best {best}: median gain {gain_med:.2f} m, p90 gain {gain_p90:.2f} m -> "
          f"{'RECOMMEND warp' if recommend else 'keep affine'}")


if __name__ == "__main__":
    main()
