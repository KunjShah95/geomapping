"""The plan's land-use zones, read from the rendered colour masks.

The sheets do not draw their zones as solid filled polygons - they draw them
as hatched areas, and the drawing objects are tens of thousands of tiny
slivers a few square points in area. What the map actually shows is a
colour, and the colour is the legend.

So the zones are read the way they are drawn: the sheet is rasterised and
each pixel is assigned to the nearest legend colour. Nearest-colour rather
than per-colour matching, because the sheet has three greens within a few
RGB counts of each other and per-colour matching gives the boundaries away
to the wrong class.

The sheet is rendered at 4.4 m/px (zoom 15), the same resolution as the
satellite imagery it is compared against, so the two are directly comparable
pixel for pixel.
"""
import json
import math
import os
import sys

import fitz
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dp_georef as G  # noqa: E402

ROOT = G.ROOT
M_PER_PX = 4.4          # zoom 15
SHEET = "data/dp2021/legend.json"
TOL = 22                # max sum-of-absolute-difference to count as a match
MIN_COMPONENT = 60     # px; smaller than this and it is anti-aliasing, not an area


def legend_classes(path=None):
    """Legend entries, merged where two classes share a colour."""
    with open(path or os.path.join(ROOT, SHEET), encoding="utf-8") as f:
        leg = json.load(f)
    by_col = {}
    for e in leg["entries"]:
        h = e["color"].lstrip("#")
        rgb = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
        by_col.setdefault(rgb, []).append(e["label"])
    out = []
    for rgb, labels in by_col.items():
        out.append({"label": " / ".join(labels), "rgb": np.array(rgb, np.int16),
                    "hex": "#%02x%02x%02x" % rgb, "members": labels})
    return out


def zoom_for(block, m_per_px=M_PER_PX):
    """Pixels per PDF point, for a render at the requested ground resolution.

    The sheet is drawn at block.s metres per PDF point, so a point is
    m_per_px / block.s pixels, and the zoom is the reciprocal of that.
    """
    return 1.0 / (m_per_px / block.s)


def render_sheet(block, m_per_px=M_PER_PX, band_rows=3000):
    """Rasterise the whole sheet, in bands, as one uint8 RGB array.

    Returns (rgb, zoom). Row 0 is the top of the sheet.
    """
    doc = fitz.open(block.path)
    page = doc[0]
    dl = page.get_displaylist()
    crop = page.cropbox
    zoom = zoom_for(block, m_per_px)
    total_rows = int(round((crop.y1 - crop.y0) * zoom))
    bands = []
    for r0 in range(0, total_rows, band_rows):
        r1 = min(r0 + band_rows, total_rows)
        clip = fitz.Rect(crop.x0, crop.y1 - r1 / zoom, crop.x1, crop.y1 - r0 / zoom)
        pix = dl.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip, alpha=False)
        bands.append(np.frombuffer(pix.samples, dtype=np.uint8)
                     .reshape(pix.height, pix.width, 3).copy())
    doc.close()
    w = max(b.shape[1] for b in bands)
    out = np.zeros((sum(b.shape[0] for b in bands), w, 3), np.uint8)
    y = 0
    for b in bands:
        out[y:y + b.shape[0], :b.shape[1]] = b
        y += b.shape[0]
    return out, zoom


def label_sheet(rgb, classes, tol=TOL, min_component=MIN_COMPONENT):
    """Assign every pixel to the nearest legend colour.

    Anti-iasing along the boundary between two fills lands on a third colour
    that is not in the legend at all, and those specks are then nearest to
    whichever legend colour happens to sit between them. So components smaller
    than a plausible area are dropped.

    Returns (labels, names) where labels is int16, -1 for "no match".
    """
    a = rgb.astype(np.int16)
    best = np.full(a.shape[:2], 10 ** 6, np.int32)
    lab = np.full(a.shape[:2], -1, np.int16)
    for i, c in enumerate(classes):
        d = np.abs(a - c["rgb"]).sum(axis=2)
        better = d < best
        best = np.where(better, d, best)
        lab = np.where(better, np.int16(i), lab)
    lab[best > tol] = -1
    if min_component:
        import cv2
        for i in range(len(classes)):
            m = (lab == i).astype(np.uint8)
            if not m.any():
                continue
            n, cc, stats, cent = cv2.connectedComponentsWithStats(m, 8)
            if n <= 1:
                continue
            small = np.nonzero(stats[1:, cv2.CC_STAT_AREA] < min_component)[0] + 1
            if len(small):
                drop = np.isin(cc, small)
                lab[drop] = -1
    return lab, [c["label"] for c in classes]


def sheet_px_to_ground(block, zoom, col, row):
    """Sheet pixel -> (lon, lat). Row 0 is the top of the sheet."""
    crop = block.cropbox
    ux = col / zoom + crop.x0
    uy = crop.y1 - row / zoom
    return block.page_to_ground(ux, uy)


def ground_to_sheet_px(block, zoom, lon, lat):
    ux, uy = block.ground_to_page(lon, lat)
    crop = block.cropbox
    return (ux - crop.x0) * zoom, (crop.y1 - uy) * zoom


if __name__ == "__main__":
    blocks, _ = G.build_blocks()
    classes = legend_classes()
    out = os.path.join(ROOT, "work", "newdp", "zones")
    os.makedirs(out, exist_ok=True)
    only = sys.argv[1].split(",") if len(sys.argv) > 1 else None
    for quad, block in blocks.items():
        if only and quad not in only:
            continue
        print(f"[{quad}] rendering the sheet at {M_PER_PX} m/px ...", flush=True)
        rgb, zoom = render_sheet(block)
        print(f"    {rgb.shape[1]} x {rgb.shape[0]} px", flush=True)
        lab, names = label_sheet(rgb, classes)
        print(f"    assigned {(lab >= 0).mean()*100:.1f}% of the sheet to a legend class")
        for i, nm in enumerate(names):
            frac = (lab == i).mean()
            print(f"    {nm:46s} {classes[i]['hex']}  {frac*100:6.3f}%")
        np.savez_compressed(os.path.join(out, f"{quad}.npz"),
                            labels=lab, zoom=np.array([zoom]))
        print(f"    wrote {out}/{quad}.npz", flush=True)
