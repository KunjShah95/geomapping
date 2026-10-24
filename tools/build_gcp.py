"""Build control points by matching place names on the scan to OSM.

The scan is a land-use plan with no georeference and no coordinates, but it
DOES carry place names: MOTERA, HANSOL, PINDHARADA, CHHALA, VASAN, SANAND...
At 4x OCR those read at confidence 1.00 (tools/ocr_hires.py).

Each name is looked up in Nominatim, restricted to the sheet's bbox. A match
is only accepted if the place is roughly where H0 already thinks it is - that
bound is generous (2 km, versus the ~365 m error being fixed) but it is enough
to reject the two failure modes that matter:

  * a name that Nominatim resolves somewhere else entirely (a different
    district with the same name)
  * a misread label that happens to be a real place name elsewhere

Accepted matches become work/gcp.csv, which affine_fit.py then fits.

The consistency check is also a DIAGNOSTIC: printing the H0-vs-OSM distance
for every match shows, before any fitting, whether H0 is off by a constant
offset (a translation error) or by a growing amount away from the centre (a
scale/anisotropy error). Those need different fixes.
"""
import argparse, json, os, re, time, unicodedata
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "work")
BL = (22.763306, 72.178109)
TR = (23.357406, 72.860675)
FULL_W, FULL_H = 27200, 25600

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "geomapping-gcp/1.0 (local research script)"})

# Words that are plan furniture, not places. Matched as whole words.
NOISE = set("""
SUR NO CHANG KOCHRAB CHHAD GUJARAT COLLEGE LAW GARDEN HOSPITAL RUPALI PART OF
REFER DETAIL PLAN DEVELOPMENT RIVER BRIDGE APPROACH SABARMATI VILLAGE GRAM TALUKA
DISTRICT SECTOR LAYOUT SCHOOL TEMPLE MARKET BUS STATION PETROL GAS SURVEY
NORTH SOUTH EAST WEST ADJOINING SHEET SCALE LEGEND ABOVE BELOW AREA ROAD TO FROM
IN AT NEW OLD GRAN NA MOTI SITE TAL UNG MOTA IS AS AN AND CHOWK BAZAR GHAT KUWA
BACK SIDE FRONT AIRPORT STADIUM LAKE UNIVERSITY INSTITUTE MKT VASNA SARKHEJ
BOPAL RING EXPRESS HIGHWAY SHELF FACTORY INDUSTRIAL RESIDENTIAL COMMERCIAL AGRI
OPEN ZONE COLONY APARTMENT BLOCK BOUNDARY NUMBER DATE REVISED DISCLAIMER AUDA
AHMEDABAD MASTER 2041 2022 2011 1966 TOTAL FOR WHERE ONLY OTHER REMAINING
PROPOSED EXISTING WATER RAILWAY DEPOT GIDC ESTATE SOCIETY STREET LANE PLOT
STREET CRIST JOHN MARY BESIDE NEAR OPP ACROSS PUBLIC PRIVATE GOVT CRS CROSS
TOTAL AREA BLOCK C T P E F K M R S N W
""".split())


def merc_y(lat):
    return math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


import math  # noqa: E402  (after merc_y for readability of the above)


def h0_lonlat(px, py):
    SX = FULL_W / (TR[1] - BL[1])
    SY = FULL_H / (merc_y(TR[0]) - merc_y(BL[0]))
    lon = BL[1] + px / SX
    lat = math.degrees(2 * math.atan(math.exp(math.radians(merc_y(TR[0]) - py / SY))) - math.pi / 2)
    return lon, lat


def norm(s):
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^A-Za-z ]", " ", s).upper()


def looks_like_place(key):
    if len(key) < 4 or len(key) > 26:
        return False
    if re.search(r"\d", key):
        return False
    words = key.split()
    if len(words) > 3:
        return False
    for w in words:
        if len(w) < 3:
            return False
        if w in NOISE:
            return False
    # must be mostly A-Z letters
    letters = re.sub(r"[^A-Za-z]", "", key)
    if len(letters) < 4:
        return False
    if not re.fullmatch(r"[A-Z ]+", key):
        return False
    # A real place name is a single word, or two words where the second is a
    # known qualifier. Three-word strings are almost always a misread of a
    # longer label, and the OCR frequently produces them.
    if len(words) == 3 and words[-1] not in ("NO", "SUR", "BL"):
        return False
    return True


def geocode(q):
    params = {"q": q, "format": "jsonv2", "limit": 6, "countrycodes": "in",
              "viewbox": f"{BL[1]},{TR[0]},{TR[1]},{BL[0]}", "bounded": 1,
              "addressdetails": 0}
    r = SESSION.get("https://nominatim.openstreetmap.org/search", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ocr", default=os.path.join(WORK, "ocr4.ndjson"))
    ap.add_argument("--out", default=os.path.join(WORK, "gcp.csv"))
    ap.add_argument("--report", default=os.path.join(WORK, "gcp_report.json"))
    ap.add_argument("--min-h", type=float, default=14.0, help="min glyph height for a place label")
    ap.add_argument("--max-dist-m", type=float, default=2000.0,
                    help="how far the OSM place may sit from H0's guess for the match to count")
    ap.add_argument("--min-occurrences", type=int, default=1)
    a = ap.parse_args()

    occ = {}
    with open(a.ocr, encoding="utf-8") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("h", 0) < a.min_h:
                continue
            key = norm(r["t"]).strip()
            key = re.sub(r"\s+", " ", key)
            if not looks_like_place(key):
                continue
            occ.setdefault(key, []).append((r["px"], r["py"], r.get("s", 0.0), r.get("h", 0)))

    cands = [(k, v) for k, v in occ.items() if len(v) >= a.min_occurrences]
    cands.sort(key=lambda kv: -max(h for _, _, _, h in kv[1]))
    print(f"{len(occ)} place-name candidates, {len(cands)} to look up")

    rows = []
    rejected = []
    for key, lst in cands:
        try:
            hits = geocode(key)
        except Exception as e:
            print(f"  {key}: lookup failed ({e})")
            time.sleep(2)
            continue
        time.sleep(1.15)     # Nominatim rate limit

        inside = [h for h in hits
                  if BL[0] <= float(h["lat"]) <= TR[0] and BL[1] <= float(h["lon"]) <= TR[1]]
        if not inside:
            continue

        # the label's own pixel position, and where H0 puts it
        xs = sorted(p[0] for p in lst)
        ys = sorted(p[1] for p in lst)
        px = xs[len(xs) // 2]
        py = ys[len(ys) // 2]
        h0lon, h0lat = h0_lonlat(px, py)

        # pick the OSM hit closest to the label's H0 position
        def d(h):
            return math.hypot((float(h["lon"]) - h0lon) * 102500.0,
                              (float(h["lat"]) - h0lat) * 110574.0)
        inside.sort(key=d)
        h = inside[0]
        dist = d(h)
        if dist > a.max_dist_m:
            rejected.append(dict(name=key, dist_m=round(dist, 1),
                                 osm=[h["lat"], h["lon"]]))
            continue

        rows.append(dict(px=px, py=py, lon=float(h["lon"]), lat=float(h["lat"]),
                         name=key.lower().replace(" ", "_"),
                         h0_dist_m=round(dist, 1), n=len(lst),
                         osm_type=h.get("type", "")))
        print(f"  {key:24s} ({px:6d},{py:6d})  OSM {float(h['lat']):.5f},{float(h['lon']):.5f}"
              f"  H0 off by {dist:7.0f} m   x{len(lst)}  [{h.get('type','')}]")

    # spread check: a fit needs points across the sheet, not a cluster
    if rows:
        xs = [r["px"] for r in rows]
        ys = [r["py"] for r in rows]
        print(f"\nspread: px {min(xs)}..{max(xs)}  py {min(ys)}..{max(ys)}")
        print(f"  (sheet is 0..{FULL_W} x 0..{FULL_H})")

    import csv
    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["px", "py", "lon", "lat", "name"])
        for r in rows:
            w.writerow([r["px"], r["py"], f"{r['lon']:.7f}", f"{r['lat']:.7f}", r["name"]])
    print(f"\nwrote {len(rows)} control points -> {a.out}")

    with open(a.report, "w", encoding="utf-8") as fh:
        json.dump(dict(accepted=rows, rejected=rejected), fh, indent=2)
    print(f"wrote {a.report} ({len(rejected)} rejected as inconsistent)")


if __name__ == "__main__":
    main()
