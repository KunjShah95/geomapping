"""Is the built pyramid pixel-faithful to the source PDFs?

For a sample of tiles it renders the same ground from every sheet that covers
it, through the tile builder's own code path, merges them the way the
builder does, and compares that with the tile that was actually written. If
they agree, the pyramid is faithful and any white patch on the map is white
on the sheet.

Output: work/newdp/fidelity.json
"""
import json, os, random, sys
import fitz, numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dp_georef as G
import dp_build_tiles as B

ROOT = G.ROOT
TILES = os.path.join(ROOT, "tiles_dp2021")


def main():
    z = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    random.seed(11)

    blocks, grid = G.build_blocks()
    dls = {}
    for q, b in blocks.items():
        doc = fitz.open(b.path)
        dls[q] = (doc, doc[0].get_displaylist(), b, doc[0].cropbox)

    cands = []
    zdir = os.path.join(TILES, str(z))
    for txd in os.listdir(zdir):
        for f in os.listdir(os.path.join(zdir, txd)):
            cands.append((int(txd), int(os.path.splitext(f)[0])))
    picks = random.sample(cands, min(n, len(cands)))
    print(f"comparing {len(picks)} tiles at z{z}")

    rows = []
    for tx, ty in picks:
        path = os.path.join(TILES, str(z), str(tx), f"{ty}.webp")
        with Image.open(path) as im:
            built = np.asarray(im.convert("RGB")).astype(np.int16)

        lon_l = tx * 256 / (256 * 2 ** z) * 360.0 - 180.0
        lon_r = (tx + 1) * 256 / (256 * 2 ** z) * 360.0 - 180.0
        lat_t = B.py2lat(ty * 256, z)
        lat_b = B.py2lat((ty + 1) * 256, z)

        covers = []
        for q, (doc, dl, b, cropbox) in dls.items():
            if not (b.lon_min - 0.2 <= lon_l and lon_r <= b.lon_max + 0.2):
                continue
            if not (b.lat_min - 0.2 <= lat_b and lat_t <= b.lat_max + 0.2):
                continue
            mx, my = G.INV.transform(np.array([lon_l, lon_r, lon_r, lon_l]),
                                     np.array([lat_t, lat_t, lat_b, lat_b]))
            ux = (mx - b.e0) / b.s + b.llx
            uy = (my - b.n0) / b.s + b.ury
            q4 = list(zip(ux, uy))
            w_pt = max(p[0] for p in q4) - min(p[0] for p in q4)
            h_pt = max(p[1] for p in q4) - min(p[1] for p in q4)
            if w_pt <= 0 or h_pt <= 0:
                continue
            zoom = 256 / max(min(w_pt, h_pt), 1e-6)
            arr = B.render_tile(dl, q4, zoom, cropbox)
            if arr is None:
                continue
            covers.append((B.ink_fraction(arr), arr))
        if not covers:
            rows.append({"tx": tx, "ty": ty, "error": "no source coverage"})
            continue
        merged, blank = B.merge_covers(covers)
        d = np.abs(merged.astype(np.int16) - built)
        rows.append({
            "tx": tx, "ty": ty, "sheets": len(covers),
            "mean_abs_diff": float(d.mean()),
            "max_abs_diff": int(d.max()),
            "pct_pixels_over_8": float((d.max(axis=2) > 8).mean()),
        })

    ok = [r for r in rows if "mean_abs_diff" in r]
    mad = np.array([r["mean_abs_diff"] for r in ok])
    big = np.array([r["pct_pixels_over_8"] for r in ok])
    print("")
    print(f"  compared {len(ok)} tiles against a fresh merged render of the source")
    print(f"  mean absolute difference: median {np.median(mad):.2f}/255, "
          f"p90 {np.percentile(mad, 90):.2f}, max {mad.max():.2f}")
    print(f"  pixels differing by >8: median {np.median(big)*100:.3f}%, "
          f"p90 {np.percentile(big, 90)*100:.3f}%, max {big.max()*100:.3f}%")
    worst = sorted(ok, key=lambda r: -r["mean_abs_diff"])[:5]
    print("  worst tiles:")
    for r in worst:
        print(f"    z{z}/{r['tx']}/{r['ty']}  sheets {r['sheets']}  "
              f"mean {r['mean_abs_diff']:.2f}  over8 {r['pct_pixels_over_8']*100:.2f}%")
    json.dump({"z": z, "rows": rows}, open(os.path.join(ROOT, "work", "newdp", "fidelity.json"), "w", encoding="utf-8"), indent=1)


if __name__ == "__main__":
    main()
