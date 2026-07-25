"""
ooe/scoring.py
~~~~~~~~~~~~~~
Event scoring, ingestion consolidation, and user-state derivation.

This module is self-contained: it holds all constants shared across the engine
plus every scoring function.  ooe/prediction.py imports helpers from here;
ooe/analysis.py is a thin backwards-compatibility shim that re-exports from both.
"""
from __future__ import annotations

import json
import hashlib
import math
import os
import re
import ssl
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from statistics import fmean, pstdev
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

import certifi

from .models import NormalizedEvent, utcnow_iso

CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "disaster": ("earthquake", "flood", "landslide", "eruption", "wildfire", "tsunami", "disaster"),
    "weather": ("storm", "cyclone", "hurricane", "typhoon", "rainfall", "heatwave", "cold wave"),
    "environment": ("drought", "pollution", "air quality", "climate", "water shortage", "ecosystem"),
    "politics": ("election", "parliament", "minister", "policy", "cabinet", "vote", "government"),
    "economy": ("market", "inflation", "gdp", "trade", "interest rate", "layoff", "currency"),
    "crime": ("murder", "shooting", "crime", "arrest", "kidnapping", "stabbing", "fraud"),
    "conflict": ("war", "missile", "troop", "ceasefire", "military", "strike", "shelling"),
    "health": ("outbreak", "virus", "hospital", "disease", "health", "pandemic", "epidemic"),
    "social": ("protest", "strike action", "march", "demonstration", "boycott", "movement"),
    "culture": ("festival", "film", "concert", "museum", "award", "cultural"),
    "technology": ("ai", "cyber", "software", "outage", "launch", "technology", "robot"),
    "infrastructure": ("bridge", "power outage", "rail", "airport", "port", "telecom"),
}

CATEGORY_DIMENSIONS: dict[str, tuple[float, float, float]] = {
    "crime": (0.98, 0.48, 0.76),
    "conflict": (1.00, 0.68, 0.82),
    "disaster": (0.92, 0.58, 0.84),
    "weather": (0.78, 0.42, 0.70),
    "environment": (0.72, 0.50, 0.58),
    "politics": (0.58, 0.88, 0.44),
    "economy": (0.52, 0.84, 0.40),
    "health": (0.82, 0.74, 0.64),
    "social": (0.62, 0.68, 0.52),
    "culture": (0.24, 0.28, 0.18),
    "technology": (0.42, 0.72, 0.34),
    "infrastructure": (0.64, 0.66, 0.72),
    "other": (0.40, 0.42, 0.30),
}

CATEGORY_ALIASES = {
    "severe storms": "weather",
    "wildfires": "disaster",
    "volcanoes": "disaster",
    "sea and lake ice": "environment",
    "drought": "environment",
    "dust and haze": "environment",
    "landslides": "disaster",
    "floods": "disaster",
    "earthquakes": "disaster",
}

SYSTEM_BY_CATEGORY = {
    "conflict": "geopolitics",
    "politics": "governance",
    "economy": "markets",
    "technology": "technology",
    "crime": "civil_stability",
    "health": "public_health",
    "disaster": "environment",
    "weather": "environment",
    "environment": "environment",
    "social": "civic_pressure",
    "infrastructure": "infrastructure",
    "culture": "social_climate",
    "other": "general",
}

SYSTEM_LABELS = {
    "geopolitics": "Geopolitics",
    "governance": "Governance",
    "markets": "Markets",
    "technology": "Technology",
    "civil_stability": "Civil Stability",
    "public_health": "Public Health",
    "environment": "Environment",
    "civic_pressure": "Civic Pressure",
    "infrastructure": "Infrastructure",
    "social_climate": "Social Climate",
    "general": "General Pressure",
}

SYSTEM_ORDER = [
    "geopolitics",
    "governance",
    "markets",
    "environment",
    "civil_stability",
    "public_health",
    "technology",
    "infrastructure",
    "civic_pressure",
    "social_climate",
]

TITLE_TOKEN_RE = re.compile(r"[^a-z0-9]+")
JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
DECISION_AI_CACHE: dict[tuple[str, str, str, str, str], tuple[dict[str, float], list[str], str, str]] = {}
AI_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

DECISION_EFFECT_RULES: tuple[tuple[tuple[str, ...], dict[str, float], str], ...] = (
    (("ceasefire", "talks", "de-escalation", "negotiation"), {"geopolitics": -16, "markets": 4, "civil_stability": -6}, "conflict diffusion"),
    (("truce", "peacekeeping", "demobilize", "withdrawal"), {"geopolitics": -12, "civil_stability": -8, "civic_pressure": -4}, "security de-intensification"),
    (("sanction", "embargo", "tariff"), {"markets": 11, "geopolitics": 8, "technology": 4}, "coercive economic pressure"),
    (("price cap", "export ban", "import ban", "asset freeze"), {"markets": 10, "geopolitics": 7, "infrastructure": 4}, "trade friction shock"),
    (("relief", "aid", "humanitarian", "rescue"), {"environment": -10, "public_health": -8, "civil_stability": -4}, "humanitarian mitigation"),
    (("airlift", "corridor", "reopen port", "supply corridor"), {"infrastructure": -8, "public_health": -5, "markets": -4}, "logistics normalization"),
    (("rate cut", "stimulus", "liquidity", "subsidy"), {"markets": -8, "governance": 4, "infrastructure": -2}, "economic stabilization"),
    (("crackdown", "surge", "retaliation", "offensive"), {"geopolitics": 12, "civil_stability": 10, "civic_pressure": 8}, "force escalation"),
    (("mobilization", "troop deployment", "airstrike", "missile", "blockade"), {"geopolitics": 14, "markets": 8, "civil_stability": 9}, "hard-power escalation"),
    (("cyberattack", "cyber attack", "ransomware", "internet shutdown", "internet blackout"), {"technology": 14, "infrastructure": 11, "markets": 6}, "digital disruption"),
    (("evacuate", "evacuation", "pause", "shutdown"), {"environment": -5, "public_health": -3, "infrastructure": 4}, "protective slowdown"),
    (("curfew", "emergency law", "martial law"), {"civil_stability": 9, "governance": 8, "civic_pressure": 7}, "domestic control tightening"),
    (("vaccination", "testing", "quarantine", "mask mandate"), {"public_health": -10, "civil_stability": -3, "markets": -2}, "public-health containment"),
    (("inspection", "audit", "probe", "investigation"), {"governance": -4, "technology": -3, "civil_stability": -2}, "compliance tightening"),
    (("election", "vote", "cabinet", "policy"), {"governance": 7, "markets": 3, "civic_pressure": 4}, "political realignment"),
    (("election delay", "constitutional crisis", "parliament suspended"), {"governance": 12, "civic_pressure": 10, "markets": 6}, "institutional instability"),
)

OFFICIAL_SOURCE_NAMES = {
    "USGS Significant Earthquakes",
    "USGS All Earthquakes Past Day",
    "NASA EONET Open Events",
    "GDACS Alerts",
    "NWS Active Alerts",
}

TRUSTED_SOURCE_NAMES = {
    "BBC World",
    "BBC Business",
    "BBC Science & Environment",
    "NPR World",
    "DW World",
    "Guardian World",
    "Al Jazeera",
    "CNN World",
    "TIME",
    "The Hindu International",
    "India Today World",
    "NYPost World",
}

STORY_STOPWORDS = {
    "after",
    "amid",
    "announces",
    "before",
    "could",
    "during",
    "faces",
    "first",
    "from",
    "have",
    "into",
    "over",
    "says",
    "saying",
    "still",
    "than",
    "that",
    "their",
    "there",
    "these",
    "this",
    "warns",
    "with",
    "world",
    "news",
}

TREND_GENERIC_TOKENS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "because",
    "been",
    "being",
    "back",
    "but",
    "can",
    "could",
    "down",
    "drop",
    "drops",
    "fall",
    "falls",
    "get",
    "gets",
    "got",
    "had",
    "head",
    "headed",
    "heads",
    "high",
    "higher",
    "hit",
    "hits",
    "by",
    "for",
    "from",
    "global",
    "has",
    "have",
    "if",
    "in",
    "is",
    "it",
    "its",
    "just",
    "keep",
    "last",
    "likely",
    "low",
    "lower",
    "breaking",
    "bbc",
    "cnn",
    "dw",
    "gdacs",
    "guardian",
    "nasa",
    "npr",
    "report",
    "reports",
    "reuters",
    "time",
    "times",
    "update",
    "updates",
    "usgs",
    "latest",
    "today",
    "tomorrow",
    "yesterday",
    "week",
    "weeks",
    "month",
    "months",
    "year",
    "years",
    "world",
    "news",
    "new",
    "one",
    "or",
    "our",
    "over",
    "pump",
    "pumped",
    "pumps",
    "rise",
    "rises",
    "said",
    "say",
    "says",
    "still",
    "than",
    "that",
    "the",
    "their",
    "them",
    "there",
    "these",
    "they",
    "this",
    "those",
    "through",
    "top",
    "tops",
    "under",
    "up",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "who",
    "will",
    "with",
    "would",
}

TREND_TOKEN_SYNONYMS = {
    "agentic": "autonomous",
    "agents": "autonomous",
    "autonomy": "autonomous",
    "browser": "browsing",
    "browsers": "browsing",
    "assistant": "copilot",
    "assistants": "copilot",
    "ai": "ai",
    "generative": "genai",
    "genai": "genai",
}

SOURCE_QUALITY_WEIGHTS = {
    "official": 1.0,
    "trusted": 0.88,
    "local": 0.72,
    "unknown": 0.58,
    "demo": 0.25,
}

TREND_WINDOW_HOURS_DEFAULT = 168
TREND_MIN_SIGNALS_DEFAULT = 2
TREND_MAX_ITEMS_DEFAULT = 8
TREND_BUCKET_HOURS = 6
TREND_GROWTH_WINDOW_HOURS = 24
TREND_PRIOR_WINDOW_HOURS = 72


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, value))


def parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        if value > 1_000_000_000_000:
            value = value / 1000
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)

    text = str(value).strip()
    if not text:
        return None

    try:
        if text.endswith("Z"):
            return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
        return datetime.fromisoformat(text).astimezone(timezone.utc)
    except ValueError:
        pass

    try:
        return parsedate_to_datetime(text).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def isoformat(value: Any) -> str:
    parsed = parse_datetime(value)
    if not parsed:
        return utcnow_iso()
    return parsed.replace(microsecond=0).isoformat()


def canonicalize_category(value: str | None) -> str:
    if not value:
        return "other"
    lowered = value.strip().lower()
    if lowered in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[lowered]
    for category in CATEGORY_KEYWORDS:
        if lowered == category:
            return category
    return lowered if lowered in CATEGORY_DIMENSIONS else "other"


def system_for_category(category: str | None) -> str:
    return SYSTEM_BY_CATEGORY.get(canonicalize_category(category), "general")


def infer_category(title: str, summary: str, tags: Iterable[str], category_hint: str | None = None) -> str:
    hint = canonicalize_category(category_hint)
    if hint != "other":
        return hint

    haystack = " ".join([title, summary, " ".join(tags)]).lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in haystack for keyword in keywords):
            return category
    return "other"


def stable_event_id(source_name: str, external_id: str | None, title: str, happened_at: str) -> str:
    normalized_external_id = (external_id or "").strip()
    if normalized_external_id:
        seed = "::".join([source_name, normalized_external_id])
    else:
        seed = "::".join([source_name, "", title, happened_at])
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return digest[:20]


def normalize_title(title: str) -> str:
    lowered = TITLE_TOKEN_RE.sub(" ", title.lower()).strip()
    parts = [part for part in lowered.split() if len(part) > 2]
    return " ".join(parts[:8])


def stable_cluster_id(category: str, title: str, region: str, happened_at: str) -> str:
    parsed = parse_datetime(happened_at) or utcnow()
    hour_bucket = parsed.replace(minute=0, second=0, microsecond=0).isoformat()
    seed = "::".join(
        [
            canonicalize_category(category),
            normalize_title(title),
            region.lower().strip(),
            hour_bucket,
        ]
    )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]


def source_tier_for_event(source_name: str, partial_event: dict[str, Any] | None = None) -> str:
    explicit = ""
    if partial_event:
        explicit = str(
            partial_event.get("source_tier")
            or (partial_event.get("raw_payload") or {}).get("source_tier")
            or ""
        ).strip().lower()
    if explicit in {"official", "trusted", "local", "demo"}:
        return explicit
    if source_name in OFFICIAL_SOURCE_NAMES:
        return "official"
    if source_name in TRUSTED_SOURCE_NAMES:
        return "trusted"
    if source_name in {"Local Inbox", "Inbox API"}:
        return "local"
    if "demo" in source_name.lower():
        return "demo"
    return "unknown"


def significant_story_tokens(title: str, region: str = "", country: str = "") -> list[str]:
    region_tokens = set(TITLE_TOKEN_RE.sub(" ", f"{region} {country}".lower()).split())
    tokens = [
        token
        for token in TITLE_TOKEN_RE.sub(" ", title.lower()).split()
        if len(token) > 2 and token not in STORY_STOPWORDS and token not in region_tokens
    ]
    return tokens[:6]


def corroboration_key(event: NormalizedEvent) -> str:
    tier = source_tier_for_event(event.source_name, event.raw_payload)
    if tier == "official":
        if event.source_name == "GDACS Alerts":
            parsed = parse_datetime(event.happened_at) or utcnow()
            seed = "::".join(
                [
                    "official",
                    event.source_name,
                    normalize_title(event.title),
                    normalize_title(event.region or event.country or event.location_name),
                    parsed.date().isoformat(),
                ]
            )
            return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]
        if event.source_name == "NWS Active Alerts":
            return event.cluster_id or stable_cluster_id(event.category, event.title, event.region or event.country, event.happened_at)
        seed = "::".join(
            [
                "official",
                event.source_name,
                event.external_id or normalize_title(event.title),
                event.happened_at[:13],
            ]
        )
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]

    region_label = normalize_title(event.country or event.region or event.location_name or "global")
    tokens = significant_story_tokens(event.title, event.region, event.country)
    token_seed = " ".join(tokens or normalize_title(event.title).split()[:5] or [event.category])
    parsed = parse_datetime(event.happened_at) or utcnow()
    bucket_hour = (parsed.hour // 6) * 6
    bucket = parsed.replace(hour=bucket_hour, minute=0, second=0, microsecond=0).isoformat()
    seed = "::".join(["story", event.category, region_label, token_seed, bucket])
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]


def derive_severity(category: str, title: str, summary: str, magnitude: float = 0.0) -> float:
    if magnitude > 0:
        return clamp(20.0 + (magnitude * 11.5))

    haystack = f"{title} {summary}".lower()
    base = {
        "disaster": 72,
        "weather": 62,
        "environment": 58,
        "politics": 52,
        "economy": 54,
        "crime": 76,
        "conflict": 82,
        "health": 68,
        "social": 48,
        "culture": 28,
        "technology": 40,
        "infrastructure": 56,
        "other": 34,
    }.get(category, 34)

    if any(word in haystack for word in ("massive", "major", "critical", "emergency", "state of disaster")):
        base += 16
    if any(word in haystack for word in ("fatal", "killed", "dead", "collapse", "explosion")):
        base += 14
    if any(word in haystack for word in ("minor", "contained", "small", "localized")):
        base -= 12
    return clamp(base)


def derive_confidence(source_name: str, partial_event: dict[str, Any]) -> float:
    trusted_sources = {
        "USGS Significant Earthquakes": 0.96,
        "USGS All Earthquakes Past Day": 0.95,
        "NASA EONET Open Events": 0.92,
        "GDACS Alerts": 0.93,
        "NWS Active Alerts": 0.94,
        "GDACS All Events 24h": 0.88,
        "ReliefWeb Reports": 0.86,
        "ReliefWeb": 0.86,
        "OpenAQ": 0.84,
        "GDELT Global News": 0.78,
        "ACLED": 0.86,
        "WHO Outbreaks": 0.9,
        "BBC World": 0.84,
        "BBC Business": 0.84,
        "BBC Science & Environment": 0.84,
        "NPR World": 0.84,
        "DW World": 0.84,
        "Guardian World": 0.84,
        "Al Jazeera": 0.83,
        "Reddit": 0.48,
        "Bluesky": 0.46,
        "Local Inbox": 0.72,
        "Inbox API": 0.72,
    }
    completeness = 0.0
    for key in ("title", "summary", "happened_at", "location_name", "region", "country", "latitude", "longitude", "url"):
        if partial_event.get(key) not in (None, "", [], {}):
            completeness += 0.09
    if partial_event.get("external_id"):
        completeness += 0.08
    if partial_event.get("raw_payload"):
        completeness += 0.06
    base = trusted_sources.get(source_name, 0.62)
    return round(clamp((base * 100) + (completeness * 100 * 0.35), 28, 99), 2)


def derive_coverage_score(source_name: str, category: str, region: str, tags: Iterable[str]) -> float:
    score = 40.0
    lowered_source = source_name.lower()
    if any(word in lowered_source for word in ("usgs", "nasa", "gdacs", "reliefweb", "who", "openaq", "acled", "gdelt")):
        score += 22
    if region:
        score += 10
    if category in {"conflict", "crime", "politics", "economy"}:
        score += 8
    if category in {"disaster", "weather", "health", "environment"}:
        score += 12
    if len(list(tags)) >= 3:
        score += 6
    return round(clamp(score, 10, 100), 2)


def recency_multiplier(happened_at: str) -> float:
    event_time = parse_datetime(happened_at)
    if not event_time:
        return 0.95
    hours = (utcnow() - event_time).total_seconds() / 3600
    if hours < 0:
        return 1.05
    if hours <= 12:
        return 1.10
    if hours <= 48:
        return 1.00
    if hours <= 120:
        return 0.82
    return 0.66


def keyword_weight(text: str, keyword_weights: dict[str, float]) -> float:
    weight = 1.0
    lowered = text.lower()
    for keyword, value in keyword_weights.items():
        if keyword.lower() in lowered:
            weight = max(weight, float(value))
    return weight


def region_weight(event: dict[str, Any], region_weights: dict[str, float]) -> float:
    values = " ".join(
        filter(
            None,
            [
                str(event.get("location_name", "")),
                str(event.get("country", "")),
                str(event.get("region", "")),
            ],
        )
    ).lower()
    weight = 1.0
    for region_name, value in region_weights.items():
        if region_name.lower() in values:
            weight = max(weight, float(value))
    return weight


def interaction_boost(event: dict[str, Any], interaction_summary: dict[str, dict[str, float]]) -> float:
    """
    Compute a personal relevance multiplier based on interaction history.

    Uses time-decayed weights (from weighted_interaction_summary) so recent
    engagement counts more than old. Ceiling raised to 1.45 total boost to
    allow genuine personalization without overpowering source signals.
    """
    category_weights = interaction_summary.get("categories", {})
    region_weights = interaction_summary.get("regions", {})

    category = str(event.get("category", "other"))
    region = str(event.get("region") or event.get("country") or event.get("location_name") or "")

    boost = 1.0

    # Category boost: logarithmic scaling so heavy engagement doesn't explode scores
    if category:
        cat_weight = float(category_weights.get(category, 0.0))
        if cat_weight > 0:
            import math
            boost += min(math.log1p(cat_weight) * 0.08, 0.28)

    # Region boost: same logarithmic approach
    if region:
        region_weight_val = float(region_weights.get(region, 0.0))
        if region_weight_val > 0:
            import math
            boost += min(math.log1p(region_weight_val) * 0.07, 0.22)

    # Tag keyword bonus: if user has interacted with this category heavily, tags also matter
    tags = [str(t).lower() for t in (event.get("tags") or [])]
    for tag in tags:
        if tag in category_weights and float(category_weights[tag]) > 0.5:
            boost += 0.04
            break  # one tag bonus max

    return min(boost, 1.55)  # hard ceiling: max 55% boost from behavior


def score_event(
    partial_event: dict[str, Any],
    source_name: str,
    personal_profile: dict[str, Any],
    interaction_summary: dict[str, dict[str, float]] | None = None,
) -> NormalizedEvent:
    interaction_summary = interaction_summary or {"categories": {}, "regions": {}}

    tags = [str(tag).strip() for tag in partial_event.get("tags", []) if str(tag).strip()]
    title = str(partial_event.get("title", "")).strip() or "Untitled event"
    summary = str(partial_event.get("summary", "")).strip()
    category = infer_category(title, summary, tags, str(partial_event.get("category", "")))
    event_type = canonicalize_category(str(partial_event.get("event_type", "")) or category)
    subtype = str(partial_event.get("subtype", "")).strip() or (tags[0] if tags else category)
    magnitude = float(partial_event.get("magnitude") or 0.0)
    severity = float(partial_event.get("severity") or 0.0) or derive_severity(category, title, summary, magnitude)
    urgency = clamp((severity * 0.72) + (magnitude * 4.5))

    category_weights = personal_profile.get("category_interest_weights", {})
    region_weights = personal_profile.get("region_interest_weights", {})
    keyword_weights = personal_profile.get("keyword_interest_weights", {})

    category_interest = float(category_weights.get(category, 1.0))
    region_interest = region_weight(partial_event, region_weights)
    keyword_interest = keyword_weight(f"{title} {summary}", keyword_weights)
    recency = recency_multiplier(str(partial_event.get("happened_at", "")))
    boost = interaction_boost(partial_event, interaction_summary)

    relevance = clamp(
        severity * 0.42 * category_interest * region_interest * keyword_interest * recency * boost
    )

    emotional_factor, cognitive_factor, behavioral_factor = CATEGORY_DIMENSIONS.get(
        category, CATEGORY_DIMENSIONS["other"]
    )
    emotional_load = clamp(severity * emotional_factor * (0.58 + relevance / 180))
    cognitive_load = clamp(severity * cognitive_factor * (0.52 + relevance / 210))
    behavioral_load = clamp(severity * behavioral_factor * (0.50 + relevance / 220))
    personal_impact = clamp((emotional_load * 0.48) + (cognitive_load * 0.30) + (behavioral_load * 0.22))

    raw_happened_at = partial_event.get("happened_at")
    happened_at_missing = parse_datetime(raw_happened_at) is None
    happened_at = isoformat(raw_happened_at)
    collected_at = utcnow_iso()
    external_id = str(partial_event.get("external_id") or partial_event.get("id") or "")
    event_id = str(partial_event.get("event_id") or stable_event_id(source_name, external_id, title, happened_at))
    region_name = str(partial_event.get("region") or partial_event.get("country") or partial_event.get("location_name") or "")
    cluster_id = str(partial_event.get("cluster_id") or stable_cluster_id(category, title, region_name, happened_at))
    source_tier = source_tier_for_event(source_name, partial_event)
    confidence = float(partial_event.get("confidence") or derive_confidence(source_name, partial_event))
    coverage_score = float(
        partial_event.get("coverage_score") or derive_coverage_score(source_name, category, region_name, tags)
    )
    source_refs = partial_event.get("source_refs") or [
        {
            "source_name": source_name,
            "external_id": external_id,
            "url": str(partial_event.get("url", "")).strip(),
            "confidence": confidence,
            "source_tier": source_tier,
        }
    ]
    raw_refs = [str(value) for value in partial_event.get("raw_refs", []) if str(value).strip()]
    time_window_start = isoformat(partial_event.get("time_window_start") or happened_at)
    time_window_end = isoformat(partial_event.get("time_window_end") or happened_at)
    expires_at = isoformat(partial_event.get("expires_at")) if partial_event.get("expires_at") else ""
    actors = [str(actor).strip() for actor in partial_event.get("actors", []) if str(actor).strip()]
    casualties_payload = partial_event.get("casualties") or {}
    casualties = dict(casualties_payload) if isinstance(casualties_payload, dict) else {}
    verification_status = str(
        partial_event.get("verification_status")
        or ("official" if source_tier == "official" else "checking")
    )
    raw_payload = dict(partial_event.get("raw_payload") or partial_event)
    raw_payload.setdefault("source_tier", source_tier)
    raw_payload.setdefault("missing_happened_at", happened_at_missing)

    return NormalizedEvent(
        event_id=event_id,
        source_name=source_name,
        external_id=external_id,
        cluster_id=cluster_id,
        title=title,
        raw_title=str(partial_event.get("raw_title") or title),
        summary=summary,
        category=category,
        event_type=event_type,
        subtype=subtype,
        severity=round(severity, 2),
        magnitude=round(magnitude, 2),
        urgency=round(urgency, 2),
        relevance=round(relevance, 2),
        personal_impact=round(personal_impact, 2),
        emotional_load=round(emotional_load, 2),
        cognitive_load=round(cognitive_load, 2),
        behavioral_load=round(behavioral_load, 2),
        confidence=round(confidence, 2),
        coverage_score=round(coverage_score, 2),
        probability=float(partial_event.get("probability") or 1.0),
        predicted=bool(partial_event.get("predicted", False)),
        status=str(partial_event.get("status", "active")),
        verification_status=verification_status,
        location_name=str(partial_event.get("location_name", "")).strip(),
        country=str(partial_event.get("country", "")).strip(),
        region=str(partial_event.get("region", "")).strip(),
        latitude=(
            float(partial_event["latitude"])
            if partial_event.get("latitude") not in (None, "")
            else None
        ),
        longitude=(
            float(partial_event["longitude"])
            if partial_event.get("longitude") not in (None, "")
            else None
        ),
        time_window_start=time_window_start,
        time_window_end=time_window_end,
        happened_at=happened_at,
        collected_at=collected_at,
        updated_at=collected_at,
        expires_at=expires_at,
        url=str(partial_event.get("url", "")).strip(),
        actors=actors,
        casualties=casualties,
        tags=tags,
        source_refs=list(source_refs),
        raw_refs=raw_refs,
        raw_payload=raw_payload,
    )


def _jaccard_similarity(tokens_a: frozenset[str], tokens_b: frozenset[str]) -> float:
    """Compute Jaccard similarity between two token sets."""
    if not tokens_a or not tokens_b:
        return 0.0
    union = len(tokens_a | tokens_b)
    return len(tokens_a & tokens_b) / union if union > 0 else 0.0


def _event_title_tokens(event: NormalizedEvent) -> frozenset[str]:
    """Meaningful tokens from title for semantic comparison."""
    raw = TITLE_TOKEN_RE.sub(" ", event.title.lower()).split()
    return frozenset(t for t in raw if len(t) > 2 and t not in STORY_STOPWORDS)


def _merge_semantic_duplicates(
    grouped: dict[str, list[NormalizedEvent]],
    *,
    similarity_threshold: float = 0.45,
    time_window_hours: float = 3.0,
) -> dict[str, list[NormalizedEvent]]:
    """
    Second-pass semantic dedup using Jaccard on title tokens.
    Catches stories reported with different wording by different sources
    (e.g. 'ceasefire talks resume' vs 'peace negotiations underway').
    Only merges same-category events within a tight time window.
    """
    group_keys = list(grouped.keys())
    if len(group_keys) < 2:
        return grouped

    # Representative token set, category and timestamp per group
    group_info: dict[str, tuple[frozenset[str], str, datetime | None]] = {}
    for key in group_keys:
        group = grouped[key]
        if not group:
            continue
        rep = max(group, key=lambda e: (float(e.confidence), float(e.severity)))
        group_info[key] = (
            _event_title_tokens(rep),
            rep.category,
            parse_datetime(rep.happened_at),
        )

    # Union-Find for O(n²) pair merging (n is small per ingest cycle, typically <60)
    parent: dict[str, str] = {k: k for k in group_keys}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, key_a in enumerate(group_keys):
        tokens_a, cat_a, time_a = group_info[key_a]
        if len(tokens_a) < 2:
            continue
        for key_b in group_keys[i + 1 :]:
            tokens_b, cat_b, time_b = group_info[key_b]
            if cat_a != cat_b:
                continue
            if len(tokens_b) < 2:
                continue
            if time_a and time_b and abs((time_a - time_b).total_seconds()) / 3600 > time_window_hours:
                continue
            if _jaccard_similarity(tokens_a, tokens_b) >= similarity_threshold:
                root_a, root_b = find(key_a), find(key_b)
                if root_a != root_b:
                    parent[root_b] = root_a

    merged: dict[str, list[NormalizedEvent]] = defaultdict(list)
    for key in group_keys:
        root = find(key)
        merged[root].extend(grouped[key])
    return dict(merged)


def _verification_priority(event: NormalizedEvent) -> tuple[int, float, float, float, int]:
    tier = source_tier_for_event(event.source_name, event.raw_payload)
    tier_rank = {"official": 3, "trusted": 2, "local": 1, "unknown": 0, "demo": -1}.get(tier, 0)
    has_coordinates = int(event.latitude is not None and event.longitude is not None)
    return (tier_rank, float(event.confidence), float(event.coverage_score), float(event.severity), has_coordinates)


def _merge_source_refs(events: list[NormalizedEvent]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for event in events:
        for ref in event.source_refs or []:
            key = (
                str(ref.get("source_name", "")),
                str(ref.get("external_id", "")),
                str(ref.get("url", "")),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(dict(ref))
    return merged


def consolidate_ingested_events(events: list[NormalizedEvent], settings: dict[str, Any]) -> list[NormalizedEvent]:
    verification_settings = settings.get("verification", {})
    require_verified = bool(verification_settings.get("require_verified", False))
    minimum_trusted_sources = int(verification_settings.get("minimum_trusted_sources", 2))
    max_age_hours = verification_settings.get("max_age_hours", {})
    default_max_age = float(max_age_hours.get("default", 96))
    tier_max_age = {
        "official": float(max_age_hours.get("official", 192)),
        "trusted": float(max_age_hours.get("trusted", 48)),
        "local": float(max_age_hours.get("local", 24)),
        "unknown": float(max_age_hours.get("unknown", 36)),
        "demo": float(max_age_hours.get("demo", 1)),
    }

    grouped: dict[str, list[NormalizedEvent]] = defaultdict(list)
    for event in events:
        parsed_time = parse_datetime(event.happened_at)
        if parsed_time:
            age_hours = (utcnow() - parsed_time).total_seconds() / 3600
            event_tier = source_tier_for_event(event.source_name, event.raw_payload)
            if age_hours > tier_max_age.get(event_tier, default_max_age):
                continue
        grouped[corroboration_key(event)].append(event)

    # Semantic second pass: merge groups that describe the same story differently
    grouped = _merge_semantic_duplicates(grouped)

    consolidated: list[NormalizedEvent] = []
    for group in grouped.values():
        ordered = sorted(group, key=_verification_priority, reverse=True)
        representative = ordered[0]
        location_owner = next(
            (item for item in ordered if item.latitude is not None and item.longitude is not None),
            representative,
        )
        summary_owner = max(ordered, key=lambda item: len(item.summary or ""))
        source_refs = _merge_source_refs(ordered)
        distinct_sources = {str(ref.get("source_name", "")) for ref in source_refs if str(ref.get("source_name", ""))}
        official_sources = {
            str(ref.get("source_name", ""))
            for ref in source_refs
            if source_tier_for_event(
                str(ref.get("source_name", "")),
                {"source_tier": ref.get("source_tier")},
            )
            == "official"
        }
        trusted_sources = {
            str(ref.get("source_name", ""))
            for ref in source_refs
            if source_tier_for_event(
                str(ref.get("source_name", "")),
                {"source_tier": ref.get("source_tier")},
            )
            == "trusted"
        }

        verification_status = "checking"
        confidence = float(representative.confidence)
        verification_reason = "Awaiting corroboration from additional trusted or official feeds."
        if official_sources:
            verification_status = "official"
            confidence = max(confidence, 96.0)
            verification_reason = f"Confirmed by official source{'s' if len(official_sources) > 1 else ''}: {', '.join(sorted(official_sources)[:3])}."
        elif len(trusted_sources) >= minimum_trusted_sources:
            verification_status = "verified"
            confidence = max(confidence, min(96.0, fmean(float(ref.get('confidence', 0.0) or 0.0) for ref in source_refs) + 6.0))
            verification_reason = f"Cross-checked across {len(trusted_sources)} trusted publishers."

        if require_verified and verification_status == "checking":
            continue

        merged_payload = dict(representative.raw_payload or {})
        merged_payload["verification_reason"] = verification_reason
        merged_payload["corroborating_sources"] = sorted(distinct_sources)

        consolidated.append(
            replace(
                representative,
                cluster_id=corroboration_key(representative),
                source_name=representative.source_name if len(distinct_sources) <= 1 else f"{representative.source_name} + {len(distinct_sources) - 1}",
                summary=summary_owner.summary or representative.summary,
                confidence=round(confidence, 2),
                verification_status=verification_status,
                coverage_score=round(
                    max(
                        representative.coverage_score,
                        min(100.0, float(representative.coverage_score) + max(len(distinct_sources) - 1, 0) * 9.0),
                    ),
                    2,
                ),
                location_name=location_owner.location_name or representative.location_name,
                country=location_owner.country or representative.country,
                region=location_owner.region or representative.region,
                latitude=location_owner.latitude if location_owner.latitude is not None else representative.latitude,
                longitude=location_owner.longitude if location_owner.longitude is not None else representative.longitude,
                source_refs=source_refs,
                raw_payload=merged_payload,
            )
        )

    consolidated.sort(key=lambda item: (str(item.happened_at), float(item.severity), float(item.confidence)), reverse=True)
    return consolidated


def derive_user_state(
    personal_profile: dict[str, Any],
    recent_events: list[dict[str, Any]],
    recent_interactions: list[dict[str, Any]],
    last_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    baseline = dict(personal_profile.get("baseline_state") or {})
    stress = float((last_snapshot or {}).get("stress", baseline.get("stress", 35)))
    focus = float((last_snapshot or {}).get("focus", baseline.get("focus", 65)))
    mood = float((last_snapshot or {}).get("mood", baseline.get("mood", 60)))

    impactful = recent_events[:30]
    if impactful:
        stress += fmean(float(event.get("personal_impact", 0.0)) for event in impactful) * 0.12
        focus -= fmean(float(event.get("cognitive_load", 0.0)) for event in impactful) * 0.10
        mood -= fmean(float(event.get("emotional_load", 0.0)) for event in impactful) * 0.08

    if recent_interactions:
        stress += sum(float(item.get("stress_delta", 0.0)) for item in recent_interactions) * 0.45
        focus += sum(float(item.get("focus_delta", 0.0)) for item in recent_interactions) * 0.45
        mood += sum(float(item.get("mood_delta", 0.0)) for item in recent_interactions) * 0.45

    stress = clamp(stress)
    focus = clamp(focus)
    mood = clamp(mood)
    cognitive_load = clamp(max(0.0, 55 + (stress - focus) * 0.6))

    return {
        "stress": round(stress, 2),
        "focus": round(focus, 2),
        "mood": round(mood, 2),
        "cognitive_load": round(cognitive_load, 2),
        "updated_at": utcnow_iso(),
    }


