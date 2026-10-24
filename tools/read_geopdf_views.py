"""Read the GeoPDF viewport (/VP -> /Measure -> /GPTS) from every PDF using pikepdf.

pikepdf resolves objects inside object streams, which PyMuPDF does not expose.
Result: for each file, the ground quad (lon/lat) plus the PDF-space bbox and matrix,
i.e. a full affine ground->page transform.
"""
import pikepdf, os, json, re, sys, math, time

BASE = r"C:\ideabot internships\geomapping\NEW DP BOOK AUDA"


def gp_to_quad(gpts):
    """/GPTS [ llx lly urx ury urx uly llx uly ] -> list of 4 (lon,lat) in page order."""
    v = [float(x) for x in gpts]
    return [
        (v[0], v[1]),  # lower-left
        (v[2], v[3]),  # upper-right
        (v[4], v[5]),  # upper-left
        (v[6], v[7]),  # lower-left (repeat, top edge start)
    ]


def read_file(path):
    out = {"file": os.path.relpath(path, BASE), "views": []}
    try:
        pdf = pikepdf.open(path)
    except Exception as e:
        out["error"] = str(e)
        return out
    out["pages"] = len(pdf.pages)
    for i, page in enumerate(pdf.pages):
        try:
            vps = page.obj.get("/VP")
        except Exception:
            vps = None
        if vps is None:
            continue
        for vp in vps:
            rec = {"page": i + 1, "bbox": list(map(float, vp.get("/BBox", [0, 0, 0, 0])))}
            mat = vp.get("/Matrix")
            rec["matrix"] = [float(x) for x in mat] if mat is not None else None
            meas = vp.get("/Measure")
            if meas is not None:
                gpts = meas.get("/GPTS")
                if gpts is not None:
                    nums = [float(x) for x in gpts]
                    rec["gpts"] = nums
                    rec["gcs_wkt"] = _wkt(pdf, meas.get("/GCS"))
                    rec["bounds"] = [float(x) for x in meas.get("/Bounds", [])]
                else:
                    rec["gpts"] = None
            else:
                rec["gpts"] = None
                rec["measure_missing"] = True
            out["views"].append(rec)
    pdf.close()
    return out


def _wkt(pdf, gcs):
    if gcs is None:
        return ""
    try:
        w = gcs.get("/WKT")
        if w is not None:
            return str(w)
    except Exception:
        pass
    return ""


def main():
    t0 = time.time()
    files = []
    for dp, dn, fn in os.walk(BASE):
        for f in fn:
            if f.lower().endswith(".pdf"):
                files.append(os.path.join(dp, f))
    files.sort()
    rows = []
    for p in files:
        r = read_file(p)
        rows.append(r)
        if len(rows) % 50 == 0:
            print(f"  ...{len(rows)}/{len(files)} {time.time()-t0:.0f}s", flush=True)
    dest = os.path.join(BASE, "..", "work", "geopdf_views.json")
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, default=str)
    print(f"\nwrote {dest}  ({time.time()-t0:.0f}s)")

    withgeo = [r for r in rows if r.get("views")]
    print(f"files with a /VP view: {len(withgeo)} / {len(rows)}")
    nogeo = [r["file"] for r in rows if not r.get("views") and not r.get("error")]
    print(f"files without any view ({len(nogeo)}):")
    import collections
    fam = collections.Counter(os.path.dirname(x) for x in nogeo)
    for k, v in fam.items():
        print(f"   {k}: {v}")
    for r in withgeo[:5]:
        print(json.dumps(r, indent=2)[:900])


if __name__ == "__main__":
    main()
