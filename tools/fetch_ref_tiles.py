import math, urllib.request, os, io
from PIL import Image

Image.MAX_IMAGE_PIXELS = None
TPL = "https://s3-ap-south-1.amazonaws.com/tpmap-auda/AUDAGUDA2023-Final/{z}/{x}/{y}.png"
BL = (22.763306, 72.178109)   # lat, lon
TR = (23.357406, 72.860675)
out = r"C:\ideabot internships\geomapping\work\ref_tiles"
os.makedirs(out, exist_ok=True)


def deg2tile(lat, lon, z):
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def fetch(z, x, y, tag):
    url = TPL.format(z=z, x=x, y=y)
    p = os.path.join(out, f"{tag}_z{z}_{x}_{y}.png")
    if os.path.exists(p) and os.path.getsize(p) > 0:
        return p, os.path.getsize(p)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        with open(p, "wb") as f:
            f.write(data)
        return p, len(data)
    except Exception as e:
        return None, f"ERR {e}"


z = 12
nx, ny = deg2tile(*TR, z)   # north-east corner
sx, sy = deg2tile(*BL, z)   # south-west corner
x0, x1 = sorted((nx, sx))
y0, y1 = sorted((ny, sy))
print(f"z{z} bbox in tile coords: x {x0:.2f}..{x1:.2f}  y {y0:.2f}..{y1:.2f}")
print(f"tile count approx: {(x1-x0+1)*(y1-y0+1):.0f}")

got = 0
for y in range(int(y0), int(y1) + 1):
    for x in range(int(x0), int(x1) + 1):
        if y >= 2 ** z or x >= 2 ** z:
            continue
        p, info = fetch(z, x, y, "auda")
        if p:
            got += 1
        if got <= 12:
            print(" ", x, y, info)
print("fetched", got)
