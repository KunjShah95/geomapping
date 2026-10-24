# Reverse-engineering report — townplanmap.com

Date: 2026-10-17
Analyst tooling: REA (`rea-agents` 6.5.0) static JavaScript application analysis, CLI fallback
(target-free; REA MCP was not registered for the `opencode` client — see "Method limits").

## 0. Method and evidence

| Item | Value |
| --- | --- |
| REA Evidence ID | `ev_27e939492809e4023eedecd434d7c1b4565964a5f890a30b7bb6e989d2623ced` |
| Provider | `rea-javascript-application` v1 |
| Root artifact SHA-256 | `bb26f44e9c978ac19b52b887dcabb25d4acdcd03015dfe19f3a7d4570638419e` |
| main.js SHA-256 | `3ef97f977abd5fa304c449da354bd0ce164440915ba2ffe80ea507e6b8bf059e` |
| Files analysed | 4 inventoried, 3 parsed as JS, 0 parse failures |
| AST nodes visited | 440,149 |
| Graph | 273 nodes / 686 edges; semantic graph 29,422 nodes, 20,712 relations |
| Findings recorded | 543 |
| Artifacts pulled | `index.html`, `runtime`, `polyfills`, `main` (1.47 MB), plus **85 lazy chunks** (4.18 MB) enumerated from the webpack runtime manifest |

Capture date of the bundles: HTTP `Last-Modified: Tue, 22 Sep 2026 10:29:38 GMT`, served from
`X-Served-By: cache-bom-vanm7210021-BOM` (Google/Fastly CDN).

**Everything below marked "Observed" is read directly out of the shipped bundles or HTTP responses.**
Anything marked "Inference" is my reading of that evidence, not a confirmed runtime fact.

## 1. What the product is (Observed)

An India-focused town-planning / land-use map platform. Its own `llms.txt` claims: 14,000+ RERA
projects, 33,647+ public notices, 551+ TP schemes, 214+ cities, 278+ land listings. Content routes
(`Observed`, from the Angular route table in `main.js`):

```
home  city/:id  tp/:id  land/:id  project/:name/:id  project/:projectTitle
notice/:id  :cityTitle  learn  pricing  pay  session-full  login  welcome
contact  about  terms  legal  privacy-policy  refund  offline  geotag
redirect-to-store  generate-sitemap  **
```

Indexed URL shapes (from `llms.txt`): `/auda`, `/project/{title}-by-{builder}-{location}-{id}`,
`/tp/town-planning-scheme-map-{state}-{district}-{name}`, `/public-notice/...`, `/land/...`.

## 2. Stack (Observed)

| Layer | Technology | Evidence |
| --- | --- | --- |
| Framework | **Angular** (standalone, lazy routes) | `data-critters-container`, `webpackChunkapp`, `ɵprov`/`ɵfac` DI symbols, `ngZone`/`NgZone` references |
| UI kit | **Ionic** | `--ion-*` CSS custom properties, `ion-` theming block, `ionChange` handlers |
| Native shell | **Capacitor** | `me.Ii.isNativePlatform()`, `capacitorjs.com/docs/web/pwa-elements`, `Capacitor`/`Plugins` usage |
| Map renderer | **MapLibre GL JS** | `maplibre.org`, style `version: 8`, `addProtocol("pmtiles", …)` |
| Backend-as-code | **Firebase** (Auth custom tokens, **Firestore**, Remote Config, Storage, Installations) | `firebase_remote_config` IndexedDB node, `securetoken.google.com`, `firebaseremoteconfig.googleapis.com` |
| Search | **Typesense** client | `Typesense.SearchClient`, `x-typesense-api-key` transport |
| Payments | **Razorpay** Checkout | `checkout.razorpay.com/v1/checkout.js`, `new Razorpay(R).open()` |
| Attribution/session replay | **Mixpanel**, incl. session replay | `api-js.mixpanel.com`, `mixpanel.com/projects/replay-redirect` |
| Telemetry | **Firebase Crashlytics** | `FirebaseCrashlyticsWeb` class (web stub: `throw unimplemented`) |
| Auth (secondary) | **Otpless** headless / one-tap | `otpless.com/v4/headless.js`, `enableOneTap`, `initHeadless` |
| Monetisation | **Google AdSense**, **RevenueCat** (in-app subs), **AppsFlyer-style** OneLink | `ca-pub-9622245233051749`, `onelink.to/n4rmgs`, revenuecat keys in env |
| Payments backend | **Razorpay** + custom Cloud Run verifier | `landbnk-payment-verification-…run.app` |
| Geo backend | **GeoServer** (WFS/WMS) + custom **Cloud Run tile server** + **PMTiles** | see §4 |
| Third-party maps | **Mappls** (Indian basemap, vector + advanced maps), **MapTiler**, **ArcGIS World Imagery**, **OSM** | see §4 |
| Misc | Turf.js (geometry), Nominatim reverse geocode, OpenWeather air pollution, Google Translate widget | string inventory |

Deployment target is a PWA: `mobile-web-app-capable`, standalone `index.html`, service-worker chunk
graph present.

## 3. Environment / configuration block (Observed — verbatim from the bundle)

```js
firebase: { apiKey:"AIzaSyB2r13V8v3bm6jrSX8apZnX1HZSOwqFQ0I",
            authDomain:"townplanmap.com", projectId:"townplanmap",
            storageBucket:"townplanmap.appspot.com",
            messagingSenderId:"585432733039",
            appId:"1:585432733039:web:137d2b5633d4662748849" },
googleMap: { apiKey:"AIzaSyDi6EE2bgdH1MminM1jJRkPwF3aWIHwT-I" },
mixpanel:  { token:"691a59293f7ec38a36fdcb531643ce51" },
razorpay:  { key:"rzp_live_TSJKsAqDGbIf8e" },
paymentApiUrl: "https://landbnk-payment-verification-585432733039.us-central1.run.app",
revenuecat: { android:"goog_hqLlFuomzWqlGQzcVMqxKaqsYdx", ios:"appl_mvZkeZSFPlURfYFeXVqyOWzWPBq" },
maxusersession: 5,
useCdnTiles: true
```

Third-party map key observed in a second config location: `apiKey:"NfLSJZ5JUEYvl46yptZkQySWPDdeWR53"`
plus `AppId="6KUUBR9KKTPY96MMLTXK"` (Mappls SDK loader). Firebase installations endpoint is called
with project number `585432733039`.

These are all *client-side* identifiers by design; none of them is a server secret. They are listed
because they map the operator's accounts and because each is a place where domain restriction
matters.

## 4. Data architecture — how the map actually works (Observed)

This is the most interesting part of the reverse-engineering, and it is not what the marketing copy implies.

### 4.1 Plan rasters are pre-baked PNG tile pyramids on public S3

Per-city config entries carry an XYZ raster template plus a georeference box:

```js
Fe.set("auda", {
  shortTitle:"AUDA Urban Development Plan", sourceName:"AUDA",
  sourceUrl:"https://www.auda.org.in/RDP/",
  MapTilerUrl:"https://s3-ap-south-1.amazonaws.com/tpmap-auda/AUDAGUDA2023-Final/{TileMatrix}/{TileCol}/{TileRow}.png",
  BottomLeft:[22.763306,72.178109], TopRight:[23.357406,72.860675],
  ZoomBounds:[9,20], CityName:"Ahmedabad", IsPopular:true })
```

Buckets observed: `tpmap-auda`, `tpmap-bauda`, `tpmap-bhavnagar`, `tpmap-chandigarh`, `tpmap-dahej`,
`tpmap-delhi`, `tpmap-dholera`, `tpmap-gurgaon`, `tpmap-noida`, `tpmap-surat`, `tpmap-vuda2022`.
Each entry also carries legend/logo assets and `ZoomBounds`.

**Inference:** the coloured DP/TP map imagery is a *static raster tile pyramid* rendered offline from
government plan sheets, not a queryable vector cadastral database. That explains the
`BottomLeft`/`TopRight` + `ZoomBounds` georeference (you need an anchor box when you only have an image),
and it explains why the app cannot do real spatial queries against the plan itself.

### 4.2 Vector/raster runtime stack

```js
transformRequest: url => url.includes(tileserver.baseUrl) || url.includes(geoserver.baseUrl)
  ? { url, headers:{ Authorization:`Bearer ${firebase.userToken}` } } : { url }
style:{ version:8, glyphs:"assets/glyphs/{fontstack}/{range}.pbf",
  sources:{
    "arcgis-satellite": { type:"raster", tiles:[satelliteTileUrl], tileSize:256 },
    "dp":               { type:"raster", tiles:[`${tileserver}/v2/dp/{z}/{x}/{y}.png`], tileSize:256 },
    "filtered_area_project_counts_symbol": { type:"vector",
        url:"pmtiles://filtered_area_project_counts_symbol.pmtiles" } },
  layers:[ satellite, dp (zoom 9–20), area_names (symbol), filtered_area_projects_symbol ] }
```

Services (`Observed` base URLs):
- Tile server: `https://tileserver-585432733039.asia-south1.run.app`
  (and `https://beta-tileserver-…` — chosen automatically when `location.hostname` is not
  `townplanmap.com`/`www.townplanmap.com`, i.e. staging is reachable from any other origin)
- GeoServer: `https://server.townplanmap.com/geoserver`
- GeoServer layers used: WFS `typeName=tpm:inventory`, WFS `typeName=tpm:insights` (bbox query),
  WMS `LAYERS=tpm:filtered_area_project_counts_symbol` (Mapbox vector tile output)
- Tile API: `GET /auth/tile-token`, `GET /api/explore?lat=&lng=`, `GET /api/project/{id}`
- CDN tile URLs are pulled at runtime from Firestore `app_settings/layer-urls` (`getLayerUrls()`),
  which also returns `osm`, `satellite`, `dp_toggle`, `tp_toggle`, `village_toggle`.

### 4.3 Short-lived tile tokens (Observed)

`TileTokenService` obtains a Firebase ID token, then:

```
GET {tileserver}/auth/tile-token      Authorization: Bearer <firebase ID token>
→ { token, expires_at, ttl_seconds }
```

It caches the token in local storage, schedules a refresh at `ttl_seconds`, treats a token as expired
when `new Date(expires_at) <= Date.now()`, refetches on Capacitor `appStateChange` when
`isExpiringSoon`, and retries with exponential backoff (3 attempts, `1e3 * 2^(n-1)`).
PMTiles access uses the same token as `?${pmtiles_token}`.

**Inference:** tile delivery is metered per Firebase user; the short TTL plus per-user bearer is a
reasonable design, and the bearer is attached only to tile/geoserver hosts, not to third parties.

### 4.4 Multilingual labelling (Observed)

Text fields switch on `selectedLanguage`: `gu` → `name_guj`, `hi` → `name_hin`, else `name`; font
stacks `["Anek Gujarati"]`, `["Anek Devanagari"]`, `["Open Sans Bold"]`.

## 5. Identity, auth and authorisation (Observed)

### 5.1 Custom-token sign-in via an unauthenticated Cloud Run hop

```js
verifyTestOTP(phone){
  const { token } = await fetch("https://generate-token-585432733039.us-central1.run.app",
    { method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({ phoneNumber: phone }) }).json();
  const cred = signInWithCustomToken ? … : await signInWithCustomToken({ token });
  return user;
}
```

Web and native both land on the same path (native additionally calls the Capacitor Firebase plugin).
There is also a real OTP path using `verificationId` + `credential(verificationId, code)` →
`signInWithCredential`, which *does* prove possession of the SMS.

### 5.2 Identity is a bare phone number

`getLayerUrls`, `listenUserObject(users/{phone})`, `checkAdmin` and Firestore docs are all keyed by
`userData.phone`. Firestore documents read by the client include `app_settings/subscription`
(annual price), `app_settings/superadmin`, `app_settings/layer-urls`, `settings/androidRules`,
`users/{phone}` (with `payAttempt`, `payAttemptsList`).

### 5.3 Admin check is a client-side list membership test

```js
checkAdmin(){
  const u = session.userDataSubject.getValue();
  if (!u || !u.phone) return false;
  const snap = await getDoc(doc(db,"app_settings","superadmin"));
  if (!snap.exists()) return (session.isAdmin=false, false);
  const users = snap.data()?.users ?? [];
  return (session.isAdmin = users.includes(u.phone));
}
```

## 6. Payments (Observed)

Order creation and verification both go to `paymentApiUrl`
(`landbnk-payment-verification-585432733039.us-central1.run.app`):

```
POST {paymentApiUrl}/  Authorization: Bearer <firebase ID token>
     body { phone }          → { updated:boolean, reason, error? }
```

Frontend polling loop `pollForActivation`, fail alert modal, and a `session-full` route gate
(`maxusersession: 5`). Razorpay is loaded on demand from `checkout.razorpay.com` and opened with the
live key from §3.

## 7. HTTP posture of the edge (Observed)

```
HTTP/1.1 200 OK
Cache-Control: max-age=3600
Strict-Transport-Security: max-age=31556926
X-Served-By: cache-bom-vanm7210021-BOM
X-Cache: HIT
```

Present: HSTS. **Absent:** `Content-Security-Policy`, `X-Content-Type-Options`,
`Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`.

`robots.txt` is unusually permissive — `Disallow:` empty for `*`, plus explicit `Allow: /` for
GPTBot, ChatGPT-User, CCBot, anthropic-ai, Claude-Web, PerplexityBot, Applebot-Extended, Bytespider,
Google-Extended. Sitemaps: `/sitemap.xml` (index → `sitemap-static.xml` + more) and `/llms.txt`.

## 8. Findings and concerns

Ordered by how much they matter. Severity is my judgement; each is tied to observed evidence.

1. **Custom-token minting endpoint takes an unauthenticated phone number.**
   `generate-token-…run.app` is a POST that returns a Firebase custom token for a caller-supplied
   `phoneNumber`. The whole security of phone-based auth now rests on that service validating an OTP
   server-side before minting — something the frontend cannot enforce. The sibling function name
   `verifyTestOTP` suggests a test/fallback path. **I did not send any request to this endpoint**;
   verify server-side that an OTP challenge is completed before minting, and that the endpoint is
   rate-limited and not callable cross-origin without an allow-list.
2. **Admin authorisation is a client-side `Array.includes` over a Firestore document.**
   `app_settings/superadmin.users` is compared against `userData.phone` in shipped JavaScript. If that
   document is client-readable — and sibling `app_settings/*` docs clearly are — then the admin phone
   list is public, and "admin" is a client-side flag rather than a server-enforced claim. Move this to a
   Firebase custom claim checked by Firestore/Cloud Run security rules.
3. **Authorisation identity is the phone number alone.** Phone numbers are low-entropy, frequently
   public, and here they are the primary key for user documents, admin checks and payment verification.
   Binding a Firebase UID (or at least an OTP-verified claim) instead would remove a whole class of
   impersonation.
4. **No CSP, and several third-party script origins on a page holding an authenticated session.**
   AdSense, GTM, Meta Pixel, Mixpanel (+ session replay), Otpless, Mappls, Razorpay and Firebase all
   execute on the same origin as the app. A CSP with explicit script-src would cut a large XSS blast
   radius; `Referrer-Policy` and `X-Content-Type-Options` are trivially addable.
5. **Beta infrastructure is selected purely by hostname and is not origin-restricted.**
   `window.location.hostname !== "townplanmap.com"` → the app talks to `beta-tileserver-…run.app`.
   Hosting a copy of the app on any other domain silently points production user traffic at staging.
6. **Third-party API keys have no visible origin/referrer restriction path in the app layer.**
   Two Google Maps keys, a Firebase Web API key, a Mappls key, a Mixpanel token and public AdSense,
   Meta Pixel and GTM ids are shipped in the bundle. Confirm each is restricted at the provider
   (HTTP referrer / Android package + signing cert), particularly the two `AIza…` Maps keys.
7. **Session replay is enabled.** Mixpanel session recording on a map app that displays parcel-level
   land data is a privacy consideration worth an explicit decision and a disclosure.
8. **Positive:** tile delivery is properly tokenised, tokens are short-lived and refreshed on app
   foreground, retries are bounded, and bearer headers are scoped to first-party hosts only.

## 9. Unknowns and limitations

- **Static analysis only.** REA parsed the bundles as inert text; no bundle bootstrap code was
  executed. Nothing here proves runtime behaviour, request order, or which branches are live.
- **No server-side visibility.** Cloud Run services (`generate-token`, `landbnk-payment-verification`,
  `tileserver`), GeoServer, the S3 buckets and the Firebase project were not inspected. Every claim
  about their behaviour is inference from client call sites.
- **No dynamic observation.** REA's browser-capture tools require the MCP server, which is not
  registered for this client. Network traces, console output and rendered behaviour are unverified.
- **The 220 MB evidence record** (`ev_27e939…`) was produced with default detail; 79,414 semantic
  unknowns are retained. Paging was not performed, so coverage of individual modules is partial
  (`coverage.status: "partial"`).
- **3 inline scripts in `index.html`** (lines 41, 51, 76 — GTM, Meta Pixel, and one more) were not
  analysed, per REA's own limitation note. Their endpoints and storage use are absent from the graph.
- **Lazy chunks were analysed by direct string inspection, not by REA's graph.** I downloaded all 85
  and read them, but the graph above covers `main`/`runtime`/`polyfills` only.
- **Point-in-time snapshot.** `main.js` as of `2026-09-22`; conclusions may not hold for the current
  deployment.

## 10. Reproducing this

```powershell
# 1. capture
curl.exe -o index.html https://townplanmap.com/
curl.exe -O https://townplanmap.com/main.111eb75d1b2da677.js
# 2. enumerate lazy chunks from runtime.js's f.u map, download each
# 3. static analysis
npx -y rea-agents@6.5.0 analyze-javascript-application <dir> --json > out.json
# 4. read the record (PowerShell redirection writes UTF-16; decode accordingly)
node -e "const b=require('fs').readFileSync('out.json');const r=b[0]===0xff?b.toString('utf16le',2):b.toString();console.log(JSON.parse(r.slice(r.indexOf('{'))).normalized_result.statistics)"
```
