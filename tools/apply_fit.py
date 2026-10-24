"""Write the fitted georeference into work/georef.json, in a form the rest of
the pipeline can consume.

THE PROBLEM
-----------
Every consumer in this project (tile builder, OCR, GeoJSON maker, viewer)
assumes a north-up model:

    lon = lon_min + px / SX
    merc = merc_top - py / SY

That is 4 parameters: an origin and two scales, and it cannot represent
rotation or shear. It also forces SX and SY to be whatever the published
bounding box implies, which is where the 0.5% anisotropy comes from.

THE FIX
-------
georef.json gains an optional "affine" block:

    "affine": { "A": [[...],[...]], "Ai": [[...],[...]], ... }

where A maps source pixels -> (lon, mercator_y) and Ai maps (lon, mercator_y)
-> source pixels. When it is present, consumers use it and ignore the north-up
fields. When it is absent, everything behaves exactly as before.

The north-up fields are still written (fitted origin and scales, projected
from the affine) so that any consumer that has NOT been updated still gets a
better answer than H0 rather than a wrong one.
"""
import argparse, json, math, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "work")
FULL_W, FULL_H = 27200, 25600


def merc_y(lat):
    return math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def inv_merc_y(my):
    return math.degrees(2 * math.atan(math.exp(math.radians(my))) - math.pi / 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fit", default=os.path.join(WORK, "affine_fit.json"))
    ap.add_argument("--out", default=os.path.join(WORK, "georef.json"))
    ap.add_argument("--backup", action="store_true", help="keep the previous georef.json")
    a = ap.parse_args()

    with open(a.fit, encoding="utf-8") as f:
        fit = json.load(f)

    M = fit["M"]            # 3x2 in MERCATOR space: M[0] -> lon, M[1] -> merc
    Mi = fit["M_inverse"]   # 3x2: [lon, merc] -> [px, py]

    A = [[M[0][0], M[0][1], M[0][2]],
         [M[1][0], M[1][1], M[1][2]],
         [0.0, 0.0, 1.0]]
    Ai = [[Mi[0][0], Mi[0][1], Mi[0][2]],
          [Mi[1][0], Mi[1][1], Mi[1][2]],
          [0.0, 0.0, 1.0]]

    corners = [(0, 0), (FULL_W, 0), (FULL_W, FULL_H), (0, FULL_H)]
    geo = []
    for px, py in corners:
        lon = A[0][0] * px + A[0][1] * py + A[0][2]
        my = A[1][0] * px + A[1][1] * py + A[1][2]
        geo.append((lon, my))
    lon_min = min(g[0] for g in geo)
    lon_max = max(g[0] for g in geo)
    my_min = min(g[1] for g in geo)
    my_max = max(g[1] for g in geo)
    lat_min, lat_max = inv_merc_y(my_min), inv_merc_y(my_max)

    lat_c = (lat_min + lat_max) / 2
    kpd = 111.32 * math.cos(math.radians(lat_c))
    SX = FULL_W / (lon_max - lon_min)
    SY = FULL_H / (merc_y(lat_max) - merc_y(lat_min))

    old = {}
    if os.path.exists(a.out):
        with open(a.out, encoding="utf-8") as f:
            old = json.load(f)
        if a.backup:
            with open(a.out + ".h0", "w", encoding="utf-8") as f:
                json.dump(old, f, indent=2)
            print(f"kept the previous georeference as {a.out}.h0")

    g = dict(
        source_image="work/source.jpeg",
        source_width=FULL_W,
        source_height=FULL_H,
        crs="EPSG:3857",
        origin="sheet top-left",
        lon_min=lon_min, lon_max=lon_max,
        lat_min=lat_min, lat_max=lat_max,
        merc_top=merc_y(lat_max),
        SX=SX, SY=SY,
        anisotropy=SY / SX,
        # the authoritative transform
        affine=dict(
            A=A, Ai=Ai,
            rmse_px=fit["rmse_px"], rmse_m=fit["rmse_m"],
            rotation_deg=fit["rotation_deg"], shear_deg=fit.get("shear_deg", 0.0),
            m_per_px_x=fit["m_per_px_x"], m_per_px_y=fit["m_per_px_y"],
            n_points=fit["n_points"],
            h0_rmse_m=fit.get("h0_rmse_m"),
            source=fit.get("source", "affine_fit.py"),
        ),
        native_zoom=round(math.log2(FULL_W / 256 * 360 / (lon_max - lon_min)), 2),
        ground_width_km=(lon_max - lon_min) * kpd,
        m_per_px=(lon_max - lon_min) * kpd * 1000 / FULL_W,
    )
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(g, f, indent=2)

    print(f"wrote {a.out}")
    print(f"  extent lon {lon_min:.6f}..{lon_max:.6f}  lat {lat_min:.6f}..{lat_max:.6f}")
    print(f"  RMSE {fit['rmse_px']:.2f} px ({fit['rmse_m']:.1f} m) from {fit['n_points']} points")
    if old:
        print(f"  previous: lon {old['lon_min']:.6f}..{old['lon_max']:.6f} "
              f"lat {old['lat_min']:.6f}..{old['lat_max']:.6f}")


if __name__ == "__main__":
    main()
