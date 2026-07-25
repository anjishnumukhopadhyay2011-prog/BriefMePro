"""
ooe/profile_adapter.py
~~~~~~~~~~~~~~~~~~~~~~
ProfileAdapter: makes the personal profile self-evolving.

Instead of static category/region weights locked in config.py,
the adapter computes a merged profile at each ingest cycle by
blending the user's configured base weights with signals derived
from their actual interaction history (time-decayed).

The result: if you consistently engage with 'technology' events
and ignore 'culture', the system learns that automatically. The
base profile acts as a prior; behavior shifts it over time.

Design principles:
- Never fully overrides the base config — base weight acts as floor
- Learning rate controls how fast behavior shifts the profile
- Decay ensures old habits don't permanently override new focus
- Weights stay bounded to [0.5, 2.5] to prevent scoring explosions
"""
from __future__ import annotations

import math
from typing import Any

# Bounded weight range to prevent extreme scoring distortions
MIN_WEIGHT = 0.5
MAX_WEIGHT = 2.5

# How strongly behavior adjusts base weights (0.0 = no learning, 1.0 = full override)
DEFAULT_LEARNING_RATE = 0.35

# Minimum cumulative interaction weight before a category gets a behavioral boost
ENGAGEMENT_THRESHOLD = 0.2


def _clamp(value: float, lo: float = MIN_WEIGHT, hi: float = MAX_WEIGHT) -> float:
    return max(lo, min(hi, value))


def _behavioral_weight(
    raw_interaction_weight: float,
    *,
    learning_rate: float = DEFAULT_LEARNING_RATE,
) -> float:
    """
    Convert a raw time-decayed interaction score into a weight multiplier.

    Logarithmic scaling prevents heavy past engagement from permanently
    dominating. A user who interacted with 'conflict' 20 times last week
    gets ~1.6x, not 20x.

    Scale (approximate):
        raw ≈ 0.2  →  adjusted ≈ 1.08  (light engagement)
        raw ≈ 1.0  →  adjusted ≈ 1.24  (moderate)
        raw ≈ 3.0  →  adjusted ≈ 1.39  (heavy)
        raw ≈ 10.0 →  adjusted ≈ 1.55  (very heavy)
    """
    if raw_interaction_weight < ENGAGEMENT_THRESHOLD:
        return 1.0
    log_score = math.log1p(raw_interaction_weight)
    return _clamp(1.0 + log_score * learning_rate * 0.55)


def adapt_profile(
    base_profile: dict[str, Any],
    weighted_summary: dict[str, dict[str, float]],
    *,
    learning_rate: float = DEFAULT_LEARNING_RATE,
) -> dict[str, Any]:
    """
    Merge base profile with behavioral signals to produce an adapted profile.

    Parameters
    ----------
    base_profile:
        The raw personal_profile dict from config.py (never mutated).
    weighted_summary:
        Output of storage.weighted_interaction_summary() — time-decayed
        interaction counts keyed by category and region.
    learning_rate:
        Float 0.0–1.0 controlling how strongly behavior moves base weights.

    Returns
    -------
    A new profile dict with dynamically adjusted category/region weights.
    The original base_profile is not modified.
    """
    adapted = dict(base_profile)

    # --- Category interest weights ---
    base_cat_weights: dict[str, float] = dict(
        base_profile.get("category_interest_weights") or {}
    )
    cat_interactions = weighted_summary.get("categories") or {}

    adapted_cat_weights: dict[str, float] = {}
    all_categories = set(base_cat_weights) | set(cat_interactions)

    for cat in all_categories:
        base_w = float(base_cat_weights.get(cat, 1.0))
        interaction_w = float(cat_interactions.get(cat, 0.0))
        behavioral_w = _behavioral_weight(interaction_w, learning_rate=learning_rate)
        # Blend: base is the anchor, behavior shifts it
        blended = base_w * (1.0 - learning_rate) + (base_w * behavioral_w) * learning_rate
        adapted_cat_weights[cat] = round(_clamp(blended), 4)

    adapted["category_interest_weights"] = adapted_cat_weights

    # --- Region interest weights ---
    base_region_weights: dict[str, float] = dict(
        base_profile.get("region_interest_weights") or {}
    )
    region_interactions = weighted_summary.get("regions") or {}

    adapted_region_weights: dict[str, float] = {}
    all_regions = set(base_region_weights) | set(region_interactions)

    for region in all_regions:
        base_w = float(base_region_weights.get(region, 1.0))
        interaction_w = float(region_interactions.get(region, 0.0))
        behavioral_w = _behavioral_weight(interaction_w, learning_rate=learning_rate)
        blended = base_w * (1.0 - learning_rate) + (base_w * behavioral_w) * learning_rate
        adapted_region_weights[region] = round(_clamp(blended), 4)

    adapted["region_interest_weights"] = adapted_region_weights

    # Attach adaptation metadata so callers can inspect what changed
    adapted["_adapted"] = True
    adapted["_behavioral_categories"] = {
        cat: round(_behavioral_weight(w, learning_rate=learning_rate), 4)
        for cat, w in cat_interactions.items()
        if w >= ENGAGEMENT_THRESHOLD
    }

    return adapted


def profile_drift_report(
    base_profile: dict[str, Any],
    adapted_profile: dict[str, Any],
) -> dict[str, Any]:
    """
    Summarize how much behavior has shifted the profile away from base.
    Useful for debugging and for building a 'your interests' profile panel.
    """
    base_cats = base_profile.get("category_interest_weights") or {}
    adapted_cats = adapted_profile.get("category_interest_weights") or {}

    drifted: list[dict[str, Any]] = []
    for cat in set(base_cats) | set(adapted_cats):
        base_w = float(base_cats.get(cat, 1.0))
        adapt_w = float(adapted_cats.get(cat, 1.0))
        delta = adapt_w - base_w
        if abs(delta) >= 0.05:
            drifted.append({
                "category": cat,
                "base_weight": round(base_w, 3),
                "adapted_weight": round(adapt_w, 3),
                "delta": round(delta, 3),
                "direction": "boosted" if delta > 0 else "reduced",
            })

    drifted.sort(key=lambda x: abs(x["delta"]), reverse=True)

    base_regions = base_profile.get("region_interest_weights") or {}
    adapted_regions = adapted_profile.get("region_interest_weights") or {}
    region_drifts: list[dict[str, Any]] = []
    for region in set(base_regions) | set(adapted_regions):
        base_w = float(base_regions.get(region, 1.0))
        adapt_w = float(adapted_regions.get(region, 1.0))
        delta = adapt_w - base_w
        if abs(delta) >= 0.05:
            region_drifts.append({
                "region": region,
                "base_weight": round(base_w, 3),
                "adapted_weight": round(adapt_w, 3),
                "delta": round(delta, 3),
                "direction": "boosted" if delta > 0 else "reduced",
            })

    region_drifts.sort(key=lambda x: abs(x["delta"]), reverse=True)

    return {
        "category_drifts": drifted[:10],
        "region_drifts": region_drifts[:10],
        "total_shifted_categories": len(drifted),
        "total_shifted_regions": len(region_drifts),
    }
