# AUDA Second Revised Development Plan 2021 — georeferenced map pipeline

Turns the client's AUDA DP 2021 book — four georeferenced vector GeoPDF
quadrants, 145 per-village extracts and 159 per-sheet pages — into a
slippy-map raster tile pyramid that drapes onto a MapLibre basemap, with an
exact survey-number layer beside it.

This replaces the earlier pipeline, which was built from a 323 MB scan of the
RDP 2022 sheet. That map is kept on disk (`tiles/`, `data/bounds.json`,
`data/plots.geojson`, `web/legacy_rdp2022.html`) but is no longer the
deliverable.

## Result

| | old (RDP 2022 scan) | new (DP 2021 vector) |
| --- | --- | --- |
| Source | one 27200×25600 scan of one printed sheet | four vector GeoPDFs, 4 sheets of 36.3 × 34.2 km |
| Georeference | eyeballed against a published bounding box, 53 m median error | the sheets' own projected grid (±2.2 m internal fit) **plus a ground correction** fitted to OSM roads |
| Plan vs ground (OSM roads, 1 km grid) | 53 m median, 122 m p90 (old method) | **6.4 m median, 16.4 m p90** held-out (affine + local warp; 7.7 m affine alone; 90.4 m before) |
| Resolution | 2.57 m/px, capped by the scan | **1.10 m/px** at z17, vector-rendered, crisp at every zoom |
| Tiles | 10,126, z11–z16, JPEG | 43,470, z11–z17, WebP |
| Survey numbers | 29,175 OCR reads, 26k fragments, `718/4` → `728/4` | **211,630 read from the vector sheet**, no OCR at all |
| Land-use legend | nine colours guessed from the scan | 22 entries read off the sheet's own legend block |

## What the new source actually is

The decisive finding: **the new PDFs are georeferenced, and they are vector.**

* 126 of the 311 supplied PDFs carry a GeoPDF viewport (`/VP` → `/Measure` →
  `/GPTS`) and a WKT for `WGS_1984_Transverse_Mercator` (CM 71.5, k0 0.999738,
  FE/FN 400000) — the projected space the sheets were drawn in.
* The survey numbers are **text objects** in the PDF, at type sizes 5.8 pt and
  6.7 pt. They are read, not guessed.
* The four part sheets are the four quadrants of one plan and their printed
  edges **overlap by about 1.8 km**, so every sheet seam has a strip covered
  twice. The builder merges those strips (see *The overlap* below).

The `/GPTS` corner values are published to four decimal places of a degree —
about 11 m of rounding. Fitting the four sheets' 2×2 projected grid to all
sixteen corners at once averages that down to **2.2 m RMS**, and the
projection is then exact.

## The pipeline

```
tools/read_geopdf_views.py   read every /VP + /Measure out of the 311 PDFs
tools/dp_georef.py           THE georeference: the 2x2 grid fit, the exact
                             projection, page<->ground in both directions
tools/dp_build_tiles.py      render the tile pyramid from the vector sheets
tools/dp_extract_labels.py   the survey numbers, places, road widths
tools/dp_bucket_plots.py     bucket the numbers for the viewer
tools/dp_read_legend.py      read the land-use legend off the sheet
tools/dp_make_bounds.py      extent, resolution, accuracy, manifest, app config
tools/dp_measure_accuracy.py measure the plan against today's satellite
tools/dp_verify_align.py     blend plan over satellite at named landmarks
tools/dp_stitch.py           stitch the pyramid back into one overview image
tools/dp_check_fidelity.py   prove the pyramid matches the source pixel for pixel
```

Verification and diagnostics:

```
tools/dp_check_projection.py   the projected rectangle, and what the wrong
                               model (bilinear in lon/lat) would have cost
tools/dp_check_gaps.py         are the white patches real, or dropped tiles
tools/dp_check_fidelity.py     tile-for-tile against a fresh render
tools/dp_accuracy_by_block.py  each sheet's own offset from the satellite
tools/dp_content_extents.py    where each sheet's own drawing lies on the ground
tools/dp_show_tile_diff.py     one tile, from every sheet, side by side
tools/dp_sheet_titles.py       what each of the four part sheets is
tools/dp_spotcheck_numbers.py  a contact sheet of numbers vs the map
tools/dp_gapcheck.py           one place, source vs tiles vs satellite
tools/dp_test_encodings.py     PNG / PNG8 / WebP / JPEG, size against quality
tools/inspect_new_dp.py        first look at the new files
tools/scan_georefs_fast.py     which files carry a georeference
```

The old pipeline's tools are still in `tools/` and still work. They are the
record of what was tried and rejected, and that record is part of the value
of the project.

## How a tile is made

1. the tile's four corners are taken in lon/lat from the web-mercator grid;
2. they are pushed through the exact model — lon/lat → Transverse Mercator
   metres → PDF user space — so the tile is placed by the survey grid;
3. the page is rasterised over that rectangle with MuPDF's own antialiasing,
   at a resolution at or above the tile's own;
4. the render is warped onto the 256×256 tile with the four corner
   correspondences, which removes the ~0.4° rotation the sheets carry in
   geographic space;
5. a tile with no ink in it is not written, so the basemap shows through
   outside the plan instead of a sheet of white paper.

### The overlap

The four sheets overlap by ~1.8 km, so a tile on a seam is covered in full by
one sheet and only in part by its neighbour. A builder that lets each sheet
write its own tiles keeps whichever wrote last, and the map tears — a band of
missing plan along every seam, which is exactly what the first build did.

So a tile is rendered from **every** sheet that covers it and the renders are
merged: the fullest one first, and any part of it that is blank paper is
filled in from the next. The sheets agree where they overlap, so the result
is the union of the plan, seamless. `tools/dp_check_fidelity.py` proves it
tile-for-tile.

## Plot boundaries — the plan's own parcel geometry

The sheets are not only a picture and a set of numbers. They also **draw the
plot boundaries**, as vector strokes, and those strokes had not been extracted.
This is the layer that turns "a number on a map" into "this number, in this
plot, in this village, zoned this way".

### What is in the source

| | what it is | how it is drawn |
| --- | --- | --- |
| **plot boundary** | the parcel a survey number is printed in | a closed ring, 5–20 vertices, 0.48 pt black (0.96 pt red in some regions) |
| **plot boundary (shared strokes)** | the same, in the villages the plan draws as a shared line network rather than as separate rings | open strokes, shared between neighbours, 0.48 pt black |
| **unit boundary** | the plan's own village, TP-scheme and settlement limits | a closed ring, 200–600 vertices, 2.4 pt black / magenta / blue |

The test that identifies a plot is a **measurement, not a style rule**: a plot
is a ring that contains exactly one survey number. On the NW sheet 88.4% of
the numbers fall inside exactly one thin black ring, and 56,881 of those rings
hold exactly one number. The rings that hold *many* numbers are the plan's own
village and scheme boundaries, and they carry the plan's own place names inside
them — which is how they are named.

A record is assigned to the **smallest** ring that contains it, so a record
inside a village is also inside a plot, and the plot wins.

Where the plan draws plots as a **shared line network** instead of separate
rings, the same test is applied one step later: the linework is noded and
polygonized, and every **face** of that network becomes a candidate plot; a
face is a plot if it holds exactly one survey number. The recovery is
strictly additive — a plot that already existed as a closed ring is left
alone.

```
tools/dp_extract_boundaries.py  the closed rings off the four sheets, on the
                                Earth, de-duplicated across the 1.8 km seams
tools/dp_polygonize_plots.py    the plots the plan draws as shared strokes:
                                node the linework, polygonize it, keep the
                                faces that hold a survey number
tools/dp_join_boundaries.py     every record -> its plot, its village, its
                                scheme, its land use
tools/bucket_parcels.py         the plots, bucketed by z15 tile for the viewer
tools/dp_check_parcels.py       the overlay, the content check, the area check
tools/dp_boundary_report.py     the tables
```

### A coordinate trap, worth stating

PyMuPDF reports drawing items in **page space** — origin at the top-left of the
crop box, y increasing downward. PDF user space has y increasing upward from
the bottom-left. Treating one as the other mirrors the drawing about the
horizontal, silently, and the layer still *looks* plausible: 42% of the records
land in a "parcel" that is the right shape in the wrong place.

The tell is the number: 42% before the fix, 79% after it.

### What came out

| | |
| --- | --- |
| closed rings on the four sheets | 234,247 |
| after de-duplicating the 1.8 km seams | 193,914 (20,526 duplicates dropped) |
| **plot boundaries** | **199,497** (161,857 from closed rings + 37,640 from the shared strokes) |
| **unit boundaries** (village, scheme, frame) | 3,487 |
| records placed in a plot | 205,616 of 211,630 (97.2%) |
| records in a **named village boundary** | 160,497 (75.8%) |
| plots holding exactly one survey number | 194,235 (97.4%) |
| median plot area | 4,599 m² (0.46 ha) |

The 2.8% of records with no plot are the ones the plan does not draw a
boundary around at all — they sit in open ground, in a settlement drawn as an
area rather than as plots, or in the dense village-inlay blocks. Recovering the
shared-stroke plots is **strictly additive**: where a closed ring already
existed it was left alone, all 166,149 records that already had a plot kept
exactly the plot they had, 39,467 gained one, and none moved or lost theirs.
Where both sources draw the same plot, the two agree — median IoU **0.9999**
over 160,533 records, 10th percentile 0.9991.

### How it was verified

| | |
| --- | ---: |
| records inside their own parcel | **205,615 / 205,616 — 100.00%** |
| median offset from the plan's own drawn line (NW) | **0.000 m** |
| median offset from the plan's own drawn line (SE) | **0.000 m** |
| vertices within 1 m of the plan's line (SE) | 89.7% |

The offset measurement renders the plan **with the text wiped out**, so the
numbers cannot be mistaken for lines, and measures every extracted parcel
vertex against the nearest ink. It is in
`tools/dp_measure_boundary_offset.py`.



Two different numbers, and they must not be confused.

**The sheet fit is ±2.2 m.** That is the residual of the 2×2 grid fit
against the sheets' own published corners: internal consistency, nothing
more. It does not say where the plan is on the ground.

**The published corners are ~85 m off the ground** (found 2026-10-22). On a
1 km grid across AUDA, the plan's road corridors were matched to OSM roads by
gradient-orientation correlation (`tools/dp_osm_offsets.py`): the plan sat a
median 90.4 m from them, almost all of it one systematic error - 84.9 m
east, 23.8 m north, -0.03 deg rotation, 0.067 % north-south stretch.
`tools/dp_fit_ground_correction.py` fits that as an affine in the plan's TM
metres (held-out 5 km blocks: 8.9 m median) and `tools/dp_georef.py` applies
it inside `FWD`/`INV`, so tiles, labels, records, zones and plots all move
together. `DP_NO_GROUND_CORRECTION=1` turns it off.

**The corrected map sits 7.7 m (median) from OSM roads, 19.7 m p90**, with
0.8 m E / 0.3 m N of systematic shift left. That residual includes OSM's own
error and places where the plan's drawing differs from what was built, so
it is an upper bound on placement error.

**Local warp (2026-10-23).** The affine's residuals are spatially coherent
(neighbour correlation +0.42 at 1 km, fading by ~10 km), so
`tools/dp_fit_ground_warp.py` fits a Gaussian 3 km residual field on top of it
(`work/dp_ground_warp.json`, 250 m grid, shrinks to zero where windows are
sparse). `dp_georef` applies it after the affine and inverts it by fixed-point
iteration (round trip ~1e-5 m). Held-out (5 km blocks, 2 km buffer): **6.4 m
median, 16.4 m p90**; measured on the rebuilt tiles 4.8 / 13.9 m (in-sample).
The browser gets a 500 m copy (`data/dp2021/ground_warp.json`) so the set-out
sheet's plan coordinates undo it too (7 mm median vs the exact inverse).

The earlier "19.8 m from today's satellite" figure is **withdrawn**: its
road detector matched bright rooftops, and it missed the 85 m offset.

The georeference was checked by eye at eight landmarks — the airport runway,
Sardar Patel Stadium, Kankaria Lake, Sarkhej, the Sabarmati ashram, Sanand,
Gandhinagar, Adraj — with the plan blended over the satellite. The road
networks coincide; see `work/newdp/verify/`.

## Survey numbers

`tools/dp_extract_labels.py` pulls every text span out of the four sheets,
places it with the same transform the tiles use, and sorts it by type size:

| type size | what it is |
| --- | --- |
| 5.8 / 6.7 pt | the survey number inside a plot — **211,630 of them** |
| 8.6 / 9.6 / 12.0 pt | road widths: `12.00 Mt.`, `30 M` |
| 9.6 / 13.9 / 15.8 pt | settlement and feature names: MOTERA, Stadium |
| 4.8 pt | the `!` TP-boundary marker glyph |

Classifying on the type size, not on the shape of the text, is what keeps a
village called "5" or a road numbered "30" out of the plot-number layer. The
blocks are de-duplicated across their 1.8 km overlap, which removes 35,156
labels.

**These are the sheet's own text.** They are not legal land records: the
plan is a land-use zoning drawing, with no ownership, survey history or legal
status in it, and the survey numbers in it are the ones the drawing used.

## Artefacts

Everything the site consumes lives in `data/dp2021/`, generated and
version-controlled rather than hardcoded in the page:

| File | What |
| --- | --- |
| `bounds.json` | extent, resolution, accuracy, tile inventory, **app config** |
| `accuracy.json` | the measured accuracy, stated separately from the georeference |
| `manifest.json` | artefact inventory |
| `plots.geojson` | 211,630 survey numbers with their ground positions |
| `plots/15/{x}/{y}.geojson` | the same numbers bucketed by z15 tile, for the viewer |
| `plots_overview.geojson` | a 1-in-12 sample, for the low zooms |
| `labels.geojson` | place names, road widths, sheet references |
| `legend.json` | the 22 land-use classes with the sheet's own colours |
| `parcels.geojson` | **the plan's own plot boundaries**, with area and the survey number in each |
| `parcels/15/{x}/{y}.geojson` | the same, bucketed by z15 tile, for the viewer |
| `units.geojson` | **the plan's own village and TP-scheme boundaries**, named from the sheet |
| `village_units.json` | the named units, for the viewer's search box |
| `records_bounded.ndjson` | every record, with its plot, village, scheme and land use |
| `details/15/{x}/{y}.json` | the same, bucketed by z15 tile, for lazy loading |

The viewer fetches `bounds.json` at startup and re-applies the camera, the
raster bounds and the zoom range; the constants in `index.html` are a
fallback that only engage if that fetch fails. **The extent is the weak link
in this project** — duplicating it across the tile builder, the extractor
and the viewer would mean a correction has to be made in several places at
once.

## Running it

```powershell
node --env-file=.env tools/serve.js
# http://127.0.0.1:8777/web/index.html
```

To leave it running in the background without tying it to the shell (a
cancelled shell takes the server with it), start it detached:

```powershell
$env:PORT = "8777"
Start-Process node -ArgumentList "--env-file=.env","tools/serve.js" `
  -WorkingDirectory (Get-Location) -WindowStyle Hidden `
  -RedirectStandardOutput work/serve.out -RedirectStandardError work/serve.err
```

`tools/serve.js` is a dependency-free static server that also reads `.env`.
It generates `/config.js` from the environment and serves it, so browser code
can read `window.ENV` without the key ever being written into the HTML.

`PORT`, `HOST` and `CLIENT_EXPOSE` are all overridable. Note that
`python -m http.server` works fine for the map itself but serves no
`/config.js`, so keys are simply absent — the page degrades gracefully and
says so in the panel.

## Building it

```powershell
# 1. read the georeference out of the source PDFs (once)
python tools/read_geopdf_views.py

# 2. build the tile pyramid
python tools/dp_build_tiles.py --zmin 11 --zmax 17 --fmt webp --out tiles_dp2021

# 3. the numbers, the legend, the config
python tools/dp_extract_labels.py
python tools/dp_bucket_plots.py
python tools/dp_read_legend.py
python tools/dp_make_bounds.py

# 4. the plot boundaries and the record join
python tools/dp_extract_boundaries.py
python tools/bucket_parcels.py
python tools/dp_join_boundaries.py

# 5. measure and check
python tools/dp_measure_accuracy.py
python tools/dp_verify_align.py
python tools/dp_check_fidelity.py 14 60
python tools/dp_check_parcels.py
```

Useful flags: `--zmax 18` adds a level (~4× the tile count); `--fmt png` for
lossless; `--overwrite` to rebuild existing tiles; `--workers N` to control
parallelism (one process per zoom).

The whole build takes about 20 minutes on 24 cores.

## Publishing

```powershell
python tools/upload_r2.py --dry-run        # inventory + manifest, uploads nothing
python tools/upload_r2.py                  # real upload
```

Requires `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` and
`R2_BUCKET`. Tiles get `cache-control: public, max-age=31536000, immutable`,
which is safe because a changed tile changes its path.

R2 rather than S3 because **there is no egress fee** — with 416 MB of tiles,
S3 charges every time a user zooms, which is the entire cost argument for a
tile server. The script also runs to completion without credentials, so CI
can build artefacts before secrets exist.

`data/dp2021/bounds.json` carries an `app_config` block in the shape the
client's city config already uses, and the viewer's **Copy config for the
app** button puts it on the clipboard:

```json
{
  "shortTitle": "AUDA Second Revised Development Plan 2021",
  "sourceName": "AUDA",
  "MapTilerUrl": "/tiles_dp2021/{z}/{x}/{y}.webp",
  "BottomLeft": [22.757385, 72.173812],
  "TopRight": [23.364034, 72.869898],
  "ZoomBounds": [11, 17],
  "CityName": "Ahmedabad"
}
```

## Notes

- The four part sheets are the four quadrants of the plan and their printed
  edges overlap by ~1.8 km. The builder merges that overlap; see above.
- `work/source.rgb` (2.09 GB) is the old pipeline's decoded raw cache. Safe
  to delete; regenerated on demand.
- The mosaic export writes a `.pgw` world file, so the stitched image opens
  correctly georeferenced in QGIS/GDAL/ArcGIS.
- Basemaps in the viewer are OSM + Esri World Imagery for development only.
  Both have usage policies; pick a keyed provider (MapTiler, Mappls, Mapbox)
  for anything real.
- AUDA RDP 2021 and RDP 2022 are Gujarat government publications; check
  their terms before redistributing tiles.
