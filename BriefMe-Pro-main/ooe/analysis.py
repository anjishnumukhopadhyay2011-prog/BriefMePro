"""
ooe/analysis.py
~~~~~~~~~~~~~~~
Backwards-compatibility shim.

All logic has been migrated:
  - Scoring, consolidation, user-state → ooe/scoring.py
  - Prediction, trends, simulation, AI → ooe/prediction.py

This file re-exports everything so any code that still imports from
ooe.analysis continues to work unchanged.  New code should import
directly from ooe.scoring or ooe.prediction.
"""
from __future__ import annotations

# Re-export all scoring / engine symbols
from .scoring import (  # noqa: F401
    CATEGORY_ALIASES,
    CATEGORY_DIMENSIONS,
    CATEGORY_KEYWORDS,
    DECISION_AI_CACHE,
    DECISION_EFFECT_RULES,
    OFFICIAL_SOURCE_NAMES,
    STORY_STOPWORDS,
    SYSTEM_BY_CATEGORY,
    SYSTEM_LABELS,
    SYSTEM_ORDER,
    TITLE_TOKEN_RE,
    TREND_BUCKET_HOURS,
    TREND_GENERIC_TOKENS,
    TREND_GROWTH_WINDOW_HOURS,
    TREND_MAX_ITEMS_DEFAULT,
    TREND_MIN_SIGNALS_DEFAULT,
    TREND_PRIOR_WINDOW_HOURS,
    TREND_TOKEN_SYNONYMS,
    TREND_WINDOW_HOURS_DEFAULT,
    TRUSTED_SOURCE_NAMES,
    SOURCE_QUALITY_WEIGHTS,
    canonicalize_category,
    clamp,
    consolidate_ingested_events,
    corroboration_key,
    derive_confidence,
    derive_coverage_score,
    derive_severity,
    derive_user_state,
    infer_category,
    interaction_boost,
    isoformat,
    keyword_weight,
    normalize_title,
    parse_datetime,
    recency_multiplier,
    region_weight,
    score_event,
    significant_story_tokens,
    source_tier_for_event,
    stable_cluster_id,
    stable_event_id,
    system_for_category,
    utcnow,
)

# Re-export all prediction / trend / simulation symbols
from .prediction import (  # noqa: F401
    build_prediction_scenarios,
    build_simulation,
    build_trend_predictions,
    summarize_interactions,
)
