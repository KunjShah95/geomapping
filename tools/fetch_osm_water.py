import json, math, urllib.request, urllib.parse, os

BBOX = (22.70, 72.10, 23.42, 72.92)  # S, W, N, E  (generous margin around the sheet)

Q = f"""
[out:json][timeout:180];
(
  way["natural"="water"]({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});
  relation["natural"="water"]({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});
  way["waterway"~"^(river|riverbank|canal|stream)$"]({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});
  way["landuse"="reservoir"]({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});
);
out geom;
"""

url = "https://overpass-api.de/api/interpreter"
req = urllib.request.Request(
    url, data=Q.encode(), headers={"User-Agent": "townplanmap-georeference/1.0 (local research script)"}
)
print("querying overpass ...", flush=True)
with urllib.request.urlopen(req, timeout=240) as r:
    data = json.load(r)
print("elements:", len(data.get("elements", [])))

os.makedirs(r"C:\ideabot internships\geomapping\work", exist_ok=True)
p = r"C:\ideabot internships\geomapping\work\osm_water.json"
with open(p, "w", encoding="utf-8") as f:
    json.dump(data, f)
print("wrote", p, os.path.getsize(p))

kinds = {}
for e in data["elements"]:
    k = e.get("tags", {}).get("natural") or e.get("tags", {}).get("waterway") or e.get("tags", {}).get("landuse") or "?"
    kinds[k] = kinds.get(k, 0) + 1
print("kinds:", kinds)
