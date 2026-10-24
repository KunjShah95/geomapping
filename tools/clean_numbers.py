"""Clean and merge OCR detections into real plot numbers.

Two problems this fixes, both visible in the shipped data/plots.geojson:

1. FRAGMENTS. The detector often splits one printed number into two boxes
   ("285" + "/2"), and the halves both get emitted. The result is a layer full
   of 1-3 character numbers that are not numbers at all - 42,281 features with
   only 7,355 distinct texts, the most common being "2", "22", "7".

2. NEAR-DUPLICATES. Re-scanned tiles (the run is resumable and a killed run
   has no per-tile ledger for the tiles it was working on) leave the same
   number in the file twice, a few pixels apart. De-duplication on exact
   text+position does not catch those, because the position differs by a pixel.

Fix:
  a. GROUP detections into text runs. Two boxes join the same run when their
     vertical spans overlap and the horizontal gap is small relative to glyph
     height - the signature of one continuous text line on a map.
  b. MERGE each run into one detection, joining the text with no separator and
     keeping the lowest member score (a run is only as good as its worst part).
  c. REJECT fragments that survived merging, using the shape of a real AUDA
     survey number: it has a separator (/ . + -) or a suffix (NC, MT, A, B),
     or it is a bare 3+ digit number. A bare 1-2 digit token is a fragment
     unless it is a legitimately large plot number.
  d. DE-DUPLICATE by text + rounded position, so the same number found twice
     a few pixels apart is emitted once.

Input : work/ocr4.ndjson (or work/plots.ndjson)
Output: work/numbers_clean.ndjson
"""
import argparse, json, math, os, re
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "work")

# A real AUDA plot/survey number looks like:
#   285.02 | 424/2 | 816-A | 431+432 NC | 30.50 Mt | 118/3 | 264
SEPARATOR = re.compile(r"[./+\-]")
SUFFIX = re.compile(r"(NC|N\.?C\.?|MT|MT\.?|A|B|C|D|M|N|P|S)$", re.I)
LETTER_PREFIX = re.compile(r"^(FP|RP|SP|OP|MU|SR|PL|GP|SE|SW|NE|NW)\b", re.I)

# a bare token of 1-2 digits is almost always a fragment
BARE_MIN_DIGITS = 3


def norm_text(t):
    return " ".join(str(t).split())


def load(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "px" not in r or "py" not in r:
                continue
            r["t"] = norm_text(r.get("t", ""))
            if not r["t"]:
                continue
            rows.append(r)
    return rows


def group_runs(rows, gap_factor=0.75, v_overlap=0.45, max_boxes=4, max_chars=18):
    """Group detections into text runs (one printed label).

    Two guards keep this from over-merging, which is the failure mode that
    produces junk like "42942942424249944944422":
      * max_boxes  - a printed number is never 5+ separate boxes
      * vertical overlap must be large, so a stack of numbers in adjacent
        plots (which share a line band) does not chain together
    """
    # bucket by rough text-line so the pairing stays local
    by_line = defaultdict(list)
    for r in rows:
        # line key: vertical band, tolerant of small vertical jitter
        by_line[round(r["py"] / 24.0)].append(r)

    runs = []
    for _, bucket in by_line.items():
        bucket.sort(key=lambda r: r["px"])
        cur = []
        cur_right = None
        for r in bucket:
            h = r.get("h") or 16.0
            left = r["px"] - r.get("w", 0) / 2
            right = r["px"] + r.get("w", 0) / 2
            if cur:
                gap = left - cur_right
                # vertical overlap between the two boxes
                prev = cur[-1]
                ph = prev.get("h") or 16.0
                top_a, bot_a = prev["py"] - ph / 2, prev["py"] + ph / 2
                top_b, bot_b = r["py"] - h / 2, r["py"] + h / 2
                ov = min(bot_a, bot_b) - max(top_a, top_b)
                minh = min(ph, h)
                # a character gap is small; the gap between two DIFFERENT
                # numbers in neighbouring plots is a full glyph or more
                joined = len("".join(x["t"] for x in cur)) + len(r["t"])
                if (gap < gap_factor * minh and ov > v_overlap * minh
                        and len(cur) < max_boxes and joined <= max_chars):
                    cur.append(r)
                    cur_right = max(cur_right, right)
                    continue
                runs.append(cur)
                cur = [r]
                cur_right = right
            else:
                cur = [r]
                cur_right = right
        if cur:
            runs.append(cur)
    return runs


def merge(run):
    """Merge one run into a single detection."""
    run = sorted(run, key=lambda r: r["px"])
    text = "".join(r["t"] for r in run)
    ws = [r.get("w", 0) for r in run]
    hs = [r.get("h", 16.0) for r in run]
    x0 = min(r["px"] - r.get("w", 0) / 2 for r in run)
    x1 = max(r["px"] + r.get("w", 0) / 2 for r in run)
    y0 = min(r["py"] - r.get("h", 16.0) / 2 for r in run)
    y1 = max(r["py"] + r.get("h", 16.0) / 2 for r in run)
    out = dict(run[0])
    out["t"] = text
    out["s"] = round(min(r.get("s", 0.0) for r in run), 3)
    out["px"] = int(round((x0 + x1) / 2))
    out["py"] = int(round((y0 + y1) / 2))
    out["w"] = round(x1 - x0, 1)
    out["h"] = round(max(hs), 1)
    out["n"] = len(run)
    return out


def is_real_number(t, allow_bare=()):
    """Does this look like a genuine AUDA plot/survey number?"""
    t = t.strip()
    if not t:
        return False
    if len(t) > 16:
        return False
    if LETTER_PREFIX.match(t):
        return True

    # The recogniser sometimes returns a run of repeated digits for a single
    # stretched box ("42942942424249944944422"). Those are not numbers, and
    # they are long, so reject anything that is mostly one repeated token.
    toks = re.findall(r"\d+", t)
    if toks:
        joined = "".join(toks)
        if len(joined) >= 6:
            # the longest single token must account for most of the digits
            if max(len(x) for x in toks) < 0.6 * len(joined):
                return False
            if len(toks) > 2:
                return False

    if SEPARATOR.search(t):
        # must still have digits and no letters except a known suffix
        core = SUFFIX.sub("", t)
        if not re.search(r"\d", core):
            return False
        letters = re.sub(r"[^A-Za-z]", "", core)
        if letters and not re.fullmatch(r"(FP|RP|SP|OP|MU|SR|PL|GP|SE|SW|NE|NW)", letters):
            return False
        # a number with a separator has at most 2 numeric groups
        if len(re.findall(r"\d+", core)) > 2:
            return False
        return True
    if SUFFIX.search(t) and re.search(r"\d", t):
        return True
    digits = re.findall(r"\d+", t)
    if not digits:
        return False
    if len(digits) > 1:
        return False
    d = digits[0]
    if len(d) >= BARE_MIN_DIGITS:
        return True
    # short bare number: only keep if the caller says so (e.g. a known short form)
    return t in allow_bare


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", default=os.path.join(WORK, "ocr4.ndjson"))
    ap.add_argument("--out", default=os.path.join(WORK, "numbers_clean.ndjson"))
    ap.add_argument("--min-conf", type=float, default=0.45)
    ap.add_argument("--gap-factor", type=float, default=0.75)
    ap.add_argument("--dedupe-px", type=int, default=12,
                    help="positions closer than this in both axes count as the same label")
    ap.add_argument("--keep-bare-short", action="store_true",
                    help="keep bare 1-2 digit tokens (they are usually fragments)")
    a = ap.parse_args()

    rows = load(a.src)
    print(f"{len(rows)} raw detections")
    rows = [r for r in rows if r.get("s", 0) >= a.min_conf]
    print(f"{len(rows)} at conf >= {a.min_conf}")

    runs = group_runs(rows, a.gap_factor)
    print(f"{len(runs)} text runs")

    merged = [merge(run) for run in runs]
    n_multi = sum(1 for m in merged if m["n"] > 1)
    print(f"{n_multi} runs absorbed more than one box")

    allow = set() if a.keep_bare_short else set()
    kept, dropped_frag = [], 0
    for m in merged:
        if is_real_number(m["t"], allow):
            kept.append(m)
        else:
            dropped_frag += 1
    print(f"dropped {dropped_frag} fragments / non-numbers, kept {len(kept)}")

    # de-duplicate: same text within a small box counts as one label
    seen = {}
    out = []
    dupes = 0
    cell = a.dedupe_px
    for m in kept:
        key = (m["t"], m["px"] // cell, m["py"] // cell)
        prev = seen.get(key)
        if prev is not None:
            dupes += 1
            # keep the higher-confidence copy
            if m["s"] > prev["s"]:
                out[out.index(prev)] = m
                seen[key] = m
            continue
        seen[key] = m
        out.append(m)
    print(f"dropped {dupes} near-duplicates, {len(out)} final numbers")

    with open(a.out, "w", encoding="utf-8") as fh:
        for m in out:
            fh.write(json.dumps(m, ensure_ascii=False) + "\n")
    print(f"wrote {a.out}")

    dist = defaultdict(int)
    for m in out:
        d = len(m["t"])
        dist[min(d, 12)] += 1
    print("text length histogram:", dict(sorted(dist.items())))


if __name__ == "__main__":
    main()
