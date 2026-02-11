"""Tests for StreamSorter — configurable ranking and sorting of streams."""

from __future__ import annotations

import pytest

from scavengarr.domain.entities.stremio import (
    RankedStream,
    StreamLanguage,
    StreamQuality,
)
from scavengarr.infrastructure.config.schema import StremioConfig
from scavengarr.infrastructure.stremio.stream_sorter import StreamSorter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

GERMAN_DUB = StreamLanguage(code="de", label="German Dub", is_dubbed=True)
GERMAN_SUB = StreamLanguage(code="de-sub", label="German Sub", is_dubbed=False)
ENGLISH_SUB = StreamLanguage(code="en-sub", label="English Sub", is_dubbed=False)
ENGLISH_DUB = StreamLanguage(code="en", label="English Dub", is_dubbed=True)
UNKNOWN_LANG = StreamLanguage(code="ja", label="Japanese", is_dubbed=True)


def _stream(
    *,
    hoster: str = "generic",
    quality: StreamQuality = StreamQuality.UNKNOWN,
    language: StreamLanguage | None = None,
) -> RankedStream:
    return RankedStream(
        url=f"https://example.com/{hoster}",
        hoster=hoster,
        quality=quality,
        language=language,
    )


# ---------------------------------------------------------------------------
# TestRank
# ---------------------------------------------------------------------------


class TestRank:
    """Unit tests for StreamSorter.rank()."""

    def setup_method(self) -> None:
        self.sorter = StreamSorter(StremioConfig())

    def test_german_dub_1080p(self) -> None:
        stream = _stream(quality=StreamQuality.HD_1080P, language=GERMAN_DUB)
        # 1000 + (50 * 10) + 0 = 1500
        assert self.sorter.rank(stream) == 1500

    def test_german_sub_720p(self) -> None:
        stream = _stream(quality=StreamQuality.HD_720P, language=GERMAN_SUB)
        # 500 + (40 * 10) + 0 = 900
        assert self.sorter.rank(stream) == 900

    def test_english_sub_1080p(self) -> None:
        stream = _stream(quality=StreamQuality.HD_1080P, language=ENGLISH_SUB)
        # 200 + (50 * 10) + 0 = 700
        assert self.sorter.rank(stream) == 700

    def test_unknown_language_code(self) -> None:
        stream = _stream(quality=StreamQuality.SD, language=UNKNOWN_LANG)
        # default_language_score(100) + (30 * 10) + 0 = 400
        assert self.sorter.rank(stream) == 400

    def test_no_language(self) -> None:
        stream = _stream(quality=StreamQuality.HD_720P, language=None)
        # default_language_score(100) + (40 * 10) + 0 = 500
        assert self.sorter.rank(stream) == 500

    def test_hoster_bonus_voe(self) -> None:
        stream = _stream(
            hoster="VOE",
            quality=StreamQuality.HD_1080P,
            language=GERMAN_DUB,
        )
        # 1000 + 500 + 4 = 1504
        assert self.sorter.rank(stream) == 1504

    def test_hoster_bonus_case_insensitive(self) -> None:
        stream = _stream(
            hoster="Filemoon",
            quality=StreamQuality.UNKNOWN,
            language=None,
        )
        # 100 + 0 + 3 = 103
        assert self.sorter.rank(stream) == 103

    def test_custom_quality_multiplier(self) -> None:
        config = StremioConfig(quality_multiplier=20)
        sorter = StreamSorter(config)
        stream = _stream(quality=StreamQuality.HD_1080P, language=GERMAN_DUB)
        # 1000 + (50 * 20) + 0 = 2000
        assert sorter.rank(stream) == 2000


# ---------------------------------------------------------------------------
# TestSort
# ---------------------------------------------------------------------------


class TestSort:
    """Unit tests for StreamSorter.sort()."""

    def setup_method(self) -> None:
        self.sorter = StreamSorter(StremioConfig())

    def test_german_dub_720p_above_english_sub_1080p(self) -> None:
        """Language dominates over quality with default config."""
        de_720 = _stream(quality=StreamQuality.HD_720P, language=GERMAN_DUB)
        en_1080 = _stream(quality=StreamQuality.HD_1080P, language=ENGLISH_SUB)
        result = self.sorter.sort([en_1080, de_720])
        assert result[0].language == GERMAN_DUB
        assert result[1].language == ENGLISH_SUB

    def test_same_language_higher_quality_first(self) -> None:
        hd = _stream(quality=StreamQuality.HD_1080P, language=GERMAN_DUB)
        sd = _stream(quality=StreamQuality.SD, language=GERMAN_DUB)
        result = self.sorter.sort([sd, hd])
        assert result[0].quality == StreamQuality.HD_1080P
        assert result[1].quality == StreamQuality.SD

    def test_same_language_quality_hoster_tiebreaker(self) -> None:
        voe = _stream(
            hoster="voe", quality=StreamQuality.HD_1080P, language=GERMAN_DUB
        )
        generic = _stream(
            hoster="unknown", quality=StreamQuality.HD_1080P, language=GERMAN_DUB
        )
        result = self.sorter.sort([generic, voe])
        assert result[0].hoster == "voe"
        assert result[1].hoster == "unknown"

    def test_empty_list(self) -> None:
        assert self.sorter.sort([]) == []

    def test_single_stream_score_set(self) -> None:
        stream = _stream(quality=StreamQuality.HD_720P, language=GERMAN_DUB)
        result = self.sorter.sort([stream])
        assert len(result) == 1
        assert result[0].rank_score == 1400  # 1000 + (40*10)

    def test_rank_score_field_is_set_on_all_results(self) -> None:
        streams = [
            _stream(quality=StreamQuality.HD_1080P, language=GERMAN_DUB),
            _stream(quality=StreamQuality.SD, language=ENGLISH_SUB),
            _stream(quality=StreamQuality.UNKNOWN, language=None),
        ]
        result = self.sorter.sort(streams)
        for s in result:
            assert s.rank_score > 0

    def test_sort_is_stable_for_equal_scores(self) -> None:
        """Streams with identical scores preserve insertion order (stable sort)."""
        a = RankedStream(url="https://a.com", hoster="generic", language=GERMAN_DUB)
        b = RankedStream(url="https://b.com", hoster="generic", language=GERMAN_DUB)
        result = self.sorter.sort([a, b])
        assert result[0].url == "https://a.com"
        assert result[1].url == "https://b.com"


# ---------------------------------------------------------------------------
# TestCustomConfig
# ---------------------------------------------------------------------------


class TestCustomConfig:
    """Tests with non-default StremioConfig values."""

    def test_higher_quality_multiplier_shifts_balance(self) -> None:
        """With multiplier=20, English Sub 1080p (200+1000=1200) beats
        German Dub 720p (1000+800=1800) — wait, still doesn't. Use multiplier=100."""
        config = StremioConfig(quality_multiplier=100)
        sorter = StreamSorter(config)
        en_1080 = _stream(quality=StreamQuality.HD_1080P, language=ENGLISH_SUB)
        de_720 = _stream(quality=StreamQuality.HD_720P, language=GERMAN_DUB)
        # en_1080: 200 + (50*100) = 5200
        # de_720:  1000 + (40*100) = 5000
        result = sorter.sort([de_720, en_1080])
        assert result[0].language == ENGLISH_SUB
        assert sorter.rank(en_1080) == 5200
        assert sorter.rank(de_720) == 5000

    def test_custom_language_scores_english_preferred(self) -> None:
        config = StremioConfig(
            language_scores={"en": 1000, "de": 500},
            preferred_language="en",
        )
        sorter = StreamSorter(config)
        en = _stream(quality=StreamQuality.HD_1080P, language=ENGLISH_DUB)
        de = _stream(quality=StreamQuality.HD_1080P, language=GERMAN_DUB)
        result = sorter.sort([de, en])
        assert result[0].language == ENGLISH_DUB

    def test_custom_hoster_scores(self) -> None:
        config = StremioConfig(hoster_scores={"myhost": 100})
        sorter = StreamSorter(config)
        stream = _stream(hoster="myhost", quality=StreamQuality.UNKNOWN, language=None)
        # 100 (default lang) + 0 (quality) + 100 (hoster) = 200
        assert sorter.rank(stream) == 200
