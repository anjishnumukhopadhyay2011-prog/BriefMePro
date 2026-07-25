/* ── Pure utility functions ──────────────────────────── */
/* ─── Utilities ─────────────────────────────────────────────────────────── */
function clamp(v, lo = 0, hi = 100) { return Math.max(lo, Math.min(hi, v)); }

function escapeHTML(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function colorWithAlpha(hex, alpha) {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha.toFixed(3)})`;
}

function formatNumber(n) {
  const v = Number(n);
  return Number.isFinite(v) ? String(Math.round(v)) : "—";
}

function formatLocation(e) {
  return [e.location_name, e.region, e.country].filter(Boolean).join(", ") || "Unknown location";
}

function sourceLabel(e) {
  if (Array.isArray(e.source_refs) && e.source_refs.length) return e.source_refs[0]?.source_name || e.source_refs[0]?.external_id || "";
  return e.source_name || "Unknown source";
}

function hoursFromNow(v) {
  if (!v) return 0;
  return (Date.now() - new Date(v).getTime()) / 3600000;
}

function ageHours(v) {
  if (!v) return Infinity;
  const t = new Date(v).getTime();
  if (!isFinite(t)) return Infinity;
  return Math.max((Date.now() - t) / 3600000, 0);
}

function relativeAge(v) {
  const h = ageHours(v);
  if (!isFinite(h)) return "";
  if (h < 1) return `${Math.round(h * 60)}m ago`;
  if (h < 24) return `${Math.round(h)}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

function manifestationPhase(e) {
  if (e.predicted) return { label: "Forecast", color: "#ffd16d", descriptor: "AI scenario built from current signals" };
  const h = Math.abs(hoursFromNow(e.happened_at));
  const s = Number(e.severity || 0);
  if (h < 2)  return s >= 68
    ? { label: "Breaking",   color: "#ff8d77", descriptor: "Story accelerating right now" }
    : { label: "Emerging",   color: "#76d5ff", descriptor: "New signal still taking shape" };
  if (h < 16) return   { label: "Developing", color: "#55efc2", descriptor: "Effects spreading through nearby systems" };
  return               { label: "Ongoing",    color: "#87c9ff", descriptor: "Story remains active" };
}

function verificationState(e) {
  const srcs = Array.isArray(e.source_refs) ? e.source_refs.length : e.source_name ? 1 : 0;
  const conf = Number(e.confidence || e.probability || 0.5) * 100;
  if (conf >= 80 || srcs >= 3) return { label: "Verified",     color: "#55efc2", detail: "Multiple corroborating sources" };
  if (conf >= 60 || srcs >= 2) return { label: "Corroborated", color: "#76d5ff", detail: "Two or more sources" };
  if (conf >= 40 || srcs >= 1) return { label: "Emerging",     color: "#ffd16d", detail: "Single source" };
  return                              { label: "Unconfirmed",  color: "#ff6c7e", detail: "No source confirmed" };
}

function futureStrength() {
  return clamp((state.timelineValue - 46) / 54, 0, 1);
}

function hotlineLinks() {
  return Array.isArray(state.simulation?.twin?.hotlines) ? state.simulation.twin.hotlines : [];
}

function regionPressures() {
  const r = state.simulation?.twin?.regions;
  if (Array.isArray(r) && r.length) return r;
  return deriveFallbackRegions(state.observedEvents);
}

function systemPressures() {
  const s = state.simulation?.twin?.system_pressures;
  if (Array.isArray(s) && s.length) return s;
  return deriveFallbackSystems(state.observedEvents);
}

function deriveFallbackRegions(events) {
  const buckets = new Map();
  for (const e of events) {
    const key = formatLocation(e) + ":" + (e.category || "other");
    const b = buckets.get(key) || { region: formatLocation(e), dominant_category: e.category || "other", count: 0, score: 0, avg_severity: 0, latitude: e.latitude, longitude: e.longitude, latest_at: e.happened_at };
    b.count++;
    b.score += Number(e.severity || 0) * 0.58 + Number(e.urgency || 0) * 0.22;
    b.avg_severity += Number(e.severity || 0);
    buckets.set(key, b);
  }
  return [...buckets.values()].map(b => ({ ...b, avg_severity: b.count ? b.avg_severity / b.count : 0, score: clamp(b.score / Math.max(b.count, 1) + Math.min(b.count * 8, 24), 0, 100) })).sort((a, b) => b.score - a.score).slice(0, 6);
}

function deriveFallbackSystems(events) {
  const buckets = new Map();
  for (const e of events) {
    const sys = systemForCategory(e.category || "other");
    const b = buckets.get(sys) || { system: sys, label: metaForSystem(sys).label, dominant_category: e.category || "other", count: 0, intensity: 0 };
    b.count++;
    b.intensity += Number(e.severity || 0) * 0.64 + Number(e.urgency || 0) * 0.18;
    buckets.set(sys, b);
  }
  return [...buckets.values()].map(b => ({ ...b, intensity: clamp(b.intensity / Math.max(b.count, 1) + Math.min(b.count * 4, 18), 0, 100) })).sort((a, b) => b.intensity - a.intensity);
}

function pickDefault(pool = state.events) {
  const candidates = pool.filter(e => !e.predicted);
  return [...(candidates.length ? candidates : pool)].sort((a, b) => {
    const sa = Number(a.severity||0)*0.6 + Number(a.urgency||0)*0.2;
    const sb = Number(b.severity||0)*0.6 + Number(b.urgency||0)*0.2;
    return sb - sa;
  })[0] || null;
}

function syncDisplayEvents() {
  state.events = [...state.observedEvents, ...state.predictedEvents].sort((a, b) => {
    if (Boolean(a.predicted) !== Boolean(b.predicted)) return a.predicted ? 1 : -1;
    const fa = clamp(100 - ageHours(a.happened_at) * 5.4, 0, 100);
    const fb = clamp(100 - ageHours(b.happened_at) * 5.4, 0, 100);
    const wa = Number(a.severity||0)*0.38 + Number(a.urgency||0)*0.2 + fa*0.3;
    const wb = Number(b.severity||0)*0.38 + Number(b.urgency||0)*0.2 + fb*0.3;
    return wb - wa;
  });
  if (state.selectedEvent) {
    const rep = state.events.find(e => e.event_id === state.selectedEvent.event_id);
    if (rep) { state.selectedEvent = rep; state.selectedEventDetail = rep; }
    else { state.selectedEvent = null; state.selectedEventDetail = null; }
  }
  if (!state.selectedEvent) {
    state.selectedEvent = pickDefault();
    state.selectedEventDetail = state.selectedEvent;
  }
}

function globeVisibleEvents() {
  const cat = state.filters.category;
  const q   = state.filters.search.toLowerCase();
  return state.events.filter(e => {
    if (e.predicted && !state.filters.includePredicted) return false;
    if (cat && (e.category || "other") !== cat) return false;
    if (state.viewScope === "native" && !isEventInNativeCountry(e)) return false;
    if (q && !(e.title || "").toLowerCase().includes(q) && !(formatLocation(e)).toLowerCase().includes(q)) return false;
    return true;
  });
}

/* ─── API helpers ───────────────────────────────────────────────────────── */
async function apiFetch(url, opts = {}) {
  try {
    const res = await fetch(url, { credentials: "same-origin", ...opts });
    const data = await res.json().catch(() => ({}));
    return { ok: res.ok, status: res.status, data };
  } catch (err) {
    return { ok: false, status: 0, data: { error: err.message } };
  }
}
function fetchJSON(url, opts = {}) {
  return apiFetch(url, opts).then(({ ok, status, data }) => {
    if (!ok) throw new Error(`Request failed: ${status}`);
    return data;
  });
}
function postJSON(url, payload) {
  return fetchJSON(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
}

