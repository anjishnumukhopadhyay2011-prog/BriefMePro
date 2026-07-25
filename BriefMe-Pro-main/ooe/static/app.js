/*
 * app.js — entry point (boot loader only)
 *
 * All logic has been split into focused modules under static/js/:
 *   constants.js  — CATEGORY_META, SYSTEM_META, colour maps
 *   state.js      — global state object, DOM element refs, runtime vars
 *   utils.js      — pure utility functions (formatting, escaping, etc.)
 *   api.js        — backend API calls, connection badge, retry logic
 *   data.js       — data loading (events, trends, intelligence)
 *   globe.js      — WebGL/canvas globe + flat-map rendering engine
  *   feed.js       — news feed, event detail panel, feedback
 *   ui.js         — controls, SSE live stream, user identity, app boot
 *
 * index.html loads the modules in order; this file is kept for any legacy
 * references and to trigger the DOMContentLoaded boot sequence.
 */
