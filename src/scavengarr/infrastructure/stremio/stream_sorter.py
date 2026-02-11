"""Stream ranking and sorting for Stremio addon.

Scores streams by language preference, quality, and hoster reliability.
All weights are configurable via StremioConfig.
"""

from __future__ import annotations

from scavengarr.domain.entities.stremio import RankedStream
from scavengarr.infrastructure.config.schema import StremioConfig


class StreamSorter:
    """Ranking: Language (German first) + Quality (best first).

    All weights come from StremioConfig — no hardcoded values.
    Score formula: language_score + (quality.value * quality_multiplier) + hoster_bonus

    With default config: German Dub 720p (1000+400+0=1400) ranks higher than
    English Sub 1080p (200+500+0=700). Language dominates over quality.
    """

    def __init__(self, config: StremioConfig) -> None:
        self._language_scores = config.language_scores
        self._default_language_score = config.default_language_score
        self._quality_multiplier = config.quality_multiplier
        self._hoster_scores = config.hoster_scores

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
        hoster_bonus = self._hoster_scores.get(stream.hoster.lower(), 0)
        return lang_score + quality_score + hoster_bonus

    def sort(self, streams: list[RankedStream]) -> list[RankedStream]:
        """Sort streams descending by score. Returns new list with rank_score set."""
        scored = []
        for s in streams:
            score = self.rank(s)
            scored.append(
                RankedStream(
                    url=s.url,
                    hoster=s.hoster,
                    quality=s.quality,
                    language=s.language,
                    size=s.size,
                    release_name=s.release_name,
                    source_plugin=s.source_plugin,
                    rank_score=score,
                )
            )
        scored.sort(key=lambda s: s.rank_score, reverse=True)
        return scored
