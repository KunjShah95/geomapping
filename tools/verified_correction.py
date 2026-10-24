"""Fit a correction to the measured error field, apply it, and VERIFY by
re-measuring.

THE QUESTION THIS ANSWERS
-------------------------
The README claims the sheet has a 0.5% anisotropy that accumulates to
~365 m at the north/south edges. If that were true, the error field would
show a clear systematic pattern: the north quadrant and the south quadrant
would be displaced in OPPOSITE directions, by amounts that grow with
distance from the centre.

The measurement (tools/measure_hires_fft.py) says otherwise. The median
offsets per quadrant are all within a couple of metres of zero, and the
typical |error| is roughly the same in all four quadrants. That is the
signature of NOISE - the plan's road network drawn from an older base map,
compared against today's satellite - not of a correctable transform error.

So this script does the honest thing: it fits whatever correction the
data supports, applies it, re-measures, and reports whether the error
actually went down. If the correction is noise, the verification will say
so, and the correction will be discarded rather than shipped.

Output: work/verified_correction.json
"""
import argparse, json, math, os, subprocess, sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "work")


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--georef", default=os.path.join(WORK, "georef.json"))
    ap.add_argument("--error", default=os.path.join(WORK, "hires_error.json"))
    ap.add_argument("--out", default=os.path.join(WORK, "verified_correction.json"))
    ap.add_argument("--stride", type=int, default=700)
    ap.add_argument("--patch", type=int, default=200)
    ap.add_argument("--z", type=int, default=16)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    with open(a.georef, encoding="utf-8") as f:
        g = json.load(f)
    with open(a.error, encoding="utf-8") as f:
        err = json.load(f)

    A, Ai = affine_matrices(g)
    lat_c = (g["lat_min"] + g["lat_max"]) / 2
    m_lon = 111320.0 * math.cos(math.radians(lat_c))
    m_merc = 110574.0 * math.cos(math.radians(lat_c))

    res = err["results"]
    dlon = np.array([r["dlon_m"] for r in res])
    dmerc = np.array([r["dmerc_m"] for r in res])
    P = np.array([[r["px"], r["py"]] for r in res], float)

    # Convert the measured offsets to degrees so the fit is in map units.
    dlon_deg = dlon / m_lon
    dmerc_deg = dmerc / m_merc

    # THE KEY DIAGNOSTIC: is there a systematic pattern at all?
    #
    # A scale error produces offsets proportional to the distance from the
    # sheet centre, and with the SAME sign on opposite sides of the centre
    # only if the error is a translation. So: fit the model
    #
    #   delta = c + K @ [px - cx, py - cy]
    #
    # and look at how much of the variance K explains. If K is near zero,
    # there is no transform error to correct and the residual is noise.
    cx, cy = 27200 / 2, 25600 / 2
    Q = np.column_stack([np.ones(len(P)), P[:, 0] - cx, P[:, 1] - cy])
    D = np.column_stack([dlon_deg, dmerc_deg])
    M, *_ = np.linalg.lstsq(Q, D, rcond=None)
    pred = Q @ M
    resid = D - pred

    var_total = float((D ** 2).sum())
    var_expl = float(((D - resid) ** 2).sum())
    print("=== IS THERE A SYSTEMATIC ERROR TO CORRECT? ===")
    print(f"  total variance of the measured offsets: {var_total:.3e} deg^2")
    print(f"  explained by a linear field:            {var_expl:.3e} deg^2 "
          f"({100*var_expl/var_total:.1f}%)")
    print(f"  dlon : const {M[0][0]:+.3e}  x {M[1][0]:+.3e}  y {M[2][0]:+.3e}")
    print(f"  dmerc: const {M[0][1]:+.3e}  x {M[1][1]:+.3e}  y {M[2][1]:+.3e}")

    # the same, but as the implied scale change over the whole sheet
    print("\n  implied displacement at the sheet's edges:")
    for lbl, row, m_per_deg, span in (("lon ", M[:, 0], m_lon, 27200),
                                     ("merc", M[:, 1], m_merc, 25600)):
        ex = row[1] * m_per_deg * span
        ey = row[2] * m_per_deg * span
        print(f"    {lbl}: {ex*1000:+8.1f} m across the width, "
              f"{ey*1000:+8.1f} m down the height")
        if ex:
            print(f"          => relative scale change {ex/span*100:+.4f}%")
        if ey:
            print(f"          => relative scale change {ey/span*100:+.4f}%")

    # build the corrected affine
    A_corr = A.copy()
    A_corr[0, 0] += M[1][0]
    A_corr[0, 1] += M[2][0]
    A_corr[0, 2] += M[0][0]
    A_corr[1, 0] += M[1][1]
    A_corr[1, 1] += M[2][1]
    A_corr[1, 2] += M[0][1]

    corners = np.array([[0, 0], [27200, 0], [27200, 25600], [0, 25600]], float)
    geo = np.column_stack([np.ones(4), corners]) @ A_corr
    lon_min, lon_max = geo[:, 0].min(), geo[:, 0].max()
    my_min, my_max = geo[:, 1].min(), geo[:, 1].max()
    lat_min, lat_max = inv_merc_y(my_min), inv_merc_y(my_max)

    g2 = dict(g)
    g2["affine"] = dict(
        A=[[A_corr[0, 0], A_corr[0, 1], A_corr[0, 2]],
           [A_corr[1, 0], A_corr[1, 1], A_corr[1, 2]]],
        Ai=[[0, 0, 0], [0, 0, 0]],
        rmse_px=0, rmse_m=0,
        rotation_deg=0, shear_deg=0,
        m_per_px_x=0, m_per_px_y=0,
        n_points=err["n"],
        source="verified_correction.py")
    Ai2 = np.linalg.inv(A_corr)
    g2["affine"]["Ai"] = [[Ai2[0, 0], Ai2[0, 1], Ai2[0, 2]],
                          [Ai2[1, 0], Ai2[1, 1], Ai2[1, 2]]]
    g2["lon_min"], g2["lon_max"] = lon_min, lon_max
    g2["lat_min"], g2["lat_max"] = lat_min, lat_max
    g2["merc_top"] = my_max
    g2["SX"] = 27200 / (lon_max - lon_min)
    g2["SY"] = 25600 / (my_max - my_min)
    g2["anisotropy"] = g2["SY"] / g2["SX"]
    g2["m_per_px"] = (lon_max - lon_min) * 111.32 * math.cos(math.radians(lat_c))
    g2["native_zoom"] = round(math.log2(27200 / 256 * 360 / (lon_max - lon_min)), 2)
    g2["ground_width_km"] = (lon_max - lon_min) * 111.32 * math.cos(math.radians(lat_c))

    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(g2, f, indent=2)
    print(f"\nwrote {a.out}")

    if a.apply:
        tmp = os.path.join(WORK, "georef_verify2.json")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(g2, f, indent=2)
        print("\nre-measuring with the corrected georeference ...")
        r = subprocess.run([sys.executable,
                            os.path.join(ROOT, "tools", "measure_hires_fft.py"),
                            "--georef", tmp,
                            "--out", os.path.join(WORK, "hires_error_after.json"),
                            "--stride", str(a.stride), "--patch", str(a.patch),
                            "--z", str(a.z)],
                           capture_output=True, text=True)
        print(r.stdout[-2200:])
        if r.returncode != 0:
            print(r.stderr[-1500:])
            raise SystemExit("verification measurement failed")
        with open(os.path.join(WORK, "hires_error_after.json", encoding="utf-8"), encoding="utf-8") as f:
            after = json.load(f)
        before_m = err["shift_median_m"]
        after_m = after["shift_median_m"]
        print("\n=== VERIFICATION ===")
        print(f"  median |error| before: {before_m:6.1f} m")
        print(f"  median |error| after:  {after_m:6.1f} m")
        if after_m >= before_m:
            print("\n  REFUSING: the correction did not reduce the measured error.")
            print("  The residual is noise, not a transform error. Not applying it.")
            raise SystemExit(1)
        print(f"  improvement: {100*(before_m-after_m)/before_m:.0f}%")
        with open(a.georef, "w", encoding="utf-8") as f:
            json.dump(g2, f, indent=2)
        print(f"\napplied to {a.georef}")


if __name__ == "__main__":
    main()
