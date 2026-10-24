"""Write the authoritative georeference for the AUDA RDP 2022 sheet.

Model (WebMercator degrees -> sheet image pixels):
    px = (lon - lon_min) * SX
    py = (mercY(lat_max) - mercY(lat)) * SY
    mercY(lat) = degrees(ln(tan(pi/4 + radians(lat)/2)))

H0 anchors the sheet's four edges to the bounding box that townplanmap.com publishes
for its AUDA layer (BottomLeft/TopRight in the app's city config), north-up.
"""
import json, math, os

WORK = r"C:\ideabot internships\geomapping\work"
FULL_W, FULL_H = 27200, 25600

# From townplanmap.com's AUDA city config (lat, lon)
BL_LAT, BL_LON = 22.763306, 72.178109
TR_LAT, TR_LON = 23.357406, 72.860675


def merc_y(lat):
    return math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


OY = merc_y(TR_LAT)
SX = FULL_W / (TR_LON - BL_LON)
SY = FULL_H / (OY - merc_y(BL_LAT))

georef = dict(
    source_image="work/source.jpeg",
    source_width=FULL_W,
    source_height=FULL_H,
    crs="EPSG:3857",
    origin="sheet top-left",
    lon_min=BL_LON,
    lon_max=TR_LON,
    lat_max=TR_LAT,
    lat_min=BL_LAT,
    merc_top=OY,
    SX=SX,
    SY=SY,
    anisotropy=SY / SX,
)

# native slippy zoom: sheet px per tile == 256
import math as _m
georef["native_zoom"] = round(_m.log2(27200 / 256 * 360 / (TR_LON - BL_LON)), 2)
kpd = 111.32 * _m.cos(_m.radians((BL_LAT + TR_LAT) / 2))
georef["ground_width_km"] = (TR_LON - BL_LON) * kpd
georef["m_per_px"] = georef["ground_width_km"] * 1000 / FULL_W

p = os.path.join(WORK, "georef.json")
with open(p, "w", encoding="utf-8") as f:
    json.dump(georef, f, indent=2)
print(json.dumps(georef, indent=2))
print("\nwrote", p)

print("\ntile span per zoom (tiles across the sheet):")
for z in range(10, 19):
    tw = (TR_LON - BL_LON) * 2 ** z / 360
    th = (OY - merc_y(BL_LAT)) * 2 ** z / 360
    print(f"  z={z}: {tw:7.2f} x {th:7.2f} tiles   (~{tw*th:8.0f} tiles, sheet px per tile = {FULL_W/tw:8.0f})")
