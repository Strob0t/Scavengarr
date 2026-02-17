"""EWMA scoring functions for plugin ranking.

All functions are pure (no I/O, no state) and operate on domain
entities from ``scavengarr.domain.entities.scoring``.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from scavengarr.domain.entities.scoring import EwmaState, ProbeResult

# Maximum latency (ms) used to normalise duration into 0.0–1.0 range.
_MAX_LATENCY_MS: float = 10_000.0

# Latency weight relative to binary reachability in health observations.
_LATENCY_WEIGHT: float = 0.3


def alpha_from_halflife(dt: float, half_life: float) -> float:
    """Compute EWMA smoothing factor from probe interval and half-life.

    Args:
        dt: Time between consecutive probes (same unit as *half_life*).
        half_life: Time after which a single observation decays to 50%.

    Returns:
        Smoothing factor ``alpha`` in ``(0, 1)``.

    Raises:
        ValueError: If *dt* or *half_life* is not positive.
    """
    if dt <= 0 or half_life <= 0:
        raise ValueError("dt and half_life must be positive")
    return 1.0 - 0.5 ** (dt / half_life)


def ewma_update(
    state: EwmaState,
    observation: float,
    alpha: float,
    ts: datetime,
) -> EwmaState:
    """Apply one EWMA update and return a new state.

    ``new_value = alpha * observation + (1 - alpha) * state.value``

    The observation is clamped to [0.0, 1.0] before blending.
    """
    obs = max(0.0, min(1.0, observation))
    new_value = alpha * obs + (1.0 - alpha) * state.value
    return EwmaState(
        value=new_value,
        last_ts=ts,
        n_samples=state.n_samples + 1,
    )


def compute_confidence(
    n_samples: int,
    age_seconds: float,
    k: float = 10.0,
    tau: float = 2_419_200.0,
) -> float:
    """Compute score confidence from sample count and recency.

    ``confidence = sample_conf * recency_conf`` where:
    - ``sample_conf = 1 - exp(-n_samples / k)``  (saturates around 10 samples)
    - ``recency_conf = exp(-age_seconds / tau)``  (tau defaults to 4 weeks)

    Returns:
        Confidence in ``[0.0, 1.0]``.
    """
    sample_conf = 1.0 - math.exp(-n_samples / k)
    recency_conf = math.exp(-age_seconds / tau) if age_seconds >= 0 else 1.0
    return max(0.0, min(1.0, sample_conf * recency_conf))


def compute_health_observation(probe: ProbeResult) -> float:
    """Convert a health probe result into a 0.0–1.0 observation.

    Combines binary reachability (70% weight) with an inverted latency
    penalty (30% weight).  A fast, reachable site scores ~1.0; a slow
    or unreachable site scores close to 0.0.

    Captcha detection forces a 0.0 score — if the plugin's base URL
    serves a Cloudflare challenge the site is effectively unreachable.
    """
    if probe.captcha_detected:
        return 0.0
    reachable = 1.0 if probe.ok else 0.0
    speed = 1.0 - min(probe.duration_ms / _MAX_LATENCY_MS, 1.0)
    return (1.0 - _LATENCY_WEIGHT) * reachable + _LATENCY_WEIGHT * speed


def compute_search_observation(probe: ProbeResult, limit: int) -> float:
    """Convert a search probe result into a 0.0–1.0 observation.

    Components (5 weighted):
    1. Success (binary): 1.0 if ok, 0.0 otherwise           (0.20)
    2. Latency: inverted, clamped to _MAX_LATENCY_MS         (0.15)
    3. Result quality: ``min(items_found, limit) / limit``   (0.20)
    4. Hoster reachability: ``reachable / checked``           (0.20)
    5. Supported-hoster ratio: ``supported / total``          (0.25)

    The supported-hoster ratio gets the largest weight because it
    directly measures whether a plugin's results can be resolved and
    played by registered hoster resolvers.
    """
    success = 1.0 if probe.ok else 0.0
    speed = 1.0 - min(probe.duration_ms / _MAX_LATENCY_MS, 1.0)

    effective_limit = max(limit, 1)
    quality = min(probe.items_found, effective_limit) / effective_limit

    if probe.hoster_checked > 0:
        hoster_ratio = probe.hoster_reachable / probe.hoster_checked
    else:
        hoster_ratio = 1.0

    if probe.hoster_total > 0:
        supported_ratio = probe.hoster_supported / probe.hoster_total
    else:
        supported_ratio = 0.0

    return (
        0.20 * success
        + 0.15 * speed
        + 0.20 * quality
        + 0.20 * hoster_ratio
        + 0.25 * supported_ratio
    )


def compute_final_score(
    health: EwmaState,
    search: EwmaState,
    confidence: float,
    *,
    w_health: float = 0.4,
    w_search: float = 0.6,
) -> float:
    """Compute weighted composite score, discounted by confidence.

    ``raw = w_health * health.value + w_search * search.value``
    ``final = raw * (0.5 + 0.5 * confidence)``

    Returns:
        Final score in ``[0.0, 1.0]``.
    """
    raw = w_health * health.value + w_search * search.value
    discounted = raw * (0.5 + 0.5 * max(0.0, min(1.0, confidence)))
    return max(0.0, min(1.0, discounted))


def update_snapshot_scores(
    health: EwmaState,
    search: EwmaState,
    *,
    w_health: float = 0.4,
    w_search: float = 0.6,
    now: datetime | None = None,
) -> tuple[float, float]:
    """Convenience: compute both final_score and confidence.

    Returns:
        ``(final_score, confidence)`` tuple.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # Use the more recent of health/search timestamps for age calculation.
    most_recent = max(health.last_ts, search.last_ts)
    age_seconds = (now - most_recent).total_seconds()
    total_samples = health.n_samples + search.n_samples

    conf = compute_confidence(total_samples, age_seconds)
    score = compute_final_score(
        health, search, conf, w_health=w_health, w_search=w_search
    )
    return score, conf
