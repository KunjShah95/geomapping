"""Fetch Google satellite imagery tiles for the Ahmedabad (AUDA) bounding box.

Saves into satellite/{z}/{x}/{y}.jpg in slippy-map (XYZ) layout, matching the
existing tiles/ pyramid convention. Resumable: already-present, non-empty files
are skipped.

Uses the official Google Map Tiles API (2D tiles, mapType "satellite"):

    1. POST https://tile.googleapis.com/v1/createSession?key=KEY
       {"mapType": "satellite", "language": "en-US", "region": "IN"}
       -> {"session": "...", "expiry": "..."}
    2. GET  https://tile.googleapis.com/v1/2dtiles/{z}/{x}/{y}?session=...&key=KEY

Needs GOOGLE_API_KEY in the environment (never hardcode it), with "Map Tiles
API" enabled on its Google Cloud project:

    node --env-file=.env ...   or   $env:GOOGLE_API_KEY = "..."; python tools/fetch_google_satellite.py

Google's terms limit caching of tiles and forbid using them to derive or
correct map data. Use these tiles for viewing/visual checks only; the ground
correction is fitted to OSM (tools/dp_osm_offsets.py), not to these.

The unofficial mt*.google.com endpoint this script used to fall back to has
been removed: scraping it violates Google's Terms of Service.
"""
import concurrent.futures
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request

BL = (22.763306, 72.178109)   # lat, lon  (south-west)
TR = (23.357406, 72.860675)   # lat, lon  (north-east)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "satellite")
ZMIN, ZMAX = 11, 16
WORKERS = 8
API = "https://tile.googleapis.com/v1"

KEY = os.environ.get("GOOGLE_API_KEY")
SESSION = None


def create_session():
    body = json.dumps({"mapType": "satellite", "language": "en-US", "region": "IN"}).encode()
    req = urllib.request.Request(f"{API}/createSession?key={KEY}", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)["session"]
    except urllib.error.HTTPError as e:
        sys.exit(f"createSession failed ({e.code}). Is the Map Tiles API enabled for this key? "
                 f"{e.read()[:300].decode(errors='replace')}")


def deg2tile(lat, lon, z):
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def tile_url(z, x, y):
    return f"{API}/2dtiles/{z}/{x}/{y}?session={SESSION}&key={KEY}"


def fetch_one(z, x, y):
    p = os.path.join(OUT, str(z), str(x), f"{y}.jpg")
    if os.path.exists(p) and os.path.getsize(p) > 0:
        return "skip", p
    os.makedirs(os.path.dirname(p), exist_ok=True)
    url = tile_url(z, x, y)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                data = r.read()
            if len(data) < 200:
                raise Exception("too small")
            with open(p, "wb") as f:
                f.write(data)
            return "ok", p
        except Exception:
            time.sleep(1 + attempt)
    return "err", f"{z}/{x}/{y}"


def jobs():
    for z in range(ZMIN, ZMAX + 1):
        nx, ny = deg2tile(*TR, z)
        sx, sy = deg2tile(*BL, z)
        x0, x1 = int(min(nx, sx)), int(max(nx, sx))
        y0, y1 = int(min(ny, sy)), int(max(ny, sy))
        n = 2 ** z
        for y in range(max(0, y0), min(n, y1 + 1)):
            for x in range(max(0, x0), min(n, x1 + 1)):
                yield z, x, y


def main():
    global SESSION
    if not KEY:
        sys.exit("GOOGLE_API_KEY is not set. Put it in .env and run with the variable loaded; "
                 "it is never hardcoded in this script.")
    SESSION = create_session()
    os.makedirs(OUT, exist_ok=True)
    jl = list(jobs())
    print(f"total tiles: {len(jl)}")
    done = skip = err = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(fetch_one, z, x, y): (z, x, y) for z, x, y in jl}
        for fut in concurrent.futures.as_completed(futs):
            status, _ = fut.result()
            if status == "ok":
                done += 1
            elif status == "skip":
                skip += 1
            else:
                err += 1
            if (done + skip + err) % 200 == 0:
                print(f"progress: ok={done} skip={skip} err={err}")
    print(f"FINAL: ok={done} skip={skip} err={err}")


if __name__ == "__main__":
    main()
