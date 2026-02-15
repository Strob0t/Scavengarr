"""Domain entities for plugin scoring and probing.

Pure value objects — no framework dependencies, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

AgeBucket = Literal["current", "y1_2", "y5_10"]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ProbeResult:
    """Raw output from a single probe run (health or search)."""

    started_at: datetime
    duration_ms: float
    ok: bool
    error_kind: str | None = None  # "timeout", "captcha", "http_error"
    http_status: int | None = None
    captcha_detected: bool = False
    items_found: int = 0
    items_used: int = 0
    hoster_checked: int = 0
    hoster_reachable: int = 0


@dataclass(frozen=True)
class EwmaState:
    """Exponentially weighted moving average tracker.

    Tracks a single score dimension (health or search) with a
    0.0–1.0 value, the timestamp of the last update, and the
    total number of samples incorporated.
    """

    value: float = 0.5
    last_ts: datetime = field(default_factory=_utcnow)
    n_samples: int = 0


@dataclass(frozen=True)
class PluginScoreSnapshot:
    """Composite score for a plugin within a (category, bucket) context.

    Persisted by ``PluginScoreStorePort`` and consumed by the Stremio
    stream use case for top-N plugin selection.
    """

    plugin: str
    category: int
    bucket: AgeBucket
    health_score: EwmaState = field(default_factory=EwmaState)
    search_score: EwmaState = field(default_factory=EwmaState)
    final_score: float = 0.5
    confidence: float = 0.0
    updated_at: datetime = field(default_factory=_utcnow)
