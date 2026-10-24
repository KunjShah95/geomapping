"""List the dated satellite captures available over AUDA, for the viewer's imagery picker.

Esri World Imagery Wayback keeps every release of World Imagery since 2014.
A release date is not a capture date: most releases reuse the same photo over
Ahmedabad. So each release's metadata layer is queried at a few points across
the plan, and only releases where the capture changed are kept, labelled by
the capture date (what the user cares about), not the release date.

The 2021 entry is the default because the plan is DP 2021: comparing it to
imagery of its own era keeps road widening and new construction since then
out of the alignment check.

Output: data/dp2021/imagery.json
"""
import concurrent.futures as cf
import json
import os
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "dp2021", "imagery.json")
CONFIG = "https://s3-us-west-2.amazonaws.com/config.maptiles.arcgis.com/waybackconfig.json"
# centre, west, north, east of the plan: a release counts as new when any changes
POINTS = {"centre": (72.5713, 23.0223), "west": (72.4650, 23.0330),
          "north": (72.5000, 23.2400), "east": (72.7050, 23.0560)}
FROM_RELEASE = "2017-01-01"
DEFAULT_YEAR = "2021"


def get_json(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def capture_at(meta_url, lon, lat):
    qs = urllib.parse.urlencode(dict(
        geometry=f"{lon},{lat}", geometryType="esriGeometryPoint", inSR=4326,
        outFields="SRC_DATE,SRC_DESC,SRC_RES,NICE_DESC", returnGeometry="false", f="json"))
    # layer 5 is the finest-scale footprint layer, the one shown at z16+
    feats = get_json(f"{meta_url}/5/query?{qs}").get("features") or []
    return feats[0]["attributes"] if feats else None


def main():
    cfg = get_json(CONFIG)
    releases = sorted(
        (v["itemTitle"].rsplit(" ", 1)[-1].strip(")"), k, v) for k, v in cfg.items())
    releases = [r for r in releases if r[0] >= FROM_RELEASE]
    jobs = [(rel, name, pt) for rel in releases for name, pt in POINTS.items()]

    found = {}
    with cf.ThreadPoolExecutor(8) as ex:
        futs = {ex.submit(capture_at, rel[2]["metadataLayerUrl"], *pt): (rel, name)
                for rel, name, pt in jobs}
        for f in cf.as_completed(futs):
            rel, name = futs[f]
            try:
                found.setdefault(rel[1], {})[name] = f.result()
            except Exception as e:  # one failed point should not drop the release
                print(f"  {rel[0]} {name}: {e}")

    entries, prev = [], None
    for date, rid, item in releases:
        caps = found.get(rid, {})
        if len(caps) < len(POINTS) or any(c is None for c in caps.values()):
            continue
        key = tuple(caps[n]["SRC_DATE"] for n in POINTS)
        if key == prev:
            continue
        prev = key
        c = caps["centre"]
        d = str(c["SRC_DATE"])
        entries.append({
            "id": f"wayback-{rid}",
            "release": date,
            "capture": f"{d[:4]}-{d[4:6]}-{d[6:]}",
            "captures": {n: caps[n]["SRC_DATE"] for n in POINTS},
            "sensor": c.get("SRC_DESC"), "provider": c.get("NICE_DESC"),
            "res_m": c.get("SRC_RES"),
            "label": f"{d[:4]}-{d[4:6]} ({c.get('NICE_DESC') or 'Esri'})",
            "tiles": item["itemURL"].replace("{level}", "{z}").replace("{row}", "{y}").replace("{col}", "{x}"),
        })

    default = next((e["id"] for e in entries if e["capture"].startswith(DEFAULT_YEAR)),
                   entries[-1]["id"])
    out = {"source": "Esri World Imagery Wayback", "config": CONFIG,
           "attribution": "Esri World Imagery Wayback; imagery © its providers (Maxar, Vantor, et al.)",
           "default": default,
           "note": "Labels are the capture date over the centre of the plan. Captures can differ "
                   "slightly across the area; see `captures`.",
           "releases": entries}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    for e in entries:
        mark = "  <- default" if e["id"] == default else ""
        print(f"  release {e['release']}  capture {e['capture']}  {e['provider']} {e['res_m']} m{mark}")
    print(f"wrote {OUT}: {len(entries)} distinct captures")


if __name__ == "__main__":
    main()
