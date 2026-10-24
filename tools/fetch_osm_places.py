"""Fetch every OSM place feature in the sheet's bbox as a gazetteer.

One Overpass query, so no rate-limit battles: the result is the ground truth
that the place names burned into the scan get matched against.

Output: work/osm_places.json  [{name, lat, lon, kind, osm_id}]
"""
import json, os, time
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "work")
BL = (22.763306, 72.178109)
TR = (23.357406, 72.860675)
OUT = os.path.join(WORK, "osm_places.json")

Q = f"""
[out:json][timeout:180];
(
  node["place"~"^(city|town|village|hamlet|suburb|quarter|neighbourhood|locality|island)$"]
       ({BL[1]},{BL[0]},{TR[1]},{TR[0]});
  way["place"~"^(city|town|village|hamlet|suburb|quarter|neighbourhood|locality|island)$"]
      ({BL[1]},{BL[0]},{TR[1]},{TR[0]});
  way["name"~"^(MOTERA|BHAT|HANSOL|ASARVA|NARODA|RAMOL|SANAND|VASNA|SARKHEJ|BOPAL)$"]
      ({BL[1]},{BL[0]},{TR[1]},{TR[0]});
);
out center tags;
"""


def main():
    for attempt in range(4):
        try:
            r = requests.post("https://overpass-api.de/api/interpreter", data={"data": Q},
                              timeout=240, headers={"User-Agent": "geomapping-gcp/1.0"})
            r.raise_for_status()
            data = r.json()
            break
        except Exception as e:
            print(f"attempt {attempt+1} failed: {e}")
            time.sleep(10)
    else:
        raise SystemExit("overpass unavailable")

    rows = []
    for el in data.get("elements", []):
        t = el.get("tags") or {}
        name = t.get("name") or t.get("name:en")
        if not name:
            continue
        if el["type"] == "node":
            lat, lon = el["lat"], el["lon"]
        else:
            c = el.get("center") or {}
            lat, lon = c.get("lat"), c.get("lon")
        if lat is None or lon is None:
            continue
        rows.append(dict(name=name, lat=lat, lon=lon,
                         kind=t.get("place") or "way",
                         osm_type=el["type"], osm_id=el["id"],
                         en=t.get("name:en") or ""))
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=1)
    print(f"{len(rows)} places -> {OUT}")
    kinds = {}
    for r in rows:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    print(kinds)


if __name__ == "__main__":
    main()
