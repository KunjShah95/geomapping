"""Export AUDA DP 2021 survey records to Google Earth KML/KMZ (non-DHS only)."""
import argparse, json, os, zipfile, sys
from xml.sax.saxutils import escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dp_georef as G

ROOT = G.ROOT
OUT = os.path.join(ROOT, "data", "dp2021")
VIL_DIR = os.path.join(OUT, "villages")
GEO_DIR = os.path.join(OUT, "google_earth")


def load_villages():
    idx = json.load(open(os.path.join(VIL_DIR, "index.json"), encoding="utf-8"))
    names = []
    for q in ("NW", "NE", "SW", "SE"):
        names.extend(idx["by_block"].get(q, []))
    V = {}
    for n in names:
        p = os.path.join(VIL_DIR, n.replace("/", "-") + ".json")
        if os.path.exists(p):
            V[n] = json.load(open(p, encoding="utf-8"))
    return V


def in_bbox(pt, v):
    return (v["lon_min"] <= pt[0] <= v["lon_max"] and
            v["lat_min"] <= pt[1] <= v["lat_max"])


def choose_village(pt, villages):
    hits = [n for n, v in villages.items() if in_bbox(pt, v)]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        def area(n):
            v = villages[n]
            return (v["lon_max"]-v["lon_min"])*(v["lat_max"]-v["lat_min"])
        return min(hits, key=area)
    return None


def write_plots_kml(path, plots, villages, skip_village=None, skip_bbox=None):
    with open(path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<kml xmlns="http://www.opengis.net/kml/2.2">\n')
        f.write('  <Document>\n')
        f.write('    <name>AUDA DP 2021 - Survey Records (non-DHS)</name>\n')
        f.write('    <Style id="plotStyle"><IconStyle><scale>0.55</scale>'
                '<Icon><href>http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png</href></Icon></IconStyle>'
                '<LabelStyle><scale>0.6</scale></LabelStyle></Style>\n')
        f.write('    <Folder><name>Survey numbers</name>\n')
        n = 0; skipped = 0
        for feat in plots["features"]:
            pt = feat["geometry"]["coordinates"]
            props = feat["properties"]
            if props.get("k") != "plot":
                continue
            if skip_bbox and in_bbox(pt, skip_bbox):
                skipped += 1
                continue
            vill = choose_village(pt, villages)
            if vill == skip_village:
                skipped += 1
                continue
            label = escape(str(props.get("t", "")))
            block = (villages[vill]["block"] if (vill and vill in villages) else "")
            desc = escape(f"Village: {vill or 'unassigned'}  |  sheet: {block}")
            f.write('      <Placemark>\n')
            f.write(f'        <name>{label}</name>\n')
            f.write(f'        <description>{desc}</description>\n')
            f.write('        <styleUrl>#plotStyle</styleUrl>\n')
            f.write('        <Point><coordinates>'
                    f'{pt[0]:.8f},{pt[1]:.8f},0</coordinates></Point>\n')
            f.write('      </Placemark>\n')
            n += 1
        f.write('    </Folder>\n')
        f.write('  </Document>\n</kml>\n')
    return n, skipped


def box_coords(v):
    return [(v["lon_min"], v["lat_min"]), (v["lon_max"], v["lat_min"]),
            (v["lon_max"], v["lat_max"]), (v["lon_min"], v["lat_max"]),
            (v["lon_min"], v["lat_min"])]


def write_villages_kml(path, villages, skip_village=None):
    with open(path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<kml xmlns="http://www.opengis.net/kml/2.2">\n')
        f.write('  <Document>\n')
        f.write('    <name>AUDA DP 2021 - Non-DHS Village extents</name>\n')
        f.write('    <Style id="polyStyle"><PolyStyle><fill>0</fill>'
                '<outline>1</outline><color>ff00ffff</color></PolyStyle>'
                '<LineStyle><width>2</width></LineStyle></Style>\n')
        f.write('    <Folder><name>Villages</name>\n')
        n = 0
        for name, v in sorted(villages.items()):
            if name == skip_village:
                continue
            f.write('      <Placemark>\n')
            f.write(f'        <name>{escape(name)}</name>\n')
            f.write(f'        <description>Block {v["block"]} | '
                    f'{v["width_km"]} x {v["height_km"]} km | '
                    f'{v["matched_place_names"]} matched place names</description>\n')
            f.write('        <styleUrl>#polyStyle</styleUrl>\n')
            f.write('        <Polygon><outerBoundaryIs><LinearRing><coordinates>\n')
            for lon, lat in box_coords(v):
                f.write(f'          {lon:.8f},{lat:.8f},0\n')
            f.write('        </coordinates></LinearRing></outerBoundaryIs></Polygon>\n')
            f.write('      </Placemark>\n')
            n += 1
        f.write('    </Folder>\n')
        f.write('  </Document>\n</kml>\n')
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip", default="DHSNAP")
    ap.add_argument("--plots", default=os.path.join(OUT, "plots.geojson"))
    ap.add_argument("--out", default=GEO_DIR)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    villages = load_villages()
    print(f"loaded {len(villages)} villages")
    plots = json.load(open(args.plots, encoding="utf-8"))
    skip_v = villages.get(args.skip)
    n1, sk = write_plots_kml(os.path.join(args.out, "non_dhs_plots.kml"),
                             plots, villages, skip_village=args.skip, skip_bbox=skip_v)
    print(f"plot placemarks: {n1}, skipped (DHS/extent): {sk}")
    n2 = write_villages_kml(os.path.join(args.out, "villages.kml"),
                             villages, skip_village=args.skip)
    print(f"village polygons: {n2}")
    kmz = os.path.join(args.out, "auda_dp2021.kmz")
    with zipfile.ZipFile(kmz, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(os.path.join(args.out, "non_dhs_plots.kml"), "non_dhs_plots.kml")
        z.write(os.path.join(args.out, "villages.kml"), "villages.kml")
    print(f"kmz: {kmz}")


if __name__ == "__main__":
    main()
