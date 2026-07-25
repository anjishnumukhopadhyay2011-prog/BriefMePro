/* ── Globe + flat-map rendering engine ─────────────────── */
/* ─── Flat map (Mercator) projection ─────────────────────────────────────── */
// Mercator world map at ±85° lat is a perfect square.
// mapSize = canvas width (fills horizontally, may overflow vertically — that's correct).
const _MAX_MERC = Math.log(Math.tan(Math.PI / 4 + 85 * Math.PI / 360));

function mapProject(lat, lon, cx, cy, mapSize) {
  const latR   = clamp(lat, -85, 85) * Math.PI / 180;
  const mercY  = Math.log(Math.tan(Math.PI / 4 + latR / 2));
  const nx     = (lon + 180) / 360;                                   // 0→1 left→right
  const ny     = 1 - (mercY + _MAX_MERC) / (2 * _MAX_MERC);          // 0→1 top→bottom
  const zoom   = state.map.zoom;
  return {
    x: cx + (nx - 0.5) * mapSize * zoom + state.map.offsetX,
    y: cy + (ny - 0.5) * mapSize * zoom + state.map.offsetY,
  };
}

/* Light cartographic colour palette for map mode */
const MAP_COUNTRY_PALETTE = [
  "#d4c9a8","#c9d4b8","#c8d0d8","#d4c4b8","#ccd4c0",
  "#d0c8d0","#c4d0c8","#d8ccc4","#c8ccd4","#d4d0c0",
  "#ccc4c8","#d0d4c8",
];
function mapCountryColor(name) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return MAP_COUNTRY_PALETTE[h % MAP_COUNTRY_PALETTE.length];
}

function drawMapBackground(width, height) {
  // Parchment ocean
  ctx.fillStyle = "#b8cdd8";
  ctx.fillRect(0, 0, width, height);
}

function drawMapLand(cx, cy, mapSize) {
  if (!state.globe.countryPolygons.length) return;
  ctx.save();
  // Land shadow/depth pass
  ctx.shadowBlur = 6;
  ctx.shadowColor = "rgba(60,50,30,0.18)";
  for (const { name, rings } of state.globe.countryPolygons) {
    ctx.beginPath();
    for (const ring of rings) {
      let first = true;
      for (const [lat, lon] of ring) {
        const p = mapProject(lat, lon, cx, cy, mapSize);
        if (first) { ctx.moveTo(p.x, p.y); first = false; }
        else ctx.lineTo(p.x, p.y);
      }
      ctx.closePath();
    }
    ctx.fillStyle = mapCountryColor(name);
    ctx.fill("evenodd");
  }
  ctx.shadowBlur = 0;
  // Border pass
  ctx.strokeStyle = "rgba(90,80,60,0.55)";
  ctx.lineWidth = 0.7;
  for (const { rings } of state.globe.countryPolygons) {
    for (const ring of rings) {
      ctx.beginPath();
      let first = true;
      for (const [lat, lon] of ring) {
        const p = mapProject(lat, lon, cx, cy, mapSize);
        if (first) { ctx.moveTo(p.x, p.y); first = false; }
        else ctx.lineTo(p.x, p.y);
      }
      ctx.closePath();
      ctx.stroke();
    }
  }
  ctx.restore();
}

function drawMapGrid(cx, cy, mapSize) {
  ctx.save();
  // Minor grid
  ctx.strokeStyle = "rgba(80,100,120,0.12)";
  ctx.lineWidth = 0.5;
  for (let lat = -60; lat <= 60; lat += 30) {
    const p  = mapProject(lat, -180, cx, cy, mapSize);
    const p2 = mapProject(lat,  180, cx, cy, mapSize);
    ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(p2.x, p2.y); ctx.stroke();
  }
  for (let lon = -180; lon <= 180; lon += 30) {
    const p  = mapProject(-85, lon, cx, cy, mapSize);
    const p2 = mapProject( 85, lon, cx, cy, mapSize);
    ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(p2.x, p2.y); ctx.stroke();
  }
  // Equator
  ctx.strokeStyle = "rgba(80,100,120,0.35)";
  ctx.lineWidth = 1;
  ctx.setLineDash([6, 4]);
  const eq  = mapProject(0, -180, cx, cy, mapSize);
  const eq2 = mapProject(0,  180, cx, cy, mapSize);
  ctx.beginPath(); ctx.moveTo(eq.x, eq.y); ctx.lineTo(eq2.x, eq2.y); ctx.stroke();
  ctx.setLineDash([]);
  ctx.restore();
}

function buildMapPoints(cx, cy, mapSize, time) {
  return state.events
    .filter(e => e.latitude != null && e.longitude != null)
    .map((e, i) => {
      const p    = mapProject(Number(e.latitude), Number(e.longitude), cx, cy, mapSize);
      const alpha = nodeOpacity(e);
      const pulse = 0.72 + ((Math.sin(time * 0.004 + i * 0.8) + 1) / 2) * 0.7;
      const base  = clamp((Number(e.severity||0) / 20) + (e.predicted ? 2.4 : 3.4), 2.4, 14.5);
      const heatRadius = clamp(base * (4.8 + Number(e.severity||0) / 14), 20, 90);
      return { event: e, x: p.x, y: p.y, z: 1, alpha, size: base * pulse,
               color: metaForCategory(e.category||"other").color, heatRadius };
    });
}

function drawMapNodes(pts, time) {
  ctx.save();
  for (const [i, p] of pts.entries()) {
    if (p.alpha < 0.04) continue;
    // White halo for legibility on light background
    ctx.beginPath();
    ctx.arc(p.x, p.y, p.size + 2.5, 0, Math.PI * 2);
    ctx.fillStyle = `rgba(255,255,255,${p.alpha * 0.75})`;
    ctx.fill();
    // Dot
    ctx.shadowBlur = 10;
    ctx.shadowColor = colorWithAlpha(p.color, p.alpha * 0.6);
    ctx.beginPath();
    ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
    ctx.fillStyle = colorWithAlpha(p.color, p.alpha * 0.92);
    ctx.fill();
    ctx.shadowBlur = 0;
    // Pulse ring
    const wave = (time * 0.026 + i * 14) % (p.heatRadius * 1.2);
    const r2   = p.size + 6 + wave;
    ctx.beginPath();
    ctx.strokeStyle = colorWithAlpha(p.color, p.alpha * clamp(1 - r2 / (p.heatRadius * 1.3), 0.03, 0.28));
    ctx.lineWidth = 1.3;
    ctx.arc(p.x, p.y, r2, 0, Math.PI * 2);
    ctx.stroke();
  }
  ctx.restore();
}

function drawMapHeat(pts) {
  ctx.save();
  ctx.globalCompositeOperation = "multiply";
  for (const p of pts.slice(-18)) {
    const g = ctx.createRadialGradient(p.x, p.y, p.size, p.x, p.y, p.heatRadius);
    g.addColorStop(0,    colorWithAlpha(p.color, p.alpha * 0.22));
    g.addColorStop(0.4,  colorWithAlpha(p.color, p.alpha * 0.10));
    g.addColorStop(1,    colorWithAlpha(p.color, 0));
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.arc(p.x, p.y, p.heatRadius, 0, Math.PI * 2); ctx.fill();
  }
  ctx.restore();
}

function drawMapFrame(time = 0) {
  // In native mode, pan map to detected country
  if (state.viewScope === "native" && state.nativeCountry) {
    const country = state.nativeCountry;
    const _MAX_MERC_LOCAL = Math.log(Math.tan(Math.PI / 4 + 85 * Math.PI / 360));
    const lon = country.lon, lat = country.lat;
    const nx = (lon + 180) / 360;
    const latR = clamp(lat, -85, 85) * Math.PI / 180;
    const mercY = Math.log(Math.tan(Math.PI / 4 + latR / 2));
    const ny = 1 - (mercY + _MAX_MERC_LOCAL) / (2 * _MAX_MERC_LOCAL);
    const rect = el.globeCanvas.getBoundingClientRect();
    const mapSize = rect.width;
    state.map.targetOffsetX = -(nx - 0.5) * mapSize * 1.8;
    state.map.targetOffsetY = -(ny - 0.5) * mapSize * 1.8;
    state.map.targetZoom = 1.8;
  }

  state.map.offsetX += (state.map.targetOffsetX - state.map.offsetX) * 0.1;
  state.map.offsetY += (state.map.targetOffsetY - state.map.offsetY) * 0.1;
  state.map.zoom    += (state.map.targetZoom    - state.map.zoom)    * 0.12;

  const rect = el.globeCanvas.getBoundingClientRect();
  const { width, height } = rect;
  ctx.clearRect(0, 0, width, height);

  const cx = width / 2, cy = height / 2;
  // Mercator at ±85° is a square — use full canvas width as map size.
  // This fills horizontally; vertically the poles may extend beyond canvas edges, which is correct.
  const mapSize = width;

  drawMapBackground(width, height);
  drawMapGrid(cx, cy, mapSize);
  drawMapLand(cx, cy, mapSize);

  const pts = buildMapPoints(cx, cy, mapSize, time);
  state.map.renderedPoints = pts;
  drawMapHeat(pts);
  drawMapNodes(pts, time);

  if (!document.hidden) animHandle = requestAnimationFrame(drawMapFrame);
}

function drawStarfield(width, height, time) {
  for (const s of state.globe.stars) {
    const twinkle = 0.55 + 0.45 * Math.sin(time * s.speed + s.phase);
    ctx.globalAlpha = s.opacity * twinkle;
    ctx.fillStyle = "#ffffff";
    ctx.beginPath();
    ctx.arc(s.x * width, s.y * height, s.r, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.globalAlpha = 1;
}

function drawCornerBrackets(width, height) {
  const sz = 18, pad = 14;
  ctx.save();
  ctx.strokeStyle = "rgba(78,207,255,0.45)";
  ctx.lineWidth = 1.5;
  for (const [x, y, dx, dy] of [[pad,pad,1,1],[width-pad,pad,-1,1],[pad,height-pad,1,-1],[width-pad,height-pad,-1,-1]]) {
    ctx.beginPath(); ctx.moveTo(x+dx*sz,y); ctx.lineTo(x,y); ctx.lineTo(x,y+dy*sz); ctx.stroke();
  }
  ctx.restore();
}

function drawOrbitalRings(cx, cy, radius) {
  ctx.save();
  ctx.strokeStyle = "rgba(78,207,255,0.07)";
  ctx.lineWidth = 0.8;
  ctx.setLineDash([4, 8]);
  ctx.beginPath();
  ctx.ellipse(cx, cy, radius * 1.18, radius * 0.18, 0, 0, Math.PI * 2);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.restore();
}

function drawGlobeBase(cx, cy, radius) {
  ctx.save();
  const outerGlow = ctx.createRadialGradient(cx, cy, radius * 0.7, cx, cy, radius * 1.35);
  outerGlow.addColorStop(0, "rgba(10,40,80,0.0)");
  outerGlow.addColorStop(1, "rgba(4,16,40,0.55)");
  ctx.fillStyle = outerGlow;
  ctx.beginPath(); ctx.arc(cx, cy, radius * 1.35, 0, Math.PI * 2); ctx.fill();
  ctx.beginPath(); ctx.arc(cx, cy, radius, 0, Math.PI * 2); ctx.clip();
  const ocean = ctx.createRadialGradient(cx - radius*0.25, cy - radius*0.25, 0, cx, cy, radius);
  ocean.addColorStop(0, "#1a4a7a"); ocean.addColorStop(0.5, "#0d2e52"); ocean.addColorStop(1, "#061828");
  ctx.fillStyle = ocean;
  ctx.fillRect(cx - radius - 2, cy - radius - 2, radius * 2 + 4, radius * 2 + 4);
  ctx.restore();
}

function drawAtmosphere(cx, cy, radius) {
  ctx.save();
  const atm = ctx.createRadialGradient(cx, cy, radius * 0.92, cx, cy, radius * 1.08);
  atm.addColorStop(0, "rgba(80,160,255,0.22)"); atm.addColorStop(0.5, "rgba(40,100,200,0.10)"); atm.addColorStop(1, "rgba(20,60,150,0.0)");
  ctx.fillStyle = atm; ctx.beginPath(); ctx.arc(cx, cy, radius * 1.08, 0, Math.PI * 2); ctx.fill();
  const hi = ctx.createRadialGradient(cx - radius*0.3, cy - radius*0.35, 0, cx - radius*0.3, cy - radius*0.35, radius * 1.1);
  hi.addColorStop(0, "rgba(180,220,255,0.08)"); hi.addColorStop(0.5, "rgba(100,160,255,0.03)"); hi.addColorStop(1, "rgba(0,0,0,0)");
  ctx.fillStyle = hi; ctx.beginPath(); ctx.arc(cx, cy, radius * 1.08, 0, Math.PI * 2); ctx.fill();
  ctx.restore();
}

function drawSweep(cx, cy, radius, time) {
  ctx.save();
  ctx.beginPath(); ctx.arc(cx, cy, radius, 0, Math.PI * 2); ctx.clip();
  const angle = (time * 0.4) % (Math.PI * 2);
  ctx.strokeStyle = "rgba(78,207,255,0.06)"; ctx.lineWidth = radius * 0.35;
  ctx.beginPath(); ctx.arc(cx, cy, radius * 0.65, angle - 0.35, angle); ctx.stroke();
  ctx.restore();
}

function drawGrid(cx, cy, radius) {
  ctx.save();
  ctx.beginPath(); ctx.arc(cx, cy, radius, 0, Math.PI * 2); ctx.clip();
  ctx.strokeStyle = "rgba(100,180,255,0.06)"; ctx.lineWidth = 0.5;
  for (let lat = -60; lat <= 60; lat += 30) { const s = projectRing(Array.from({length:181},(_,i)=>[lat,i-90]),cx,cy,radius); if(buildPath(s)) ctx.stroke(); }
  for (let lon = -180; lon < 180; lon += 30) { const s = projectRing(Array.from({length:181},(_,i)=>[i-90,lon]),cx,cy,radius); if(buildPath(s)) ctx.stroke(); }
  ctx.restore();
}

const COUNTRY_PALETTE = [
  "rgba(62,95,60,0.72)","rgba(95,75,50,0.72)","rgba(55,80,100,0.72)","rgba(90,65,80,0.72)",
  "rgba(100,88,50,0.72)","rgba(48,90,78,0.72)","rgba(85,70,55,0.72)","rgba(60,75,95,0.72)",
  "rgba(80,90,55,0.72)","rgba(70,55,90,0.72)","rgba(95,60,60,0.72)","rgba(50,85,85,0.72)",
];
function countryColor(name) {
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return COUNTRY_PALETTE[h % COUNTRY_PALETTE.length];
}

function drawContinents(cx, cy, radius) {
  if (!state.globe.countryPolygons.length) return;
  ctx.save();
  ctx.beginPath(); ctx.arc(cx, cy, radius, 0, Math.PI * 2); ctx.clip();
  for (const { name, rings } of state.globe.countryPolygons) {
    ctx.beginPath();
    let vis = false;
    for (const ring of rings) {
      for (const seg of projectRing(ring, cx, cy, radius)) {
        if (seg.length < 2) continue;
        ctx.moveTo(seg[0].sx, seg[0].sy);
        for (let i = 1; i < seg.length; i++) ctx.lineTo(seg[i].sx, seg[i].sy);
        ctx.closePath(); vis = true;
      }
    }
    if (vis) { ctx.fillStyle = countryColor(name); ctx.fill("evenodd"); }
  }
  ctx.restore();
}

function drawCountryBorders(cx, cy, radius) {
  ctx.save();
  ctx.beginPath(); ctx.arc(cx, cy, radius, 0, Math.PI * 2); ctx.clip();
  if (state.globe.statePolygons.length) {
    ctx.strokeStyle = "rgba(160,200,255,0.30)"; ctx.lineWidth = 0.6; ctx.setLineDash([2, 4]);
    for (const { rings } of state.globe.statePolygons) for (const ring of rings) { const s = projectRing(ring,cx,cy,radius); if(buildPath(s)) ctx.stroke(); }
    ctx.setLineDash([]);
  }
  if (state.globe.countryPolygons.length) {
    ctx.strokeStyle = "rgba(200,235,255,0.85)"; ctx.lineWidth = 1.2;
    for (const { rings } of state.globe.countryPolygons) for (const ring of rings) { const s = projectRing(ring,cx,cy,radius); if(buildPath(s)) ctx.stroke(); }
  } else if (state.globe.countryBorders.length) {
    ctx.strokeStyle = "rgba(200,235,255,0.70)"; ctx.lineWidth = 1.0;
    for (const seg of state.globe.countryBorders) { const s = projectRing(seg,cx,cy,radius); if(buildPath(s)) ctx.stroke(); }
  }
  ctx.restore();
}

function nodeOpacity(e) {
  if (!e.happened_at) return 0.4;
  const et = new Date(e.happened_at).getTime();
  const ft = Date.now() + (state.timelineValue - 50) * 6 * 3600000;
  const dh = Math.abs(et - ft) / 3600000;
  const band = e.predicted ? 240 : 180;
  let o = clamp(1 - dh / band, 0.08, 1);
  if (e.predicted) o *= clamp(0.18 + futureStrength() * 0.92, 0.1, 1);
  else o *= clamp(0.52 + 0.2 + (1 - Math.abs(state.timelineValue - 50) / 100) * 0.38, 0.18, 1);
  return o;
}

function buildPoints(cx, cy, radius, time) {
  return state.events
    .filter(e => e.latitude != null && e.longitude != null)
    .map((e, i) => {
      const phase = manifestationPhase(e);
      const sr = e.predicted ? radius * (1.03 + futureStrength() * 0.09) : radius * 0.985;
      const p = projectPoint(Number(e.latitude), Number(e.longitude), sr, state.globe.rotationY, state.globe.rotationX);
      const alpha = nodeOpacity(e) * clamp((p.z + 1.1) / 1.8, 0.08, 1);
      const pulse = 0.72 + ((Math.sin(time * 0.004 + i * 0.8) + 1) / 2) * 0.7;
      const base  = clamp((Number(e.severity||0) / 20) + (e.predicted ? 2.4 : 3.4), 2.4, 14.5);
      return { event: e, x: cx+p.x, y: cy-p.y, z: p.z, alpha, size: base*pulse, color: metaForCategory(e.category||"other").color, phase, heatRadius: clamp(base*(4.8+Number(e.severity||0)/14),20,110) };
    })
    .filter(p => p.z > -0.3)
    .sort((a, b) => a.z - b.z);
}

function drawHeatField(pts) {
  ctx.save(); ctx.globalCompositeOperation = "lighter";
  for (const p of pts.slice(-18)) {
    const g = ctx.createRadialGradient(p.x,p.y,p.size,p.x,p.y,p.heatRadius);
    g.addColorStop(0, colorWithAlpha(p.color, p.alpha*0.18));
    g.addColorStop(0.35, colorWithAlpha(p.color, p.alpha*0.1));
    g.addColorStop(1, colorWithAlpha(p.color, 0));
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(p.x,p.y,p.heatRadius,0,Math.PI*2); ctx.fill();
  }
  ctx.restore();
}

function drawTopologicalContours(pts, time) {
  ctx.save();
  for (const [i, p] of pts.filter(p=>p.alpha>0.12).slice(-10).entries()) {
    const rings = p.event.predicted ? 2 : 3;
    for (let r = 0; r < rings; r++) {
      const pr = p.size + 10 + ((time*0.03+i*18+r*24) % (p.heatRadius*0.8||40));
      const a  = p.alpha * clamp(1 - pr / ((p.heatRadius||40)+10), 0.04, 0.22);
      ctx.beginPath(); ctx.setLineDash(p.event.predicted?[5,5]:[]);
      ctx.strokeStyle = colorWithAlpha(p.color, a); ctx.lineWidth = r===0?1.4:1;
      ctx.arc(p.x,p.y,pr,0,Math.PI*2); ctx.stroke();
    }
    ctx.setLineDash([]);
  }
  ctx.restore();
}

function drawWavefronts(pts, time) {
  ctx.save();
  for (const [i, p] of pts.filter(p=>!p.event.predicted&&p.alpha>0.16).slice(-12).entries()) {
    const age = Math.abs(hoursFromNow(p.event.happened_at));
    const vit = clamp(1 - age/60, 0.08, 1);
    if (vit <= 0.08) continue;
    const wave = (time*0.026 + i*14 + age*4) % (p.heatRadius*1.2||40);
    const r = p.size + 8 + wave;
    ctx.beginPath();
    ctx.strokeStyle = colorWithAlpha(p.color, p.alpha*vit*clamp(1-r/(p.heatRadius*1.28||40),0.04,0.24));
    ctx.lineWidth = 1.25; ctx.arc(p.x,p.y,r,0,Math.PI*2); ctx.stroke();
  }
  ctx.restore();
}

function drawHotlines(cx, cy, radius, time) {
  const lines = hotlineLinks().slice(0, 10);
  if (!lines.length) return;
  ctx.save(); ctx.lineWidth = 1;
  for (const [i, line] of lines.entries()) {
    if (line.from_latitude == null || line.to_latitude == null) continue;
    const s = projectPoint(Number(line.from_latitude), Number(line.from_longitude), radius*1.04, state.globe.rotationY, state.globe.rotationX);
    const e2 = projectPoint(Number(line.to_latitude),   Number(line.to_longitude),   radius*1.04, state.globe.rotationY, state.globe.rotationX);
    if (s.z < -0.18 || e2.z < -0.18) continue;
    const sp = {x:cx+s.x, y:cy-s.y}, ep = {x:cx+e2.x, y:cy-e2.y};
    const col = metaForSystem(line.system||"general").color;
    const ctrl = {x:(sp.x+ep.x)/2+(cx-(sp.x+ep.x)/2)*0.14, y:(sp.y+ep.y)/2+(cy-(sp.y+ep.y)/2)*0.22};
    ctx.beginPath(); ctx.strokeStyle = colorWithAlpha(col, clamp((Number(line.strength||0)/100)*0.22,0.05,0.22));
    ctx.moveTo(sp.x,sp.y); ctx.quadraticCurveTo(ctrl.x,ctrl.y,ep.x,ep.y); ctx.stroke();
    const t = ((time*0.00018)+i*0.13) % 1;
    const om = 1-t;
    const px = om*om*sp.x + 2*om*t*ctrl.x + t*t*ep.x;
    const py = om*om*sp.y + 2*om*t*ctrl.y + t*t*ep.y;
    ctx.beginPath(); ctx.fillStyle = colorWithAlpha(col,0.72); ctx.shadowBlur=20; ctx.shadowColor=colorWithAlpha(col,0.64);
    ctx.arc(px,py,2.2,0,Math.PI*2); ctx.fill(); ctx.shadowBlur=0;
  }
  ctx.restore();
}

function drawConnections(cx, cy, pts) {
  if (!pts.length) return;
  const selId = state.selectedEvent?.event_id;
  const selP = pts.find(p => p.event.event_id === selId);
  ctx.save(); ctx.lineWidth = 1;
  if (selP) {
    const related = pts.filter(p=>p!==selP&&(p.event.category===selP.event.category||p.event.region===selP.event.region)).slice(0,8);
    for (const p of related) { ctx.beginPath(); ctx.strokeStyle=colorWithAlpha(selP.color,0.16); ctx.moveTo(selP.x,selP.y); ctx.quadraticCurveTo(cx,cy,p.x,p.y); ctx.stroke(); }
  } else {
    const anchors = [...pts].sort((a,b)=>Number(b.event.severity||0)-Number(a.event.severity||0)).slice(0,6);
    for (let i=0;i<anchors.length-1;i++) { ctx.beginPath(); ctx.strokeStyle=colorWithAlpha(anchors[i].color,0.08); ctx.moveTo(anchors[i].x,anchors[i].y); ctx.quadraticCurveTo(cx,cy,anchors[i+1].x,anchors[i+1].y); ctx.stroke(); }
  }
  ctx.restore();
}

function drawNodes(pts, time) {
  state.globe.renderedPoints = pts;
  for (const p of pts) {
    const isSel = p.event.event_id === state.selectedEvent?.event_id;
    const pr = p.size + (Math.sin(time*0.008+p.size)+1)*1.9;
    ctx.save();
    ctx.beginPath(); ctx.strokeStyle=colorWithAlpha(p.color,p.alpha*0.24); ctx.lineWidth=isSel?2:1; ctx.arc(p.x,p.y,pr*(isSel?1.42:1.16),0,Math.PI*2); ctx.stroke();
    ctx.beginPath(); ctx.fillStyle=colorWithAlpha(p.color,p.alpha*0.94); ctx.shadowBlur=isSel?34:22; ctx.shadowColor=colorWithAlpha(p.color,0.78); ctx.arc(p.x,p.y,p.size,0,Math.PI*2); ctx.fill(); ctx.shadowBlur=0;
    if (p.event.predicted) { ctx.beginPath(); ctx.setLineDash([4,4]); ctx.strokeStyle=colorWithAlpha(p.color,p.alpha*0.46); ctx.arc(p.x,p.y,p.size+5.2,0,Math.PI*2); ctx.stroke(); ctx.setLineDash([]); }
    ctx.restore();
  }
}

function drawReticle(point, time) {
  if (!point) return;
  ctx.save();
  const outer = point.size + 18 + Math.sin(time*0.006)*3;
  ctx.strokeStyle = colorWithAlpha(point.color, 0.46); ctx.lineWidth = 1.2;
  ctx.beginPath(); ctx.arc(point.x, point.y, outer, 0, Math.PI*2); ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(point.x-outer-8,point.y); ctx.lineTo(point.x-outer+6,point.y);
  ctx.moveTo(point.x+outer-6,point.y); ctx.lineTo(point.x+outer+8,point.y);
  ctx.moveTo(point.x,point.y-outer-8); ctx.lineTo(point.x,point.y-outer+6);
  ctx.moveTo(point.x,point.y+outer-6); ctx.lineTo(point.x,point.y+outer+8);
  ctx.stroke(); ctx.restore();
}

function positionFocusAnchor(point) {
  if (!point || point.z < 0.02) { el.focusAnchor.style.opacity = "0"; return; }
  const rect = el.globeCanvas.getBoundingClientRect();
  const x = clamp(point.x + 22, 24, rect.width - 220);
  const y = clamp(point.y - 28, 32, rect.height - 40);
  el.focusAnchor.style.opacity = "1";
  el.focusAnchor.style.transform = `translate(${x}px, ${y}px)`;
}

function stepGlobeMotion() {
  if (!state.globe.dragging) {
    state.globe.velocityY *= 0.985;
    state.globe.velocityX *= 0.92;
    if (state.viewScope === "native" && state.nativeCountry) {
      // In native mode, rotate globe to face the detected country
      const countryLon = state.nativeCountry.lon;
      const countryLat = state.nativeCountry.lat;
      const targetRotY = -(countryLon * Math.PI) / 180;
      const targetRotX = clamp((countryLat * Math.PI) / 180 * 0.9, -1.04, 1.04);
      state.globe.targetRotationY = targetRotY;
      state.globe.targetRotationX = targetRotX;
      state.globe.targetZoom = 1.15;
    } else {
      state.globe.targetRotationY += 0.00058 + state.globe.velocityY;
      state.globe.targetRotationX = clamp(state.globe.targetRotationX + state.globe.velocityX, -1.04, 1.04);
    }
  }
  state.globe.rotationY += (state.globe.targetRotationY - state.globe.rotationY) * 0.08;
  state.globe.rotationX += (state.globe.targetRotationX - state.globe.rotationX) * 0.1;
  state.globe.zoom      += (state.globe.targetZoom      - state.globe.zoom)      * 0.12;
}

function drawGlobeFrame(time = 0) {
  stepGlobeMotion();
  const rect = el.globeCanvas.getBoundingClientRect();
  const { width, height } = rect;
  ctx.clearRect(0, 0, width, height);
  drawStarfield(width, height, time);
  drawCornerBrackets(width, height);
  const cx = width / 2, cy = height / 2;
  const radius = Math.min(width, height) * 0.44 * state.globe.zoom;
  drawOrbitalRings(cx, cy, radius);
  drawGlobeBase(cx, cy, radius);
  drawGrid(cx, cy, radius);
  drawContinents(cx, cy, radius);
  drawCountryBorders(cx, cy, radius);
  drawAtmosphere(cx, cy, radius);
  drawSweep(cx, cy, radius, time);
  const pts = buildPoints(cx, cy, radius, time);
  drawHotlines(cx, cy, radius, time);
  drawConnections(cx, cy, pts);
  drawHeatField(pts);
  drawTopologicalContours(pts, time);
  drawWavefronts(pts, time);
  drawNodes(pts, time);
  const selPt = pts.find(p => p.event.event_id === state.selectedEvent?.event_id);
  drawReticle(selPt, time);
  positionFocusAnchor(selPt);
  if (!document.hidden) animHandle = requestAnimationFrame(drawGlobeFrame);
}

/* ──────────────────────────────────────────────────────────────────────────
   RENDER FUNCTIONS
   ────────────────────────────────────────────────────────────────────────── */

function renderTopBar() {
  const count = state.observedEvents.length;
  const stress = state.metrics?.state?.stress ?? state.metrics?.stress;
  const regions = regionPressures();
  const hotspot = regions[0];
  el.eventCount.textContent = `${count} events`;
  el.topStress.textContent  = stress != null ? `stress ${Math.round(Number(stress))}` : "stress —";
  el.topHotspot.textContent = hotspot ? hotspot.region : "—";
}

function renderStats() {
  const systems = systemPressures().slice(0, 2);
  const regions = regionPressures().slice(0, 2);
  const isNative = state.viewScope === "native" && state.nativeCountry;
  const cells = [
    {
      label: isNative ? `${state.nativeCountry.name}` : "Live Signals",
      value: formatNumber(globeVisibleEvents().length),
      detail: isNative ? "Local signals" : `${formatNumber(state.metrics?.high_impact_events || 0)} high impact`,
    },
    {
      label: "Top Pressure",
      value: systems[0]?.label || "—",
      detail: systems[1] ? `Also: ${systems[1].label}` : "System analysis active",
    },
    {
      label: "Primary Hotspot",
      value: regions[0]?.region || "—",
      detail: regions[1] ? `Also: ${regions[1].region}` : "Regional scan active",
    },
    {
      label: "Top Domain",
      value: metaForCategory(state.metrics?.top_category || "other").label,
      detail: `${formatNumber(state.predictedEvents.length)} forecast nodes`,
    },
  ];
  el.statsBar.innerHTML = cells.map(c => `
    <div class="stat-cell">
      <span>${escapeHTML(c.label)}</span>
      <strong>${escapeHTML(c.value)}</strong>
      <p>${escapeHTML(c.detail)}</p>
    </div>
  `).join("");
}

function renderNewsFeed() {
  const items = globeVisibleEvents();
  const selId = state.selectedEvent?.event_id;

  if (!items.length) {
    const msg = state.viewScope === "native" && state.nativeCountry
      ? `<p class="empty-msg">No recent signals from ${escapeHTML(state.nativeCountry.name)}. Switch to Global to see world news.</p>`
      : '<p class="empty-msg">No stories match the current filter.</p>';
    el.newsFeed.innerHTML = msg;
    return;
  }

  let html = "";
  if (state.viewScope === "native" && state.nativeCountry) {
    html += `<div class="native-banner">
      <span class="native-flag">📍</span>
      <span class="native-label">${escapeHTML(state.nativeCountry.name)} signals</span>
      <span class="native-count">${items.length} active</span>
    </div>`;
  }

  html += items.map(e => {
    const cm   = metaForCategory(e.category || "other");
    const ph   = manifestationPhase(e);
    const age  = relativeAge(e.happened_at);
    const loc  = formatLocation(e);
    const src  = sourceLabel(e);
    const url  = e.url || (Array.isArray(e.source_refs) && e.source_refs.length ? e.source_refs[0]?.external_id : null);
    const sel  = e.event_id === selId;
    return `
      <div class="news-item${sel?" is-selected":""}" data-eid="${escapeHTML(e.event_id)}">
        <div class="news-topline">
          <span class="news-cat" style="color:${cm.color}">${escapeHTML(cm.label)}</span>
          <span class="news-age">${escapeHTML(age)}</span>
        </div>
        <span class="news-title" style="color:${cm.color}">${escapeHTML(e.title || "Untitled signal")}</span>
        <span class="news-loc">${escapeHTML(loc)}</span>
        ${e.summary ? `<p class="news-summary">${escapeHTML(e.summary)}</p>` : ""}
        <div class="news-footer">
          <span class="news-phase" style="color:${ph.color}">${escapeHTML(ph.label)}</span>
          <span class="news-source">${escapeHTML(src)}</span>
          ${url ? `<a class="news-readlink" href="${escapeHTML(url)}" target="_blank" rel="noreferrer noopener" onclick="event.stopPropagation()">Read →</a>` : ""}
          <span class="news-feedback" onclick="event.stopPropagation()">
            <button class="fb-btn fb-up"   data-eid="${escapeHTML(e.event_id)}" title="Useful — show me more like this">👍</button>
            <button class="fb-btn fb-down" data-eid="${escapeHTML(e.event_id)}" title="Not useful — show me less like this">👎</button>
          </span>
        </div>
      </div>`;
  }).join("");

  el.newsFeed.innerHTML = html;

  el.newsFeed.querySelectorAll("[data-eid]").forEach(node => {
    node.addEventListener("click", () => {
      const e = state.events.find(ev => ev.event_id === node.dataset.eid);
      if (e) selectEvent(e);
    });
  });

  // Wire feedback buttons — stop propagation so they don't open the event detail
  el.newsFeed.querySelectorAll(".fb-btn").forEach(btn => {
    btn.addEventListener("click", ev => {
      ev.stopPropagation();
      const action = btn.classList.contains("fb-up") ? "up" : "down";
      sendFeedback(btn.dataset.eid, action, btn);
    });
  });
}

async function sendFeedback(eventId, action, btn) {
  // Optimistic UI — dim the button immediately
  btn.disabled = true;
  btn.style.opacity = "0.4";
  try {
    const res = await fetch("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ event_id: eventId, action }),
    });
    if (res.ok) {
      // Show a brief confirmation colour
      btn.style.opacity = "1";
      btn.style.background = action === "up" ? "rgba(85,239,194,0.25)" : "rgba(255,108,126,0.2)";
    } else {
      btn.disabled = false;
      btn.style.opacity = "1";
    }
  } catch {
    btn.disabled = false;
    btn.style.opacity = "1";
  }
}

function renderEventDetail() {
  const e = state.selectedEventDetail || state.selectedEvent;
  if (!e) { el.eventDetail.innerHTML = ""; return; }
  const cm  = metaForCategory(e.category || "other");
  const ph  = manifestationPhase(e);
  const ver = verificationState(e);
  const sev = Number(e.severity || 0);
  const urg = Number(e.urgency  || 0);
  const imp = Number(e.personal_impact || e.relevance || 0);
  const sevWord = sev>=85?"Critical":sev>=70?"High":sev>=50?"Moderate":"Low";
  const urgWord = urg>=70?"Immediate":urg>=45?"Elevated":urg>=25?"Steady":"Subdued";
  const impWord = imp>=70?"Widespread":imp>=45?"Significant":imp>=25?"Contained":"Marginal";
  const loc = formatLocation(e);
  const age = relativeAge(e.happened_at);
  const url = e.url || (Array.isArray(e.source_refs) && e.source_refs.length ? e.source_refs[0]?.external_id : null);
  const tags = [ph.label, ver.label, metaForSystem(systemForCategory(e.category||"other")).label, ...(e.country?[e.country]:[]), ...(e.region?[e.region]:[])].filter(Boolean);

  el.eventDetail.innerHTML = `
    <div class="panel-section">
      <div class="section-label">SELECTED EVENT</div>
      <div class="ev-title">${escapeHTML(e.title || "Untitled signal")}</div>
      <div class="ev-meta">
        <span class="ev-cat" style="color:${cm.color}">${escapeHTML(cm.label)}</span>
        <span class="ev-loc">${escapeHTML(loc)}</span>
        <span class="ev-age">${escapeHTML(age)}</span>
      </div>
      ${e.summary ? `<p class="ev-summary">${escapeHTML(e.summary)}</p>` : ""}
      <div class="ev-grid">
        <div class="ev-stat"><span>Severity</span><strong style="color:#ff8d77">${escapeHTML(sevWord)}</strong></div>
        <div class="ev-stat"><span>Urgency</span><strong style="color:#ffd16d">${escapeHTML(urgWord)}</strong></div>
        <div class="ev-stat"><span>Impact</span><strong style="color:#87c9ff">${escapeHTML(impWord)}</strong></div>
        <div class="ev-stat"><span>Confidence</span><strong style="color:${ver.color}">${escapeHTML(ver.label)}</strong></div>
      </div>
      <div class="ev-tags">${tags.map(t=>`<span class="ev-tag">${escapeHTML(t)}</span>`).join("")}</div>
      <div class="ev-source">${escapeHTML(ver.detail)} · ${escapeHTML(sourceLabel(e))}</div>
      ${url ? `<a class="ev-readlink" href="${escapeHTML(url)}" target="_blank" rel="noreferrer noopener">Read full story →</a>` : ""}
    </div>`;
}

function renderFocusAnchor() {
  const e = state.selectedEventDetail || state.selectedEvent;
  if (!e) { el.focusAnchor.style.opacity = "0"; el.focusAnchor.innerHTML = ""; return; }
  const ph = manifestationPhase(e);
  el.focusAnchor.innerHTML = `
    <strong style="color:${ph.color}">${escapeHTML(e.title || "Untitled signal")}</strong>
    <p>${escapeHTML(formatLocation(e))} · ${escapeHTML(ph.label)}</p>`;
}

function renderTrends() {
  const trends = (state.trends || []).slice(0, 5);
  if (!trends.length) {
    el.trendField.innerHTML = '<p class="empty-msg">Trend signals appear as related events accumulate and patterns emerge.</p>';
    return;
  }
  el.trendField.innerHTML = trends.map(t => {
    const col = t.classification==="trending"?"#55efc2":t.classification==="predicted"?"#ffd16d":"#76d5ff";
    const cls = t.classification==="trending"?"Trending Up":t.classification==="predicted"?"Forecast":"Emerging";
    const signalCount = t.signal_count || t.event_count || "";
    const momentum = t.momentum ? ` · momentum ${t.momentum > 0 ? '↑' : '↓'}` : "";
    return `<div class="trend-card">
      <div class="trend-hd">
        <span class="trend-name" style="color:${col}">${escapeHTML(t.trend_name || "Unnamed trend")}</span>
        <span class="trend-class" style="color:${col}">${escapeHTML(cls)}${signalCount ? ` · ${signalCount} signals` : ""}${momentum}</span>
      </div>
      <p class="trend-text">${escapeHTML(t.short_explanation || "Signal is being constructed.")}</p>
      ${t.possible_future_direction ? `<p class="trend-text trend-direction">→ ${escapeHTML(t.possible_future_direction)}</p>` : ""}
    </div>`;
  }).join("");
}

function renderSimOutput() {
  const storyTabs = Array.isArray(state.simulation?.story_tabs) ? state.simulation.story_tabs : [];
  const branches = Array.isArray(state.simulation?.branches) ? state.simulation.branches : [];
  const chain    = Array.isArray(state.simulation?.causal_chain) ? state.simulation.causal_chain : [];
  const focus    = state.simulation?.focus;
  const isAiStory = storyTabs.some(tab => tab.eyebrow && tab.eyebrow.toLowerCase().includes("ai"));

  if (storyTabs.length) {
    const activeId = storyTabs.some(tab => tab.id === state.simulationTab)
      ? state.simulationTab
      : storyTabs[0].id;
    const activeTab = storyTabs.find(tab => tab.id === activeId) || storyTabs[0];
    const paragraphs = Array.isArray(activeTab.paragraphs) ? activeTab.paragraphs : [];
    const steps = Array.isArray(activeTab.steps) ? activeTab.steps : [];
    const bullets = Array.isArray(activeTab.bullets) ? activeTab.bullets : [];

    let html = `<div class="sim-story-shell${isAiStory ? ' ai-generated' : ''}">`;
    html += `<div class="sim-story-hero">
      <div class="sim-story-label">${isAiStory ? "AI-generated simulation story" : "Simulation story"}</div>
      <h3 class="sim-story-title">${escapeHTML(state.simulation?.story_title || "What the model thinks happens next")}</h3>
      ${state.simulation?.story_summary ? `<p class="sim-story-summary">${escapeHTML(state.simulation.story_summary)}</p>` : ""}
    </div>`;

    html += `<div class="sim-tab-row" role="tablist" aria-label="Simulation story views">${storyTabs.map(tab => `
      <button
        type="button"
        class="sim-tab-btn${tab.id === activeId ? " active" : ""}"
        data-sim-tab="${escapeHTML(tab.id)}"
        role="tab"
        aria-selected="${tab.id === activeId ? "true" : "false"}"
      >${escapeHTML(tab.label || tab.id)}</button>
    `).join("")}</div>`;

    html += `<div class="sim-tab-panel" role="tabpanel">
      ${activeTab.eyebrow ? `<div class="sim-tab-eyebrow">${escapeHTML(activeTab.eyebrow)}</div>` : ""}
      ${activeTab.title && activeTab.title !== state.simulation?.story_title ? `<h4 class="sim-tab-title">${escapeHTML(activeTab.title)}</h4>` : ""}
    `;

    if (paragraphs.length) {
      html += paragraphs.map(p => `<p class="sim-tab-copy">${escapeHTML(p)}</p>`).join("");
    }

    if (steps.length) {
      html += `<div class="sim-step-list">${steps.map(step => `
        <div class="sim-step-card">
          <div class="sim-step-label">${escapeHTML(step.label || "")}</div>
          <div class="sim-step-title">${escapeHTML(step.title || "")}</div>
          <p class="sim-step-text">${escapeHTML(step.text || "")}</p>
        </div>
      `).join("")}</div>`;
    }

    if (bullets.length) {
      html += `<ul class="sim-watch-list">${bullets.map(item => `
        <li class="sim-watch-item">${escapeHTML(item)}</li>
      `).join("")}</ul>`;
    }

    html += "</div></div>";
    el.simOutput.innerHTML = html;
    el.simOutput.querySelectorAll("[data-sim-tab]").forEach(btn => {
      btn.addEventListener("click", () => {
        state.simulationTab = btn.dataset.simTab || "story";
        renderSimOutput();
      });
    });
    return;
  }

  if (!branches.length && !chain.length) {
    el.simOutput.innerHTML = '<p class="sim-empty">Run a simulation to see the full global causal-chain analysis and scenario branches.</p>';
    return;
  }

  const LAYER_COLORS = ["#4ecfff", "#ffd16d", "#a0c4d0"];
  const PRESSURE_COLORS = { critical: "#ff6f7e", high: "#ffa07a", moderate: "#ffd16d", low: "#55efc2" };

  let html = "";

  // Focus bar
  if (focus) {
    html += `<div class="sim-focus-bar">
      <span class="sim-focus-label">Global Synthesis</span>
      <span class="sim-focus-val">${escapeHTML(focus.event_count)} events</span>
      <span class="sim-focus-sep">·</span>
      <span class="sim-focus-val">${escapeHTML(focus.category)}</span>
      <span class="sim-focus-sep">·</span>
      <span class="sim-focus-val">sev ${escapeHTML(String(focus.severity))}</span>
    </div>`;
  }

  // Causal chain
  if (chain.length) {
    html += '<div class="sim-section-hd">Causal Chain</div>';
    chain.forEach((layer, i) => {
      const col = LAYER_COLORS[i] || "#7fa8bf";
      const pct = Math.min(100, layer.pressure || 50);
      const systems = Array.isArray(layer.systems_impact) ? layer.systems_impact : [];
      const actors  = Array.isArray(layer.actors) ? layer.actors : [];

      html += `<div class="sim-layer">
        <div class="sim-layer-hd">
          <span class="sim-layer-label" style="color:${col}">${escapeHTML(layer.label)}</span>
          <span class="sim-layer-pct" style="color:${col}">${pct}</span>
        </div>
        <div class="sim-track"><div class="sim-fill" style="width:${pct}%;background:${col}"></div></div>
        <p class="sim-layer-headline">${escapeHTML(layer.headline || "")}</p>
        <p class="sim-layer-detail">${escapeHTML(layer.detail || "")}</p>`;

      if (systems.length) {
        html += '<div class="sim-systems">' + systems.map(s => {
          const sc = PRESSURE_COLORS[s.pressure] || "#7fa8bf";
          return `<div class="sim-sys">
            <div class="sim-sys-hd">
              <span class="sim-sys-name" style="color:${sc}">${escapeHTML(s.label)}</span>
              <span class="sim-sys-pressure" style="color:${sc}">${escapeHTML(s.pressure||"")}</span>
            </div>
            <p class="sim-sys-desc">${escapeHTML(s.description)}</p>
          </div>`;
        }).join("") + "</div>";
      }

      if (actors.length) {
        html += '<div class="sim-actors">' + actors.map(a => `<span class="sim-actor">${escapeHTML(a)}</span>`).join("") + "</div>";
      }

      html += "</div>";
    });
  }

  // Branches
  if (branches.length) {
    html += '<div class="sim-section-hd" style="margin-top:16px">Scenario Branches</div>';
    branches.forEach((b, i) => {
      const col = b.colour || (i===0?"var(--danger)":i===1?"var(--accent)":"var(--positive)");
      const pct = b.probability_pct ?? Math.round((b.probability||0)*100);
      const wf  = Array.isArray(b.watch_for) ? b.watch_for : [];
      html += `<div class="sim-branch${i===0?" is-lead":""}">
        <div class="sim-branch-hd">
          <span class="sim-branch-name" style="color:${col}">${escapeHTML(b.label||b.name||"")}</span>
          <div class="sim-prob-wrap">
            <div class="sim-prob-bar"><div class="sim-prob-fill" style="width:${pct}%;background:${col}"></div></div>
            <span class="sim-prob-pct" style="color:${col}">${pct}%</span>
          </div>
        </div>
        <p class="sim-branch-headline">${escapeHTML(b.headline||"")}</p>
        <p class="sim-branch-detail">${escapeHTML(b.detail||b.text||"")}</p>
        ${b.second_order ? `<div class="sim-second-order"><strong>Second-order effects</strong>${escapeHTML(b.second_order)}</div>` : ""}
        ${wf.length ? `<div class="sim-watch"><div class="sim-watch-title">Watch for</div><ul>${wf.map(w=>`<li>${escapeHTML(w)}</li>`).join("")}</ul></div>` : ""}
      </div>`;
    });
  }

  // Scenario readout
  if (state.simulation?.scenario_readout) {
    html += `<div class="sim-readout">${escapeHTML(state.simulation.scenario_readout)}</div>`;
  }

  el.simOutput.innerHTML = html;
}

function renderAll() {
  renderTopBar();
  renderStats();
  renderNewsFeed();
  renderEventDetail();
  renderFocusAnchor();
  renderTrends();
  renderSimOutput();
}
