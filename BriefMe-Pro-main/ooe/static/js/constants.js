/* ── BriefMe constants — loaded first ───────────────── */
/* ─── Constants ─────────────────────────────────────────────────────────── */
const CATEGORY_META = {
  disaster:       { label: "Disaster",       color: "#ff8d77" },
  weather:        { label: "Weather",        color: "#76d5ff" },
  environment:    { label: "Environment",    color: "#55efc2" },
  climate:        { label: "Climate",        color: "#55efc2" },
  politics:       { label: "Politics",       color: "#ffd16d" },
  economy:        { label: "Economy",        color: "#ffbc63" },
  crime:          { label: "Crime",          color: "#ff6c7e" },
  conflict:       { label: "Conflict",       color: "#ff726b" },
  health:         { label: "Health",         color: "#b5ff97" },
  social:         { label: "Social",         color: "#8fd7ff" },
  culture:        { label: "Culture",        color: "#ffd7b7" },
  technology:     { label: "Technology",     color: "#78e9ff" },
  tech:           { label: "Technology",     color: "#78e9ff" },
  infrastructure: { label: "Infrastructure", color: "#a0b8ff" },
  trade:          { label: "Trade",          color: "#ffdb80" },
  other:          { label: "Other",          color: "#87d8ff" },
};

const SYSTEM_META = {
  geopolitics:   { label: "Geopolitics",    color: "#ff8d77" },
  governance:    { label: "Governance",     color: "#ffd16d" },
  markets:       { label: "Markets",        color: "#ffbc63" },
  technology:    { label: "Technology",     color: "#78e9ff" },
  civil_stability:{ label: "Civil Stability",color: "#ff6c7e" },
  public_health: { label: "Public Health",  color: "#b5ff97" },
  environment:   { label: "Environment",    color: "#55efc2" },
  civic_pressure:{ label: "Civic Pressure", color: "#8fd7ff" },
  infrastructure:{ label: "Infrastructure", color: "#a0b8ff" },
  social_climate:{ label: "Social Climate", color: "#ffd7b7" },
  general:       { label: "General Pressure",color: "#87d8ff" },
};

const SYSTEM_BY_CATEGORY = {
  conflict: "geopolitics", politics: "governance", economy: "markets",
  technology: "technology", tech: "technology", crime: "civil_stability",
  health: "public_health", disaster: "environment", weather: "environment",
  environment: "environment", climate: "environment", social: "civic_pressure",
  infrastructure: "infrastructure", culture: "social_climate", trade: "markets", other: "general",
};

const LAYER_META = [
  { id: "geospatial", label: "Geography",     color: "#76d5ff" },
  { id: "structural", label: "Infrastructure",color: "#a0b8ff" },
  { id: "activity",   label: "Movement",      color: "#55efc2" },
  { id: "event",      label: "Incident",      color: "#ff8d77" },
  { id: "predictive", label: "Forecast",      color: "#ffd16d" },
];

function metaForCategory(cat = "other") { return CATEGORY_META[cat] || CATEGORY_META.other; }
function systemForCategory(cat = "other") { return SYSTEM_BY_CATEGORY[cat] || "general"; }
function metaForSystem(sys = "general") { return SYSTEM_META[sys] || SYSTEM_META.general; }

