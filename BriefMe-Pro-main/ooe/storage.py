from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import NormalizedEvent, utcnow_iso


class OOEStorage:
    def __init__(self, db_path: str | Path) -> None:
        requested = Path(db_path).expanduser().resolve()
        self.db_path = self._resolve_writable_path(requested)
        # If we fell back, persistence is broken — surface this so the admin
        # dashboard can warn and so the operator notices in logs.
        self.is_ephemeral = (self.db_path != requested)
        self.requested_db_path = requested
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.db_path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._initialize()

    @staticmethod
    def _resolve_writable_path(requested: Path) -> Path:
        """Return a usable DB path.

        If the requested directory is unwritable (e.g. DB_PATH points at a
        Railway Volume that hasn't been attached yet), don't crash — fall
        back to a path under the current working directory and log loudly.
        Better to start with an ephemeral DB than to refuse to deploy.
        """
        import logging
        logger = logging.getLogger("ooe.storage")
        try:
            requested.parent.mkdir(parents=True, exist_ok=True)
            # touch-test: open and close once to verify writability
            test_conn = sqlite3.connect(requested, check_same_thread=False)
            test_conn.close()
            return requested
        except (PermissionError, OSError, sqlite3.OperationalError) as exc:
            fallback = Path.cwd() / "briefme-pro.db"
            logger.error(
                "DB_PATH %s is not writable (%s). Falling back to %s. "
                "Data will be lost on redeploy until a writable Volume is "
                "attached at %s.",
                requested, exc, fallback, requested.parent,
            )
            print(
                f"\n⚠  DB_PATH {requested} is not writable: {exc}\n"
                f"⚠  Falling back to {fallback} — data will NOT survive redeploys.\n"
                f"⚠  On Railway: attach a Volume mounted at {requested.parent}.\n",
                flush=True,
            )
            fallback.parent.mkdir(parents=True, exist_ok=True)
            return fallback

    def _initialize(self) -> None:
        with self._lock:
            self._connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=NORMAL;
                PRAGMA busy_timeout=5000;
                PRAGMA cache_size=-32768;

                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    external_id TEXT NOT NULL DEFAULT '',
                    source_name TEXT NOT NULL,
                    cluster_id TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL,
                    severity REAL NOT NULL DEFAULT 0,
                    magnitude REAL NOT NULL DEFAULT 0,
                    urgency REAL NOT NULL DEFAULT 0,
                    relevance REAL NOT NULL DEFAULT 0,
                    personal_impact REAL NOT NULL DEFAULT 0,
                    emotional_load REAL NOT NULL DEFAULT 0,
                    cognitive_load REAL NOT NULL DEFAULT 0,
                    behavioral_load REAL NOT NULL DEFAULT 0,
                    confidence REAL NOT NULL DEFAULT 0,
                    coverage_score REAL NOT NULL DEFAULT 0,
                    probability REAL NOT NULL DEFAULT 1,
                    predicted INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'active',
                    verification_status TEXT NOT NULL DEFAULT 'checking',
                    location_name TEXT NOT NULL DEFAULT '',
                    country TEXT NOT NULL DEFAULT '',
                    region TEXT NOT NULL DEFAULT '',
                    latitude REAL,
                    longitude REAL,
                    happened_at TEXT NOT NULL,
                    collected_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    url TEXT NOT NULL DEFAULT '',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    source_refs_json TEXT NOT NULL DEFAULT '[]',
                    raw_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_events_happened_at ON events(happened_at DESC);
                CREATE INDEX IF NOT EXISTS idx_events_category ON events(category);
                CREATE INDEX IF NOT EXISTS idx_events_personal_impact ON events(personal_impact DESC);
                CREATE INDEX IF NOT EXISTS idx_events_predicted ON events(predicted, happened_at DESC);

                CREATE TABLE IF NOT EXISTS interactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL DEFAULT '',
                    action TEXT NOT NULL,
                    dwell_seconds REAL NOT NULL DEFAULT 0,
                    stress_delta REAL NOT NULL DEFAULT 0,
                    focus_delta REAL NOT NULL DEFAULT 0,
                    mood_delta REAL NOT NULL DEFAULT 0,
                    source TEXT NOT NULL DEFAULT 'dashboard',
                    category TEXT NOT NULL DEFAULT '',
                    region TEXT NOT NULL DEFAULT '',
                    country TEXT NOT NULL DEFAULT '',
                    location_name TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_interactions_created_at ON interactions(created_at DESC);

                CREATE TABLE IF NOT EXISTS state_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    stress REAL NOT NULL DEFAULT 0,
                    focus REAL NOT NULL DEFAULT 0,
                    mood REAL NOT NULL DEFAULT 0,
                    cognitive_load REAL NOT NULL DEFAULT 0,
                    note TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'dashboard',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_state_snapshots_created_at ON state_snapshots(created_at DESC);

                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_alerts_unique
                    ON alerts(event_id, channel, reason);

                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email);

                CREATE TABLE IF NOT EXISTS subscriptions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL UNIQUE,
                    stripe_customer_id TEXT NOT NULL DEFAULT '',
                    stripe_subscription_id TEXT NOT NULL DEFAULT '',
                    razorpay_payment_id TEXT NOT NULL DEFAULT '',
                    subscription_status TEXT NOT NULL DEFAULT 'inactive',
                    subscription_expires_at TEXT NOT NULL DEFAULT '',
                    plan TEXT NOT NULL DEFAULT 'monthly',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                );

                CREATE INDEX IF NOT EXISTS idx_subscriptions_user_id
                    ON subscriptions(user_id);
                CREATE INDEX IF NOT EXISTS idx_subscriptions_stripe_customer
                    ON subscriptions(stripe_customer_id);
                CREATE INDEX IF NOT EXISTS idx_subscriptions_stripe_sub
                    ON subscriptions(stripe_subscription_id);

                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0,
                    ip_address TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY (user_id) REFERENCES users(id)
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_user_id
                    ON sessions(user_id);
                CREATE INDEX IF NOT EXISTS idx_sessions_token_hash
                    ON sessions(token_hash);

                -- Per-user learned preference weights, updated as users interact
                CREATE TABLE IF NOT EXISTS user_profiles (
                    user_id TEXT PRIMARY KEY,
                    region_weights_json TEXT NOT NULL DEFAULT '{}',
                    category_weights_json TEXT NOT NULL DEFAULT '{}',
                    keyword_weights_json TEXT NOT NULL DEFAULT '{}',
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                );

                -- Webhook idempotency: each provider event id is processed at most once
                CREATE TABLE IF NOT EXISTS processed_webhooks (
                    provider TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    processed_at TEXT NOT NULL,
                    PRIMARY KEY (provider, event_id)
                );

                -- Security audit log: every authentication-relevant event
                -- (login success/failure, register attempt, password change,
                -- 2FA enable/disable, suspicious activity) for forensics.
                CREATE TABLE IF NOT EXISTS security_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL DEFAULT '',
                    event_type TEXT NOT NULL,
                    ip_address TEXT NOT NULL DEFAULT '',
                    detail TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_security_events_user ON security_events(user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_security_events_type ON security_events(event_type, created_at DESC);
                """
            )
            self._ensure_event_columns(
                {
                    "cluster_id": "TEXT NOT NULL DEFAULT ''",
                    "confidence": "REAL NOT NULL DEFAULT 0",
                    "coverage_score": "REAL NOT NULL DEFAULT 0",
                    "verification_status": "TEXT NOT NULL DEFAULT 'checking'",
                    "source_refs_json": "TEXT NOT NULL DEFAULT '[]'",
                }
            )
            self._ensure_interaction_columns(
                {
                    "user_id": "TEXT NOT NULL DEFAULT ''",
                    "feedback": "TEXT NOT NULL DEFAULT ''",
                }
            )
            self._ensure_user_columns(
                {
                    "display_name":            "TEXT NOT NULL DEFAULT ''",
                    "last_login_at":           "TEXT NOT NULL DEFAULT ''",
                    "failed_login_attempts":   "INTEGER NOT NULL DEFAULT 0",
                    "locked_until":            "TEXT NOT NULL DEFAULT ''",
                    "google_id":               "TEXT NOT NULL DEFAULT ''",
                    "apple_id":                "TEXT NOT NULL DEFAULT ''",
                    "totp_secret":             "TEXT NOT NULL DEFAULT ''",
                    "totp_enabled":            "INTEGER NOT NULL DEFAULT 0",
                    "totp_backup_codes":       "TEXT NOT NULL DEFAULT '[]'",
                    "email_verified":          "INTEGER NOT NULL DEFAULT 0",
                    "email_verify_token":      "TEXT NOT NULL DEFAULT ''",
                    "email_verify_expires":    "TEXT NOT NULL DEFAULT ''",
                    "pw_reset_token":          "TEXT NOT NULL DEFAULT ''",
                    "pw_reset_expires":        "TEXT NOT NULL DEFAULT ''",
                    "deleted_at":              "TEXT NOT NULL DEFAULT ''",
                    "onboarded":               "INTEGER NOT NULL DEFAULT 0",
                    # Last-seen IP for new-device alerting on login
                    "last_login_ip":           "TEXT NOT NULL DEFAULT ''",
                    # Legal: timestamp + version of ToS the user clicked through
                    "tos_accepted_at":         "TEXT NOT NULL DEFAULT ''",
                    "tos_version":             "TEXT NOT NULL DEFAULT ''",
                    # Onboarding selections drive the personalisation cold-start
                    "onboard_regions":         "TEXT NOT NULL DEFAULT '[]'",
                    "onboard_categories":      "TEXT NOT NULL DEFAULT '[]'",
                }
            )
            # Migrate subscriptions table — add razorpay_payment_id if it doesn't exist yet
            existing_sub_cols = {
                str(row["name"])
                for row in self._connection.execute("PRAGMA table_info(subscriptions)").fetchall()
            }
            if "razorpay_payment_id" not in existing_sub_cols:
                self._connection.execute(
                    "ALTER TABLE subscriptions ADD COLUMN razorpay_payment_id TEXT NOT NULL DEFAULT ''"
                )
            self._connection.commit()

    def _ensure_event_columns(self, columns: dict[str, str]) -> None:
        existing = {
            str(row["name"])
            for row in self._connection.execute("PRAGMA table_info(events)").fetchall()
        }
        for name, definition in columns.items():
            if name in existing:
                continue
            self._connection.execute(f"ALTER TABLE events ADD COLUMN {name} {definition}")

    def _ensure_user_columns(self, columns: dict[str, str]) -> None:
        existing = {
            str(row["name"])
            for row in self._connection.execute("PRAGMA table_info(users)").fetchall()
        }
        for name, definition in columns.items():
            if name in existing:
                continue
            self._connection.execute(f"ALTER TABLE users ADD COLUMN {name} {definition}")

    def _ensure_interaction_columns(self, columns: dict[str, str]) -> None:
        existing = {
            str(row["name"])
            for row in self._connection.execute("PRAGMA table_info(interactions)").fetchall()
        }
        for name, definition in columns.items():
            if name in existing:
                continue
            self._connection.execute(f"ALTER TABLE interactions ADD COLUMN {name} {definition}")
        # Add user_id index if the column exists now
        if "user_id" in columns:
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_interactions_user_id ON interactions(user_id)"
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def upsert_events(self, events: Iterable[NormalizedEvent]) -> int:
        count = 0
        with self._lock:
            for event in events:
                self._connection.execute(
                    """
                    INSERT INTO events (
                        id, external_id, source_name, cluster_id, title, summary, category, severity, magnitude,
                        urgency, relevance, personal_impact, emotional_load, cognitive_load, behavioral_load,
                        confidence, coverage_score, probability, predicted, status, verification_status,
                        location_name, country, region, latitude, longitude,
                        happened_at, collected_at, updated_at, url, tags_json, source_refs_json, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        external_id = excluded.external_id,
                        source_name = excluded.source_name,
                        cluster_id = CASE
                            WHEN events.cluster_id != ''
                                 AND instr(excluded.raw_json, '"missing_happened_at": true') > 0
                            THEN events.cluster_id
                            ELSE excluded.cluster_id
                        END,
                        title = excluded.title,
                        summary = excluded.summary,
                        category = excluded.category,
                        severity = excluded.severity,
                        magnitude = excluded.magnitude,
                        urgency = excluded.urgency,
                        relevance = excluded.relevance,
                        personal_impact = excluded.personal_impact,
                        emotional_load = excluded.emotional_load,
                        cognitive_load = excluded.cognitive_load,
                        behavioral_load = excluded.behavioral_load,
                        confidence = excluded.confidence,
                        coverage_score = excluded.coverage_score,
                        probability = excluded.probability,
                        predicted = excluded.predicted,
                        status = excluded.status,
                        verification_status = excluded.verification_status,
                        location_name = excluded.location_name,
                        country = excluded.country,
                        region = excluded.region,
                        latitude = excluded.latitude,
                        longitude = excluded.longitude,
                        happened_at = CASE
                            WHEN events.happened_at != ''
                                 AND instr(excluded.raw_json, '"missing_happened_at": true') > 0
                            THEN events.happened_at
                            ELSE excluded.happened_at
                        END,
                        collected_at = excluded.collected_at,
                        updated_at = excluded.updated_at,
                        url = excluded.url,
                        tags_json = excluded.tags_json,
                        source_refs_json = excluded.source_refs_json,
                        raw_json = excluded.raw_json
                    """,
                    (
                        event.event_id,
                        event.external_id,
                        event.source_name,
                        event.cluster_id,
                        event.title,
                        event.summary,
                        event.category,
                        event.severity,
                        event.magnitude,
                        event.urgency,
                        event.relevance,
                        event.personal_impact,
                        event.emotional_load,
                        event.cognitive_load,
                        event.behavioral_load,
                        event.confidence,
                        event.coverage_score,
                        event.probability,
                        int(event.predicted),
                        event.status,
                        event.verification_status,
                        event.location_name,
                        event.country,
                        event.region,
                        event.latitude,
                        event.longitude,
                        event.happened_at,
                        event.collected_at,
                        event.updated_at,
                        event.url,
                        json.dumps(event.tags),
                        json.dumps(event.source_refs),
                        json.dumps(event.raw_payload),
                    ),
                )
                count += 1
            self._connection.commit()
        return count

    def _decode_event(self, row: sqlite3.Row, include_raw: bool = False) -> dict[str, Any]:
        payload = dict(row)
        payload.pop("rank_idx", None)
        payload["event_id"] = payload.pop("id")
        payload["predicted"] = bool(payload["predicted"])
        payload["tags"] = json.loads(payload.pop("tags_json") or "[]")
        payload["source_refs"] = json.loads(payload.pop("source_refs_json") or "[]")
        raw_json = payload.pop("raw_json")
        if include_raw:
            payload["raw_payload"] = json.loads(raw_json or "{}")
        return payload

    def list_events(
        self,
        *,
        limit: int = 200,
        category: str = "",
        region: str = "",
        search: str = "",
        severity_min: float = 0.0,
        max_age_hours: float | None = None,
        include_predicted: bool = True,
        include_raw: bool = False,
        deduplicate_cluster: bool = True,
    ) -> list[dict[str, Any]]:
        conditions = ["severity >= ?"]
        params: list[Any] = [severity_min]

        if category:
            conditions.append("category = ?")
            params.append(category)
        if region:
            like = f"%{region.lower()}%"
            conditions.append(
                "(LOWER(location_name) LIKE ? OR LOWER(region) LIKE ? OR LOWER(country) LIKE ?)"
            )
            params.extend([like, like, like])
        if search:
            like = f"%{search.lower()}%"
            conditions.append("(LOWER(title) LIKE ? OR LOWER(summary) LIKE ?)")
            params.extend([like, like])
        if max_age_hours is not None and float(max_age_hours) > 0:
            conditions.append("(julianday(happened_at) >= julianday('now') - (? / 24.0))")
            params.append(float(max_age_hours))
        if not include_predicted:
            conditions.append("predicted = 0")

        where_clause = " AND ".join(conditions)
        if deduplicate_cluster:
            sql = f"""
                WITH ranked_events AS (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY COALESCE(NULLIF(cluster_id, ''), id)
                               ORDER BY happened_at DESC, severity DESC, confidence DESC, updated_at DESC
                           ) AS rank_idx
                    FROM events
                    WHERE {where_clause}
                )
                SELECT * FROM ranked_events
                WHERE rank_idx = 1
                ORDER BY happened_at DESC, severity DESC
                LIMIT ?
            """
        else:
            sql = f"""
                SELECT * FROM events
                WHERE {where_clause}
                ORDER BY happened_at DESC, severity DESC
                LIMIT ?
            """
        params.append(limit)

        with self._lock:
            try:
                rows = self._connection.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                fallback_sql = f"""
                    SELECT * FROM events
                    WHERE {where_clause}
                    ORDER BY happened_at DESC, severity DESC
                    LIMIT ?
                """
                rows = self._connection.execute(fallback_sql, params).fetchall()
        return [self._decode_event(row, include_raw=include_raw) for row in rows]

    def list_personal_feed(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM events
                ORDER BY personal_impact DESC, happened_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._decode_event(row) for row in rows]

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM events WHERE id = ?",
                (event_id,),
            ).fetchone()
        return self._decode_event(row, include_raw=True) if row else None

    def get_recent_events(self, *, limit: int = 200, days: int = 21) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM events
                WHERE predicted = 0
                  AND happened_at >= datetime('now', ?)
                ORDER BY happened_at DESC, severity DESC
                LIMIT ?
                """,
                (f"-{days} days", limit),
            ).fetchall()
        return [self._decode_event(row) for row in rows]

    def record_interaction(self, payload: dict[str, Any], *, user_id: str = "") -> None:
        event_meta = self.get_event(str(payload.get("event_id", ""))) if payload.get("event_id") else None
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO interactions (
                    event_id, action, dwell_seconds, stress_delta, focus_delta, mood_delta, source,
                    category, region, country, location_name, payload_json, created_at, user_id, feedback
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(payload.get("event_id", "")),
                    str(payload.get("action", "unknown")),
                    float(payload.get("dwell_seconds") or 0.0),
                    float(payload.get("stress_delta") or 0.0),
                    float(payload.get("focus_delta") or 0.0),
                    float(payload.get("mood_delta") or 0.0),
                    str(payload.get("source", "dashboard")),
                    str(payload.get("category") or (event_meta or {}).get("category", "")),
                    str(payload.get("region") or (event_meta or {}).get("region", "")),
                    str(payload.get("country") or (event_meta or {}).get("country", "")),
                    str(payload.get("location_name") or (event_meta or {}).get("location_name", "")),
                    json.dumps(payload),
                    utcnow_iso(),
                    str(user_id),
                    str(payload.get("feedback", "")),
                ),
            )
            self._connection.commit()

    def list_recent_interactions(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT event_id, action, dwell_seconds, stress_delta, focus_delta, mood_delta, source,
                       category, region, country, location_name, payload_json, created_at
                FROM interactions
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_state_snapshot(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO state_snapshots (
                    stress, focus, mood, cognitive_load, note, source, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    float(payload.get("stress") or 0.0),
                    float(payload.get("focus") or 0.0),
                    float(payload.get("mood") or 0.0),
                    float(payload.get("cognitive_load") or 0.0),
                    str(payload.get("note", "")),
                    str(payload.get("source", "dashboard")),
                    json.dumps(payload),
                    utcnow_iso(),
                ),
            )
            self._connection.commit()

    def latest_state_snapshot(self) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT stress, focus, mood, cognitive_load, note, source, payload_json, created_at
                FROM state_snapshots
                ORDER BY created_at DESC
                LIMIT 1
                """
            ).fetchone()
        return dict(row) if row else None

    def interaction_summary(self) -> dict[str, dict[str, float]]:
        category_counts: dict[str, float] = {}
        region_counts: dict[str, float] = {}
        with self._lock:
            category_rows = self._connection.execute(
                """
                SELECT category, COUNT(*) AS count
                FROM interactions
                WHERE category != ''
                GROUP BY category
                """
            ).fetchall()
            region_rows = self._connection.execute(
                """
                SELECT COALESCE(NULLIF(region, ''), NULLIF(country, ''), NULLIF(location_name, '')) AS bucket,
                       COUNT(*) AS count
                FROM interactions
                WHERE region != '' OR country != '' OR location_name != ''
                GROUP BY bucket
                """
            ).fetchall()

        for row in category_rows:
            category_counts[str(row["category"])] = float(row["count"])
        for row in region_rows:
            category = str(row["bucket"])
            if category:
                region_counts[category] = float(row["count"])
        return {"categories": category_counts, "regions": region_counts}

    def weighted_interaction_summary(
        self,
        *,
        decay_hours: float = 168.0,
        user_id: str = "",
    ) -> dict[str, dict[str, float]]:
        """
        Like interaction_summary() but applies exponential time-decay so
        recent interactions count significantly more than old ones.
        Weight = exp(-age_hours / decay_hours), so interactions from
        the last day count ~6× more than week-old interactions.
        Returns float weights, not raw counts.

        If user_id is supplied only that user's interactions are counted,
        which is needed for per-user personalisation in a multi-user product.
        """
        import math

        category_weights: dict[str, float] = {}
        region_weights: dict[str, float] = {}

        with self._lock:
            if user_id:
                rows = self._connection.execute(
                    """
                    SELECT category, region, country, location_name, created_at
                    FROM interactions
                    WHERE created_at >= datetime('now', ?)
                      AND user_id = ?
                    ORDER BY created_at DESC
                    LIMIT 500
                    """,
                    (f"-{int(decay_hours * 2)} hours", user_id),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    """
                    SELECT category, region, country, location_name, created_at
                    FROM interactions
                    WHERE created_at >= datetime('now', ?)
                    ORDER BY created_at DESC
                    LIMIT 500
                    """,
                    (f"-{int(decay_hours * 2)} hours",),
                ).fetchall()

        for row in rows:
            try:
                from datetime import datetime, timezone
                created = datetime.fromisoformat(
                    str(row["created_at"]).replace("Z", "+00:00")
                ).astimezone(timezone.utc)
                age_hours = (datetime.now(timezone.utc) - created).total_seconds() / 3600
            except (ValueError, TypeError):
                age_hours = decay_hours  # default to low weight on parse failure

            weight = math.exp(-age_hours / decay_hours)

            cat = str(row["category"] or "")
            if cat:
                category_weights[cat] = category_weights.get(cat, 0.0) + weight

            region = str(row["region"] or row["country"] or row["location_name"] or "")
            if region:
                region_weights[region] = region_weights.get(region, 0.0) + weight

        return {"categories": category_weights, "regions": region_weights}

    def top_engaged_categories(self, *, limit: int = 5, decay_hours: float = 168.0) -> list[tuple[str, float]]:
        """Return top N categories by weighted interaction score, most engaged first."""
        summary = self.weighted_interaction_summary(decay_hours=decay_hours)
        cats = sorted(summary["categories"].items(), key=lambda x: x[1], reverse=True)
        return cats[:limit]

    def top_engaged_regions(self, *, limit: int = 5, decay_hours: float = 168.0) -> list[tuple[str, float]]:
        """Return top N regions by weighted interaction score, most engaged first."""
        summary = self.weighted_interaction_summary(decay_hours=decay_hours)
        regions = sorted(summary["regions"].items(), key=lambda x: x[1], reverse=True)
        return regions[:limit]

    # ------------------------------------------------------------------
    # Per-user profiles (learned preference weights)
    # ------------------------------------------------------------------

    def get_user_profile(self, user_id: str) -> dict[str, Any]:
        """Return the stored preference weights for a user, or empty dicts if none saved yet."""
        with self._lock:
            row = self._connection.execute(
                "SELECT region_weights_json, category_weights_json, keyword_weights_json FROM user_profiles WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if not row:
            return {"region_weights": {}, "category_weights": {}, "keyword_weights": {}}
        return {
            "region_weights": json.loads(row["region_weights_json"] or "{}"),
            "category_weights": json.loads(row["category_weights_json"] or "{}"),
            "keyword_weights": json.loads(row["keyword_weights_json"] or "{}"),
        }

    def save_user_profile(
        self,
        user_id: str,
        *,
        region_weights: dict[str, float] | None = None,
        category_weights: dict[str, float] | None = None,
        keyword_weights: dict[str, float] | None = None,
    ) -> None:
        """Upsert preference weights for a user."""
        existing = self.get_user_profile(user_id)
        merged_region = {**existing["region_weights"], **(region_weights or {})}
        merged_category = {**existing["category_weights"], **(category_weights or {})}
        merged_keyword = {**existing["keyword_weights"], **(keyword_weights or {})}
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO user_profiles (user_id, region_weights_json, category_weights_json, keyword_weights_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    region_weights_json = excluded.region_weights_json,
                    category_weights_json = excluded.category_weights_json,
                    keyword_weights_json = excluded.keyword_weights_json,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    json.dumps(merged_region),
                    json.dumps(merged_category),
                    json.dumps(merged_keyword),
                    utcnow_iso(),
                ),
            )
            self._connection.commit()

    def apply_feedback_to_profile(self, user_id: str, event_id: str, feedback: str) -> None:
        """
        Nudge a user's learned weights based on thumbs-up / thumbs-down feedback.
        'up'  → boost the event's category and region weights by 0.05
        'down' → reduce them by 0.04 (smaller penalty keeps things balanced)
        """
        event = self.get_event(event_id)
        if not event:
            return
        direction = 1.0 if feedback == "up" else -1.0
        magnitude = 0.05 if feedback == "up" else 0.04

        profile = self.get_user_profile(user_id)
        category = str(event.get("category") or "")
        region = str(event.get("region") or event.get("country") or "")

        category_weights = dict(profile["category_weights"])
        region_weights = dict(profile["region_weights"])

        if category:
            current = float(category_weights.get(category, 1.0))
            category_weights[category] = round(max(0.2, min(3.0, current + direction * magnitude)), 4)
        if region:
            current = float(region_weights.get(region, 1.0))
            region_weights[region] = round(max(0.2, min(3.0, current + direction * magnitude)), 4)

        self.save_user_profile(
            user_id,
            category_weights=category_weights,
            region_weights=region_weights,
        )

    def metrics(self) -> dict[str, Any]:
        with self._lock:
            total_events = self._connection.execute(
                "SELECT COUNT(*) FROM events WHERE predicted = 0"
            ).fetchone()[0]
            predicted_events = self._connection.execute(
                "SELECT COUNT(*) FROM events WHERE predicted = 1"
            ).fetchone()[0]
            high_impact = self._connection.execute(
                "SELECT COUNT(*) FROM events WHERE personal_impact >= 70"
            ).fetchone()[0]
            top_category_row = self._connection.execute(
                """
                SELECT category, COUNT(*) AS count
                FROM events
                WHERE predicted = 0
                GROUP BY category
                ORDER BY count DESC
                LIMIT 1
                """
            ).fetchone()
            last_event_row = self._connection.execute(
                "SELECT happened_at FROM events ORDER BY happened_at DESC LIMIT 1"
            ).fetchone()

        return {
            "total_events": int(total_events),
            "predicted_events": int(predicted_events),
            "high_impact_events": int(high_impact),
            "top_category": str(top_category_row["category"]) if top_category_row else "n/a",
            "latest_event_at": str(last_event_row["happened_at"]) if last_event_row else "",
        }

    def has_alert(self, event_id: str, channel: str, reason: str) -> bool:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT 1 FROM alerts
                WHERE event_id = ? AND channel = ? AND reason = ?
                LIMIT 1
                """,
                (event_id, channel, reason),
            ).fetchone()
        return bool(row)

    def mark_alert_sent(self, event_id: str, channel: str, reason: str) -> None:
        with self._lock:
            self._connection.execute(
                """
                INSERT OR IGNORE INTO alerts (event_id, channel, reason, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (event_id, channel, reason, utcnow_iso()),
            )
            self._connection.commit()

    # ------------------------------------------------------------------
    # User management
    # ------------------------------------------------------------------

    def create_user(
        self,
        email: str,
        password_hash: str,
        display_name: str = "",
        *,
        tos_version: str = "",
    ) -> str:
        """Insert a new user and return the generated user_id.

        If ``tos_version`` is supplied, the registration timestamp is recorded
        against that version of the Terms of Service — required for EU
        compliance and for defending against future "I never agreed" claims.
        """
        user_id = str(uuid.uuid4())
        now = utcnow_iso()
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO users (id, email, password_hash, display_name, created_at,
                                   updated_at, tos_accepted_at, tos_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, email.lower().strip(), password_hash, display_name.strip(),
                 now, now, now if tos_version else "", tos_version),
            )
            self._connection.commit()
        return user_id

    def save_onboarding_selections(
        self,
        user_id: str,
        regions: list[str],
        categories: list[str],
    ) -> None:
        """Persist the user's onboarding wizard selections, ready to seed
        the personalisation profile on first feed render."""
        import json as _json
        with self._lock:
            self._connection.execute(
                """UPDATE users SET onboard_regions=?, onboard_categories=?, updated_at=?
                   WHERE id=?""",
                (_json.dumps(list(regions)[:20]),
                 _json.dumps(list(categories)[:20]),
                 utcnow_iso(), user_id),
            )
            self._connection.commit()

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT id, email, password_hash, display_name, created_at,
                       last_login_at, last_login_ip, failed_login_attempts,
                       locked_until, email_verified, onboarded
                FROM users WHERE email = ? LIMIT 1
                """,
                (email.lower().strip(),),
            ).fetchone()
        return dict(row) if row else None

    def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT id, email, display_name, created_at, last_login_at,
                       email_verified, onboarded
                FROM users WHERE id = ? LIMIT 1
                """,
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_user_login(self, user_id: str, ip: str = "") -> None:
        """Record a successful login: reset failure counter, set last_login_at, track IP."""
        now = utcnow_iso()
        with self._lock:
            self._connection.execute(
                """
                UPDATE users
                SET last_login_at = ?, last_login_ip = ?,
                    failed_login_attempts = 0, locked_until = '', updated_at = ?
                WHERE id = ?
                """,
                (now, ip or "", now, user_id),
            )
            self._connection.commit()

    def record_security_event(
        self,
        *,
        user_id: str,
        event_type: str,
        ip: str = "",
        detail: str = "",
    ) -> None:
        """Append a row to the security audit log.

        Tolerates the empty user_id case (e.g. a login attempt for an email
        that doesn't exist) so the log captures attacker behaviour even
        when there's no real account to attribute it to.
        """
        with self._lock:
            self._connection.execute(
                "INSERT INTO security_events(user_id, event_type, ip_address, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id or "", event_type, ip or "", detail or "", utcnow_iso()),
            )
            self._connection.commit()

    def recent_security_events(self, user_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
        """Return the N most recent audit events, optionally filtered by user."""
        with self._lock:
            if user_id:
                rows = self._connection.execute(
                    "SELECT user_id, event_type, ip_address, detail, created_at "
                    "FROM security_events WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                    (user_id, limit),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT user_id, event_type, ip_address, detail, created_at "
                    "FROM security_events ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(r) for r in rows]

    def record_failed_login(self, email: str, max_attempts: int = 10, lockout_minutes: int = 15) -> None:
        """Increment failure counter; lock account after max_attempts."""
        email = email.lower().strip()
        with self._lock:
            row = self._connection.execute(
                "SELECT failed_login_attempts FROM users WHERE email = ? LIMIT 1",
                (email,),
            ).fetchone()
            if not row:
                return
            new_count = int(row["failed_login_attempts"]) + 1
            locked_until = ""
            if new_count >= max_attempts:
                locked_until = (
                    datetime.now(timezone.utc) + timedelta(minutes=lockout_minutes)
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
            self._connection.execute(
                "UPDATE users SET failed_login_attempts = ?, locked_until = ? WHERE email = ?",
                (new_count, locked_until, email),
            )
            self._connection.commit()

    def is_account_locked(self, email: str) -> bool:
        """Return True if the account is currently in its lockout window."""
        with self._lock:
            row = self._connection.execute(
                "SELECT locked_until FROM users WHERE email = ? LIMIT 1",
                (email.lower().strip(),),
            ).fetchone()
        if not row or not row["locked_until"]:
            return False
        try:
            until = datetime.fromisoformat(
                str(row["locked_until"]).replace("Z", "+00:00")
            ).astimezone(timezone.utc)
            return datetime.now(timezone.utc) < until
        except (ValueError, TypeError):
            return False

    def update_user_display_name(self, user_id: str, display_name: str) -> None:
        now = utcnow_iso()
        with self._lock:
            self._connection.execute(
                "UPDATE users SET display_name = ?, updated_at = ? WHERE id = ?",
                (display_name.strip(), now, user_id),
            )
            self._connection.commit()

    def update_user_password(self, user_id: str, new_password_hash: str) -> None:
        now = utcnow_iso()
        with self._lock:
            self._connection.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (new_password_hash, now, user_id),
            )
            self._connection.commit()

    # ------------------------------------------------------------------
    # OAuth identity linking
    # ------------------------------------------------------------------

    def get_user_by_google_id(self, google_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT id, email, display_name, google_id, totp_enabled FROM users WHERE google_id = ? LIMIT 1",
            (google_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_user_by_apple_id(self, apple_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT id, email, display_name, apple_id, totp_enabled FROM users WHERE apple_id = ? LIMIT 1",
            (apple_id,),
        ).fetchone()
        return dict(row) if row else None

    def create_oauth_user(
        self, email: str, display_name: str, google_id: str = "", apple_id: str = ""
    ) -> str:
        import uuid
        user_id = str(uuid.uuid4())
        now = utcnow_iso()
        with self._lock:
            self._connection.execute(
                """INSERT INTO users (id, email, password_hash, display_name, google_id, apple_id, created_at, updated_at)
                   VALUES (?, ?, '', ?, ?, ?, ?, ?)""",
                (user_id, email.lower().strip(), display_name.strip(), google_id, apple_id, now, now),
            )
            self._connection.commit()
        return user_id

    def link_google_id(self, user_id: str, google_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE users SET google_id = ?, updated_at = ? WHERE id = ?",
                (google_id, utcnow_iso(), user_id),
            )
            self._connection.commit()

    def link_apple_id(self, user_id: str, apple_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE users SET apple_id = ?, updated_at = ? WHERE id = ?",
                (apple_id, utcnow_iso(), user_id),
            )
            self._connection.commit()

    # ------------------------------------------------------------------
    # 2FA / TOTP
    # ------------------------------------------------------------------

    def get_totp_row(self, user_id: str) -> dict[str, Any]:
        row = self._connection.execute(
            "SELECT totp_secret, totp_enabled, totp_backup_codes FROM users WHERE id = ? LIMIT 1",
            (user_id,),
        ).fetchone()
        return dict(row) if row else {}

    def set_totp_secret(self, user_id: str, secret: str) -> None:
        """Store secret before the user has confirmed — not yet enabled."""
        with self._lock:
            self._connection.execute(
                "UPDATE users SET totp_secret = ?, totp_enabled = 0, updated_at = ? WHERE id = ?",
                (secret, utcnow_iso(), user_id),
            )
            self._connection.commit()

    def enable_totp(self, user_id: str, backup_codes_json: str) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE users SET totp_enabled = 1, totp_backup_codes = ?, updated_at = ? WHERE id = ?",
                (backup_codes_json, utcnow_iso(), user_id),
            )
            self._connection.commit()

    def disable_totp(self, user_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE users SET totp_enabled = 0, totp_secret = '', totp_backup_codes = '[]', updated_at = ? WHERE id = ?",
                (utcnow_iso(), user_id),
            )
            self._connection.commit()

    def consume_backup_code(self, user_id: str, code: str) -> bool:
        """Remove a backup code if it exists; return True if it was valid."""
        import hashlib as _hl
        row = self.get_totp_row(user_id)
        stored_hashes: list[str] = json.loads(row.get("totp_backup_codes") or "[]")
        # Normalize input then hash it — stored values are SHA-256 hashes
        normalized = code.upper().replace("-", "").replace(" ", "")
        hashed_input = _hl.sha256(normalized.encode()).hexdigest()
        if hashed_input not in stored_hashes:
            return False
        codes = [h for h in stored_hashes if h != hashed_input]
        with self._lock:
            self._connection.execute(
                "UPDATE users SET totp_backup_codes = ?, updated_at = ? WHERE id = ?",
                (json.dumps(codes), utcnow_iso(), user_id),
            )
            self._connection.commit()
        return True

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def create_session(
        self,
        user_id: str,
        token_hash: str,
        expires_at: str,
        ip_address: str = "",
    ) -> str:
        """Store a hashed session token and return the session id."""
        session_id = str(uuid.uuid4())
        now = utcnow_iso()
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO sessions (id, user_id, token_hash, created_at, expires_at, ip_address)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, user_id, token_hash, now, expires_at, ip_address),
            )
            self._connection.commit()
        return session_id

    def get_session_by_token_hash(self, token_hash: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT id, user_id, created_at, expires_at, revoked, ip_address
                FROM sessions WHERE token_hash = ? LIMIT 1
                """,
                (token_hash,),
            ).fetchone()
        return dict(row) if row else None

    def revoke_all_user_sessions(self, user_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE sessions SET revoked = 1 WHERE user_id = ?",
                (user_id,),
            )
            self._connection.commit()

    def revoke_session_by_token_hash(self, token_hash: str) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE sessions SET revoked = 1 WHERE token_hash = ?",
                (token_hash,),
            )
            self._connection.commit()

    # ------------------------------------------------------------------
    # Subscription management
    # ------------------------------------------------------------------

    def upsert_subscription(
        self,
        user_id: str,
        *,
        stripe_customer_id: str = "",
        stripe_subscription_id: str = "",
        razorpay_payment_id: str = "",
        status: str = "inactive",
        expires_at: str = "",
        plan: str = "monthly",
    ) -> None:
        now = utcnow_iso()
        with self._lock:
            existing = self._connection.execute(
                "SELECT id FROM subscriptions WHERE user_id = ? LIMIT 1",
                (user_id,),
            ).fetchone()
            if existing:
                self._connection.execute(
                    """
                    UPDATE subscriptions
                    SET stripe_customer_id = CASE WHEN ? != '' THEN ? ELSE stripe_customer_id END,
                        stripe_subscription_id = CASE WHEN ? != '' THEN ? ELSE stripe_subscription_id END,
                        razorpay_payment_id = CASE WHEN ? != '' THEN ? ELSE razorpay_payment_id END,
                        subscription_status = ?,
                        subscription_expires_at = CASE WHEN ? != '' THEN ? ELSE subscription_expires_at END,
                        plan = ?,
                        updated_at = ?
                    WHERE user_id = ?
                    """,
                    (
                        stripe_customer_id, stripe_customer_id,
                        stripe_subscription_id, stripe_subscription_id,
                        razorpay_payment_id, razorpay_payment_id,
                        status,
                        expires_at, expires_at,
                        plan,
                        now,
                        user_id,
                    ),
                )
            else:
                self._connection.execute(
                    """
                    INSERT INTO subscriptions
                        (id, user_id, stripe_customer_id, stripe_subscription_id, razorpay_payment_id,
                         subscription_status, subscription_expires_at, plan, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()), user_id,
                        stripe_customer_id, stripe_subscription_id, razorpay_payment_id,
                        status, expires_at, plan, now, now,
                    ),
                )
            self._connection.commit()

    def get_subscription(self, user_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT stripe_customer_id, stripe_subscription_id,
                       subscription_status, subscription_expires_at, plan, updated_at
                FROM subscriptions WHERE user_id = ? LIMIT 1
                """,
                (user_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_subscription_by_stripe_customer(self, stripe_customer_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT s.user_id, s.stripe_customer_id, s.stripe_subscription_id,
                       s.subscription_status, s.subscription_expires_at, s.plan
                FROM subscriptions s
                WHERE s.stripe_customer_id = ? LIMIT 1
                """,
                (stripe_customer_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_subscription_by_stripe_subscription(self, stripe_subscription_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT s.user_id, s.stripe_customer_id, s.stripe_subscription_id,
                       s.subscription_status, s.subscription_expires_at, s.plan
                FROM subscriptions s
                WHERE s.stripe_subscription_id = ? LIMIT 1
                """,
                (stripe_subscription_id,),
            ).fetchone()
        return dict(row) if row else None

    # ── Admin dashboard queries ─────────────────────────────────────

    def admin_summary(self) -> dict[str, Any]:
        """Return aggregate counters for the /admin page.

        Each query is single-row and indexed, so this stays O(1) regardless
        of table size. Designed to be safe to render on every page load.
        """
        with self._lock:
            users_total = self._connection.execute(
                "SELECT COUNT(*) c FROM users WHERE deleted_at = ''"
            ).fetchone()["c"]
            users_verified = self._connection.execute(
                "SELECT COUNT(*) c FROM users WHERE deleted_at='' AND email_verified=1"
            ).fetchone()["c"]
            users_24h = self._connection.execute(
                "SELECT COUNT(*) c FROM users WHERE created_at >= datetime('now','-1 day')"
            ).fetchone()["c"]
            users_7d = self._connection.execute(
                "SELECT COUNT(*) c FROM users WHERE created_at >= datetime('now','-7 days')"
            ).fetchone()["c"]
            subs_active = self._connection.execute(
                "SELECT COUNT(*) c FROM subscriptions WHERE subscription_status IN ('active','trialing')"
            ).fetchone()["c"]
            subs_past_due = self._connection.execute(
                "SELECT COUNT(*) c FROM subscriptions WHERE subscription_status = 'past_due'"
            ).fetchone()["c"]
            events_total = self._connection.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
            # Events table stores ingestion time as `collected_at` (not first_seen_at).
            events_24h = self._connection.execute(
                "SELECT COUNT(*) c FROM events WHERE collected_at >= datetime('now','-1 day')"
            ).fetchone()["c"]
            interactions_24h = self._connection.execute(
                "SELECT COUNT(*) c FROM interactions WHERE created_at >= datetime('now','-1 day')"
            ).fetchone()["c"]
            recent_signups = [
                dict(r) for r in self._connection.execute(
                    "SELECT email, created_at, email_verified FROM users "
                    "WHERE deleted_at='' ORDER BY created_at DESC LIMIT 10"
                ).fetchall()
            ]
        return {
            "users": {
                "total": users_total,
                "verified": users_verified,
                "new_24h": users_24h,
                "new_7d": users_7d,
            },
            "subscriptions": {
                "active": subs_active,
                "past_due": subs_past_due,
            },
            "events": {
                "total": events_total,
                "new_24h": events_24h,
            },
            "interactions_24h": interactions_24h,
            "recent_signups": recent_signups,
            "storage": {
                "db_path": str(self.db_path),
                "ephemeral": bool(self.is_ephemeral),
                "requested_path": str(self.requested_db_path),
            },
            "security": {
                "failed_logins_24h": self._connection.execute(
                    "SELECT COUNT(*) c FROM security_events "
                    "WHERE event_type='login_failed' AND created_at >= datetime('now','-1 day')"
                ).fetchone()["c"],
                "recent_events": [
                    dict(r) for r in self._connection.execute(
                        "SELECT event_type, ip_address, detail, created_at "
                        "FROM security_events ORDER BY created_at DESC LIMIT 10"
                    ).fetchall()
                ],
            },
        }

    # ── Webhook idempotency ─────────────────────────────────────────

    def webhook_event_already_processed(self, provider: str, event_id: str) -> bool:
        """Returns True if (provider, event_id) has been seen before."""
        if not provider or not event_id:
            return False
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM processed_webhooks WHERE provider=? AND event_id=? LIMIT 1",
                (provider, event_id),
            ).fetchone()
        return row is not None

    def mark_webhook_event_processed(self, provider: str, event_id: str) -> None:
        if not provider or not event_id:
            return
        with self._lock:
            self._connection.execute(
                "INSERT OR IGNORE INTO processed_webhooks(provider, event_id, processed_at) VALUES (?, ?, ?)",
                (provider, event_id, datetime.now(timezone.utc).isoformat()),
            )

    # ── Email verification ──────────────────────────────────────────

    def set_email_verify_token(self, user_id: str, token_hash: str, expires_at: str) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE users SET email_verify_token=?, email_verify_expires=? WHERE id=?",
                (token_hash, expires_at, user_id),
            )
            self._connection.commit()

    def get_user_by_verify_token(self, token_hash: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM users WHERE email_verify_token=? AND deleted_at='' LIMIT 1",
                (token_hash,),
            ).fetchone()
        return dict(row) if row else None

    def mark_email_verified(self, user_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE users SET email_verified=1, email_verify_token='', email_verify_expires='' WHERE id=?",
                (user_id,),
            )
            self._connection.commit()

    # ── Password reset ──────────────────────────────────────────────

    def set_password_reset_token(self, user_id: str, token_hash: str, expires_at: str) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE users SET pw_reset_token=?, pw_reset_expires=? WHERE id=?",
                (token_hash, expires_at, user_id),
            )
            self._connection.commit()

    def get_user_by_reset_token(self, token_hash: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM users WHERE pw_reset_token=? AND deleted_at='' LIMIT 1",
                (token_hash,),
            ).fetchone()
        return dict(row) if row else None

    def consume_reset_token(self, user_id: str, new_password_hash: str) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE users SET password_hash=?, pw_reset_token='', pw_reset_expires='' WHERE id=?",
                (new_password_hash, user_id),
            )
            self._connection.commit()

    # ── Account deletion ────────────────────────────────────────────

    def soft_delete_user(self, user_id: str) -> None:
        """Soft-delete: anonymise PII, keep subscription record for billing audit."""
        now = datetime.now(timezone.utc).isoformat()
        anon_email = f"deleted_{user_id}@deleted.invalid"
        with self._lock:
            self._connection.execute(
                """UPDATE users SET
                    email=?, display_name='', password_hash='', google_id='', apple_id='',
                    totp_secret='', totp_enabled=0, totp_backup_codes='[]',
                    email_verify_token='', pw_reset_token='', deleted_at=?
                   WHERE id=?""",
                (anon_email, now, user_id),
            )
            # Revoke all sessions
            self._connection.execute(
                "UPDATE sessions SET revoked=1 WHERE user_id=?", (user_id,)
            )
            self._connection.commit()

    # ── Onboarding ──────────────────────────────────────────────────

    def mark_onboarded(self, user_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE users SET onboarded=1 WHERE id=?", (user_id,)
            )
            self._connection.commit()

    # ── Data export (GDPR) ──────────────────────────────────────────

    def export_user_data(self, user_id: str) -> dict[str, Any]:
        with self._lock:
            user = self._connection.execute(
                "SELECT id, email, display_name, created_at, last_login_at, email_verified, onboarded FROM users WHERE id=?",
                (user_id,),
            ).fetchone()
            sub = self._connection.execute(
                "SELECT subscription_status, plan, subscription_expires_at FROM subscriptions WHERE user_id=? LIMIT 1",
                (user_id,),
            ).fetchone()
            sessions = self._connection.execute(
                "SELECT id, created_at, expires_at, ip_address FROM sessions WHERE user_id=? AND revoked=0",
                (user_id,),
            ).fetchall()
        return {
            "account":      dict(user) if user else {},
            "subscription": dict(sub)  if sub  else {},
            "sessions":     [dict(s) for s in sessions],
        }

    # ── Data retention (scheduled cleanup) ─────────────────────────

    def purge_deleted_users(self, older_than_days: int = 30) -> int:
        cutoff = (datetime.now(timezone.utc).timestamp() - older_than_days * 86400)
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
        with self._lock:
            cur = self._connection.execute(
                "DELETE FROM users WHERE deleted_at != '' AND deleted_at < ?",
                (cutoff_iso,),
            )
            self._connection.commit()
        return cur.rowcount

    def purge_old_events(self, older_than_days: int = 90) -> int:
        cutoff_ts = datetime.now(timezone.utc).timestamp() - older_than_days * 86400
        cutoff_dt = datetime.fromtimestamp(cutoff_ts, tz=timezone.utc).isoformat()
        with self._lock:
            cur = self._connection.execute(
                "DELETE FROM events WHERE collected_at < ?", (cutoff_dt,)
            )
            self._connection.commit()
        return cur.rowcount
