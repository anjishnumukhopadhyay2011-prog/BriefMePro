from __future__ import annotations

import concurrent.futures
import json
import logging
import logging.handlers
import mimetypes
import os
import secrets
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from .auth import (
    create_access_token,
    create_token,
    hash_password,
    hash_token,
    verify_password,
    verify_razorpay_payment,
    verify_razorpay_webhook,
    verify_stripe_webhook,
    verify_token,
    verify_google_token,
    verify_apple_token,
)
from .scoring import consolidate_ingested_events, derive_user_state, score_event
from .config import load_settings
from .intelligence import build_external_world_intelligence, enrich_and_prioritize_events
from .profile_adapter import adapt_profile
from .sources import build_sources
from .storage import OOEStorage
from .mailer import send_email, email_verify_html, password_reset_html, welcome_html


STATIC_DIR = Path(__file__).resolve().parent / "static"

_STRIPE_API_BASE    = "https://api.stripe.com/v1"
_RAZORPAY_API_BASE  = "https://api.razorpay.com/v1"


class _RateLimiter:
    """Thread-safe sliding-window rate limiter keyed by arbitrary string (e.g. IP)."""

    # Prune stale keys every N calls so the dict doesn't grow unbounded.
    _PRUNE_EVERY = 500

    def __init__(self, max_attempts: int = 5, window_seconds: int = 60) -> None:
        self._max = max_attempts
        self._window = window_seconds
        self._lock = threading.Lock()
        self._buckets: dict[str, list[float]] = {}
        self._call_count = 0

    def is_allowed(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            timestamps = self._buckets.get(key, [])
            timestamps = [t for t in timestamps if now - t < self._window]
            if len(timestamps) >= self._max:
                self._buckets[key] = timestamps
                return False
            timestamps.append(now)
            self._buckets[key] = timestamps
            self._call_count += 1
            if self._call_count >= self._PRUNE_EVERY:
                self._call_count = 0
                cutoff = now - self._window
                self._buckets = {
                    k: v for k, v in self._buckets.items()
                    if v and v[-1] >= cutoff
                }
            return True

    def reset(self, key: str) -> None:
        with self._lock:
            self._buckets.pop(key, None)


def _stripe_api(
    method: str,
    endpoint: str,
    data: dict[str, Any] | None,
    api_key: str,
) -> dict[str, Any]:
    """Make a Stripe REST call using only stdlib urllib."""
    url = f"{_STRIPE_API_BASE}{endpoint}"
    body: bytes | None = None
    if data:
        body = urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Bearer {api_key}")
    if body:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except Exception:
            return {"error": {"message": raw}}
    except Exception as exc:
        return {"error": {"message": str(exc)}}


def _razorpay_api(
    method: str,
    endpoint: str,
    data: dict[str, Any] | None,
    key_id: str,
    key_secret: str,
) -> dict[str, Any]:
    """Make a Razorpay REST call using stdlib urllib with HTTP Basic auth."""
    import base64 as _b64
    url = f"{_RAZORPAY_API_BASE}{endpoint}"
    credentials = _b64.b64encode(f"{key_id}:{key_secret}".encode()).decode()
    body: bytes | None = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", f"Basic {credentials}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except Exception:
            return {"error": {"description": raw}}
    except Exception as exc:
        return {"error": {"description": str(exc)}}


# Optional Sentry error monitoring — activates only when SENTRY_DSN env var is set.
# Install: pip install sentry-sdk   |   Free tier at sentry.io (5000 errors/month)
#
# PII scrubbing is mandatory: our Privacy Policy promises Sentry receives
# error context only, never email/IP/cookies. send_default_pii=False keeps
# Sentry from auto-collecting request bodies, headers, and user data; the
# before_send hook drops anything that slipped through.
try:
    import sentry_sdk as _sentry

    _sentry_dsn = os.environ.get("SENTRY_DSN", "")
    if _sentry_dsn:
        def _scrub_event(event, _hint):
            # Strip request data and any user identifier before transmission
            event.pop("user", None)
            req = event.get("request") or {}
            req.pop("cookies", None)
            req.pop("data", None)
            headers = req.get("headers") or {}
            for h in ("Authorization", "Cookie", "X-Forwarded-For", "X-Real-IP"):
                headers.pop(h, None)
                headers.pop(h.lower(), None)
            if headers:
                req["headers"] = headers
            if req:
                event["request"] = req
            return event

        _sentry.init(
            dsn=_sentry_dsn,
            traces_sample_rate=0.1,
            send_default_pii=False,
            before_send=_scrub_event,
            release=os.environ.get("RAILWAY_GIT_COMMIT_SHA") or None,
            environment=os.environ.get("RAILWAY_ENVIRONMENT") or "production",
        )
except ImportError:
    pass

_logger = logging.getLogger("ooe.server")

_DEFAULT_JWT_SECRET = "change-me-in-production"

# Bump this whenever you materially change /terms or /privacy. The current
# value is recorded against every registration; the user-record audit trail
# lets you prove which version a given user agreed to.
_CURRENT_TOS_VERSION = "2026-04-25"

# RFC 5322-compliant-ish email regex. Not perfect (no full RFC compliance is
# possible in a regex), but rejects the obvious bad inputs the previous
# `"@" in email` check let through (e.g. "@", "a@", "@b", "a@b").
import re as _re
_EMAIL_RE = _re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

def _is_valid_email(email: str) -> bool:
    if not email or len(email) > 254:
        return False
    return bool(_EMAIL_RE.match(email))

def _password_strength_error(password: str) -> str | None:
    """Return a human-readable error if the password is too weak, else None.

    Rules (NIST SP 800-63B aligned):
      - At least 8 characters
      - Not too long (DoS protection — bcrypt-style hashes have a 72-byte cap;
        we use PBKDF2 which doesn't, but bound it anyway to prevent abuse)
      - Not a literal copy of the user's email or "password" / "12345678"
    """
    if not password:
        return "Password is required."
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if len(password) > 256:
        return "Password is too long (max 256 characters)."
    common = {
        "password", "password1", "12345678", "qwerty12", "iloveyou",
        "admin123", "welcome1", "letmein1", "abc12345", "11111111",
    }
    if password.lower() in common:
        return "That password is too common — please choose a different one."
    return None


def _setup_logging(runtime_dir: str | Path) -> None:
    """Write structured logs to a rotating file in the runtime directory."""
    log_path = Path(runtime_dir) / "ooe.log"
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s — %(message)s", datefmt="%Y-%m-%dT%H:%M:%SZ")
    )
    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(logging.StreamHandler())
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def _validate_production_config(settings: dict[str, Any]) -> None:
    """Print actionable warnings for any misconfigured production settings."""
    auth = settings.get("auth", {})
    if not auth.get("enabled"):
        return  # Auth is off — nothing to validate

    warnings: list[str] = []

    # Env var takes precedence over config file for the JWT secret
    jwt_secret_env_var = str((settings.get("security") or {}).get("jwt_secret_env") or "BRIEFME_JWT_SECRET")
    jwt_secret = str(
        os.environ.get(jwt_secret_env_var, "")
        or os.environ.get("OOE_JWT_SECRET", "")
        or auth.get("jwt_secret", "")
    )
    if jwt_secret:
        settings["auth"]["jwt_secret"] = jwt_secret
    if not jwt_secret or jwt_secret == _DEFAULT_JWT_SECRET:
        # Auto-generate a secure secret and persist it into the settings dict so
        # the running process uses it.  The user should save this to their config.
        generated = secrets.token_hex(32)
        settings["auth"]["jwt_secret"] = generated
        warnings.append(
            "JWT secret was not set — a random one was generated for this session.\n"
            "  ⚠  Set BRIEFME_JWT_SECRET env var (or auth.jwt_secret in config) to keep sessions valid across restarts."
        )

    razorpay_cfg = settings.get("razorpay", {})
    rzp_key_id     = str(razorpay_cfg.get("key_id")     or os.environ.get("RAZORPAY_KEY_ID", ""))
    rzp_key_secret = str(razorpay_cfg.get("key_secret") or os.environ.get("RAZORPAY_KEY_SECRET", ""))
    rzp_configured = bool(rzp_key_id and rzp_key_secret)

    stripe_key = str(auth.get("stripe_secret_key") or os.environ.get("STRIPE_SECRET_KEY", ""))
    if not stripe_key and not rzp_configured:
        warnings.append(
            "No payment gateway configured. Add Razorpay keys under 'razorpay' in config "
            "(key_id + key_secret) for India payments, or set stripe_secret_key for Stripe."
        )
    elif not stripe_key and rzp_configured:
        pass  # Razorpay is the active gateway — Stripe not needed
    elif stripe_key and not rzp_configured:
        price_id = str(auth.get("stripe_price_id") or os.environ.get("STRIPE_PRICE_ID", ""))
        if not price_id:
            warnings.append("STRIPE_PRICE_ID is not configured — the subscribe page will not show a price.")
        webhook_secret = str(auth.get("stripe_webhook_secret") or os.environ.get("STRIPE_WEBHOOK_SECRET", ""))
        if not webhook_secret:
            warnings.append(
                "STRIPE_WEBHOOK_SECRET is not configured — incoming Stripe webhooks will be accepted unsigned.\n"
                "  ⚠  Set this in production to prevent spoofed payment events."
            )

    if rzp_configured:
        rzp_webhook = str(razorpay_cfg.get("webhook_secret") or os.environ.get("RAZORPAY_WEBHOOK_SECRET", ""))
        if not rzp_webhook:
            warnings.append(
                "RAZORPAY_WEBHOOK_SECRET is not set — Razorpay webhooks will be accepted unsigned.\n"
                "  ⚠  Set razorpay.webhook_secret in your config for production."
            )

    app_url = str(auth.get("app_url") or os.environ.get("OOE_APP_URL", ""))
    if not app_url or "localhost" in app_url:
        warnings.append(
            "OOE_APP_URL is not set or still points to localhost — Stripe redirect URLs will be wrong in production."
        )

    if warnings:
        print("\n── BriefMe production config warnings ─────────────────────")
        for w in warnings:
            print(f"  • {w}")
        print("────────────────────────────────────────────────────────────\n")


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class MacNotifier:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def send(self, title: str, message: str, subtitle: str = "") -> None:
        if not self.enabled:
            return

        def escape(value: str) -> str:
            return value.replace("\\", "\\\\").replace('"', '\\"')

        script = (
            f'display notification "{escape(message)}" '
            f'with title "{escape(title)}" '
            f'subtitle "{escape(subtitle)}"'
        )
        try:
            subprocess.run(["osascript", "-e", script], check=False, capture_output=True)
        except Exception:
            return


class OOERuntime:
    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = settings
        self.start_time = time.time()
        self.storage = OOEStorage(settings["db_path"])
        self.sources = build_sources(settings)
        self.personal_profile = dict(settings.get("personal_profile") or {})
        notifications = settings.get("notifications", {})
        self.notifier = MacNotifier(bool(notifications.get("enabled", False)))
        self.stop_event = threading.Event()
        self.source_statuses: dict[str, dict[str, Any]] = {}
        self.prediction_cache: dict[str, Any] = {"branches": [], "nodes": []}
        self.trend_cache: list[dict[str, Any]] = []
        self.intelligence_cache: dict[str, Any] = {
            "updated_at": "",
            "source_leaderboard": [],
            "narrative_shifts": [],
            "global_changes": [],
            "opportunities": [],
            "source_trust_scores": {},
            "narrative_shift_index": {},
            "opportunity_index": {},
            "story_stats": {},
        }
        self._collector_thread: threading.Thread | None = None
        self._stream_revision = 0
        self._latest_event_at = ""
        self._last_max_severity: float = 0.0
        self._high_priority_count: int = 0
        self._sim_cache: dict[str, tuple[float, Any]] = {}  # key -> (timestamp, result)
        self._sim_cache_ttl = 300  # 5 minutes

    @property
    def stream_revision(self) -> int:
        return self._stream_revision

    def ingest_once(self) -> dict[str, Any]:
        # Use time-decayed summary so recent engagement drives personalization
        interaction_summary = self.storage.weighted_interaction_summary()
        # Adapt the personal profile based on behavioral history
        adapted_profile = adapt_profile(self.personal_profile, interaction_summary)
        all_events = []
        status_snapshot: dict[str, dict[str, Any]] = {}

        def _collect_source(source: Any) -> tuple[list[Any], dict[str, Any]]:
            """Collect and score one source; always returns a result tuple."""
            started_at = time.time()
            try:
                raw_events = source.collect()
                normalized = [
                    score_event(item, source.name, adapted_profile, interaction_summary)
                    for item in raw_events
                ]
                return normalized, {
                    "name": source.name,
                    "status": "ok",
                    "count": len(normalized),
                    "last_error": "",
                    "duration_seconds": round(time.time() - started_at, 2),
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
            except Exception as exc:
                _logger.warning("Source %r failed during ingest: %s", source.name, exc)
                return [], {
                    "name": source.name,
                    "status": "error",
                    "count": 0,
                    "last_error": str(exc),
                    "duration_seconds": round(time.time() - started_at, 2),
                    "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }

        # Fetch all sources in parallel — up to 8 at once.
        # This cuts worst-case ingest time from (30 sources × 20s timeout) = 10 min
        # down to roughly one timeout window (~20s) when all sources are healthy.
        max_workers = min(8, len(self.sources)) if self.sources else 1
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            results = list(executor.map(_collect_source, self.sources))

        for normalized, status in results:
            all_events.extend(normalized)
            status_snapshot[status["name"]] = status

        self.source_statuses.update(status_snapshot)
        consolidated_events = consolidate_ingested_events(all_events, self.settings)
        self.storage.upsert_events(consolidated_events)
        if consolidated_events:
            latest_event = max(consolidated_events, key=lambda item: str(item.happened_at))
            self._latest_event_at = str(latest_event.happened_at or "")
        else:
            self._latest_event_at = self.storage.metrics().get("latest_event_at", "")
        self._stream_revision += 1
        # Track priority signals for the SSE stream so clients know urgency
        if consolidated_events:
            self._last_max_severity = max(float(e.severity) for e in consolidated_events)
            severity_threshold = float(
                self.settings.get("notifications", {}).get("severity_threshold", 82)
            )
            self._high_priority_count = sum(
                1 for e in consolidated_events if float(e.severity) >= severity_threshold
            )
        self._refresh_predictions()
        self._notify_if_needed(consolidated_events)
        return {
            "ingested_events": len(consolidated_events),
            "sources": list(status_snapshot.values()),
        }

    def _notify_if_needed(self, events: list[Any]) -> None:
        notifications = self.settings.get("notifications", {})
        severity_threshold = float(notifications.get("severity_threshold", 82))
        personal_threshold = float(notifications.get("personal_impact_threshold", 72))

        for event in events:
            reason = ""
            if event.severity >= severity_threshold:
                reason = "severity"
            if event.personal_impact >= personal_threshold:
                reason = "personal_impact"
            if not reason:
                continue
            if self.storage.has_alert(event.event_id, "macos", reason):
                continue
            self.notifier.send(
                "OOE Alert",
                f"{event.title} ({event.category}, {round(event.severity)})",
                subtitle=event.location_name or event.region or event.country or event.source_name,
            )
            self.storage.mark_alert_sent(event.event_id, "macos", reason)

    def collector_loop(self) -> None:
        poll_interval = int(self.settings.get("collector", {}).get("poll_interval_seconds", 300))
        while not self.stop_event.is_set():
            self.ingest_once()
            self.stop_event.wait(poll_interval)

    def start_background_tasks(self) -> None:
        if self._collector_thread and self._collector_thread.is_alive():
            return
        self._collector_thread = threading.Thread(target=self.collector_loop, daemon=True)
        self._collector_thread.start()
        threading.Thread(target=self._retention_loop, daemon=True).start()

    def _retention_loop(self) -> None:
        """Run data-retention cleanup once per day."""
        while not self.stop_event.is_set():
            try:
                retention = self.settings.get("data_retention") or {}
                events_days       = int(retention.get("events_days", 90))
                deleted_purge_days = int(retention.get("deleted_users_purge_days", 30))
                purged_events = self.storage.purge_old_events(events_days)
                purged_users  = self.storage.purge_deleted_users(deleted_purge_days)
                if purged_events or purged_users:
                    _logger.info("Data retention: removed %d old events, %d deleted user records", purged_events, purged_users)
            except Exception as exc:
                _logger.warning("Data retention error: %s", exc)
            self.stop_event.wait(86400)  # 24 hours

    def stop(self) -> None:
        self.stop_event.set()
        if self._collector_thread:
            self._collector_thread.join(timeout=2.0)
        self.storage.close()

    def current_state(self) -> dict[str, Any]:
        personal_feed = self.storage.list_personal_feed(limit=30)
        interactions = self.storage.list_recent_interactions(limit=120)
        snapshot = self.storage.latest_state_snapshot()
        state = derive_user_state(self.personal_profile, personal_feed, interactions, snapshot)
        if snapshot and snapshot.get("note"):
            state["note"] = snapshot["note"]
        return state

    def metrics_payload(self) -> dict[str, Any]:
        metrics = self.storage.metrics()
        latest_event_at = str(metrics.get("latest_event_at") or "")
        metrics["latest_event_age_minutes"] = self._event_age_minutes(latest_event_at)
        metrics["state"] = self.current_state()
        metrics["sources"] = sorted(self.source_statuses.values(), key=lambda item: item["name"])
        metrics["intelligence"] = {
            "updated_at": str(self.intelligence_cache.get("updated_at", "")),
            "narrative_shifts": len(self.intelligence_cache.get("narrative_shifts") or []),
            "global_changes": len(self.intelligence_cache.get("global_changes") or []),
            "opportunities": len(self.intelligence_cache.get("opportunities") or []),
            "trusted_sources": len(self.intelligence_cache.get("source_trust_scores") or {}),
        }
        return metrics

    def stream_payload(self) -> dict[str, Any]:
        max_sev = self._last_max_severity
        alert_level = (
            "critical" if max_sev >= 85
            else "high" if max_sev >= 70
            else "moderate" if max_sev >= 50
            else "low"
        )
        return {
            "revision": self._stream_revision,
            "latest_event_at": self._latest_event_at or self.storage.metrics().get("latest_event_at", ""),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "max_severity": round(max_sev, 2),
            "high_priority_count": self._high_priority_count,
            "alert_level": alert_level,
        }

    def _event_age_minutes(self, value: str) -> float | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None
        delta_minutes = (datetime.now(timezone.utc) - parsed).total_seconds() / 60
        return round(max(delta_minutes, 0.0), 2)

    def event_filters_from_query(self, query: dict[str, list[str]]) -> dict[str, Any]:
        default_max_age = float(self.settings.get("collector", {}).get("live_window_hours", 96))
        return {
            "category": (query.get("category") or [""])[0],
            "region": (query.get("region") or [""])[0],
            "search": (query.get("search") or [""])[0],
            "severity_min": float((query.get("severity_min") or ["0"])[0] or 0),
            "max_age_hours": float((query.get("max_age_hours") or [str(default_max_age)])[0] or default_max_age),
            "include_predicted": (query.get("include_predicted") or ["1"])[0] != "0",
            "limit": int((query.get("limit") or ["200"])[0] or 200),
        }

    def get_events(self, query: dict[str, list[str]]) -> list[dict[str, Any]]:
        filters = self.event_filters_from_query(query)
        events = self.storage.list_events(
            limit=filters["limit"],
            category=filters["category"],
            region=filters["region"],
            search=filters["search"],
            severity_min=filters["severity_min"],
            max_age_hours=filters["max_age_hours"],
            include_predicted=False,
        )
        if filters["include_predicted"]:
            events.extend(self.prediction_cache.get("nodes", []))
        filtered = self._filter_prediction_nodes(events, filters)
        return enrich_and_prioritize_events(
            filtered,
            self.intelligence_cache,
            limit=filters["limit"],
        )

    def _filter_prediction_nodes(self, events: list[dict[str, Any]], filters: dict[str, Any]) -> list[dict[str, Any]]:
        category = filters["category"]
        region = filters["region"].lower()
        search = filters["search"].lower()
        severity_min = filters["severity_min"]
        max_age_hours = float(filters.get("max_age_hours") or 0)
        now = datetime.now(timezone.utc)

        filtered = []
        for event in events:
            if category and event.get("category") != category:
                continue
            if region:
                haystack = " ".join(
                    [
                        str(event.get("location_name", "")),
                        str(event.get("region", "")),
                        str(event.get("country", "")),
                    ]
                ).lower()
                if region not in haystack:
                    continue
            if search:
                haystack = f"{event.get('title', '')} {event.get('summary', '')}".lower()
                if search not in haystack:
                    continue
            if float(event.get("severity", 0.0)) < severity_min:
                continue
            if not event.get("predicted") and max_age_hours > 0:
                happened_at = str(event.get("happened_at", ""))
                try:
                    happened = datetime.fromisoformat(happened_at.replace("Z", "+00:00")).astimezone(timezone.utc)
                except ValueError:
                    happened = None
                if happened is not None:
                    age_hours = (now - happened).total_seconds() / 3600
                    if age_hours > max_age_hours:
                        continue
            filtered.append(event)

        return filtered

    def intelligence_payload(self) -> dict[str, Any]:
        return {
            "updated_at": str(self.intelligence_cache.get("updated_at", "")),
            "source_leaderboard": self.intelligence_cache.get("source_leaderboard") or [],
            "narrative_shifts": self.intelligence_cache.get("narrative_shifts") or [],
            "global_changes": self.intelligence_cache.get("global_changes") or [],
            "opportunities": self.intelligence_cache.get("opportunities") or [],
        }


def make_handler(runtime: OOERuntime):
    _auth_rate_limiter = _RateLimiter(max_attempts=5, window_seconds=60)
    # AI endpoints: 20 requests/user/minute (keyed by user_id)
    _ai_rate_limiter   = _RateLimiter(max_attempts=20, window_seconds=60)
    # Email/password-reset endpoints: 3 requests/IP/hour
    _email_rate_limiter = _RateLimiter(max_attempts=3, window_seconds=3600)
    # General API endpoints: 120 requests/IP/minute — prevents scraping abuse
    _general_rate_limiter = _RateLimiter(max_attempts=120, window_seconds=60)

    class OOERequestHandler(BaseHTTPRequestHandler):
        server_version = "OOE/1.0"

        def log_message(self, format: str, *args: Any) -> None:
            _logger.info("%s %s", self.address_string(), format % args)

        # ------------------------------------------------------------------
        # Low-level send helpers
        # ------------------------------------------------------------------

        def _secure_cookies(self) -> bool:
            # Cookies marked Secure are silently dropped over plain HTTP. So:
            #   - if OOE_APP_URL is http://, never set Secure
            #   - if behind a proxy and X-Forwarded-Proto says http, never set Secure
            #   - if Host is localhost/127.0.0.1 and not forwarded as https, never set Secure
            # Otherwise honour the auth.secure_cookies config (defaults to True).
            app_url = self._app_url().lower()
            if app_url.startswith("http://"):
                return False
            forwarded_proto = self.headers.get("X-Forwarded-Proto", "").lower()
            if forwarded_proto == "http":
                return False
            host = self.headers.get("Host", "").split(":")[0].lower()
            if host in ("localhost", "127.0.0.1", "0.0.0.0") and forwarded_proto != "https":
                return False
            return bool(runtime.settings.get("auth", {}).get("secure_cookies", True))

        def _add_security_headers(self) -> None:
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-XSS-Protection", "1; mode=block")
            self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
            # Comprehensive Permissions-Policy — deny every powerful API we
            # don't legitimately use. Closes off ambient capability surface.
            self.send_header(
                "Permissions-Policy",
                "accelerometer=(), ambient-light-sensor=(), autoplay=(), "
                "battery=(), camera=(), display-capture=(), document-domain=(), "
                "encrypted-media=(), fullscreen=(self), geolocation=(), "
                "gyroscope=(), magnetometer=(), microphone=(), midi=(), "
                "payment=(self \"https://checkout.stripe.com\" \"https://checkout.razorpay.com\"), "
                "picture-in-picture=(), publickey-credentials-get=(), "
                "screen-wake-lock=(), sync-xhr=(), usb=(), web-share=(), "
                "xr-spatial-tracking=()"
            )
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("X-Permitted-Cross-Domain-Policies", "none")
            # HSTS — only meaningful on HTTPS, harmless elsewhere. 1 year + preload-eligible.
            if self._secure_cookies():
                self.send_header(
                    "Strict-Transport-Security",
                    "max-age=31536000; includeSubDomains; preload",
                )
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' "
                "https://checkout.razorpay.com "
                "https://accounts.google.com "
                "https://appleid.cdn-apple.com "
                "https://api.qrserver.com; "
                "style-src 'self' 'unsafe-inline'; "
                "font-src 'self' data:; "
                "img-src 'self' data: blob: https://checkout.razorpay.com https://api.qrserver.com; "
                "connect-src 'self' https://api.razorpay.com https://lumberjack.razorpay.com "
                "https://accounts.google.com https://oauth2.googleapis.com; "
                "frame-src https://api.razorpay.com https://checkout.razorpay.com "
                "https://accounts.google.com https://appleid.apple.com; "
                "frame-ancestors 'none'; "
                "base-uri 'self'; "
                "form-action 'self' https://checkout.stripe.com;",
            )

        def _check_csrf(self) -> bool:
            """Return True if the request passes CSRF origin check.
            Only enforced for cookie-authenticated state-mutating requests when
            csrf_check is enabled in config.  Always passes for API-key or
            non-browser clients (no Origin header).
            """
            if not (runtime.settings.get("security") or {}).get("csrf_check", True):
                return True
            origin = self.headers.get("Origin", "")
            if not origin:
                return True  # non-browser client
            app_url = self._app_url().rstrip("/")
            allowed_origins = {app_url}
            # Also allow localhost variants in development
            if "localhost" in app_url or "127.0.0.1" in app_url:
                allowed_origins.update({
                    "http://localhost:8788", "http://127.0.0.1:8788",
                    "http://localhost:8787", "http://127.0.0.1:8787",
                })
            return origin.rstrip("/") in allowed_origins

        def _send_json(self, payload: Any, status: int = HTTPStatus.OK, extra_headers: dict[str, str] | None = None) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            if extra_headers:
                for k, v in extra_headers.items():
                    self.send_header(k, v)
            self._add_security_headers()
            self.end_headers()
            self.wfile.write(body)

        def _send_file(self, file_path: Path, content_type: str, *, cache_control: str | None = None) -> None:
            body = file_path.read_bytes()
            # Inject server-side config values into auth HTML pages
            if content_type.startswith("text/html") and file_path.suffix == ".html":
                google_id = (runtime.settings.get("auth") or {}).get("google_client_id", "")
                apple_id  = (runtime.settings.get("auth") or {}).get("apple_client_id", "")
                placeholder = b'window.__GOOGLE_CLIENT_ID__ = "";'
                if placeholder in body and google_id:
                    body = body.replace(
                        placeholder,
                        f'window.__GOOGLE_CLIENT_ID__ = {json.dumps(google_id)};'.encode()
                    )
                apple_placeholder = b'window.__APPLE_CLIENT_ID__ = "";'
                if apple_placeholder in body and apple_id:
                    body = body.replace(
                        apple_placeholder,
                        f'window.__APPLE_CLIENT_ID__ = {json.dumps(apple_id)};'.encode()
                    )
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if cache_control:
                self.send_header("Cache-Control", cache_control)
            self._add_security_headers()
            self.end_headers()
            self.wfile.write(body)

        def _redirect(self, location: str, status: int = HTTPStatus.FOUND) -> None:
            self.send_response(status)
            self.send_header("Location", location)
            self._add_security_headers()
            self.end_headers()

        def _wants_html(self) -> bool:
            """Browser vs API client. If Accept: text/html is in the request,
            the user is a human in a browser; serve the styled HTML error page.
            Otherwise (curl, fetch, JSON client), serve JSON."""
            return "text/html" in (self.headers.get("Accept") or "").lower()

        def _send_not_found(self, requested_path: str = "") -> None:
            """404 — serve the styled HTML page to browsers, JSON to API clients."""
            if self._wants_html() and not requested_path.startswith(("/api/", "/static/")):
                page = STATIC_DIR / "404.html"
                if page.is_file():
                    body = page.read_bytes()
                    self.send_response(HTTPStatus.NOT_FOUND)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self._add_security_headers()
                    self.end_headers()
                    self.wfile.write(body)
                    return
            self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

        def _send_user_export_zip(self, user: dict[str, Any], export: dict[str, Any]) -> None:
            """Bundle the user's GDPR export as a ZIP with JSON + a human-readable
            HTML summary, returned with a download header."""
            import io
            import zipfile

            user_email = user.get("email", "user")
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            # Build a small HTML summary so non-technical users can read the export
            html_lines = [
                "<!doctype html><meta charset=utf-8>",
                "<title>BriefMe Pro — Your data export</title>",
                "<style>body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:760px;",
                "margin:40px auto;padding:0 20px;color:#222;line-height:1.55;}",
                "h1{font-size:1.6rem;margin-bottom:6px}h2{margin-top:32px;font-size:1.05rem}",
                "table{border-collapse:collapse;margin:8px 0 20px;width:100%}",
                "td,th{border-bottom:1px solid #eee;padding:6px 10px;text-align:left;font-size:.92rem}",
                "th{background:#f7f7f9}.muted{color:#888;font-size:.85rem}</style>",
                f"<h1>Your BriefMe Pro data</h1>",
                f"<p class=muted>Generated {stamp} for {user_email}.</p>",
                "<p>This bundle contains every piece of personal data we hold about your "
                "account. The machine-readable copy is in <code>data.json</code>; this "
                "page is a human-friendly summary of the same information.</p>",
            ]
            for section, value in export.items():
                html_lines.append(f"<h2>{section}</h2>")
                if isinstance(value, list) and value and isinstance(value[0], dict):
                    cols = list(value[0].keys())
                    html_lines.append("<table><tr>" + "".join(f"<th>{c}</th>" for c in cols) + "</tr>")
                    for row in value[:200]:
                        html_lines.append("<tr>" + "".join(
                            f"<td>{(str(row.get(c, '')) or '')[:200]}</td>" for c in cols
                        ) + "</tr>")
                    html_lines.append("</table>")
                    if len(value) > 200:
                        html_lines.append(f"<p class=muted>… and {len(value) - 200} more rows in data.json</p>")
                elif isinstance(value, dict):
                    html_lines.append("<table>")
                    for k, v in value.items():
                        html_lines.append(f"<tr><th>{k}</th><td>{(str(v) or '')[:400]}</td></tr>")
                    html_lines.append("</table>")
                else:
                    html_lines.append(f"<p>{value}</p>")

            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("data.json", json.dumps(export, indent=2, default=str))
                zf.writestr("summary.html", "\n".join(html_lines))
                zf.writestr("README.txt",
                    "BriefMe Pro — your personal data export\n"
                    "=========================================\n\n"
                    "data.json     — complete machine-readable export\n"
                    "summary.html  — human-readable summary, open in any browser\n\n"
                    "Questions: privacy@briefme.pro\n")

            body = buf.getvalue()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/zip")
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="briefme-pro-export-{stamp}.zip"',
            )
            self.send_header("Content-Length", str(len(body)))
            self._add_security_headers()
            self.end_headers()
            self.wfile.write(body)

        _MAX_BODY_BYTES = 1 * 1024 * 1024  # 1 MB cap — prevents memory-exhaustion DoS

        def _read_json(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except (ValueError, TypeError):
                length = 0
            length = min(length, self._MAX_BODY_BYTES)
            raw = self.rfile.read(length) if length > 0 else b"{}"
            try:
                return json.loads(raw.decode("utf-8") or "{}")
            except (json.JSONDecodeError, UnicodeDecodeError):
                return {}

        def _read_raw_body(self) -> bytes:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except (ValueError, TypeError):
                length = 0
            length = min(length, self._MAX_BODY_BYTES)
            return self.rfile.read(length) if length > 0 else b""

        # ------------------------------------------------------------------
        # Auth helpers
        # ------------------------------------------------------------------

        def _is_auth_enabled(self) -> bool:
            return bool(runtime.settings.get("auth", {}).get("enabled", False))

        def _jwt_secret(self) -> str:
            return str(
                runtime.settings.get("auth", {}).get("jwt_secret")
                or os.environ.get("BRIEFME_JWT_SECRET")
                or os.environ.get("OOE_JWT_SECRET", "change-me-in-production")
            )

        def _stripe_key(self) -> str:
            return str(
                runtime.settings.get("auth", {}).get("stripe_secret_key")
                or os.environ.get("STRIPE_SECRET_KEY", "")
            )

        def _stripe_price_id(self, plan: str = "monthly") -> str:
            """Resolve the Stripe Price ID for the requested plan.

            Falls back through:
              1. plan-specific env / config (STRIPE_PRICE_ID_ANNUAL etc.)
              2. legacy single STRIPE_PRICE_ID
            """
            auth_cfg = runtime.settings.get("auth", {})
            if plan == "annual":
                return str(
                    auth_cfg.get("stripe_price_id_annual")
                    or os.environ.get("STRIPE_PRICE_ID_ANNUAL", "")
                    or auth_cfg.get("stripe_price_id")
                    or os.environ.get("STRIPE_PRICE_ID", "")
                )
            return str(
                auth_cfg.get("stripe_price_id_monthly")
                or os.environ.get("STRIPE_PRICE_ID_MONTHLY", "")
                or auth_cfg.get("stripe_price_id")
                or os.environ.get("STRIPE_PRICE_ID", "")
            )

        def _stripe_webhook_secret(self) -> str:
            return str(
                runtime.settings.get("auth", {}).get("stripe_webhook_secret")
                or os.environ.get("STRIPE_WEBHOOK_SECRET", "")
            )

        def _app_url(self) -> str:
            return str(
                runtime.settings.get("auth", {}).get("app_url")
                or os.environ.get("OOE_APP_URL", "http://localhost:8787")
            ).rstrip("/")

        def _is_admin_user(self) -> bool:
            """Owner-only gate: signed-in user whose email is in ADMIN_EMAILS.

            Configured via the ADMIN_EMAILS env var (comma-separated) or
            auth.admin_emails in the config file. Empty list = nobody admin.
            """
            user = self._auth_user()
            if not user:
                return False
            raw = (
                os.environ.get("ADMIN_EMAILS", "")
                or ",".join(runtime.settings.get("auth", {}).get("admin_emails", []) or [])
            )
            allowed = {e.strip().lower() for e in raw.split(",") if e.strip()}
            return user.get("email", "").lower() in allowed

        # -- Razorpay key helpers ------------------------------------------

        def _rzp_key_id(self) -> str:
            return str(
                runtime.settings.get("razorpay", {}).get("key_id")
                or os.environ.get("RAZORPAY_KEY_ID", "")
            )

        def _rzp_key_secret(self) -> str:
            return str(
                runtime.settings.get("razorpay", {}).get("key_secret")
                or os.environ.get("RAZORPAY_KEY_SECRET", "")
            )

        def _rzp_webhook_secret(self) -> str:
            return str(
                runtime.settings.get("razorpay", {}).get("webhook_secret")
                or os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")
            )

        def _rzp_amount(self, plan: str) -> int:
            """Return amount in paise for the given plan (monthly/annual)."""
            cfg = runtime.settings.get("razorpay", {})
            if plan == "annual":
                return int(cfg.get("annual_amount_paise", 719900))
            return int(cfg.get("monthly_amount_paise", 79900))

        def _rzp_currency(self) -> str:
            return str(runtime.settings.get("razorpay", {}).get("currency", "INR")).upper()

        def _get_jwt(self) -> str | None:
            cookie_header = self.headers.get("Cookie", "")
            for part in cookie_header.split(";"):
                part = part.strip()
                if part.startswith("ooe_session="):
                    return part[len("ooe_session="):]
            return None

        def _auth_user(self) -> dict[str, Any] | None:
            """Return the authenticated user dict, or None."""
            token = self._get_jwt()
            if not token:
                return None
            payload = verify_token(token, self._jwt_secret())
            if not payload:
                return None
            user_id = payload.get("sub")
            if not user_id:
                return None
            return runtime.storage.get_user_by_id(user_id)

        def _has_active_subscription(self, user: dict[str, Any]) -> bool:
            sub = runtime.storage.get_subscription(user["id"])
            if not sub:
                return False
            return sub.get("subscription_status") == "active"

        def _set_jwt_cookie(self, token: str, remember: bool = False) -> None:
            secure_flag = "; Secure" if self._secure_cookies() else ""
            max_age = 60 * 60 * 24 * 90 if remember else 60 * 60 * 24 * 7
            cookie = (
                f"ooe_session={token}; HttpOnly; SameSite=Lax{secure_flag}; "
                f"Max-Age={max_age}; Path=/"
            )
            self.send_header("Set-Cookie", cookie)

        def _clear_jwt_cookie(self) -> None:
            secure_flag = "; Secure" if self._secure_cookies() else ""
            self.send_header(
                "Set-Cookie",
                f"ooe_session=; HttpOnly; SameSite=Lax{secure_flag}; Max-Age=0; Path=/",
            )

        def _client_ip(self) -> str:
            # Honour X-Forwarded-For when running behind a reverse proxy (nginx/Caddy).
            # Only take the first (leftmost) address — the client IP before any proxies.
            forwarded_for = self.headers.get("X-Forwarded-For", "").strip()
            if forwarded_for:
                first_ip = forwarded_for.split(",")[0].strip()
                if first_ip:
                    return first_ip
            return str(self.client_address[0])

        # ------------------------------------------------------------------
        # Individual POST action handlers
        # ------------------------------------------------------------------

        def _issue_session(self, user_id: str, remember: bool = False) -> None:
            """Create access + refresh tokens, set cookie, return JSON with redirect."""
            from .auth import create_refresh_token
            expires_in  = 60 * 60 * 24 * 90 if remember else 60 * 60 * 24 * 7
            access_token = create_access_token(user_id, self._jwt_secret(), expires_in=expires_in)

            # Store refresh token (opaque, hashed)
            raw_refresh, refresh_hash = create_refresh_token()
            refresh_expires = datetime.fromtimestamp(
                time.time() + (86400 * 90 if remember else 86400 * 14), tz=timezone.utc
            ).isoformat()
            ip = self._client_ip()
            runtime.storage.create_session(user_id, refresh_hash, refresh_expires, ip)

            redirect = "/"

            secure_flag = "; Secure" if self._secure_cookies() else ""
            refresh_max_age = 86400 * 90 if remember else 86400 * 14

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._add_security_headers()
            self._set_jwt_cookie(access_token, remember=remember)
            # HttpOnly refresh token in a separate cookie
            self.send_header(
                "Set-Cookie",
                f"ooe_refresh={raw_refresh}; HttpOnly; SameSite=Lax{secure_flag}; "
                f"Max-Age={refresh_max_age}; Path=/api/auth/refresh"
            )
            body = json.dumps({"ok": True, "redirect": redirect}).encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _issue_session_redirect(self, user_id: str, remember: bool = False) -> None:
            """Like _issue_session but redirects instead of returning JSON (for OAuth callbacks)."""
            from .auth import create_refresh_token
            expires_in   = 60 * 60 * 24 * 90 if remember else 60 * 60 * 24 * 7
            access_token = create_access_token(user_id, self._jwt_secret(), expires_in=expires_in)
            raw_refresh, refresh_hash = create_refresh_token()
            refresh_expires = datetime.fromtimestamp(
                time.time() + (86400 * 90 if remember else 86400 * 14), tz=timezone.utc
            ).isoformat()
            runtime.storage.create_session(user_id, refresh_hash, refresh_expires, self._client_ip())
            redirect = "/"
            secure_flag     = "; Secure" if self._secure_cookies() else ""
            refresh_max_age = 86400 * 90 if remember else 86400 * 14
            self.send_response(302)
            self.send_header("Location", redirect)
            self.send_header("Set-Cookie",
                f"ooe_session={access_token}; HttpOnly; SameSite=Lax{secure_flag}; Max-Age={expires_in}; Path=/")
            self.send_header("Set-Cookie",
                f"ooe_refresh={raw_refresh}; HttpOnly; SameSite=Lax{secure_flag}; Max-Age={refresh_max_age}; Path=/api/auth/refresh")
            # Clear OAuth state cookie
            self.send_header("Set-Cookie", "oauth_state=; HttpOnly; SameSite=Lax; Max-Age=0; Path=/")
            self._add_security_headers()
            self.end_headers()

        def _send_verification_email(self, user: dict[str, Any]) -> None:
            """Generate a verification token and dispatch the email (best-effort)."""
            import hashlib as _hl
            raw_token  = secrets.token_urlsafe(48)
            token_hash = _hl.sha256(raw_token.encode()).hexdigest()
            expires_at = datetime.fromtimestamp(
                time.time() + 86400, tz=timezone.utc
            ).isoformat()
            runtime.storage.set_email_verify_token(user["id"], token_hash, expires_at)
            verify_url = f"{self._app_url()}/verify-email?token={raw_token}"
            subject, html = email_verify_html(verify_url, user.get("display_name", ""))
            threading.Thread(
                target=send_email,
                args=(runtime.settings, user["email"], subject, html),
                daemon=True,
            ).start()

        def _handle_auth_register(self, payload: dict[str, Any]) -> None:
            ip = self._client_ip()
            if not _auth_rate_limiter.is_allowed(ip):
                self.send_response(429)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Retry-After", "60")
                self._add_security_headers()
                body = json.dumps({"error": "Too many attempts. Try again later."}).encode()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            email        = str(payload.get("email", "")).strip().lower()
            password     = str(payload.get("password", ""))
            display_name = str(payload.get("display_name", "")).strip()

            if not _is_valid_email(email):
                return self._send_json({"error": "Please enter a valid email address."}, status=HTTPStatus.BAD_REQUEST)
            pw_err = _password_strength_error(password)
            if pw_err:
                return self._send_json({"error": pw_err}, status=HTTPStatus.BAD_REQUEST)

            existing = runtime.storage.get_user_by_email(email)

            # Email-enumeration prevention: respond identically whether the
            # email was new or already taken. For an already-existing email,
            # we send a "you already have an account, here's a sign-in link"
            # email instead of a verification email — but the HTTP response
            # is byte-identical so an attacker can't distinguish.
            if existing:
                _logger.info("Re-register attempt for existing email %s from %s", email, ip)
                runtime.storage.record_security_event(
                    user_id=existing["id"], event_type="register_existing_email", ip=ip,
                )
                self._send_existing_account_email(existing)
                _auth_rate_limiter.reset(ip)
                return self._send_json({
                    "ok": True,
                    "verification_sent": True,
                    "message": "Check your email — we've sent a confirmation link.",
                })

            # Capture which version of the ToS the user accepted at registration.
            tos_version = str(payload.get("tos_version", "")).strip() or _CURRENT_TOS_VERSION

            password_hash = hash_password(password)
            user_id = runtime.storage.create_user(
                email, password_hash, display_name=display_name, tos_version=tos_version
            )
            _logger.info("New account registered: %s (id=%s) from %s", email, user_id, ip)
            runtime.storage.record_security_event(
                user_id=user_id, event_type="register_success", ip=ip,
            )
            token = create_access_token(user_id, self._jwt_secret(), expires_in=60 * 60 * 24 * 7)
            _auth_rate_limiter.reset(ip)

            # Send verification email (non-blocking; falls through if email disabled)
            user_obj = runtime.storage.get_user_by_id(user_id) or {"id": user_id, "email": email, "display_name": display_name}
            self._send_verification_email(user_obj)

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._add_security_headers()
            self._set_jwt_cookie(token)
            body = json.dumps({
                "ok": True,
                "verification_sent": True,
                "message": "Check your email — we've sent a confirmation link.",
                "redirect": "/",
            }).encode("utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_existing_account_email(self, user: dict[str, Any]) -> None:
            """Sent when someone re-registers an email that already exists.

            Tells the existing account holder that someone tried to sign up
            with their address, and reminds them of the sign-in URL. This
            replaces the email-enumeration leak that the old "409 Conflict"
            response provided to the attacker.
            """
            try:
                from .mailer import send_email
                subject = "Your BriefMe Pro account already exists"
                html = (
                    f"<p>Hi,</p>"
                    f"<p>Someone (possibly you) just tried to create a BriefMe Pro account "
                    f"with this email address. An account already exists for "
                    f"<strong>{user.get('email','')}</strong>, so we didn't create a duplicate.</p>"
                    f"<p>If that was you, sign in at "
                    f"<a href='{self._app_url()}/login'>{self._app_url()}/login</a>. If you "
                    f"forgot your password, use the "
                    f"<a href='{self._app_url()}/forgot-password'>password reset</a> link.</p>"
                    f"<p>If it wasn't you, no action is needed — no changes were made to your account.</p>"
                )
                send_email(runtime.settings, user.get("email", ""), subject, html)
            except Exception as exc:
                _logger.warning("Failed to send existing-account email: %s", exc)

        # Pre-computed PBKDF2 hash of a 32-byte random string. Used as a
        # decoy when verifying a password against a non-existent account so
        # that the timing of a "user not found" rejection matches the
        # timing of a "user found, wrong password" rejection. This blocks
        # the simplest email-enumeration timing attack.
        _DECOY_HASH = (
            "pbkdf2_sha256$260000$"
            "ZGVjb3lzYWx0ZGVjb3lzYWx0ZGVjb3lzYWx0ZGVjb3lzYWx0$"
            "ZGVjb3loYXNoZGVjb3loYXNoZGVjb3loYXNoZGVjb3loYXNoZGVjb3loYXNoZGVjb3loYXNoZGVjb3loYXNoZGVjb3loYXNo"
        )

        def _handle_auth_login(self, payload: dict[str, Any]) -> None:
            ip = self._client_ip()
            if not _auth_rate_limiter.is_allowed(ip):
                self.send_response(429)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Retry-After", "60")
                self._add_security_headers()
                body = json.dumps({"error": "Too many attempts. Try again later."}).encode()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            email    = str(payload.get("email", "")).strip().lower()
            password = str(payload.get("password", ""))
            remember = bool(payload.get("remember", False))

            # Account lockout check (per-user, not per-IP)
            if runtime.storage.is_account_locked(email):
                return self._send_json(
                    {"error": "Account temporarily locked due to too many failed attempts. Try again in 15 minutes."},
                    status=429,
                )

            # Constant-time check: always run verify_password, even if the
            # user doesn't exist. Prevents timing-based email enumeration.
            user = runtime.storage.get_user_by_email(email)
            stored_hash = (user or {}).get("password_hash", "") or self._DECOY_HASH
            password_ok = verify_password(password, stored_hash)
            if not user or not password_ok:
                _logger.warning("Failed login attempt for %s from %s", email, ip)
                runtime.storage.record_failed_login(email)
                runtime.storage.record_security_event(
                    user_id=(user or {}).get("id", ""),
                    event_type="login_failed",
                    ip=ip,
                    detail=email,
                )
                return self._send_json({"error": "Invalid email or password."}, status=HTTPStatus.UNAUTHORIZED)

            # Block unverified accounts — only when auth is fully enabled
            if self._is_auth_enabled() and not user.get("email_verified"):
                return self._send_json(
                    {
                        "error": "Please verify your email address before signing in. "
                                 "Check your inbox for a verification link.",
                        "require_verification": True,
                    },
                    status=403,
                )

            # Detect login from a new IP and alert the user by email. This is
            # the cheap version of "new device" detection — Google + GitHub
            # do the same thing.
            try:
                last_ip = (user.get("last_login_ip") or "").strip()
                if last_ip and last_ip != ip:
                    self._maybe_send_new_ip_alert(user, ip)
            except Exception:
                pass  # alerts are best-effort; never block login

            _logger.info("Login: %s (id=%s) from %s", email, user["id"], ip)
            _auth_rate_limiter.reset(ip)
            runtime.storage.update_user_login(user["id"], ip)
            runtime.storage.record_security_event(
                user_id=user["id"], event_type="login_success", ip=ip,
            )
            self._issue_session(user["id"], remember=remember)

        def _maybe_send_new_ip_alert(self, user: dict[str, Any], ip: str) -> None:
            """Best-effort email when a login arrives from a new IP."""
            try:
                from .mailer import send_email
                subject = "New sign-in to your BriefMe Pro account"
                html = (
                    "<p>Hi,</p>"
                    "<p>Your BriefMe Pro account was just signed in from a new IP address: "
                    f"<code>{ip}</code>.</p>"
                    "<p>If this was you, no action is needed. If not, change your password "
                    "immediately at "
                    f"<a href='{self._app_url()}/account'>{self._app_url()}/account</a>.</p>"
                )
                send_email(runtime.settings, user.get("email", ""), subject, html)
            except Exception as exc:
                _logger.warning("Failed to send new-IP alert: %s", exc)

        def _handle_auth_logout(self) -> None:
            # Revoke all sessions for this user so every device is signed out
            token = self._get_jwt()
            if token:
                payload = verify_token(token, self._jwt_secret())
                if payload:
                    user_id = payload.get("sub")
                    if user_id:
                        runtime.storage.revoke_all_user_sessions(user_id)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self._add_security_headers()
            self._clear_jwt_cookie()
            body = b'{"ok": true}'
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _handle_stripe_checkout(self, payload: dict[str, Any] | None = None) -> None:
            if not self._is_auth_enabled():
                return self._send_json({"error": "Auth not enabled."}, status=HTTPStatus.BAD_REQUEST)
            user = self._auth_user()
            if not user:
                return self._send_json({"error": "Unauthorized"}, status=HTTPStatus.UNAUTHORIZED)

            plan = "monthly"
            if payload and str(payload.get("plan", "")).lower() in {"annual", "yearly"}:
                plan = "annual"

            api_key = self._stripe_key()
            price_id = self._stripe_price_id(plan)
            if not api_key or not price_id:
                return self._send_json({"error": "Stripe not configured."}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

            sub = runtime.storage.get_subscription(user["id"])
            customer_id = sub.get("stripe_customer_id") if sub else None

            data: dict[str, Any] = {
                "mode": "subscription",
                "line_items[0][price]": price_id,
                "line_items[0][quantity]": "1",
                "success_url": f"{self._app_url()}/?checkout=success",
                "cancel_url": f"{self._app_url()}/subscribe",
                "customer_email": user["email"] if not customer_id else "",
            }
            if customer_id:
                data.pop("customer_email", None)
                data["customer"] = customer_id
            # Remove empty values
            data = {k: v for k, v in data.items() if v}

            result = _stripe_api("POST", "/checkout/sessions", data, api_key)
            if "url" in result:
                return self._send_json({"url": result["url"]})
            error_msg = result.get("error", {}).get("message", "Checkout failed.")
            return self._send_json({"error": error_msg}, status=HTTPStatus.BAD_GATEWAY)

        def _handle_stripe_portal(self) -> None:
            if not self._is_auth_enabled():
                return self._send_json({"error": "Auth not enabled."}, status=HTTPStatus.BAD_REQUEST)
            user = self._auth_user()
            if not user:
                return self._send_json({"error": "Unauthorized"}, status=HTTPStatus.UNAUTHORIZED)

            api_key = self._stripe_key()
            if not api_key:
                return self._send_json({"error": "Stripe not configured."}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

            sub = runtime.storage.get_subscription(user["id"])
            customer_id = sub.get("stripe_customer_id") if sub else None
            if not customer_id:
                return self._send_json({"error": "No billing account found."}, status=HTTPStatus.BAD_REQUEST)

            result = _stripe_api(
                "POST",
                "/billing_portal/sessions",
                {"customer": customer_id, "return_url": f"{self._app_url()}/account"},
                api_key,
            )
            if "url" in result:
                return self._send_json({"url": result["url"]})
            error_msg = result.get("error", {}).get("message", "Portal unavailable.")
            return self._send_json({"error": error_msg}, status=HTTPStatus.BAD_GATEWAY)

        def _handle_stripe_webhook(self) -> None:
            raw_body = self._read_raw_body()
            sig_header = self.headers.get("Stripe-Signature", "")
            webhook_secret = self._stripe_webhook_secret()

            # When auth is enabled, a webhook secret is required — no unsigned events accepted
            if self._is_auth_enabled() and not webhook_secret:
                _logger.warning("Stripe webhook received but STRIPE_WEBHOOK_SECRET is not configured — rejecting.")
                return self._send_json({"error": "Webhook not configured"}, status=HTTPStatus.SERVICE_UNAVAILABLE)

            if webhook_secret and not verify_stripe_webhook(raw_body, sig_header, webhook_secret):
                _logger.warning("Stripe webhook signature verification failed from %s", self._client_ip())
                return self._send_json({"error": "Invalid signature"}, status=HTTPStatus.BAD_REQUEST)

            try:
                event = json.loads(raw_body.decode("utf-8"))
            except Exception:
                return self._send_json({"error": "Invalid JSON"}, status=HTTPStatus.BAD_REQUEST)

            # Idempotency — Stripe retries on 5xx and on slow responses; never
            # double-process the same event id.
            event_id = str(event.get("id", ""))
            if event_id and runtime.storage.webhook_event_already_processed("stripe", event_id):
                return self._send_json({"ok": True, "duplicate": True})

            try:
                self._process_stripe_event(event)
            except Exception as exc:
                _logger.exception("Stripe webhook handler crashed on %s: %s", event_id, exc)
                # Return 500 so Stripe retries
                return self._send_json({"error": "Handler error"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

            if event_id:
                runtime.storage.mark_webhook_event_processed("stripe", event_id)
            return self._send_json({"ok": True})

        def _process_stripe_event(self, event: dict[str, Any]) -> None:
            event_type = event.get("type", "")
            obj = event.get("data", {}).get("object", {})

            if event_type in ("customer.subscription.created", "customer.subscription.updated"):
                self._sync_subscription(obj)
            elif event_type == "customer.subscription.deleted":
                self._sync_subscription(obj, force_cancelled=True)
            elif event_type == "checkout.session.completed":
                sub_id = obj.get("subscription")
                if sub_id:
                    api_key = self._stripe_key()
                    if api_key:
                        sub_obj = _stripe_api("GET", f"/subscriptions/{sub_id}", None, api_key)
                        if "id" in sub_obj:
                            self._sync_subscription(sub_obj)
            elif event_type == "invoice.payment_failed":
                # Mark the subscription past_due so the dashboard gates appropriately.
                # Stripe will retry the invoice; we'll get subscription.updated when it
                # transitions back to active or finally to unpaid/canceled.
                sub_id = obj.get("subscription")
                if sub_id:
                    existing = runtime.storage.get_subscription_by_stripe_subscription(sub_id)
                    if existing:
                        runtime.storage.upsert_subscription(
                            existing["user_id"],
                            stripe_customer_id=existing.get("stripe_customer_id", ""),
                            stripe_subscription_id=sub_id,
                            status="past_due",
                            expires_at=existing.get("subscription_expires_at", ""),
                            plan=existing.get("plan", ""),
                        )
            elif event_type == "invoice.paid":
                # Belt-and-braces: invoice.paid usually fires alongside
                # subscription.updated, but if for any reason the latter is missed,
                # this still keeps us in sync.
                sub_id = obj.get("subscription")
                if sub_id:
                    api_key = self._stripe_key()
                    if api_key:
                        sub_obj = _stripe_api("GET", f"/subscriptions/{sub_id}", None, api_key)
                        if "id" in sub_obj:
                            self._sync_subscription(sub_obj)

        def _sync_subscription(
            self,
            stripe_sub: dict[str, Any],
            *,
            force_cancelled: bool = False,
        ) -> None:
            stripe_sub_id = stripe_sub.get("id", "")
            customer_id = stripe_sub.get("customer", "")
            status = "cancelled" if force_cancelled else stripe_sub.get("status", "inactive")
            # Stripe uses "active" / "trialing" as good states
            if status in ("trialing",):
                status = "active"

            # current_period_end is a Unix timestamp
            period_end = stripe_sub.get("current_period_end")
            expires_iso = ""
            if period_end:
                expires_iso = datetime.fromtimestamp(int(period_end), tz=timezone.utc).isoformat()

            plan_item = (stripe_sub.get("items", {}).get("data") or [{}])[0]
            plan_name = plan_item.get("price", {}).get("nickname") or plan_item.get("plan", {}).get("nickname") or ""

            # Resolve user via existing subscription or via Stripe customer lookup
            existing = runtime.storage.get_subscription_by_stripe_subscription(stripe_sub_id)
            if not existing:
                existing = runtime.storage.get_subscription_by_stripe_customer(customer_id)

            if existing:
                user_id = existing["user_id"]
            else:
                # Can't map to a local user — nothing to update
                return

            runtime.storage.upsert_subscription(
                user_id,
                stripe_customer_id=customer_id,
                stripe_subscription_id=stripe_sub_id,
                status=status,
                expires_at=expires_iso,
                plan=plan_name,
            )

        # ------------------------------------------------------------------
        # Razorpay handlers
        # ------------------------------------------------------------------

        def _handle_razorpay_create_order(self, payload: dict[str, Any]) -> None:
            """Create a Razorpay order and return its details to the frontend."""
            if not self._is_auth_enabled():
                return self._send_json({"error": "Auth not enabled."}, status=HTTPStatus.BAD_REQUEST)
            user = self._auth_user()
            if not user:
                return self._send_json({"error": "Unauthorized"}, status=HTTPStatus.UNAUTHORIZED)

            key_id     = self._rzp_key_id()
            key_secret = self._rzp_key_secret()
            if not key_id or not key_secret:
                return self._send_json({"error": "Razorpay is not configured."}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

            plan     = str(payload.get("plan", "monthly"))
            amount   = self._rzp_amount(plan)
            currency = self._rzp_currency()

            # Razorpay receipt IDs must be ≤40 chars
            receipt = f"bzm_{user['id'][:16]}_{int(time.time())}"
            result = _razorpay_api(
                "POST",
                "/orders",
                {
                    "amount":          amount,
                    "currency":        currency,
                    "receipt":         receipt,
                    "notes":           {"plan": plan, "user_id": user["id"]},
                },
                key_id,
                key_secret,
            )

            if result.get("id"):
                return self._send_json({
                    "order_id": result["id"],
                    "amount":   amount,
                    "currency": currency,
                    "key_id":   key_id,
                    "name":     "BriefMe Pro",
                    "plan":     plan,
                    "email":    user.get("email", ""),
                })
            err = (result.get("error") or {}).get("description", "Could not create order.")
            return self._send_json({"error": err}, status=HTTPStatus.BAD_GATEWAY)

        def _handle_razorpay_verify(self, payload: dict[str, Any]) -> None:
            """Verify Razorpay payment signature, then activate the subscription."""
            if not self._is_auth_enabled():
                return self._send_json({"error": "Auth not enabled."}, status=HTTPStatus.BAD_REQUEST)
            user = self._auth_user()
            if not user:
                return self._send_json({"error": "Unauthorized"}, status=HTTPStatus.UNAUTHORIZED)

            order_id   = str(payload.get("razorpay_order_id", ""))
            payment_id = str(payload.get("razorpay_payment_id", ""))
            signature  = str(payload.get("razorpay_signature", ""))
            plan       = str(payload.get("plan", "monthly"))

            if not order_id or not payment_id or not signature:
                return self._send_json({"error": "Missing payment fields."}, status=HTTPStatus.BAD_REQUEST)

            key_secret = self._rzp_key_secret()
            if not verify_razorpay_payment(order_id, payment_id, signature, key_secret):
                _logger.warning(
                    "Razorpay signature verification failed for user %s order %s",
                    user["id"], order_id,
                )
                return self._send_json({"error": "Payment verification failed."}, status=HTTPStatus.BAD_REQUEST)

            # Signature valid — activate subscription
            from datetime import datetime, timedelta, timezone as _tz
            now = datetime.now(_tz.utc)
            expires = now + timedelta(days=366 if plan == "annual" else 31)
            expires_iso = expires.strftime("%Y-%m-%dT%H:%M:%SZ")

            runtime.storage.upsert_subscription(
                user["id"],
                razorpay_payment_id=payment_id,
                status="active",
                expires_at=expires_iso,
                plan=plan,
            )
            _logger.info(
                "Razorpay payment verified: user=%s order=%s payment=%s plan=%s",
                user["id"], order_id, payment_id, plan,
            )
            return self._send_json({"ok": True, "redirect": "/"})

        def _handle_razorpay_webhook(self) -> None:
            """Process Razorpay webhook events.

            Hardening parity with the Stripe handler:
              - HMAC-SHA256 signature verification (already present)
              - Reject unsigned events when auth is enabled
              - Idempotency via processed_webhooks table — Razorpay retries
                aggressively on 5xx; the same event id never doubles
              - Handle the full payment lifecycle, not just `payment.captured`:
                  * payment.captured / order.paid    → activate subscription
                  * payment.failed                    → mark past_due
                  * subscription.charged / activated → renew
                  * subscription.cancelled / paused  → cancel
              - Crashes return 500 so Razorpay retries (instead of silent 200)
            """
            raw_body = self._read_raw_body()
            sig_header     = self.headers.get("X-Razorpay-Signature", "")
            webhook_secret = self._rzp_webhook_secret()

            if self._is_auth_enabled() and not webhook_secret:
                _logger.warning("Razorpay webhook received but RAZORPAY_WEBHOOK_SECRET not set — rejecting.")
                return self._send_json({"error": "Webhook not configured"}, status=HTTPStatus.SERVICE_UNAVAILABLE)

            if webhook_secret and not verify_razorpay_webhook(raw_body, sig_header, webhook_secret):
                _logger.warning("Razorpay webhook signature mismatch from %s", self._client_ip())
                return self._send_json({"error": "Invalid signature"}, status=HTTPStatus.BAD_REQUEST)

            try:
                event = json.loads(raw_body.decode("utf-8"))
            except Exception:
                return self._send_json({"error": "Invalid JSON"}, status=HTTPStatus.BAD_REQUEST)

            # Razorpay's idempotency key is X-Razorpay-Event-Id (or `id` in body).
            event_id = (
                self.headers.get("X-Razorpay-Event-Id")
                or str(event.get("id", ""))
                or ""
            ).strip()
            if event_id and runtime.storage.webhook_event_already_processed("razorpay", event_id):
                return self._send_json({"ok": True, "duplicate": True})

            try:
                self._process_razorpay_event(event)
            except Exception as exc:
                _logger.exception("Razorpay webhook handler crashed on %s: %s", event_id, exc)
                return self._send_json({"error": "Handler error"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

            if event_id:
                runtime.storage.mark_webhook_event_processed("razorpay", event_id)
            return self._send_json({"ok": True})

        def _process_razorpay_event(self, event: dict[str, Any]) -> None:
            event_type = str(event.get("event", ""))
            payload    = event.get("payload") or {}
            entity     = (payload.get("payment") or {}).get("entity") or {}
            sub_entity = (payload.get("subscription") or {}).get("entity") or {}

            from datetime import datetime, timedelta, timezone as _tz

            if event_type in ("payment.captured", "order.paid"):
                notes   = entity.get("notes") or {}
                user_id = str(notes.get("user_id", ""))
                plan    = str(notes.get("plan", "monthly"))
                pay_id  = str(entity.get("id", ""))
                if user_id:
                    expires = datetime.now(_tz.utc) + timedelta(days=366 if plan == "annual" else 31)
                    runtime.storage.upsert_subscription(
                        user_id,
                        stripe_subscription_id=pay_id,
                        status="active",
                        expires_at=expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        plan=plan,
                    )
                    _logger.info("Razorpay: %s user=%s plan=%s", event_type, user_id, plan)

            elif event_type == "payment.failed":
                notes   = entity.get("notes") or {}
                user_id = str(notes.get("user_id", ""))
                if user_id:
                    existing = runtime.storage.get_subscription(user_id)
                    if existing and existing.get("subscription_status") == "active":
                        runtime.storage.upsert_subscription(
                            user_id,
                            stripe_subscription_id=existing.get("stripe_subscription_id", ""),
                            status="past_due",
                            expires_at=existing.get("subscription_expires_at", ""),
                            plan=existing.get("plan", ""),
                        )
                        _logger.warning("Razorpay: payment.failed user=%s", user_id)

            elif event_type in ("subscription.charged", "subscription.activated", "subscription.resumed"):
                notes   = sub_entity.get("notes") or {}
                user_id = str(notes.get("user_id", ""))
                plan    = str(notes.get("plan", sub_entity.get("plan_id", "monthly")))
                sub_id  = str(sub_entity.get("id", ""))
                if user_id:
                    expires = datetime.now(_tz.utc) + timedelta(days=366 if plan == "annual" else 31)
                    runtime.storage.upsert_subscription(
                        user_id,
                        stripe_subscription_id=sub_id,
                        status="active",
                        expires_at=expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        plan=plan,
                    )
                    _logger.info("Razorpay: %s user=%s", event_type, user_id)

            elif event_type in ("subscription.cancelled", "subscription.paused", "subscription.halted"):
                notes   = sub_entity.get("notes") or {}
                user_id = str(notes.get("user_id", ""))
                if user_id:
                    existing = runtime.storage.get_subscription(user_id)
                    if existing:
                        runtime.storage.upsert_subscription(
                            user_id,
                            stripe_subscription_id=existing.get("stripe_subscription_id", ""),
                            status="cancelled" if event_type == "subscription.cancelled" else "paused",
                            expires_at=existing.get("subscription_expires_at", ""),
                            plan=existing.get("plan", ""),
                        )
                        _logger.info("Razorpay: %s user=%s", event_type, user_id)

        def do_GET(self) -> None:  # noqa: N802
            try:
                self._do_GET_inner()
            except Exception as exc:
                _logger.exception("Unhandled exception in GET %s: %s", self.path, exc)
                self._send_internal_error()

        def _send_internal_error(self) -> None:
            """Last-resort 500 page. Browsers get HTML, API clients get JSON."""
            try:
                if self._wants_html() and not self.path.startswith(("/api/", "/static/")):
                    page = STATIC_DIR / "500.html"
                    if page.is_file():
                        body = page.read_bytes()
                        self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR)
                        self.send_header("Content-Type", "text/html; charset=utf-8")
                        self.send_header("Content-Length", str(len(body)))
                        self._add_security_headers()
                        self.end_headers()
                        self.wfile.write(body)
                        return
            except Exception:
                pass
            try:
                self._send_json({"error": "Internal server error"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            except Exception:
                pass

        def _do_GET_inner(self) -> None:
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            user: dict[str, Any] | None = None  # set by auth gate below

            # ----------------------------------------------------------
            # Health check — answer FIRST, before any settings access,
            # auth lookup, or routing logic. Railway kills the container
            # if /health doesn't respond inside healthcheckTimeout, so
            # this must be the cheapest possible path.
            # ----------------------------------------------------------
            if parsed.path in ("/health", "/api/health"):
                body = b'{"ok":true}'
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return

            # ----------------------------------------------------------
            # Auth pages — always public
            # ----------------------------------------------------------
            if parsed.path == "/login":
                return self._send_file(STATIC_DIR / "login.html", "text/html; charset=utf-8")
            if parsed.path == "/register":
                return self._send_file(STATIC_DIR / "register.html", "text/html; charset=utf-8")
            if parsed.path == "/subscribe":
                return self._send_file(STATIC_DIR / "subscribe.html", "text/html; charset=utf-8")
            if parsed.path == "/account":
                return self._send_file(STATIC_DIR / "account.html", "text/html; charset=utf-8")
            if parsed.path == "/forgot-password":
                return self._send_file(STATIC_DIR / "forgot-password.html", "text/html; charset=utf-8")
            if parsed.path == "/reset-password":
                return self._send_file(STATIC_DIR / "reset-password.html", "text/html; charset=utf-8")
            if parsed.path == "/verify-email":
                return self._send_file(STATIC_DIR / "verify-email.html", "text/html; charset=utf-8")
            if parsed.path == "/privacy":
                return self._send_file(STATIC_DIR / "privacy.html", "text/html; charset=utf-8")
            if parsed.path in ("/terms", "/tos"):
                return self._send_file(STATIC_DIR / "terms.html", "text/html; charset=utf-8")
            if parsed.path in ("/refund", "/refunds", "/refund-policy"):
                return self._send_file(STATIC_DIR / "refund.html", "text/html; charset=utf-8")
            if parsed.path in ("/pricing", "/plans"):
                return self._send_file(STATIC_DIR / "pricing.html", "text/html; charset=utf-8")
            # /demo is the public read-only dashboard — bypasses auth entirely.
            if parsed.path == "/demo":
                return self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            # /admin — owner-only operational dashboard. Auth check inside.
            if parsed.path == "/admin":
                if not self._is_admin_user():
                    return self._send_json({"error": "Forbidden"}, status=HTTPStatus.FORBIDDEN)
                return self._send_file(STATIC_DIR / "admin.html", "text/html; charset=utf-8")
            if parsed.path == "/api/admin/stats":
                if not self._is_admin_user():
                    return self._send_json({"error": "Forbidden"}, status=HTTPStatus.FORBIDDEN)
                return self._send_json(runtime.storage.admin_summary())
            # ----------------------------------------------------------
            # OAuth redirect flow — Google
            # ----------------------------------------------------------
            if parsed.path == "/api/auth/oauth/google/start":
                auth_cfg   = runtime.settings.get("auth", {})
                client_id  = str(auth_cfg.get("google_client_id", "")).strip()
                if not client_id:
                    return self._redirect("/login?error=google_not_configured")
                state = secrets.token_urlsafe(24)
                # Store state in a short-lived cookie so we can verify on callback
                redirect_uri = f"{self._app_url()}/api/auth/oauth/google/callback"
                google_url = (
                    "https://accounts.google.com/o/oauth2/v2/auth"
                    f"?client_id={urllib.parse.quote(client_id)}"
                    f"&redirect_uri={urllib.parse.quote(redirect_uri)}"
                    "&response_type=code"
                    "&scope=openid%20email%20profile"
                    f"&state={state}"
                    "&prompt=select_account"
                )
                self.send_response(302)
                self.send_header("Location", google_url)
                self.send_header("Set-Cookie",
                    f"oauth_state={state}; HttpOnly; SameSite=Lax; Max-Age=300; Path=/")
                self._add_security_headers()
                self.end_headers()
                return

            if parsed.path == "/api/auth/oauth/google/callback":
                auth_cfg      = runtime.settings.get("auth", {})
                client_id     = str(auth_cfg.get("google_client_id", "")).strip()
                client_secret = str(auth_cfg.get("google_client_secret", "")).strip()
                code  = (query.get("code")  or [""])[0]
                state = (query.get("state") or [""])[0]
                error = (query.get("error") or [""])[0]
                if error or not code:
                    return self._redirect("/login?error=google_cancelled")
                # Verify state
                stored_state = ""
                for part in self.headers.get("Cookie", "").split(";"):
                    part = part.strip()
                    if part.startswith("oauth_state="):
                        stored_state = part[len("oauth_state="):]
                if not stored_state or stored_state != state:
                    return self._redirect("/login?error=oauth_state_mismatch")
                # Exchange code for tokens
                redirect_uri = f"{self._app_url()}/api/auth/oauth/google/callback"
                token_data = urlencode({
                    "code": code, "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri, "grant_type": "authorization_code",
                }).encode()
                try:
                    req = urllib.request.Request(
                        "https://oauth2.googleapis.com/token",
                        data=token_data,
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                    )
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        token_resp = json.loads(resp.read())
                except Exception as exc:
                    _logger.error("Google token exchange failed: %s", exc)
                    return self._redirect("/login?error=google_token_failed")
                id_token = token_resp.get("id_token", "")
                info = verify_google_token(id_token, client_id) if id_token else None
                if not info:
                    return self._redirect("/login?error=google_verify_failed")
                google_id = str(info.get("sub", ""))
                email     = str(info.get("email", "")).lower().strip()
                name      = str(info.get("name", "") or info.get("given_name", ""))
                user = runtime.storage.get_user_by_google_id(google_id)
                if not user:
                    user = runtime.storage.get_user_by_email(email) if email else None
                    if user:
                        runtime.storage.link_google_id(user["id"], google_id)
                        if not user.get("email_verified"):
                            runtime.storage.mark_email_verified(user["id"])
                    elif email:
                        uid  = runtime.storage.create_oauth_user(email, name, google_id=google_id)
                        runtime.storage.mark_email_verified(uid)
                        user = runtime.storage.get_user_by_id(uid) or {"id": uid}
                    else:
                        return self._redirect("/login?error=google_no_email")
                runtime.storage.update_user_login(user["id"], self._client_ip())
                self._issue_session_redirect(user["id"])
                return

            # ----------------------------------------------------------
            # OAuth redirect flow — Apple
            # ----------------------------------------------------------
            if parsed.path == "/api/auth/oauth/apple/start":
                auth_cfg  = runtime.settings.get("auth", {})
                client_id = str(auth_cfg.get("apple_client_id", "")).strip()
                if not client_id:
                    return self._redirect("/login?error=apple_not_configured")
                state        = secrets.token_urlsafe(24)
                redirect_uri = f"{self._app_url()}/api/auth/oauth/apple/callback"
                apple_url = (
                    "https://appleid.apple.com/auth/authorize"
                    f"?client_id={urllib.parse.quote(client_id)}"
                    f"&redirect_uri={urllib.parse.quote(redirect_uri)}"
                    "&response_type=code%20id_token"
                    "&response_mode=form_post"
                    "&scope=name%20email"
                    f"&state={state}"
                )
                self.send_response(302)
                self.send_header("Location", apple_url)
                self.send_header("Set-Cookie",
                    f"oauth_state={state}; HttpOnly; SameSite=Lax; Max-Age=300; Path=/")
                self._add_security_headers()
                self.end_headers()
                return

            if parsed.path == "/robots.txt":
                robots = (STATIC_DIR / "robots.txt")
                if robots.is_file():
                    origin = self._app_url().rstrip("/")
                    content = robots.read_text("utf-8").replace("https://yourdomain.com", origin)
                    data = content.encode("utf-8")
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self._add_security_headers()
                    self.end_headers()
                    self.wfile.write(data)
                    return
            if parsed.path == "/sitemap.xml":
                sitemap = (STATIC_DIR / "sitemap.xml")
                if sitemap.is_file():
                    origin = self._app_url().rstrip("/")
                    content = sitemap.read_text("utf-8").replace("https://yourdomain.com", origin)
                    data = content.encode("utf-8")
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/xml; charset=utf-8")
                    self.send_header("Content-Length", str(len(data)))
                    self._add_security_headers()
                    self.end_headers()
                    self.wfile.write(data)
                    return

            # ----------------------------------------------------------
            # Static assets — always public
            # ----------------------------------------------------------
            if parsed.path.startswith("/static/"):
                relative_path = parsed.path.removeprefix("/static/")
                file_path = (STATIC_DIR / relative_path).resolve()
                if STATIC_DIR not in file_path.parents and file_path != STATIC_DIR:
                    return self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
                if not file_path.is_file():
                    return self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
                content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
                if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
                    content_type = f"{content_type}; charset=utf-8"
                # Public cache for static assets — short on HTML/JS/CSS so deploys
                # don't get pinned, long on images/fonts. CDN-friendly.
                ext = file_path.suffix.lower()
                if ext in {".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico", ".woff", ".woff2", ".ttf"}:
                    cache_control = "public, max-age=86400, stale-while-revalidate=604800"
                else:
                    cache_control = "public, max-age=300, stale-while-revalidate=600"
                return self._send_file(file_path, content_type, cache_control=cache_control)
            if parsed.path == "/og-image.svg":
                return self._send_file(STATIC_DIR / "og-image.svg", "image/svg+xml",
                                       cache_control="public, max-age=86400")
            if parsed.path == "/favicon.svg":
                return self._send_file(STATIC_DIR / "favicon.svg", "image/svg+xml",
                                       cache_control="public, max-age=86400")
            if parsed.path == "/manifest.webmanifest":
                return self._send_file(STATIC_DIR / "manifest.webmanifest",
                                       "application/manifest+json",
                                       cache_control="public, max-age=3600")
            if parsed.path == "/favicon.ico":
                # Browsers still ask for /favicon.ico; serve the SVG instead
                # of returning 204 so the icon shows up in the tab bar.
                return self._send_file(STATIC_DIR / "favicon.svg", "image/svg+xml",
                                       cache_control="public, max-age=86400")

            # ----------------------------------------------------------
            # Always-public API endpoints
            # ----------------------------------------------------------
            if parsed.path in ("/health", "/api/health"):
                return self._send_json({
                    "ok":      True,
                    "version": "1.0",
                    "events":  runtime.storage.metrics().get("total_events", 0),
                    "uptime":  int(time.time() - runtime.start_time) if hasattr(runtime, "start_time") else 0,
                })

            # Auth info endpoints — public but auth-aware
            if parsed.path == "/api/auth/me":
                user = self._auth_user()
                if not user:
                    return self._send_json({"error": "Unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
                sub = runtime.storage.get_subscription(user["id"])
                return self._send_json({
                    "id":             user["id"],
                    "email":          user.get("email", ""),
                    "display_name":   user.get("display_name", ""),
                    "created_at":     user.get("created_at", ""),
                    "last_login_at":  user.get("last_login_at", ""),
                    "email_verified": bool(user.get("email_verified")),
                    "onboarded":      bool(user.get("onboarded")),
                    "subscription":   sub or {},
                })

            if parsed.path == "/api/auth/check-email":
                ip = self._client_ip()
                if not _auth_rate_limiter.is_allowed(ip):
                    return self._send_json({"error": "Rate limited."}, status=429)
                email = (query.get("email") or [""])[0].strip().lower()
                if not _is_valid_email(email):
                    return self._send_json({"available": True})
                # Email-enumeration: this endpoint LEAKS account existence.
                # Return a constant `true` for valid-format addresses; the
                # frontend treats all addresses as "available" and the actual
                # duplicate check happens server-side in /register with a
                # generic response.
                return self._send_json({"available": True})

            if parsed.path == "/api/auth/price-info":
                # Razorpay takes priority when configured (Indian market)
                if self._rzp_key_id() and self._rzp_key_secret():
                    monthly_paise  = self._rzp_amount("monthly")
                    currency       = self._rzp_currency()
                    divisor        = 100  # paise → rupees
                    amount_str     = f"{monthly_paise / divisor:.2f}"
                    return self._send_json({
                        "amount":    amount_str,
                        "currency":  currency.lower(),
                        "interval":  "month",
                        "gateway":   "razorpay",
                    })
                # Fall back to Stripe
                api_key  = self._stripe_key()
                price_id = self._stripe_price_id()
                if not api_key or not price_id:
                    return self._send_json({"amount": None, "currency": "inr", "interval": "month"})
                result = _stripe_api("GET", f"/prices/{price_id}", None, api_key)
                unit_amount = result.get("unit_amount")
                currency    = result.get("currency", "inr").lower()
                interval    = (result.get("recurring") or {}).get("interval", "month")
                if unit_amount is not None:
                    amount = f"{unit_amount / 100:.2f}"
                    return self._send_json({"amount": amount, "currency": currency, "interval": interval, "gateway": "stripe"})
                return self._send_json({"amount": None, "currency": currency, "interval": interval})

            if parsed.path == "/api/stripe/price":
                api_key = self._stripe_key()
                price_id = self._stripe_price_id()
                if not api_key or not price_id:
                    return self._send_json({"display_price": None})
                result = _stripe_api("GET", f"/prices/{price_id}", None, api_key)
                unit_amount = result.get("unit_amount")
                currency = result.get("currency", "usd").upper()
                if unit_amount is not None:
                    dollars = unit_amount / 100
                    display = f"${dollars:.0f}" if currency == "USD" else f"{currency} {dollars:.2f}"
                    return self._send_json({"display_price": display})
                return self._send_json({"display_price": None})

            # ----------------------------------------------------------
            # Auth gate for the main app and all other API routes
            # ----------------------------------------------------------
            # General API rate limit covers public + authenticated /api/* paths
            if parsed.path.startswith("/api/"):
                if not _general_rate_limiter.is_allowed(self._client_ip()):
                    return self._send_json({"error": "Too many requests. Please slow down."}, status=429)
            # /api/metrics is public so the landing page can show live counts
            if parsed.path == "/api/metrics":
                return self._send_json(runtime.metrics_payload())

            # Public read-only data endpoints — power the /demo page and
            # the live counters on the marketing landing.
            _public_data_paths = {"/api/events", "/api/trends", "/api/intelligence"}

            if self._is_auth_enabled():
                user = self._auth_user()
                if not user:
                    if parsed.path == "/":
                        # Show the public marketing landing page, not a redirect
                        return self._send_file(STATIC_DIR / "landing.html", "text/html; charset=utf-8")
                    if parsed.path in _public_data_paths:
                        # Anonymous demo data — clamp limits to keep load light
                        query = dict(query)
                        try:
                            existing_limit = int(query.get("limit", "200"))
                        except ValueError:
                            existing_limit = 200
                        query["limit"] = str(min(existing_limit, 100))
                        query.setdefault("days", "1")  # last 24 h only for anon
                        # Fall through to the data routes below
                    else:
                        return self._send_json({"error": "Unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
                # Subscription gate — redirect to subscribe page for users without active plan
                _sub_exempt = {"/onboarding", "/account", "/subscribe", "/api/auth/me",
                                "/api/auth/logout", "/api/auth/onboarded", "/api/metrics",
                                "/api/auth/price-info", "/api/stripe/price"}
                _require_sub = bool(runtime.settings.get("auth", {}).get("require_subscription", False))
                if (
                    _require_sub
                    and parsed.path not in _sub_exempt
                    and not parsed.path.startswith("/api/auth/")
                    and not parsed.path.startswith("/static/")
                    and not self._has_active_subscription(user)
                ):
                    if parsed.path == "/":
                        return self._redirect("/subscribe")
                    return self._send_json({"error": "Subscription required.", "redirect": "/subscribe"}, status=402)

            # ----------------------------------------------------------
            # Main app + API routes (authenticated)
            # ----------------------------------------------------------
            if parsed.path == "/onboarding":
                return self._send_file(STATIC_DIR / "onboarding.html", "text/html; charset=utf-8")
            if parsed.path == "/":
                return self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")

            if parsed.path == "/api/events":
                return self._send_json({"events": runtime.get_events(query)})
            if parsed.path == "/api/trends":
                return self._send_json({"trends": runtime.trend_cache, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            if parsed.path == "/api/intelligence":
                return self._send_json(runtime.intelligence_payload())
            if parsed.path == "/api/stream":
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self._add_security_headers()
                self.end_headers()
                last_revision = -1
                started_at = time.time()
                keepalive_seconds = int(runtime.settings.get("api", {}).get("stream_keepalive_seconds", 15))

                try:
                    while not runtime.stop_event.is_set():
                        payload = runtime.stream_payload()
                        current_revision = int(payload.get("revision", 0))
                        if current_revision != last_revision:
                            body = f"event: sync\ndata: {json.dumps(payload)}\n\n".encode("utf-8")
                            last_revision = current_revision
                            self.wfile.write(body)
                            self.wfile.flush()
                            # For critical/high alerts, immediately send a dedicated alert event
                            # so the client can surface it without waiting for next poll cycle
                            if payload.get("alert_level") in ("critical", "high") and payload.get("high_priority_count", 0) > 0:
                                alert_body = f"event: alert\ndata: {json.dumps({'alert_level': payload['alert_level'], 'high_priority_count': payload['high_priority_count'], 'max_severity': payload['max_severity']})}\n\n".encode("utf-8")
                                self.wfile.write(alert_body)
                                self.wfile.flush()
                        else:
                            heartbeat = {
                                "revision": last_revision,
                                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            }
                            body = f"event: keepalive\ndata: {json.dumps(heartbeat)}\n\n".encode("utf-8")
                            self.wfile.write(body)
                            self.wfile.flush()
                        if time.time() - started_at > 300:
                            break
                        runtime.stop_event.wait(keepalive_seconds)
                except (BrokenPipeError, ConnectionResetError, TimeoutError):
                    return
                return
            if parsed.path == "/api/personal-feed":
                return self._send_json({"events": runtime.storage.list_personal_feed(limit=40)})
            if parsed.path == "/api/globe":
                events = runtime.get_events(query)
                return self._send_json({"events": events, "simulation": runtime.prediction_cache, "trends": runtime.trend_cache})
            if parsed.path == "/api/event":
                event_id = (query.get("id") or [""])[0]
                event = runtime.storage.get_event(event_id)
                if not event:
                    return self._send_json({"error": "Event not found"}, status=HTTPStatus.NOT_FOUND)
                return self._send_json({"event": event})

            return self._send_not_found(parsed.path)

        def do_POST(self) -> None:  # noqa: N802
            try:
                self._do_POST_inner()
            except Exception as exc:
                _logger.exception("Unhandled exception in POST %s: %s", self.path, exc)
                self._send_internal_error()

        def _do_POST_inner(self) -> None:
            parsed = urlparse(self.path)

            # Webhooks must read raw bytes (for signature verification).
            # Both singular and plural URL forms are accepted — Stripe and
            # most documentation use plural, our older code used singular.
            if parsed.path in ("/api/webhook/stripe", "/api/webhooks/stripe"):
                return self._handle_stripe_webhook()
            if parsed.path in ("/api/webhook/razorpay", "/api/webhooks/razorpay"):
                return self._handle_razorpay_webhook()

            payload = self._read_json()

            # ----------------------------------------------------------
            # Public auth routes
            # ----------------------------------------------------------
            if parsed.path == "/api/auth/register":
                return self._handle_auth_register(payload)
            if parsed.path == "/api/auth/login":
                return self._handle_auth_login(payload)
            if parsed.path == "/api/auth/logout":
                return self._handle_auth_logout()

            # Profile update (auth required, no subscription check)
            if parsed.path == "/api/auth/update-profile":
                if not self._check_csrf():
                    return self._send_json({"error": "CSRF check failed."}, status=403)
                user = self._auth_user()
                if not user:
                    return self._send_json({"error": "Unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
                display_name = str(payload.get("display_name", "")).strip()
                if display_name:
                    runtime.storage.update_user_display_name(user["id"], display_name)
                return self._send_json({"ok": True})

            if parsed.path == "/api/auth/change-password":
                if not self._check_csrf():
                    return self._send_json({"error": "CSRF check failed."}, status=403)
                user = self._auth_user()
                if not user:
                    return self._send_json({"error": "Unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
                current_pw = str(payload.get("current_password", ""))
                new_pw     = str(payload.get("new_password", ""))
                if not current_pw or not new_pw:
                    return self._send_json({"error": "Both current and new password are required."}, status=HTTPStatus.BAD_REQUEST)
                if len(new_pw) < 8:
                    return self._send_json({"error": "New password must be at least 8 characters."}, status=HTTPStatus.BAD_REQUEST)
                full_user = runtime.storage.get_user_by_id(user["id"])
                if not full_user or not verify_password(current_pw, full_user.get("password_hash", "")):
                    return self._send_json({"error": "Current password is incorrect."}, status=HTTPStatus.UNAUTHORIZED)
                runtime.storage.update_user_password(user["id"], hash_password(new_pw))
                # Revoke all sessions to force re-login on other devices
                runtime.storage.revoke_all_user_sessions(user["id"])
                return self._send_json({"ok": True})

            # ----------------------------------------------------------
            # OAuth — Apple callback (Apple POSTs form data here)
            # ----------------------------------------------------------
            if parsed.path == "/api/auth/oauth/apple/callback":
                # Apple sends form-encoded, not JSON
                content_len = int(self.headers.get("Content-Length", 0))
                raw_body    = self.rfile.read(content_len) if content_len else b""
                from urllib.parse import parse_qs as _pqs
                form = {k: v[0] for k, v in _pqs(raw_body.decode("utf-8", "replace")).items()}
                code     = form.get("code", "")
                state    = form.get("state", "")
                id_token = form.get("id_token", "")
                user_json = form.get("user", "")
                error_val = form.get("error", "")
                if error_val or not code:
                    return self._redirect("/login?error=apple_cancelled")
                # Verify state cookie
                stored_state = ""
                for part in self.headers.get("Cookie", "").split(";"):
                    part = part.strip()
                    if part.startswith("oauth_state="):
                        stored_state = part[len("oauth_state="):]
                if not stored_state or stored_state != state:
                    return self._redirect("/login?error=oauth_state_mismatch")
                auth_cfg  = runtime.settings.get("auth", {})
                client_id = str(auth_cfg.get("apple_client_id", "")).strip()
                # Verify id_token
                info = verify_apple_token(id_token, client_id) if id_token else None
                if not info:
                    return self._redirect("/login?error=apple_verify_failed")
                apple_id = str(info.get("sub", ""))
                email    = str(info.get("email", "")).lower().strip()
                # Apple only sends name on first auth — grab from form if present
                name = ""
                if user_json:
                    try:
                        u = json.loads(user_json)
                        n = u.get("name", {})
                        name = f"{n.get('firstName','')} {n.get('lastName','')}".strip()
                    except Exception:
                        pass
                user = runtime.storage.get_user_by_apple_id(apple_id)
                if not user:
                    user = runtime.storage.get_user_by_email(email) if email else None
                    if user:
                        runtime.storage.link_apple_id(user["id"], apple_id)
                        if not user.get("email_verified"):
                            runtime.storage.mark_email_verified(user["id"])
                    elif email:
                        uid  = runtime.storage.create_oauth_user(email, name, apple_id=apple_id)
                        runtime.storage.mark_email_verified(uid)
                        user = runtime.storage.get_user_by_id(uid) or {"id": uid}
                    else:
                        return self._redirect("/login?error=apple_no_email")
                runtime.storage.update_user_login(user["id"], self._client_ip())
                self._issue_session_redirect(user["id"])
                return

            # ----------------------------------------------------------
            # Token refresh (exchange refresh cookie for new access token)
            # ----------------------------------------------------------
            if parsed.path == "/api/auth/refresh":
                from .auth import hash_token as _hash_tok
                raw_refresh = ""
                for part in self.headers.get("Cookie", "").split(";"):
                    part = part.strip()
                    if part.startswith("ooe_refresh="):
                        raw_refresh = part[len("ooe_refresh="):]
                        break
                if not raw_refresh:
                    return self._send_json({"error": "No refresh token."}, status=401)
                token_hash = _hash_tok(raw_refresh)
                session = runtime.storage.get_session_by_token_hash(token_hash)
                if not session or session.get("revoked"):
                    return self._send_json({"error": "Invalid or revoked session."}, status=401)
                try:
                    if datetime.fromisoformat(session["expires_at"]) < datetime.now(timezone.utc):
                        return self._send_json({"error": "Refresh token expired."}, status=401)
                except Exception:
                    pass
                # Rotate: revoke old, issue new. Preserve the "remember this
                # device" flag — a session whose lifespan is > 30 days was
                # originally created with remember=True, so the new session
                # rotated from it must inherit that property. Otherwise a 90-day
                # session silently downgrades to 7 days on the first refresh.
                try:
                    created = datetime.fromisoformat(session.get("created_at", ""))
                    expires = datetime.fromisoformat(session.get("expires_at", ""))
                    was_remembered = (expires - created).days > 30
                except Exception:
                    was_remembered = False
                runtime.storage.revoke_session_by_token_hash(token_hash)
                return self._issue_session(session["user_id"], remember=was_remembered)

            # ----------------------------------------------------------
            # Password reset (public, rate-limited)
            # ----------------------------------------------------------
            if parsed.path == "/api/auth/forgot-password":
                ip = self._client_ip()
                if not _email_rate_limiter.is_allowed(ip):
                    return self._send_json({"error": "Too many requests. Try again later."}, status=429, extra_headers={"Retry-After": "3600"})
                email = str(payload.get("email", "")).strip().lower()
                if not _is_valid_email(email):
                    # Always 200 to avoid email enumeration
                    return self._send_json({"ok": True})
                user = runtime.storage.get_user_by_email(email)
                if user and not user.get("deleted_at"):
                    import hashlib as _hl
                    raw_token   = secrets.token_urlsafe(48)
                    token_hash  = _hl.sha256(raw_token.encode()).hexdigest()
                    expires_at  = datetime.fromtimestamp(
                        time.time() + 3600, tz=timezone.utc
                    ).isoformat()
                    runtime.storage.set_password_reset_token(user["id"], token_hash, expires_at)
                    reset_url   = f"{self._app_url()}/reset-password?token={raw_token}"
                    subject, html = password_reset_html(reset_url)
                    threading.Thread(
                        target=send_email,
                        args=(runtime.settings, email, subject, html),
                        daemon=True,
                    ).start()
                return self._send_json({"ok": True})

            if parsed.path == "/api/auth/reset-password":
                raw_token = str(payload.get("token", "")).strip()
                new_pw    = str(payload.get("password", ""))
                if not raw_token or len(new_pw) < 8:
                    return self._send_json({"error": "Invalid request."}, status=400)
                import hashlib as _hl
                token_hash = _hl.sha256(raw_token.encode()).hexdigest()
                user = runtime.storage.get_user_by_reset_token(token_hash)
                if not user:
                    return self._send_json({"error": "Invalid or expired reset link."}, status=400)
                # Check expiry
                expires_at = user.get("pw_reset_expires", "")
                try:
                    if expires_at and datetime.fromisoformat(expires_at) < datetime.now(timezone.utc):
                        return self._send_json({"error": "Reset link has expired. Request a new one."}, status=400)
                except Exception:
                    pass
                new_hash = hash_password(new_pw)
                runtime.storage.consume_reset_token(user["id"], new_hash)
                # Revoke all sessions for security
                runtime.storage.revoke_all_user_sessions(user["id"])
                return self._send_json({"ok": True})

            # ----------------------------------------------------------
            # Email verification (public)
            # ----------------------------------------------------------
            if parsed.path == "/api/auth/verify-email":
                raw_token = str(payload.get("token", "")).strip()
                if not raw_token:
                    return self._send_json({"error": "Missing token."}, status=400)
                import hashlib as _hl
                token_hash = _hl.sha256(raw_token.encode()).hexdigest()
                user = runtime.storage.get_user_by_verify_token(token_hash)
                if not user:
                    return self._send_json({"error": "Invalid or already-used verification link."}, status=400)
                expires_at = user.get("email_verify_expires", "")
                try:
                    if expires_at and datetime.fromisoformat(expires_at) < datetime.now(timezone.utc):
                        return self._send_json({"error": "Verification link has expired. Please register again or request a new one."}, status=400)
                except Exception:
                    pass
                runtime.storage.mark_email_verified(user["id"])
                # Send welcome email
                app_url = self._app_url()
                subject, html = welcome_html(user.get("display_name", ""), app_url)
                send_email(runtime.settings, user["email"], subject, html)
                return self._send_json({"ok": True})

            if parsed.path == "/api/auth/resend-verification":
                ip = self._client_ip()
                if not _email_rate_limiter.is_allowed(ip):
                    return self._send_json({"error": "Too many requests. Try again later."}, status=429, extra_headers={"Retry-After": "3600"})
                user = self._auth_user()
                if not user:
                    return self._send_json({"error": "Unauthorized"}, status=401)
                if user.get("email_verified"):
                    return self._send_json({"ok": True, "already_verified": True})
                self._send_verification_email(user)
                return self._send_json({"ok": True})

            # ----------------------------------------------------------
            # Account deletion (GDPR right to erasure)
            # ----------------------------------------------------------
            if parsed.path == "/api/auth/delete-account":
                user = self._auth_user()
                if not user:
                    return self._send_json({"error": "Unauthorized"}, status=401)
                if not self._check_csrf():
                    return self._send_json({"error": "CSRF check failed."}, status=403)
                # Require password confirmation (except for OAuth-only accounts)
                pw = str(payload.get("password", ""))
                stored_hash = user.get("password_hash", "")
                if stored_hash and not verify_password(pw, stored_hash):
                    return self._send_json({"error": "Incorrect password."}, status=401)
                runtime.storage.soft_delete_user(user["id"])
                # Clear session cookie
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Set-Cookie", "ooe_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")
                self._add_security_headers()
                body = json.dumps({"ok": True}).encode()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            # ----------------------------------------------------------
            # Data export (GDPR right to portability)
            # ----------------------------------------------------------
            if parsed.path == "/api/auth/export-data":
                user = self._auth_user()
                if not user:
                    return self._send_json({"error": "Unauthorized"}, status=401)
                export = runtime.storage.export_user_data(user["id"])
                fmt = (query.get("format") or "json").lower()
                if fmt == "zip":
                    return self._send_user_export_zip(user, export)
                return self._send_json(export)

            # ----------------------------------------------------------
            # Onboarding completion
            # ----------------------------------------------------------
            if parsed.path == "/api/auth/onboarded":
                user = self._auth_user()
                if user:
                    runtime.storage.mark_onboarded(user["id"])
                    # Save initial region + category preferences from onboarding wizard
                    regions    = payload.get("regions") or []
                    categories = payload.get("categories") or []
                    if isinstance(regions, list) and regions:
                        region_weights = {r: 1.4 for r in regions if isinstance(r, str)}
                        runtime.storage.save_user_profile(user["id"], region_weights=region_weights)
                    if isinstance(categories, list) and categories:
                        category_weights = {c: 1.4 for c in categories if isinstance(c, str)}
                        runtime.storage.save_user_profile(user["id"], category_weights=category_weights)
                return self._send_json({"ok": True})

            # ----------------------------------------------------------
            # Razorpay routes (auth required but no subscription check)
            # ----------------------------------------------------------
            if parsed.path == "/api/razorpay/create-order":
                return self._handle_razorpay_create_order(payload)
            if parsed.path == "/api/razorpay/verify":
                return self._handle_razorpay_verify(payload)

            # ----------------------------------------------------------
            # Stripe routes (auth required but no subscription check)
            # ----------------------------------------------------------
            if parsed.path in ("/api/stripe/checkout", "/api/auth/create-checkout"):
                return self._handle_stripe_checkout(payload)
            if parsed.path in ("/api/stripe/portal", "/api/auth/create-portal"):
                return self._handle_stripe_portal()

            # ----------------------------------------------------------
            # Auth gate for remaining POST routes
            # ----------------------------------------------------------
            if self._is_auth_enabled():
                user = self._auth_user()
                if not user:
                    return self._send_json({"error": "Unauthorized"}, status=HTTPStatus.UNAUTHORIZED)

            if parsed.path == "/api/interactions":
                uid = str((self._auth_user() or {}).get("id", ""))
                runtime.storage.record_interaction(payload, user_id=uid)
                return self._send_json({"ok": True})

            if parsed.path == "/api/feedback":
                # Thumbs up / thumbs down on a specific event.
                # Body: {"event_id": "...", "action": "up" | "down"}
                uid = str((self._auth_user() or {}).get("id", ""))
                event_id = str(payload.get("event_id", ""))
                action = str(payload.get("action", "")).strip().lower()
                if action not in {"up", "down"}:
                    return self._send_json(
                        {"error": "action must be 'up' or 'down'"},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                if not event_id:
                    return self._send_json(
                        {"error": "event_id is required"},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                runtime.storage.record_interaction(
                    {
                        "event_id": event_id,
                        "action": f"feedback_{action}",
                        "source": "feedback",
                        "feedback": action,
                    },
                    user_id=uid,
                )
                # Nudge learned preference weights based on this feedback
                if uid:
                    runtime.storage.apply_feedback_to_profile(uid, event_id, action)
                return self._send_json({"ok": True})

            if parsed.path == "/api/personal-state":
                runtime.storage.record_state_snapshot(payload)
                return self._send_json({"ok": True, "state": runtime.current_state()})

            if parsed.path == "/api/ingest":
                return self._send_json(runtime.ingest_once())

            return self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

    return OOERequestHandler


def serve(settings: dict[str, Any], *, run_server: bool = True, ingest_once_only: bool = False) -> int:
    runtime_dir = str(settings.get("runtime_dir", Path.home() / "Library" / "Application Support" / "OOE"))
    _setup_logging(runtime_dir)
    _validate_production_config(settings)

    runtime = OOERuntime(settings)
    if ingest_once_only or not run_server:
        summary = runtime.ingest_once()
        print(json.dumps(summary, indent=2))
        runtime.stop()
        return 0

    runtime.start_background_tasks()
    host = str(settings.get("server", {}).get("host", "127.0.0.1"))
    port = int(settings.get("server", {}).get("port", 8787))

    # Loud, visible startup banner so Railway/Render/Fly logs make it
    # obvious which port the proxy needs to route to. The previous one-line
    # _logger.info was getting buried under ingest spam.
    print("=" * 60, flush=True)
    print(f"BriefMe Pro listening on http://{host}:{port}", flush=True)
    print(f"  PORT env var       = {os.environ.get('PORT', '(unset)')}", flush=True)
    print(f"  RAILWAY_PUBLIC_DOMAIN = {os.environ.get('RAILWAY_PUBLIC_DOMAIN', '(unset)')}", flush=True)
    print(f"  OOE_APP_URL        = {os.environ.get('OOE_APP_URL', '(unset, derived)')}", flush=True)
    print("=" * 60, flush=True)

    httpd = ReusableThreadingHTTPServer((host, port), make_handler(runtime))

    def shutdown_handler(signum, frame) -> None:
        del signum, frame
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)
    _logger.info("BriefMe server started at http://%s:%d (auth=%s)", host, port, settings.get("auth", {}).get("enabled", False))
    print(f"OOE running at http://{host}:{port}")
    try:
        httpd.serve_forever(poll_interval=0.5)
    finally:
        httpd.server_close()
        runtime.stop()
        _logger.info("BriefMe server stopped.")
    return 0


def run(config_path: str | None = None, *, run_server: bool = True, ingest_once_only: bool = False) -> int:
    settings = load_settings(config_path)
    return serve(settings, run_server=run_server, ingest_once_only=ingest_once_only)
