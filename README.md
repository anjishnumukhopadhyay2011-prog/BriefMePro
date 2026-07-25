BriefMe Pro

Live geopolitical event intelligence, mapped to the world. Pulls from RSS feeds and disaster/earthquake APIs (BBC, Reuters-adjacent sources, GDACS, USGS, NASA EONET, ReliefWeb, and more), scores and deduplicates events, and visualizes them on an interactive globe with a live news feed, filters, and a severity/stress dashboard.

Backend is pure Python standard library (http.server — no Flask/Django). Frontend is vanilla HTML/CSS/JS with a hand-rolled canvas globe (no Three.js or other 3D library).

Requirements
Python 3.9+
pip3 install python-dotenv certifi
Setup
Clone the repo and enter the app folder:
bash
   git clone https://github.com/anjishnumukhopadhyay2011-prog/BriefMePro.git
   cd BriefMePro/BriefMe-Pro-main
Install dependencies:
bash
   pip3 install python-dotenv certifi
Create run.py in BriefMe-Pro-main/ (this is the entry point — the repo doesn't ship one):
python
   import os
   import certifi

   # Make sure Python trusts a real certificate bundle. Some Python installs
   # (notably the one bundled with Xcode Command Line Tools on macOS) don't
   # ship their own CA bundle, which causes every HTTPS fetch (news feeds,
   # etc.) to fail with CERTIFICATE_VERIFY_FAILED.
   os.environ.setdefault("SSL_CERT_FILE", certifi.where())
   os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

   from ooe.config import load_settings
   from ooe.server import serve

   if __name__ == "__main__":
       settings = load_settings()
       serve(settings)

Run it:
bash
   python3 run.py

Open http://127.0.0.1:8787 in your browser.

Known issues & fixes already applied in this repo

If you're setting this up fresh and something's broken, check these first — they were all found and patched during initial setup:

ooe/server.py — ingest_once() used to call a method, self._refresh_predictions(), that was never defined anywhere in the class. It crashed the background ingestion thread on every cycle (the web server itself stayed up, but background refresh died). Fixed by adding a no-op stub for _refresh_predictions on OOERuntime.
ooe/static/js/ui.js — attachControls() referenced two DOM elements, el.simulateBtn and el.scenarioDecision, that don't exist anywhere in index.html or in the el object in state.js. This threw an uncaught TypeError on page load, which silently killed boot() before it ever called loadData() — meaning the page would render its shell but the "connecting" badge would never resolve and no data would load. Fixed by removing the two dead addEventListener calls.
ooe/sources.py — Country/coordinate lookup used to call restcountries.com's /v3.1 API to resolve country names mentioned in article text into capital-city coordinates for the globe. That API version has been permanently shut down; its replacement (/v5) requires a paid/registered API key. The old code silently swallowed the failure (try: resolver = ... except: resolver = None), so every single RSS-sourced event silently got zero coordinates and never appeared on the globe — only events from feeds with their own built-in geo-tags (USGS earthquakes, NASA EONET, GDACS) showed up. Fixed by embedding a static table of ~197 countries/capitals/coordinates directly in the code, removing the network dependency entirely.
ooe/static/js/globe.js — Events are geocoded at country-capital granularity, not city/address level (see above — there's no true geocoding API involved, just country-name text matching). This means many events in the same country would render as perfectly overlapping dots on the exact same pixel. Added a small deterministic jitter (based on a hash of each event's own ID, so it's stable across re-renders) so co-located events fan out visibly instead of stacking on top of each other.
Known limitations (not fully fixed, by design/scope)
Location accuracy is country-level, not city-level. Pins land on a country's capital, not the actual city/region a story is about. True city-level geocoding would require integrating a real geocoding API (e.g. Nominatim/OpenStreetMap or Google Geocoding).
Not every event resolves to a location. The country-name matcher only fires when a country name (or known alias/demonym) appears in the headline or summary — local-interest and crime stories, for instance, often won't. It's also intentionally conservative on a few ambiguous country names (e.g. "Iran" alone won't match without a supporting word like "Tehran" nearby) to avoid false positives.
Runtime/config directory defaults to ~/Library/Application Support/OOE (a macOS-style path). It's created automatically on any OS, just looks a little out of place on Linux/Windows.
Project structure
BriefMe-Pro-main/
├── run.py                  # entry point (create this — see Setup)
├── ooe.env                 # optional: API keys, SMTP config (create this — see Setup)
└── ooe/
    ├── server.py           # HTTP server, routing, runtime loop
    ├── config.py           # settings, env var handling, default source list
    ├── sources.py          # RSS/API ingestion + country-coordinate resolution
    ├── scoring.py          # event scoring, dedup, consolidation
    ├── intelligence.py     # AI-powered summarization/enrichment
    ├── storage.py          # SQLite persistence
    ├── auth.py             # auth, JWT, OAuth, payment verification
    ├── mailer.py           # transactional email
    ├── models.py           # data models
    ├── profile_adapter.py  # personalization
    └── static/             # frontend (HTML/CSS/JS), including the globe visualization
