"""Write the map's configuration: extent, resolution, accuracy, manifest.

This is the single source of truth for where the map is. The viewer reads it
at startup, and the client can paste the "app_config" block straight into
their own application - the shape their city config uses for the old layer.
"""
import argparse
import json
import math
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dp_georef as G  # noqa: E402

ROOT = G.ROOT
OUT = os.path.join(ROOT, "data", "dp2021")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--tiles", default=os.path.join(ROOT, "tiles_dp2021"))
    ap.add_argument("--zmin", type=int, default=11)
    ap.add_argument("--zmax", type=int, default=17)
    ap.add_argument("--tile-url", default="/tiles_dp2021/{z}/{x}/{y}.webp")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    blocks, grid = G.build_blocks()
    lon_min = min(b.lon_min for b in blocks.values())
    lon_max = max(b.lon_max for b in blocks.values())
    lat_min = min(b.lat_min for b in blocks.values())
    lat_max = max(b.lat_max for b in blocks.values())

    s = grid["scale_m_per_pt"]
    latm = (lat_min + lat_max) / 2
    mpp = lambda z: 156543.03392 * math.cos(math.radians(latm)) / (2 ** z)
    # the zoom at which one PDF point is one pixel: the drawing's own limit
    native_zoom = round(math.log2(156543.03392 * math.cos(math.radians(latm)) / s), 2)
    # the plan's type is 5.8 pt on a 2.8224 m/pt sheet, i.e. 16.4 m tall; a
    # letter needs about ten pixels to be read, so this is where the numbers
    # become legible
    type_height_m = 5.8 * s
    legible_zoom = round(math.log2(156543.03392 * math.cos(math.radians(latm))
                                    / (type_height_m / 10)), 2)

    counts = Counter()
    total_bytes = 0
    for z in range(args.zmin, args.zmax + 1):
        d = os.path.join(args.tiles, str(z))
        if not os.path.isdir(d):
            continue
        for txname in os.listdir(d):
            td = os.path.join(d, txname)
            if not os.path.isdir(td):
                continue
            for fn in os.listdir(td):
                counts[z] += 1
                total_bytes += os.path.getsize(os.path.join(td, fn))

    # where the plan lands on the ground, measured against OSM roads after the
    # ground correction (tools/dp_osm_offsets.py run on the rebuilt tiles)
    # the measurement of THIS build: the warped build is measured into
    # georef_osm_warp/, the affine-only one into georef_osm/
    field_path = os.path.join(ROOT, "work", "georef_osm_warp" if G.WARP else "georef_osm", "field.json")
    field = None
    if os.path.exists(field_path):
        with open(field_path, encoding="utf-8") as f:
            field = json.load(f)
    corr = G.CORRECTION

    accuracy = {
        "method": "the four part sheets are a regular 2x2 grid in the projected "
                  "plane, fitted to all sixteen of their published corner values; "
                  "a ground correction fitted to OSM roads then moves the plan "
                  "onto the ground",
        "sheet_fit_rms_m": round(max(grid["east"]["rms_m"], grid["north"]["rms_m"]), 2),
        "sheet_fit_note": "How well the four sheets agree with their own printed "
                          "corners. It is internal consistency only and says "
                          "nothing about where the plan sits on the ground: the "
                          "printed corners themselves place the plan ~85 m east "
                          "of the ground.",
        "ground_correction": None,
        "plan_to_ground": None,
        "plan_to_ground_note": "Residual offset between the plan's road corridors "
                               "and OSM roads. It includes OSM's own error and "
                               "places where the plan's drawing differs from what "
                               "was built, so it is an upper bound on the "
                               "placement error, not the placement error itself.",
        "projection": "WGS_1984_Transverse_Mercator, CM 71.5, k0 0.999738, "
                      "FE/FN 400000 (the CRS published in the source files)",
        "scale_m_per_point": round(s, 6),
    }
    if corr:
        accuracy["ground_correction"] = {"id": corr["id"], **corr["summary"],
                                         "fit": corr["fit"]}
    warp = G.WARP
    if warp:
        accuracy["ground_warp"] = {"id": warp["id"], "sigma_m": warp["sigma_m"],
                                   "max_shift_m": warp["max_shift_m"], "fit": warp["fit"]}
    if field and corr and field.get("built_with") == G.BUILD_ID:
        none = field["models"]["none"]
        accuracy["plan_to_ground"] = {
            "offset_median_m": none["cv_median_m"],
            "offset_p90_m": none["cv_p90_m"],
            "n_windows": field["used_after_outliers"],
            "window_m": field["window_m"],
            "reference": "OSM roads",
        }
        if G.WARP:
            # the warp was fitted to these same windows, so their residual is
            # in-sample; the honest figure is the warp's held-out prediction
            accuracy["plan_to_ground"].update({
                "in_sample_median_m": none["cv_median_m"],
                "in_sample_p90_m": none["cv_p90_m"],
                "offset_median_m": G.WARP["fit"]["held_out_median_m"],
                "offset_p90_m": G.WARP["fit"]["held_out_p90_m"],
                "note": "offset_* is the held-out prediction (5 km blocks, 2 km buffer); "
                        "in_sample_* is measured on the windows the warp was fitted to",
            })

    bounds = {
        "id": "auda-srdp-2021",
        "title": "AUDA Second Revised Development Plan 2021",
        "source": "AUDA DP 2021 (Second Revised) - four georeferenced vector "
                  "GeoPDF parts supplied by the client",
        "crs": "EPSG:3857 (served) / WGS_1984_Transverse_Mercator (source)",
        "lon_min": round(lon_min, 7),
        "lon_max": round(lon_max, 7),
        "lat_min": round(lat_min, 7),
        "lat_max": round(lat_max, 7),
        "raster_source": {
            "type": "raster",
            "tiles": [args.tile_url],
            "tileSize": 256,
            "minzoom": args.zmin,
            "maxzoom": args.zmax,
            "bounds": [round(lon_min, 7), round(lat_min, 7),
                       round(lon_max, 7), round(lat_max, 7)],
        },
        "map_view": {
            "fitBounds": [[round(lon_min, 7), round(lat_min, 7)],
                          [round(lon_max, 7), round(lat_max, 7)]],
            "minZoom": args.zmin,
            "maxZoom": args.zmax + 1,
        },
        "resolution": {
            "m_per_px": round(mpp(args.zmax), 3),
            "m_per_px_at_z16": round(mpp(16), 3),
            "native_zoom": native_zoom,
            "text_legible_from_zoom": legible_zoom,
            "type_height_m": round(type_height_m, 1),
            "width_km": round((lon_max - lon_min) * 111320.0 * math.cos(math.radians(latm)) / 1000, 2),
            "height_km": round((lat_max - lat_min) * 110990.0 / 1000, 2),
        },
        "accuracy": accuracy,
        # the correction itself, shown in the viewer's accuracy note; the
        # warp grid is still published for the tools
        "ground_correction": None if not corr else {
            "id": corr["id"], "centre_m": corr["centre_m"], "M": corr["M"], "t_m": corr["t_m"],
            "model": corr["model"],
            "warp": None if not warp else {"id": warp["id"],
                                           "url": "/data/dp2021/ground_warp.json"}},
        "tiles": {"count": sum(counts.values()), "bytes": total_bytes,
                  "per_zoom": {str(z): counts[z] for z in sorted(counts)}},
        "app_config": {
            "shortTitle": "AUDA Second Revised Development Plan 2021",
            "sourceName": "AUDA",
            "MapTilerUrl": args.tile_url,
            "BottomLeft": [round(lat_min, 7), round(lon_min, 7)],
            "TopRight": [round(lat_max, 7), round(lon_max, 7)],
            "ZoomBounds": [args.zmin, args.zmax],
            "CityName": "Ahmedabad",
        },
    }

    plots_path = os.path.join(args.out, "plots.geojson")
    plot_count = None
    if os.path.exists(plots_path):
        with open(plots_path, encoding="utf-8") as f:
            g = json.load(f)
        plot_count = len(g["features"])
        bounds["plots"] = {"count": plot_count}

    if warp:
        g = warp["grid"]
        k = max(1, int(round(500.0 / g["step"])))
        gw = {"id": warp["id"], "affine": warp["affine"],
              "grid": {"x0": g["x0"], "y0": g["y0"], "step": g["step"] * k,
                       "nx": len(range(0, g["nx"], k)), "ny": len(range(0, g["ny"], k))},
              "dE": [[round(v, 2) for v in row[::k]] for row in warp["dE"][::k]],
              "dN": [[round(v, 2) for v in row[::k]] for row in warp["dN"][::k]],
              "model": warp["model"]}
        with open(os.path.join(args.out, "ground_warp.json"), "w", encoding="utf-8") as f:
            json.dump(gw, f, separators=(",", ":"))
        print(f"wrote ground_warp.json ({gw['grid']['nx']}x{gw['grid']['ny']} at {gw['grid']['step']:g} m)")

    p = os.path.join(args.out, "bounds.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(bounds, f, indent=2)
    print(f"wrote {p}")

    p = os.path.join(args.out, "accuracy.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(accuracy, f, indent=2)
    print(f"wrote {p}")

    manifest = {
        "id": bounds["id"],
        "generated_from": bounds["source"],
        "tiles": bounds["tiles"],
        "layers": {
            "plan": {"kind": "raster", "url": args.tile_url,
                     "zmin": args.zmin, "zmax": args.zmax},
            "plots": {"kind": "geojson", "url": "/data/dp2021/plots.geojson",
                      "count": plot_count,
                      "buckets": "/data/dp2021/plots/15/{x}/{y}.geojson"},
            "labels": {"kind": "geojson", "url": "/data/dp2021/labels.geojson"},
            "legend": {"kind": "json", "url": "/data/dp2021/legend.json"},
        },
    }
    p = os.path.join(args.out, "manifest.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    print(f"wrote {p}")

    print(f"\nextent   lon {lon_min:.6f}..{lon_max:.6f}  lat {lat_min:.6f}..{lat_max:.6f}")
    print(f"scale    {s:.4f} m/point    native zoom {native_zoom}"
          f"    numbers legible from z{legible_zoom}")
    print(f"tiles    {sum(counts.values())}  ({total_bytes/1e6:.1f} MB)")
    for z in sorted(counts):
        print(f"   z{z}: {counts[z]}")


if __name__ == "__main__":
    main()
