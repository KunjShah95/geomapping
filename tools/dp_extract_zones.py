"""Turn the plan's land-use zones into real polygons.

The zones are read from the rendered sheet (the sheets draw them as hatched
areas, so the drawing objects are not extractable), and the resulting colour
areas are traced into polygons and placed on the Earth.

This is the land-use layer as vector data: the client can query it, style
it, or load it into a GIS, which a raster plan cannot be.

Output: data/dp2021/zones.geojson         every zone polygon
        data/dp2021/zones/<class>.geojson  one file per legend class
        data/dp2021/zone_stats.json        area and count per class
"""
import argparse
import json
import math
import os
import sys
from collections import Counter, defaultdict

import cv2
import fitz
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dp_georef as G  # noqa: E402
import dp_zone_masks as ZM  # noqa: E402

ROOT = G.ROOT
OUT = os.path.join(ROOT, "data", "dp2021")


def trace(mask, min_area_px=40, simplify=2.0):
    """The outlines of one colour area, as rings in mask pixel coordinates."""
    m = mask.astype(np.uint8)
    contours, hier = cv2.findContours(m, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if not len(contours):
        return []
    hier = hier[0]
    rings = []
    for i, c in enumerate(contours):
        if hier[i][3] != -1:          # a hole: handled with its parent
            continue
        if cv2.contourArea(c) < min_area_px:
            continue
        outer = c.reshape(-1, 2)
        holes = []
        child = hier[i][2]
        while child != -1:
            if cv2.contourArea(contours[child]) >= min_area_px:
                holes.append(contours[child].reshape(-1, 2))
            child = hier[child][0]
        rings.append((outer, holes))
    return rings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--min-area-px", type=float, default=60.0)
    ap.add_argument("--simplify", type=float, default=2.0)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    classes = ZM.legend_classes()
    names = [c["label"] for c in classes]
    hexes = [c["hex"] for c in classes]
    zone_dir = os.path.join(ROOT, "work", "newdp", "zones")
    blocks, _ = G.build_blocks()

    by_class = defaultdict(list)
    stats = {n: {"polygons": 0, "area_km2": 0.0, "holes": 0} for n in names}

    for quad, block in blocks.items():
        npz = np.load(os.path.join(zone_dir, f"{quad}.npz"))
        lab = npz["labels"]
        zoom = float(npz["zoom"][0])
        crop = fitz.open(block.path)[0].cropbox
        latm = (block.lat_min + block.lat_max) / 2
        kx = 111320.0 * math.cos(math.radians(latm))
        ky = 110990.0
        px_m2 = ZM.M_PER_PX * ZM.M_PER_PX
        print(f"[{quad}] tracing the zones ...", flush=True)
        for i, nm in enumerate(names):
            mask = (lab == i)
            if not mask.any():
                continue
            rings = trace(mask, args.min_area_px)
            if not rings:
                continue
            feats = []
            for outer, holes in rings:
                polys = []
                for ring in [outer] + holes:
                    if len(ring) < 3:
                        continue
                    # ring pixel -> PDF user space -> ground
                    ux = ring[:, 0] / zoom + crop.x0
                    uy = crop.y1 - ring[:, 1] / zoom
                    mx = block.e0 + block.s * (ux - block.llx)
                    my = block.n0 + block.s * (uy - block.ury)
                    lon, lat = G.FWD.transform(mx, my)
                    polys.append([[round(float(a), 7), round(float(b), 7)]
                                  for a, b in zip(lon, lat)])
                if not polys:
                    continue
                if len(polys) == 1:
                    geom = {"type": "Polygon", "coordinates": [polys[0]]}
                else:
                    geom = {"type": "MultiPolygon",
                            "coordinates": [[p] for p in polys]}
                area = cv2.contourArea(outer) * px_m2 / 1e6
                feats.append({
                    "type": "Feature", "geometry": geom,
                    "properties": {"zone": nm, "block": quad,
                                   "area_km2": round(area, 3)},
                })
                stats[nm]["polygons"] += 1
                stats[nm]["area_km2"] += area
                stats[nm]["holes"] += max(0, len(polys) - 1)
            if feats:
                by_class[nm].extend(feats)
            print(f"    {nm:46s} {len(feats):6d} polygons", flush=True)
        del lab

    out = {
        "type": "FeatureCollection",
        "name": "auda_dp2021_zones",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3/CRS84"}},
        "features": [f for nm in sorted(by_class) for f in by_class[nm]],
    }
    p = os.path.join(args.out, "zones.geojson")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"\nwrote {p}  ({len(out['features'])} features, {os.path.getsize(p)/1e6:.1f} MB)")

    zdir = os.path.join(args.out, "zones")
    os.makedirs(zdir, exist_ok=True)
    for nm, items in by_class.items():
        slug = "_".join("".join(ch if ch.isalnum() else "_" for ch in nm).split("_"))
        p = os.path.join(zdir, slug + ".geojson")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"type": "FeatureCollection", "features": items},
                      f, separators=(",", ":"))
    for nm in sorted(stats):
        s = stats[nm]
        print(f"   {nm:46s} {s['polygons']:6d} polygons  {s['area_km2']:9.1f} km2")
    p = os.path.join(args.out, "zone_stats.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"classes": {n: {"polygons": s["polygons"],
                                   "area_km2": round(s["area_km2"], 1),
                                   "holes": s["holes"]}
                              for n, s in stats.items() if s["polygons"]},
                   "total_polygons": sum(s["polygons"] for s in stats.values()),
                   "total_area_km2": round(sum(s["area_km2"] for s in stats.values()), 1)},
                  f, indent=2)
    print(f"wrote {p}")


if __name__ == "__main__":
    main()
