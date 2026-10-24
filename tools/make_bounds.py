"""Emit the sheet's georeference as a portable, versioned artefact.

The bounds currently live as hardcoded constants in web/index.html. That is a
liability: the weak link in this project is the georeference, and duplicating it
across the tile builder, the OCR and the viewer means a correction has to be made
in several places at once. One artefact, read by everything, is the fix.

Outputs
  data/bounds.json    extent, scale, resolution, provenance and accuracy caveat
  data/bounds.geojson a GeoJSON FeatureCollection polygon, readable by any GIS
"""
import json, math, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "work")


def merc_y(lat):
    return math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def main():
    with open(os.path.join(WORK, "georef.json", encoding="utf-8"), encoding="utf-8") as f:
        g = json.load(f)

    lon_min, lon_max = g["lon_min"], g["lon_max"]
    lat_min, lat_max = g["lat_min"], g["lat_max"]

    ring = [[lon_min, lat_min], [lon_max, lat_min], [lon_max, lat_max], [lon_min, lat_max]]
    fc = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {
                "id": "auda-rdp-2022",
                "title": "AUDA Revised Development Plan 2022",
                "crs": "EPSG:3857",
                "m_per_px": round(g["m_per_px"], 4),
                "native_zoom": g["native_zoom"],
                "anisotropy": round(g["anisotropy"], 6),
                "accuracy_note": "anchored to a published bounding box, not survey control",
            },
            "geometry": {"type": "Polygon", "coordinates": [ring]},
        }],
    }
    with open(os.path.join(ROOT, "data", "bounds.geojson"), "w", encoding="utf-8") as f:
        json.dump(fc, f, indent=2)

    bounds = {
        "id": "auda-rdp-2022",
        "title": "AUDA Revised Development Plan 2022",
        "source": "audareviseddevelopmentplan2022map04112022064034976.pdf (1 page, 27200x25600 scan)",
        "crs": "EPSG:3857",
        "lon_min": lon_min, "lon_max": lon_max,
        "lat_min": lat_min, "lat_max": lat_max,
        # slippy-map source config, copy-paste ready
        "raster_source": {
            "type": "raster",
            "tiles": ["/tiles/{z}/{x}/{y}.jpg"],
            "tileSize": 256,
            "minzoom": 11,
            "maxzoom": 16,
            # MapLibre wants this FLAT: [minLng, minLat, maxLng, maxLat]
            "bounds": [lon_min, lat_min, lon_max, lat_max],
        },
        "map_view": {"fitBounds": [ring[0], ring[2]], "minZoom": 11, "maxZoom": 18},
        "resolution": {
            "m_per_px": round(g["m_per_px"], 4),
            "native_zoom": g["native_zoom"],
            "anisotropy": round(g["anisotropy"], 6),
            "width_km": round(g["ground_width_km"], 2),
        },
        "accuracy": {
            "method": "sheet edges anchored to the AUDA bbox published by townplanmap.com",
            "aspect_agreement_pct": round(abs(1 - g["anisotropy"]) * 100, 3),
            "worst_case_error_m": 365,
            "caveat": (
                "Not survey control. The reference bbox is hand-entered and internally "
                "anisotropic by 0.5%, which accumulates to roughly 365 m at the far "
                "north/south edges of this 70 km sheet. Verified good near the centre "
                "(~10-20 m at z16). Replace with surveyed control points before any "
                "parcel-level legal use."
            ),
            "improvement_paths": [
                "least-squares fit against the place names burned into the scan "
                "(MOTERA, BHAT, HANSOL, ASARVA, RAMOL, SANAND, ...)",
                "request the georeferenced original or a GeoPDF from AUDA",
            ],
        },
    }
    with open(os.path.join(ROOT, "data", "bounds.json"), "w", encoding="utf-8") as f:
        json.dump(bounds, f, indent=2)

    print("wrote data/bounds.json and data/bounds.geojson")
    print(json.dumps(bounds["resolution"], indent=2))


if __name__ == "__main__":
    main()
