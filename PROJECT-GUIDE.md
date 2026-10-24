# The AUDA map project — a senior's walkthrough

This is the document to read if you are picking the project up, explaining it
to someone else, or deciding what to do next. It is the mental model plus the
practical checklist. The code-level detail is in `README-DP2021.md`.

---

## 1. What the project is, in one paragraph

The client is an urban development authority. They hold a land-use zoning
plan for the Ahmedabad area and want it on a web map, georeferenced well
enough to be trusted and detailed enough to read. We deliver a raster tile
pyramid that any slippy-map library can consume, plus the plan's own survey
numbers as a clickable layer, plus the accuracy figures stated honestly.

That is the whole product. Everything else in this repository is either how
the product is built, or the evidence that it is correct.

---

## 2. The two maps, and why the first one failed

| | **old** — RDP 2022 | **new** — DP 2021 |
| --- | --- | --- |
| what it was | one enormous scan of one printed sheet, 323 MB | four vector GeoPDFs, the same plan as drawn |
| where it came from | a PDF the client had | the book the client has now |
| how it was placed | by comparing the sheet's aspect ratio to a bounding box typed into a website | by the sheet's own survey grid |
| error | 53 m median, 122 m p90 | **6.4 m median, 16.4 m p90** against OSM roads (affine ground correction + local warp, held-out; 90 m before) |
| resolution | 2.57 m/px, because the scan was 2.57 m/px | 1.10 m/px, and vector, so crisp at any zoom |
| survey numbers | 29,175 OCR reads; 26,000 were fragments; `718/4` came back as `728/4` | 211,630 numbers read out of the file, no OCR |

The old map was not a bad piece of work. It was the best you could do with a
scan and no georeference in the file. The new map is better because the
*source* is better, and almost all of the improvement comes from one
discovery:

> **The client's new PDFs are georeferenced vector drawings.** They carry a
> GeoPDF viewport with the ground coordinates of the sheet, and the survey
> numbers are text objects in the file.

That single fact changes everything. It is worth looking for in every future
"we only have a PDF" situation before accepting any downstream compromise.

---

## 3. How to read the source (the part that is easy to get wrong)

Four things in the source will bite you if you do not know them.

**a) The projected space.** The sheets were drawn in
`WGS_1984_Transverse_Mercator`, CM 71.5, k0 0.999738, FE/FN 400000. The WKT
is published in each file's `/Measure` dictionary. Use it.

**b) The corners are rounded.** The published ground quad is given to four
decimal places of a degree — about 11 m of rounding. Fitted per file, that
is 2 m of noise; fitted across all four sheets as one 2×2 grid, it averages
down to 2.2 m RMS. **Fit the grid, not the individual files.** (That 2.2 m is
internal consistency only: the published corners themselves place the plan
~85 m east of the ground; the ground correction fixes that, see §5.) The four
sheets share one canvas geometry and one scale, and they are a regular grid
of printed sheets that *overlap*.

**c) The overlap is the whole difficulty.** The printed sheets overlap by
about 1.8 km, so every sheet seam has a strip covered by two sheets, and a
tile on a seam is covered in full by one sheet and only in part by its
neighbour. Let each sheet write its own tiles and the map tears — a band of
missing plan along every seam. This is the single bug that mattered, and it
is invisible until you look at the whole book at once. Render every covering
sheet for every tile and merge them, fullest first.

**d) The numbers are classified by type size, not by shape.** The sheets use
a fixed set of sizes and each size means one kind of label: 5.8/6.7 pt are
survey numbers, 8.6/9.6/12 pt are road widths, 9.6/13.9/15.8 pt are place
names. Classify on the size and a village called "5" or a road numbered "30"
stays out of the plot-number layer.

---

## 4. The architecture, and why it is shaped this way

```
NEW DP BOOK AUDA/            the client's files, read-only, never modified
  └─ 4 part PDFs             the four quadrants of the plan, georeferenced
  └─ 145 village extracts     crops of the same sheets
  └─ 159 book sheets         the printed pages, not georeferenced
  └─ key maps, name list     the book furniture

work/                         everything intermediate
  └─ geopdf_views.json       the /VP + /Measure read out of all 311 files
  └─ dp_grid.json            the fitted 2x2 grid
  └─ newdp/verify/           plan blended over satellite, per landmark
  └─ newdp/overview_z*.png   the pyramid stitched back into one image

tiles_dp2021/{z}/{x}/{y}.webp    43,458 tiles, 416 MB
data/dp2021/                  the config, the numbers, the legend
web/index.html                the viewer
```

Two rules hold the project together:

1. **The extent lives in exactly one place.** `tools/dp_georef.py` owns where
   every page lands on the Earth. The tile builder, the label extractor and
   the viewer all read it. If the extent ever needs correcting, it is one
   edit and one rebuild, not four coordinated edits. The old project got
   this wrong and said so in its own README ("the extent is the weak link").

2. **The published data is a build product, not a hand-edit.**
   `data/dp2021/bounds.json` is generated. The viewer fetches it at startup
   and re-applies the camera, the raster bounds and the zoom range; the
   constants in the HTML are a fallback that only engages if the fetch fails.
   If the client changes the extent, the client changes one JSON file.

---

## 5. What is done, and what each claim rests on

| Claim | Evidence | Where to look |
| --- | --- | --- |
| The sheets agree with their own printed corners to ±2.2 m | RMS residual of the 2×2 grid fit against all sixteen published corners. Internal consistency only. | `tools/dp_georef.py` |
| The printed corners are ~85 m off the ground; corrected | 1 km grid of plan-vs-OSM road matches (937 trusted windows); affine fit, scored on held-out 5 km blocks | `tools/dp_osm_offsets.py`, `tools/dp_fit_ground_correction.py`, `work/dp_ground_correction.json` |
| The corrected map sits 7.7 m (median) from OSM roads, p90 19.7 m (affine only) | same 1 km grid re-run on the rebuilt tiles, 991 windows; residual systematic shift 0.8 m E / 0.3 m N | `work/georef_osm/field.json` (`field_raw.json` = before) |
| With the local warp: 6.4 m median, 16.4 m p90 held-out (4.8 / 13.9 in-sample) | Gaussian 3 km residual field, scored on held-out 5 km blocks with a 2 km buffer; rebuilt tiles re-measured, 1,010 windows | `tools/dp_fit_ground_warp.py`, `work/dp_ground_warp.json`, `work/georef_osm_warp/field.json` |
| ~~The plan is 19.8 m from today's satellite~~ | **Withdrawn 2026-10-22.** Its road detector matched bright rooftops, not roads, and it missed an 85 m systematic offset. | `tools/dp_measure_accuracy.py` (do not cite) |
| The map lines up with the ground | eight landmarks blended plan-over-satellite | `work/newdp/verify/`, `tools/dp_verify_align.py` |
| The numbers are exact | contact sheet of claimed numbers against the rendered map | `work/newdp/spotcheck.png`, `tools/dp_spotcheck_numbers.py` |
| The tiles are faithful to the source | 60 random tiles re-rendered from the PDF and compared pixel for pixel | `tools/dp_check_fidelity.py` |
| The map has no holes | every tile inside the book's extent is present, and the gaps render white *in the source too* | `tools/dp_check_gaps.py` |
| The legend is the plan's own | 22 swatches and labels read out of the drawing objects | `tools/dp_read_legend.py` |

**The honest framing on accuracy**, and it is the single most important
sentence in the delivery:

> The plan's own printed corner coordinates put it about 85 m east and 24 m
> north of the ground, slightly rotated and stretched. We measured that
> against OSM roads on a 1 km grid across AUDA and corrected it. The
> corrected map, with a smooth local warp on top, sits a median **6.4 m** (90% within
> **16 m**) from OSM roads, measured on areas held out of the fit.
> That residual includes OSM's own error and places where the plan's drawing
> differs from what was built, so it is an upper bound on placement error.

Do not quote the ±2.2 m as accuracy: it is how well the four sheets agree
with their own printed corners, and those corners were 85 m off. Do not
quote the old 19.8 m figure at all (withdrawn - see the table above).

---

## 6. What is not done, and what I would do next

Ordered by what the client will feel first.

**a) Search over survey numbers.** The layer has 28,807 distinct numbers
across 211,630 positions, and a text index over them is small. The blocker
is that the *positions* are 30 MB, so it wants a spatial index — a static
trie or a server-side endpoint, not a browser download. If the client's app
has a search box, this is the feature they will notice is missing.

**b) Land-use polygons as vector data.** The plan's land use is real filled
polygons in the PDF. They can be extracted as GeoJSON or MVT and give the
client parcel-level queries, not just a picture. This is the biggest
available upgrade and it is a well-understood piece of work.

**c) The per-village and per-sheet pages.** The 145 village extracts and the
159 printed book sheets are not yet in the product. The village extracts are
georeferenced crops of the same sheets, so they can be served as a
per-village deep link. The printed sheets (A-12 … Q-15) are not georeferenced
— Esri stripped the viewport when the pages were extracted — but they can be
placed by matching them against the block content, which would let the
client link a parcel to its book page.

**d) A tile-level "which sheet is this" service.** The sheets overlap, so a
point can be on two printed sheets. The client may want to know which.

**e) Vector tiles.** If the client's app wants client-side styling, MVT
rather than raster is the answer. Bigger job; not needed for the current
brief.

---

## 7. Running it, and the things that will surprise you

```powershell
node --env-file=.env tools/serve.js     # http://127.0.0.1:8777/web/index.html
```

To keep it up in the background, start it detached — a cancelled shell takes
the server with it otherwise:

```powershell
$env:PORT = "8777"
Start-Process node -ArgumentList "--env-file=.env","tools/serve.js" `
  -WorkingDirectory (Get-Location) -WindowStyle Hidden `
  -RedirectStandardOutput work/serve.out -RedirectStandardError work/serve.err
```

The build, in order:

```powershell
python tools/read_geopdf_views.py        # once: read the georeference out
python tools/dp_build_tiles.py --zmin 11 --zmax 17 --fmt webp --out tiles_dp2021
python tools/dp_extract_labels.py
python tools/dp_bucket_plots.py
python tools/dp_read_legend.py
python tools/dp_make_bounds.py
python tools/dp_measure_accuracy.py
```

About 20 minutes for the tiles on 24 cores.

Things that will surprise you:

- **`get_pixmap(clip=...)` takes y-down page coordinates**, not PDF user
  space. PDF user space has y increasing upward from the bottom-left of the
  media box. Mixing them mirrors the map about the horizontal, silently.
  `Block.user_to_page()` in `tools/dp_georef.py` exists for exactly this.
- **The viewport's `/BBox` has a flipped y** in this book (`lly > ury`).
  Getting the corner order wrong is easy and produces a map that is
  confidently, subtly wrong.
- **A bilinear over lon/lat costs 11 m** against the true projection across
  one sheet. Use the projection.
- **The blank-tile rule drops the seams** if you render each sheet
  independently. Merge, do not overwrite.
- **WebP at q90** is the best size/quality trade here: 9.6 KB average at z17
  against 50 KB for PNG, and visually identical on this artwork. The
  delivered tiles are WebP; the client should check their CDN serves
  `image/webp`.

---

## 8. If you have to brief the client

- The map sits on the ground to about **6 m median (16 m for 90% of the
  area)** against OSM roads. The plan's own printed coordinates were ~85 m
  off; we measured that across the whole area and corrected it.
- **Every survey number is the sheet's own text** — 211,630 of them, exact,
  clickable, and searchable in a later release. None of them is OCR.
- It is **crisp at every zoom** up to z17, because it is rendered from vector
  drawings rather than a scan. The numbers are legible at full zoom.
- Where the map still differs from the satellite by a road's width, that is
  usually the plan's drawing differing from what was built. Use the
  satellite-date picker (defaults to Oct 2021, the plan's era) to compare.
- The **land-use legend is the plan's own**, 22 classes with the sheet's
  exact colours.
- The old map is still on disk if anyone wants to compare.

---

## 9. The four parts, the zones, and marking records

Added after the first delivery, at the client's request.

### The four parts against the satellite

```
tools/dp_compare_parts.py
```

For each of the four part sheets: the satellite over exactly the same ground,
the plan blended over it, and the plan alone, stacked, with a landmark
crosshair on each. Output in `work/newdp/parts/`:

| file | what it shows |
| --- | --- |
| `NW_blend.png` … `SE_blend.png` | one part each: satellite / blend / plan |
| `all_four.png` | the four parts blended, in one figure |
| `summary.json` | each part's extent and landmark |

The blend is the check that matters: the plan's roads and the satellite's
roads are the same roads, so a correct georeference makes them coincide. All
four parts agree, at 37-40 m median against the satellite - the same figure
the whole book gives, which is what you expect if no part is out of place.

### The zones

```
tools/dp_zone_masks.py       the sheet rasterised, every pixel assigned to
                             the nearest legend colour -> the zones
tools/dp_zone_satellite.py   the zones against a satellite land-cover screen
tools/dp_zone_offsets.py     the plan's road corridors against the
                             satellite's, measured per zone
```

**The zones are read from the rendered sheet, not from the drawing objects.**
The sheets draw their land use as hatched areas, and the drawing objects are
tens of thousands of slivers a few square points in area - there is no path
to extract. The colour is the legend, so the colour is what is read, and the
result is the only description of the zones that matches what a reader of
the sheet sees.

**What was tried and rejected.** A land-cover screen of the satellite
(water / vegetated / built-up / bare) was built three ways - fixed RGB
thresholds, k-means clusters named from the plan, and thresholds learned
from the plan's own unambiguous classes. All three produced a table where
the plan's residential zones and its agricultural zones had identical
statistics, which is not a credible result.

The reason is in the imagery, and it is worth knowing: **it is a dry-season
capture.** Kankaria is a dry salt pan, pale brown, not open blue water; the
fields are brown stubble, not green; the whole book reads brown and grey. A
built-up area and a ploughed field differ in texture, not much in colour, and
with no near-infrared band there is no index that separates them reliably.

So the zone comparison shipped is a **measurement**, not a classification:

| file | what it is |
| --- | --- |
| `data/dp2021/zone_satellite.json` | each zone's plan area, and the satellite's median brightness, greenness and texture over it |
| `data/dp2021/zone_offsets.json` | each zone's **offset from the satellite**, measured by the same FFT cross-correlation as the global figure, with every sample attributed to the zone it falls in |
| `work/newdp/zone_offsets.md` | the table |

The offsets are the interesting part:

| zone | offset median |
| --- | ---: |
| Core Walled City | 17.6 m |
| Sewage / Water Treatment | 30.8 m |
| Prime Agricultural A2 | 35.4 m |
| General Agricultural A1 | 39.6 m |
| Commercial Zone C | 48.6 m |
| Restricted Agricultural A3 | 50.5 m |

The plan agrees best with the ground in the old walled core, where the street
fabric has not changed, and worst where the plan's boundaries are newest.
That is the expected shape, and it is the shape a classification-free
measurement gives.

(Measured before the 2026-10-22 ground correction, with the withdrawn
rooftop-prone detector; kept for history, do not cite.)

Note the zone-attributed figure (39.8 m overall) is higher than the global
one (19.8 m). They measure different things: the global figure samples the
whole book, where most of the ground is open and the plan's road corridors
line up well; the zone figure samples only inside zoned land, where the
plan's structure is the plot boundaries, which the satellite does not carry.
Quote the global figure for the map, the zone figure for the zones.

### Marking a particular record

```
tools/dp_mark_records.py 461/2 504 435
```

Finds every occurrence of the survey numbers, rings them on the map, and
writes both a figure and a GeoJSON the viewer can load.

Survey numbers **repeat across the district** - "461" is on 115 different
plots, because the numbers restart in each village - so a record is a number
*and* a position, and every occurrence is marked and numbered.

In the viewer there is a **Find a survey record** box, and a deep link:

```
http://127.0.0.1:8777/web/index.html#rec=461%2F2
```

which marks the record on load. It is backed by
`data/dp2021/number_index.json` - 28,807 distinct numbers, 5.35 MB, which is
the whole search index.

The numbers are the sheet's own text, so this is not OCR and it is not
approximate. They are the numbers the drawing used.

---

## 9. The plot boundaries — the plan's own parcel geometry

Added after the boundary work. This is the layer that was missing and that
the client actually needed.

### What was found

The four part sheets are vector drawings, and they **draw the plot
boundaries**. Those strokes had not been extracted. What the project had
instead was a *land-use zone* layer, traced from the sheet's fill colours,
which answers "what is this ground zoned as" and not "where is the plot".

| | what it is | how the plan draws it | how many |
| --- | --- | --- | ---: |
| **plot boundary** | the parcel a survey number is printed in | a closed ring, 5–20 vertices, 0.48 pt black (0.96 pt red in some regions) | 161,857 |
| **plot boundary, assembled** | the same, where the plan draws the plots as shared strokes rather than as rings | a face of the plot line network | 37,640 |
| **unit boundary** | the plan's own village, TP-scheme and settlement limits | a closed ring, 200–600 vertices, 2.4 pt black / magenta / blue | 3,487 |

### The plots the plan does not draw as rings

Most of the book draws a plot as a closed ring, and the extractor found
161,857 of them. Three regions do not: the **1.8 km seam strips** where the
four part sheets overlap, the **old city**, drawn in 0.96 pt red, and the
**southern and western fringe**. There the plan draws the plot lines as
*loose, shared strokes* — one line between two plots, not one ring per plot
— so the closed-ring extractor had nothing to find and 45,481 records had no
plot at all.

Those lines are still in the drawing; they only need to be assembled.
`tools/dp_polygonize_plots.py` nodes the plan's own plot strokes and
polygonizes them: every **face** of the line network is a candidate plot,
and a face that holds a survey number is that record's plot — the same "by
content, not by style" rule the closed-ring extractor uses. It adds
37,640 plots and places 39,467 of the 45,481 orphaned records.

**It only adds.** Where a closed ring already exists it is left alone, and
the polygonized face of the same plot is used as the check: the two are
drawn from the same strokes, so they should agree. They do — median IoU
**0.9999** over 160,533 records, and the 10th percentile is still 0.9991.

Work is done in windows of the sheet with a margin. A face that touches the
margin's edge may be cut off by it, so only faces wholly inside the window
and centred in its core are kept; the neighbouring window owns the rest.

### How a record is matched to its plot

**By content, not by style.** A plot is a closed ring that contains exactly
one survey number. On the NW sheet 88.4% of the numbers fall inside exactly
one thin black ring, and 56,881 of those rings hold exactly one number. The
rings that hold *many* numbers are the plan's own village and scheme
boundaries, and they carry the plan's own place names inside them — which is
how they are named.

A record is then assigned to the **smallest** ring that contains it, so a
record inside a village is also inside a plot, and the plot wins.

### The result

| | |
| --- | ---: |
| closed rings on the four sheets | 234,247 |
| after de-duplicating the 1.8 km seams | 193,914 (20,526 dropped) |
| plot boundaries from the closed rings | 161,857 |
| plot boundaries from the shared strokes | 37,640 |
| **plot boundaries in total** | **199,497** |
| records placed in a plot | 205,616 of 211,630 (97.2%) |
| records in a **named village boundary** | 160,497 (75.8%) |
| records in a TP-scheme boundary | 32,580 (15.4%) |
| plots holding exactly one survey number | 194,235 (97.4%) |
| median plot area | 4,599 m² (0.46 ha) |

The 2.8% of records with no plot are the ones the plan did not draw a closed
boundary around — open ground, settlements drawn as areas rather than as
plots, and the dense village-inlay blocks.

### The two coordinate traps

Both are silent, and both produce a layer that still *looks* plausible.

**1. Drawing items and text spans are in different spaces.**
`get_text()` reports bboxes in **page space** (origin top-left, y
downward). `get_drawings()` reports items in **PDF user space** (origin
bottom-left, y upward). Treating one as the other mirrors the drawing about
the horizontal. The tell is the number: **42%** of the records landed in a
"plot" that was the right shape in the wrong place, against **79%** after
the fix.

**2. `get_pixmap(clip=…)` takes a clip in page space, not user space.**
Passing a user-space rectangle flips the render. This one bit the
*verification* rather than the data: the extracted boundaries were exact,
but the check was measuring them against a mirrored render and reporting
4.5 m of error that was not there.

### How it was verified

| | |
| --- | --- |
| records inside their own parcel | **205,615 / 205,616 — 100.00%** |
| median offset from the plan's own drawn line | **0.000 m** |
| vertices within 1 m of the plan's line | 76.4% |
| the sheet fit's own RMS (internal consistency), for comparison | ±2.2 m |

The offset measurement renders the plan **with the text wiped out**, so the
numbers cannot be mistaken for lines, and measures every extracted parcel
vertex against the nearest ink. It is in
`tools/dp_measure_boundary_offset.py`.

### In the viewer

* **Plot boundaries** — a toggle for the whole layer.
* Clicking a survey number **highlights the plot it is in**, drawn white-cased
  then magenta so it reads on top of the plan's own black lines, and a badge
  says the plot's survey number and area.
* **Village / scheme boundaries** — a toggle for the plan's own unit lines.
* The search box finds a **village by the plan's own boundary** first, and
  only falls back to the loose place-name match if the plan has no boundary
  with that name.
* A **record detail panel** gives the plot, the plot's area, the village,
  the scheme and the land use.
* Clicking a number also opens a **set-out (markout) sheet** for the plot it
  is in - the corners in the plan's own metres, with the bearings and the
  distances. Section 11 is that work.



## 10. The records, the client's data, and Google Earth

The last piece the client asked for: every survey record tied to the data
they supplied, and checked against Google Earth.

### The per-village extracts are not where their crop box says

This was the missing mapping, and it is worth stating plainly because it
changes the numbers.

All 145 per-village PDFs quote the **same viewport** as the part sheet they
were cut from, so the viewport cannot say which sheet an extract belongs to.
The crop box looks like it could - but it does not. Measured against the
sheets, the crop boxes are a **median of 19.7 km out** (max 34 km). They are
bookkeeping, not position.

What works is that the extracts and the sheets are the same plan drawn
twice, so they carry the same words. Matching a village extract's place
names against the sheets' place names gives the offset between the two
pages, and that offset puts the village on the sheet, and therefore on the
Earth, through the sheet's own surveyed transform.

```
tools/dp_map_villages.py
```

`data/dp2021/villages/` holds, for each of the 145 extracts: the sheet it
came from, how many place names matched, its **true** ground extent, and
the source file. `index.json` lists them by sheet.

### Every record, tied to a village, a zone and a sheet

```
tools/dp_map_records.py
```

`data/dp2021/records.ndjson` - one line per record:

```json
{"t":"461/2","lon":72.4907,"lat":23.0659,"block":"SE",
 "village":"BAREJA","zone":"Residental Zone I-R1","built_up_today":true,
 "village_km":0.412}
```

| | |
| --- | --- |
| records | 211,630 |
| with a village | 191,695 (90.6%) |
| distinct villages | 154 |
| with a land-use zone | 83,206 |

The village is the nearest place name on the same sheet, within 4 km. The
zone is the majority legend class in a small window around the record - the
record's own glyphs are drawn over the zone colour, so a single-pixel
lookup would fail.

### The records against Google Earth

```
tools/dp_verify_records.py
```

The same measurement the whole project rests on - the plan's road corridors
against the satellite, by FFT cross-correlation - with every window tied to
the survey record nearest its centre.

| | |
| --- | ---: |
| measurements | 849 |
| offset from Google Earth | **20.3 m median, 47.0 m p90** |

`data/dp2021/record_checks.json` has it per village and per land-use zone;
`data/dp2021/record_disagreements.json` is the **ranked list of the records
where the plan and Google Earth disagree most** - the ones worth a human
looking at, with their village, their zone and their coordinates.

The zone table again shows the shape you would expect: the old core
(Residential Zone I) agrees best with the present day at 14.7 m, and the
zones whose boundaries are newest agree least.

### A note on what is NOT claimed

The per-record `built_up_today` flag is in the output, but it is the weakest
thing here and it should be read with care. It is a texture threshold
learned from the plan's own dense residential zones, and the dry-season
imagery does not separate a built-up area from a ploughed field by texture
alone - the same limitation as the land-cover screen in section 9. Use the
**measured offsets** for anything that matters, not this flag.

### In the viewer

The search box takes a survey number **or a village name**:

* a number marks every occurrence of it (numbers repeat across the
  district - "461" is on 115 plots, because they restart in each village);
* a village marks its records and flies to its extent.

`#rec=461%2F2` deep-links to a record. The land-use zone boundaries load
from `data/dp2021/zones/15/{x}/{y}.geojson` as map tiles, so the client can
turn the plan's own zoning on and off over the satellite.

---

## 11. Marking a record properly, and the set-out sheet

The last piece of work: the **marking** side of the viewer, polished into
something a surveyor can actually work with, and a **set-out (markout)
sheet** they can carry out.

### A survey number is a set of places, not a place

"461" is on 115 plots, because the numbers restart in every village. So a
marked number is a **set**, and the viewer treats it as one:

* every occurrence is marked with a **real marker** - a white disc, a red
  ring and the occurrence's number - not a plain circle;
* the occurrence **in hand** is picked out in full (a glow, a heavy ring, the
  number in bold above it) and the map flies to it;
* the **occurrence list** gives every occurrence's village, land use and
  position, and the list is a **ring, not a queue** - stepping past the last
  one comes back to the first, because the surveyor is walking a village,
  not working through a queue;
* the **left and right arrow keys** step through it. They are caught before
  MapLibre sees them, so the map does not pan out from under the surveyor
  as well;
* **link** copies a deep link to the occurrence in hand
  (`#rec=461&occ=4`), so a colleague opens exactly the same plot.

### The set-out sheet

Picking an occurrence opens a **set-out (markout) sheet**: the corners of
that plot, in the plan's own projected metres, with the bearing and the
distance of every edge.

| column | what it is |
| --- | --- |
| corner | the corner number, counted from the south-west corner, counter-clockwise |
| easting / northing | the corner in the plan's own grid |
| azimuth | the azimuth of the edge from this corner to the next |
| length | the length of that edge, in metres |
| reduced bearing | the same edge as a quadrant bearing |

The sheet also carries the plot's area and perimeter, the corner count, the
plan's **own** area figure beside the computed one, and the grid's full
definition.

On the map the same corners are drawn: the plot's edges in blue, every
corner as a white dot with its number, and the length of every edge along
the edge. The azimuths can be switched on as well, above the edges.

**Where the numbers come from.** The projection is the plan's own -
`WGS_1984_Transverse_Mercator`, central meridian 71.5°E, latitude of
origin 22.5°N, k₀ 0.999738, false easting/northing 400000/400000. It is the
same CRS `tools/dp_georef.py` uses to put the sheets on the Earth, so the
sheet and the map cannot disagree about where a plot is.

### The honest empty case

**6,014 of the 211,630 records - 2.8% - sit outside every plot boundary
the vector sheets gave up.** For those there is no set-out to give, and the
viewer says so rather than showing an empty box:

> **No set-out for this spot.** The plan's own plot boundaries were
> recovered from the vector sheets, and this record sits outside every
> boundary that was recovered - 6,014 of the 211,630 records are like that,
> about one in thirty-five. Step to the next occurrence, or click a plot
> boundary on the map to set that one out.

This was **21.5%** before the shared strokes were assembled (section 9). The
recovery is strictly additive: all 166,149 records that already had a plot
kept **exactly** the plot they had, and the 39,467 that gained one gained it
from a newly assembled face.

### The corners are reduced, not just listed

The plan draws a plot boundary as a polyline with a vertex every few
centimetres along a straight edge. A set-out sheet that lists 51 points for
a five-sided plot is a drawing, not a set-out. So the corner list is
reduced first: a vertex is dropped when it lies within **5 cm** of the
straight line through its neighbours, and duplicates within **1 cm** are
merged. What is left is then counted from the south-west corner, going
counter-clockwise, so that two sheets for the same plot always start in the
same place.

### Exports, and the print

* **CSV** - the corner table for a spreadsheet. RFC 4180: the comment lines
  carry no commas, and the reduced bearing (which holds a double quote for
  the seconds) is quoted with the inner quote doubled.
* **JSON** - the same table with the CRS spelled out, the plan's own area,
  and the corner count.
* **Print sheet** - the print view is the sheet **alone**. The map, the
  panels, the occurrence list, the buttons and the checkboxes are all out
  of the way, and what is left is the field document: a title, the date, the
  corner table, the summary and the caveat.

### How it was verified

The set-out is computed **in the browser**, in `web/markout.js`, so that the
sheet on the screen and the sheet that was checked cannot drift apart.
`tools/dp_markout_reference.py` computes the same thing with **pyproj**, and
`tools/dp_check_markout.js` compares them:

```
python tools/dp_markout_reference.py
node tools/dp_check_markout.js
```

```
parcels checked      30
corners checked      449  (from 455 drawn vertices)
worst position       0.000000 m   (tolerance 0.002)
worst edge length    0.000000 m   (tolerance 0.002)
worst azimuth        0.000000 deg (tolerance 0.0005)
worst area           0.000061 m2  (tolerance 0.01)
worst lon/lat        0.000296 m   (tolerance 0.005)
worst round trip     0.000304 m   (tolerance 0.005)
```

The same check is run against the plots that were **assembled** from the
plan's shared strokes rather than read off a closed ring, because those
corners come from a different geometry path and must not be assumed to
behave the same way:

```
python tools/dp_markout_reference.py --style polygonized --out work/markout_reference_polygonized.json
node tools/dp_check_markout.js work/markout_reference_polygonized.json
```

```
parcels checked      30
corners checked      504  (from 531 drawn vertices)
worst position       0.000000 m   (tolerance 0.002)
worst edge length    0.000000 m   (tolerance 0.002)
worst azimuth        0.000000 deg (tolerance 0.0005)
worst area           0.000137 m2  (tolerance 0.01)
worst lon/lat        0.000296 m   (tolerance 0.005)
worst round trip     0.000304 m   (tolerance 0.005)
```

The check also fails if the set-out area differs from the plan's **own**
recorded area by more than 0.5%.

Two things were wrong on the way, and both are worth recording because they
are the kind of mistake that produces a sheet that looks perfect and is
wrong:

* the **inverse** projection was out by 4.5 m. Recovering ξ and η in one
  step leaves the latitude's own convergence term behind; it has to be a
  **fixed-point iteration**, four rounds. The round trip is now 0.3 mm.
* the χ → φ series was carrying `(2j − 1)` factors that do not belong in
  it. With them gone, and the latitude of origin subtracted in the forward
  transform, the two agree to the millimetre.

### Two smaller things

* `data/dp2021/number_index.json` is now at **seven decimals**, not five.
  At five decimals a position is rounded by up to a metre, and the viewer
  then cannot tell which of two records with the same number it is looking
  at - which is the whole difficulty with these numbers, since they restart
  in every village. The index grew from 4.5 MB to 5.35 MB.
* A marked occurrence is also one of the plan's own numbers, so one click
  reaches two handlers. The occurrence **claims** the click and the
  plan-number handler stands aside; otherwise the two race, and the sheet's
  headings would be whichever landed last.

