# AUDA Revised Development Plan 2022 — georeferenced tile pipeline

Turns the scanned AUDA RDP 2022 plan sheet
(`audareviseddevelopmentplan2022map04112022064034976.pdf`, 1 page, 27200×25600 px
scan) into a slippy-map raster tile pyramid that drapes onto a MapLibre basemap —
the same delivery model townplanmap.com uses for its city plan layers
(pre-rendered raster tiles served from a static path, referenced by a
`{TileMatrix}/{TileCol}/{TileRow}` template and placed with a `BottomLeft`/`TopRight`
+ `ZoomBounds` bounding box).

## Result

| | |
| --- | --- |
| Source | single-page PDF, one 27200×25600 DeviceRGB JPEG (`DCTDecode`), 323 MB |
| Tiles | **10,126** (`z11`–`z16`), 256×256, JPEG q85, **172 MB** total (17.4 KB avg) |
| Ground resolution | **2.57 m/px** — native at **z15.77** |
| Extent | lon 72.178109 … 72.860675, lat 22.763306 … 23.357406 (≈ 70 × 71 km) |
| Tile path | `tiles/{z}/{x}/{y}.jpg` |
| Empty tiles | omitted (they fall through to the basemap) |
| **Measured accuracy** | **53 m median, 122 m p90**, against z16 satellite — see below |
| Plot numbers | **29,175** real survey numbers, 9 land-use classes |

```
tools/inspect_pdf.py       probe the PDF: page count, geometry, georeference keys
tools/extract_source.py    pull the embedded scan out as work/source.jpeg
tools/measure_extent.py    measure ink extent + compare aspect against the target bbox
tools/fetch_osm_water.py   Overpass fetch of OSM water (control features)
tools/predict_landmarks.py project known landmarks onto the sheet to validate H0
tools/check_landmark.py    crop + crosshair at a predicted landmark
tools/make_georef.py       write work/georef.json (build-time input)
tools/make_bounds.py       -> data/bounds.{json,geojson}  <- the single source of truth
tools/build_tiles.py       render the tile pyramid (multiprocess)
tools/stitch.py            stitch a zoom back to a georeferenced mosaic + .pgw
tools/ocr_hires.py         4x OCR pass over the sheet (resumable)
tools/clean_numbers.py     merge split detections, reject fragments
tools/make_geojson.py      numbers + land-use zone -> data/plots.geojson
tools/bucket_plots.py      optional per-tile GeoJSON, only if the dataset grows large
tools/upload_r2.py         manifest + optional Cloudflare R2 upload
tools/serve.js             dev server with .env support
web/index.html             MapLibre viewer (overlay, plot numbers, markers)
```

### Accuracy and verification tools

```
tools/measure_hires_fft.py measure the georeference against z16 satellite imagery
tools/verified_correction.py fit a correction, re-measure, ship only if it helps
tools/accuracy_report.py   -> data/accuracy.json (the numbers the README quotes)
tools/compare_alignment.py blend the plan over the satellite, for eyeballing
tools/test_warp.py         self-test for the affine tile warp
tools/derive_palette.py    derive the plan's land-use colours from the sheet
tools/fetch_google_satellite.py  download satellite tiles for the bbox
```

These are the tools that survived. The approaches that were tried and
rejected are listed in **Accuracy** above, and their scripts are kept
(`affine_fit.py`, `build_gcp.py`, `gcp_from_water.py`, `name_consensus.py`,
`apply_fit.py`) because the negative results are part of the record: they
are what establishes that there is no correctable systematic error to fix.

The Python side is the build; the web side is the delivery. They are separate
lifecycles and neither needs to know about the other's framework.

## How the georeference was obtained

The sheet is **not** a GeoPDF — no `/Measure`, no `LGIDict`, no `/VP`, no graticule,
no legend, no north arrow, no coordinate ticks. There is nothing on the sheet from
which to read coordinates directly. So the georeference is anchored externally:

1. **Aspect-ratio consistency.** The sheet's pixel aspect (27200/25600 = 1.0625) is
   compared against the Mercator aspect of the AUDA bounding box that
   townplanmap.com publishes in its city config — `BottomLeft [22.763306, 72.178109]`,
   `TopRight [23.357406, 72.860675]`. That bbox has Mercator aspect 1.0571, i.e.
   **0.5 % from the sheet's aspect**. The two are the same document.
2. **H0.** Sheet edges ↔ bbox edges, north-up. `SX = 39849.6 px/deg`,
   `SY = 39646.9 px/deg` (0.5 % apart, expected from the bbox rounding).
3. **Validation by prediction, not by search.** Known landmarks were projected onto
   the sheet and checked visually (`predict_landmarks.py`, `check_landmark.py`).
   The decisive one: **Sardar Vallabhbhai Patel International Airport** lands dead on
   the plan's own "Ahmedabad Airport" label, with the runway immediately beside it.
   The Sabarmati shows a long continuous overlap against OSM.
4. **End-to-end check in a browser** against live satellite and OSM tiles
   (`work/shot_align.png`, `work/align_z16.png`). At z16 the plan's plot boundaries
   sit on the real street grid.

### Honest accuracy statement — measured, not estimated

The georeference is verified by cross-correlating the plan against z16
satellite imagery at hundreds of points across the sheet
(`tools/measure_hires_fft.py`), and by eye (`tools/compare_alignment.py`,
which blends the plan over the satellite so the roads can be compared
directly).

| | |
| --- | --- |
| Measured offset, median | **53 m** |
| Measured offset, p90 | 122 m |
| Near the sheet centre (r < 7 km) | 48 m |
| Far from the centre (r ≥ 7 km) | 62 m |
| Quadrant medians | west 63 m · east 48 m · north 52 m · south 57 m |
| Median signed offset | dlon −2 m · dmerc −2 m |
| Explained by any linear field | **4 %** |

**What this means.** The offset is close to uniform: it does not grow with
distance from the sheet centre, the four quadrant medians are within
15 m of each other, and the signed offsets are essentially zero. A real
scale error would grow with distance and would show up as opposite
displacements in opposite quadrants. Neither happens.

Most of that 53 m is the **age of the base map**, not the georeference:
the plan was drawn from an older survey than the satellite imagery, so
roads have been widened, realigned and built over since. The road-by-road
blend confirms this — the plan's roads sit on top of the satellite's roads
at the centre, and the same holds at Kankaria Lake and the airport
runway, where the features are large and unambiguous.

**Correcting the previous claim.** The earlier README asserted a worst
case of ~365 m at the north/south edges, derived from the published
bounding box's 0.5 % anisotropy. That was an *estimate*, not a
measurement, and it was pessimistic in two ways: it assumed the box's
hand-entered imprecision was entirely scale, and it ignored that a print
sheet's own neat-line is not the same as the sheet's scanned edge. The
measured numbers above replace it. The old caveat is kept in
`data/bounds.json` for continuity, but `data/accuracy.json` is the
current statement.

**What was tried and rejected.** Four automatic approaches were built and
measured, and none of them beat "leave the transform alone":

| Approach | Why it fails |
| --- | --- |
| Lake outline ↔ OSM polygon (`gcp_from_water.py`) | the plan's generalised shoreline and OSM's surveyed one are not the same shape, so the "same point" is ambiguous by 100–300 m |
| Place-name ↔ OSM place (`build_gcp.py`) | a map label is placed where the name *fits*, which can be a kilometre from the settlement; 45 name matches gave residuals to 1.9 km |
| OSM water mask IoU (`register_water.py`) | 754 of the 1,568 OSM water features are under 200 m — ponds the plan never draws — and they swamp the objective |
| Land-use class vs satellite (`match_landuse.py`) | a flat objective: a few hundred metres of misalignment barely changes the overlap of large blobby regions |

The useful result of that work is the diagnosis: **there is no
correctable systematic error to fix.** `tools/verified_correction.py` fits
a correction to the measured error field, re-measures with it applied, and
refuses to ship it unless the error actually drops. It does not, so the
georeference is left as it is.

If tighter accuracy is ever needed, the route is still survey control, or
AUDA's own georeferenced original. The PDF producer is
"Adobe Acrobat 10.0.2 Image Conversion Plug-in" — the scan was flattened
before publication, so the georeference was lost at their end, not ours.

## Marking — the plot numbers

The label layer was rebuilt from scratch, because the previous one had two
defects that were visible in the shipped data.

**Fragmented numbers.** The detector splits one printed number into two
boxes ("285" + "/2"), and both halves were emitted as separate features.
The old layer held 42,281 features with only 7,355 distinct texts, and
the most common values were `2`, `22` and `7` — fragments, not numbers.
`tools/clean_numbers.py` groups detections into text runs, merges them,
and rejects what is left that cannot be a survey number. The 4× OCR pass
(`tools/ocr_hires.py`) also reads the text far better than the 3× pass the
old numbers came from.

| | Old | New |
| --- | --- | --- |
| Features | 42,281 | **29,175** |
| Distinct numbers | 7,355 | 6,947 |
| Most common value | `2` | `222` |
| Fragments dropped | — | 25,968 |

**Wrong land-use zones.** The old classifier put red — which the plan uses
for *industrial* — inside the orange "commercial" band, and lumped the
plan's teal-green open land in with the darker agricultural green. The
result was 26,631 features labelled "Open / agricultural" on an urban
plan, which is a symptom rather than a fact. The palette is now taken
from the sheet itself, and the fill is sampled from a ring *around* each
number so the red glyph is excluded.

| Zone | Features |
| --- | --- |
| Agricultural | 12,824 |
| Residential | 6,637 |
| Open / vacant | 6,110 |
| Institutional / public | 1,572 |
| Unzoned / paper | 759 |
| Water | 444 |
| Industrial | 385 |
| Commercial / mixed | 254 |
| Mixed land use | 102 |
| Green / undeveloped | 87 |
| Other | 1 |

The palette is **derived, not guessed**: `tools/derive_palette.py` clusters the
saturated colours of the sheet and reports each one's hue, saturation, value
and share of the sheet. That is how the saturated blue at hue 213 was found
— a land-use fill near Chakkarpur with plot numbers in it, which the old
classifier had no band for at all, so those plots fell through to "Other".
Water is a different blue entirely (hue 195, pale).

## Running it

```powershell
node --env-file=.env tools/serve.js
# http://127.0.0.1:8777/web/index.html
```

`tools/serve.js` is a dependency-free static server that also reads `.env`. It
generates `/config.js` from the environment and serves it, so browser code can read
`window.ENV` without the key ever being written into the HTML.

`PORT`, `HOST` and `CLIENT_EXPOSE` are all overridable. Note that
`python -m http.server` works fine for the map itself but serves no `/config.js`, so
keys are simply absent — the page degrades gracefully and says so in the panel.

## Artefacts

Everything the site consumes lives in `data/`, generated and version-controlled
rather than hardcoded in the page:

| File | What |
| --- | --- |
| `data/bounds.json` | sheet extent, scale, and the accuracy caveat. Single source of truth |
| `data/bounds.geojson` | same extent as a polygon, opens in QGIS/GIS |
| `data/accuracy.json` | **measured** accuracy, from the satellite cross-correlation |
| `data/manifest.json` | artefact inventory from the R2 upload step |
| `data/plots.geojson` | OCR'd plot numbers with land-use zone |

`tools/make_bounds.py` regenerates the first two from `work/georef.json`. The viewer
fetches `bounds.json` at startup and re-applies the camera and raster bounds; the
constants left in `index.html` are a fallback that only engage if that fetch fails.
**The extent is the weak link in this project** — duplicating it across the tile
builder, OCR and viewer would mean a correction has to be made in several places at
once.

## Publishing

```powershell
python tools/upload_r2.py --dry-run        # inventory + manifest, uploads nothing
python tools/upload_r2.py                  # real upload
```

Requires `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` and
`R2_BUCKET`. Tiles get `cache-control: public, max-age=31536000, immutable`, which is
safe because a changed tile changes its path.

R2 rather than S3 because **there is no egress fee** — with 172 MB of tiles, S3
charges every time a user zooms, which is the entire cost argument for a tile server.
The script also runs to completion without credentials, so CI can build artefacts
before secrets exist.

## Plot numbers

```powershell
python tools/ocr_hires.py --workers 8               # 4x OCR, resumable
python tools/clean_numbers.py                      # merge + reject fragments
python tools/make_geojson.py                       # -> data/plots.geojson
```

The sheet has no text layer, so the numbers only exist via OCR. Two properties make it
survivable: the run is **resumable** (a per-tile ledger in `work/ocr4_done.txt`, so a
shell restart costs nothing) and results are **de-duplicated** on build, because a
killed run leaves numbers whose originating tiles are unknown.

The 4× pass is used rather than the original 3× one because the place names and the
plot numbers are both much better resolved: at 4× the recogniser reads `HANSOL`,
`PINDHARADA` and `CHEKHALARANI` at confidence 1.00, where at 3× they came back as
noise. `tools/clean_numbers.py` then does the part the recogniser cannot: it groups
detections into text runs, merges the ones the detector split, and rejects what is
left that cannot be a survey number. See **Marking** above for the before/after.

**These are OCR reads, not authority.** Measured on a test crop, the recogniser
returned `728/4` for `718/4` and `451+432NC` for `431+432NC`. Every feature carries
its confidence and the UI filters on it — do not present these as legal land records.
The PDF is a land-use zoning drawing; it contains no ownership, survey history or
legal status, and no amount of processing changes that.

### Data layer

`db/schema.sql` is the PostGIS target shape:

| Table | Populated from the PDF? |
| --- | --- |
| `sheets` | yes — extent and accuracy note from `data/bounds.json` |
| `land_use_zones` | yes — the plan's own legend colours |
| `ocr_numbers` | yes — OCR reads, each with its confidence |
| `plots` | **no** — needs AUDA's vector + attribute source |

The split is deliberate. `ocr_numbers` holds unverified text that must never be
confused with a real identifier, so it is a separate table with its own
confidence constraint and index. `plots` is the target for real parcel data, and
stays empty until that source exists.

```powershell
set DATABASE_URL=postgres://...
psql "$DATABASE_URL" -f db/schema.sql
python tools/load_ocr.py --dry-run
python tools/load_ocr.py
```

`identify_at(px, py, sheet, min_conf)` is the click-to-identify entry point. It
returns confidence on every row so a caller can refuse anything below threshold
rather than discover it cannot trust the result.

## Environment

| File | Purpose |
| --- | --- |
| `.env` | real values, gitignored |
| `.env.example` | template to copy |
| `.gitignore` | excludes `.env`, the 2 GB raw cache, the extracted scan |

`CLIENT_EXPOSE` is a comma-separated allowlist of variables the dev server is allowed
to hand to the browser. Everything else stays server-side; startup prints which
key-shaped variables were withheld. **Treat anything you expose as public** — a key
shipped to a browser must be restricted by HTTP referrer (and, for Maps JS, by API)
in the Google Cloud console, or it will be scraped and billed to you within hours.

## Building the tiles

```powershell
python tools/extract_source.py                 # PDF -> work/source.jpeg (once)
python tools/make_georef.py                    # -> work/georef.json
python tools/build_tiles.py --zmin 11 --zmax 16 --quality 85
python tools/stitch.py --tiles tiles --z 13 --out work/mosaic_z13.png
```

Useful flags: `--zmax 17` adds an upsampled level (~4× the tile count, no new detail);
`--fmt png` for lossless; `--dry-run` to count tiles first.

## Viewer

`web/index.html` is a plain static page — MapLibre GL from a CDN, no build step.

```js
{
  type: 'raster',
  tiles: ['/tiles/{z}/{x}/{y}.jpg'],
  tileSize: 256,
  minzoom: 11, maxzoom: 16,
  bounds: [72.178109, 22.763306, 72.860675, 23.357406]   // flat [minLng,minLat,maxLng,maxLat]
}
```

Controls: overlay toggle, opacity, landmark markers (for verifying alignment), and a
satellite/OSM basemap switch. Note `bounds` on a raster source must be **flat** — a
nested `[[w,s],[e,n]]` pair throws `sources.plan.bounds: array length 4 expected`.

## Notes

- `work/source.rgb` (2.09 GB) is a decoded raw cache created by `build_tiles.py` on
  first run so workers can memory-map it. Workers decode the 323 MB JPEG per-process
  otherwise and exhaust RAM. Safe to delete; regenerated on demand.
- The mosaic export writes a `.pgw` world file, so `work/mosaic_z13.png` opens
  correctly georeferenced in QGIS/GDAL/ArcGIS.
- Basemaps in the viewer are OSM + Esri World Imagery for development only. Both have
  usage policies; pick a keyed provider (MapTiler, Mappls, Mapbox) for anything real.
- AUDA RDP 2022 is a Gujarat government publication; check its terms before
  redistributing tiles.
