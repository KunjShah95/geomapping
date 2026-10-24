"""Georeference for the NEW AUDA DP 2021 source (the authoritative one).

What the source actually is
---------------------------
The client supplied the Second Revised Development Plan 2021 as four
"part" PDFs (one per quadrant of the AUDA area), plus a per-village extract
and a per-book-sheet set. The four part PDFs are Esri "georeferenced PDF"
exports: each page carries a /VP viewport whose /Measure holds

  * /GPTS  - the ground quad, as (lat, lon) pairs, and
  * /GCS   - a WKT for WGS_1984_Transverse_Mercator (CM 71.5, k0 0.999738,
             FE/FN 400000), which is the projected space the sheet was drawn in.

The quad corners are given to four decimal places of a degree - about 11 m of
rounding - so the corners alone carry ~2 m of noise. What removes that noise is
the *structure* of the book: the four parts are a regular 2x2 grid of
identical sheets in the projected plane. Fitting that grid once, over all
sixteen corners, gives every sheet the same millimetre-level internal
consistency and about 1.5 m of absolute placement.

The model, therefore, is

    page points  --scale/offset-->  TM metres  --exact projection-->  lon/lat

and NOT a bilinear interpolation over the lon/lat quad. The bilinear form
loses up to 11 m against the true projection over a single sheet, which is
more than the entire error budget of the fitted model.

Nothing else in the project may hardcode an extent: this module is the single
source of truth for where every page lands on the Earth.
"""
import json
import math
import os

import numpy as np
from pyproj import CRS, Transformer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIEWS = os.path.join(ROOT, "work", "geopdf_views.json")
GRID_JSON = os.path.join(ROOT, "work", "dp_grid.json")

# The projected CRS the sheets were drawn in, as published in the files.
WKT = ('PROJCS["WGS_1984_Transverse_Mercator",GEOGCS["GCS_WGS_1984",'
       'DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],'
       'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],'
       'PROJECTION["Transverse_Mercator"],PARAMETER["False_Easting",400000.0],'
       'PARAMETER["False_Northing",400000.0],PARAMETER["Central_Meridian",71.5],'
       'PARAMETER["Scale_Factor",0.999738],PARAMETER["Latitude_Of_Origin",22.5],'
       'UNIT["Meter",1.0]]')

CRS_TM = CRS.from_wkt(WKT)
FWD_RAW = Transformer.from_crs(CRS_TM, 4326, always_xy=True)   # metres -> lon, lat
INV_RAW = Transformer.from_crs(4326, CRS_TM, always_xy=True)   # lon, lat -> metres

# ------------------------------------------------------- ground correction
# The sheets' published corners are internally consistent to ~2 m but sit
# ~85 m east of the ground, with a tilt across the book (measured against
# OSM roads and 2021 imagery; tools/dp_osm_offsets.py). The correction is an
# affine map in plan TM metres, fitted by tools/dp_fit_ground_correction.py,
# and applied inside FWD / INV so that every caller - the Block methods and
# the tools that inline page->metres - lands on the ground the same way.
# DP_NO_GROUND_CORRECTION=1 turns it off (for measuring the raw sheets).
CORRECTION_JSON = os.path.join(ROOT, "work", "dp_ground_correction.json")


def load_correction(path=CORRECTION_JSON):
    if os.environ.get("DP_NO_GROUND_CORRECTION") == "1" or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def correct(xy, c):
    """(n, 2) raw plan metres -> corrected metres."""
    ctr = np.asarray(c["centre_m"])
    return (np.asarray(xy, float) - ctr) @ np.asarray(c["M"]).T + ctr + np.asarray(c["t_m"])


def uncorrect(xy, c):
    """Inverse of correct()."""
    ctr = np.asarray(c["centre_m"])
    Mi = np.linalg.inv(np.asarray(c["M"]))
    return (np.asarray(xy, float) - ctr - np.asarray(c["t_m"])) @ Mi.T + ctr


# ------------------------------------------------------------- local warp
# What the affine leaves is spatially coherent (neighbouring 1 km windows err
# the same way, fading by ~10 km), so a smooth residual field fitted to the
# same OSM measurement removes another ~2 m (tools/dp_fit_ground_warp.py).
# It is a grid of (dE, dN) in corrected-ground TM metres, applied after the
# affine. It is only used with the affine it was fitted on top of.
# DP_NO_GROUND_WARP=1 turns it off.
WARP_JSON = os.path.join(ROOT, "work", "dp_ground_warp.json")


def load_warp(correction, path=WARP_JSON):
    if (not correction or os.environ.get("DP_NO_GROUND_WARP") == "1"
            or not os.path.exists(path)):
        return None
    with open(path, encoding="utf-8") as f:
        w = json.load(f)
    if w.get("affine") != correction["id"]:
        raise RuntimeError(f"ground warp {w.get('id')} was fitted on affine {w.get('affine')}, "
                           f"but the active affine is {correction['id']}: refit the warp "
                           f"(or set DP_NO_GROUND_WARP=1)")
    g = w["grid"]
    w["_E"] = np.asarray(w["dE"], float)
    w["_N"] = np.asarray(w["dN"], float)
    w["_g"] = (float(g["x0"]), float(g["y0"]), float(g["step"]), int(g["nx"]), int(g["ny"]))
    return w


def warp_shift(xy, w):
    """(n, 2) corrected-ground metres -> (n, 2) warp shift, bilinear; 0 off-grid."""
    x0, y0, step, nx, ny = w["_g"]
    fx = (xy[:, 0] - x0) / step
    fy = (xy[:, 1] - y0) / step
    inside = (fx >= 0) & (fy >= 0) & (fx <= nx - 1) & (fy <= ny - 1)
    ix = np.clip(np.floor(fx).astype(int), 0, nx - 2)
    iy = np.clip(np.floor(fy).astype(int), 0, ny - 2)
    tx = np.clip(fx - ix, 0, 1)
    ty = np.clip(fy - iy, 0, 1)
    out = np.zeros((len(xy), 2))
    for k, A in enumerate((w["_E"], w["_N"])):
        v = (A[iy, ix] * (1 - tx) * (1 - ty) + A[iy, ix + 1] * tx * (1 - ty)
             + A[iy + 1, ix] * (1 - tx) * ty + A[iy + 1, ix + 1] * tx * ty)
        out[:, k] = np.where(inside, v, 0.0)
    return out


def to_ground(xy, c, w):
    """Raw plan metres -> ground metres: affine, then warp."""
    g = correct(xy, c)
    return g + warp_shift(g, w) if w else g


def from_ground(gm, c, w):
    """Inverse of to_ground. The warp varies ~0.01 m per m, so the fixed point
    g = gm - R(g) converges in a few steps."""
    gm = np.asarray(gm, float)
    g = gm
    if w:
        for _ in range(3):      # contraction ~0.01/m: 3 steps reach ~1e-5 m
            g = gm - warp_shift(g, w)
    return uncorrect(g, c)


class _Corrected:
    """Drop-in for a pyproj Transformer's .transform(x, y), with the correction."""

    def __init__(self, forward, c, w=None):
        self.forward, self.c, self.w = forward, c, w

    def transform(self, x, y):
        scalar = np.ndim(x) == 0
        xa, ya = np.atleast_1d(np.asarray(x, float)), np.atleast_1d(np.asarray(y, float))
        if self.forward:                      # plan metres -> lon, lat
            m = to_ground(np.c_[xa.ravel(), ya.ravel()], self.c, self.w)
            lon, lat = FWD_RAW.transform(m[:, 0], m[:, 1])
        else:                                 # lon, lat -> plan metres
            mx, my = INV_RAW.transform(xa.ravel(), ya.ravel())
            m = from_ground(np.c_[mx, my], self.c, self.w)
            lon, lat = m[:, 0], m[:, 1]
        lon = np.asarray(lon).reshape(xa.shape)
        lat = np.asarray(lat).reshape(xa.shape)
        return (float(lon[0]), float(lat[0])) if scalar else (lon, lat)


CORRECTION = load_correction()
WARP = load_warp(CORRECTION)
# the id a build is tagged with: affine, plus the warp when there is one
BUILD_ID = None if not CORRECTION else CORRECTION["id"] + ("+" + WARP["id"] if WARP else "")
FWD = _Corrected(True, CORRECTION, WARP) if CORRECTION else FWD_RAW    # metres -> lon, lat
INV = _Corrected(False, CORRECTION, WARP) if CORRECTION else INV_RAW   # lon, lat -> metres

# The four full-book parts, in the book's own numbering (Final-1..4).
PART_FILES = {
    "NW": "AUDA FULL DP 4 PART\\DP 2021 AUDA Final-1.pdf",
    "NE": "AUDA FULL DP 4 PART\\DP 2021 AUDA Final-2.pdf",
    "SW": "AUDA FULL DP 4 PART\\DP 2021 AUDA Final-3.pdf",
    "SE": "AUDA FULL DP 4 PART\\DP 2021 AUDA Final-4.pdf",
}


# ---------------------------------------------------------------- grid fit

def _sheet_corners(view):
    """The four page corners of the viewport, in the order SW, NW, NE, SE.

    The viewport rectangle is llx..urx by lly..ury in default user space, and
    the normalised position of a point inside it is

        u = (x - llx) / (urx - llx)
        v = (y - lly) / (ury - lly)

    /Bounds maps the ground corners (SW, NW, NE, SE) to the unit square
    (0,1), (0,0), (1,0), (1,1). So SW is at u=0,v=1 and NW at u=0,v=0:
    the *ground* south edge is the page y = ury, and the ground north edge is
    the page y = lly. In this book the producer wrote the box with the y axis
    flipped (lly > ury), which is why getting this backwards is easy.
    """
    llx, lly, urx, ury = [float(v) for v in view["bbox"]]
    return [(llx, ury), (llx, lly), (urx, lly), (urx, ury)]


def _ground_corners(view):
    """The four ground corners, in the same SW, NW, NE, SE order, as (lat, lon)."""
    g = [float(v) for v in view["gpts"]]
    return [(g[0], g[1]), (g[2], g[3]), (g[4], g[5]), (g[6], g[7])]


def fit_grid(views):
    """Fit the 2x2 book grid over all sixteen corners of the four parts.

    The four part sheets share one canvas geometry and one scale, and they tile
    the book in a regular 2x2 grid of projected rectangles. Fitting that grid
    once - over all sixteen corners, instead of trusting each file's four
    rounded corner values on its own - averages out the ~11 m rounding of the
    published /GPTS values.

    Returns a dict with the grid origin, sheet size, scale and the residuals.
    """
    obs_e, obs_n = [], []
    refs = set()
    for quad, rel in PART_FILES.items():
        rec = next((r for r in views if r["file"] == rel), None)
        if not rec or not rec.get("views"):
            raise RuntimeError("no georeference for " + rel)
        view = rec["views"][0]
        page = _sheet_corners(view)
        ground = _ground_corners(view)
        col = 0 if quad in ("SW", "NW") else 1
        row = 0 if quad in ("SW", "SE") else 1
        llx, lly, urx, ury = [float(v) for v in view["bbox"]]
        refs.add((llx, ury))
        for (px, py), (lat, lon) in zip(page, ground):
            # the published corners define the RAW plan frame; the ground
            # correction is applied after it, so it must not leak in here
            # (through INV it would be absorbed into the grid and cancelled)
            mx, my = INV_RAW.transform(lon, lat)
            obs_e.append((px, col, mx))
            obs_n.append((py, row, my))

    if len(refs) != 1:
        raise RuntimeError("the four parts do not share one canvas origin: %r" % (refs,))
    ref_x, ref_y = refs.pop()

    def solve(obs, cell, ref):
        # value = origin + cell*span + s*(coord - ref)
        rows = []
        rhs = []
        for coord, c, v in obs:
            rows.append([1.0, float(c), coord - ref])
            rhs.append(v)
        A = np.array(rows)
        b = np.array(rhs)
        sol, *_ = np.linalg.lstsq(A, b, rcond=None)
        resid = b - A @ sol
        return {"origin": float(sol[0]), "pitch": float(sol[1]),
                "scale_m_per_pt": float(sol[2]),
                "residuals": resid.tolist(),
                "rms_m": float(np.sqrt((resid ** 2).mean()))}

    east = solve(obs_e, "col", ref_x)
    north = solve(obs_n, "row", ref_y)
    return {
        "ref_x": ref_x, "ref_y": ref_y,
        "east": east, "north": north,
        "scale_m_per_pt": float((east["scale_m_per_pt"] + north["scale_m_per_pt"]) / 2),
    }


# ------------------------------------------------------------- transform

class Block:
    """One page of the book, with its exact page->ground transform."""

    def __init__(self, path, view, grid, label=None, col=0, row=0):
        self.path = path
        self.label = label or os.path.basename(path)
        self.view = view
        self.page_corners = _sheet_corners(view)
        self.ground_corners = _ground_corners(view)
        llx, lly, urx, ury = [float(v) for v in view["bbox"]]
        self.bbox = (llx, lly, urx, ury)
        self.llx, self.lly, self.urx, self.ury = llx, lly, urx, ury
        e, n = grid["east"], grid["north"]
        self._col = col
        self._row = row
        # The four parts OVERLAP: the printed sheets are 36.3 km wide but the
        # column pitch is only 34.5 km, so neighbouring parts share a 1.8 km
        # strip. The pitch, not the sheet size, is what the grid fit gives.
        self.e0 = e["origin"] + e["pitch"] * col
        self.n0 = n["origin"] + n["pitch"] * row
        self.s = grid["scale_m_per_pt"]

    def set_grid_cell(self, col, row):
        self._col, self._row = col, row

    # -- the affine page -> TM metres ---------------------------------
    def page_to_metres(self, x, y):
        """PDF user space (origin bottom-left of the media box) -> TM metres."""
        return (self.e0 + self.s * (x - self.llx),
                self.n0 + self.s * (y - self.ury))

    def metres_to_page(self, mx, my):
        return ((mx - self.e0) / self.s + self.llx,
                (my - self.n0) / self.s + self.ury)

    def page_to_ground(self, x, y):
        mx, my = self.page_to_metres(x, y)
        lon, lat = FWD.transform(mx, my)
        return lon, lat

    def ground_to_page(self, lon, lat):
        mx, my = INV.transform(lon, lat)
        return self.metres_to_page(mx, my)

    @property
    def lon_min(self):
        return min(FWD.transform(*self.page_to_metres(x, y))[0]
                   for x, y in self.page_corners)

    @property
    def lon_max(self):
        return max(FWD.transform(*self.page_to_metres(x, y))[0]
                   for x, y in self.page_corners)

    @property
    def lat_min(self):
        return min(FWD.transform(*self.page_to_metres(x, y))[1]
                   for x, y in self.page_corners)

    @property
    def lat_max(self):
        return max(FWD.transform(*self.page_to_metres(x, y))[1]
                   for x, y in self.page_corners)

    def ground_of_rect(self, x0, y0, x1, y1):
        pts = [self.page_to_ground(x0, y0), self.page_to_ground(x1, y0),
               self.page_to_ground(x1, y1), self.page_to_ground(x0, y1)]
        lons = [p[0] for p in pts]
        lats = [p[1] for p in pts]
        return (min(lons), min(lats), max(lons), max(lats))

    # -- coordinate spaces ---------------------------------------------
    # "user space"   PDF user space: origin at the bottom-left of the media
    #                box, y increasing upward. This is what /VP and /GPTS use.
    # "page space"   PyMuPDF page space: origin at the top-left of the crop
    #                box, y increasing downward. This is what get_pixmap(clip=)
    #                takes. Mixing the two up mirrors the map about the
    #                horizontal, which is silent and very easy to miss.
    def user_to_page(self, x, y, cropbox):
        return (x - cropbox[0], cropbox[3] - y)

    def page_to_user(self, px, py, cropbox):
        return (px + cropbox[0], cropbox[3] - py)

    def describe(self):
        return {
            "label": self.label,
            "pdf_bbox": list(self.bbox),
            "scale_m_per_pt": self.s,
            "grid_cell": [self._col, self._row],
            "e0": self.e0, "n0": self.n0,
            "lon_min": self.lon_min, "lon_max": self.lon_max,
            "lat_min": self.lat_min, "lat_max": self.lat_max,
        }


# ------------------------------------------------------------- loading

def load_views(path=VIEWS):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_blocks(views=None, base=None):
    """-> {quad: Block} for the four full-book parts, with the grid fitted."""
    views = views or load_views()
    base = base or os.path.join(ROOT, "NEW DP BOOK AUDA")
    grid = fit_grid(views)
    blocks = {}
    for quad, rel in PART_FILES.items():
        rec = next((r for r in views if r["file"] == rel), None)
        if not rec or not rec.get("views"):
            raise RuntimeError("no georeference for " + rel)
        b = Block(os.path.join(base, rel), rec["views"][0], grid, label=quad,
                  col=0 if quad in ("SW", "NW") else 1,
                  row=0 if quad in ("SW", "SE") else 1)
        blocks[quad] = b
    return blocks, grid


def village_blocks(views=None, base=None):
    """-> {filename: Block} for every georeferenced per-village extract.

    The village PDFs are crops of the same four part sheets, so they carry the
    same /VP and therefore the same transform; only the crop window differs.
    """
    views = views or load_views()
    base = base or os.path.join(ROOT, "NEW DP BOOK AUDA")
    blocks, grid = build_blocks(views, base)
    by_quad = {b.bbox: b for b in blocks.values()}
    out = {}
    for rec in views:
        if "FULL DP" in rec["file"] or not rec.get("views"):
            continue
        view = rec["views"][0]
        key = tuple(float(v) for v in view["bbox"])
        b = by_quad.get(key)
        if b is None:
            continue
        vb = Block(os.path.join(base, rec["file"]), view, grid,
                   label=os.path.splitext(os.path.basename(rec["file"]))[0],
                   col=b._col, row=b._row)
        out[rec["file"]] = vb
    return out, grid


if __name__ == "__main__":
    views = load_views()
    blocks, grid = fit_grid(views), None
    blocks, grid = build_blocks(views)
    print("grid fit:")
    print(f"  scale           {grid['scale_m_per_pt']:.6f} m/pt")
    print(f"  sheet easting   {grid['east']['origin']:.3f}  pitch {grid['east']['pitch']:.3f} m"
          f"  rms {grid['east']['rms_m']:.3f} m")
    print(f"  sheet northing  {grid['north']['origin']:.3f}  pitch {grid['north']['pitch']:.3f} m"
          f"  rms {grid['north']['rms_m']:.3f} m")
    print(f"  sheet size      {grid['scale_m_per_pt']*(grid['east']['pitch']*0+12867):.1f} x "
          f"{grid['scale_m_per_pt']*12107:.1f} m")
    for q, b in blocks.items():
        d = b.describe()
        print(f"\n{q}: {d['label']}")
        print(f"   grid cell {d['grid_cell']}  e0 {d['e0']:.2f}  n0 {d['n0']:.2f}")
        print(f"   extent lon {d['lon_min']:.6f}..{d['lon_max']:.6f}"
              f"  lat {d['lat_min']:.6f}..{d['lat_max']:.6f}")
    lons = [b.lon_min for b in blocks.values()] + [b.lon_max for b in blocks.values()]
    lats = [b.lat_min for b in blocks.values()] + [b.lat_max for b in blocks.values()]
    print(f"\nBOOK EXTENT  lon {min(lons):.6f}..{max(lons):.6f}  lat {min(lats):.6f}..{max(lats):.6f}")
