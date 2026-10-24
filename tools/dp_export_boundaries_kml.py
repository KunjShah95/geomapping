"""Export the plot boundaries and the records to Google Earth.

The client reviews the plan in Google Earth, so the deliverable has to open
there. This writes:

  * the plot boundaries, one KML folder per land-use zone, so the client can
    switch zones on and off and see them against the satellite;
  * the survey records, as points, one folder per village;
  * the plan's own village and scheme boundaries;
  * a KMZ bundle of all of it.

Output: data/dp2021/google_earth/auda_dp2021_plots.kmz
        data/dp2021/google_earth/plots.kml
        data/dp2021/google_earth/records.kml
        data/dp2021/google_earth/units.kml
"""
import argparse
import json
import os
import sys
import zipfile
from collections import defaultdict
from xml.sax.saxutils import escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dp_georef as G  # noqa: E402

ROOT = G.ROOT
OUT = os.path.join(ROOT, "data", "dp2021")
GEO_DIR = os.path.join(OUT, "google_earth")

KML_HEAD = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<kml xmlns="http://www.opengis.net/kml/2.2">\n  <Document>\n')
KML_TAIL = '  </Document>\n</kml>\n'


def style_defs():
    return """    <Style id="parcel"><LineStyle><color>ff00b3ff</color><width>1</width></LineStyle>
      <PolyStyle><fill>0</fill><outline>1</outline></PolyStyle></Style>
    <Style id="unit"><LineStyle><color>ff9c27b0</color><width>3</width></LineStyle>
      <PolyStyle><fill>0</fill><outline>1</outline></PolyStyle></Style>
    <Style id="rec"><IconStyle><scale>0.55</scale>
      <Icon><href>http://maps.google.com/mapfiles/kml/shapes/placemark_circle.png</href></Icon></IconStyle>
      <LabelStyle><scale>0.6</scale></LabelStyle></Style>
"""


def kml_polygon(ring, style="parcel"):
    out = ['      <Placemark>\n        <styleUrl>#%s</styleUrl>\n' % style,
           '        <Polygon><outerBoundaryIs><LinearRing><coordinates>\n']
    for lon, lat in ring:
        out.append('          %.8f,%.8f,0\n' % (lon, lat))
    out.append('        </coordinates></LinearRing></outerBoundaryIs></Polygon>\n')
    out.append('      </Placemark>\n')
    return out


def write_plots_kml(path, parcels, zones_by_pid):
    """One folder per land-use zone."""
    by_zone = defaultdict(list)
    for f in parcels:
        p = f["properties"]
        by_zone[zones_by_pid.get(p.get("pid"), "unzoned")].append(f)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(KML_HEAD)
        fh.write('    <name>AUDA DP 2021 - Plot boundaries</name>\n')
        fh.write(style_defs())
        for zone in sorted(by_zone):
            fh.write('    <Folder>\n      <name>%s</name>\n' % escape(zone))
            for f in by_zone[zone]:
                ring = f["geometry"]["coordinates"][0]
                fh.write('    <Placemark>\n')
                fh.write('      <name>%s</name>\n' % escape(
                    f["properties"].get("survey") or zone))
                fh.write('      <description>area %s m2%s</description>\n' % (
                    f["properties"].get("area_m2"),
                    " | survey " + f["properties"]["survey"]
                    if f["properties"].get("survey") else ""))
                fh.write('      <styleUrl>#parcel</styleUrl>\n')
                fh.write('      <Polygon><outerBoundaryIs><LinearRing><coordinates>\n')
                for lon, lat in ring:
                    fh.write('        %.8f,%.8f,0\n' % (lon, lat))
                fh.write('      </coordinates></LinearRing></outerBoundaryIs></Polygon>\n')
                fh.write('    </Placemark>\n')
            fh.write('    </Folder>\n')
        fh.write(KML_TAIL)
    return sum(len(v) for v in by_zone.values()), len(by_zone)


def write_records_kml(path, records):
    """One folder per village."""
    by_village = defaultdict(list)
    for r in records:
        by_village[r.get("village_unit") or r.get("village") or "unassigned"].append(r)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(KML_HEAD)
        fh.write('    <name>AUDA DP 2021 - Survey records</name>\n')
        fh.write(style_defs())
        for v in sorted(by_village):
            fh.write('    <Folder>\n      <name>%s</name>\n' % escape(v))
            for r in by_village[v]:
                bits = ["survey: %s" % r.get("t", "")]
                if r.get("parcel") is not None:
                    bits.append("plot: #%s" % r["parcel"])
                if r.get("parcel_area_m2"):
                    bits.append("area: %s m2" % r["parcel_area_m2"])
                if r.get("zone"):
                    bits.append("land use: %s" % r["zone"])
                if r.get("scheme"):
                    bits.append("scheme: %s" % r["scheme"])
                fh.write('    <Placemark>\n')
                fh.write('      <name>%s</name>\n' % escape(str(r.get("t", ""))))
                fh.write('      <description>%s</description>\n' % escape(" | ".join(bits)))
                fh.write('      <styleUrl>#rec</styleUrl>\n')
                fh.write('      <Point><coordinates>%.8f,%.8f,0</coordinates></Point>\n'
                         % (r["lon"], r["lat"]))
                fh.write('    </Placemark>\n')
            fh.write('    </Folder>\n')
        fh.write(KML_TAIL)
    return len(records), len(by_village)


def write_units_kml(path, units):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(KML_HEAD)
        fh.write('    <name>AUDA DP 2021 - Village and scheme boundaries</name>\n')
        fh.write(style_defs())
        for f in units:
            p = f["properties"]
            if p.get("type") not in ("village", "scheme"):
                continue
            ring = f["geometry"]["coordinates"][0]
            fh.write('    <Placemark>\n')
            fh.write('      <name>%s</name>\n' % escape(p.get("name") or p["type"]))
            fh.write('      <description>%s | %s km2 | %s records</description>\n' % (
                escape(p["type"]), p.get("area_km2"), p.get("records", 0)))
            fh.write('      <styleUrl>#unit</styleUrl>\n')
            fh.write('      <Polygon><outerBoundaryIs><LinearRing><coordinates>\n')
            for lon, lat in ring:
                fh.write('        %.8f,%.8f,0\n' % (lon, lat))
            fh.write('      </coordinates></LinearRing></outerBoundaryIs></Polygon>\n')
            fh.write('    </Placemark>\n')
        fh.write(KML_TAIL)
    return len(units)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=GEO_DIR)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    parcels = json.load(open(os.path.join(OUT, "parcels.geojson"),
                             encoding="utf-8"))["features"]
    units = json.load(open(os.path.join(OUT, "units.geojson"),
                           encoding="utf-8"))["features"]
    print(f"{len(parcels):,} parcels, {len(units):,} units")

    # the land-use zone of each plot, from the bounded records
    zones_by_pid = {}
    records = []
    p = os.path.join(OUT, "records_bounded.ndjson")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                records.append(r)
                if r.get("parcel") is not None and r.get("zone"):
                    zones_by_pid.setdefault(r["parcel"], r["zone"])
        print(f"{len(records):,} bounded records")

    n, nz = write_plots_kml(os.path.join(args.out, "plots.kml"), parcels, zones_by_pid)
    print(f"plots.kml: {n:,} placemarks in {nz} land-use folders")
    n, nv = write_records_kml(os.path.join(args.out, "records.kml"), records)
    print(f"records.kml: {n:,} placemarks in {nv} village folders")
    n = write_units_kml(os.path.join(args.out, "units.kml"), units)
    print(f"units.kml: {n:,} placemarks")

    kmz = os.path.join(args.out, "auda_dp2021_plots.kmz")
    with zipfile.ZipFile(kmz, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ("plots.kml", "records.kml", "units.kml"):
            z.write(os.path.join(args.out, name), name)
    print(f"kmz: {kmz}  ({os.path.getsize(kmz)/1e6:.1f} MB)")


if __name__ == "__main__":
    main()
