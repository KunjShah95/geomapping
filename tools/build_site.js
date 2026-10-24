#!/usr/bin/env node
/**
 * Assemble the static site that GitHub Pages publishes.
 *
 *   node tools/build_site.js                 # -> dist/
 *   node tools/build_site.js --out dist --max-tile-zoom 17
 *
 * The repository root is not publishable: it holds the client's source PDFs,
 * two superseded tile pyramids, the raw 100 MB+ GeoJSON aggregates and the
 * pipeline scratch. This copies only what the viewer actually requests, into
 * the layout web/index.html expects when it sits at the site root:
 *
 *   dist/index.html              (web/index.html, unchanged)
 *   dist/{icon.svg,sw.js,manifest.webmanifest}
 *   dist/glyphs/                 (web/glyphs)
 *   dist/tiles_dp2021/{z}/...    (the plan raster pyramid)
 *   dist/data/dp2021/...         (bounds, indexes, per-tile buckets)
 *
 * Every path inside index.html is resolved against the site root at runtime
 * (see the SITE/U block at the top of its script), so the same file works here
 * and at https://<user>.github.io/<repo>/.
 *
 * GitHub Pages caps a published site at 1 GB. `--max-tile-zoom 16` drops the
 * z17 layer (~268 MB), which is upsampled past the plan's own 1.1 m/px
 * resolution anyway; MapLibre overzooms z16 instead.
 */
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');

const arg = (name, dflt) => {
  const argv = process.argv.slice(2);
  const i = argv.indexOf(`--${name}`);
  if (i >= 0) {
    if (argv[i + 1] == null || argv[i + 1].startsWith('--'))
      throw new Error(`--${name} needs a value`);
    return argv[i + 1];
  }
  const hit = argv.find(a => a.startsWith(`--${name}=`));
  return hit ? hit.slice(name.length + 3) : dflt;
};
const KNOWN = ['out', 'max-tile-zoom', 'min-tile-zoom'];
for (const a of process.argv.slice(2)) {
  if (!a.startsWith('--')) continue;
  const name = a.replace(/^--/, '').split('=')[0];
  if (!KNOWN.includes(name)) throw new Error(`unknown option --${name}`);
}
const OUT = path.resolve(ROOT, arg('out', 'dist'));
const MAX_TILE_ZOOM = Number(arg('max-tile-zoom', '17'));
const MIN_TILE_ZOOM = Number(arg('min-tile-zoom', '11'));

/* The page shell: everything that lives beside index.html. */
const SHELL = ['index.html', 'icon.svg', 'sw.js', 'manifest.webmanifest'];

/* data/dp2021 files the viewer fetches (see the fetch(U('/data/dp2021/...'))
   calls in web/index.html). The whole-layer geojson / ndjson files are build
   intermediates and stay out. */
const DATA_FILES = [
  'bounds.json', 'legend.json', 'imagery.json',
  'labels.geojson', 'units.geojson',
  'number_index.json', 'village_index.json', 'village_units.json',
  'manifest.json', 'accuracy.json', 'label_stats.json', 'boundary_stats.json',
  'boundary_join_summary.json', 'zone_stats.json', 'record_disagreements.json'
];

/* Bucketed layers, one zoom directory each (BUCKET_Z = 15 in the viewer). */
const DATA_BUCKETS = ['plots', 'zones', 'parcels', 'details'];
const BUCKET_Z = 15;

let copied = 0;
let bytes = 0;

function put(src, dest) {
  const st = fs.statSync(src);
  if (!st.isFile()) throw new Error(`not a file: ${src}`);
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.copyFileSync(src, dest);
  copied += 1;
  bytes += st.size;
}

function copyTree(srcDir, destDir) {
  if (!fs.existsSync(srcDir)) return 0;
  fs.cpSync(srcDir, destDir, { recursive: true });
  const stack = [srcDir];
  let n = 0;
  while (stack.length) {
    const dir = stack.pop();
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) stack.push(p);
      else { n += 1; bytes += fs.statSync(p).size; }
    }
  }
  copied += n;
  return n;
}

function need(src, dest, label) {
  if (!fs.existsSync(src)) throw new Error(`missing ${label}: ${path.relative(ROOT, src)}`);
  put(src, dest);
}

fs.rmSync(OUT, { recursive: true, force: true });
fs.mkdirSync(OUT, { recursive: true });

/* GitHub Pages would otherwise run Jekyll over 100k files. */
fs.writeFileSync(path.join(OUT, '.nojekyll'), '');

for (const f of SHELL) need(path.join(ROOT, 'web', f), path.join(OUT, f), 'page asset');
console.log(`shell: ${SHELL.length} files`);

const glyphs = copyTree(path.join(ROOT, 'web', 'glyphs'), path.join(OUT, 'glyphs'));
console.log(`glyphs: ${glyphs} files`);

/* tiles_dp2021/{z}/{x}/{y}.webp — the plan raster pyramid */
const tileRoot = path.join(ROOT, 'tiles_dp2021');
if (!fs.existsSync(tileRoot)) throw new Error('missing tiles_dp2021/ (build the pyramid first)');
const zooms = fs.readdirSync(tileRoot)
  .filter(d => /^\d+$/.test(d))
  .map(Number)
  .filter(z => z >= MIN_TILE_ZOOM && z <= MAX_TILE_ZOOM)
  .sort((a, b) => a - b);
if (!zooms.length) throw new Error(`no tiles between z${MIN_TILE_ZOOM} and z${MAX_TILE_ZOOM}`);
const tileStats = {};
for (const z of zooms) {
  const startBytes = bytes;
  const n = copyTree(path.join(tileRoot, String(z)), path.join(OUT, 'tiles_dp2021', String(z)));
  tileStats[z] = { count: n, bytes: bytes - startBytes };
  console.log(`tiles z${z}: ${n.toLocaleString()} files`);
}

/* data/dp2021 — the config, the indexes, the per-tile buckets.
   Strict on purpose: a file listed here but missing means the viewer would
   404 at runtime, so the build fails instead of publishing a broken site. */
const dataSrc = path.join(ROOT, 'data', 'dp2021');
const dataDest = path.join(OUT, 'data', 'dp2021');
for (const f of DATA_FILES) need(path.join(dataSrc, f), path.join(dataDest, f), 'data file');
console.log(`data: ${DATA_FILES.length} config/index files`);
for (const layer of DATA_BUCKETS) {
  const src = path.join(dataSrc, layer, String(BUCKET_Z));
  const n = copyTree(src, path.join(dataDest, layer, String(BUCKET_Z)));
  if (!n) throw new Error(`missing data/dp2021/${layer}/${BUCKET_Z}/ (rebuild the buckets)`);
  console.log(`data ${layer} z${BUCKET_Z}: ${n.toLocaleString()} files`);
}

/* If the pyramid was trimmed, bounds.json must not advertise tiles that are
   not in the build: MapLibre would request z17 forever and get 404s. */
const boundsPath = path.join(dataDest, 'bounds.json');
const bounds = JSON.parse(fs.readFileSync(boundsPath, 'utf8'));
if (bounds.raster_source.maxzoom > MAX_TILE_ZOOM) {
  bounds.raster_source.maxzoom = MAX_TILE_ZOOM;
  bounds.tiles = {
    count: Object.values(tileStats).reduce((a, t) => a + t.count, 0),
    bytes: Object.values(tileStats).reduce((a, t) => a + t.bytes, 0),
    per_zoom: Object.fromEntries(zooms.map(z => [z, tileStats[z].count]))
  };
  if (bounds.app_config) bounds.app_config.ZoomBounds = [zooms[0], MAX_TILE_ZOOM];
  fs.writeFileSync(boundsPath, JSON.stringify(bounds, null, 2) + '\n');
  console.log(`bounds.json: maxzoom lowered to z${MAX_TILE_ZOOM}`);
}

const mb = bytes / 1e6;
console.log(`\ndist: ${OUT}`);
console.log(`  ${copied.toLocaleString()} files, ${mb.toFixed(1)} MB, tiles z${zooms[0]}-z${zooms[zooms.length - 1]}`);
if (mb > 1000) {
  console.log('  WARNING: GitHub Pages caps a published site at 1 GB; rebuild with --max-tile-zoom=16');
}
