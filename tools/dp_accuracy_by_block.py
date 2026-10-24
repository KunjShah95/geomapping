"""Measure each part sheet's own offset from the satellite.

The four part sheets all claim to cover the same 2x2 grid, but they do not
draw the same things in the same places. This measures, for each sheet
separately, how far its own drawing sits from today's satellite - which says
which sheet is where it says it is.

Output: work/newdp/accuracy_by_block.json
"""
import json, math, os, sys, time
import fitz, numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dp_georef as G

ROOT = G.ROOT
SAT = os.path.join(ROOT, "satellite")
Z = 16

def merc_y(lat): return math.degrees(math.log(math.tan(math.pi/4+math.radians(lat)/2)))
def inv_merc_y(my): return math.degrees(2*math.atan(math.exp(math.radians(my)))-math.pi/2)

def sat_bright_window(lon, lat, half_px, z=16):
    n = 2**z; dpp = 360.0/n/256.0
    fx = (lon+180.0)/dpp; fy = (180.0-merc_y(lat))/dpp
    x0, y0 = int(fx)-half_px, int(fy)-half_px
    out = np.zeros((half_px*2, half_px*2), bool)
    for tx in range(x0//256, (x0+half_px*2)//256+1):
        for ty in range(y0//256, (y0+half_px*2)//256+1):
            p = os.path.join(SAT, str(z), str(tx), f"{ty}.jpg")
            if not os.path.exists(p): continue
            with Image.open(p) as im:
                a = np.asarray(im.convert("RGB")).astype(np.int16)
            mx = a.max(axis=2); mn = a.min(axis=2)
            sat = (mx-mn)/np.maximum(mx,1)
            bright = (mn>100)&(sat<0.22)
            ix0,iy0 = max(x0,tx*256), max(y0,ty*256)
            ix1,iy1 = min(x0+half_px*2,(tx+1)*256), min(y0+half_px*2,(ty+1)*256)
            if ix1<=ix0 or iy1<=iy0: continue
            out[iy0-y0:iy1-y0, ix0-x0:ix1-x0] = bright[iy0-ty*256:iy1-ty*256, ix0-tx*256:ix1-tx*256]
    return out

def best_shift_fft(plan, sat):
    h,w = plan.shape; ph,pw = h*2, w*2
    P = np.zeros((ph,pw)); P[:h,:w] = plan
    S = np.zeros((ph,pw)); S[:h,:w] = sat
    cc = np.fft.irfft2(np.fft.rfft2(S)*np.fft.rfft2(P[::-1,::-1]), s=(ph,pw))
    idx = np.unravel_index(np.argmax(cc), cc.shape)
    dy = idx[0] if idx[0]<=ph//2 else idx[0]-ph
    dx = idx[1] if idx[1]<=pw//2 else idx[1]-pw
    return dy, dx

def main():
    blocks, grid = G.build_blocks()
    m_per_px = 156543.03392*math.cos(math.radians(23.06))/(2**Z)
    out = {}
    for q, b in blocks.items():
        doc = fitz.open(b.path); page = doc[0]
        dl = page.get_displaylist(); crop = page.cropbox
        # low-res render of the whole sheet to find where the ink is
        zoom = 1400.0/page.rect.width
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        low = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        ink = low.min(axis=2) < 245
        ys, xs = np.nonzero(ink)
        # sample points on the ink
        rng = np.random.default_rng(5)
        idx = rng.choice(len(xs), size=min(900, len(xs)), replace=False)
        res = []
        for i in idx:
            px = xs[i]/zoom; py = ys[i]/zoom
            ux = px + crop.x0; uy = crop.y1 - py
            lon, lat = b.page_to_ground(ux, uy)
            half = 60
            m = half*m_per_px
            # plan window in page space
            pw_pt = m/b.s
            clip = fitz.Rect(px-half*0+0, 0, 0, 0)
            x0 = ux-pw_pt; x1 = ux+pw_pt; y0 = uy-pw_pt; y1 = uy+pw_pt
            c = fitz.Rect(x0-crop.x0, crop.y1-y1, x1-crop.x0, crop.y1-y0)
            z2 = (2*half)/(c.width*b.s)*m_per_px*m_per_px  # placeholder
            z2 = m_per_px/b.s
            p2 = dl.get_pixmap(matrix=fitz.Matrix(z2, z2), clip=c, alpha=False)
            if p2.width < 2 or p2.height < 2: continue
            pa = np.frombuffer(p2.samples, dtype=np.uint8).reshape(p2.height, p2.width, 3)
            if pa.shape[0] != 2*half or pa.shape[1] != 2*half:
                pa = np.asarray(Image.fromarray(pa).resize((2*half, 2*half), Image.BILINEAR))
            plan_b = pa.min(axis=2) > 150
            if not (0.05 < plan_b.mean() < 0.6): continue
            sat_b = sat_bright_window(lon, lat, half)
            if not (0.04 < sat_b.mean() < 0.65): continue
            dy, dx = best_shift_fft(plan_b, sat_b)
            if abs(dx) > 30 or abs(dy) > 30: continue
            ss = np.roll(np.roll(sat_b, dy, axis=0), dx, axis=1)
            iou = np.logical_and(plan_b, ss).sum()/max(1, np.logical_or(plan_b, ss).sum())
            if iou < 0.05: continue
            res.append((dx*m_per_px, dy*m_per_px, float(iou), lon, lat))
        doc.close()
        if len(res) < 10:
            print(f"{q}: only {len(res)} usable samples"); continue
        arr = np.array([(r[0], r[1]) for r in res])
        s = np.hypot(arr[:,0], arr[:,1])
        print(f"{q}: n={len(res):4d}  median {np.median(s):7.1f} m  p90 {np.percentile(s,90):7.1f} m  "
              f"dlon {np.median(arr[:,0]):+7.1f}  dmerc {np.median(arr[:,1]):+7.1f}  "
              f"IoU {np.mean([r[2] for r in res]):.3f}")
        out[q] = {"n": len(res), "median_m": float(np.median(s)),
                  "p90_m": float(np.percentile(s, 90)),
                  "dlon_m": float(np.median(arr[:,0])), "dmerc_m": float(np.median(arr[:,1])),
                  "samples": res}
    json.dump(out, open(os.path.join(ROOT, "work", "newdp", "accuracy_by_block.json"), "w", encoding="utf-8"), indent=1)
    print("wrote work/newdp/accuracy_by_block.json")

if __name__ == "__main__":
    main()
