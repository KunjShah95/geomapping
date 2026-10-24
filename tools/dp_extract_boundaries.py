"""Extract the plan's REAL boundaries from the vector GeoPDFs, and map every
survey record onto the boundary that contains it.

What the source actually contains
----------------------------------
The four part sheets are vector drawings, and they draw two kinds of closed
boundary:

  * PLOT boundaries - the parcels the survey numbers sit in. Thin strokes
    (0.48pt black, 0.96pt red in some regions), a handful of vertices each,
    and they contain exactly one survey number.

  * UNIT boundaries - the plan's own village, TP-scheme and settlement
    boundaries. Thicker strokes (2.4pt and up, in black, magenta and blue),
    hundreds of vertices, and they contain many survey numbers and the
    plan's own place names.

The project so far had neither. It had a *land-use zone* layer traced from
the sheet's fill colours, which answers "what is this ground zoned as" and
not "where is the plot boundary". The zones are the wrong shape for a record:
a zone is a zone, a record is a plot, and the client needs the plot.

How a record is matched to its boundary
---------------------------------------
By CONTENT, not by style. For each record we find every closed ring that
contains it, and take the SMALLEST. That is the plot it is printed in. A
style-based rule (thin black stroke) would have been a guess; the
containment test is a measurement, and it is the same test that proves the
rings are parcels: on the NW sheet 88.4% of the numbers land in exactly one
thin black ring, and 60,952 of those rings hold exactly one number.

The sheets OVERLAP by about 1.8 km, so the same parcel is drawn twice. The
grid fit puts both copies within a couple of metres, so a parcel is the same
parcel if its centroid and its area match.

Output
------
  data/dp2021/parcels.geojson     every plot boundary, with its attributes
  data/dp2021/units.geojson       the plan's own village / scheme boundaries
  data/dp2021/record_parcels.ndjson  every record, with the parcel it is in
  data/dp2021/boundary_stats.json the counts and the coverage
"""
import argparse
import collections
import json
import math
import os
import re
import sys
import time

import fitz
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dp_georef as G  # noqa: E402
from dp_extract_labels import kind_of, spans_of  # noqa: E402

ROOT = G.ROOT
OUT = os.path.join(ROOT, "data", "dp2021")

# A ring larger than this is not a plot.
PARCEL_MAX_AREA_M2 = 5.0e5      # 50 ha
# A ring smaller than this is a speck, not a plot.
PARCEL_MIN_AREA_M2 = 150.0
# A ring with more vertices than this is an enclosing boundary, not a plot.
PARCEL_MAX_VERTICES = 60
# A unit boundary is drawn at least this thick.
UNIT_MIN_WIDTH = 1.2
# The plan's own colours for its unit boundaries.
SCHEME_COLOUR = (1.0, 0.0, 0.773)      # magenta - TP scheme
SHEET_COLOUR = (0.0, 0.439, 1.0)       # blue    - the sheet's own frame

NOT_NAME = ("SUR.NO", "SUR. NO", "BL.NO", "BL. NO", "SUR NO", "PART OF")
# Sheet furniture and reference codes are not place names. The list is
# deliberately strict: a wrong name on a boundary is worse than no name.
# (No trailing "|" - an empty alternative would match every string.)
CODE_RE = re.compile(
    r"^(?:DTPS|TPS|SP|DRC|UDP|TP|SEZ|NE?S|WS?|N|PH|GT|GIDC|MIDC)\b[\s\-]?\d*"
    r"|^BL\.?\s?NO|^SUR\.?\s?NO|^NR\b|^\d+(?:\.\d+)?\s*(?:M|Mt)\.?$"
    r"|^PART OF$|^REFER\b|^NOTE\b|^SCALE\b|^LEGEND\b|^SHEET\b"
    r"|^A-?\d|^[A-Q]-?\d{1,2}$"
    r"|^[\d/]+[A-Z]*(?:/P)?$|^\d+(?:\.\d+)?$"
    r"|^[A-Z]?\d+[A-Z]?/D$|^[A-Z]?\d+[A-Z]?/[A-Z]$"
    r"|^\s*[/\\-]",
    re.IGNORECASE)

# A place name is letters: "PARU", "DAHEGAM", "GIDC", "SARKHEJ". Not a code.
PLACENAME_RE = re.compile(r"^[A-Z][A-Z&.\- ]{2,}$")


# ---------------------------------------------------------------- geometry

def geom_closed(d):
    """Is this path a closed ring? PyMuPDF does not set closePath on them."""
    it = d["items"]
    if len(it) < 2 or it[0][0] != "l" or it[-1][0] != "l":
        return False
    a, z = it[0][1], it[-1][2]
    return abs(a[0] - z[0]) < 0.05 and abs(a[1] - z[1]) < 0.05


def ring_of(d):
    """The path's ring, in PDF user space (y up). Duplicate last point removed."""
    pts = []
    for it in d["items"]:
        if it[0] == "l":
            p, q = it[1], it[2]
            if not pts:
                pts.append((p.x, p.y))
            pts.append((q.x, q.y))
        elif it[0] == "re":
            r = it[1]
            pts += [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
        elif it[0] == "qu":
            q = it[1]
            pts += [(q.ul.x, q.ul.y), (q.ur.x, q.ur.y),
                    (q.lr.x, q.lr.y), (q.ll.x, q.ll.y)]
    if len(pts) > 1 and abs(pts[0][0] - pts[-1][0]) < 0.05 \
            and abs(pts[0][1] - pts[-1][1]) < 0.05:
        pts.pop()
    return pts


def shoelace(ring):
    a = 0.0
    n = len(ring)
    j = n - 1
    for i in range(n):
        a += ring[j][0] * ring[i][1] - ring[i][0] * ring[j][1]
        j = i
    return a / 2.0


def centroid_of(ring):
    a = shoelace(ring)
    if abs(a) < 1e-9:
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        return (sum(xs) / len(xs), sum(ys) / len(ys))
    cx = cy = 0.0
    n = len(ring)
    j = n - 1
    for i in range(n):
        f = ring[j][0] * ring[i][1] - ring[i][0] * ring[j][1]
        cx += (ring[j][0] + ring[i][0]) * f
        cy += (ring[j][1] + ring[i][1]) * f
        j = i
    return (cx / (6.0 * a), cy / (6.0 * a))


def point_in_ring(pt, ring):
    """Ray casting. `ring` is closed implicitly."""
    x, y = pt
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y):
            if x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
        j = i
    return inside


def ring_bbox(ring):
    xs = [p[0] for p in ring]
    ys = [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


class Grid:
    """A uniform bucket index over rings, for point-in-polygon lookups."""

    def __init__(self, cell):
        self.cell = cell
        self.b = collections.defaultdict(list)

    def add(self, i, ring):
        x0, y0, x1, y1 = ring_bbox(ring)
        for cx in range(int(x0 // self.cell), int(x1 // self.cell) + 1):
            for cy in range(int(y0 // self.cell), int(y1 // self.cell) + 1):
                self.b[(cx, cy)].append(i)

    def query(self, pt):
        return self.b.get((int(pt[0] // self.cell), int(pt[1] // self.cell)), ())


# ------------------------------------------------------------ extraction

def sheet_features(block):
    """The closed rings, survey numbers and place names of one part sheet.

    IMPORTANT: PyMuPDF reports drawing items in PAGE space (origin at the
    top-left of the crop box, y increasing downward) - the same space as a
    text span's bbox. PDF user space has y increasing upward. Mixing them
    mirrors the drawing about the horizontal, silently. The conversion is
    `Block.user_to_page`'s inverse, and it is applied once, in
    `ring_to_ground`.
    """
    doc = fitz.open(block.path)
    page = doc[0]
    crop = page.cropbox
    dr = page.get_drawings()

    rings = []
    for d in dr:
        if d["type"] not in ("s", "fs"):
            continue
        col = d.get("color")
        if col is None or not geom_closed(d):
            continue
        r = ring_of(d)
        if len(r) < 3:
            continue
        rings.append({
            "ring": r,
            "colour": tuple(round(c, 3) for c in col),
            "width": round(d.get("width") or 0, 2),
        })

    numbers = []
    for text, bbox, size, direction in spans_of(page):
        t = text.strip()
        if kind_of(t, size) == "plot":
            numbers.append((t, (bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0))

    places = []
    for text, bbox, size, direction in spans_of(page):
        t = text.strip()
        if len(t) < 3 or not t.isupper() or CODE_RE.match(t) or t in NOT_NAME:
            continue
        if not PLACENAME_RE.match(t):
            continue
        places.append((t, (bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0, size))

    doc.close()
    return rings, numbers, places, crop


def ring_to_ground(block, ring, crop):
    """A PAGE-space ring -> a list of (lon, lat).

    PDF user space has y increasing upward from the bottom-left of the media
    box; PyMuPDF page space has y increasing downward from the top-left of
    the crop box. `Block.user_to_page` is the forward direction; this is its
    inverse.
    """
    out = []
    for x, y in ring:
        ux = x + crop.x0
        uy = crop.y1 - y
        lon, lat = block.page_to_ground(ux, uy)
        out.append((lon, lat))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--plots", default=os.path.join(OUT, "plots.geojson"))
    ap.add_argument("--min-area-m2", type=float, default=PARCEL_MIN_AREA_M2)
    ap.add_argument("--max-area-m2", type=float, default=PARCEL_MAX_AREA_M2)
    ap.add_argument("--max-vertices", type=int, default=PARCEL_MAX_VERTICES)
    ap.add_argument("--cache", default=os.path.join(ROOT, "work", "newdp", "rings.pkl"),
                    help="where the read-from-PDF rings are kept, so the "
                         "classification can be re-tuned without re-reading "
                         "the sheets")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    blocks, _ = G.build_blocks()

    # ---- 1. read the sheets -------------------------------------------------
    sheet_data = {}
    if args.cache and os.path.exists(args.cache) and not args.no_cache:
        import pickle
        t0 = time.time()
        with open(args.cache, "rb") as f:
            sheet_data = pickle.load(f)
        print(f"read the rings from the cache ({time.time()-t0:.0f}s)")

    if not sheet_data:
        for quad, b in blocks.items():
            t0 = time.time()
            rings, numbers, places, crop = sheet_features(b)
            sheet_data[quad] = {"rings": rings, "numbers": numbers, "places": places,
                                "crop": crop}
            print(f"[{quad}] {len(rings):,} closed rings, {len(numbers):,} survey "
                  f"numbers, {len(places):,} place names  ({time.time()-t0:.0f}s)",
                  flush=True)
        if args.cache:
            import pickle
            os.makedirs(os.path.dirname(args.cache), exist_ok=True)
            with open(args.cache, "wb") as f:
                pickle.dump(sheet_data, f, protocol=4)
            print(f"cached the rings -> {args.cache}")

    # ---- 2. put every ring on the Earth, and measure it ---------------------
    all_rings = []
    for quad, b in blocks.items():
        m2 = b.s * b.s
        crop = sheet_data[quad]["crop"]
        for r in sheet_data[quad]["rings"]:
            g = ring_to_ground(b, r["ring"], crop)
            if len(g) < 3:
                continue
            # the area, in the sheet's own projected space (not in degrees)
            r["area_m2"] = abs(shoelace(r["ring"])) * m2
            r["ground"] = g
            r["block"] = quad
            cx, cy = centroid_of(r["ring"])
            lon, lat = b.page_to_ground(cx + crop.x0, crop.y1 - cy)
            r["lon"] = lon
            r["lat"] = lat
            all_rings.append(r)
    print(f"\n{len(all_rings):,} rings on the Earth")

    # ---- 3. de-duplicate across the sheet seams ----------------------------
    # The parts overlap by ~1.8 km, so the same boundary is drawn twice. The
    # grid fit puts both copies within a couple of metres, so the same
    # boundary has the same centroid and the same area.
    all_rings, dupes = dedupe_rings(all_rings)
    print(f"{len(all_rings):,} after de-duplicating the sheet seams "
          f"({dupes:,} duplicates dropped)")

    # ---- 4. index, and put every record in the smallest ring that holds it --
    # Only rings that could be a plot go in the plot index: a plot is small.
    # The big enclosing boundaries go in a separate index, and are used for
    # the village and scheme join. Splitting them keeps the index small.
    cand = [i for i, r in enumerate(all_rings)
            if args.min_area_m2 <= r["area_m2"] <= args.max_area_m2
            and len(r["ground"]) <= args.max_vertices]
    print(f"plot candidates: {len(cand):,}")

    pgrid = Grid(0.0004)          # ~44 m cells
    for i in cand:
        pgrid.add(i, all_rings[i]["ground"])
    print(f"index: {len(pgrid.b):,} cells")

    with open(args.plots, encoding="utf-8") as f:
        plots = json.load(f)["features"]
    print(f"{len(plots):,} records to place")

    rec_parcel = {}
    t0 = time.time()
    for n, ft in enumerate(plots):
        lon, lat = ft["geometry"]["coordinates"]
        cands = [i for i in pgrid.query((lon, lat))
                 if point_in_ring((lon, lat), all_rings[i]["ground"])]
        if not cands:
            continue
        # the SMALLEST containing ring is the plot the number is printed in
        best = min(cands, key=lambda i: all_rings[i]["area_m2"])
        rec_parcel[(round(lon, 7), round(lat, 7), ft["properties"].get("t", ""))] = best
        if n % 50000 == 0:
            print(f"    {n:,} records ({time.time()-t0:.0f}s)", flush=True)
    print(f"  placed {len(rec_parcel):,} records ({time.time()-t0:.0f}s)")

    # ---- 5. which rings are plots, and which are the plan's own units ----
    # A ring is a PLOT if it holds a survey number. A ring is a UNIT if the
    # plan drew it as one: a thick stroke, the magenta scheme colour, the blue
    # frame colour, or a very complex outline.
    recs_per_ring = collections.Counter(rec_parcel.values())
    used = set(rec_parcel.values())
    units = []
    parcels = []
    for i, r in enumerate(all_rings):
        is_unit = (r["width"] >= UNIT_MIN_WIDTH
                   or r["colour"] in (SCHEME_COLOUR, SHEET_COLOUR)
                   or len(r["ground"]) > args.max_vertices
                   or r["area_m2"] > args.max_area_m2)
        if is_unit:
            units.append(i)
        elif i in used:
            parcels.append(i)
    print(f"\nparcels: {len(parcels):,}   unit/other boundaries: {len(units):,}")
    multi = sum(1 for i in parcels if recs_per_ring[i] > 1)
    print(f"  parcels holding more than one survey number: {multi:,}")

    # ---- 6. name the units from the plan's own place names -----------------
    # The place names and the rings are both in PAGE space, so no conversion
    # is needed here. A grid over the place names keeps this linear: without
    # it, every unit would be tested against every name on the sheet.
    for q in blocks:
        places = sheet_data[q]["places"]
        pgrid = Grid(400.0)
        for k, (t, x, y, sz) in enumerate(places):
            pgrid.b[(int(x // 400.0), int(y // 400.0))].append(k)
        for i in units:
            r = all_rings[i]
            if r["block"] != q:
                continue
            names = []
            x0, y0, x1, y1 = ring_bbox(r["ring"])
            for cx in range(int(x0 // 400.0), int(x1 // 400.0) + 1):
                for cy in range(int(y0 // 400.0), int(y1 // 400.0) + 1):
                    for k in pgrid.b.get((cx, cy), ()):
                        t, x, y, sz = places[k]
                        if point_in_ring((x, y), r["ring"]):
                            names.append((sz, t))
            names.sort(key=lambda z: -z[0])
            r["name"] = names[0][1] if names else ""
            r["n_names"] = len(names)
            r["records"] = recs_per_ring.get(i, 0)
            r["type"] = unit_type(r)

    # ---- 7. write -----------------------------------------------------------
    pmap = {i: k for k, i in enumerate(sorted(parcels))}
    recs_by_parcel = collections.defaultdict(list)
    for key, i in rec_parcel.items():
        if i in pmap:
            recs_by_parcel[pmap[i]].append(key)

    write_parcels(os.path.join(args.out, "parcels.geojson"), all_rings,
                  sorted(parcels), pmap, recs_by_parcel)
    write_units(os.path.join(args.out, "units.geojson"), all_rings, units)
    write_records(os.path.join(args.out, "record_parcels.ndjson"),
                  plots, rec_parcel, pmap, all_rings)
    # dp_polygonize_plots.py snapshots the closed-ring layer once, as
    # *.closed_rings.*, and always restarts from that snapshot. A fresh
    # closed-ring layer makes the snapshot stale (after a georeference change
    # it is in the wrong place entirely), so drop it and let the next
    # polygonize run take a new one.
    for stale in ("parcels.closed_rings.geojson", "record_parcels.closed_rings.ndjson"):
        p = os.path.join(args.out, stale)
        if os.path.exists(p):
            os.remove(p)
            print(f"removed stale snapshot {stale}")

    summary = {
        "records": len(plots),
        "records_with_a_boundary": len(rec_parcel),
        "parcels": len(parcels),
        "units": len(units),
        "units_by_type": dict(collections.Counter(all_rings[i]["type"] for i in units)),
        "parcel_area_m2": area_summary(all_rings, parcels),
        "records_per_parcel": collections.Counter(
            len(v) for v in recs_by_parcel.values()).most_common(12),
    }
    with open(os.path.join(args.out, "boundary_stats.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print("\n" + json.dumps(summary, indent=2)[:2000])


def unit_type(r):
    """What kind of unit boundary is this?

    The plan draws its TP schemes in magenta and its villages in black, and
    the sheet's own frame in blue. The colour is the plan's own convention,
    so it is worth keeping.
    """
    col = r["colour"]
    if col == SCHEME_COLOUR:
        return "scheme"
    if col == SHEET_COLOUR:
        return "sheet"
    if r["width"] >= UNIT_MIN_WIDTH:
        return "village"
    if r.get("records", 0) > 0:
        return "plot-group"
    return "other"


def dedupe_rings(rings):
    """Merge the same boundary drawn on two overlapping part sheets."""
    cell = 0.0002      # ~22 m
    buckets = collections.defaultdict(list)
    for i, r in enumerate(rings):
        buckets[(int(r["lon"] // cell), int(r["lat"] // cell))].append(i)

    keep = []
    dropped = 0
    used = set()
    for i, r in enumerate(rings):
        if i in used:
            continue
        cx = int(r["lon"] // cell)
        cy = int(r["lat"] // cell)
        dup = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in buckets.get((cx + dx, cy + dy), ()):
                    if j == i or j in used:
                        continue
                    s = rings[j]
                    if abs(s["area_m2"] - r["area_m2"]) > 0.05 * max(1.0, r["area_m2"]):
                        continue
                    dlon = s["lon"] - r["lon"]
                    dlat = s["lat"] - r["lat"]
                    if math.hypot(dlon * 111320.0 * math.cos(math.radians(r["lat"])),
                                  dlat * 110990.0) > 8.0:
                        continue
                    dup = j
                    break
                if dup is not None:
                    break
            if dup is not None:
                break
        if dup is None:
            keep.append(r)
        else:
            used.add(dup)
            dropped += 1
    return keep, dropped


def area_summary(rings, idxs):
    if not idxs:
        return {}
    a = np.array([rings[i]["area_m2"] for i in idxs])
    return {"p10": round(float(np.percentile(a, 10)), 1),
            "median": round(float(np.median(a)), 1),
            "p90": round(float(np.percentile(a, 90)), 1),
            "max": round(float(a.max()), 1)}


def write_parcels(path, rings, parcels, pmap, recs_by_parcel):
    feats = []
    for i in parcels:
        r = rings[i]
        g = r["ground"]
        ring = [[round(x, 7), round(y, 7)] for x, y in g]
        if len(ring) < 3:
            continue
        nums = [k[2] for k in recs_by_parcel.get(pmap[i], ())]
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {
                "pid": pmap[i],
                "area_m2": round(r["area_m2"], 1),
                "block": r["block"],
                "vertices": len(g),
                "numbers": len(nums),
                "survey": ",".join(sorted(set(nums))[:8]),
                "style": "%s@%s" % (r["colour"], r["width"]),
            },
        })
    fc = {"type": "FeatureCollection", "name": "auda_dp2021_parcels",
          "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
          "features": feats}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(fc, f, separators=(",", ":"))
    print(f"wrote {path}  ({len(feats):,} features, {os.path.getsize(path)/1e6:.1f} MB)")


def write_units(path, rings, units):
    feats = []
    for i in units:
        r = rings[i]
        g = r["ground"]
        if len(g) < 3:
            continue
        ring = [[round(x, 7), round(y, 7)] for x, y in g]
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {
                "type": r["type"], "name": r.get("name", ""), "block": r["block"],
                "area_km2": round(r["area_m2"] / 1e6, 3),
                "place_names": r.get("n_names", 0),
                "records": r.get("records", 0),
                "vertices": len(g),
            },
        })
    fc = {"type": "FeatureCollection", "name": "auda_dp2021_units",
          "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
          "features": feats}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(fc, f, separators=(",", ":"))
    print(f"wrote {path}  ({len(feats):,} features, {os.path.getsize(path)/1e6:.1f} MB)")
    # the named-unit index, with the record counts, is written by
    # tools/dp_join_boundaries.py - it needs the join to count them.


def write_records(path, plots, rec_parcel, pmap, rings):
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        for ft in plots:
            lon, lat = ft["geometry"]["coordinates"]
            t = ft["properties"].get("t", "")
            key = (round(lon, 7), round(lat, 7), t)
            i = rec_parcel.get(key)
            rec = {"t": t, "lon": lon, "lat": lat}
            if i is not None and i in pmap:
                rec["pid"] = pmap[i]
                rec["area_m2"] = round(rings[i]["area_m2"], 1)
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
            n += 1
    print(f"wrote {path}  ({n:,} records)")


if __name__ == "__main__":
    main()
