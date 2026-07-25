/* ── Controls, SSE stream, user identity, app boot ─────── */
/* ─── Controls ──────────────────────────────────────────────────────────── */
function attachControls() {
  // Panel open/close
  el.openPanelBtn.addEventListener("click", openPanel);
  el.closePanelBtn.addEventListener("click", closePanel);

  // Sign out
  el.signOutBtn.addEventListener("click", async () => {
    try { await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" }); } catch (_) {}
    window.location.href = "/login";
  });

  // Map / Globe toggle
  el.mapModeBtn.addEventListener("click", () => {
    const toMap = state.viewMode === "globe";
    state.viewMode = toMap ? "map" : "globe";
    el.mapModeBtn.textContent = toMap ? "⊕ Globe" : "⊞ Map";
    el.mapModeBtn.classList.toggle("map-active", toMap);
    // Reset map pan when entering map mode
    if (toMap) {
      state.map.offsetX = state.map.targetOffsetX = 0;
      state.map.offsetY = state.map.targetOffsetY = 0;
      state.map.zoom = state.map.targetZoom = 1.0;
    }
    // Restart animation loop with correct frame function
    cancelAnimationFrame(animHandle);
    animHandle = 0;
    if (toMap) requestAnimationFrame(drawMapFrame);
    else requestAnimationFrame(drawGlobeFrame);
  });

  // Refresh
  el.refreshBtn.addEventListener("click", async () => {
    el.refreshBtn.textContent = "Refreshing…";
    el.refreshBtn.disabled = true;
    try { await postJSON("/api/ingest", {}); await loadData({ force: true }); } catch (_) {}
    el.refreshBtn.textContent = "Refresh";
    el.refreshBtn.disabled = false;
  });

  // Category filter chips
  el.filterRow.addEventListener("click", e => {
    const chip = e.target.closest("[data-cat]");
    if (!chip) return;
    el.filterRow.querySelectorAll(".chip").forEach(c => c.classList.remove("active"));
    chip.classList.add("active");
    state.filters.category = chip.dataset.cat;
    renderNewsFeed();
  });

  // Search
  el.feedSearch.addEventListener("input", () => {
    state.filters.search = el.feedSearch.value;
    renderNewsFeed();
  });

  // Scope toggle (Native / Global)
  el.scopeBtn.addEventListener("click", () => {
    if (state.viewScope === "global") {
      if (!state.nativeCountry) {
        state.nativeCountry = detectCountryFromTimezone();
        if (!state.nativeCountry) return;
      }
      state.viewScope = "native";
      el.scopeLabel.textContent = state.nativeCountry.name.split(" ")[0];
      el.scopeBtn.classList.add("scope-native");
      state.globe.targetZoom = 1.15;
    } else {
      state.viewScope = "global";
      el.scopeLabel.textContent = "Global";
      el.scopeBtn.classList.remove("scope-native");
      state.globe.targetZoom = 1.02;
    }
    renderNewsFeed();
    renderStats();
  });

  // Simulate button — manual trigger only
  el.simulateBtn.addEventListener("click", () => {
    openPanel();
    runSimulation().catch(console.error);
  });

  // Only trigger on Enter key, not on typing
  el.scenarioDecision.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); openPanel(); runSimulation().catch(console.error); }
  });

  // Keyboard shortcuts
  document.addEventListener("keydown", e => {
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA") return;
    if (e.key === "Escape") closePanel();
    if (e.key === "r" || e.key === "R") el.refreshBtn.click();
  });
}

/* ─── Live SSE stream ───────────────────────────────────────────────────── */
function setupStream() {
  if (!window.EventSource) return;
  let backoff = 1000, reconnTimer = null;
  function connect() {
    if (liveStream) { liveStream.close(); liveStream = null; }
    liveStream = new EventSource("/api/stream");
    liveStream.addEventListener("sync", e => {
      backoff = 1000;
      let payload = {};
      try { payload = JSON.parse(e.data || "{}"); } catch (_) { return; }
      const rev = Number(payload.revision || 0);
      if (!isFinite(rev) || rev === lastRevision) return;
      lastRevision = rev;
      loadData().catch(handleLoadError);
    });
    liveStream.addEventListener("error", () => {
      setServerBadge("Reconnecting");
      liveStream.close(); liveStream = null;
      clearTimeout(reconnTimer);
      reconnTimer = setTimeout(() => { connect(); backoff = Math.min(backoff * 2, 30000); }, backoff);
    });
  }
  connect();
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      if (!animHandle) drawGlobeFrame();
      if (!liveStream) { clearTimeout(reconnTimer); connect(); }
    }
  });
}

/* ─── User identity ─────────────────────────────────────────────────────── */
async function loadUser() {
  try {
    const data = await fetchJSON("/api/auth/me");
    const name  = data.display_name || data.email?.split("@")[0] || "";
    const email = data.email || "";
    if (el.userName)  el.userName.textContent  = name || email || "Account";
    if (el.userAvatar) {
      const initials = name
        ? name.trim().split(/\s+/).map(w => w[0]).slice(0, 2).join("").toUpperCase()
        : (email[0] || "?").toUpperCase();
      el.userAvatar.textContent = initials;
    }
  } catch (_) { /* not fatal — user stays as "Account" */ }
}

/* ─── Cookie consent ────────────────────────────────────────────────────── */
function initCookieConsent() {
  const banner = document.getElementById("cookieBanner");
  const btn    = document.getElementById("cookieAccept");
  if (!banner || !btn) return;
  if (localStorage.getItem("cookie_consent") === "1") return;
  banner.style.display = "flex";
  btn.addEventListener("click", () => {
    localStorage.setItem("cookie_consent", "1");
    banner.style.display = "none";
  });
}

/* ─── Mobile bottom-tab navigation ───────────────────────────────────────── */
// On phones the dashboard is a one-pane-at-a-time app. The tab bar at the
// bottom toggles which pane is showing via body[data-mobile-tab].
function initMobileTabs() {
  const tabs = document.querySelectorAll(".mtab");
  if (!tabs.length) return;

  // Default to the feed tab on first load (most useful read-first view)
  document.body.setAttribute("data-mobile-tab", "feed");

  function setTab(name) {
    document.body.setAttribute("data-mobile-tab", name);
    tabs.forEach(t => t.classList.toggle("active", t.dataset.tab === name));
    // The globe canvas needs a re-measure when it becomes visible
    if (name === "globe" && typeof resizeCanvas === "function") {
      requestAnimationFrame(resizeCanvas);
    }
    // When opening the insight tab, ensure the panel is "open" (desktop class
    // is .panel-closed by default). Mobile CSS ignores that class but the
    // event-detail rendering reads it.
    const right = document.getElementById("rightPanel");
    if (right) right.classList.toggle("panel-closed", name !== "insight");
  }

  tabs.forEach(t => t.addEventListener("click", () => setTab(t.dataset.tab)));

  // When a story is tapped, jump to the insight tab automatically (small
  // window so we don't fight clicks that are still being dispatched).
  document.addEventListener("click", (ev) => {
    const card = ev.target.closest(".news-card");
    if (card && window.matchMedia("(max-width: 768px)").matches) {
      setTimeout(() => setTab("insight"), 50);
    }
  });

  // Tapping the mode button on top — keep desktop behaviour intact, but on
  // mobile route to the globe tab.
  const mapBtn = document.getElementById("mapModeBtn");
  if (mapBtn) {
    mapBtn.addEventListener("click", () => {
      if (window.matchMedia("(max-width: 768px)").matches) setTab("globe");
    });
  }

  // Expose so other modules (feed click handlers) can call it
  window.__setMobileTab = setTab;
}

/* ─── RSS attribution footer ─────────────────────────────────────────────── */
function addSourceAttribution() {
  const feed = document.getElementById("newsFeed");
  if (!feed) return;
  const existing = feed.querySelector(".source-attribution");
  if (existing) return;
  const div = document.createElement("div");
  div.className = "source-attribution";
  div.innerHTML = "Headlines aggregated from public RSS feeds. BriefMe Pro does not host third-party content.<br>Click any story to read the full article at the original source.";
  feed.appendChild(div);
}

/* ─── Boot ──────────────────────────────────────────────────────────────── */
async function boot() {
  initCookieConsent();
  initMobileTabs();
  loadUser();
  await loadCountryBorders();
  resizeCanvas();
  attachCanvasInteractions();
  attachControls();
  setupStream();
  if (!animHandle) drawGlobeFrame();
  try { await loadData(); addSourceAttribution(); } catch (err) { handleLoadError(err); }
  setInterval(() => loadData().catch(handleLoadError), 20000);
}

if (typeof ResizeObserver !== "undefined" && el.globeCanvas) {
  new ResizeObserver(() => resizeCanvas()).observe(el.globeCanvas);
} else {
  window.addEventListener("resize", resizeCanvas);
}

window.addEventListener("DOMContentLoaded", boot);
