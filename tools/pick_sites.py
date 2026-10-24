"""Render labelled pixel-grid crops for manual control-point picking.

gridview.py does this but re-decodes the 27200x25600 source for every crop,
which is slow. This opens it once and emits every crop in one pass.

    python tools/pick_sites.py            # default 3x3 site grid
    python tools/pick_sites.py --list     # show site coordinates

Writes work/sites/<name>.png plus an index.
"""
import argparse, json, os, sys
from PIL import Image, ImageDraw, ImageFont

Image.MAX_IMAGE_PIXELS = None
HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(os.path.dirname(HERE), "work")
SRC = os.path.join(WORK, "source.jpeg")
OUT = os.path.join(WORK, "sites")
FULL_W, FULL_H = 27200, 25600

# Sites placed from a measured 16x16 occupancy grid of the plan footprint
# (work/sites_overview.png), not from the image rectangle. The plan is a
# diagonal band: there is an upper-left lobe around px 7000-13000 / py
# 3200-9000, a top-right lobe around 17000-23000 / 0-6000, a dense central
# mass, a right-hand arm, and nothing at all in the bottom-left.
#
# Chosen to span all four extremes of the footprint plus the centre. That
# spread matters: an affine needs control points covering the full range in
# both axes, otherwise rotation and scale come out meaningless. This is why
# the earlier image-rectangle grid was wrong - it put 4 of 9 sites on blank
# paper and clustered the rest in one corner.
#
# Each window is 6000x6000 source px = ~15 km at 2.57 m/px.
SITES = [
    ("NW",   6800,  3200, 12800,  9200),   # upper-left lobe
    ("NE",  17000,     0, 23000,  6000),   # top-right lobe
    ("W",    1700,  9600,  7700, 15600),   # left arm, incl. the large lake
    ("C",    8500, 11200, 14500, 17200),   # densest area, urban core
    ("E",   20400,  6400, 26400, 12400),   # right arm
    ("S",   11900, 17600, 17900, 23600),   # lower middle
    ("SE",  17000, 20800, 23000, 25600),   # bottom-right lobe
]

MINOR, MAJOR, OW = 250, 1000, 2000


def font(sz):
    for p in (r"C:\Windows\Fonts\consola.ttf", r"C:\Windows\Fonts\arial.ttf"):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, sz)
            except OSError:
                pass
    return ImageFont.load_default()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="rebuild sites_overview.png even if it exists")
    a = ap.parse_args()

    if a.list:
        for n, x0, y0, x1, y1 in SITES:
            print(f"{n:>3}  px {x0}..{x1}  py {y0}..{y1}")
        return

    os.makedirs(OUT, exist_ok=True)
    im = Image.open(SRC).convert("RGB")
    print(f"source {im.size}; decoding once...", flush=True)

    # overview: whole sheet with the site boxes drawn on it
    ovp = os.path.join(WORK, "sites_overview.png")
    if os.path.exists(ovp) and not a.force:
        print(f"{ovp} exists, skipping (use --force to rebuild)")
    else:
        ov_w = 1600
        sc = ov_w / FULL_W
        ov = im.resize((ov_w, int(FULL_H * sc)), Image.BILINEAR)
        d = ImageDraw.Draw(ov)
        f = font(15)
        for n, x0, y0, x1, y1 in SITES:
            d.rectangle([x0 * sc, y0 * sc, x1 * sc, y1 * sc],
                        outline=(255, 40, 40), width=3)
            d.text((x0 * sc + 6, y0 * sc + 4), n, fill=(255, 255, 0), font=f,
                   stroke_width=2, stroke_fill=(0, 0, 0))
        ov.save(ovp)
        print(f"wrote {ovp} ({ov.size[0]}x{ov.size[1]})", flush=True)

    fnt = font(17)
    idx = []
    for n, x0, y0, x1, y1 in SITES:
        crop = im.crop((x0, y0, x1, y1))
        cw, ch = crop.size
        s = OW / cw
        rs = crop.resize((OW, max(1, int(ch * s))), Image.LANCZOS)
        d = ImageDraw.Draw(rs)

        def gx(v):
            return (v - x0) * s

        def gy(v):
            return (v - y0) * s

        for v in range(x0 - x0 % MINOR, x1 + 1, MINOR):
            major = (v % MAJOR == 0)
            d.line([gx(v), 0, gx(v), rs.size[1]],
                   fill=(255, 210, 0) if major else (0, 255, 255),
                   width=2 if major else 1)
            if major:
                d.text((gx(v) + 3, 3), str(v), fill=(255, 0, 0), font=fnt,
                       stroke_width=2, stroke_fill=(255, 255, 255))
        for v in range(y0 - y0 % MINOR, y1 + 1, MINOR):
            major = (v % MAJOR == 0)
            d.line([0, gy(v), rs.size[0], gy(v)],
                   fill=(255, 210, 0) if major else (0, 255, 255),
                   width=2 if major else 1)
            if major:
                d.text((3, gy(v) + 3), str(v), fill=(255, 0, 0), font=fnt,
                       stroke_width=2, stroke_fill=(255, 255, 255))
        p = os.path.join(OUT, f"{n}.png")
        rs.save(p)
        idx.append(dict(name=n, px0=x0, py0=y0, px1=x1, py1=y1, file=p))
        print(f"wrote {p}", flush=True)

    with open(os.path.join(WORK, "sites_index.json"), "w", encoding="utf-8") as f:
        json.dump(dict(source_size=[FULL_W, FULL_H], m_per_px=2.57,
                       minor=MINOR, major=MAJOR, sites=idx), f, indent=2)
    print(f"\nindex written; work/sites_overview.png shows where each site sits")


if __name__ == "__main__":
    main()