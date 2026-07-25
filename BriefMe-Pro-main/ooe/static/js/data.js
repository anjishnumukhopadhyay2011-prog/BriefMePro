/* ── Data loading — events, trends, intelligence ──────── */
/* ─── GeoJSON loading ───────────────────────────────────────────────────── */
function normalizeBorderSegments(geojson) {
  const segs = [];
  for (const f of (geojson?.features || [])) {
    const g = f?.geometry;
    if (!g) continue;
    if (g.type === "LineString") segs.push(g.coordinates || []);
    else if (g.type === "MultiLineString") for (const l of (g.coordinates || [])) segs.push(l);
  }
  return segs
    .map(s => s.filter(p => Array.isArray(p) && p.length >= 2).map(p => [Number(p[1]), Number(p[0])]))
    .filter(s => s.length > 1);
}

async function loadCountryBorders() {
  try {
    const g = await fetchJSON("/static/countries.geojson");
    state.globe.countryPolygons = (g?.features || []).map(f => ({
      name: f?.properties?.name || "",
      rings: extractRings(f),
    })).filter(c => c.rings.length > 0);
  } catch (e) { console.error("countries.geojson", e); }
  try {
    const g = await fetchJSON("/static/us_states.geojson");
    state.globe.statePolygons = (g?.features || []).map(f => ({
      name: f?.properties?.name || "",
      rings: extractRings(f),
    })).filter(s => s.rings.length > 0);
  } catch (e) { /* optional */ }
  try {
    const g = await fetchJSON("/static/country_boundaries.geojson");
    state.globe.countryBorders = normalizeBorderSegments(g);
  } catch (e) { state.globe.countryBorders = []; }
}

/* ──────────────────────────────────────────────────────────────────────────
   GLOBE RENDERING (preserved verbatim)
   ────────────────────────────────────────────────────────────────────────── */

function ensureStars(width, height) {
  if (state.globe.stars.length && state.globe.width === width && state.globe.height === height) return;
  state.globe.width = width;
  state.globe.height = height;
  const count = Math.max(200, Math.round((width * height) / 3800));
  state.globe.stars = Array.from({ length: count }, () => ({
    x: Math.random(), y: Math.random(),
    r: Math.random() * 1.6 + 0.2,
    opacity: Math.random() * 0.55 + 0.12,
    speed: Math.random() * 0.6 + 0.2,
    phase: Math.random() * Math.PI * 2,
  }));
}

function extractRings(feature) {
  const rings = [];
  const geom = feature?.geometry;
  if (!geom) return rings;
  const polys = geom.type === "Polygon" ? [geom.coordinates] : geom.type === "MultiPolygon" ? geom.coordinates : [];
  for (const poly of polys) {
    for (const ring of poly) {
      const pts = ring.filter(p => Array.isArray(p) && p.length >= 2).map(p => [Number(p[1]), Number(p[0])]);
      if (pts.length > 1) rings.push(pts);
    }
  }
  return rings;
}

function projectRing(ring, cx, cy, radius) {
  const segments = [];
  let current = [];
  for (const [lat, lon] of ring) {
    const p = projectPoint(lat, lon, radius, state.globe.rotationY, state.globe.rotationX);
    if (p.z < -0.01) { if (current.length > 1) segments.push(current); current = []; }
    else { current.push({ sx: cx + p.x, sy: cy - p.y, z: p.z }); }
  }
  if (current.length > 1) segments.push(current);
  return segments;
}

function buildPath(segments, fill = false) {
  let any = false;
  ctx.beginPath();
  for (const seg of segments) {
    if (seg.length < 2) continue;
    ctx.moveTo(seg[0].sx, seg[0].sy);
    for (let i = 1; i < seg.length; i++) ctx.lineTo(seg[i].sx, seg[i].sy);
    if (fill) ctx.closePath();
    any = true;
  }
  return any;
}

function resizeCanvas() {
  const ratio = window.devicePixelRatio || 1;
  const rect = el.globeCanvas.getBoundingClientRect();
  el.globeCanvas.width  = Math.floor(rect.width  * ratio);
  el.globeCanvas.height = Math.floor(rect.height * ratio);
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ensureStars(rect.width, rect.height);
}

function projectPoint(lat, lon, radius, rotY, rotX) {
  const latR = (lat * Math.PI) / 180;
  const lonR = (lon * Math.PI) / 180;
  let x = Math.cos(latR) * Math.sin(lonR);
  let y = Math.sin(latR);
  let z = Math.cos(latR) * Math.cos(lonR);
  const cY = Math.cos(rotY), sY = Math.sin(rotY);
  const x1 = x * cY + z * sY; z = z * cY - x * sY; x = x1;
  const cX = Math.cos(rotX), sX = Math.sin(rotX);
  const y1 = y * cX - z * sX; z = z * cX + y * sX; y = y1;
  return { x: x * radius, y: y * radius, z };
}

