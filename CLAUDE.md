# AUDA DP 2021 web map — project context

## What we are building
A townplanmap.com-style web map for the Ahmedabad Urban Development Authority (AUDA)
Development Plan: the plan's land-use zoning as a georeferenced raster tile pyramid over
an OSM/satellite basemap, plus the plan's own survey numbers, plot boundaries, village /
TP-scheme boundaries and land-use zones as clickable, searchable layers.

Goal: match townplanmap.com's map experience (it serves the same kind of static raster
plan tiles from S3 with a BottomLeft/TopRight box), but with better accuracy and real
vector data extracted from the source. The gap vs. townplanmap is the product shell
(mobile/PWA, Gujarati/Hindi toggle, search backend, optional auth), not the map data.

## Read first
- `PROJECT-GUIDE.md` — mental model, what's done, what's next, traps. Start here.
- `README-DP2021.md` — code-level detail of the DP 2021 pipeline.
- `TOWNPLANMAP-COMPARISON.md` — gap analysis + roadmap toward townplanmap parity.
- `REPORT-townplanmap.md` — reverse-engineering of townplanmap.com (stack, data model, security findings).
- `REVIEW.md` — applied fixes and open recommendations.
- `README.md` — the old RDP 2022 scan pipeline (superseded; kept for comparison).

## Layout
- `NEW DP BOOK AUDA/` — client's source PDFs. **Read-only, never modify.** 4 georeferenced
  vector part sheets (NW/NE/SW/SE), 145 village extracts, 159 printed book sheets.
- `tools/` — Python build pipeline (`dp_*.py` = DP 2021; un-prefixed = old RDP 2022) + `serve.js`.
- `tiles_dp2021/{z}/{x}/{y}.webp` — delivered tiles, z11–z17 (~43k tiles).
- `data/dp2021/` — generated JSON/GeoJSON (bounds, legend, number index, parcels, zones, records).
- `web/index.html` — the viewer: single static file, MapLibre GL from CDN, no build step.
  Mobile-first app shell (search bar, action buttons, layers/legend drawers, result sheet;
  bottom sheets under 760px). Shell JS is the last IIFE in the script; it only drives the
  original element ids — map logic is untouched. Pre-shell copy: `work/index.before-shell.html`.
- `web/sw.js` — service worker, served at `/sw.js` (alias in `serve.js`) for root scope.
  Plan tiles + data buckets cache-first; bump `VERSION` after any rebuild. Never caches OSM/Esri.
- `work/` — intermediates, logs, verification images. `db/schema.sql` — target PostGIS shape (unpopulated).

## Run
```powershell
node --env-file=.env tools/serve.js     # http://127.0.0.1:8777/web/index.html
```
Build order and flags: see `PROJECT-GUIDE.md` §7. Plot boundaries:
`dp_extract_boundaries.py` (closed rings) → `dp_polygonize_plots.py` (open linework in the
seam strips / old city; adds plots, IoU-checked against closed rings) → `dp_join_boundaries.py`
→ `bucket_parcels.py`. Then bump `VERSION` in `web/sw.js`.

## Rules
- `tools/dp_georef.py` is the single owner of where pages land on Earth. Never hardcode extents elsewhere.
- `data/dp2021/*` is generated — regenerate via tools, don't hand-edit.
- Coordinate traps (silent, mirror the map): `get_text()` = page space (y down),
  `get_drawings()` = PDF user space (y up), `get_pixmap(clip=)` takes page space.
  Use `Block.user_to_page()`. Viewport `/BBox` has flipped y.
- Fit the 2×2 sheet grid, not individual files. Sheets overlap ~1.8 km: merge all covering
  sheets per tile, never overwrite.
- Classify labels by font size, not shape (5.8/6.7 pt = survey numbers).
- Accuracy wording: ±2.2 m = sheet grid fit to its own printed corners (internal only; the corners
  were ~85 m off). Ground accuracy = 6.4 m median / 16.4 m p90 vs OSM roads (held-out) after the affine
  correction (`work/dp_ground_correction.json`) + local warp (`work/dp_ground_warp.json`, Gaussian
  3 km), both applied in `dp_georef.FWD/INV` (in-sample 4.8/13.9; affine alone 7.7/19.7). The old "~20 m vs
  satellite" figure is withdrawn. After rebuilding tiles, re-measure with `dp_osm_offsets.py
  --built-with <id>` before refitting.
- MapLibre zoom is 512-px based: viewer z16 = 1.1 m/px. Screenshot measurements use 2**(zoom+1).
- MapLibre: `line-join` is layout not paint; bounds are `[[lon,lat],[lon,lat]]`.
- `.env` holds a live Google key — never commit or print it.
- Deploy: GitHub Pages via `.github/workflows/pages.yml`, live at
  https://kunjshah95.github.io/geomapping/. `tools/build_site.js` owns the allowlist and
  writes `dist/` (shell + glyphs + `tiles_dp2021/` + the `data/dp2021` files the viewer
  fetches); the repo root is not publishable. Every URL in `web/index.html` is resolved
  against the site root by `SITE`/`U()` at the top of its script, so the same file works at
  `/web/index.html` (dev) and `/<repo>/index.html` (Pages) — never reintroduce a bare `'/data/...'`.
  Pages caps a site at 1 GB; `node tools/build_site.js --max-tile-zoom=16` drops z17
  (~649 MB total) and rewrites `bounds.json` to match.

## Next up (from TOWNPLANMAP-COMPARISON.md §5)
1. PWA manifest is in; add service worker + bottom-sheet mobile layout.
2. Gujarati/Hindi label toggle (`name_guj` / `name_hin`, Anek fonts).
3. ~~Self-host glyphs~~ done: `web/glyphs/` (Noto Sans Regular/Bold, all ranges incl. Gujarati
   2560-2815 and Devanagari 2304-2559; source + OFL in `web/glyphs/LICENSE.txt`). Every symbol
   layer must set `text-font` to one of those two, or MapLibre falls back to an unhosted stack.
4. Publish tiles to R2, point viewer at CDN via `bounds.json`.
5. Auth only if client wants gating — avoid townplanmap's flaws (REPORT §8).
6. ~~Local ground warp~~ done 2026-10-23: `tools/dp_fit_ground_warp.py` -> `work/dp_ground_warp.json`,
   applied after the affine in dp_georef (`DP_NO_GROUND_WARP=1` disables). Fit only on a field measured
   on affine-only tiles (`work/georef_osm/`); the warped build is measured into `work/georef_osm_warp/`.
