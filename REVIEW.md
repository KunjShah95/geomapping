# Codebase review and improvements

Scope: `web/` viewer, `tools/serve.js`, and the Python build tools under `tools/`.

## Applied fixes (security & CI)
- Added SRI integrity hashes and a Content-Security-Policy to both `web/index.html` and `web/legacy_rdp2022.html`.
- Added `.github/workflows/ci.yml` running `node --check`, `compileall`, and JSON validation.
- Added `tools/bucket_legacy_plots.py` for lazy-loading the legacy plot numbers.

## Applied fixes (boundaries)
- Added `tools/bucket_zones.py` to bucket the 144k land-use zone polygons into per-tile GeoJSON (z15), generated under `data/dp2021/zones/`.
- `web/index.html`: added a "Plot boundaries" toggle, lazy-loaded zone tiles, a contrasting (cyan) highlight layer, and point-in-polygon lookup so clicking a survey number / record highlights its boundary.

## Plot boundaries — the plan's own parcel geometry

The plan's sheets **draw** the plot boundaries as vector strokes. They had
never been extracted, so a "boundary" in this project meant a *land-use
colour region* — the wrong shape for a record. These are the new tools.

| tool | what it does |
| --- | --- |
| `tools/dp_extract_boundaries.py` | the closed rings off the four sheets, on the Earth, de-duplicated across the 1.8 km seams, split into plots and units |
| `tools/dp_polygonize_plots.py` | the plots the plan draws as shared strokes rather than as rings: nodes the plot linework, polygonizes it, and takes the faces that hold a survey number |
| `tools/dp_join_boundaries.py` | every record → its plot, its village, its scheme, its land use |
| `tools/bucket_parcels.py` | the plots, bucketed by z15 tile for the viewer |
| `tools/dp_check_parcels.py` | the overlay, the content check, the area check |
| `tools/dp_measure_boundary_offset.py` | the extracted boundary against the plan's own drawn line, in metres |
| `tools/dp_export_boundaries_kml.py` | Google Earth KMZ: plots by land-use folder, records by village folder, unit boundaries |
| `tools/dp_boundary_report.py` | the tables — closed rings and assembled plots are counted separately and totalled |

**Result:** 199,497 plot boundaries (161,857 from the closed rings, 37,640
assembled from the shared strokes), 3,487 unit boundaries, 205,616 of 211,630
records placed (97.2%), 160,497 in a named village boundary (75.8%), 97.4% of
plots holding exactly one survey number.

**The assembled plots only add.** Where a closed ring already existed it was
left alone, and the polygonized face of the same plot was used as the check:
the two are drawn from the same strokes, so they should agree, and they do —
median IoU **0.9999** over 160,533 records, 10th percentile 0.9991. On the
record side the recovery is strictly additive: all 166,149 records that
already had a plot kept **exactly** the plot they had, 39,467 gained one, and
none moved or lost theirs.

### Two coordinate-space traps, both silent

**1. `get_drawings()` and `get_text()` are in different spaces.**
`get_text()` reports bboxes in **page space** (origin top-left, y down).
`get_drawings()` reports items in **PDF user space** (origin bottom-left, y
up). Mixing them mirrors the drawing about the horizontal, and the layer
still looks plausible: **42%** of the records landed in a "plot" of the right
shape in the wrong place, against **79%** after the fix.

**2. `get_pixmap(clip=…)` takes a clip in page space, not user space.**
This one bit the *verification*, not the data: the extracted boundaries were
exact, but the check was measuring them against a mirrored render and
reporting 4.5 m of error that was not there. Both `dp_check_parcels.py` and
`dp_measure_boundary_offset.py` now convert the clip.

### How a record is matched to a plot

**By content, not by style.** A plot is a closed ring that contains exactly
one survey number; on the NW sheet 88.4% of the numbers fall inside exactly
one thin black ring, and 56,881 of those rings hold exactly one number. A
record is assigned to the **smallest** ring containing it, so a record inside
a village is also inside a plot, and the plot wins.

### Verification

| | |
| --- | ---: |
| records inside their own parcel | **205,615 / 205,616 — 100.00%** |
| median offset from the plan's own drawn line (NW) | **0.000 m** |
| median offset from the plan's own drawn line (SE) | **0.000 m** |
| vertices within 1 m of the plan's line (SE) | 89.7% |

### Viewer

- **Plot boundaries** toggle — the whole layer, lazy-loaded per z15 tile.
- Clicking a survey number **highlights the plot it is in**, white-cased
  then magenta so it reads on top of the plan's own black lines, with a
  badge giving the plot's survey number and area.
- **Village / scheme boundaries** toggle — the plan's own unit lines.
- The search box finds a village by **the plan's own boundary** first, and
  only falls back to the loose place-name match if the plan has no such
  boundary.
- A **record detail panel** gives plot, plot area, village, scheme, land use.

### Fixed while wiring it up

- `line-join` is a **layout** property in MapLibre, not a paint property —
  as a paint property it aborted the whole style and the map never loaded.
- A Polygon's `coordinates` **are** the rings; `poly[0]` is the first
  *point*, not the ring. The point-in-polygon test needs `g.coordinates` for
  a Polygon and `g.coordinates.map(p => p[0])` for a MultiPolygon.
- `map.fitBounds` wants `[[swLng, swLat], [neLng, neLat]]`; the bbox from
  `village_units.json` is `[minLon, minLat, maxLon, maxLat]`, and the two
  were being passed the wrong way round.
- A regex with a **trailing `|`** has an empty alternative that matches
  every string — it silently emptied the place-name list.


## Applied fixes

### `tools/serve.js` (dev server)
- **Path traversal hardening.** Replaced the weak `file.startsWith(ROOT)` prefix check (which could be bypassed by a sibling directory whose name shares a prefix) with a strict containment check using `path.resolve` and `ROOT + path.sep`.
- **Directory handling.** Replaced `fs.statSync` crash-on-missing with a try/catch and an explicit `isFile()` check.
- **Security headers.** Added `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy` to all responses.

### `web/index.html` (viewer)
- **Initial bounds order.** Fixed `bounds: [[CFG.lat_min, CFG.lon_min], ...]` (lat/lon) to `[[CFG.lon_min, CFG.lat_min], ...]` (lon/lat) to match MapLibre's expected `LngLatBounds` order.
- **Survey-number lookup.** `findRecord` now performs a case-insensitive match and a unique-prefix match, matching the documented "tolerate a partial" behaviour.
- **UX.** Added a `#numCount` element so the loaded plot count is visible; added a `:focus-visible` outline for keyboard accessibility.

### `tools/*.py` (build pipeline)
- Added `encoding="utf-8"` to 24 text-mode `open()` calls that previously relied on the platform default (cp1252 on Windows), which could corrupt non-ASCII paths/content. Applied via AST-based, syntax-verified patching; all 75 scripts still pass `py_compile`.
- Removed BOMs introduced by editor saves where present and removed a stray empty `=3.12,` artefact file.

## Findings / recommendations (not yet applied)

- `web/legacy_rdp2022.html` loads the full 30 MB `/data/plots.geojson` at once; consider reusing the bucketed approach from `index.html`.
- No Content-Security-Policy or Subresource Integrity on the unpkg MapLibre assets; add SRI hashes or self-host for production.
- Python tools use `open()` with relative/`ROOT`-based paths consistently (good); a few long-running scripts write large intermediates under `work/` without atomic writes.
- `.env` currently contains a live Google API key committed to disk; rotate before publishing and keep `.env` out of version control (already in `.gitignore`).
- Consider adding CI: `node --check tools/serve.js`, `python -m py_compile tools/*.py`, and an HTML validator pass.

## Verification
- `node --check tools/serve.js` passes.
- `py_compile` passes for all 75 scripts under `tools/`.
- Dev server responds HTTP 200 on `/web/index.html`.

---

## Applied fixes (marking and set-out)

### `web/index.html` — the marking side

- **A marked occurrence is also one of the plan's own numbers, so one click
  reached two handlers.** The `plot-hit` handler set the sheet out with only
  the survey number; the `rec-hit` handler set it out with the record's own
  detail (village, scheme, land use, sheet). Whichever fetch landed last
  won, so the sheet's headings came and went with the network. The
  occurrence now **claims** the click (`occClaim`) and the plan-number
  handler stands aside. The claim is read one turn of the event loop later,
  so the order the two handlers happen to run in cannot decide the answer.
- **The occurrence list and the previous/next buttons** step through the
  occurrences of a number; the list is a **ring, not a queue**, so stepping
  past the last one comes back to the first.
- **The arrow keys** step through the occurrences, and are caught before
  MapLibre sees them (`capture` phase on `document`, `stopPropagation`) so
  the map does not pan as well. They are ignored while the caret is in a
  text box.
- **`selectOccurrence`** used to fire the parcel lookup and the record
  lookup separately, so the panel and the sheet could belong to different
  selections. It now waits for both (`Promise.all`) and drops the answer if
  a newer selection has been made (`selectToken`).
- **The sheet's headings** are assembled once, in `selectOccurrence`, from
  the record's detail. They used to be patched in afterwards by a separate
  merge step, which could leave a half-populated heading on the sheet.
- **The print view** printed the whole viewer: the top bar, the floating
  tool buttons, the search-result sheet and the occurrence list, plus the
  CSV/JSON/Print buttons and the two checkboxes (the `no-print` class was
  never given a rule). All of it is now out of the way, and what prints is
  the field document alone.

### `web/markout.js` — the set-out engine

- **The inverse projection was out by 4.5 m.** Recovering ξ and η in one
  step leaves the latitude's own convergence term behind; it is now a
  **fixed-point iteration** (four rounds). The round trip is 0.3 mm.
- **The χ → φ series was carrying `(2j − 1)` factors that do not belong in
  it.** Removed, and the latitude of origin is now subtracted in the
  forward transform as well.
- **The CSV was not RFC 4180 clean.** The reduced bearing holds a double
  quote for the seconds, so the field is now quoted with the inner quote
  doubled, and the comment lines carry no commas (the CRS line used
  commas; it uses semicolons now). `interior_angle_deg` was added.
- The corner list is **reduced** before it is printed: a vertex within 5 cm
  of the chord through its neighbours is dropped, duplicates within 1 cm are
  merged, and the corners are counted from the south-west corner,
  counter-clockwise. The plan draws a vertex every few centimetres along a
  straight edge; a set-out sheet that lists 51 points for a five-sided plot
  is a drawing, not a set-out.

### `tools/dp_make_number_index.py`

- The number index is now at **seven decimals**, not five. At five decimals
  a position is rounded by up to a metre, and the viewer then cannot tell
  which of two records with the same number it is looking at — which is the
  whole difficulty with these numbers, since they restart in every village.
  The index grew from 4.5 MB to 5.35 MB.

### Verification

- `node tools/dp_check_markout.js` — the browser's set-out against the
  pyproj reference: 30 parcels, 449 corners from 455 drawn vertices, worst
  position 0.000000 m, worst edge 0.000000 m, worst azimuth 0.000000°,
  worst area 0.000061 m², worst lon/lat 0.000296 m, worst round trip
  0.000304 m. **PASSED.**
- The same check is run against the **assembled** plots
  (`dp_markout_reference.py --style polygonized`, then
  `dp_check_markout.js work/markout_reference_polygonized.json`): 30 parcels,
  504 corners from 531 drawn vertices, worst position 0.000000 m, worst
  azimuth 0.000000°, worst area 0.000137 m². **PASSED.** The assembled
  corners come from a different geometry path and are checked on their own
  rather than assumed to behave like the closed-ring ones.
- The set-out area is also compared with the plan's **own** recorded area,
  and the check fails above 0.5%.
- `node --check` passes on `web/markout.js` and on the viewer's inline
  script.
