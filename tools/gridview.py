"""Render a region of the source plan image with a labelled pixel grid.

Usage:
  python tools/gridview.py <x0> <y0> <x1> <y1> <out.png> [out_width_px] [minor] [major]
Coordinates are in SOURCE image pixels (27200 x 25600).
"""
import sys
from PIL import Image, ImageDraw, ImageFont

Image.MAX_IMAGE_PIXELS = None
SRC = r"C:\ideabot internships\geomapping\work\source.jpeg"

x0, y0, x1, y1 = map(int, sys.argv[1:5])
out = sys.argv[5]
ow = int(sys.argv[6]) if len(sys.argv) > 6 else 1800
minor = int(sys.argv[7]) if len(sys.argv) > 7 else 500
major = int(sys.argv[8]) if len(sys.argv) > 8 else 2000

im = Image.open(SRC).convert("RGB")
cw, ch = x1 - x0, y1 - y0
scale = ow / cw
oh = max(1, int(round(ch * scale)))

crop = im.crop((x0, y0, x1, y1)).resize((ow, oh), Image.LANCZOS)
d = ImageDraw.Draw(crop, "RGBA")
try:
    font = ImageFont.truetype("arial.ttf", 13)
except Exception:
    font = ImageFont.load_default()

# minor grid
px = x0 - (x0 % minor) + minor
while px < x1:
    X = (px - x0) * scale
    d.line([(X, 0), (X, oh)], fill=(255, 0, 255, 70), width=1)
    px += minor
py = y0 - (y0 % minor) + minor
while py < y1:
    Y = (py - y0) * scale
    d.line([(0, Y), (ow, Y)], fill=(255, 0, 255, 70), width=1)
    py += minor

# major grid + labels
px = x0 - (x0 % major) + major
while px < x1:
    X = (px - x0) * scale
    d.line([(X, 0), (X, oh)], fill=(255, 0, 0, 190), width=2)
    d.rectangle([X + 2, 2, X + 2 + 62, 18], fill=(255, 255, 255, 210))
    d.text((X + 4, 4), str(px), fill=(200, 0, 0), font=font)
    px += major
py = y0 - (y0 % major) + major
while py < y1:
    Y = (py - y0) * scale
    d.line([(0, Y), (ow, Y)], fill=(255, 0, 0, 190), width=2)
    d.rectangle([2, Y + 2, 64, Y + 18], fill=(255, 255, 255, 210))
    d.text((4, Y + 4), str(py), fill=(200, 0, 0), font=font)
    py += major

crop.save(out)
print(f"{out}  region=({x0},{y0})-({x1},{y1})  display={ow}x{oh}  scale={scale:.5f} px/px")
