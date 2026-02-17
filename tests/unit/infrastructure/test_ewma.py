"""Unit tests for EWMA scoring functions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from scavengarr.domain.entities.scoring import EwmaState, ProbeResult
from scavengarr.infrastructure.scoring.ewma import (
    alpha_from_halflife,
    compute_confidence,
    compute_final_score,
    compute_health_observation,
    compute_search_observation,
    ewma_update,
    update_snapshot_scores,
)

_NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


class TestAlphaFromHalflife:
    def test_health_probe_values(self) -> None:
        # dt=1 day, half_life=2 days -> ~0.2929
        alpha = alpha_from_halflife(1.0, 2.0)
        assert abs(alpha - 0.2929) < 0.001

    def test_search_probe_values(self) -> None:
        # dt=0.5 weeks, half_life=2 weeks -> ~0.1591
        alpha = alpha_from_halflife(0.5, 2.0)
        assert abs(alpha - 0.1591) < 0.001

    def test_dt_equals_halflife(self) -> None:
        # When dt == half_life, alpha = 0.5
        assert alpha_from_halflife(1.0, 1.0) == 0.5

    def test_zero_dt_raises(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            alpha_from_halflife(0.0, 2.0)

    def test_negative_halflife_raises(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            alpha_from_halflife(1.0, -1.0)


class TestEwmaUpdate:
    def test_increases_on_high_observation(self) -> None:
        state = EwmaState(value=0.3, last_ts=_NOW, n_samples=5)
        new = ewma_update(state, 1.0, 0.3, _NOW)
        assert new.value > state.value
        assert new.n_samples == 6

    def test_decreases_on_low_observation(self) -> None:
        state = EwmaState(value=0.8, last_ts=_NOW, n_samples=5)
        new = ewma_update(state, 0.0, 0.3, _NOW)
        assert new.value < state.value

    def test_clamps_observation(self) -> None:
        state = EwmaState(value=0.5, last_ts=_NOW, n_samples=0)
        # Observation > 1.0 is clamped to 1.0
        new = ewma_update(state, 2.0, 0.5, _NOW)
        assert new.value == pytest.approx(0.75)

    def test_negative_observation_clamped(self) -> None:
        state = EwmaState(value=0.5, last_ts=_NOW, n_samples=0)
        new = ewma_update(state, -1.0, 0.5, _NOW)
        assert new.value == pytest.approx(0.25)

    def test_updates_timestamp(self) -> None:
        state = EwmaState(value=0.5, last_ts=_NOW, n_samples=0)
        later = _NOW + timedelta(hours=1)
        new = ewma_update(state, 0.5, 0.3, later)
        assert new.last_ts == later

    def test_exact_formula(self) -> None:
        state = EwmaState(value=0.4, last_ts=_NOW, n_samples=3)
        alpha = 0.3
        obs = 0.9
        expected = alpha * obs + (1 - alpha) * 0.4
        new = ewma_update(state, obs, alpha, _NOW)
        assert new.value == pytest.approx(expected)


class TestComputeConfidence:
    def test_zero_samples(self) -> None:
        assert compute_confidence(0, 0.0) == pytest.approx(0.0)

    def test_many_samples_recent(self) -> None:
        conf = compute_confidence(50, 0.0)
        assert conf > 0.99

    def test_many_samples_old(self) -> None:
        # 8 weeks old (twice the tau of 4 weeks)
        conf = compute_confidence(50, 4_838_400.0)
        assert conf < 0.2

    def test_few_samples_recent(self) -> None:
        conf = compute_confidence(2, 0.0)
        assert 0.15 < conf < 0.25

    def test_clamped_to_unit(self) -> None:
        conf = compute_confidence(1000, 0.0)
        assert 0.0 <= conf <= 1.0

    def test_negative_age_treated_as_recent(self) -> None:
        conf = compute_confidence(10, -100.0)
        assert conf > 0.5


class TestComputeHealthObservation:
    def test_reachable_fast(self) -> None:
        probe = ProbeResult(started_at=_NOW, duration_ms=100.0, ok=True)
        obs = compute_health_observation(probe)
        assert obs > 0.9

    def test_reachable_slow(self) -> None:
        probe = ProbeResult(started_at=_NOW, duration_ms=9000.0, ok=True)
        obs = compute_health_observation(probe)
        assert 0.7 < obs < 0.8

    def test_unreachable(self) -> None:
        probe = ProbeResult(started_at=_NOW, duration_ms=5000.0, ok=False)
        obs = compute_health_observation(probe)
        assert obs < 0.2

    def test_unreachable_fast(self) -> None:
        # Fast failure still scores low (reachability dominates)
        probe = ProbeResult(started_at=_NOW, duration_ms=50.0, ok=False)
        obs = compute_health_observation(probe)
        assert obs < 0.35

    def test_captcha_detected_returns_zero(self) -> None:
        probe = ProbeResult(
            started_at=_NOW,
            duration_ms=100.0,
            ok=False,
            captcha_detected=True,
            error_kind="captcha",
        )
        obs = compute_health_observation(probe)
        assert obs == 0.0


class TestComputeSearchObservation:
    def test_perfect_probe(self) -> None:
        probe = ProbeResult(
            started_at=_NOW,
            duration_ms=100.0,
            ok=True,
            items_found=20,
            items_used=20,
            hoster_checked=5,
            hoster_reachable=5,
            hoster_supported=20,
            hoster_total=20,
        )
        obs = compute_search_observation(probe, limit=20)
        assert obs > 0.9

    def test_failed_probe(self) -> None:
        probe = ProbeResult(
            started_at=_NOW,
            duration_ms=10000.0,
            ok=False,
            items_found=0,
        )
        obs = compute_search_observation(probe, limit=20)
        # success=0, speed=0, quality=0, hoster_ratio=1.0, supported_ratio=0
        # = 0.20*0 + 0.15*0 + 0.20*0 + 0.20*1.0 + 0.25*0 = 0.20
        assert obs == pytest.approx(0.20, abs=0.01)

    def test_partial_results(self) -> None:
        probe = ProbeResult(
            started_at=_NOW,
            duration_ms=500.0,
            ok=True,
            items_found=5,
            hoster_checked=3,
            hoster_reachable=2,
            hoster_supported=3,
            hoster_total=5,
        )
        obs = compute_search_observation(probe, limit=20)
        assert 0.3 < obs < 0.8

    def test_zero_limit_safe(self) -> None:
        probe = ProbeResult(
            started_at=_NOW,
            duration_ms=100.0,
            ok=True,
            items_found=5,
            hoster_supported=5,
            hoster_total=5,
        )
        obs = compute_search_observation(probe, limit=0)
        # limit=0 -> effective_limit=1, quality = min(5,1)/1 = 1.0
        assert obs > 0.5

    def test_all_unsupported_hosters_low_score(self) -> None:
        """Plugin returning only unsupported hoster links scores low."""
        probe = ProbeResult(
            started_at=_NOW,
            duration_ms=200.0,
            ok=True,
            items_found=20,
            items_used=20,
            hoster_checked=0,
            hoster_reachable=0,
            hoster_supported=0,
            hoster_total=20,
        )
        obs = compute_search_observation(probe, limit=20)
        # success=1.0, speed~0.98, quality=1.0, hoster_ratio=1.0, supported=0.0
        # = 0.20 + 0.15*0.98 + 0.20 + 0.20 + 0.0 = ~0.747
        # Crucially lower than the "all supported" case.
        assert obs < 0.80

    def test_all_supported_hosters_high_score(self) -> None:
        """Plugin returning only supported hoster links scores high."""
        probe = ProbeResult(
            started_at=_NOW,
            duration_ms=200.0,
            ok=True,
            items_found=20,
            items_used=20,
            hoster_checked=3,
            hoster_reachable=3,
            hoster_supported=20,
            hoster_total=20,
        )
        obs = compute_search_observation(probe, limit=20)
        # success=1.0, speed~0.98, quality=1.0, hoster_ratio=1.0, supported=1.0
        assert obs > 0.95

    def test_supported_ratio_zero_when_no_links(self) -> None:
        """When hoster_total is 0, supported_ratio defaults to 0.0."""
        probe = ProbeResult(
            started_at=_NOW,
            duration_ms=100.0,
            ok=True,
            items_found=0,
            hoster_total=0,
            hoster_supported=0,
        )
        obs = compute_search_observation(probe, limit=20)
        # success=1.0, speed~0.99, quality=0.0, hoster=1.0, supported=0.0
        # = 0.20 + 0.15*0.99 + 0.0 + 0.20 + 0.0 = ~0.549
        assert 0.5 < obs < 0.6

    def test_half_supported_half_unsupported(self) -> None:
        """50% supported hosters gives proportional supported_ratio."""
        probe = ProbeResult(
            started_at=_NOW,
            duration_ms=200.0,
            ok=True,
            items_found=10,
            items_used=10,
            hoster_checked=3,
            hoster_reachable=3,
            hoster_supported=5,
            hoster_total=10,
        )
        obs = compute_search_observation(probe, limit=20)
        # supported_ratio = 0.5, quality = 10/20 = 0.5
        # 0.20*1 + 0.15*~0.98 + 0.20*0.5 + 0.20*1.0 + 0.25*0.5
        # = 0.20 + 0.147 + 0.10 + 0.20 + 0.125 = ~0.772
        assert 0.7 < obs < 0.85


class TestComputeFinalScore:
    def test_default_weights(self) -> None:
        health = EwmaState(value=1.0, last_ts=_NOW, n_samples=10)
        search = EwmaState(value=1.0, last_ts=_NOW, n_samples=10)
        score = compute_final_score(health, search, confidence=1.0)
        assert score == pytest.approx(1.0)

    def test_zero_confidence_halves_score(self) -> None:
        health = EwmaState(value=1.0, last_ts=_NOW, n_samples=10)
        search = EwmaState(value=1.0, last_ts=_NOW, n_samples=10)
        score = compute_final_score(health, search, confidence=0.0)
        assert score == pytest.approx(0.5)

    def test_custom_weights(self) -> None:
        health = EwmaState(value=1.0, last_ts=_NOW, n_samples=10)
        search = EwmaState(value=0.0, last_ts=_NOW, n_samples=10)
        score = compute_final_score(
            health, search, confidence=1.0, w_health=0.7, w_search=0.3
        )
        assert score == pytest.approx(0.7)

    def test_clamped_to_unit(self) -> None:
        health = EwmaState(value=1.0, last_ts=_NOW, n_samples=10)
        search = EwmaState(value=1.0, last_ts=_NOW, n_samples=10)
        score = compute_final_score(
            health, search, confidence=1.5, w_health=0.6, w_search=0.6
        )
        assert 0.0 <= score <= 1.0


class TestUpdateSnapshotScores:
    def test_returns_tuple(self) -> None:
        health = EwmaState(value=0.8, last_ts=_NOW, n_samples=5)
        search = EwmaState(value=0.6, last_ts=_NOW, n_samples=5)
        score, conf = update_snapshot_scores(health, search, now=_NOW)
        assert isinstance(score, float)
        assert isinstance(conf, float)
        assert 0.0 <= score <= 1.0
        assert 0.0 <= conf <= 1.0

    def test_old_scores_low_confidence(self) -> None:
        old = _NOW - timedelta(weeks=8)
        health = EwmaState(value=0.9, last_ts=old, n_samples=20)
        search = EwmaState(value=0.9, last_ts=old, n_samples=20)
        score, conf = update_snapshot_scores(health, search, now=_NOW)
        assert conf < 0.3
