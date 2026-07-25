/* ── Backend API calls, connection badge, retry logic ── */
/* ─── Badge & retry ─────────────────────────────────────────────────────── */
function setServerBadge(text, tone = "neutral") {
  el.serverBadge.textContent = text;
  el.serverBadge.style.borderColor =
    tone === "ok"    ? "rgba(76,215,160,0.40)" :
    tone === "error" ? "rgba(255,107,122,0.40)" :
                       "rgba(78,207,255,0.18)";
}

function handleLoadError(err) {
  failCount++;
  const recent = Date.now() - lastSyncAt < 300000;
  setServerBadge(recent ? "Reconnecting" : "Offline", recent ? "neutral" : "error");
  console.error(err);
  if (!retryTimer) {
    const delay = Math.min(30000, 1400 * 2 ** Math.min(failCount, 5));
    retryTimer = setTimeout(() => { retryTimer = null; loadData().catch(handleLoadError); }, delay);
  }
}

/* ─── Data loading ──────────────────────────────────────────────────────── */
async function loadData({ force = false } = {}) {
  if (loadPromise) return loadPromise;
  loadPromise = (async () => {
    const qs = new URLSearchParams({ include_predicted: "0", limit: "240" });
    if (state.filters.severity_min) qs.set("severity_min", String(state.filters.severity_min));
    const [globeData, metricsData] = await Promise.all([
      fetchJSON(`/api/globe?${qs}`),
      fetchJSON("/api/metrics").catch(() => ({})),
    ]);
    failCount = 0;
    lastSyncAt = Date.now();
    state.observedEvents = Array.isArray(globeData.events) ? globeData.events : [];
    state.predictedEvents = [];
    state.trends = Array.isArray(globeData.trends) ? globeData.trends : [];
    state.metrics = metricsData;
    syncDisplayEvents();
    renderAll();
    setServerBadge("Live", "ok");
  })().finally(() => { loadPromise = null; });
  return loadPromise;
}

