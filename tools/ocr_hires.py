"""High-resolution OCR pass over the AUDA sheet.

The default 3x pass used by ocr_plots.py is tuned for plot NUMBERS, but it
mangles the large place names (MOTERA, HANSOL, AHmedabad, Airport...) that a
georeference needs. At 4x those read at confidence 1.00, so this pass runs at
4x and keeps every detection with its glyph height, which lets the caller
split "big place name" from "tiny plot number" by size rather than by regex.

Resumable: a per-tile ledger (work/ocr4_done.txt) means a killed run costs
nothing and never double-writes a tile.

Outputs
  work/ocr4.ndjson    one JSON object per detection
"""
import argparse, json, math, os, time
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "work")
RAW = os.path.join(WORK, "source.rgb")
FULL_W, FULL_H = 27200, 25600
TILE = 1024
OVERLAP = 96
SCALE = 4
BOX_THRESH = 0.30
UNCLIP = 2.4
TEXT_SCORE = 0.30
_G = {}


def load_georef():
    with open(os.path.join(WORK, "georef.json", encoding="utf-8"), encoding="utf-8") as f:
        return json.load(f)


def to_lonlat(g, px, py):
    """Source pixel -> (lon, lat).

    Uses the fitted affine when the georeference has one. The affine is fitted
    in Mercator space (see tools/affine_fit.py), so latitude comes back via
    the inverse Mercator projection.
    """
    af = g.get("affine")
    if af:
        A = af["A"]
        lon = A[0][0] * px + A[0][1] * py + A[0][2]
        my = A[1][0] * px + A[1][1] * py + A[1][2]
        lat = math.degrees(2 * math.atan(math.exp(math.radians(my))) - math.pi / 2)
        return round(lon, 7), round(lat, 7)
    lon = g["lon_min"] + px / g["SX"]
    my = g["merc_top"] - py / g["SY"]
    lat = math.degrees(2 * math.atan(math.exp(math.radians(my))) - math.pi / 2)
    return round(lon, 7), round(lat, 7)


def content_tiles():
    mm = np.memmap(RAW, dtype=np.uint8, mode="r", shape=(FULL_H, FULL_W, 3))
    out = []
    nx = math.ceil(FULL_W / TILE)
    ny = math.ceil(FULL_H / TILE)
    for ty in range(ny):
        for tx in range(nx):
            x0, y0 = tx * TILE, ty * TILE
            blk = np.asarray(mm[y0:y0 + TILE:4, x0:x0 + TILE:4])
            mn = blk.min(axis=2)
            sat = blk.max(axis=2).astype(np.int16) - mn
            ink = float(((mn < 238) | (sat > 16)).mean())
            out.append((tx, ty, ink))
    mm._mmap.close()
    return out


def _init(g):
    import onnxruntime as ort
    if not getattr(ort.InferenceSession, "_rea_pinned", False):
        _orig = ort.InferenceSession.__init__

        def _patched(self, path_or_bytes, sess_options=None, providers=None, *a, **kw):
            if sess_options is None:
                so = ort.SessionOptions()
                so.intra_op_num_threads = 1
                so.inter_op_num_threads = 1
                sess_options = so
            return _orig(self, path_or_bytes, sess_options, providers, *a, **kw)
        ort.InferenceSession.__init__ = _patched
        ort.InferenceSession._rea_pinned = True
    _G["g"] = g
    _G["mm"] = np.memmap(RAW, dtype=np.uint8, mode="r", shape=(FULL_H, FULL_W, 3))


def _make_ocr():
    from rapidocr import RapidOCR
    return RapidOCR()


def _ocr_tile(job):
    if "ocr" not in _G:
        _G["ocr"] = _make_ocr()
    tx, ty = job
    g = _G["g"]
    mm = _G["mm"]
    ocr = _G["ocr"]

    x0 = tx * TILE
    y0 = ty * TILE
    x1 = min(x0 + TILE, FULL_W)
    y1 = min(y0 + TILE, FULL_H)
    if x1 - x0 < 32 or y1 - y0 < 32:
        return []

    crop = Image.fromarray(np.asarray(mm[y0:y1, x0:x1]))
    big = np.asarray(crop.resize((int((x1 - x0) * SCALE), int((y1 - y0) * SCALE)), Image.LANCZOS))
    try:
        out = ocr(big, box_thresh=BOX_THRESH, unclip_ratio=UNCLIP, text_score=TEXT_SCORE)
    except Exception:
        return []
    r = out[0] if isinstance(out, tuple) else out
    if r is None or r.txts is None or not len(r.txts):
        return []

    core_x0 = x0 + (OVERLAP if x0 > 0 else 0)
    core_y0 = y0 + (OVERLAP if y0 > 0 else 0)
    core_x1 = x1 - (OVERLAP if x1 < FULL_W else 0)
    core_y1 = y1 - (OVERLAP if y1 < FULL_H else 0)

    res = []
    for box, txt, sc in zip(r.boxes, r.txts, r.scores):
        cx = sum(p[0] for p in box) / len(box) / SCALE + x0
        cy = sum(p[1] for p in box) / len(box) / SCALE + y0
        if not (core_x0 <= cx < core_x1 and core_y0 <= cy < core_y1):
            continue
        h = (max(p[1] for p in box) - min(p[1] for p in box)) / SCALE
        w = (max(p[0] for p in box) - min(p[0] for p in box)) / SCALE
        t = " ".join(str(txt).split())
        if not t:
            continue
        lon, lat = to_lonlat(g, float(cx), float(cy))
        res.append({"t": t, "s": round(float(sc), 3), "px": int(cx), "py": int(cy),
                    "h": round(float(h), 1), "w": round(float(w), 1),
                    "lon": float(lon), "lat": float(lat)})
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", default=os.path.join(WORK, "ocr4.ndjson"))
    ap.add_argument("--ledger", default=os.path.join(WORK, "ocr4_done.txt"))
    ap.add_argument("--min-ink", type=float, default=0.004)
    ap.add_argument("--limit-tiles", type=int, default=0)
    a = ap.parse_args()

    g = load_georef()
    done = set()
    if os.path.exists(a.ledger):
        with open(a.ledger, encoding="utf-8") as fh:
            done = {ln.strip() for ln in fh if ln.strip()}
        print(f"resume: {len(done)} tiles already done", flush=True)

    tiles = [(tx, ty) for tx, ty, ink in content_tiles() if ink >= a.min_ink]
    jobs = [j for j in tiles if f"{j[0]},{j[1]}" not in done]
    if a.limit_tiles:
        jobs = jobs[:a.limit_tiles]
    print(f"{len(tiles)} content tiles, {len(jobs)} to OCR at {SCALE}x, {a.workers} workers",
          flush=True)

    mode = "a" if (done and os.path.exists(a.out) and os.path.getsize(a.out) > 0) else "w"
    total = 0
    t0 = time.time()
    n = 0
    _init(g)
    with open(a.out, mode, encoding="utf-8") as fh, \
         open(a.ledger, "a", encoding="utf-8") as ledger:
        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            futs = {ex.submit(_ocr_tile, j): j for j in jobs}
            for fut in as_completed(futs):
                job = futs[fut]
                try:
                    rows = fut.result()
                except Exception as e:
                    print(f"  ERR tile {job}: {e}", flush=True)
                    continue
                for row in rows:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()
                total += len(rows)
                ledger.write(f"{job[0]},{job[1]}\n")
                ledger.flush()
                n += 1
                if n % 20 == 0:
                    el = time.time() - t0
                    rate = n / el if el else 0
                    print(f"  {n}/{len(jobs)} tiles +{total} dets "
                          f"{el/60:.1f}min eta {((len(jobs)-n)/rate/60 if rate else 0):.1f}min",
                          flush=True)
    print(f"done: {total} detections in {(time.time()-t0)/60:.1f} min -> {a.out}", flush=True)


if __name__ == "__main__":
    main()
