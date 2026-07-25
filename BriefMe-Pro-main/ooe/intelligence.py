from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import fmean
from typing import Any


TOKEN_RE = re.compile(r"[^a-z0-9]+")

TOPIC_STOPWORDS = {
    "about",
    "after",
    "amid",
    "and",
    "are",
    "because",
    "before",
    "being",
    "between",
    "breaking",
    "from",
    "global",
    "has",
    "have",
    "into",
    "just",
    "latest",
    "news",
    "over",
    "report",
    "reports",
    "said",
    "says",
    "still",
    "that",
    "their",
    "there",
    "this",
    "today",
    "update",
    "world",
}

CATEGORY_IMPLICATIONS = {
    "conflict": "Watch for spillover into trade routes, energy pricing, and regional security posture.",
    "economy": "Watch for second-order effects in pricing, credit conditions, and consumer behavior.",
    "politics": "Watch for policy execution risk, institutional response speed, and sentiment shifts.",
    "disaster": "Watch for infrastructure stress, humanitarian demand, and cascading logistics disruption.",
    "weather": "Watch for mobility disruption, grid stress, and supply-chain delays.",
    "environment": "Watch for longer-term adaptation costs, compliance shifts, and operational constraints.",
    "health": "Watch for healthcare system load, workforce availability, and regulatory intervention.",
    "technology": "Watch for platform shifts, capability gaps, and fast-moving competitive repositioning.",
    "infrastructure": "Watch for service reliability risk and concentrated points of operational failure.",
    "crime": "Watch for public-confidence impact and near-term policy/security response changes.",
    "social": "Watch for behavior change, attention migration, and coordinated action effects.",
    "culture": "Watch for shifts in mainstream attention and narrative influence.",
    "other": "Watch for cross-system spillovers as the signal matures.",
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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return _utcnow().replace(microsecond=0).isoformat()


def _clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, value))


def _safe_float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _safe_confidence_percent(value: Any) -> float:
    numeric = _safe_float(value, 0.0)
    if numeric <= 0:
        return 0.0
    if numeric <= 1.0:
        numeric *= 100.0
    return _clamp(numeric, 0.0, 100.0)


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)

    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
        return datetime.fromisoformat(text).astimezone(timezone.utc)
    except ValueError:
        return None


def _canonical_category(value: Any) -> str:
    lowered = str(value or "other").strip().lower()
    if lowered in SYSTEM_BY_CATEGORY:
        return lowered
    return "other"


def _system_for_category(category: str) -> str:
    return SYSTEM_BY_CATEGORY.get(_canonical_category(category), "general")


def _normalize_token(token: str) -> str:
    normalized = TOKEN_RE.sub("", token.strip().lower())
    if len(normalized) < 3:
        return ""
    if normalized in TOPIC_STOPWORDS:
        return ""
    if normalized.isdigit():
        return ""
    return normalized


def _tokenize_text(value: str, *, limit: int = 12) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for raw in TOKEN_RE.sub(" ", str(value or "").lower()).split():
        token = _normalize_token(raw)
        if not token or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
        if len(tokens) >= limit:
            break
    return tokens


def _event_tokens(event: dict[str, Any], *, limit: int = 12) -> list[str]:
    title_tokens = _tokenize_text(str(event.get("title", "")), limit=limit)
    summary_tokens = _tokenize_text(str(event.get("summary", "")), limit=limit)
    tags_tokens: list[str] = []
    for tag in event.get("tags") or []:
        tags_tokens.extend(_tokenize_text(str(tag), limit=4))
    merged = title_tokens + tags_tokens + summary_tokens
    deduped: list[str] = []
    seen: set[str] = set()
    for token in merged:
        if token in seen:
            continue
        seen.add(token)
        deduped.append(token)
        if len(deduped) >= limit:
            break
    return deduped or [_canonical_category(event.get("category"))]


def _region_label(event: dict[str, Any]) -> str:
    return str(event.get("region") or event.get("country") or event.get("location_name") or "Global").strip() or "Global"


def _story_key(event: dict[str, Any]) -> str:
    category = _canonical_category(event.get("category"))
    region = TOKEN_RE.sub(" ", _region_label(event).lower()).strip()
    tokens = _event_tokens(event, limit=3)
    topic = " ".join(tokens[:2]) if tokens else category
    seed = f"{category}|{region}|{topic}"
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20]


def _topic_key(event: dict[str, Any]) -> str:
    category = _canonical_category(event.get("category"))
    tokens = _event_tokens(event, limit=3)
    signature = " ".join(tokens[:2]).strip() or category
    return f"{category}:{signature}"


def _verification_weight(event: dict[str, Any]) -> float:
    state = str(event.get("verification_status", "")).strip().lower()
    if state == "official":
        return 1.0
    if state == "verified":
        return 0.85
    if state == "checking":
        return 0.55
    return 0.45


def _source_tier_weight(event: dict[str, Any]) -> float:
    refs = event.get("source_refs") or []
    tier = ""
    if refs:
        tier = str(refs[0].get("source_tier") or "").strip().lower()
    if not tier:
        source_name = str(event.get("source_name") or "").lower()
        if any(key in source_name for key in ("usgs", "nasa", "gdacs", "who")):
            tier = "official"
        elif source_name:
            tier = "trusted"
        else:
            tier = "unknown"
    return {"official": 1.0, "trusted": 0.86, "local": 0.68, "unknown": 0.55, "demo": 0.25}.get(tier, 0.55)


def _build_story_stats(events: list[dict[str, Any]], now: datetime) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for event in events:
        story_key = _story_key(event)
        happened = _parse_datetime(event.get("happened_at")) or now
        age_hours = max((now - happened).total_seconds() / 3600.0, 0.0)
        row = stats.setdefault(
            story_key,
            {
                "total": 0,
                "recent": 0,
                "prior": 0,
                "source_names": set(),
            },
        )
        row["total"] += 1
        if age_hours <= 24:
            row["recent"] += 1
        elif age_hours <= 96:
            row["prior"] += 1
        source_name = str(event.get("source_name", "")).strip()
        if source_name:
            row["source_names"].add(source_name)

    for row in stats.values():
        row["source_count"] = len(row["source_names"])
        row["source_names"] = sorted(row["source_names"])
    return stats


def _build_source_trust_profiles(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    now = _utcnow()
    source_rows: dict[str, dict[str, Any]] = {}
    story_first_seen: dict[str, tuple[datetime, set[str]]] = {}

    for event in events:
        source_name = str(event.get("source_name", "")).strip() or "Unknown source"
        happened = _parse_datetime(event.get("happened_at")) or now
        story_key = _story_key(event)
        first_seen = story_first_seen.get(story_key)
        if first_seen is None:
            story_first_seen[story_key] = (happened, {source_name})
        else:
            earliest_time, reporters = first_seen
            if happened < earliest_time - timedelta(minutes=20):
                story_first_seen[story_key] = (happened, {source_name})
            elif abs((happened - earliest_time).total_seconds()) <= 90 * 60:
                reporters.add(source_name)

    for event in events:
        source_name = str(event.get("source_name", "")).strip() or "Unknown source"
        row = source_rows.setdefault(
            source_name,
            {
                "count": 0,
                "confidence_sum": 0.0,
                "verified_count": 0,
                "summary_len_sum": 0.0,
                "signal_count": 0,
                "noise_count": 0,
                "stories": set(),
                "first_story_keys": set(),
                "regions": set(),
                "categories": set(),
            },
        )

        story_key = _story_key(event)
        row["count"] += 1
        row["confidence_sum"] += _safe_confidence_percent(event.get("confidence"))
        row["summary_len_sum"] += len(str(event.get("summary", "")).strip())
        if str(event.get("verification_status", "")).strip().lower() in {"official", "verified"}:
            row["verified_count"] += 1
        if _safe_float(event.get("severity"), 0.0) >= 55 or _safe_float(event.get("urgency"), 0.0) >= 52:
            row["signal_count"] += 1
        if _safe_float(event.get("severity"), 0.0) < 35 and _safe_confidence_percent(event.get("confidence")) < 65:
            row["noise_count"] += 1
        row["stories"].add(story_key)
        row["regions"].add(_region_label(event))
        row["categories"].add(_canonical_category(event.get("category")))

        first_seen = story_first_seen.get(story_key)
        if first_seen and source_name in first_seen[1]:
            row["first_story_keys"].add(story_key)

    profiles: dict[str, dict[str, Any]] = {}
    for source_name, row in source_rows.items():
        count = max(int(row["count"]), 1)
        avg_confidence = row["confidence_sum"] / count
        verified_ratio = row["verified_count"] / count
        unique_story_ratio = len(row["stories"]) / count
        first_report_ratio = len(row["first_story_keys"]) / max(len(row["stories"]), 1)
        avg_depth = row["summary_len_sum"] / count
        depth_score = _clamp((avg_depth / 420.0) * 100.0, 12.0, 100.0)
        signal_noise = _clamp((row["signal_count"] / max(row["signal_count"] + row["noise_count"], 1)) * 100.0, 5.0, 100.0)
        diversity = _clamp((len(row["regions"]) * 6.0) + (len(row["categories"]) * 8.0), 0.0, 100.0)

        historical_accuracy = _clamp((avg_confidence * 0.55) + (verified_ratio * 100.0 * 0.45), 0.0, 100.0)
        originality = _clamp((unique_story_ratio * 70.0) + (first_report_ratio * 30.0), 0.0, 100.0)

        trust_score = _clamp(
            (historical_accuracy * 0.34)
            + (originality * 0.22)
            + (depth_score * 0.16)
            + (signal_noise * 0.16)
            + (diversity * 0.12),
            0.0,
            100.0,
        )

        profiles[source_name] = {
            "source_name": source_name,
            "trust_score": round(trust_score, 2),
            "historical_accuracy": round(historical_accuracy, 2),
            "originality": round(originality, 2),
            "depth": round(depth_score, 2),
            "signal_noise_ratio": round(signal_noise, 2),
            "sample_size": count,
            "first_report_ratio": round(first_report_ratio, 3),
            "verified_ratio": round(verified_ratio, 3),
            "updated_at": _utcnow_iso(),
        }

    return profiles


def _build_narrative_shifts(
    events: list[dict[str, Any]],
    now: datetime,
    *,
    min_topic_events: int = 3,
    recent_window_hours: int = 24,
    prior_window_hours: int = 120,
    max_items: int = 12,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        buckets[_topic_key(event)].append(event)

    narrative_rows: list[dict[str, Any]] = []
    narrative_index: dict[str, dict[str, Any]] = {}
    min_recent_events = max(2, min_topic_events - 1)

    for topic_key, topic_events in buckets.items():
        if len(topic_events) < max(2, min_topic_events):
            continue

        recent: list[dict[str, Any]] = []
        prior: list[dict[str, Any]] = []
        recent_tokens: Counter[str] = Counter()
        prior_tokens: Counter[str] = Counter()

        for event in topic_events:
            happened = _parse_datetime(event.get("happened_at")) or now
            age_hours = max((now - happened).total_seconds() / 3600.0, 0.0)
            bucket = (
                recent
                if age_hours <= recent_window_hours
                else prior
                if age_hours <= prior_window_hours
                else None
            )
            if bucket is None:
                continue
            bucket.append(event)
            token_counter = recent_tokens if bucket is recent else prior_tokens
            token_counter.update(_event_tokens(event, limit=6))

        if len(recent) < min_recent_events or len(prior) < 1:
            continue

        recent_tone = fmean((_safe_float(item.get("severity"), 0.0) * 0.72) + (_safe_float(item.get("urgency"), 0.0) * 0.28) for item in recent)
        prior_tone = fmean((_safe_float(item.get("severity"), 0.0) * 0.72) + (_safe_float(item.get("urgency"), 0.0) * 0.28) for item in prior)
        tone_change = recent_tone - prior_tone

        recent_perception = fmean(_safe_confidence_percent(item.get("confidence")) * _verification_weight(item) for item in recent)
        prior_perception = fmean(_safe_confidence_percent(item.get("confidence")) * _verification_weight(item) for item in prior)
        perception_change = recent_perception - prior_perception

        recent_frame = [token for token, _count in recent_tokens.most_common(3)]
        prior_frame = [token for token, _count in prior_tokens.most_common(3)]
        frame_change = [token for token in recent_frame if token not in set(prior_frame)]

        # Jaccard semantic drift: how different are the token vocabularies?
        recent_vocab = frozenset(recent_tokens.keys())
        prior_vocab = frozenset(prior_tokens.keys())
        vocab_union = len(recent_vocab | prior_vocab)
        vocab_overlap = len(recent_vocab & prior_vocab) / vocab_union if vocab_union > 0 else 1.0
        # Low overlap = high semantic drift (topic has pivoted)
        semantic_drift = 1.0 - vocab_overlap

        direction = "steady"
        if tone_change >= 8:
            direction = "intensifying"
        elif tone_change <= -8:
            direction = "stabilizing"
        elif semantic_drift >= 0.55:
            # Vocabulary shifted significantly — this story is being re-framed at a deep level
            direction = "pivoting"
        elif frame_change and semantic_drift >= 0.30:
            direction = "reframing"
        elif frame_change:
            direction = "reframing"

        dominant_narrative = " ".join(recent_frame[:2]).strip() or topic_key.split(":", 1)[-1]

        row = {
            "topic_key": topic_key,
            "dominant_narrative": dominant_narrative,
            "direction": direction,
            "tone_change": round(tone_change, 2),
            "perception_change": round(perception_change, 2),
            "semantic_drift": round(semantic_drift, 3),
            "recent_framing": recent_frame,
            "prior_framing": prior_frame,
            "frame_change": frame_change,
            "timeline": [
                {"phase": "prior", "window": "24-120h", "event_count": len(prior)},
                {"phase": "current", "window": "0-24h", "event_count": len(recent)},
            ],
            "updated_at": _utcnow_iso(),
        }
        narrative_rows.append(row)
        narrative_index[topic_key] = row

    narrative_rows.sort(
        key=lambda item: (
            abs(_safe_float(item.get("tone_change"), 0.0)) + abs(_safe_float(item.get("perception_change"), 0.0)),
            len(item.get("timeline", [])),
        ),
        reverse=True,
    )
    return narrative_rows[:max_items], narrative_index


def _build_global_changes(
    events: list[dict[str, Any]],
    narrative_index: dict[str, dict[str, Any]],
    *,
    min_topic_events: int = 3,
    min_regions: int = 2,
    max_items: int = 10,
) -> list[dict[str, Any]]:
    topic_events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        topic_events[_topic_key(event)].append(event)

    change_rows: list[dict[str, Any]] = []
    for topic_key, rows in topic_events.items():
        if len(rows) < min_topic_events:
            continue
        regions = {_region_label(item) for item in rows}
        if len(regions) < min_regions:
            continue

        category = _canonical_category(rows[0].get("category"))
        system = _system_for_category(category)
        system_label = SYSTEM_LABELS.get(system, system.title())
        narrative = narrative_index.get(topic_key, {})
        direction = str(narrative.get("direction") or "steady")
        tone_change = _safe_float(narrative.get("tone_change"), 0.0)
        confidence = _clamp(
            (min(len(rows), 8) * 9.0)
            + (len(regions) * 7.0)
            + min(abs(tone_change) * 1.8, 18.0),
            0.0,
            100.0,
        )
        topic_label = str(narrative.get("dominant_narrative") or topic_key.split(":", 1)[-1]).strip()
        what_is_changing = (
            f"{topic_label} is {direction} across {len(regions)} regions and is now exerting pressure on {system_label}."
        )
        why_it_matters = (
            f"The signal moved beyond single-source noise and is now persistent enough to shift operating assumptions."
        )
        long_term = CATEGORY_IMPLICATIONS.get(category, CATEGORY_IMPLICATIONS["other"])

        change_rows.append(
            {
                "change_id": hashlib.sha1(topic_key.encode("utf-8")).hexdigest()[:16],
                "topic_key": topic_key,
                "what_is_changing": what_is_changing,
                "why_it_matters": why_it_matters,
                "long_term_implications": long_term,
                "confidence": round(confidence, 2),
                "updated_at": _utcnow_iso(),
            }
        )

    change_rows.sort(key=lambda item: _safe_float(item.get("confidence"), 0.0), reverse=True)
    return change_rows[:max_items]


def _build_opportunity_radar(
    changes: list[dict[str, Any]],
    narrative_index: dict[str, dict[str, Any]],
    *,
    min_score: float = 55.0,
    max_items: int = 10,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    opportunities: list[dict[str, Any]] = []
    opportunity_index: dict[str, dict[str, Any]] = {}

    for change in changes:
        topic_key = str(change.get("topic_key", ""))
        narrative = narrative_index.get(topic_key, {})
        direction = str(narrative.get("direction") or "steady")
        tone_change = abs(_safe_float(narrative.get("tone_change"), 0.0))
        confidence = _safe_float(change.get("confidence"), 0.0)
        score = _clamp((confidence * 0.6) + (tone_change * 1.4) + (12.0 if direction in {"intensifying", "reframing"} else 0.0), 0.0, 100.0)
        if score < min_score:
            continue

        topic_label = str(narrative.get("dominant_narrative") or topic_key.split(":", 1)[-1]).strip()
        opportunity = {
            "opportunity_id": hashlib.sha1(f"opp:{topic_key}".encode("utf-8")).hexdigest()[:16],
            "topic_key": topic_key,
            "opportunity_statement": (
                f"Teams operating around {topic_label} need faster monitoring and response tooling as the signal {direction}."
            ),
            "product_direction": (
                "Build a focused workflow that converts verified signal changes into concrete operator actions."
            ),
            "why_now": (
                "Cross-source validation is rising while framing is still fluid, creating a timing window before consensus hardens."
            ),
            "timing_relevance": "near-term",
            "score": round(score, 2),
            "updated_at": _utcnow_iso(),
        }
        opportunities.append(opportunity)
        opportunity_index[topic_key] = opportunity

    opportunities.sort(key=lambda item: _safe_float(item.get("score"), 0.0), reverse=True)
    return opportunities[:max_items], opportunity_index


def _build_signal_dimensions(
    event: dict[str, Any],
    *,
    source_trust_score: float,
    story_stats: dict[str, dict[str, Any]],
    narrative: dict[str, Any] | None,
    opportunity: dict[str, Any] | None,
) -> tuple[dict[str, float], str, str, str]:
    severity = _clamp(_safe_float(event.get("severity"), 0.0), 0.0, 100.0)
    urgency = _clamp(_safe_float(event.get("urgency"), 0.0), 0.0, 100.0)
    relevance = _clamp(_safe_float(event.get("relevance"), 0.0), 0.0, 100.0)
    personal_impact = _clamp(_safe_float(event.get("personal_impact"), 0.0), 0.0, 100.0)
    importance = _clamp((severity * 0.36) + (urgency * 0.24) + (relevance * 0.22) + (personal_impact * 0.18), 0.0, 100.0)

    story_key = _story_key(event)
    stats = story_stats.get(story_key, {"total": 1, "recent": 1, "prior": 0, "source_count": 1})
    novelty = _clamp(100.0 - min(max(stats["total"] - 1, 0) * 16.0, 84.0), 6.0, 100.0)
    growth = _clamp(
        ((stats["recent"] - stats["prior"]) / (stats["prior"] + 1.0)) * 42.0 + (stats["recent"] * 10.0),
        0.0,
        100.0,
    )

    refs = event.get("source_refs") or []
    cross_validation = _clamp(
        (len(refs) * 26.0)
        + (_verification_weight(event) * 26.0)
        + (min(stats.get("source_count", 1), 5) * 8.0),
        0.0,
        100.0,
    )

    narrative_boost = 0.0
    if narrative:
        narrative_boost += min(abs(_safe_float(narrative.get("tone_change"), 0.0)) * 0.25, 8.0)
        if str(narrative.get("direction", "")) in {"intensifying", "reframing"}:
            narrative_boost += 4.0

    opportunity_boost = min(_safe_float((opportunity or {}).get("score"), 0.0) * 0.06, 6.0)

    intelligence_score = _clamp(
        (importance * 0.28)
        + (novelty * 0.20)
        + (growth * 0.20)
        + (cross_validation * 0.17)
        + (source_trust_score * 0.15)
        + narrative_boost
        + opportunity_boost,
        0.0,
        100.0,
    )

    category = _canonical_category(event.get("category"))
    direction = str((narrative or {}).get("direction") or "steady")
    what_is_changing = (
        "Signal acceleration is visible across sources."
        if growth >= 60
        else "Signal persistence is building with repeat mention."
        if growth >= 35
        else "Signal is present but still forming."
    )
    why_it_matters = (
        f"{_region_label(event)} sits in the active pressure path with trust-weighted corroboration."
        if cross_validation >= 55
        else "This is still early; confidence depends on additional corroboration."
    )
    implications = CATEGORY_IMPLICATIONS.get(category, CATEGORY_IMPLICATIONS["other"])
    if direction in {"intensifying", "reframing"}:
        implications = f"{implications} Narrative direction is currently {direction}."

    return (
        {
            "importance": round(importance, 2),
            "novelty": round(novelty, 2),
            "growth": round(growth, 2),
            "cross_validation": round(cross_validation, 2),
            "source_trust": round(source_trust_score, 2),
            "signal_priority": round(intelligence_score, 2),
        },
        why_it_matters,
        what_is_changing,
        implications,
    )


def build_external_world_intelligence(
    recent_events: list[dict[str, Any]],
    *,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runtime_settings = settings or {}
    ewie_settings = runtime_settings.get("ewie") if isinstance(runtime_settings, dict) else {}
    if not isinstance(ewie_settings, dict):
        ewie_settings = {}

    lookback_hours = int(_clamp(_safe_float(ewie_settings.get("lookback_hours"), 120.0), 24.0, 720.0))
    source_settings = ewie_settings.get("source_trust") if isinstance(ewie_settings.get("source_trust"), dict) else {}
    source_max_items = int(_clamp(_safe_float(source_settings.get("max_items"), 12.0), 3.0, 30.0))

    narrative_settings = ewie_settings.get("narrative") if isinstance(ewie_settings.get("narrative"), dict) else {}
    narrative_min_topic_events = int(
        _clamp(_safe_float(narrative_settings.get("min_topic_events"), 3.0), 2.0, 8.0)
    )
    narrative_recent_window = int(
        _clamp(_safe_float(narrative_settings.get("recent_window_hours"), 24.0), 6.0, 72.0)
    )
    narrative_prior_window = int(
        _clamp(
            _safe_float(narrative_settings.get("prior_window_hours"), 120.0),
            float(narrative_recent_window + 12),
            24.0 * 14.0,
        )
    )
    narrative_max_items = int(_clamp(_safe_float(narrative_settings.get("max_items"), 12.0), 1.0, 30.0))

    global_change_settings = (
        ewie_settings.get("global_change") if isinstance(ewie_settings.get("global_change"), dict) else {}
    )
    global_change_min_topic_events = int(
        _clamp(_safe_float(global_change_settings.get("min_topic_events"), 3.0), 2.0, 10.0)
    )
    global_change_min_regions = int(
        _clamp(_safe_float(global_change_settings.get("min_regions"), 2.0), 1.0, 8.0)
    )
    global_change_max_items = int(_clamp(_safe_float(global_change_settings.get("max_items"), 10.0), 1.0, 20.0))

    opportunity_settings = (
        ewie_settings.get("opportunity") if isinstance(ewie_settings.get("opportunity"), dict) else {}
    )
    opportunity_min_score = _clamp(_safe_float(opportunity_settings.get("min_score"), 55.0), 20.0, 95.0)
    opportunity_max_items = int(_clamp(_safe_float(opportunity_settings.get("max_items"), 10.0), 1.0, 20.0))

    now = _utcnow()
    cutoff = now - timedelta(hours=lookback_hours)
    observed: list[dict[str, Any]] = []
    for event in recent_events:
        if bool(event.get("predicted")):
            continue
        happened = _parse_datetime(event.get("happened_at")) or now
        if happened < cutoff:
            continue
        observed.append(event)

    source_trust_profiles = _build_source_trust_profiles(observed)
    source_trust_scores = {
        source_name: float(profile.get("trust_score", 55.0))
        for source_name, profile in source_trust_profiles.items()
    }
    story_stats = _build_story_stats(observed, now)
    narrative_shifts, narrative_index = _build_narrative_shifts(
        observed,
        now,
        min_topic_events=narrative_min_topic_events,
        recent_window_hours=narrative_recent_window,
        prior_window_hours=narrative_prior_window,
        max_items=narrative_max_items,
    )
    global_changes = _build_global_changes(
        observed,
        narrative_index,
        min_topic_events=global_change_min_topic_events,
        min_regions=global_change_min_regions,
        max_items=global_change_max_items,
    )
    opportunities, opportunity_index = _build_opportunity_radar(
        global_changes,
        narrative_index,
        min_score=opportunity_min_score,
        max_items=opportunity_max_items,
    )

    source_leaderboard = sorted(
        source_trust_profiles.values(),
        key=lambda row: (
            _safe_float(row.get("trust_score"), 0.0),
            _safe_float(row.get("sample_size"), 0.0),
        ),
        reverse=True,
    )[:source_max_items]

    return {
        "updated_at": _utcnow_iso(),
        "source_trust_profiles": source_trust_profiles,
        "source_trust_scores": source_trust_scores,
        "source_leaderboard": source_leaderboard,
        "narrative_shifts": narrative_shifts,
        "narrative_shift_index": narrative_index,
        "global_changes": global_changes,
        "opportunities": opportunities,
        "opportunity_index": opportunity_index,
        "story_stats": story_stats,
    }


def enrich_and_prioritize_events(
    events: list[dict[str, Any]],
    intelligence_snapshot: dict[str, Any] | None,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    snapshot = intelligence_snapshot or {}
    source_scores = snapshot.get("source_trust_scores") or {}
    story_stats = snapshot.get("story_stats") or {}
    narrative_index = snapshot.get("narrative_shift_index") or {}
    opportunity_index = snapshot.get("opportunity_index") or {}

    enriched: list[dict[str, Any]] = []
    for raw_event in events:
        event = dict(raw_event)
        source_name = str(event.get("source_name", "")).strip()
        source_trust = _safe_float(source_scores.get(source_name), 55.0 if source_name else 48.0)
        topic_key = _topic_key(event)
        narrative = narrative_index.get(topic_key)
        opportunity = opportunity_index.get(topic_key)

        dimensions, why_it_matters, what_is_changing, implications = _build_signal_dimensions(
            event,
            source_trust_score=source_trust,
            story_stats=story_stats,
            narrative=narrative,
            opportunity=opportunity,
        )

        what_is_happening = str(event.get("summary") or event.get("title") or "Signal detected.")
        ewie_payload = {
            "what_is_happening": what_is_happening,
            "why_it_matters": why_it_matters,
            "what_is_changing": what_is_changing,
            "possible_implications": implications,
            "topic_key": topic_key,
            "narrative_direction": str((narrative or {}).get("direction") or "steady"),
            "opportunity_score": round(_safe_float((opportunity or {}).get("score"), 0.0), 2),
            **dimensions,
        }

        raw_payload = dict(event.get("raw_payload") or {})
        raw_payload["ewie"] = ewie_payload
        event["raw_payload"] = raw_payload
        event["intelligence_score"] = dimensions["signal_priority"]
        enriched.append(event)

    enriched.sort(
        key=lambda item: (
            _safe_float(item.get("intelligence_score"), 0.0),
            _safe_float(item.get("severity"), 0.0),
            str(item.get("happened_at", "")),
        ),
        reverse=True,
    )

    if limit is not None and limit > 0:
        return enriched[:limit]
    return enriched
