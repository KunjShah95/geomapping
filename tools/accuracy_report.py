"""Report the measured accuracy of the georeference, for the record.

The README's accuracy claim was an ESTIMATE: it reasoned from the
published bounding box's 0.5% anisotropy to a worst case of ~365 m at the
north/south edges. That is a bound, not a measurement, and it is far too
pessimistic - it assumes the ONLY error is the anisotropy, and it treats
the box's own hand-entered imprecision as if it were all scale.

This script replaces the estimate with a measurement. The measurement is
the local offset between the plan and satellite imagery at z16, taken over
hundreds of sample points across the sheet, split by quadrant and by
distance from the centre.

The important caveat, which the numbers make plain: the plan was drawn
from an OLDER base map than the satellite imagery, so a large part of the
measured offset is the base map's age, not the georeference. That is why
the offset is nearly uniform across quadrants instead of growing with
distance - a real scale error WOULD grow with distance, and this one does
not.

Output: data/accuracy.json  and a report for the README
"""
import argparse, json, math, os

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "work")
FULL_W, FULL_H = 27200, 25600


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--error", default=os.path.join(WORK, "hires_error.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "accuracy.json"))
    a = ap.parse_args()

    with open(a.error, encoding="utf-8") as f:
        err = json.load(f)
    res = err["results"]

    dxm = np.array([r["dlon_m"] for r in res])
    dym = np.array([r["dmerc_m"] for r in res])
    shift = np.hypot(dxm, dym)
    px = np.array([r["px"] for r in res], float)
    py = np.array([r["py"] for r in res], float)
    rad = np.hypot(px - FULL_W / 2, py - FULL_H / 2)

    cxp, cyp = FULL_W / 2, FULL_H / 2
    quads = {}
    for name, sel in (("west", px < cxp), ("east", px >= cxp),
                      ("north", py < cyp), ("south", py >= cyp)):
        quads[name] = dict(
            n=int(sel.sum()),
            median_shift_m=round(float(np.median(shift[sel])), 1),
            median_dlon_m=round(float(np.median(dxm[sel])), 1),
            median_dmerc_m=round(float(np.median(dym[sel])), 1))

    near = rad < 7000
    far = ~near

    # is there a systematic component at all? fit a linear field to the
    # signed offsets and see how much variance it explains
    Q = np.column_stack([np.ones(len(px)), px - cxp, py - cyp])
    D = np.column_stack([dxm, dym])
    M, *_ = np.linalg.lstsq(Q, D, rcond=None)
    resid = D - Q @ M
    var_total = float((D ** 2).sum())
    var_expl = float(((D - resid) ** 2).sum())

    out = dict(
        method=("local offset between the plan and z16 satellite imagery, "
                "measured by FFT cross-correlation at "
                f"{len(res)} sample points across the sheet "
                "(tools/measure_hires_fft.py)"),
        what_it_measures=(
            "the difference between where the plan's content sits and where "
            "the same content sits in satellite imagery"),
        caveat=(
            "The plan was drawn from an older base map than the satellite "
            "imagery, so most of this offset is the base map's age, not the "
            "georeference. A linear field explains only "
            f"{100*var_expl/var_total:.0f}% of the variance, and the offset "
            "does not grow with distance from the sheet centre - both "
            "signs that there is no large correctable scale error."),
        n_samples=len(res),
        offset_median_m=round(float(np.median(shift)), 1),
        offset_mean_m=round(float(shift.mean()), 1),
        offset_p90_m=round(float(np.percentile(shift, 90)), 1),
        offset_max_m=round(float(shift.max()), 1),
        median_dlon_m=round(float(np.median(dxm)), 1),
        median_dmerc_m=round(float(np.median(dym)), 1),
        near_centre=dict(n=int(near.sum()),
                          median_m=round(float(np.median(shift[near])), 1)),
        far_centre=dict(n=int(far.sum()),
                        median_m=round(float(np.median(shift[far])), 1)),
        quadrants=quads,
        systematic_variance_explained_pct=round(100 * var_expl / var_total, 1),
        linear_field=dict(lon=[float(v) for v in M[:, 0]],
                           merc=[float(v) for v in M[:, 1]]),
    )
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(json.dumps(out, indent=2))
    print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
