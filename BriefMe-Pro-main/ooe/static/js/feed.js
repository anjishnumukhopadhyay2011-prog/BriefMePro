/* ── News feed, event detail panel ────────────────────── */
/* ─── Event selection ───────────────────────────────────────────────────── */
async function selectEvent(e) {
  const rep = state.events.find(ev => ev.event_id === e.event_id) || e;
  state.selectedEvent = rep;
  state.selectedEventDetail = rep;
  centerOnEvent(rep);
  openPanel();
  renderAll();

  if (!rep.predicted) {
    try {
      const payload = await fetchJSON(`/api/event?id=${encodeURIComponent(rep.event_id)}`);
      if (state.selectedEvent?.event_id === rep.event_id) {
        state.selectedEventDetail = payload.event;
        renderEventDetail();
        renderFocusAnchor();
      }
    } catch (_) {}
    postJSON("/api/interactions", { event_id: rep.event_id, action: "open_event", source: "planetary_field", category: rep.category }).catch(() => {});
  }
}

function centerOnEvent(e) {
  if (e.latitude == null || e.longitude == null) return;
  state.globe.targetRotationY = -(Number(e.longitude) * Math.PI) / 180;
  state.globe.targetRotationX = clamp((Number(e.latitude) * Math.PI) / 180 * 0.9, -1.0, 1.0);
  state.globe.targetZoom = clamp(1.06 + Number(e.severity||0)/260, 0.92, 1.3);
}

/* ─── Panel open/close ──────────────────────────────────────────────────── */
function openPanel() {
  el.workspace.classList.add("panel-open");
  el.rightPanel.classList.add("panel-open");
  el.openPanelBtn.classList.add("hidden");
}

function closePanel() {
  el.workspace.classList.remove("panel-open");
  el.rightPanel.classList.remove("panel-open");
  el.openPanelBtn.classList.remove("hidden");
}

/* ─── Canvas interactions ───────────────────────────────────────────────── */
function attachCanvasInteractions() {
  el.globeCanvas.addEventListener("pointerdown", e => {
    if (state.viewMode === "map") {
      state.map.dragging = true;
      state.map.dragDistance = 0;
    } else {
      state.globe.dragging = true;
      state.globe.dragDistance = 0;
    }
    lastPointer = { x: e.clientX, y: e.clientY };
  });

  window.addEventListener("pointerup", () => {
    state.globe.dragging = false;
    state.map.dragging = false;
    lastPointer = null;
  });

  window.addEventListener("pointermove", e => {
    if (!lastPointer) return;
    const dx = e.clientX - lastPointer.x, dy = e.clientY - lastPointer.y;
    if (state.viewMode === "map" && state.map.dragging) {
      state.map.dragDistance += Math.hypot(dx, dy);
      state.map.targetOffsetX += dx;
      state.map.targetOffsetY += dy;
    } else if (state.globe.dragging) {
      state.globe.dragDistance += Math.hypot(dx, dy);
      state.globe.targetRotationY += dx * 0.0048;
      state.globe.targetRotationX = clamp(state.globe.targetRotationX + dy * 0.0032, -1.08, 1.08);
      state.globe.velocityY = dx * 0.00022;
      state.globe.velocityX = dy * 0.00008;
    }
    lastPointer = { x: e.clientX, y: e.clientY };
  });

  el.globeCanvas.addEventListener("wheel", e => {
    e.preventDefault();
    if (state.viewMode === "map") {
      state.map.targetZoom = clamp(state.map.targetZoom + (e.deltaY < 0 ? 0.1 : -0.1), 0.6, 6.0);
    } else {
      state.globe.targetZoom = clamp(state.globe.targetZoom + (e.deltaY < 0 ? 0.06 : -0.06), 0.72, 1.35);
    }
  }, { passive: false });

  el.globeCanvas.addEventListener("click", e => {
    const rect = el.globeCanvas.getBoundingClientRect();
    const cx = e.clientX - rect.left, cy = e.clientY - rect.top;
    if (state.viewMode === "map") {
      if (state.map.dragDistance > 8) return;
      const hit = [...state.map.renderedPoints].reverse().find(p => Math.hypot(p.x - cx, p.y - cy) <= p.size + 9);
      if (hit) selectEvent(hit.event);
    } else {
      if (state.globe.dragDistance > 8) return;
      const hit = [...state.globe.renderedPoints].reverse().find(p => Math.hypot(p.x - cx, p.y - cy) <= p.size + 9);
      if (hit) selectEvent(hit.event);
    }
  });
}
