"""Corrected georeference solver.

Two bugs in solve_georef.py are fixed here:

  1. The stage-1 bracket was 1100..3400 at div=40, i.e. 44000..136000 in
     full-resolution px/merc-deg. The truth is ~39647, which is BELOW the
     floor. The entire bracket excluded the answer, so the solver returned the
     best-looking wrong fit instead of failing.

  2. Scale is not invariant across `div`. s is expressed in px-per-merc-deg AT
     THE CURRENT div, so going 40 -> 20 must halve it. The original cascade
     passed b1.s*0.93..*1.07 straight into the div=20 search (a 2x error),
     and again into div=10, then finally multiplied by 10 assuming a div=10
     result. Here s_full is the single invariant and s_div = s_full / div.

This script is self-validating: it reports the solved extent against the
published AUDA RDP anchor box and flags agreement. It writes
work/georef_solved.json and never touches work/georef.json.
"""
import json, math, os, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

Image.MAX_IMAGE_PIXELS = None

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from solve_georef import load_osm, best_iou, merc_y, WORK, SRC, FULL_W, FULL_H

# Published AUDA anchor box (same source as tools/fetch_ref_tiles.py).
ANCHOR_BL = (22.763306, 72.178109)   # lat, lon
ANCHOR_TR = (23.357406, 72.860675)
ANCHOR_S_FULL = FULL_H / (merc_y(ANCHOR_TR[0]) - merc_y(ANCHOR_BL[0]))

EXPAND = 1.6
MASK_CACHE = {}


def image_mask(div):
    """Blue-plan water mask, cached to .npy so the 700MP JPEG is decoded once."""
    npy = os.path.join(WORK, f"_mask_d{div}.npy")
    if div in MASK_CACHE:
        return MASK_CACHE[div]
    if os.path.exists(npy):
        a = np.load(npy)
        MASK_CACHE[div] = a
        return a
    im = Image.open(SRC).convert("RGB")
    w, h = FULL_W // div, FULL_H // div
    a = np.asarray(im.resize((w, h), Image.BILINEAR)).astype(np.int16)
    R, G, B = a[..., 0], a[..., 1], a[..., 2]
    m = (B > R + 12) & (B > 170) & (G > R)
    del im, a
    np.save(npy, m)
    MASK_CACHE[div] = m
    return m


def raster_osm(s, ox, oy, W, H, polys, lines, line_w=2):
    img = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(img)

    def P(lon, lat):
        return ((lon - ox) * s, (oy - merc_y(lat)) * s)

    for pts in polys:
        d.polygon([P(lo, la) for lo, la in pts], fill=255)
    for pts in lines:
        d.line([P(lo, la) for lo, la in pts], fill=255, width=line_w)
    return np.asarray(img) > 127


def search(s_full, div, polys, lines, pad=3, verbose=True):
    """Best (iou, ox, oy) over free translation, at fixed scale s_full."""
    A = image_mask(div)
    if A.sum() < 500:
        raise SystemExit(f"div={div}: water mask has only {A.sum()} px - mask is not usable")
    Ap = np.asarray(Image.fromarray((A * 255).astype(np.uint8))
                    .filter(ImageFilter.MaxFilter(5))) > 127
    s = s_full / div
    cw, ch = A.shape[1], A.shape[0]
    W, H = int(cw * EXPAND), int(ch * EXPAND)

    lons = [p[0] for pl in polys + lines for p in pl]
    lats = [p[1] for pl in polys + lines for p in pl]
    x0, x1 = min(lons), max(lons)
    ytop, ybot = merc_y(max(lats)), merc_y(min(lats))
    mx, my = (x0 + x1) / 2, (ytop + ybot) / 2

    ox0, oy0 = mx - (W / 2) / s, my + (H / 2) / s
    Bm = raster_osm(s, ox0, oy0, W, H, polys, lines,
                    line_w=max(1, int(pad * s / 900)))
    if Bm.sum() < 100:
        return None
    iou, ay, ax = best_iou(Ap, Bm)
    ox = ox0 + ax / s
    oy = oy0 - ay / s
    if verbose:
        print(f"   div={div:2d} s_full={s_full:9.1f} iou={iou:.4f} "
              f"ox={ox:.5f} oy={oy:.5f}")
    return dict(iou=iou, s_full=float(s_full), ox=ox, oy=oy)


def extent(s_full, ox, oy):
    lon_tl = ox
    lon_br = ox + FULL_W / s_full
    lat_tr = math.degrees(2 * math.atan(math.exp(math.radians(oy))) - math.pi / 2)
    lat_br = math.degrees(2 * math.atan(math.exp(math.radians(oy - FULL_H / s_full))) - math.pi / 2)
    return lon_tl, lon_br, lat_tr, lat_br


def main():
    polys, lines = load_osm()
    print(f"osm: {len(polys)} water polygons, {len(lines)} river lines")

    for div in (40, 20, 10):
        n = int(image_mask(div).sum())
        print(f"  mask div={div:2d}: {n:>8d} px ({100.0*n/image_mask(div).size:5.2f}% of canvas)")

    print(f"\nanchor s_full = {ANCHOR_S_FULL:.1f} px/merc-deg")

    # Stage 1: coarse sweep bracketing the anchor +/-25%, so this is a real test
    # of the anchor rather than a blind search.
    lo, hi = ANCHOR_S_FULL * 0.75, ANCHOR_S_FULL * 1.25
    step = (hi - lo) / 60.0
    print(f"\n[stage 1] div=40 sweep {lo:.0f}..{hi:.0f} step {step:.0f}")
    best = None
    for s in np.arange(lo, hi, step):
        r = search(s, 40, polys, lines, verbose=False)
        if r and (best is None or r["iou"] > best["iou"]):
            best = r
            print(f"   s_full={s:9.1f} iou={r['iou']:.4f}  <== best")
    if not best:
        raise SystemExit("stage 1 found nothing")
    print(f"stage1: s_full={best['s_full']:.1f} iou={best['iou']:.4f}")

    # Stages 2-3: refine in s_full space, halving the window each time.
    for div, tol, n in ((20, 0.04, 14), (10, 0.015, 12)):
        span = best["s_full"] * tol
        step = (2 * span) / n
        print(f"\n[stage] div={div} refine +/-{tol*100:.1f}%  step {step:.0f}")
        cands = []
        for s in np.arange(best["s_full"] - span, best["s_full"] + span, step):
            r = search(s, div, polys, lines, verbose=False)
            if r:
                cands.append(r)
        cands.sort(key=lambda r: -r["iou"])
        if cands:
            best = cands[0]
            print(f"   -> s_full={best['s_full']:.1f} iou={best['iou']:.4f}")

    lon_tl, lon_br, lat_tr, lat_br = extent(best["s_full"], best["ox"], best["oy"])
    kpd = 111.32 * math.cos(math.radians((lat_tr + lat_br) / 2))

    res = dict(
        source="fix_georef.py (corrected solver)",
        s_px_per_merc_deg=best["s_full"],
        lon_min=lon_tl, lon_max=lon_br, lat_min=lat_br, lat_max=lat_tr,
        iou=best["iou"],
        m_per_px=(lon_br - lon_tl) * kpd * 1000 / FULL_W,
        width_km=(lon_br - lon_tl) * kpd,
        anchor=dict(
            s_px_per_merc_deg=ANCHOR_S_FULL,
            lon_min=ANCHOR_BL[1], lon_max=ANCHOR_TR[1],
            lat_min=ANCHOR_BL[0], lat_max=ANCHOR_TR[0],
        ),
    )
    res["scale_error_pct"] = 100.0 * (best["s_full"] - ANCHOR_S_FULL) / ANCHOR_S_FULL
    res["contains_anchor"] = bool(
        lon_tl <= ANCHOR_BL[1] and lon_br >= ANCHOR_TR[1]
        and lat_br <= ANCHOR_BL[0] and lat_tr >= ANCHOR_TR[0])

    print("\n=== SOLVED vs ANCHOR ===")
    print(f"solved extent : lon {lon_tl:.4f}..{lon_br:.4f}  lat {lat_br:.4f}..{lat_tr:.4f}")
    print(f"anchor extent : lon {ANCHOR_BL[1]:.4f}..{ANCHOR_TR[1]:.4f}"
          f"  lat {ANCHOR_BL[0]:.4f}..{ANCHOR_TR[0]:.4f}")
    print(f"scale error   : {res['scale_error_pct']:+.2f}%   IoU {best['iou']:.4f}")
    print(f"ground res    : {res['m_per_px']:.2f} m/px   sheet {res['width_km']:.1f} km wide")
    print(f"contains anchor box: {res['contains_anchor']}")

    old = json.load(open(os.path.join(WORK, "georef.json"), encoding="utf-8"))
    res["previous_result"] = old
    print(f"\nprevious (buggy): s_full={old['s_px_per_deg']:.1f} "
          f"scale error {100.0*(old['s_px_per_deg']-ANCHOR_S_FULL)/ANCHOR_S_FULL:+.1f}%")

    out = os.path.join(WORK, "georef_solved.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()