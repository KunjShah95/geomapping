# townplanmap.com vs. our AUDA map — how it was built, and how to build a similar one

Companion to `REPORT-townplanmap.md` (full reverse-engineering) and `PROJECT-GUIDE.md`
(our project). This document is the practical bridge: what townplanmap.com actually is,
what we already have, and the concrete steps to make our viewer a townplanmap-style
product on OpenStreetMap.

---

## 1. How townplanmap.com was built (summary)

Source: `REPORT-townplanmap.md`, reverse-engineered from the shipped Angular/Ionic bundle.

| Layer | Choice |
|---|---|
| Frontend | Angular (standalone, lazy routes) + Ionic UI kit + Capacitor (PWA + native app) |
| Map renderer | **MapLibre GL JS** (style spec v8, `addProtocol("pmtiles", …)`) |
| Basemaps | Mappls (Indian vector + advanced), MapTiler, ArcGIS World Imagery, **OSM** |
| Plan data | **Pre-baked PNG raster tile pyramids on S3** (`tpmap-auda/…/{z}/{x}/{y}.png`), georeferenced with a `BottomLeft`/`TopRight` + `ZoomBounds` box — exactly our model |
| Vector overlays | GeoServer WFS/WMS (`tpm:inventory`, `tpm:insights`, `tpm:filtered_area_project_counts_symbol`) + PMTiles |
| Backend | Firebase (Auth custom tokens, Firestore, Remote Config, Storage), Cloud Run tile/token server, GeoServer |
| Search | Typesense |
| Payments | Razorpay + RevenueCat (in-app) |
| Analytics | Mixpanel (incl. session replay), Firebase Crashlytics |
| Auth | Phone-number OTP → Firebase custom token (minted by an unauthenticated Cloud Run hop) |

**The key insight for us:** townplanmap's plan layer is *not* a queryable vector cadastral DB.
It is a static raster tile pyramid rendered offline from government plan sheets, draped over a
slippy basemap with an opacity slider. **That is precisely what we already ship** (`tiles_dp2021/`,
`data/dp2021/bounds.json`). The gap is not in the map data — it is in the *product shell*
(auth, search, mobile, monetisation, multilingual).

---

## 2. How our project is built (summary)

| Layer | Choice |
|---|---|
| Build | Python pipeline: GeoPDF → georeference (sheet grid + OSM-fitted ground correction, 6.4 m median vs OSM roads, held-out) → raster tiles (WebP) + vector labels/zones |
| Data | `tiles_dp2021/{z}/{x}/{y}.webp` (43,458 tiles), `data/dp2021/*.json` + bucketed GeoJSON |
| Viewer | Single static `web/index.html` — MapLibre GL from CDN, no build step |
| Basemaps | OSM + Esri World Imagery (dev only) |
| Serving | `tools/serve.js` (dependency-free static server + `.env` → `/config.js`) |
| Hosting target | Static (R2 for tiles, no egress fees) |

We already implement the *core* of townplanmap's map: raster plan tiles over a slippy basemap,
clickable survey numbers, land-use zones, search over survey numbers/villages, legend, scale.

---

## 3. Gap analysis — what townplanmap has that we don't

| Feature | townplanmap | Us | Priority for "similar" |
|---|---|---|---|
| MapLibre renderer + raster plan tiles | yes | yes | done |
| OSM basemap | yes | yes (now default) | done |
| Satellite basemap | yes (ArcGIS) | yes (Esri) | done |
| Opacity slider + layer toggles | yes | yes | done |
| Survey-number click/search | yes (RERA/projects) | yes (survey nums) | done |
| Legend | yes | yes | done |
| Mobile-first / PWA | yes (Ionic+Capacitor) | no | **high** |
| Multilingual (Gujarati/Hindi/English) | yes | partial (Gujarati labels exist) | **high** |
| Auth (phone OTP) | yes (Firebase) | no | medium |
| Search backend (Typesense) | yes | client-side JSON index | medium |
| Payments / subscription | yes (Razorpay) | no | low (out of scope) |
| RERA projects / notices / land listings | yes | no | low (different product) |
| Short-lived tile tokens | yes | no (static tiles) | not needed (static) |
| Analytics (Mixpanel) | yes | no | low |
| Proper OSM attribution + tile-policy compliance | yes | partial | **high** |

---

## 4. How to build a similar one (using OpenStreetMap)

Principle: keep our Python build + static tile pyramid (it is already the right architecture),
and upgrade the *viewer shell* to townplanmap's UX patterns, with OSM as the default basemap.

### 4.1 Basemap — OpenStreetMap (done in this change)

- Default basemap is now **OpenStreetMap** (was satellite).
- OSM attribution is carried by the source (`© OpenStreetMap contributors`) and shown by
  MapLibre's attribution control.
- **OSM tile policy compliance:** the public `tile.openstreetmap.org` endpoint is for
  light/development use only. For a real product, either (a) self-host tiles via a tile
  server (tiling OSM extracts with `tileserver-gl` / `openmaptiles`), or (b) use a keyed
  provider (MapTiler, Mapbox, Mappls) with referrer-restricted keys. Keep OSM as the
  fallback/dev basemap.

### 4.2 Mobile / PWA (next)

- Add `web/manifest.webmanifest` (name, icons, theme_color, display: standalone) and link it
  in `<head>`. (Done in this change as a starter.)
- Add a service worker for offline tile caching of the plan pyramid (cache-first for
  `/tiles_dp2021/**`, network-first for data JSON). Use Workbox or a hand-rolled SW.
- Make the side panel a bottom sheet on small screens (CSS `@media (max-width: 760px)`).
  Currently the legend hides on mobile; promote it to a collapsible drawer.
- Touch targets ≥ 44px, pinch-zoom already handled by MapLibre.

### 4.3 Multilingual (high value for Gujarat)

- We already have Gujarati place labels (`labels.geojson` with `name_guj`?) — verify and
  wire a language toggle that switches `text-field` between `name` / `name_guj` / `name_hin`,
  mirroring townplanmap's `selectedLanguage` switch and font stacks
  (`["Anek Gujarati"]`, `["Anek Devanagari"]`, `["Open Sans Bold"]`).
- Gujarati/Devanagari glyph ranges are already self-hosted in `web/glyphs/` (Noto Sans
  Regular/Bold, ranges 2560-2815 and 2304-2559), so only the label data and toggle remain.

### 4.4 Search

- Keep client-side index for survey numbers (already fast: 4.5 MB `number_index.json`).
- For a townplanmap-style search (projects, notices, villages), add a small backend
  (Cloud Run / Vercel function) over Firestore/Postgres with Typesense or pg full-text.
  Our `db/schema.sql` + PostGIS target is the right foundation.

### 4.5 Auth / monetisation (only if needed)

- If the client wants gated access (like townplanmap's `maxusersession: 5`), add Firebase
  Auth (phone OTP) + a Cloud Run token verifier. **Fix the security issues noted in
  REPORT-townplanmap.md §8** (custom-token minting must verify OTP server-side; admin check
  must be a Firebase custom claim, not client-side `Array.includes`).
- Payments: Razorpay (India) if monetising.

### 4.6 Hosting

- Tiles: R2 (no egress) — already planned (`tools/upload_r2.py`).
- Viewer: any static host (Cloudflare Pages / GitHub Pages / S3+CloudFront).
- Add HSTS + CSP + `X-Content-Type-Options` + `Referrer-Policy` (townplanmap lacks these;
  our `serve.js` already sets most).

---

## 5. Immediate next steps (ordered)

1. **Done:** OSM is now the default basemap; attribution present.
2. Add PWA manifest + bottom-sheet mobile layout.
3. Wire Gujarati/Hindi language toggle using existing label data.
4. ~~Replace `demotiles.maplibre.org` glyphs~~ - done 2026-10-23: self-hosted in `web/glyphs/`, cached cache-first by `web/sw.js`.
5. (Optional) Add Firebase Auth only if access gating is required.
6. Publish tiles to R2 and point the viewer at the CDN URL via `bounds.json`.

---

## 6. What NOT to copy from townplanmap

- Its auth model (unauthenticated custom-token minting, client-side admin check) —
  security findings in `REPORT-townplanmap.md §8`.
- Its beta-infra-by-hostname trick (`location.hostname !== "townplanmap.com"` → staging)
  — it silently routes production traffic to staging.
- Mixpanel session replay over parcel-level land data — privacy decision needed.
- Third-party keys shipped without origin restriction.

---

## 7. Files touched in this change

- `web/index.html` — OSM set as default basemap; satellite layer now defaults to hidden.
- `web/manifest.webmanifest` (new) — PWA manifest starter.
- `TOWNPLANMAP-COMPARISON.md` (this file).
