"""Crop the plan around a predicted landmark with a crosshair at the predicted point,
so the guess can be confirmed or rejected visually."""
import json, math, os, sys
from PIL import Image, ImageDraw, ImageFont

Image.MAX_IMAGE_PIXELS = None
WORK = r"C:\ideabot internships\geomapping\work"
SRC = os.path.join(WORK, "source.jpeg")

with open(os.path.join(WORK, "landmark_px.json", encoding="utf-8"), encoding="utf-8") as f:
    marks = json.load(f)

want = sys.argv[1]
half = int(sys.argv[2]) if len(sys.argv) > 2 else 1200
out_w = int(sys.argv[3]) if len(sys.argv) > 3 else 1600

im = Image.open(SRC).convert("RGB")
for name, lat, lon, px, py, inside in marks:
    if want.lower() not in name.lower():
        continue
    px, py = int(round(px)), int(round(py))
    x0, y0 = max(0, px - half), max(0, py - half)
    x1, y1 = min(im.size[0], px + half), min(im.size[1], py + half)
    crop = im.crop((x0, y0, x1, y1))
    sc = out_w / crop.size[0]
    crop = crop.resize((out_w, int(crop.size[1] * sc)), Image.LANCZOS)
    d = ImageDraw.Draw(crop, "RGBA")
    cx, cy = (px - x0) * sc, (py - y0) * sc
    for r in (12, 24, 36):
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 0, 0), width=2)
    d.line([(cx - 60, cy), (cx - 14, cy)], fill=(255, 0, 0), width=2)
    d.line([(cx + 14, cy), (cx + 60, cy)], fill=(255, 0, 0), width=2)
    d.line([(cx, cy - 60), (cx, cy - 14)], fill=(255, 0, 0), width=2)
    d.line([(cx, cy + 14), (cx, cy + 60)], fill=(255, 0, 0), width=2)
    try:
        font = ImageFont.truetype("arialbd.ttf", 18)
    except Exception:
        font = ImageFont.load_default()
    tag = f"{name}  lat={lat} lon={lon}  src=({px},{py})"
    d.rectangle([0, 0, min(out_w, 760), 26], fill=(255, 255, 255, 225))
    d.text((6, 4), tag, fill=(200, 0, 0), font=font)
    p = os.path.join(WORK, "lm_" + want.split()[0].lower() + ".png")
    crop.save(p)
    print("wrote", p, crop.size, "crosshair at", (round(cx), round(cy)))
    break
