"""Stitch a tile level back into a single georeferenced mosaic, for verification.

Writes <out>.png plus <out>.pgw (an ESRI world file) so the mosaic can be loaded
directly by GDAL/QGIS/ArcGIS with the correct georeferencing.
"""
import argparse, glob, math, os, re
from PIL import Image

TILE = 256


def deg2tile(lon, lat, z):
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tiles", required=True)
    ap.add_argument("--z", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--maxpx", type=int, default=4000)
    a = ap.parse_args()

    files = glob.glob(os.path.join(a.tiles, str(a.z), "*", "*.jpg"))
    if not files:
        raise SystemExit(f"no tiles at z{a.z}")
    xs, ys = [], []
    for f in files:
        m = re.search(r"[/\\](\d+)[/\\](\d+)\.jpg$", f)
        xs.append(int(m.group(1)))
        ys.append(int(m.group(2)))
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    gw, gh = (x1 - x0 + 1) * TILE, (y1 - y0 + 1) * TILE
    print(f"z{a.z}: {len(files)} tiles, grid x {x0}..{x1} y {y0}..{y1}, grid {gw}x{gh} px")

    scale = min(1.0, a.maxpx / max(gw, gh))
    ow, oh = int(gw * scale), int(gh * scale)
    mosaic = Image.new("RGB", (ow, oh), (255, 255, 255))
    for f in files:
        m = re.search(r"[/\\](\d+)[/\\](\d+)\.jpg$", f)
        tx, ty = int(m.group(1)), int(m.group(2))
        t = Image.open(f)
        if scale != 1.0:
            t = t.resize((int(TILE * scale), int(TILE * scale)), Image.LANCZOS)
        mosaic.paste(t, (int((tx - x0) * TILE * scale), int((ty - y0) * TILE * scale)))
    mosaic.save(a.out)
    print("wrote", a.out, mosaic.size)

    # world file: mosaic spans the tile grid's geographic extent
    n = 2 ** a.z
    lon_w = x0 * 360.0 / n - 180.0
    lon_e = (x1 + 1) * 360.0 / n - 180.0
    R = 6378137.0
    my_n = math.pi * (1 - 2 * y0 / n)
    my_s = math.pi * (1 - 2 * (y1 + 1) / n)
    lat_n = math.degrees(2 * math.atan(math.exp(my_n)) - math.pi / 2)
    lat_s = math.degrees(2 * math.atan(math.exp(my_s)) - math.pi / 2)
    px_w = (lon_e - lon_w) * math.pi / 180 * R / ow
    px_n = (my_n - my_s) * R / oh
    w = [px_w, 0.0, lon_w * math.pi / 180 * R, 0.0, -px_n, my_n * R]
    with open(a.out + ".pgw", "w", encoding="utf-8") as fh:
        fh.write("\n".join(f"{v:.10f}" for v in w) + "\n")
    print("wrote", a.out + ".pgw")
    print(f"extent: lon {lon_w:.5f}..{lon_e:.5f}  lat {lat_s:.5f}..{lat_n:.5f}")


if __name__ == "__main__":
    main()
