"""Tests for stream converter (SearchResult -> RankedStream)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from scavengarr.domain.entities.stremio import (
    StreamLanguage,
    StreamQuality,
)
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.stremio.stream_converter import (
    _extract_hoster,
    convert_search_results,
)

_MOCK_QUALITY = StreamQuality.HD_1080P
_MOCK_LANGUAGE = StreamLanguage(code="de", label="German Dub", is_dubbed=True)


def _make_result(
    *,
    title: str = "Test Movie",
    download_link: str = "https://example.com/dl",
    download_links: list[dict[str, str]] | None = None,
    release_name: str | None = None,
    size: str | None = None,
    metadata: dict | None = None,
) -> SearchResult:
    return SearchResult(
        title=title,
        download_link=download_link,
        download_links=download_links,
        release_name=release_name,
        size=size,
        metadata=metadata or {},
    )


@pytest.fixture(autouse=True)
def _mock_parsers():
    """Mock release_parser functions to isolate converter logic."""
    with (
        patch(
            "scavengarr.infrastructure.stremio.stream_converter.parse_quality",
            return_value=_MOCK_QUALITY,
        ) as mock_q,
        patch(
            "scavengarr.infrastructure.stremio.stream_converter.parse_language",
            return_value=_MOCK_LANGUAGE,
        ) as mock_l,
    ):
        yield mock_q, mock_l


class TestConvertSearchResults:
    def test_single_result_with_download_links(self) -> None:
        result = _make_result(
            download_links=[
                {"url": "https://voe.sx/e/abc", "quality": "1080p", "language": "de"},
                {"url": "https://filemoon.sx/e/xyz", "quality": "720p"},
            ],
        )
        streams = convert_search_results([result])
        assert len(streams) == 2
        assert streams[0].url == "https://voe.sx/e/abc"
        assert streams[1].url == "https://filemoon.sx/e/xyz"

    def test_result_without_download_links_uses_download_link(self) -> None:
        result = _make_result(
            download_link="https://streamtape.com/v/abc",
            download_links=None,
        )
        streams = convert_search_results([result])
        assert len(streams) == 1
        assert streams[0].url == "https://streamtape.com/v/abc"

    def test_multiple_results_concatenated(self) -> None:
        r1 = _make_result(
            download_links=[{"url": "https://voe.sx/e/1"}],
        )
        r2 = _make_result(
            download_links=[
                {"url": "https://voe.sx/e/2"},
                {"url": "https://voe.sx/e/3"},
            ],
        )
        streams = convert_search_results([r1, r2])
        assert len(streams) == 3

    def test_empty_results_list(self) -> None:
        streams = convert_search_results([])
        assert streams == []

    def test_link_without_url_skipped(self) -> None:
        result = _make_result(
            download_links=[
                {"quality": "1080p"},  # No url key
                {"url": "", "quality": "720p"},  # Empty url
                {"url": "https://voe.sx/e/valid"},
            ],
        )
        streams = convert_search_results([result])
        assert len(streams) == 1
        assert streams[0].url == "https://voe.sx/e/valid"

    def test_hoster_from_link_dict(self) -> None:
        result = _make_result(
            download_links=[
                {"url": "https://voe.sx/e/abc", "hoster": "MyHoster"},
            ],
        )
        streams = convert_search_results([result])
        assert streams[0].hoster == "MyHoster"

    def test_hoster_extracted_from_url_when_not_in_link(self) -> None:
        result = _make_result(
            download_links=[
                {"url": "https://filemoon.sx/e/abc"},
            ],
        )
        streams = convert_search_results([result])
        assert streams[0].hoster == "filemoon"

    def test_source_plugin_from_metadata(self) -> None:
        result = _make_result(
            metadata={"source_plugin": "hdfilme"},
            download_links=[{"url": "https://voe.sx/e/abc"}],
        )
        streams = convert_search_results([result])
        assert streams[0].source_plugin == "hdfilme"

    def test_source_plugin_empty_when_missing(self) -> None:
        result = _make_result(
            download_links=[{"url": "https://voe.sx/e/abc"}],
        )
        streams = convert_search_results([result])
        assert streams[0].source_plugin == ""

    def test_release_name_passed_through(self) -> None:
        result = _make_result(
            release_name="Movie.2024.1080p.WEB-DL",
            download_links=[{"url": "https://voe.sx/e/abc"}],
        )
        streams = convert_search_results([result])
        assert streams[0].release_name == "Movie.2024.1080p.WEB-DL"

    def test_size_from_link_dict(self) -> None:
        result = _make_result(
            download_links=[{"url": "https://voe.sx/e/abc", "size": "1.5 GB"}],
        )
        streams = convert_search_results([result])
        assert streams[0].size == "1.5 GB"

    def test_size_fallback_to_result_size(self) -> None:
        result = _make_result(
            size="2.3 GB",
            download_links=[{"url": "https://voe.sx/e/abc"}],
        )
        streams = convert_search_results([result])
        assert streams[0].size == "2.3 GB"

    def test_quality_and_language_set(self) -> None:
        result = _make_result(
            download_links=[{"url": "https://voe.sx/e/abc"}],
        )
        streams = convert_search_results([result])
        assert streams[0].quality == _MOCK_QUALITY
        assert streams[0].language == _MOCK_LANGUAGE

    def test_parse_quality_called_with_correct_args(
        self,
        _mock_parsers: tuple,
    ) -> None:
        mock_q, _mock_l = _mock_parsers
        result = _make_result(
            release_name="Movie.720p",
            metadata={"quality": "HD"},
            download_links=[{"url": "https://voe.sx/e/abc", "quality": "720p"}],
        )
        convert_search_results([result])
        mock_q.assert_called_once_with(
            release_name="Movie.720p",
            quality_badge="HD",
            link_quality="720p",
        )

    def test_parse_language_called_with_correct_args(
        self,
        _mock_parsers: tuple,
    ) -> None:
        _mock_q, mock_l = _mock_parsers
        result = _make_result(
            release_name="Movie.German",
            download_links=[{"url": "https://voe.sx/e/abc", "language": "de"}],
        )
        convert_search_results([result])
        mock_l.assert_called_once_with(
            release_name="Movie.German",
            link_language="de",
            plugin_default_language=None,
        )

    def test_result_with_empty_download_links_falls_back(self) -> None:
        result = _make_result(
            download_link="https://fallback.com/dl",
            download_links=[],
        )
        streams = convert_search_results([result])
        # Empty list is falsy, so falls back to download_link
        assert len(streams) == 1
        assert streams[0].url == "https://fallback.com/dl"

    def test_ranked_stream_is_frozen(self) -> None:
        result = _make_result(
            download_links=[{"url": "https://voe.sx/e/abc"}],
        )
        streams = convert_search_results([result])
        with pytest.raises(AttributeError):
            streams[0].url = "other"  # type: ignore[misc]

    def test_plugin_default_language_passed_from_mapping(
        self,
        _mock_parsers: tuple,
    ) -> None:
        _mock_q, mock_l = _mock_parsers
        result = _make_result(
            metadata={"source_plugin": "hdfilme"},
            download_links=[{"url": "https://voe.sx/e/abc"}],
        )
        convert_search_results([result], plugin_languages={"hdfilme": "de"})
        mock_l.assert_called_once_with(
            release_name=None,
            link_language=None,
            plugin_default_language="de",
        )

    def test_plugin_not_in_languages_map_gets_none(
        self,
        _mock_parsers: tuple,
    ) -> None:
        _mock_q, mock_l = _mock_parsers
        result = _make_result(
            metadata={"source_plugin": "unknown_plugin"},
            download_links=[{"url": "https://voe.sx/e/abc"}],
        )
        convert_search_results([result], plugin_languages={"hdfilme": "de"})
        mock_l.assert_called_once_with(
            release_name=None,
            link_language=None,
            plugin_default_language=None,
        )

    def test_plugin_default_language_for_single_download_link(
        self,
        _mock_parsers: tuple,
    ) -> None:
        _mock_q, mock_l = _mock_parsers
        result = _make_result(
            download_link="https://streamtape.com/v/abc",
            download_links=None,
            metadata={"source_plugin": "kinoger"},
        )
        convert_search_results([result], plugin_languages={"kinoger": "de"})
        mock_l.assert_called_once_with(
            release_name=None,
            link_language=None,
            plugin_default_language="de",
        )


class TestExtractHoster:
    def test_voe(self) -> None:
        assert _extract_hoster("https://voe.sx/e/abc") == "voe"

    def test_filemoon(self) -> None:
        assert _extract_hoster("https://filemoon.sx/e/abc") == "filemoon"

    def test_streamtape(self) -> None:
        assert _extract_hoster("https://streamtape.com/v/abc") == "streamtape"

    def test_doodstream(self) -> None:
        assert _extract_hoster("https://doodstream.com/d/abc") == "doodstream"

    def test_empty_url(self) -> None:
        assert _extract_hoster("") == "unknown"

    def test_invalid_url(self) -> None:
        assert _extract_hoster("not-a-url") == "unknown"

    def test_url_without_scheme(self) -> None:
        # urlparse without scheme puts everything in path
        assert _extract_hoster("voe.sx/e/abc") == "unknown"
