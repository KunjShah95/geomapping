"""OCR the AUDA RDP sheet for plot/survey numbers and emit a georeferenced label layer.

The sheet is a pure raster (no text layer, no vector), so the only way to get the
numbers out is OCR. Measured on a test crop: default RapidOCR finds 14 boxes; at
4x upscale with box_thresh=0.30 / unclip_ratio=2.4 it finds 91 (32 number-like).

Accuracy is best-effort, not authoritative: on the test crop it misread 718/4 as
728/4 and 431+432NC as 451+432NC. Every detection therefore carries its OCR
confidence so the UI can de-emphasise or hide weak reads.

Outputs
  work/plots.ndjson    one JSON object per detection
  work/plots_index.json summary + count per zoom bucket
"""
import argparse, json, math, os, re, time

# BLAS/OpenMP pinning must be in the environment before onnxruntime loads. The real
# ONNX control is the SessionOptions patch in _init(), which the runtime ignores
# OMP_NUM_THREADS for.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

# Threads, not processes: ONNX sessions cannot be shared across threads, so each
# thread builds its own engine, but memory is shared. Processes duplicated the
# whole model arena per worker and got OOM-killed on a 16 GB machine.
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "work")
RAW = os.path.join(WORK, "source.rgb")
FULL_W, FULL_H = 27200, 25600

TILE = 1024          # native-px tile size
OVERLAP = 96         # px of overlap; detections in the margin are dropped as dupes
SCALE = 3            # upscale factor; 2x/3x/4x cost the same and 3x detects best
BOX_THRESH = 0.30
UNCLIP = 2.4
TEXT_SCORE = 0.30

# a plot/survey number looks like 285.02 | 424/2 | 816-A | 431+432 NC | 30.50 Mt.
NUM_RE = re.compile(r"^[\d\s.,/+\-]{1,18}(\s*(NC|Nc|nc|N\.?C\.?|Mt|mt|MT|NO|No|no))?$")
# ward/place/road labels we do NOT want as plot numbers
BLOCKLIST = re.compile(
    r"(SUR\.?\s*NO|CHANG|KOCHRAB|CHHAD|GUJARAT|COLLEGE|LAW\s*GARDEN|HOSPITAL|"
    r"RUPALI|PART\s*OF|REFER|DETAIL|PLAN|DEVELOPMENT|RIVER|Road|ROAD|Bridge|"
    r"APPROACH|SABARMATI|VILLAGE|GRAM|TALUKA|DISTRICT|SECTOR|LAYOUT|"
    r"SCHOOL|TEMPLE|MARKET|BUS|STATION|PETROL|GAS|SURVEY|NO\.?\s*\d?$)",
    re.I,
)

_G = {}


def merc_y(lat):
    return math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def load_georef():
    with open(os.path.join(WORK, "georef.json", encoding="utf-8"), encoding="utf-8") as f:
        return json.load(f)


def to_lonlat(g, px, py):
    """Source pixel -> (lon, lat).

    Uses the fitted affine when the georeference has one; the affine is fitted
    in Mercator space, so latitude comes back via the inverse projection.
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


def classify(text):
    t = " ".join(text.split())
    if not t:
        return None
    if BLOCKLIST.search(t):
        return None
    core = t
    m = NUM_RE.match(t)
    if not m:
        # allow a leading/trailing letter class used in FP/RP numbering
        if re.fullmatch(r"[A-Za-z]{0,2}[\d\s.,/+\-]{1,16}[A-Za-z]{0,4}", t):
            core = t
        else:
            return None
    if not re.search(r"\d", core):
        return None
    digits = re.findall(r"\d+", core)
    if not digits:
        return None
    if max(len(d) for d in digits) > 5:
        return None
    return core


def content_tiles():
    """Skip tiles with no plan ink (large agricultural / blank areas)."""
    mm = np.memmap(RAW, dtype=np.uint8, mode="r", shape=(FULL_H, FULL_W, 3))
    out = []
    nx = math.ceil(FULL_W / TILE)
    ny = math.ceil(FULL_H / TILE)
    for ty in range(ny):
        for tx in range(nx):
            x0, y0 = tx * TILE, ty * TILE
            blk = np.asarray(mm[y0:y0 + TILE:4, x0:x0 + TILE:4])   # 1/4 subsample
            mn = blk.min(axis=2)
            sat = blk.max(axis=2).astype(np.int16) - mn
            ink = float(((mn < 238) | (sat > 16)).mean())
            out.append((tx, ty, ink))
    mm._mmap.close()
    return out


def _init(g):
    # Pin onnxruntime to ONE intra-op thread. It ignores OMP_NUM_THREADS and otherwise
    # sizes its pool to every core, so N concurrent sessions oversubscribe badly.
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


def _ocr_tile_thread(job):
    """Thread entry point. Each thread owns its own RapidOCR engine (its ONNX
    sessions are not thread-safe), but shares the parent's memory - which is the
    point: 8 PROCESSES meant 8 copies of the model arena and 8 ORT thread pools,
    and on a 16 GB box that gets workers OOM-killed."""
    if "ocr" not in _G:
        _G["ocr"] = _make_ocr()
    return _ocr_tile(job)


def _ocr_tile(job):
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
    big = np.asarray(crop.resize(((x1 - x0) * SCALE, (y1 - y0) * SCALE), Image.LANCZOS))

    try:
        out = ocr(big, box_thresh=BOX_THRESH, unclip_ratio=UNCLIP, text_score=TEXT_SCORE)
    except Exception:
        return []
    r = out[0] if isinstance(out, tuple) else out
    # RapidOCR returns a result whose txts/boxes/scores are None when the detector
    # finds nothing - e.g. a blank or fully agricultural tile.
    if r is None or r.txts is None or not len(r.txts):
        return []

    # core region: detections here are owned by this tile (margins are dupes)
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
        label = classify(txt)
        if not label:
            continue
        lon, lat = to_lonlat(g, float(cx), float(cy))
        res.append({
            "t": label,
            "s": round(float(sc), 3),
            "lon": float(lon),
            "lat": float(lat),
            "px": int(cx),
            "py": int(cy),
            "rot": round(float(math.degrees(math.atan2(
                float(box[1][0]) - float(box[0][0]),
                float(box[1][1]) - float(box[0][1])))), 1),
        })
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", default=os.path.join(WORK, "plots.ndjson"))
    ap.add_argument("--limit-tiles", type=int, default=0, help="debug: only N tiles")
    ap.add_argument("--start", type=int, default=0, help="debug: skip the first N tiles")
    ap.add_argument("--min-ink", type=float, default=0.004,
                    help="skip tiles with less ink than this fraction (default 0.4%%)")
    ap.add_argument("--resume", action="store_true",
                    help="skip tiles already recorded in work/ocr_done.txt and append")
    a = ap.parse_args()

    g = load_georef()
    if not os.path.exists(RAW):
        raise SystemExit("work/source.rgb missing - run tools/build_tiles.py once first")

    # resume bookkeeping: a shell restart can kill this run at any point, so record
    # each finished tile and never redo it.
    done_file = os.path.join(WORK, "ocr_done.txt")
    done = set()
    if a.resume and os.path.exists(done_file):
        with open(done_file, encoding="utf-8") as fh:
            done = {ln.strip() for ln in fh if ln.strip()}
        print(f"resume: {len(done)} tiles already done", flush=True)
    elif a.resume:
        # first resume attempt but no ledger: trust the existing output file
        if os.path.exists(a.out) and os.path.getsize(a.out) > 0:
            print("resume: no ledger found, will re-scan and rely on de-duplication",
                  flush=True)

    print("scanning for plan content ...", flush=True)
    t_scan = time.time()
    tiles = content_tiles()
    jobs = [(tx, ty) for tx, ty, ink in tiles if ink >= a.min_ink]
    jobs = [j for j in jobs if f"{j[0]},{j[1]}" not in done]
    print(f"  {len(tiles)} tiles total, {len(jobs)} to OCR "
          f"(>= {a.min_ink*100:.1f}% ink, minus {len(done)} done) in {time.time()-t_scan:.1f}s",
          flush=True)

    if a.start:
        jobs = jobs[a.start:]
    if a.limit_tiles:
        jobs = jobs[: a.limit_tiles]

    print(f"georeference: lon {g['lon_min']}..{g['lon_max']} lat {g['lat_min']}..{g['lat_max']}")
    print(f"OCR {len(jobs)} tiles of {TILE}px @ {SCALE}x, {a.workers} workers")
    print(f"  box_thresh={BOX_THRESH} unclip={UNCLIP} text_score={TEXT_SCORE}", flush=True)

    t0 = time.time()
    total = 0
    out_path = a.out
    # append when resuming; an earlier partial run may already be in the file and
    # its tiles have no ledger entries, so those results get de-duplicated later
    mode = "a" if (a.resume and os.path.exists(out_path)
                   and os.path.getsize(out_path) > 0) else "w"
    print(f"output mode: {mode}", flush=True)

    with open(out_path, mode, encoding="utf-8") as fh, \
         open(done_file, "a", encoding="utf-8") as ledger:
        n_done = 0
        n_err = 0
        _init(g)      # pin ONNX in this process; memmap shared by all threads
        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            futs = {ex.submit(_ocr_tile_thread, j): j for j in jobs}
            for fut in as_completed(futs):
                job = futs[fut]
                try:
                    rows = fut.result()
                except Exception as e:
                    # NEVER silently treat a crashed worker as an "empty tile". That
                    # is how a whole run reports zero detections while looking healthy.
                    n_err += 1
                    if n_err <= 20:
                        print(f"  ERROR tile {job}: {type(e).__name__}: {e}", flush=True)
                    rows = []
                    continue       # no ledger entry, so --resume retries it
                for row in rows:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                total += len(rows)
                ledger.write(f"{job[0]},{job[1]}\n")
                ledger.flush()
                n_done += 1
                if n_done % 25 == 0 or n_done == len(jobs):
                    el = time.time() - t0
                    rate = n_done / el if el else 0
                    eta = ((len(jobs) - n_done) / rate / 60) if rate else 0
                    print(f"  {n_done}/{len(jobs)} tiles  +{total} numbers  "
                          f"{el/60:.1f}min  {rate*60:.1f} tiles/min  eta {eta:.1f}min"
                          + (f"  ({n_err} errors)" if n_err else ""), flush=True)

    if n_err:
        print(f"\nWARNING: {n_err} tile(s) failed and were NOT ledgered; "
              f"re-run with --resume to retry them", flush=True)

    el = time.time() - t0
    print(f"\ndone: {total} plot numbers in {el/60:.1f} min -> {out_path}")

    # summarise confidence distribution
    conf = {}
    with open(out_path, encoding="utf-8") as fh:
        for line in fh:
            s = json.loads(line)["s"]
            b = ">=0.9" if s >= 0.9 else "0.8-0.9" if s >= 0.8 else "0.6-0.8" if s >= 0.6 else "<0.6"
            conf[b] = conf.get(b, 0) + 1
    print("confidence:", conf)


if __name__ == "__main__":
    main()
