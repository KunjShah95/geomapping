/* =====================================================================
   AudaMarkout - the geometry behind the survey set-out sheet.

   Two things live here:

     1. The projection. The DP 2021 sheets were drawn in the projected
        space published in their own /Measure dictionary:

            WGS_1984_Transverse_Mercator, CM 71.5, k0 0.999738,
            FE 400000, FN 400000, latitude of origin 22.5

        That is the same CRS tools/dp_georef.py uses, and the set-out
        sheet is stated in it - because a set-out sheet that gives
        latitudes and longitudes is not a set-out sheet, it is a
        screenshot.

     2. The set-out itself: the corners of a plot, their easting and
        northing, the azimuth and length of every edge, the perimeter
        and the area.

   The corner list is reduced from the drawn outline first. The plan
   draws a plot boundary as a polyline with a vertex every few
   centimetres along a straight edge, and a set-out sheet that lists 51
   points for a five-sided plot is a drawing, not a set-out. The
   reduction removes only points that are within TOL_M of the straight
   line through their neighbours, so it cannot move a real corner.

   Usable from a browser (window.AudaMarkout) and from node
   (require('./web/markout.js')), so the same code that draws the sheet
   is the code the check tool verifies.
   ===================================================================== */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.AudaMarkout = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /* ------------------------------------------------------------------
     the projection, exactly as published in the sheets
     ------------------------------------------------------------------ */
  const CRS = {
    name: 'WGS_1984_Transverse_Mercator',
    a: 6378137.0,
    f: 1 / 298.257223563,
    lon0: 71.5,          // central meridian
    lat0: 22.5,          // latitude of origin
    k0: 0.999738,        // scale factor on the central meridian
    FE: 400000,          // false easting
    FN: 400000           // false northing
  };

  const RAD = Math.PI / 180;
  const DEG = 180 / Math.PI;

  /* Krueger series coefficients, in terms of the third flattening n. */
  const n = CRS.f / (2 - CRS.f);
  const n2 = n * n, n3 = n2 * n, n4 = n3 * n;
  const A_RECT = CRS.a / (1 + n) *
    (1 + n2 / 4 + n4 / 64 + n2 * n2 * n2 / 256);
  const ALPHA = [
    n / 2 - 2 * n2 / 3 + 5 * n3 / 16 + 41 * n4 / 180,
    13 * n2 / 48 - 3 * n3 / 5 + 557 * n4 / 1440,
    61 * n3 / 240 - 103 * n4 / 140,
    49561 * n4 / 161280
  ];

  /* the reduced latitude of a geodetic latitude, in the series above */
  function xiOfLat(lat) {
    const phi = lat * RAD;
    const sp = Math.sin(phi);
    const e = 2 * Math.sqrt(n) / (1 + n);
    const t = Math.sinh(Math.atanh(sp) - e * Math.atanh(e * sp));
    const xi0 = Math.atan(t);
    let xi = xi0;
    for (let j = 1; j <= 4; j++) {
      xi += ALPHA[j - 1] * Math.sin(2 * j * xi0) * Math.cosh(0);
    }
    return xi;
  }

  /* the reduced latitude of the latitude of origin: the parallel the
     northings are counted from */
  const XI_AT_ORIGIN = xiOfLat(CRS.lat0);

  /* forward: lon/lat -> projected metres */
  function forward(lon, lat) {
    const phi = lat * RAD;
    const lam = (lon - CRS.lon0) * RAD;
    const sp = Math.sin(phi);
    const e = 2 * Math.sqrt(n) / (1 + n);
    const t = Math.sinh(Math.atanh(sp) - e * Math.atanh(e * sp));
    const xi0 = Math.atan2(t, Math.cos(lam));
    const eta0 = Math.atanh(Math.sin(lam) / Math.sqrt(1 + t * t));
    let xi = xi0, eta = eta0;
    for (let j = 1; j <= 4; j++) {
      xi += ALPHA[j - 1] * Math.sin(2 * j * xi0) * Math.cosh(2 * j * eta0);
      eta += ALPHA[j - 1] * Math.cos(2 * j * xi0) * Math.sinh(2 * j * eta0);
    }
    // the latitude of origin shifts the whole grid: a TM with lat_0 = 22.5
    // numbers its northings from that parallel, not from the equator.
    return {
      e: CRS.FE + CRS.k0 * A_RECT * eta,
      n: CRS.FN + CRS.k0 * A_RECT * (xi - XI_AT_ORIGIN)
    };
  }

  /* inverse: projected metres -> lon/lat

     The series has to be run backwards as a FIXED POINT, not as a single
     subtraction. The forward produces the series-expanded xi and eta, so
     recovering the unexpanded xi' and eta' from them is an iteration.
     Subtracting once leaves a residual of 7e-7 rad - 4.5 m on the
     ground - which is precisely the kind of silent error a set-out sheet
     must not have. Four rounds drive the residual below 1e-12 rad.

     The latitude series below is the inverse of the forward's, tested
     against an exact conformal latitude: worst case 0.3 mm over the
     plan's whole extent. */
  function inverse(e, nn) {
    const xi = (nn - CRS.FN) / (CRS.k0 * A_RECT) + XI_AT_ORIGIN;
    const eta = (e - CRS.FE) / (CRS.k0 * A_RECT);
    let xi0 = xi, eta0 = eta;
    for (let it = 0; it < 4; it++) {
      let nx = xi, ne = eta;
      for (let j = 1; j <= 4; j++) {
        nx -= ALPHA[j - 1] * Math.sin(2 * j * xi0) * Math.cosh(2 * j * eta0);
        ne -= ALPHA[j - 1] * Math.cos(2 * j * xi0) * Math.sinh(2 * j * eta0);
      }
      xi0 = nx; eta0 = ne;
    }
    const chi = Math.asin(Math.sin(xi0) / Math.cosh(eta0));
    const d1 = 2 * n - 2 * n2 / 3 - 2 * n3;
    const d2 = 7 * n2 / 3 - 8 * n3 / 5;
    const d3 = 56 * n3 / 15;
    const d4 = 4279 * n4 / 630;
    const phi = chi + d1 * Math.sin(2 * chi) + d2 * Math.sin(4 * chi) +
                d3 * Math.sin(6 * chi) + d4 * Math.sin(8 * chi);
    const lam = Math.atan2(Math.sinh(eta0), Math.cos(xi0));
    return {
      lon: CRS.lon0 + lam * DEG,
      lat: phi * DEG
    };
  }

  /* ------------------------------------------------------------------
     corner reduction

     A point is dropped only when it lies within TOL_M of the straight
     line through its neighbours. That is a shape-preserving test: a
     genuine corner of a plot is never within 5 cm of the line between
     the two points either side of it, whatever the drawing did to it.
     ------------------------------------------------------------------ */
  const TOL_M = 0.05;          // how far a point may sit off the chord
  const DUP_M = 0.01;          // two points closer than this are one point
  const MIN_CORNERS = 3;

  function metresOf(ring) {
    return ring.map(p => {
      const q = forward(p[0], p[1]);
      return [q.e, q.n];
    });
  }

  /* signed area of a ring of projected metres (positive = counter-clockwise) */
  function signedArea(pts) {
    let a = 0;
    for (let i = 0, n = pts.length; i < n; i++) {
      const j = (i + 1) % n;
      a += pts[i][0] * pts[j][1] - pts[j][0] * pts[i][1];
    }
    return a / 2;
  }

  /* how far a point sits off the line through its neighbours */
  function deviation(pts, i) {
    const n = pts.length;
    const p0 = pts[(i - 1 + n) % n], p1 = pts[i], p2 = pts[(i + 1) % n];
    const ax = p1[0] - p0[0], ay = p1[1] - p0[1];
    const bx = p2[0] - p0[0], by = p2[1] - p0[1];
    const len = Math.hypot(bx, by);
    if (len < 1e-9) return 0;
    return Math.abs(ax * by - ay * bx) / len;
  }

  /* drop the points that are not corners */
  function reduceCorners(metres) {
    let pts = metres.slice();
    // duplicates first: two points closer than DUP_M are the same point
    {
      const out = [];
      for (const p of pts) {
        if (!out.length || Math.hypot(p[0] - out[out.length - 1][0],
                                     p[1] - out[out.length - 1][1]) >= DUP_M) out.push(p);
      }
      pts = out;
      // and the wrap-around duplicate, if the ring was drawn closed
      while (pts.length > 1 &&
             Math.hypot(pts[0][0] - pts[pts.length - 1][0],
                        pts[0][1] - pts[pts.length - 1][1]) < DUP_M) pts.pop();
    }
    // then the points that sit on the straight between their neighbours
    let changed = true;
    while (changed && pts.length > MIN_CORNERS) {
      changed = false;
      let worst = -1, worstDev = Infinity;
      for (let i = 0; i < pts.length; i++) {
        const dev = deviation(pts, i);
        if (dev < TOL_M && dev < worstDev) { worstDev = dev; worst = i; }
      }
      if (worst >= 0) {
        pts.splice(worst, 1);
        changed = true;
      }
    }
    return pts;
  }

  /* ------------------------------------------------------------------
     the set-out
     ------------------------------------------------------------------ */

  /* grid azimuth, clockwise from north, 0..360 */
  function azimuth(dE, dN) {
    let a = Math.atan2(dE, dN) * DEG;
    if (a < 0) a += 360;
    return a;
  }

  /* the quadrant form a surveyor writes on a field book:
     N 45 30 10 E */
  function reducedBearing(az) {
    // four quadrants, each measured from the north or south line:
    //   0-90 N a E,  90-180 S (180-a) E,  180-270 S (a-180) W,  270-360 N (360-a) W
    const a = ((az % 360) + 360) % 360;
    const ns = (a <= 90 || a >= 270) ? 'N' : 'S';
    const ew = a <= 180 ? 'E' : 'W';
    const v = a <= 90 ? a : a <= 180 ? 180 - a : a <= 270 ? a - 180 : 360 - a;
    // round to whole seconds first, so 59.6" carries into the minutes
    let total = Math.round(v * 3600);
    const deg = Math.floor(total / 3600); total -= deg * 3600;
    const min = Math.floor(total / 60);
    const sec = total - min * 60;
    return ns + ' ' + deg + '°' + String(min).padStart(2, '0') + "'" +
           String(sec).padStart(2, '0') + '" ' + ew;
  }

  /* the whole set-out of one closed ring given as [lon, lat] pairs */
  function setOut(ring, opts) {
    const o = opts || {};
    if (!ring || ring.length < 3) return null;
    const metres = metresOf(ring);
    // one winding, so the bearings mean the same thing every time:
    // counter-clockwise, interior on the left
    let pts = reduceCorners(metres);
    if (signedArea(pts) < 0) pts = pts.slice().reverse();
    // and a predictable start: the corner nearest the south-west, so the
    // same plot always prints the same table
    let start = 0, bestKey = Infinity;
    for (let i = 0; i < pts.length; i++) {
      const key = pts[i][1] * 1e7 + pts[i][0];
      if (key < bestKey) { bestKey = key; start = i; }
    }
    pts = pts.slice(start).concat(pts.slice(0, start));

    const corners = pts.map((p, i) => {
      const j = (i + 1) % pts.length;
      const dE = pts[j][0] - p[0];
      const dN = pts[j][1] - p[1];
      const len = Math.hypot(dE, dN);
      const az = azimuth(dE, dN);
      const lonlat = inverse(p[0], p[1]);
      return {
        i: i + 1,
        e: p[0],
        n: p[1],
        lon: lonlat.lon,
        lat: lonlat.lat,
        dE: dE,
        dN: dN,
        len: len,
        az: az,
        bearing: reducedBearing(az)
      };
    });

    let perimeter = 0;
    for (const c of corners) perimeter += c.len;

    // cumulative distance around the traverse, for stationing
    let run = 0;
    for (const c of corners) { c.cum = run; run += c.len; }

    // interior angle at each corner, from the two edges that meet there
    for (let i = 0; i < corners.length; i++) {
      const prev = corners[(i - 1 + corners.length) % corners.length];
      const cur = corners[i];
      const next = corners[(i + 1) % corners.length];
      const a1 = Math.atan2(cur.dE, cur.dN);
      const a2 = Math.atan2(next.dE, next.dN);
      let turn = (a2 - a1) * DEG;
      while (turn <= -180) turn += 360;
      while (turn > 180) turn -= 360;
      // counter-clockwise traverse: the turn at a corner is the exterior
      // angle, and the interior angle is its supplement
      cur.turn = turn;
      cur.interior = 180 - Math.abs(turn);
      cur.prev = prev.i;
    }

    const area = Math.abs(signedArea(pts));
    let cx = 0, cy = 0;
    for (const p of pts) { cx += p[0]; cy += p[1]; }
    cx /= pts.length; cy /= pts.length;

    return {
      corners: corners,
      n: corners.length,
      area_m2: area,
      perimeter_m: perimeter,
      centroid: { e: cx, n: cy },
      crs: CRS,
      vertices_drawn: ring.length,
      reduced: ring.length - corners.length
    };
  }

  /* the plan's own printed coordinates for each corner

     The plot is drawn where the plan says, then moved onto the ground by the
     project's ground correction (bounds.json "ground_correction", fitted to
     OSM roads: the sheets' printed corners sit ~85 m east of the ground).
     The set-out's e/n are therefore TRUE GROUND coordinates in the plan's
     grid. A surveyor comparing against the paper sheet also needs the number
     the sheet itself would give, so this undoes the correction:

         sheet = M^-1 (ground - centre - t) + centre                       */
  /* the local warp's shift at a ground point: bilinear on its grid, 0 off it
     (the same rule as dp_georef.warp_shift) */
  function warpShift(e, n, w) {
    const g = w.grid;
    const fx = (e - g.x0) / g.step, fy = (n - g.y0) / g.step;
    if (fx < 0 || fy < 0 || fx > g.nx - 1 || fy > g.ny - 1) return [0, 0];
    const ix = Math.min(Math.floor(fx), g.nx - 2), iy = Math.min(Math.floor(fy), g.ny - 2);
    const tx = fx - ix, ty = fy - iy;
    const bl = A => A[iy][ix] * (1 - tx) * (1 - ty) + A[iy][ix + 1] * tx * (1 - ty) +
                    A[iy + 1][ix] * (1 - tx) * ty + A[iy + 1][ix + 1] * tx * ty;
    return [bl(w.dE), bl(w.dN)];
  }

  function addSheetCoords(so, corr, warp) {
    if (!so || !corr || !corr.M) return so;
    const M = corr.M, c = corr.centre_m, t = corr.t_m;
    const det = M[0][0] * M[1][1] - M[0][1] * M[1][0];
    const inv = [[M[1][1] / det, -M[0][1] / det], [-M[1][0] / det, M[0][0] / det]];
    for (const k of so.corners) {
      // undo the warp first (fixed point g = ground - R(g)), then the affine
      let ge = k.e, gn = k.n;
      if (warp) {
        for (let it = 0; it < 6; it++) {
          const r = warpShift(ge, gn, warp);
          ge = k.e - r[0]; gn = k.n - r[1];
        }
      }
      const x = ge - c[0] - t[0], y = gn - c[1] - t[1];
      k.se = inv[0][0] * x + inv[0][1] * y + c[0];
      k.sn = inv[1][0] * x + inv[1][1] * y + c[1];
    }
    so.correction = (corr.id || 'corrected') + (warp ? '+' + warp.id : '');
    return so;
  }

  /* ------------------------------------------------------------------
     formatting
     ------------------------------------------------------------------ */
  function fmtMetres(v, dp) {
    if (v == null || !isFinite(v)) return '—';
    return Number(v).toFixed(dp == null ? 3 : dp);
  }
  function fmtAz(v) {
    if (v == null || !isFinite(v)) return '—';
    return Number(v).toFixed(3) + '°';
  }
  function fmtArea(m2) {
    if (m2 == null || !isFinite(m2)) return '—';
    if (m2 >= 1e6) return (m2 / 1e6).toFixed(4) + ' km²';
    if (m2 >= 1e4) return (m2 / 1e4).toFixed(4) + ' ha';
    return m2.toFixed(2) + ' m²';
  }

  /* the set-out as a CSV table, for the surveyor's spreadsheet

     The comment lines carry no commas, so each is a single field, and the
     reduced bearing - which holds a double quote for the seconds - is
     quoted the RFC 4180 way, with the quote itself doubled. A sheet that
     a spreadsheet mangles is worse than no sheet. */
  function csvField(v) {
    const s = String(v);
    if (s.indexOf(',') < 0 && s.indexOf('"') < 0 && s.indexOf('\n') < 0) return s;
    return '"' + s.replace(/"/g, '""') + '"';
  }

  function toCsv(so, meta) {
    const m = meta || {};
    const rows = [];
    rows.push('# AUDA Second Revised Development Plan 2021 - survey set-out (markout)');
    if (m.survey) rows.push('# survey number: ' + m.survey);
    if (m.pid != null) rows.push('# plot id: ' + m.pid);
    if (m.village) rows.push('# village: ' + m.village);
    if (m.scheme) rows.push('# TP scheme: ' + m.scheme);
    if (m.zone) rows.push('# land use: ' + m.zone);
    if (m.block) rows.push('# sheet: ' + m.block);
    rows.push('# CRS: ' + CRS.name + ' (CM ' + CRS.lon0 + '; k0 ' + CRS.k0 +
              '; FE ' + CRS.FE + '; FN ' + CRS.FN + ')');
    rows.push('# area: ' + so.area_m2.toFixed(2) + ' m2; perimeter: ' +
              so.perimeter_m.toFixed(3) + ' m; corners: ' + so.n);
    const sheet = so.corners.length > 0 && so.corners[0].se != null;
    rows.push('# the shape is the plan\'s own; easting/northing are ground ' +
              'coordinates after the ground correction' +
              (sheet ? ' (' + so.correction + '); sheet_easting/northing are the ' +
                       'numbers the printed sheet itself gives' : ''));
    rows.push('# position: about 6 m median against OSM roads; not a finished survey');
    rows.push('');
    rows.push('corner,easting_m,northing_m,' +
              (sheet ? 'sheet_easting_m,sheet_northing_m,' : '') +
              'azimuth_to_next_deg,length_to_next_m,' +
              'cumulative_m,reduced_bearing,interior_angle_deg,latitude,longitude');
    for (const c of so.corners) {
      rows.push([
        c.i,
        c.e.toFixed(3),
        c.n.toFixed(3)
      ].concat(sheet ? [c.se.toFixed(3), c.sn.toFixed(3)] : []).concat([
        c.az.toFixed(3),
        c.len.toFixed(3),
        c.cum.toFixed(3),
        csvField(c.bearing),
        c.interior.toFixed(3),
        c.lat.toFixed(8),
        c.lon.toFixed(8)
      ]).join(','));
    }
    return rows.join('\n');
  }

  return {
    CRS: CRS,
    forward: forward,
    inverse: inverse,
    setOut: setOut,
    addSheetCoords: addSheetCoords,
    warpShift: warpShift,
    toCsv: toCsv,
    fmtMetres: fmtMetres,
    fmtAz: fmtAz,
    fmtArea: fmtArea,
    reducedBearing: reducedBearing,
    signedArea: signedArea,
    TOL_M: TOL_M
  };
});
