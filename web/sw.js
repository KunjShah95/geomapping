/**
 * Service worker for the plan viewer. Served at the site root (/sw.js on the
 * dev server, /<repo>/sw.js on GitHub Pages) so its scope covers tiles_dp2021
 * and data as well as the page itself.
 *
 * - Plan tiles and the per-tile data buckets never change for a given build,
 *   so they are cache-first: once seen, they work offline.
 * - The page, the scripts and the small config JSON are network-first, so a
 *   rebuild shows up on the next load, with the cached copy as a fallback.
 * - Third-party basemap tiles (OSM, Esri) are left alone: their terms do not
 *   allow bulk caching, and they are not ours to keep.
 *
 * Bump VERSION whenever tiles_dp2021 or data/dp2021 is rebuilt.
 */
const VERSION = 'dp2021-v12';
const STATIC = `${VERSION}-static`;
const TILES = `${VERSION}-tiles`;
const MAX_TILES = 4000;

// Matched anywhere in the path, not anchored at '/': the same worker runs from
// the domain root (dev server) and from /<repo>/ (GitHub Pages).
const IMMUTABLE = [
  /\/tiles_dp2021\//,
  /\/data\/dp2021\/(plots|zones|parcels|details)\//,
  /\/glyphs\//                // self-hosted label glyphs; never change
];

self.addEventListener('install', () => self.skipWaiting());

// Activation only takes control. Deleting the previous version's tile cache
// (thousands of entries) inside activate held every request of the page until
// it finished - a reload after a data release hung for a minute. The cleanup
// runs once, in the background, kept alive by whichever events come next.
self.addEventListener('activate', event => {
  event.waitUntil(self.clients.claim());
  event.waitUntil(cleanup());
});

let cleaning = null;
function cleanup() {
  if (!cleaning) {
    cleaning = (async () => {
      for (const key of await caches.keys())
        if (!key.startsWith(VERSION)) await caches.delete(key);
    })().catch(() => { cleaning = null; });
  }
  return cleaning;
}

self.addEventListener('fetch', event => {
  // never delays the response; only keeps the worker alive until cleanup ends
  event.waitUntil(cleanup());
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.endsWith('/config.js')) return;

  if (IMMUTABLE.some(re => re.test(url.pathname))) event.respondWith(cacheFirst(req));
  else event.respondWith(networkFirst(req));
});

async function cacheFirst(req) {
  const cache = await caches.open(TILES);
  const hit = await cache.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok) {
    await cache.put(req, res.clone());
    trim(cache);
  }
  return res;
}

async function networkFirst(req) {
  const cache = await caches.open(STATIC);
  try {
    const res = await fetch(req);
    if (res.ok) cache.put(req, res.clone());
    return res;
  } catch (err) {
    const hit = await cache.match(req);
    if (hit) return hit;
    throw err;
  }
}

// keep the tile cache bounded: drop the oldest entries past the cap
let trimming = false;
async function trim(cache) {
  if (trimming) return;
  trimming = true;
  try {
    const keys = await cache.keys();
    for (let i = 0; i < keys.length - MAX_TILES; i++) await cache.delete(keys[i]);
  } finally {
    trimming = false;
  }
}
