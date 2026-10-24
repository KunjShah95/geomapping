"""Join the survey records to their plot boundaries, and to the plan's own
village and scheme boundaries.

This is the deliverable the client asked for: not "a number on a map", but
"this number, in this plot, in this village, zoned this way, with this area".

Three joins, all by containment:

  1. record -> PLOT. The smallest closed ring that contains the record's
     position. That is the plot the number is printed in.

  2. record -> VILLAGE / SCHEME. The plan's own thick-stroke unit
     boundaries contain the plan's own place names, so a record can be
     placed in a named village by containment rather than by the
     "nearest place name within 4 km" guess that records.ndjson uses.

  3. record -> LAND USE ZONE. The plan's legend class at the record's
     position, read from the colour masks (as records.ndjson does).

Output: data/dp2021/records_bounded.ndjson  the joined records
        data/dp2021/details/15/{x}/{y}.json  per-tile record details, for
                                    the viewer to fetch on demand
        data/dp2021/parcel_index.json      a compact index for the viewer
        work/newdp/boundary_report.md      the tables
"""
import argparse
import collections
import json
import math
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dp_georef as G  # noqa: E402
import dp_zone_masks as ZM  # noqa: E402

ROOT = G.ROOT
OUT = os.path.join(ROOT, "data", "dp2021")
ZONE_DIR = os.path.join(ROOT, "work", "newdp", "zones")


def grid_from_features(feats, cell=0.0004):
    """Bucket index over polygon features, for point-in-polygon lookups."""
    b = collections.defaultdict(list)
    for i, f in enumerate(feats):
        g = f["geometry"]
        polys = ([g["coordinates"]] if g["type"] == "Polygon"
                 else g["coordinates"])
        for poly in polys:
            for ring in poly:
                xs = [p[0] for p in ring]
                ys = [p[1] for p in ring]
                for cx in range(int(min(xs) // cell), int(max(xs) // cell) + 1):
                    for cy in range(int(min(ys) // cell), int(max(ys) // cell) + 1):
                        b[(cx, cy)].append(i)
    return b


def point_in_ring(pt, ring):
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


def inside_any(pt, feat):
    g = feat["geometry"]
    polys = ([g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"])
    for poly in polys:
        if point_in_ring(pt, poly[0]):
            return True
    return False


def write_unit_counts(units_path, unit_recs):
    """Put the record count on each unit boundary, and refresh the index."""
    with open(units_path, encoding="utf-8") as f:
        fc = json.load(f)
    for i, feat in enumerate(fc["features"]):
        feat["properties"]["records"] = unit_recs.get(i, 0)
    with open(units_path, "w", encoding="utf-8") as f:
        json.dump(fc, f, separators=(",", ":"))
    print(f"wrote the record counts onto {units_path}")

    # the named units, for the viewer's search box
    by_name = {}
    for f in fc["features"]:
        p = f["properties"]
        if p["type"] not in ("village", "scheme") or not p["name"]:
            continue
        e = by_name.setdefault(p["name"], {
            "type": p["type"], "n": 0, "bbox": None, "blocks": []})
        e["n"] += p.get("records", 0)
        ring = f["geometry"]["coordinates"][0]
        xs = [q[0] for q in ring]
        ys = [q[1] for q in ring]
        if e["bbox"] is None:
            e["bbox"] = [min(xs), min(ys), max(xs), max(ys)]
        else:
            e["bbox"][0] = min(e["bbox"][0], min(xs))
            e["bbox"][1] = min(e["bbox"][1], min(ys))
            e["bbox"][2] = max(e["bbox"][2], max(xs))
            e["bbox"][3] = max(e["bbox"][3], max(ys))
        if p["block"] not in e["blocks"]:
            e["blocks"].append(p["block"])
    p = os.path.join(os.path.dirname(units_path), "village_units.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(by_name, f, separators=(",", ":"))
    print(f"wrote {p}  ({len(by_name):,} named units)")


def lon2px(lon, z):
    return (lon + 180.0) / 360.0 * (256 * 2 ** z)


def lat2py(lat, z):
    la = math.radians(lat)
    return (1.0 - math.log(math.tan(la) + 1.0 / math.cos(la)) / math.pi) / 2.0 * (256 * 2 ** z)


def write_detail_tiles(recs, outdir, z=15):
    """The record details, bucketed by z15 tile, for lazy loading."""
    buckets = collections.defaultdict(list)
    for r in recs:
        x = int(lon2px(r["lon"], z) // 256)
        y = int(lat2py(r["lat"], z) // 256)
        buckets[(x, y)].append({
            "t": r["t"], "lo": r["lon"], "la": r["lat"],
            "pid": r.get("parcel"), "a": r.get("parcel_area_m2"),
            "v": r.get("village_unit") or "", "s": r.get("scheme") or "",
            "z": r.get("zone") or "", "b": r.get("block"),
        })
    total = 0
    for (x, y), items in sorted(buckets.items()):
        d = os.path.join(outdir, str(z), str(x))
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, f"{y}.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(items, f, separators=(",", ":"))
        total += os.path.getsize(p)
    print(f"wrote {outdir}/{z}: {len(buckets):,} tiles, {total/1e6:.1f} MB")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parcels", default=os.path.join(OUT, "parcels.geojson"))
    ap.add_argument("--units", default=os.path.join(OUT, "units.geojson"))
    ap.add_argument("--records", default=os.path.join(OUT, "records.ndjson"))
    ap.add_argument("--out", default=os.path.join(OUT, "records_bounded.ndjson"))
    ap.add_argument("--index", default=os.path.join(OUT, "parcel_index.json"))
    ap.add_argument("--report", default=os.path.join(ROOT, "work", "newdp", "boundary_report.md"))
    args = ap.parse_args()

    t0 = time.time()
    parcels = json.load(open(args.parcels, encoding="utf-8"))["features"]
    units = json.load(open(args.units, encoding="utf-8"))["features"]
    print(f"{len(parcels):,} parcels, {len(units):,} unit boundaries "
          f"({time.time()-t0:.0f}s)")

    # the plan's own land-use classes, per sheet
    blocks, _ = G.build_blocks()
    classes = ZM.legend_classes()
    zone_names = [c["label"] for c in classes]
    zones = {}
    for q, b in blocks.items():
        npz = np.load(os.path.join(ZONE_DIR, f"{q}.npz"))
        # the crop box is what the masks were rendered against
        import fitz
        doc = fitz.open(b.path)
        crop = doc[0].cropbox
        doc.close()
        zones[q] = {"labels": npz["labels"], "zoom": float(npz["zoom"][0]),
                    "crop": crop, "block": b}
    print("land-use masks loaded")

    recs = []
    with open(args.records, encoding="utf-8") as f:
        for line in f:
            recs.append(json.loads(line))
    print(f"{len(recs):,} records")

    pgrid = grid_from_features(parcels)
    ugrid = grid_from_features(units)
    print(f"indexes: {len(pgrid):,} parcel cells, {len(ugrid):,} unit cells")

    # the parcel a record falls in: the smallest containing one
    p_area = np.array([f["properties"].get("area_m2", 0.0) for f in parcels])

    by_zone = collections.Counter()
    by_village = collections.Counter()
    by_scheme = collections.Counter()
    in_parcel = 0
    in_village = 0
    in_scheme = 0
    rec_parcel = collections.defaultdict(list)
    parcel_recs = collections.defaultdict(list)
    # how many records fall inside each unit boundary. A unit encloses its
    # plots, so a record is inside every unit that contains it - the count
    # is "how many records does this boundary hold", not "how many did this
    # boundary win".
    unit_recs = collections.Counter()

    t0 = time.time()
    for n, r in enumerate(recs):
        lon, lat = r["lon"], r["lat"]

        # --- the plot
        cands = [i for i in pgrid.get((int(lon // 0.0004), int(lat // 0.0004)), ())
                 if inside_any((lon, lat), parcels[i])]
        pid = None
        if cands:
            pid = int(min(cands, key=lambda i: p_area[i]))
            in_parcel += 1
            parcel_recs[pid].append(r["t"])

        # --- the plan's own unit boundary
        # A record can sit inside more than one (a plot inside a village
        # inside a scheme). The SMALLEST is the most specific, so it wins.
        village = scheme = ""
        best_v_area = best_s_area = None
        for i in ugrid.get((int(lon // 0.0004), int(lat // 0.0004)), ()):
            if not inside_any((lon, lat), units[i]):
                continue
            unit_recs[i] += 1
            p = units[i]["properties"]
            area = p.get("area_km2") or 0.0
            if p.get("type") == "village" and p.get("name"):
                if best_v_area is None or area < best_v_area:
                    best_v_area = area
                    village = p["name"]
            elif p.get("type") == "scheme" and p.get("name"):
                if best_s_area is None or area < best_s_area:
                    best_s_area = area
                    scheme = p["name"]

        # --- the land use, read from the colour mask
        zone = ""
        q = r.get("block")
        z = zones.get(q)
        if z:
            b = z["block"]
            ux, uy = b.ground_to_page(lon, lat)
            col = (ux - z["crop"].x0) * z["zoom"]
            row = (z["crop"].y1 - uy) * z["zoom"]
            zl = z["labels"]
            r0, r1 = max(0, int(row) - 5), min(zl.shape[0], int(row) + 6)
            c0, c1 = max(0, int(col) - 5), min(zl.shape[1], int(col) + 6)
            if r1 > r0 and c1 > c0:
                win = zl[r0:r1, c0:c1].ravel()
                win = win[win >= 0]
                if len(win) >= 6:
                    zone = zone_names[int(np.bincount(win).argmax())]

        r["parcel"] = pid
        r["parcel_area_m2"] = round(float(p_area[pid]), 1) if pid is not None else None
        r["village_unit"] = village
        r["scheme"] = scheme
        if zone:
            r["zone"] = zone
        if village:
            in_village += 1
            by_village[village] += 1
        if scheme:
            in_scheme += 1
            by_scheme[scheme] += 1
        if zone:
            by_zone[zone] += 1
        if n % 50000 == 0:
            print(f"    {n:,} records ({time.time()-t0:.0f}s)", flush=True)

    with open(args.out, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
    print(f"\nwrote {args.out}")

    # the record counts belong on the unit boundaries themselves
    write_unit_counts(args.units, unit_recs)

    # per-tile record details, so the viewer can fetch just what it needs
    # when a record is clicked
    write_detail_tiles(recs, os.path.join(OUT, "details"), 15)

    summary = {
        "records": len(recs),
        "in_a_plot_boundary": in_parcel,
        "in_a_named_village_boundary": in_village,
        "in_a_scheme_boundary": in_scheme,
        "parcels_used": len(parcel_recs),
        "by_village": dict(by_village.most_common()),
        "by_scheme": dict(by_scheme.most_common()),
        "by_zone": dict(by_zone.most_common()),
        "records_per_parcel": dict(collections.Counter(
            len(v) for v in parcel_recs.values()).most_common()),
    }
    with open(os.path.join(OUT, "boundary_join_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print("wrote boundary_join_summary.json")

    # a compact index for the viewer: the parcels that hold records
    idx = {}
    for pid, nums in parcel_recs.items():
        p = parcels[pid]["properties"]
        idx[str(pid)] = {
            "t": sorted(set(nums))[:6],
            "n": len(nums),
            "a": p.get("area_m2"),
            "b": p.get("block"),
        }
    with open(args.index, "w", encoding="utf-8") as f:
        json.dump(idx, f, separators=(",", ":"))
    print(f"wrote {args.index}  ({os.path.getsize(args.index)/1e6:.2f} MB)")

    # the report
    md = ["# Records, plots and boundaries", "",
          "Every survey number on the plan, placed in the plot it is printed "
          "in, and in the plan's own village and scheme boundaries.", "",
          "| | count | of |", "| --- | ---: | ---: |",
          f"| records | {len(recs):,} | |",
          f"| in a plot boundary | {in_parcel:,} | {100*in_parcel/len(recs):.1f}% |",
          f"| in a named village boundary | {in_village:,} | {100*in_village/len(recs):.1f}% |",
          f"| in a scheme boundary | {in_scheme:,} | {100*in_scheme/len(recs):.1f}% |",
          f"| distinct plots | {len(parcel_recs):,} | |", ""]
    md += ["## Records per plot", "",
           "| numbers in the plot | plots |", "| ---: | ---: |"]
    for k, v in sorted(summary["records_per_parcel"].items(),
                       key=lambda kv: -int(kv[0]))[:12]:
        md.append(f"| {k} | {v:,} |")
    md += ["", "## By village (the plan's own boundary)", "",
           "| village | records |", "| --- | ---: |"]
    for v, c in list(summary["by_village"].items())[:40]:
        md.append(f"| {v} | {c:,} |")
    md += ["", "## By land-use zone", "", "| zone | records |", "| --- | ---: |"]
    for v, c in sorted(summary["by_zone"].items(), key=lambda kv: -kv[1]):
        md.append(f"| {v} | {c:,} |")
    md.append("")
    os.makedirs(os.path.dirname(args.report), exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print("wrote", args.report)


if __name__ == "__main__":
    main()
