"""Tests for release name parser (quality and language extraction)."""

from __future__ import annotations

from scavengarr.domain.entities.stremio import StreamLanguage, StreamQuality
from scavengarr.infrastructure.stremio.release_parser import (
    parse_language,
    parse_quality,
)


class TestParseQuality:
    def test_guessit_extracts_1080p_from_release_name(self) -> None:
        result = parse_quality(
            release_name="Iron.Man.2008.1080p.BluRay.x264-GROUP"
        )
        assert result == StreamQuality.HD_1080P

    def test_guessit_extracts_720p_from_release_name(self) -> None:
        result = parse_quality(release_name="Movie.720p.WEB-DL")
        assert result == StreamQuality.HD_720P

    def test_guessit_extracts_2160p_4k_from_release_name(self) -> None:
        result = parse_quality(release_name="Movie.2160p.UHD")
        assert result == StreamQuality.UHD_4K

    def test_fallback_to_link_quality_when_no_release_name(self) -> None:
        result = parse_quality(link_quality="1080p")
        assert result == StreamQuality.HD_1080P

    def test_fallback_to_quality_badge_when_no_release_name_or_link_quality(
        self,
    ) -> None:
        result = parse_quality(quality_badge="HD")
        assert result == StreamQuality.HD_720P

    def test_returns_unknown_when_no_data_available(self) -> None:
        result = parse_quality()
        assert result == StreamQuality.UNKNOWN

    def test_badge_cam(self) -> None:
        result = parse_quality(quality_badge="CAM")
        assert result == StreamQuality.CAM

    def test_badge_ts(self) -> None:
        result = parse_quality(quality_badge="TS")
        assert result == StreamQuality.TS

    def test_badge_bdrip(self) -> None:
        result = parse_quality(quality_badge="BDRIP")
        assert result == StreamQuality.HD_1080P

    def test_guessit_priority_over_badge(self) -> None:
        result = parse_quality(
            release_name="Movie.720p.WEB-DL",
            quality_badge="HD",
        )
        assert result == StreamQuality.HD_720P


class TestParseLanguage:
    def test_guessit_german_audio_from_release_name(self) -> None:
        result = parse_language(release_name="Movie.German.DL.1080p")
        assert result is not None
        assert result.code == "de"
        assert result.is_dubbed is True

    def test_guessit_english_subtitle_from_release_name(self) -> None:
        result = parse_language(
            release_name="Movie.1080p.English.Subs"
        )
        assert result is not None
        assert result.code == "en-sub"
        assert result.is_dubbed is False

    def test_fallback_to_link_language_german_dub(self) -> None:
        result = parse_language(link_language="German Dub")
        assert result is not None
        assert result.code == "de"
        assert result.is_dubbed is True

    def test_fallback_to_link_language_english_sub(self) -> None:
        result = parse_language(link_language="English Sub")
        assert result is not None
        assert result.code == "en-sub"
        assert result.is_dubbed is False

    def test_fallback_to_plugin_default_language(self) -> None:
        result = parse_language(plugin_default_language="de")
        assert result == StreamLanguage(
            code="de", label="German Dub", is_dubbed=True
        )

    def test_returns_none_when_nothing_available(self) -> None:
        result = parse_language()
        assert result is None

    def test_link_language_deutsch(self) -> None:
        result = parse_language(link_language="Deutsch")
        assert result is not None
        assert result.code == "de"
        assert result.is_dubbed is True

    def test_link_language_containing_sub(self) -> None:
        result = parse_language(link_language="German Sub")
        assert result is not None
        assert result.code == "de-sub"
        assert result.is_dubbed is False

    def test_link_language_containing_untertitel(self) -> None:
        result = parse_language(link_language="Deutsch Untertitel")
        assert result is not None
        assert result.code == "de-sub"
        assert result.is_dubbed is False
