/* Check the browser's set-out code against the pyproj reference.
 *
 *   python tools/dp_markout_reference.py      (writes work/markout_reference.json)
 *   node tools/dp_check_markout.js            (compares)
 *
 * The viewer's set-out is computed in the browser, in web/markout.js. The
 * reference is computed with pyproj, in tools/dp_markout_reference.py.
 * This script re-computes the reference parcels with the browser's code
 * and compares the two.
 *
 * What it checks:
 *   - the projection: lon/lat -> the plan's own metres, to the millimetre
 *   - the corner reduction: the same corners come out of both
 *   - every corner's easting, northing, azimuth and length
 *   - the area and the perimeter
 *   - the INVERSE projection: the browser's metres -> lon/lat, against
 *     pyproj's own inverse, because those are the numbers the sheet
 *     prints and the ones the map draws the corners at
 *   - the round trip: lon/lat -> metres -> lon/lat
 *
 * A set-out sheet is a list of numbers somebody will peg a peg in the
 * ground from, so this is not a smoke test.
 */
'use strict';
const fs = require('fs');
const path = require('path');

const ROOT = path.dirname(__dirname);
const REF = path.join(ROOT, 'work', 'markout_reference.json');
const M = require(path.join(ROOT, 'web', 'markout.js'));

const TOL_POS = 0.002;   // 2 mm on an easting or northing
const TOL_LEN = 0.002;   // 2 mm on an edge length
const TOL_AZ = 0.0005;   // 0.018 arc-second on an azimuth
const TOL_AREA = 0.01;   // 1 cm2 on the area
const TOL_LL = 0.005;    // 5 mm on a corner's geographic position
const TOL_RT = 0.005;    // 5 mm on the lon/lat -> metres -> lon/lat round trip

let failures = 0;
let checks = 0;

function fail(msg) {
  failures++;
  console.log('  FAIL  ' + msg);
}

function ok(msg) {
  console.log('  ok    ' + msg);
}

function close(a, b, tol) {
  return Math.abs(a - b) <= tol;
}

/* how far apart two lon/lat pairs are, in metres */
function metresApart(lon1, lat1, lon2, lat2) {
  const midLat = (lat1 + lat2) / 2 * Math.PI / 180;
  const dx = (lon2 - lon1) * 111320 * Math.cos(midLat);
  const dy = (lat2 - lat1) * 110574;
  return Math.hypot(dx, dy);
}

function main() {
  const refPath = process.argv[2] ? path.resolve(process.argv[2]) : REF;
  if (!fs.existsSync(refPath)) {
    console.error('no reference found: ' + refPath);
    console.error('run: python tools/dp_markout_reference.py');
    process.exit(2);
  }
  const ref = JSON.parse(fs.readFileSync(refPath, 'utf8'));
  console.log('reference: ' + path.relative(ROOT, refPath) +
              '  (' + ref.parcels.length + ' parcels, tolerance ' +
              ref.tolerance_m + ' m)');
  console.log('');

  let maxPos = 0, maxLen = 0, maxAz = 0, maxArea = 0;
  let maxLL = 0, maxRT = 0;
  let totalCorners = 0, totalDrawn = 0;

  for (const p of ref.parcels) {
    const r = p.reference;
    const so = M.setOut(p.ring);
    const label = 'pid ' + (p.props && p.props.pid);

    if (!so) { fail(label + ': setOut returned nothing'); continue; }

    if (so.n !== r.n) {
      fail(label + ': corner count ' + so.n + ' vs reference ' + r.n);
      continue;
    }
    if (so.vertices_drawn !== r.vertices_drawn) {
      fail(label + ': vertex count ' + so.vertices_drawn + ' vs reference ' + r.vertices_drawn);
      continue;
    }

    let worst = null;
    for (let i = 0; i < so.corners.length; i++) {
      const c = so.corners[i], q = r.corners[i];
      const dPos = Math.max(Math.abs(c.e - q.e), Math.abs(c.n - q.n));
      const dLen = Math.abs(c.len - q.len);
      let dAz = Math.abs(c.az - q.az);
      if (dAz > 180) dAz = 360 - dAz;
      maxPos = Math.max(maxPos, dPos);
      maxLen = Math.max(maxLen, dLen);
      maxAz = Math.max(maxAz, dAz);
      totalCorners++;
      if (!worst || dPos > worst.dPos) worst = { i: i + 1, dPos: dPos, dLen: dLen, dAz: dAz };
      if (dPos > TOL_POS) fail(label + ' corner ' + (i + 1) + ': position off by ' + dPos.toFixed(4) + ' m');
      if (dLen > TOL_LEN) fail(label + ' corner ' + (i + 1) + ': length off by ' + dLen.toFixed(4) + ' m');
      if (dAz > TOL_AZ) fail(label + ' corner ' + (i + 1) + ': azimuth off by ' + dAz.toFixed(5) + ' deg');

      // the corner's geographic position, against pyproj's own inverse
      if (q.lon != null && q.lat != null) {
        const dLL = metresApart(c.lon, c.lat, q.lon, q.lat);
        maxLL = Math.max(maxLL, dLL);
        if (dLL > TOL_LL) {
          fail(label + ' corner ' + (i + 1) + ': lon/lat off by ' + dLL.toFixed(4) + ' m');
        }
      }
    }

    const dArea = Math.abs(so.area_m2 - r.area_m2);
    maxArea = Math.max(maxArea, dArea);
    totalDrawn += r.vertices_drawn;
    if (dArea > TOL_AREA) fail(label + ': area off by ' + dArea.toFixed(4) + ' m2');
    if (Math.abs(so.perimeter_m - r.perimeter_m) > TOL_LEN * r.n) {
      fail(label + ': perimeter off by ' + Math.abs(so.perimeter_m - r.perimeter_m).toFixed(4) + ' m');
    }

    // the area the plan itself recorded for the plot, for reference
    const stored = p.props && p.props.area_m2;
    if (stored != null) {
      const rel = Math.abs(so.area_m2 - stored) / stored * 100;
      if (rel > 0.5) {
        fail(label + ': set-out area ' + so.area_m2.toFixed(1) +
             ' m2 differs from the plan\'s own ' + stored.toFixed(1) + ' m2 by ' + rel.toFixed(2) + '%');
      }
    }
    checks++;
  }

  /* ---- the projection's round trip ----
     lon/lat -> the plan's metres -> lon/lat, over the plan's whole
     extent. The inverse has to be the inverse of the forward: an
     inverse that is 4 m out would print a set-out sheet that looks
     perfect and is wrong. */
  const RT = [
    [72.10, 22.70], [72.90, 22.70], [72.10, 23.40], [72.90, 23.40],
    [71.50, 22.50], [72.50, 23.00], [72.17, 22.76], [72.87, 23.36]
  ];
  for (const [lon, lat] of RT) {
    const p = M.forward(lon, lat);
    const q = M.inverse(p.e, p.n);
    const d = metresApart(lon, lat, q.lon, q.lat);
    maxRT = Math.max(maxRT, d);
    if (d > TOL_RT) {
      fail('round trip ' + lon + ',' + lat + ': off by ' + d.toFixed(4) + ' m');
    }
  }

  /* ---- reduced bearings ----
     The quadrant form is what goes in the field book. One letter wrong
     sends the chain the wrong way, so every quadrant and the carries at
     its edges are pinned. */
  const BEARINGS = [
    [0, "N 0°00'00\" E"], [45.5, "N 45°30'00\" E"], [90, "N 90°00'00\" E"],
    [135.25, "S 44°45'00\" E"], [180, "S 0°00'00\" E"], [225, "S 45°00'00\" W"],
    [269.606, "S 89°36'22\" W"], [270, "N 90°00'00\" W"], [359.606, "N 0°23'38\" W"],
    [10.99999, "N 11°00'00\" E"], [360, "N 0°00'00\" E"]
  ];
  for (const [az, want] of BEARINGS) {
    const got = M.reducedBearing(az);
    if (got !== want) fail('reduced bearing of ' + az + ': ' + got + ' (want ' + want + ')');
  }

  /* ---- the plan's own printed coordinates ----
     addSheetCoords undoes the ground correction. Applying the correction
     forward to the sheet coordinate must give back the ground one. */
  const corr = { id: 'check', centre_m: [509623.17, 463587.621],
                 M: [[1.0001, -0.00027], [-0.00132, 1.00067]], t_m: [-84.94, -23.78] };
  const sq = M.setOut([[72.507, 23.0257], [72.5075, 23.0257], [72.5075, 23.0262],
                       [72.507, 23.0262], [72.507, 23.0257]]);
  M.addSheetCoords(sq, corr);
  for (const c of sq.corners) {
    const x = c.se - corr.centre_m[0], y = c.sn - corr.centre_m[1];
    const ge = corr.M[0][0] * x + corr.M[0][1] * y + corr.centre_m[0] + corr.t_m[0];
    const gn = corr.M[1][0] * x + corr.M[1][1] * y + corr.centre_m[1] + corr.t_m[1];
    if (!close(ge, c.e, 1e-6) || !close(gn, c.n, 1e-6)) {
      fail('sheet coordinate of corner ' + c.i + ' does not map back to the ground');
    }
  }

  console.log('');
  console.log('parcels checked      ' + checks);
  console.log('corners checked      ' + totalCorners + '  (from ' + totalDrawn + ' drawn vertices)');
  console.log('worst position       ' + maxPos.toFixed(6) + ' m   (tolerance ' + TOL_POS + ')');
  console.log('worst edge length    ' + maxLen.toFixed(6) + ' m   (tolerance ' + TOL_LEN + ')');
  console.log('worst azimuth        ' + maxAz.toFixed(6) + ' deg (tolerance ' + TOL_AZ + ')');
  console.log('worst area           ' + maxArea.toFixed(6) + ' m2  (tolerance ' + TOL_AREA + ')');
  console.log('worst lon/lat        ' + maxLL.toFixed(6) + ' m   (tolerance ' + TOL_LL + ')');
  console.log('worst round trip     ' + maxRT.toFixed(6) + ' m   (tolerance ' + TOL_RT + ')');
  console.log('');

  if (failures) {
    console.log('FAILED: ' + failures + ' problem(s)');
    process.exit(1);
  }
  console.log('PASSED');
}

main();
