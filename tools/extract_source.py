import fitz, os
from PIL import Image

Image.MAX_IMAGE_PIXELS = None
path = r"C:\Users\jksha\Downloads\audareviseddevelopmentplan2022map04112022064034976.pdf"
out = r"C:\ideabot internships\geomapping\work"
os.makedirs(out, exist_ok=True)

doc = fitz.open(path)
img = doc.extract_image(12)
print("ext:", img["ext"], "w", img["width"], "h", img["height"], "cs", img["colorspace"], "bytes", len(img["image"]))
raw = os.path.join(out, "source." + img["ext"])
with open(raw, "wb") as f:
    f.write(img["image"])
print("wrote", raw, os.path.getsize(raw))

im = Image.open(raw)
print("mode", im.mode, "size", im.size)
prev = im.convert("RGB").resize((1500, int(1500 * im.size[1] / im.size[0])), Image.LANCZOS)
p = os.path.join(out, "preview_full.jpg")
prev.save(p, quality=88)
print("wrote", p, prev.size)
doc.close()
