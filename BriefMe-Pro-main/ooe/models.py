from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class NormalizedEvent:
    event_id: str = ""
    source_name: str = ""
    external_id: str = ""
    cluster_id: str = ""
    title: str = ""
    raw_title: str = ""
    summary: str = ""
    category: str = "other"
    event_type: str = "other"
    subtype: str = ""
    severity: float = 0.0
    magnitude: float = 0.0
    urgency: float = 0.0
    relevance: float = 0.0
    personal_impact: float = 0.0
    emotional_load: float = 0.0
    cognitive_load: float = 0.0
    behavioral_load: float = 0.0
    confidence: float = 0.0
    coverage_score: float = 0.0
    probability: float = 1.0
    predicted: bool = False
    status: str = "active"
    verification_status: str = "checking"
    location_name: str = ""
    country: str = ""
    region: str = ""
    latitude: float | None = None
    longitude: float | None = None
    time_window_start: str = ""
    time_window_end: str = ""
    happened_at: str = ""
    collected_at: str = field(default_factory=utcnow_iso)
    updated_at: str = field(default_factory=utcnow_iso)
    expires_at: str = ""
    url: str = ""
    actors: list[str] = field(default_factory=list)
    casualties: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    source_refs: list[dict[str, Any]] = field(default_factory=list)
    raw_refs: list[str] = field(default_factory=list)
    raw_payload: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
