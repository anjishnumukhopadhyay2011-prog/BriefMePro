/* ── Global state, DOM element refs, runtime vars ────── */
/* ─── State ─────────────────────────────────────────────────────────────── */
const state = {
  observedEvents: [],
  predictedEvents: [],
  events: [],
  trends: [],
  metrics: null,

  selectedEvent: null,
  selectedEventDetail: null,
  filters: { category: "", search: "", max_age_hours: 96, severity_min: 0, includePredicted: false },
  timelineValue: 52,
  layerSplit: 40,
  viewMode: "globe", // "globe" | "map"
  viewScope: "global", // "global" | "native"
  nativeCountry: null, // { name, lat, lon, tz }
  globe: {
    rotationY: 0.56, rotationX: -0.18,
    targetRotationY: 0.56, targetRotationX: -0.18,
    zoom: 1.02, targetZoom: 1.02,
    dragging: false, dragDistance: 0,
    velocityY: 0, velocityX: 0,
    renderedPoints: [],
    countryPolygons: [], statePolygons: [], countryBorders: [],
    stars: [], width: 0, height: 0,
  },
  map: {
    offsetX: 0, offsetY: 0,
    targetOffsetX: 0, targetOffsetY: 0,
    zoom: 1.0, targetZoom: 1.0,
    dragging: false, dragDistance: 0,
    renderedPoints: [],
  },
};

/* ─── Elements ──────────────────────────────────────────────────────────── */
const el = {
  globeCanvas:      document.getElementById("globeCanvas"),
  newsFeed:         document.getElementById("newsFeed"),
  serverBadge:      document.getElementById("serverBadge"),
  eventCount:       document.getElementById("eventCount"),
  topStress:        document.getElementById("topStress"),
  topHotspot:       document.getElementById("topHotspot"),
  feedSearch:       document.getElementById("feedSearch"),
  filterRow:        document.getElementById("filterRow"),
  focusAnchor:      document.getElementById("focusAnchor"),
  timelineLabel:    document.getElementById("timelineLabel"),
  statsBar:         document.getElementById("statsBar"),
  eventDetail:      document.getElementById("eventDetail"),
  trendField:       document.getElementById("trendField"),
  rightPanel:       document.getElementById("rightPanel"),
  workspace:        document.getElementById("workspace"),
  openPanelBtn:     document.getElementById("openPanelBtn"),
  closePanelBtn:    document.getElementById("closePanelBtn"),
  refreshBtn:       document.getElementById("refreshBtn"),
  mapModeBtn:       document.getElementById("mapModeBtn"),
  scopeBtn:         document.getElementById("scopeBtn"),
  scopeLabel:       document.getElementById("scopeLabel"),
  signOutBtn:       document.getElementById("signOutBtn"),
  userAvatar:       document.getElementById("userAvatar"),
  userName:         document.getElementById("userName"),
};

const ctx = el.globeCanvas.getContext("2d");

/* ─── Misc runtime vars ─────────────────────────────────────────────────── */
let animHandle = 0;
let lastPointer = null;
let liveStream = null;
let lastRevision = -1;
let loadPromise = null;
let retryTimer = null;
let failCount = 0;
let lastSyncAt = 0;
