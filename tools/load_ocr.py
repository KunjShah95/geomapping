"""Load OCR numbers from work/plots.ndjson into the PostGIS `ocr_numbers` table.

Requires DATABASE_URL and psycopg. Populates only OCR-derived data - `plots` stays
empty on purpose, because the PDF carries no parcel attributes.

    set DATABASE_URL=postgres://...
    python tools/load_ocr.py --dry-run
    python tools/load_ocr.py
"""
import argparse, json, math, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "work")

ZONES = [
    ("RES", "Residential", "#f5e93a"),
    ("OPEN", "Open / agricultural", "#2fa84f"),
    ("INST", "Institutional / public", "#a86fd6"),
    ("COM", "Commercial / mixed", "#ef8a1f"),
    ("IND", "Industrial", "#d9342b"),
    ("WATER", "Water / open", "#7fc8e8"),
    ("NONE", "Unzoned / paper", None),
]

# hue bands, kept in step with tools/make_geojson.py: classify_zone()
BAND = {
    "RES": lambda d: 48 <= d < 78,
    "IND": lambda d: d < 12 or d >= 345,
    "COM": lambda d: 12 <= d < 48,
    "OPEN": lambda d: 78 <= d < 175,
    "WATER": lambda d: 175 <= d < 205,
    "INST": lambda d: 205 <= d < 345,
}


def zone_for(rgb):
    import colorsys
    r, g, b = (v / 255.0 for v in rgb)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if s < 0.12:
        return "NONE"
    deg = h * 360.0
    for code, test in BAND.items():
        if test(deg):
            return code
    return "NONE"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.join(WORK, "plots.ndjson"))
    ap.add_argument("--sheet", default="auda-rdp-2022")
    ap.add_argument("--min-conf", type=float, default=0.45)
    ap.add_argument("--batch", type=int, default=2000)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    # Prefer the enriched GeoJSON: it already carries the sampled zone colour and
    # de-duplication, so we do not re-derive either here.
    geo = os.path.join(ROOT, "data", "plots.geojson")
    if os.path.exists(geo):
        with open(geo, encoding="utf-8") as fh:
            g = json.load(fh)
        rows = []
        for f in g["features"]:
            p = f["properties"]
            if p["s"] < a.min_conf:
                continue
            lon, lat = f["geometry"]["coordinates"]
            rows.append({
                "t": p["t"], "s": p["s"], "lon": lon, "lat": lat,
                "px": p.get("px"), "py": p.get("py"), "rot": p.get("r", 0),
                "z_hex": p.get("c"), "z": p.get("z"),
            })
        dupes = 0
        print(f"loaded {len(rows)} rows from {os.path.relpath(geo, ROOT)}")
    else:
        rows, seen = [], set()
        dupes = 0
        with open(a.src, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r["s"] < a.min_conf:
                    continue
                key = (r["t"], round(r["lon"], 6), round(r["lat"], 6))
                if key in seen:
                    dupes += 1
                    continue
                seen.add(key)
                rows.append(r)
        print(f"{len(rows)} rows from ndjson, {dupes} duplicates dropped")

    counts = {}
    for r in rows:
        counts[r.get("z", "?")] = counts.get(r.get("z", "?"), 0) + 1
    print(f"{len(rows)} rows (conf >= {a.min_conf})")
    for z, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {n:7d}  {z}")

    if a.dry_run:
        return

    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set")
    try:
        import psycopg
    except ImportError:
        sys.exit("psycopg is required: pip install 'psycopg[binary]'")

    R = 6378137.0
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        # extent + resolution come from data/bounds.json - the same artefact the
        # viewer and tile builder read, never re-derived from constants here.
        with open(os.path.join(ROOT, "data", "bounds.json", encoding="utf-8"), encoding="utf-8") as fh:
            b = json.load(fh)
        R = 6378137.0
        mx = lambda lon: lon * math.pi / 180.0 * R
        my = lambda lat: math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * R
        x0, x1 = mx(b["lon_min"]), mx(b["lon_max"])
        y0, y1 = my(b["lat_min"]), my(b["lat_max"])
        cur.execute("""
            INSERT INTO sheets (id, title, extent, m_per_px, native_zoom, accuracy_note)
            VALUES (%s, %s, ST_MakeEnvelope(%s,%s,%s,%s,3857), %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                extent = EXCLUDED.extent,
                m_per_px = EXCLUDED.m_per_px,
                accuracy_note = EXCLUDED.accuracy_note
        """, (
            a.sheet, b.get("title", "AUDA Revised Development Plan 2022"),
            x0, y0, x1, y1,
            b["resolution"]["m_per_px"], b["resolution"]["native_zoom"],
            b["accuracy"]["caveat"],
        ))
        for code, name, colour in ZONES:
            cur.execute("""
                INSERT INTO land_use_zones (code, name, colour) VALUES (%s,%s,%s)
                ON CONFLICT (code) DO NOTHING
            """, (code, name, colour))

        cur.execute("DELETE FROM ocr_numbers WHERE sheet_id = %s", (a.sheet,))

        # lon/lat -> EPSG:3857 (the column is 3857; PostGIS will not guess)
        def proj(lon, lat):
            return (lon * math.pi / 180.0 * R,
                    math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) * R)

        batch = []
        for r in rows:
            code = "NONE"
            if r.get("z_hex"):
                try:
                    rgb = (int(r["z_hex"][1:3], 16), int(r["z_hex"][3:5], 16),
                           int(r["z_hex"][5:7], 16))
                    code = zone_for(rgb)
                except ValueError:
                    code = "NONE"
            px_, py_ = proj(r["lon"], r["lat"])
            batch.append((
                a.sheet, r["t"], " ".join(r["t"].split()), r["s"],
                code, r.get("z_hex"), r["px"], r["py"], r.get("rot", 0),
                px_, py_,
            ))
            if len(batch) >= a.batch:
                _flush(cur, batch)
                batch = []
        if batch:
            _flush(cur, batch)

    print(f"loaded into {a.sheet}")


def _flush(cur, batch):
    cur.executemany("""
        INSERT INTO ocr_numbers
            (sheet_id, raw_text, norm_text, confidence, zone_id, zone_colour,
             src_px, src_py, rotation_deg, geom)
        VALUES (%s,%s,%s,%s,
                (SELECT id FROM land_use_zones WHERE code = %s),
                (SELECT colour FROM land_use_zones WHERE code = %s),
                %s,%s,%s, ST_SetSRID(ST_MakePoint(%s,%s),3857))
    """, batch)


if __name__ == "__main__":
    main()
