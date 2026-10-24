"""
Recover the plot boundaries the closed-ring extractor cannot see.

`dp_extract_boundaries.py` takes a plot to be a closed ring: one path whose
last point meets its first. Most of the book draws plots that way, but not
all of it. Three regions draw the same plot lines as loose, shared strokes -
one line between two plots, not one ring per plot:

  * the 1.8 km seam strips where the four part sheets overlap (a cross
    through the middle of the plan),
  * the old city, drawn in 0.96 pt red,
  * the southern and western fringe.

There the closed-ring layer has nothing, and ~45,000 survey records had no
plot. The lines ARE in the drawing; they only need to be assembled. So this
tool nodes the plan's own plot strokes and polygonizes them: every face of
the line network is a candidate plot, and a face that holds a survey number
is that record's plot - the same "by content, not by style" rule the
closed-ring extractor uses.

It only ADDS plots, for records that have none. Where a closed ring exists it
is left alone, and the polygonized face of the same plot is used as the
check: the two are drawn from the same strokes, so they should agree, and
the IoU between them is reported.

Work is done in windows of the sheet with a margin. A face that touches the
margin's edge may be cut off by it, so only faces wholly inside the window
and centred in its core are kept; the neighbouring window owns the rest.

    python tools/dp_polygonize_plots.py            # ~10-20 min on many cores
    python tools/dp_join_boundaries.py             # then re-join and re-bucket
    python tools/bucket_parcels.py

Inputs:  the four part sheets, work/newdp/rings.pkl (survey numbers, page space),
         data/dp2021/parcels.geojson, data/dp2021/record_parcels.ndjson
Outputs: the same two files, with the recovered plots added (style
         "polygonized"), and data/dp2021/polygonize_summary.json.
         The originals are kept as *.closed_rings.* the first time it runs.
"""
import argparse
import collections
import json
import math
import multiprocessing as mp
import os
import pickle
import shutil
import sys
import time

import fitz
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import polygonize, unary_union
from shapely.strtree import STRtree

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dp_georef as G  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "dp2021")
WORK = os.path.join(ROOT, "work", "newdp")

# Window geometry, in sheet points (1 pt is ~2.8 m on the ground).
CORE_PT = 300.0        # the part of the window whose faces this window owns
MARGIN_PT = 300.0      # context around it, so a plot on the core's edge is whole
                       # (a 50 ha plot is ~250 pt across, so the margin must exceed it)

# Village house sites (gamtal) are drawn as cells of 20-140 m2, so the floor
# is low: a face that holds a survey number is a plot, however small.
PARCEL_MIN_AREA_M2 = 10.0
PARCEL_MAX_AREA_M2 = 5.0e5
# A face holding more numbers than this is a block of plots the plan did not
# divide, not one plot.
MAX_NUMBERS_PER_FACE = 4

BLACK = (0.0, 0.0, 0.0)
RED = (1.0, 0.0, 0.0)
WHITE = (1.0, 1.0, 1.0)

# Second pass: in the packed house clusters the plan draws each cell as a
# small filled rectangle with a gap round it, not as lines. Those fills are
# used, but only locally, around records the first pass could not place, so
# they can never re-cut a plot the linework already found.
CELL_MAX_AREA_PT2 = 400.0     # ~3,200 m2: a cell, not a zone fill
LOCAL_PT = 120.0

# Labels the classifier took for survey numbers that are not: road widths
# ("12 M", "18.00 M") and several crowded numbers run together by the PDF's
# text order ("959493929"). They get no plot.
import re  # noqa: E402
ROAD_WIDTH_RE = re.compile(r"^\d+(?:\.\d+)?\s*M\.?$", re.IGNORECASE)
RUN_TOGETHER_RE = re.compile(r"^\d{6,}$")


def not_a_survey_number(t):
    t = t.strip()
    return bool(ROAD_WIDTH_RE.match(t) or RUN_TOGETHER_RE.match(t))


def is_plot_stroke(colour, width):
    """The strokes that draw plot boundaries.

    Black at any weight: 0.48 pt plot lines, plus the heavier black village
    and scheme lines, which bound the plots beside them too. Thin red: the
    old city draws its plots in 0.96 pt red. Thicker red (1.44 pt) is the
    proposed-road centreline and is NOT a plot edge.
    """
    return colour == BLACK or (colour == RED and width <= 1.0)


# ------------------------------------------------------------ reading strokes

def read_strokes(quad, path):
    """Every plot stroke on one sheet, as a list of page-space polylines."""
    doc = fitz.open(path)
    page = doc[0]
    lines = []
    cells = []
    for d in page.get_drawings():
        if d["type"] == "f":
            fill = tuple(round(c, 3) for c in (d.get("fill") or ()))
            r = d["rect"]
            if fill and fill != WHITE and r.width * r.height <= CELL_MAX_AREA_PT2:
                ring = _outline(d["items"])
                if len(ring) >= 4:
                    cells.append(ring)
            continue
        if d["type"] not in ("s", "fs"):
            continue
        col = tuple(round(c, 3) for c in (d.get("color") or ()))
        if not is_plot_stroke(col, round(d.get("width") or 0, 2)):
            continue
        pts = []
        for it in d["items"]:
            if it[0] == "l":
                pts += [(it[1].x, it[1].y), (it[2].x, it[2].y)]
            elif it[0] == "c":
                pts += [(it[1].x, it[1].y), (it[4].x, it[4].y)]
            elif it[0] == "re":
                r = it[1]
                pts += [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1), (r.x0, r.y0)]
            elif it[0] == "qu":
                q = it[1]
                pts += [(q.ul.x, q.ul.y), (q.ur.x, q.ur.y), (q.lr.x, q.lr.y),
                        (q.ll.x, q.ll.y), (q.ul.x, q.ul.y)]
        clean = [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]
        if len(clean) >= 2:
            lines.append(clean)
    size = (page.rect.width, page.rect.height)
    doc.close()
    return lines, cells, size


def _outline(items):
    pts = []
    for it in items:
        if it[0] == "l":
            pts += [(it[1].x, it[1].y), (it[2].x, it[2].y)]
        elif it[0] == "re":
            r = it[1]
            pts += [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
        elif it[0] == "qu":
            q = it[1]
            pts += [(q.ul.x, q.ul.y), (q.ur.x, q.ur.y), (q.lr.x, q.lr.y), (q.ll.x, q.ll.y)]
    clean = [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]
    if clean and clean[0] != clean[-1]:
        clean.append(clean[0])
    return clean


# ------------------------------------------------------------ the worker

_W = {}


def _init(stroke_files, numbers):
    _W["files"] = stroke_files
    _W["numbers"] = numbers
    _W["sheets"] = {}


def _sheet(quad):
    s = _W["sheets"].get(quad)
    if s is None:
        # one sheet at a time per worker: four sheets of shapely lines in each
        # of 20+ workers does not fit in memory. Tasks arrive sorted by sheet,
        # so each worker loads each sheet once.
        _W["sheets"].clear()
        with open(_W["files"][quad], "rb") as f:
            data = pickle.load(f)
        lines = data["lines"]
        geoms = [LineString(l) for l in lines]
        cells = [LineString(c) for c in data["cells"]]
        nums = _W["numbers"][quad]
        s = {"geoms": geoms, "tree": STRtree(geoms),
             "cells": cells, "ctree": STRtree(cells) if cells else None,
             "npts": [Point(x, y) for _, x, y in nums], "ntxt": [t for t, _, _ in nums]}
        s["ntree"] = STRtree(s["npts"])
        _W["sheets"][quad] = s
    return s


def faces_in_window(task):
    """The plot faces this window owns: (page-space ring, [numbers])."""
    quad, x0, y0 = task
    s = _sheet(quad)
    core = box(x0, y0, x0 + CORE_PT, y0 + CORE_PT)
    outer = box(x0 - MARGIN_PT, y0 - MARGIN_PT, x0 + CORE_PT + MARGIN_PT, y0 + CORE_PT + MARGIN_PT)
    idx = s["tree"].query(outer)
    if len(idx) == 0:
        return []
    nidx = s["ntree"].query(core)
    if len(nidx) == 0:
        return []
    clipped = []
    for i in idx:
        g = s["geoms"][int(i)].intersection(outer)
        if not g.is_empty:
            clipped.append(g)
    try:
        faces = list(polygonize(unary_union(clipped)))
    except Exception:
        return []
    edge = outer.exterior
    out = []
    ftree = STRtree(faces) if faces else None
    held = collections.defaultdict(list)
    for j in nidx:
        p = s["npts"][int(j)]
        for k in ftree.query(p, predicate="within"):
            held[int(k)].append(s["ntxt"][int(j)])
    for k, nums in held.items():
        f = faces[k]
        if len(nums) > MAX_NUMBERS_PER_FACE:
            continue
        if not core.contains(f.representative_point()) or f.intersects(edge):
            continue
        out.append((list(f.exterior.coords)[:-1], nums))
    return out


def face_for_record(task):
    """Second pass: the smallest numbered face round one page point, with the
    plan's cell fills added to its linework. Returns (page ring, numbers) or None."""
    quad, px, py = task
    s = _sheet(quad)
    pt = Point(px, py)
    outer = box(px - LOCAL_PT, py - LOCAL_PT, px + LOCAL_PT, py + LOCAL_PT)
    parts = [s["geoms"][int(i)].intersection(outer) for i in s["tree"].query(outer)]
    if s["ctree"] is not None:
        parts += [s["cells"][int(i)].intersection(outer) for i in s["ctree"].query(outer)]
    parts = [g for g in parts if not g.is_empty]
    if not parts:
        return None
    try:
        faces = [f for f in polygonize(unary_union(parts)) if f.contains(pt)]
    except Exception:
        return None
    if not faces:
        return None
    f = min(faces, key=lambda f: f.area)
    if f.intersects(outer.exterior):
        return None
    nums = [s["ntxt"][int(j)] for j in s["ntree"].query(f) if f.contains(s["npts"][int(j)])]
    if not nums or len(nums) > MAX_NUMBERS_PER_FACE:
        return None
    return list(f.exterior.coords)[:-1], nums


# ------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    ap.add_argument("--rings-cache", default=os.path.join(WORK, "rings.pkl"))
    ap.add_argument("--parcels", default=os.path.join(OUT, "parcels.geojson"))
    ap.add_argument("--records", default=os.path.join(OUT, "record_parcels.ndjson"))
    ap.add_argument("--only", help="limit to one sheet (NW/NE/SW/SE), for testing")
    ap.add_argument("--dry-run", action="store_true", help="report, do not write")
    args = ap.parse_args()

    blocks, _ = G.build_blocks()
    quads = [args.only] if args.only else list(blocks)

    # ---- keep the closed-ring originals, once, so this can be re-run ----
    keep_p = args.parcels.replace(".geojson", ".closed_rings.geojson")
    keep_r = args.records.replace(".ndjson", ".closed_rings.ndjson")
    if not args.dry_run:
        if not os.path.exists(keep_p):
            shutil.copyfile(args.parcels, keep_p)
        if not os.path.exists(keep_r):
            shutil.copyfile(args.records, keep_r)
    # always start from the closed-ring layer, so a re-run replaces the
    # polygonized plots instead of stacking a second copy on top
    src_p = keep_p if os.path.exists(keep_p) else args.parcels
    src_r = keep_r if os.path.exists(keep_r) else args.records

    # ---- the survey numbers, already read from the sheets ----
    with open(args.rings_cache, "rb") as f:
        cache = pickle.load(f)
    numbers = {q: cache[q]["numbers"] for q in quads}
    crops = {q: cache[q]["crop"] for q in quads}
    del cache

    # ---- the plot strokes, read once per sheet ----
    stroke_files, sizes = {}, {}
    for q in quads:
        p = os.path.join(WORK, f"plot_strokes_v2_{q}.pkl")
        if os.path.exists(p):
            with open(p, "rb") as f:
                sizes[q] = pickle.load(f)["size"]
        else:
            t0 = time.time()
            lines, cells, size = read_strokes(q, blocks[q].path)
            with open(p, "wb") as f:
                pickle.dump({"lines": lines, "cells": cells, "size": size}, f, protocol=4)
            sizes[q] = size
            print(f"[{q}] {len(lines):,} plot strokes, {len(cells):,} cell fills "
                  f"({time.time()-t0:.0f}s)", flush=True)
        stroke_files[q] = p

    # ---- polygonize, window by window ----
    tasks = []
    for q in quads:
        w, h = sizes[q]
        for gy in range(int(math.ceil(h / CORE_PT))):
            for gx in range(int(math.ceil(w / CORE_PT))):
                tasks.append((q, gx * CORE_PT, gy * CORE_PT))
    print(f"{len(tasks):,} windows on {args.workers} workers", flush=True)
    t0 = time.time()
    faces = []  # (quad, page ring, numbers)
    with mp.Pool(args.workers, initializer=_init, initargs=(stroke_files, numbers)) as pool:
        for i, (task, res) in enumerate(zip(tasks, pool.imap(faces_in_window, tasks, chunksize=4))):
            for ring, nums in res:
                faces.append((task[0], ring, nums))
            if (i + 1) % 500 == 0:
                print(f"  {i+1:,}/{len(tasks):,} windows, {len(faces):,} faces "
                      f"({time.time()-t0:.0f}s)", flush=True)
    print(f"{len(faces):,} numbered faces from the linework ({time.time()-t0:.0f}s)")

    # ---- onto the Earth ----
    cands = []
    for q, ring, nums in faces:
        b, crop = blocks[q], crops[q]
        area = abs(Polygon(ring).area) * b.s * b.s
        if not (PARCEL_MIN_AREA_M2 <= area <= PARCEL_MAX_AREA_M2):
            continue
        ground = [b.page_to_ground(x + crop.x0, crop.y1 - y) for x, y in ring]
        cands.append({"block": q, "ground": ground, "area_m2": area, "nums": nums,
                      "poly": Polygon(ground)})
    print(f"{len(cands):,} faces of plot size")

    # ---- the records, and the plots they already have ----
    recs = [json.loads(l) for l in open(src_r, encoding="utf-8")]
    pfc = json.load(open(src_p, encoding="utf-8"))
    by_pid = {f["properties"]["pid"]: f for f in pfc["features"]}
    old_polys = {pid: Polygon(f["geometry"]["coordinates"][0]) for pid, f in by_pid.items()}

    # ---- the check: where a closed ring exists, the face should match it ----
    ctree = STRtree([c["poly"] for c in cands])
    ious = []
    for r in recs:
        pid = r.get("pid")
        if pid is None or pid not in old_polys:
            continue
        pt = Point(r["lon"], r["lat"])
        for k in ctree.query(pt, predicate="within"):
            a, c = old_polys[pid], cands[int(k)]["poly"]
            if a.is_valid and c.is_valid:
                u = a.union(c).area
                if u > 0:
                    ious.append(a.intersection(c).area / u)
            break

    # ---- assign the unplaced records ----
    claimed = {}  # cand index -> new pid
    next_pid = max(by_pid) + 1 if by_pid else 0
    placed = placed2 = flagged = 0
    unplaced = 0
    left = []
    for r in recs:
        if r.get("pid") is not None:
            continue
        unplaced += 1
        if not_a_survey_number(r["t"]):
            r["not_survey_number"] = True
            flagged += 1
            continue
        pt = Point(r["lon"], r["lat"])
        hit = [int(k) for k in ctree.query(pt, predicate="within")]
        k = next((h for h in hit if h in claimed), None)
        if k is None and hit:
            # the same plot from two overlapping sheets: take the one already
            # claimed, else the smallest (the plot, not an undivided block)
            k = min(hit, key=lambda h: cands[h]["area_m2"])
            # a face of a plot that already exists (drawn twice) is not new
            rp = cands[k]["poly"].representative_point()
            if any(old_polys[p].contains(rp) for p in _near(old_polys, rp)):
                k = None
            else:
                claimed[k] = next_pid
                next_pid += 1
        if k is None:
            left.append(r)
            continue
        r["pid"] = claimed[k]
        r["area_m2"] = round(cands[k]["area_m2"], 1)
        placed += 1

    # ---- second pass: the cell fills, round each record still without a plot ----
    tasks, owners = [], []
    for r in left:
        for q in quads:
            b, crop = blocks[q], crops[q]
            ux, uy = b.ground_to_page(r["lon"], r["lat"])
            px, py = ux - crop.x0, crop.y1 - uy
            if 0 <= px <= crop.width and 0 <= py <= crop.height:
                tasks.append((q, px, py))
                owners.append(r)
                break
    order = sorted(range(len(tasks)), key=lambda i: tasks[i][0])
    tasks = [tasks[i] for i in order]
    owners = [owners[i] for i in order]
    print(f"second pass: {len(tasks):,} records, with the plan's cell fills", flush=True)
    seen = {}  # a face found from two records -> one plot
    with mp.Pool(min(args.workers, 6), initializer=_init, initargs=(stroke_files, numbers)) as pool:
        for (q, _, _), r, res in zip(tasks, owners, pool.imap(face_for_record, tasks, chunksize=8)):
            if r.get("pid") is not None or res is None:
                continue
            ring, nums = res
            b, crop = blocks[q], crops[q]
            area = abs(Polygon(ring).area) * b.s * b.s
            if not (PARCEL_MIN_AREA_M2 <= area <= PARCEL_MAX_AREA_M2):
                continue
            ground = [b.page_to_ground(x + crop.x0, crop.y1 - y) for x, y in ring]
            poly = Polygon(ground)
            if not poly.is_valid or not poly.contains(Point(r["lon"], r["lat"])):
                continue
            rp = poly.representative_point()
            if any(old_polys[p].contains(rp) for p in _near(old_polys, rp)):
                continue
            key = (round(rp.x, 6), round(rp.y, 6), round(area))
            k = seen.get(key)
            if k is None:
                cands.append({"block": q, "ground": ground, "area_m2": area, "nums": nums,
                              "poly": poly, "pass": 2})
                k = len(cands) - 1
                seen[key] = k
                claimed[k] = next_pid
                next_pid += 1
            r["pid"] = claimed[k]
            r["area_m2"] = round(area, 1)
            placed2 += 1

    still = [r for r in recs if r.get("pid") is None and not r.get("not_survey_number")]
    ious.sort()
    summary = {
        "faces_from_linework": len(faces),
        "faces_of_plot_size": len(cands),
        "records_without_a_plot_before": unplaced,
        "records_placed_now": placed + placed2,
        "placed_by_linework": placed,
        "placed_by_cell_fills": placed2,
        "flagged_not_a_survey_number": flagged,
        "records_still_without_a_plot": len(still),
        "new_plots": len(claimed),
        "check_against_closed_rings": {
            "pairs": len(ious),
            "iou_median": round(ious[len(ious) // 2], 4) if ious else None,
            "iou_p10": round(ious[len(ious) // 10], 4) if ious else None,
            "share_iou_over_0.9": round(sum(1 for v in ious if v > 0.9) / len(ious), 4) if ious else None,
        },
    }
    print(json.dumps(summary, indent=2))
    if args.dry_run:
        return

    # ---- write ----
    recs_by_pid = collections.defaultdict(list)
    for r in recs:
        if r.get("pid") is not None:
            recs_by_pid[r["pid"]].append(r["t"])
    for k, pid in claimed.items():
        c = cands[k]
        nums = recs_by_pid.get(pid, [])
        pfc["features"].append({
            "type": "Feature",
            "geometry": {"type": "Polygon",
                         "coordinates": [[[round(x, 7), round(y, 7)] for x, y in c["ground"]]]},
            "properties": {
                "pid": pid, "area_m2": round(c["area_m2"], 1), "block": c["block"],
                "vertices": len(c["ground"]), "numbers": len(nums),
                "survey": ",".join(sorted(set(nums))[:8]),
                "style": "polygonized-cells" if c.get("pass") == 2 else "polygonized",
            },
        })
    with open(args.parcels, "w", encoding="utf-8") as f:
        json.dump(pfc, f, separators=(",", ":"))
    with open(args.records, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
    with open(os.path.join(OUT, "polygonize_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"wrote {args.parcels} ({len(pfc['features']):,} plots) and {args.records}")


_NEAR = {}


def _near(polys, pt, cell=0.002):
    """The existing plots whose bbox could hold a point (a lazy grid index)."""
    if not _NEAR:
        for pid, p in polys.items():
            x0, y0, x1, y1 = p.bounds
            for gx in range(int(x0 // cell), int(x1 // cell) + 1):
                for gy in range(int(y0 // cell), int(y1 // cell) + 1):
                    _NEAR.setdefault((gx, gy), []).append(pid)
    return _NEAR.get((int(pt.x // cell), int(pt.y // cell)), ())


if __name__ == "__main__":
    main()
