from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent

# Load .env file from the project root (sibling of ooe/ directory)
_dotenv_loaded = load_dotenv(PROJECT_ROOT / "ooe.env")
DEFAULT_RUNTIME_DIR = Path.home() / "Library" / "Application Support" / "OOE"
DEFAULT_CONFIG_PATH = DEFAULT_RUNTIME_DIR / "ooe_config.json"

DEFAULT_SETTINGS: dict[str, Any] = {
    "server": {
        "host": "127.0.0.1",
        "port": 8787,
    },
    "collector": {
        "poll_interval_seconds": 300,
        "request_timeout_seconds": 20,
        "prediction_refresh_seconds": 300,
        "history_days": 21,
        "live_window_hours": 96,
        "telemetry_poll_seconds": 60,
    },
    "notifications": {
        "enabled": False,
        "severity_threshold": 82,
        "personal_impact_threshold": 72,
        "max_notifications_per_hour": 8,
    },
    "api": {
        "stream_keepalive_seconds": 15,
    },
    "trend_predictor": {
        "window_hours": 72,
        "min_signals": 2,
        "max_items": 10,
    },
    "ewie": {
        "lookback_hours": 120,
        "source_trust": {
            "max_items": 12,
        },
        "narrative": {
            "recent_window_hours": 24,
            "prior_window_hours": 120,
            "min_topic_events": 3,
            "max_items": 12,
        },
        "global_change": {
            "min_topic_events": 3,
            "min_regions": 2,
            "max_items": 10,
        },
        "opportunity": {
            "min_score": 55,
            "max_items": 10,
        },
    },
    "telemetry": {
        "foreground_app": {
            "enabled": True,
            "sample_interval_seconds": 60,
        },
        "session_activity": {
            "enabled": True,
            "sample_interval_seconds": 60,
        },
        "microphone_features": {
            "enabled": False,
            "model_path": "models/vosk-model-small-en-us-0.15",
            "sample_rate": 16000,
            "block_size": 512,
        },
        "store_raw_audio": False,
    },
    # AI provider — defaults to Groq which has a generous free tier.
    # Get your free API key at https://console.groq.com (no credit card needed).
    # Set GROQ_API_KEY in ooe.env, then restart the server.
    "ai": {
        "enabled": True,
        "provider": "groq",
        "api_base_url": "https://api.groq.com/openai/v1/chat/completions",
        "api_key_env": "GROQ_API_KEY",
        "model": "llama-3.3-70b-versatile",
        "timeout_seconds": 20,
    },
    "verification": {
        "require_verified": True,
        "minimum_trusted_sources": 1,
        "max_age_hours": {
            "default": 96,
            "official": 120,
            "trusted": 36,
            "local": 24,
            "unknown": 36,
        },
    },
    "personal_profile": {
        "baseline_state": {
            "stress": 34,
            "focus": 66,
            "mood": 62,
        },
        # Neutral starting weights — personalisation kicks in as users interact.
        # Onboarding wizard sets these to match the user's chosen regions/topics.
        "region_interest_weights": {},
        "category_interest_weights": {
            "conflict":      1.0,
            "politics":      1.0,
            "economy":       1.0,
            "disaster":      1.0,
            "health":        1.0,
            "technology":    1.0,
            "climate":       1.0,
            "trade":         1.0,
            "crime":         1.0,
            "environment":   1.0,
            "infrastructure":1.0,
            "weather":       1.0,
            "social":        1.0,
            "culture":       1.0,
            "other":         1.0,
        },
        "keyword_interest_weights": {},
    },
    # ── Transactional email (verification, password reset, welcome) ──────────
    # Free options:
    #   • Gmail SMTP: smtp_host=smtp.gmail.com, port=587, use an App Password
    #     (myaccount.google.com → Security → App passwords), limit 500/day
    #   • Resend.com:  smtp_host=smtp.resend.com, port=465, free 3000/month
    #   • Mailgun:     smtp_host=smtp.mailgun.org, port=587, free 1000/month
    # Set env vars SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD to configure.
    "email": {
        "enabled": False,
        "smtp_host":     "",
        "smtp_port":     587,
        "smtp_user":     "",
        "smtp_password": "",
        "from_address":  "",
        "from_name":     "BriefMe Pro",
    },
    "sources": [
        {
            "enabled": True,
            "kind": "rss",
            "name": "GDACS Alerts",
            "url": "https://www.gdacs.org/xml/rss.xml",
            "category": "disaster",
            "source_tier": "official",
            "limit": 60,
        },
        {
            "enabled": True,
            "kind": "usgs_geojson",
            "name": "USGS Significant Earthquakes",
            "url": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_day.geojson",
        },
        {
            "enabled": True,
            "kind": "eonet_open",
            "name": "NASA EONET Open Events",
            "url": "https://eonet.gsfc.nasa.gov/api/v3/events?status=open&limit=25",
        },
        {
            "enabled": True,
            "kind": "rss",
            "name": "BBC World",
            "url": "https://feeds.bbci.co.uk/news/world/rss.xml",
            "source_tier": "trusted",
            "allowed_domains": ["bbc.com"],
            "limit": 35,
        },
        {
            "enabled": True,
            "kind": "rss",
            "name": "BBC Business",
            "url": "https://feeds.bbci.co.uk/news/business/rss.xml",
            "category": "economy",
            "source_tier": "trusted",
            "allowed_domains": ["bbc.com"],
            "limit": 25,
        },
        {
            "enabled": True,
            "kind": "rss",
            "name": "BBC Science & Environment",
            "url": "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
            "source_tier": "trusted",
            "allowed_domains": ["bbc.com"],
            "limit": 25,
        },
        {
            "enabled": True,
            "kind": "rss",
            "name": "NPR World",
            "url": "https://feeds.npr.org/1004/rss.xml",
            "source_tier": "trusted",
            "allowed_domains": ["npr.org"],
            "limit": 25,
        },
        {
            "enabled": True,
            "kind": "rss",
            "name": "DW World",
            "url": "https://rss.dw.com/rdf/rss-en-world",
            "source_tier": "trusted",
            "allowed_domains": ["dw.com"],
            "limit": 25,
        },
        {
            "enabled": True,
            "kind": "rss",
            "name": "Guardian World",
            "url": "https://www.theguardian.com/world/rss",
            "source_tier": "trusted",
            "allowed_domains": ["theguardian.com"],
            "limit": 25,
        },
        {
            "enabled": True,
            "kind": "rss",
            "name": "Al Jazeera",
            "url": "https://www.aljazeera.com/xml/rss/all.xml",
            "source_tier": "trusted",
            "allowed_domains": ["aljazeera.com"],
            "limit": 25,
        },
        {
            "enabled": True,
            "kind": "rss",
            "name": "CNN World",
            "url": "http://rss.cnn.com/rss/edition_world.rss",
            "source_tier": "trusted",
            "allowed_domains": ["cnn.com"],
            "limit": 25,
        },
        {
            "enabled": True,
            "kind": "rss",
            "name": "TIME",
            "url": "https://time.com/feed/",
            "source_tier": "trusted",
            "allowed_domains": ["time.com"],
            "limit": 25,
        },
        {
            "enabled": True,
            "kind": "rss",
            "name": "The Hindu International",
            "url": "https://www.thehindu.com/news/international/feeder/default.rss",
            "source_tier": "trusted",
            "allowed_domains": ["thehindu.com"],
            "limit": 25,
        },
        {
            "enabled": True,
            "kind": "rss",
            "name": "India Today World",
            "url": "https://www.indiatoday.in/rss/1206577",
            "source_tier": "trusted",
            "allowed_domains": ["indiatoday.in"],
            "limit": 25,
        },
        {
            "enabled": True,
            "kind": "rss",
            "name": "NYPost World",
            "url": "https://nypost.com/world-news/feed/",
            "source_tier": "trusted",
            "allowed_domains": ["nypost.com"],
            "limit": 25,
        },
        {
            "enabled": True,
            "kind": "rss",
            "name": "Sky News World",
            "url": "https://feeds.skynews.com/feeds/rss/world.xml",
            "source_tier": "trusted",
            "allowed_domains": ["news.sky.com", "sky.com"],
            "limit": 20,
        },
        {
            "enabled": True,
            "kind": "rss",
            "name": "CNBC World",
            "url": "https://www.cnbc.com/id/100727362/device/rss/rss.html",
            "source_tier": "trusted",
            "allowed_domains": ["cnbc.com"],
            "limit": 20,
        },
        {
            "enabled": True,
            "kind": "rss",
            "name": "ABC International",
            "url": "https://abcnews.go.com/abcnews/internationalheadlines",
            "source_tier": "trusted",
            "allowed_domains": ["abcnews.com", "abcnews.go.com"],
            "limit": 20,
        },
        {
            "enabled": True,
            "kind": "rss",
            "name": "CBS World",
            "url": "https://www.cbsnews.com/latest/rss/world",
            "source_tier": "trusted",
            "allowed_domains": ["cbsnews.com"],
            "limit": 20,
        },
        {
            "enabled": True,
            "kind": "rss",
            "name": "France24",
            "url": "https://www.france24.com/en/rss",
            "source_tier": "trusted",
            "allowed_domains": ["france24.com"],
            "limit": 20,
        },
        {
            "enabled": True,
            "kind": "rss",
            "name": "Bloomberg World",
            "url": "https://feeds.bloomberg.com/markets/news.rss",
            "source_tier": "trusted",
            "allowed_domains": ["bloomberg.com"],
            "limit": 20,
        },
        {
            "enabled": True,
            "kind": "rss",
            "name": "Fox World",
            "url": "https://moxie.foxnews.com/google-publisher/world.xml",
            "source_tier": "trusted",
            "allowed_domains": ["foxnews.com"],
            "limit": 20,
        },
        {
            "enabled": True,
            "kind": "rss",
            "name": "NBC World",
            "url": "https://feeds.nbcnews.com/nbcnews/public/world",
            "source_tier": "trusted",
            "allowed_domains": ["nbcnews.com"],
            "limit": 20,
        },
        {
            "enabled": True,
            "kind": "rss",
            "name": "TOI World",
            "url": "https://timesofindia.indiatimes.com/rssfeeds/296589292.cms",
            "source_tier": "trusted",
            "allowed_domains": ["timesofindia.indiatimes.com"],
            "limit": 20,
        },
        {
            "enabled": True,
            "kind": "rss",
            "name": "Indian Express World",
            "url": "https://indianexpress.com/section/world/feed/",
            "source_tier": "trusted",
            "allowed_domains": ["indianexpress.com"],
            "limit": 20,
        },
        {
            "enabled": True,
            "kind": "rss",
            "name": "Hindustan Times World",
            "url": "https://www.hindustantimes.com/feeds/rss/world-news/rssfeed.xml",
            "source_tier": "trusted",
            "allowed_domains": ["hindustantimes.com"],
            "limit": 20,
        },
        {
            "enabled": True,
            "kind": "rss",
            "name": "Financial Times World",
            "url": "https://www.ft.com/world?format=rss",
            "source_tier": "trusted",
            "allowed_domains": ["ft.com"],
            "limit": 20,
        },
        {
            "enabled": True,
            "kind": "rss",
            "name": "The Atlantic",
            "url": "https://www.theatlantic.com/feed/all/",
            "source_tier": "trusted",
            "allowed_domains": ["theatlantic.com"],
            "limit": 20,
        },
        {
            "enabled": True,
            "kind": "rss",
            "name": "Economist International",
            "url": "https://www.economist.com/international/rss.xml",
            "source_tier": "trusted",
            "allowed_domains": ["economist.com"],
            "limit": 20,
        },
        # ──────────────────────────────────────────────────────────────
        # Specialised feeds — differentiation from generic news aggregators
        # ──────────────────────────────────────────────────────────────
        {
            "enabled": True,
            "kind": "rss",
            "name": "NOAA Tsunami Warnings (Pacific)",
            "url": "https://www.tsunami.gov/events/xml/PHEBAtom.xml",
            "category": "disaster",
            "source_tier": "official",
            "allowed_domains": ["tsunami.gov"],
            "limit": 30,
        },
        {
            "enabled": True,
            "kind": "rss",
            "name": "ReliefWeb Disasters",
            "url": "https://reliefweb.int/disasters/rss.xml",
            "category": "disaster",
            "source_tier": "official",
            "allowed_domains": ["reliefweb.int"],
            "limit": 30,
        },
        {
            "enabled": True,
            "kind": "rss",
            "name": "ReliefWeb Updates",
            "url": "https://reliefweb.int/updates/rss.xml?advanced-search=%28F10%29",
            "category": "humanitarian",
            "source_tier": "official",
            "allowed_domains": ["reliefweb.int"],
            "limit": 25,
        },
        {
            "enabled": False,
            "kind": "directory_inbox",
            "name": "Local Inbox",
            "path": "data/inbox",
        },
    ],
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
            continue
        merged[key] = value
    return merged


def materialize_default_config(config_path: Path) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if config_path.exists():
        return
    config_path.write_text(json.dumps(DEFAULT_SETTINGS, indent=2) + "\n", encoding="utf-8")


def resolve_resource_path(path_value: str, config_path: Path) -> Path:
    path = Path(path_value).expanduser()
    if path.is_absolute():
        return path

    candidates = [
        config_path.parent / path,
        PACKAGE_ROOT / path,
        PROJECT_ROOT / path,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return (config_path.parent / path).resolve()


def load_settings(config_path: str | None = None) -> dict[str, Any]:
    resolved_config_path = Path(config_path).expanduser().resolve() if config_path else DEFAULT_CONFIG_PATH
    materialize_default_config(resolved_config_path)

    loaded = json.loads(resolved_config_path.read_text(encoding="utf-8"))
    settings = deep_merge(DEFAULT_SETTINGS, loaded)
    runtime_dir = resolved_config_path.parent
    db_path_value = settings.get("db_path", "ooe.db")
    db_path = resolve_resource_path(str(db_path_value), resolved_config_path)

    settings["config_path"] = str(resolved_config_path)
    settings["runtime_dir"] = str(runtime_dir)
    settings["db_path"] = str(db_path)

    # Railway / Render / Heroku inject PORT env var — honour it
    import os

    # DB_PATH env var lets Railway/Docker point the database at a persistent volume
    env_db_path = os.environ.get("DB_PATH", "").strip()
    if env_db_path:
        settings["db_path"] = env_db_path

    env_port = os.environ.get("PORT")
    if env_port:
        settings.setdefault("server", {})["port"] = int(env_port)
        settings["server"]["host"] = "0.0.0.0"

    # OOE_APP_URL env var overrides config file (used for tunnel/staging URLs)
    # Treat known placeholder values as "unset" so Railway/env-var detection
    # can override them. Without this, an example config that ships with
    # "app_url": "https://yourdomain.com" silently breaks every OAuth flow
    # because the server tells Google to redirect to yourdomain.com.
    _PLACEHOLDER_URLS = {
        "https://yourdomain.com",
        "https://your-domain.com",
        "https://example.com",
        "http://localhost:8787",  # dev default
        "http://127.0.0.1:8787",
    }
    current_app_url = settings.get("auth", {}).get("app_url", "").strip()
    if current_app_url in _PLACEHOLDER_URLS:
        settings.setdefault("auth", {})["app_url"] = ""

    # Explicit env var wins over everything
    env_app_url = os.environ.get("OOE_APP_URL", "").strip()
    if env_app_url:
        settings.setdefault("auth", {})["app_url"] = env_app_url

    # Railway injects RAILWAY_PUBLIC_DOMAIN; use it whenever app_url is still
    # empty or points at a known placeholder.
    railway_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
    if railway_domain:
        existing = settings.get("auth", {}).get("app_url", "").strip()
        if not existing or existing in _PLACEHOLDER_URLS:
            settings.setdefault("auth", {})["app_url"] = f"https://{railway_domain}"

    # SMTP configuration from environment variables (takes precedence over config file)
    smtp_host = os.environ.get("SMTP_HOST", "")
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_password = os.environ.get("SMTP_PASSWORD", "")
    smtp_port_str = os.environ.get("SMTP_PORT", "")
    if smtp_host and smtp_user:
        settings.setdefault("email", {})["smtp_host"]     = smtp_host
        settings.setdefault("email", {})["smtp_user"]     = smtp_user
        settings.setdefault("email", {})["smtp_password"] = smtp_password
        settings.setdefault("email", {})["enabled"]       = True
        if smtp_port_str.isdigit():
            settings.setdefault("email", {})["smtp_port"] = int(smtp_port_str)
        smtp_from_addr = os.environ.get("SMTP_FROM_ADDRESS", "")
        smtp_from_name = os.environ.get("SMTP_FROM_NAME", "")
        if smtp_from_addr:
            settings.setdefault("email", {})["from_address"] = smtp_from_addr
        elif not settings.get("email", {}).get("from_address"):
            settings.setdefault("email", {})["from_address"] = smtp_user
        if smtp_from_name:
            settings.setdefault("email", {})["from_name"] = smtp_from_name

    # Sentry DSN
    if os.environ.get("SENTRY_DSN"):
        settings.setdefault("monitoring", {})["sentry_dsn"] = os.environ["SENTRY_DSN"]

    # Google OAuth — env vars unlock the Google sign-in button on the login page.
    # Get credentials at https://console.cloud.google.com/apis/credentials
    google_client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    google_client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    if google_client_id:
        settings.setdefault("auth", {})["google_client_id"] = google_client_id
    if google_client_secret:
        settings.setdefault("auth", {})["google_client_secret"] = google_client_secret

    # Apple Sign-In — optional, mirrors Google
    apple_client_id = os.environ.get("APPLE_CLIENT_ID", "").strip()
    apple_team_id = os.environ.get("APPLE_TEAM_ID", "").strip()
    apple_key_id = os.environ.get("APPLE_KEY_ID", "").strip()
    apple_private_key = os.environ.get("APPLE_PRIVATE_KEY", "").strip()
    if apple_client_id:
        settings.setdefault("auth", {})["apple_client_id"] = apple_client_id
    if apple_team_id:
        settings.setdefault("auth", {})["apple_team_id"] = apple_team_id
    if apple_key_id:
        settings.setdefault("auth", {})["apple_key_id"] = apple_key_id
    if apple_private_key:
        settings.setdefault("auth", {})["apple_private_key"] = apple_private_key

    return settings
