"""Predict where known landmarks should land under the H0 georeference and
render marked crops so each guess can be confirmed or rejected by eye.

Landmarks are chosen because they are visually unmistakable in a land-use plan:
an airport runway, a stadium oval, large reservoirs, a bridge/ashram on the river.
"""
import json, math, os, sys
from PIL import Image, ImageDraw, ImageFont

Image.MAX_IMAGE_PIXELS = None
WORK = r"C:\ideabot internships\geomapping\work"
SRC = os.path.join(WORK, "source.jpeg")
FULL_W, FULL_H = 27200, 25600

BL_LAT, BL_LON = 22.763306, 72.178109
TR_LAT, TR_LON = 23.357406, 72.860675
OX, OY = BL_LON, math.degrees(math.log(math.tan(math.pi / 4 + math.radians(TR_LAT) / 2)))
SX = FULL_W / (TR_LON - BL_LON)
SY = FULL_H / (OY - math.degrees(math.log(math.tan(math.pi / 4 + math.radians(BL_LAT) / 2))))


def merc_y(lat):
    return math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def to_px(lat, lon):
    return ((lon - OX) * SX, (OY - merc_y(lat)) * SY)


LANDMARKS = [
    ("Sardar Vallabhbhai Patel Intl Airport (runway)", 23.0774, 72.6327),
    ("Sardar Patel Stadium, Navrangpura",           23.0380, 72.4777),
    ("Kankaria Lake",                              22.9786, 72.6466),
    ("Sarkhej Lake / Sarkhej Roza",                 22.9723, 72.4997),
    ("Sabarmati Ashram",                           23.0535, 72.5714),
    ("Ahmedabad Junction railway station",         23.0223, 72.5713),
    ("Gandhinagar (Secretariat area)",             23.2156, 72.6369),
    ("Bhadreshwar",                                23.0289, 72.4600),
    ("Viramgam",                                   23.0378, 72.3360),
    ("Sanand",                                     22.9833, 72.3833),
    ("Kheda",                                      22.7525, 72.6875),
    ("Mahemdavad",                                 22.8167, 72.9667),
    ("Nalsarovar",                                 22.8300, 72.5100),
    ("Dholera SIR centre",                         22.2500, 72.6190),
]

im = Image.open(SRC).convert("RGB")

print(f"{'landmark':46s} {'lat':>9s} {'lon':>9s} {'px':>8s} {'py':>8s}  in-sheet")
rows = []
for name, lat, lon in LANDMARKS:
    px, py = to_px(lat, lon)
    inside = 0 <= px <= FULL_W and 0 <= py <= FULL_H
    rows.append((name, lat, lon, px, py, inside))
    print(f"{name:46s} {lat:9.4f} {lon:9.4f} {px:8.0f} {py:8.0f}  {inside}")

# marked overview
DIV = 14
ov = im.resize((FULL_W // DIV, FULL_H // DIV), Image.LANCZOS)
d = ImageDraw.Draw(ov)
try:
    font = ImageFont.truetype("arialbd.ttf", 15)
except Exception:
    font = ImageFont.load_default()
for name, lat, lon, px, py, inside in rows:
    X, Y = px / DIV, py / DIV
    if not inside:
        continue
    d.ellipse([X - 9, Y - 9, X + 9, Y + 9], outline=(255, 0, 0), width=3)
    d.line([(X - 14, Y), (X + 14, Y)], fill=(255, 0, 0), width=1)
    d.line([(X, Y - 14), (X, Y + 14)], fill=(255, 0, 0), width=1)
    d.text((X + 12, Y - 8), name.split("(")[0].strip()[:28], fill=(255, 0, 0), font=font,
           stroke_width=2, stroke_fill=(255, 255, 255))
ov.save(os.path.join(WORK, "landmarks_overview.png"))
print("\nwrote", os.path.join(WORK, "landmarks_overview.png"), ov.size)

with open(os.path.join(WORK, "landmark_px.json"), "w", encoding="utf-8") as f:
    json.dump([(n, la, lo, px, py, ins) for n, la, lo, px, py, ins in rows], f, indent=2)
