"""Stream ranking and sorting for Stremio addon.

Scores streams by quality, language preference, measured bitrate, and hoster
reliability. All weights are configurable via StremioConfig.
"""

from __future__ import annotations

from dataclasses import replace

from scavengarr.domain.entities.stremio import RankedStream
from scavengarr.infrastructure.config.schema import StremioConfig


class StreamSorter:
    """Ranking: language and quality by their weights (quality first at the
    deployed multiplier of 1000, language first at the default 10), then the
    measured bitrate, then the hoster bonus.

    Score formula: language_score + (quality.value * quality_multiplier)
    + bitrate_points + hoster_bonus

    ``quality_multiplier`` decides whether quality or language dominates: at
    the deployment's 1000 one quality step is 10,000, above the widest
    language gap (1000 - 100), so every 1080p stream outranks every 720p one
    and language breaks the tie within a class (2026-11-17). The bitrate adds
    one point per Mbps, capped below the smallest gap between two language
    scores and below one quality step, so it settles streams of the same
    quality and language without outranking either. With the defaults
    (multiplier 10, language scores 1000/500/200/150) language dominates over
    quality: German Dub 720p (1000+400=1400) ranks higher than English Sub
    1080p (200+500=700).
    """

    def __init__(self, config: StremioConfig) -> None:
        self._language_scores = config.language_scores
        self._default_language_score = config.default_language_score
        self._quality_multiplier = config.quality_multiplier
        self._hoster_scores = config.hoster_scores
        # One point per Mbps, but never enough to outrank a better language
        # (the smallest gap between two language scores) or a better quality
        # (one quality step), even with the largest hoster bonus on the
        # better side. With the deployed scores that is 44 points.
        languages = sorted(
            {*config.language_scores.values(), config.default_language_score}
        )
        gaps = [b - a for a, b in zip(languages, languages[1:])]
        quality_margin = 10 * config.quality_multiplier
        bonus = max(config.hoster_scores.values(), default=0)
        margin = min(gaps, default=quality_margin) - bonus
        self._max_bitrate_points = max(0, min(margin, quality_margin - bonus) - 1)

    def rank(self, stream: RankedStream) -> int:
        """Calculate ranking score for a single stream."""
        lang_score = (
            self._language_scores.get(
                stream.language.code, self._default_language_score
            )
            if stream.language
            else self._default_language_score
        )
        quality_score = stream.quality.value * self._quality_multiplier
        bitrate_score = min(
            (stream.bitrate_bps or 0) // 1_000_000, self._max_bitrate_points
        )
        hoster_bonus = self._hoster_scores.get(stream.hoster.lower(), 0)
        return lang_score + quality_score + bitrate_score + hoster_bonus

    def sort(self, streams: list[RankedStream]) -> list[RankedStream]:
        """Sort streams descending by score. Returns new list with rank_score set."""
        scored = []
        for s in streams:
            score = self.rank(s)
            scored.append(replace(s, rank_score=score))
        scored.sort(key=lambda s: s.rank_score, reverse=True)
        return scored
