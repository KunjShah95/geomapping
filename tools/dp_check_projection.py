"""Check whether the GeoPDF's ground quad is exactly the projection the sheet was drawn in.

The /Measure carries a WKT for a Transverse Mercator (CM 71.5, k0 0.999738, FE/FN 400000).
The sheet was drawn in that projected space, so the correct page->ground model is

    page  --affine-->  TM metres  --exact projection-->  lat/lon

and NOT a bilinear over the lon/lat quad (which only approximates it). This script
converts the quad's four corners into TM metres, fits the page<->metres affine, and
reports the residual. If the residual is sub-millimetre, the affine+projection model
is the true one and the map can be placed to survey accuracy.
"""
import json
import math
import os
import sys

import numpy as np
from pyproj import CRS, Transformer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dp_georef import load_views, build_index  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

WKT = ('PROJCS["WGS_1984_Transverse_Mercator",GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
       'SPHEROID["WGS_1984",6378137.0,298.257223563]],PRIMEM["Greenwich",0.0],'
       'UNIT["Degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],'
       'PARAMETER["False_Easting",400000.0],PARAMETER["False_Northing",400000.0],'
       'PARAMETER["Central_Meridian",71.5],PARAMETER["Scale_Factor",0.999738],'
       'PARAMETER["Latitude_Of_Origin",22.5],UNIT["Meter",1.0]]')

crs = CRS.from_wkt(WKT)
print("CRS:", crs.name)
print("axis order:", crs.axis_info)
fwd = Transformer.from_crs(crs, 4326, always_xy=True)      # metres -> lon,lat
inv = Transformer.from_crs(4326, crs, always_xy=True)      # lon,lat -> metres

views = load_views()
idx = build_index(views)

for rel in sorted(idx):
    if "FULL DP" not in rel:
        continue
    t = idx[rel][0]["transform"]
    print("=" * 90)
    print(rel)
    # page corners of the viewport rect
    pc = [(t.llx, t.ury), (t.urx, t.ury), (t.urx, t.lly), (t.llx, t.lly)]
    # matching ground corners: /Bounds maps SW,NW,NE,SE to (0,1),(0,0),(1,0),(1,1)
    # and the page rect's own corners are (llx,lly)=bottom-left ... work it out:
    #   u = (x-llx)/(urx-llx), v = (lly-y)/(lly-ury)
    #   SW = (u,v)=(0,1) -> x=llx, y=lly  (bottom-left of page space)
    #   NW = (0,0)     -> x=llx, y=ury
    #   NE = (1,0)     -> x=urx, y=ury
    #   SE = (1,1)     -> x=urx, y=lly
    gp = t.corners  # SW, NW, NE, SE as (lat, lon)
    page_pts = np.array([(t.llx, t.lly), (t.llx, t.ury), (t.urx, t.ury), (t.urx, t.lly)], float)
    ground = [(g[1], g[0]) for g in gp]  # (lon, lat)
    metres = np.array([inv.transform(lon, lat) for lon, lat in ground], float)

    print("  corners (lon,lat) -> TM metres:")
    for (lon, lat), m in zip(ground, metres):
        print(f"     {lon:.6f} {lat:.6f}  ->  {m[0]:10.3f} {m[1]:12.3f}")

    # the projected quad should be an exact rectangle
    e = metres[1] - metres[0]
    f = metres[2] - metres[3]
    print(f"  west edge length {np.linalg.norm(e):.4f} m   east edge {np.linalg.norm(f):.4f} m")
    e2 = metres[3] - metres[0]
    print(f"  south edge {np.linalg.norm(e2):.4f} m   north edge {np.linalg.norm(metres[2]-metres[1]):.4f} m")
    print(f"  corner angles (deg from north): "
          f"{math.degrees(math.atan2(e[0], e[1])):.6f}  "
          f"{math.degrees(math.atan2(f[0], f[1])):.6f}")

    # fit page -> metres affine by least squares, then look at the residual
    A = np.hstack([page_pts, np.ones((4, 1))])
    sol, *_ = np.linalg.lstsq(A, metres, rcond=None)
    pred = A @ sol
    res = pred - metres
    print("  affine fit page->metres residual (m):", np.round(res, 6).tolist())
    print(f"  max residual {np.abs(res).max():.6f} m")

    # and the inverse: metres -> page
    sol_i, *_ = np.linalg.lstsq(np.hstack([metres, np.ones((4, 1))]), page_pts, rcond=None)
    resi = np.hstack([metres, np.ones((4, 1))]) @ sol_i - page_pts
    print(f"  inverse residual (pt): max {np.abs(resi).max():.6f}")

    # what does the bilinear-in-lon/lat model cost against the exact projection?
    print("\n  bilinear(lon,lat) model vs exact TM projection, sampled over the block:")
    worst = 0.0
    for i in range(7):
        for j in range(7):
            u = i / 6.0
            v = j / 6.0
            x, y = t.uv_to_page(u, v)
            lon_bi, lat_bi = t.page_to_ground(x, y)
            # exact: page -> metres (affine) -> lon/lat
            A2 = np.array([x, y, 1.0])
            mx, my = A2 @ sol
            lon_ex, lat_ex = fwd.transform(mx, my)
            m_lat = (lat_bi - lat_ex) * 110990.0
            m_lon = (lon_bi - lon_ex) * 111319.5 * math.cos(math.radians(lat_ex))
            d = math.hypot(m_lat, m_lon)
            worst = max(worst, d)
    print(f"     worst divergence: {worst:.2f} m")
